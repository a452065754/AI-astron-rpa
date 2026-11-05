#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""流程参数体检（param_health.py）—— 只读巡检服务端所有机器人的参数预算。

为什么需要它（平台缺陷 A20，2026-11-05 正式服事故）
--------------------------------------------------
「添加应用」列表接口 `GET /api/robot/triggerTask/robotExe/list` 会把每个机器人的参数
拼成 `Executor.paramJson`，再整段进 `SensitiveDataSanitizer.sanitize()`；里面的
`QUOTED_ARRAY_VALUE` 靠 `java.util.regex.Pattern$Loop` 递归匹配，
**递归深度 ≈ 字符数**，超过 ~1500~1800 字符就 `StackOverflowError` →
整个接口只剩 `{"code":"999999"}` → 前端「添加应用」报错。

而且它**只影响发布者自己的账号**（参数查询带 `creator_id` 过滤）：
同一台服务器上别的账号一切正常，所以极容易被误判成「浏览器缓存 / 网络问题」。

这个脚本把服务器上**每个机器人**的参数条数和 `paramJson` 估算长度一次列清，
红/黄/绿分级，红的就是「下次点添加应用就会报错」的雷。

用法
----
    python scripts/param_health.py                      # 默认查 rpa_config 里的 gateway 主机
    python scripts/param_health.py --host 192.168.0.183
    python scripts/param_health.py --json               # 机器可读（给 CI / 巡检用）
    python scripts/param_health.py --strict             # 有红就退出码 2

⚠️ **全程只读**：只跑 `SELECT`，不改数据、不重启容器。
⚠️ 需要 `paramiko`（`pip install paramiko`）；MySQL 走容器内 `mysql` 客户端。
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import param_budget as pb   # noqa: E402

ROOT = os.path.dirname(HERE)

# 值里可能有制表符/换行（process_content 那种），统计前先拍平，避免把一行拆成多行
#
# JOIN 条件必须与平台 DAO 的**退化行为**一致（这是本脚本最容易算错的地方）：
#   · `app_version` 有值 → `getSelfRobotParam(robot_id, process_id, robot_version)`，
#     只取该版本、且 `process_id is not null` 的**流程参数**（模块参数不在内）；
#   · `app_version` 为 NULL → `getMianProcessId` 返回 null，条件退化成 `where robot_id=?`，
#     把**所有版本 + 模块级参数**全捞回来（同一个名字在 v0/v3/v4 各占一条，参数条数直接翻倍）。
_SQL = """
SELECT r.robot_id, IFNULL(r.name,''), IFNULL(r.app_version,-1),
       IFNULL(p.robot_version,-1), IFNULL(p.var_name,''), IFNULL(p.var_type,'Str'),
       REPLACE(REPLACE(IFNULL(p.var_value,''),CHAR(9),' '),CHAR(10),' '),
       REPLACE(REPLACE(IFNULL(p.var_describe,''),CHAR(9),' '),CHAR(10),' '),
       IF(p.module_id IS NULL,0,1)
FROM robot_execute r
LEFT JOIN c_param p
       ON p.robot_id = r.robot_id AND p.deleted = 0
      AND ( (r.app_version IS NOT NULL AND p.process_id IS NOT NULL
             AND p.robot_version = r.app_version)
         OR (r.app_version IS NULL AND (p.process_id IS NOT NULL OR p.module_id IS NOT NULL)) )
WHERE r.deleted = 0
ORDER BY r.name, p.var_name
"""


def default_host():
    try:
        import rpa_config as cfg
        return cfg.host_port().split(":")[0]
    except Exception:
        return "192.168.0.183"


def query(host, ssh_user, ssh_pw, container, db_user, db_pw, db):
    import paramiko

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, port=22, username=ssh_user, password=ssh_pw, timeout=15)
    try:
        cmd = ("docker exec -i -e MYSQL_PWD=%s %s mysql -u%s -N -B %s 2>&1"
               % (db_pw, container, db_user, db))
        stdin, stdout, stderr = client.exec_command(cmd, timeout=180)
        stdin.write(_SQL.strip() + ";\n")
        stdin.flush()
        stdin.channel.shutdown_write()
        out = stdout.read().decode("utf-8", "replace")
    finally:
        client.close()
    if out.startswith("ERROR") or "mysql: [Warning]" in out[:40] and "\t" not in out:
        raise SystemExit("SQL 执行失败：\n%s" % out[:800])
    return out


def parse(out):
    """→ {robot_id: {"name","app_version","params":[{...}]}}"""
    robots = {}
    for line in out.splitlines():
        if not line.strip():
            continue
        f = line.split("\t")
        if len(f) < 9:
            continue
        rid, name, appv, pv, vn, vt, vv, vd, is_mod = f[:9]
        r = robots.setdefault(rid, {"name": name, "app_version": int(appv or -1), "params": []})
        if vn:  # LEFT JOIN 的空行
            r["params"].append({"varName": vn, "varType": vt, "varValue": vv,
                                "varDescribe": vd, "_ver": int(pv or -1),
                                "_module": is_mod.strip() == "1"})
    return robots


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=default_host())
    ap.add_argument("--ssh-user", default=os.environ.get("RPA_SSH_USER", "xudongdong"))
    ap.add_argument("--ssh-password", default=os.environ.get("RPA_SSH_PASSWORD", "1234"))
    ap.add_argument("--container", default="rpa-opensource-mysql")
    ap.add_argument("--db-user", default="root")
    ap.add_argument("--db-password", default=os.environ.get("RPA_DB_PASSWORD", "rpa123456"))
    ap.add_argument("--db", default="rpa")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true", help="有红色项时退出码 2")
    args = ap.parse_args()

    robots = parse(query(args.host, args.ssh_user, args.ssh_password, args.container,
                         args.db_user, args.db_password, args.db))

    rows = []
    for rid, r in robots.items():
        st = pb.summary(r["params"])
        versions = sorted({p["_ver"] for p in r["params"]})
        n_mod = sum(1 for p in r["params"] if p["_module"])
        rows.append({
            "robot_id": rid, "name": r["name"], "app_version": r["app_version"],
            "count": st["count"], "span": st["span"], "level": st["level"],
            "module_count": n_mod,
            "param_versions": versions,
            "top": [n for _s, n, _v in pb.advise(r["params"])[:5]],
        })
    rows.sort(key=lambda x: (-x["span"], x["name"]))

    red = [x for x in rows if x["level"] == "red"]
    yellow = [x for x in rows if x["level"] == "yellow"]

    if args.json:
        print(json.dumps({"host": args.host, "robots": rows,
                          "red": len(red), "yellow": len(yellow),
                          "limits": {"span_warn": pb.SPAN_WARN, "span_err": pb.SPAN_ERR,
                                     "count_err": pb.COUNT_ERR}},
                         ensure_ascii=False, indent=2))
        return 2 if (args.strict and red) else 0

    print("=" * 88)
    print("流程参数体检 · %s · 只读" % args.host)
    print("判定：paramJson 估算 ≥%d 字符 或 参数 >%d 条 → 红（点「添加应用」必报 999999）"
          % (pb.SPAN_ERR, pb.COUNT_ERR))
    print("      ≥%d 字符 或 >%d 条 → 黄（接近悬崖，建议收敛；工程目标 ≤%d 字符）"
          % (pb.SPAN_WARN, pb.COUNT_WARN, pb.SPAN_WARN))
    print("=" * 88)
    print("%-4s %-34s %-6s %-4s %-7s %s" % ("级别", "机器人", "app_ver", "条数", "估算", "参数版本"))
    print("-" * 88)
    icon = {"red": "🔴", "yellow": "🟡", "green": "🟢"}
    for x in rows:
        if not x["count"] and x["level"] == "green":
            continue   # 完全没参数的机器人不占版面
        tail = ",".join("v%d" % v for v in x["param_versions"]) or "-"
        if x["module_count"]:
            tail += "（含模块参数 %d 条）" % x["module_count"]
        print("%-4s %-34s %-6s %-4d %-7d %s" % (
            icon[x["level"]], x["name"][:32],
            x["app_version"] if x["app_version"] >= 0 else "NULL",
            x["count"], x["span"], tail))

    null_appv = [x for x in rows if x["app_version"] < 0 and x["count"]]
    if red:
        print("\n🔴 高危（发布者账号点「添加应用」会整包 999999）：")
        for x in red:
            print("   · %s（%s）%d 条 / %d 字符 —— 占用最大：%s"
                  % (x["name"], x["robot_id"], x["count"], x["span"], "、".join(x["top"])))
        print("   处置：把静态值（URL/目录/开关/轮次）写死进节点，参数降到 %d 条以内；"
              "或给一组参数改成 1 个「配置文件路径」参数。" % pb.COUNT_WARN)
    if null_appv:
        print("\n⚠️ app_version 为 NULL（%d 个）：参数查询会退化成「取该机器人所有版本的参数」，"
              "条数会随发布版本累积 —— 这是参数膨胀的源头，建议对齐到线上版本。" % len(null_appv))
        for x in null_appv:
            print("   · %s（%s）%d 条 / %d 字符 · 参数版本 %s"
                  % (x["name"], x["robot_id"], x["count"], x["span"],
                     ",".join("v%d" % v for v in x["param_versions"]) or "-"))

    print("\n汇总：🔴 %d · 🟡 %d · 机器人共 %d 个" % (len(red), len(yellow), len(robots)))
    if not red:
        print("✅ 全部在安全线内")
    return 2 if (args.strict and red) else 0


if __name__ == "__main__":
    sys.exit(main())
