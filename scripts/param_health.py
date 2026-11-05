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

判据必须与平台 SQL 严格对齐（2026-11-05 二次校正，改掉了一版误报）
--------------------------------------------------------------
`CParamDao.xml` 里是**硬等值**，没有 `<if>` 兜底：

    select * from c_param
     where robot_id = #{robotId} and process_id = #{processId}
       and robot_version = #{robotVersion} and deleted = 0

两个后果，都与「想当然」相反：

1. **`app_version` 为 NULL → 一条参数都取不到**，`paramJson` 为空，**不会爆栈**。
   （MySQL 里 `robot_version = NULL` 恒为假，不是「退化成全捞」。）
   这类机器人是「参数全丢」问题，不是 A20。
2. **模块参数（`module_id` 非空）永远进不来**：它们在 `c_param` 里 `process_id` 全部为
   NULL，而 SQL 要求 `process_id` 等值。所以 `paramJson` 只含**流程参数**。

⇒ 估算口径 = 该机器人 `robot_version = app_version` 且 `process_id is not null` 的参数。
   同版本有多个 `process_id` 时按**上界**算（实际只取主流程那一个）。

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
# 口径必须复刻「定时任务 → 添加应用」这条链路的真实取数（2026-11-05 第三次校正）：
#
#   1) `TriggerTaskServiceImpl.getRobotExeList`
#        → `RobotExecuteDao.getRobotExecuteByName`（SQL 里 left join robot_version，取 online=1）
#        → `packageCreateRobotVersion`：`if ("create".equals(dataSource)) setAppVersion(rv.version)`
#      ⇒ **实际生效的版本是线上版本（robot_version.online=1），不是 robot_execute.app_version。**
#        （15 台全是 data_source='create'，所以这条覆盖对每一台都成立。）
#
#   2) `getExecutorList` → `CParamService.getAllParams(mode=EXECUTOR)`
#        → `ExecutorModeHandler`：processId 为空则 `getMianProcessId(robotId, appVersion)`
#        → `CParamDao.getAllParams(processId, robotId, version)`
#      `getAllParams` 的 mapper 是 **`<if>` 而非等值**：
#        `<if test="processId!=null">`、`<if test="version!=null">` 一起被跳过 →
#        `where robot_id=? and deleted=0` → **该机器人所有版本 + 所有模块参数全被捞出来**。
#      ⇒ 只有「没有线上版本」的机器人（rv.version 为 NULL）才会走这条全量路径，也才是真雷。
#
# 反面教材：本脚本第一版拿 `robot_execute.app_version` 当生效版本，把 14 台「无线上版本」
# 的机器人全算成全量，报了 3 个假红灯；第二版又以为 `app_version` 为 NULL 会取 0 条 —— 也不对。
# 两版都错在看错调用链。判据只认：**线上版本有值 → 只算该版本主流程参数；无线上版本 → 全量**。
_SQL = """
SELECT r.robot_id, IFNULL(r.name,''),
       IFNULL(CASE WHEN r.data_source = 'create' THEN rv.version ELSE r.app_version END, -1),
       IFNULL(p.robot_version,-1), IFNULL(p.var_name,''), IFNULL(p.var_type,'Str'),
       REPLACE(REPLACE(IFNULL(p.var_value,''),CHAR(9),' '),CHAR(10),' '),
       REPLACE(REPLACE(IFNULL(p.var_describe,''),CHAR(9),' '),CHAR(10),' '),
       IFNULL(p.process_id,'')
FROM robot_execute r
LEFT JOIN robot_version rv
       ON rv.robot_id = r.robot_id AND rv.deleted = 0 AND rv.online = 1
LEFT JOIN c_param p
       ON p.robot_id = r.robot_id AND p.deleted = 0
      AND (  ( (CASE WHEN r.data_source = 'create' THEN rv.version ELSE r.app_version END) IS NOT NULL
               AND p.process_id IS NOT NULL
               AND p.robot_version = (CASE WHEN r.data_source = 'create' THEN rv.version
                                           ELSE r.app_version END) )
          OR ( (CASE WHEN r.data_source = 'create' THEN rv.version ELSE r.app_version END) IS NULL
               AND (p.process_id IS NOT NULL OR p.module_id IS NOT NULL) ) )
WHERE r.deleted = 0
ORDER BY r.name, p.var_name
"""

# 「线上版本失效」时的最坏情况：robot_version 里没有 online=1 的记录 →
# `getAllParams` 的 `<if>` 双双跳过 → 该机器人**所有版本 + 所有模块参数**一次性进 paramJson。
# MES 2026-11-05 的事故就是这条路径（当时没有线上版本，58 条 / 2053 字符）。
# 现在全绿只代表「当前有线上版本」，不代表「以后一直在」—— 所以单独推演一遍。
_SQL_WORST = """
SELECT r.robot_id, IFNULL(r.name,''), -1,
       IFNULL(p.robot_version,-1), IFNULL(p.var_name,''), IFNULL(p.var_type,'Str'),
       '','', IFNULL(p.process_id,'')
FROM robot_execute r
JOIN c_param p
  ON p.robot_id = r.robot_id AND p.deleted = 0
 AND (p.process_id IS NOT NULL OR p.module_id IS NOT NULL)
WHERE r.deleted = 0
ORDER BY r.name, p.var_name
"""


def default_host():
    try:
        import rpa_config as cfg
        return cfg.host_port().split(":")[0]
    except Exception:
        return "192.168.0.183"


def query(host, ssh_user, ssh_pw, container, db_user, db_pw, db, sql=None):
    import paramiko

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, port=22, username=ssh_user, password=ssh_pw, timeout=15)
    try:
        cmd = ("docker exec -i -e MYSQL_PWD=%s %s mysql -u%s -N -B %s 2>&1"
               % (db_pw, container, db_user, db))
        stdin, stdout, stderr = client.exec_command(cmd, timeout=180)
        stdin.write((sql or _SQL).strip() + ";\n")
        stdin.flush()
        stdin.channel.shutdown_write()
        out = stdout.read().decode("utf-8", "replace")
    finally:
        client.close()
    if out.startswith("ERROR") or "mysql: [Warning]" in out[:40] and "\t" not in out:
        raise SystemExit("SQL 执行失败：\n%s" % out[:800])
    return out


def parse(out):
    """→ {robot_id: {"name","app_version","params":[{...}],"process_ids":set()}}"""
    robots = {}
    for line in out.splitlines():
        if not line.strip():
            continue
        f = line.split("\t")
        if len(f) < 9:
            continue
        rid, name, appv, pv, vn, vt, vv, vd, pid = f[:9]
        r = robots.setdefault(rid, {"name": name, "app_version": int(appv or -1),
                                    "params": [], "process_ids": set()})
        if vn:  # LEFT JOIN 的空行
            r["params"].append({"varName": vn, "varType": vt, "varValue": vv,
                                "varDescribe": vd, "_ver": int(pv or -1)})
            if pid:
                r["process_ids"].add(pid)
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
    worst = parse(query(args.host, args.ssh_user, args.ssh_password, args.container,
                        args.db_user, args.db_password, args.db, _SQL_WORST))

    rows = []
    for rid, r in robots.items():
        st = pb.summary(r["params"])
        versions = sorted({p["_ver"] for p in r["params"]})
        n_proc = len(r["process_ids"])
        # app_version < 0 = 没有线上版本 → 平台走 getAllParams 全量路径（含历史版本 + 模块参数）
        full_scan = r["app_version"] < 0
        w = pb.summary((worst.get(rid) or {}).get("params") or [])
        rows.append({
            "robot_id": rid, "name": r["name"], "app_version": r["app_version"],
            "full_scan": full_scan,
            "count": st["count"], "span": st["span"], "level": st["level"],
            "process_count": n_proc,
            "worst_count": w["count"], "worst_span": w["span"], "worst_level": w["level"],
            "param_versions": versions,
            "top": [n for _s, n, _v in pb.advise(r["params"])[:5]],
        })
    rows.sort(key=lambda x: (-x["span"], x["name"]))

    red = [x for x in rows if x["level"] == "red"]
    yellow = [x for x in rows if x["level"] == "yellow"]
    full = [x for x in rows if x["full_scan"]]
    # 当前绿、但线上版本一旦失效就会红 —— 这才是「埋着的雷」
    landmine = [x for x in rows if x["level"] != "red" and x["worst_level"] == "red"]

    if args.json:
        print(json.dumps({"host": args.host, "robots": rows,
                          "red": len(red), "yellow": len(yellow),
                          "no_online_version": len(full),
                          "limits": {"span_warn": pb.SPAN_WARN, "span_err": pb.SPAN_ERR,
                                     "count_err": pb.COUNT_ERR}},
                         ensure_ascii=False, indent=2))
        return 2 if (args.strict and red) else 0

    print("=" * 88)
    print("流程参数体检 · %s · 只读" % args.host)
    print("口径：「添加应用」用 **线上版本**（robot_version.online=1；data_source='create' 时覆盖 app_version）")
    print("      有线上版本 → 只算该版本主流程参数；无线上版本 → getAllParams 全量捞（历史版本+模块参数）")
    print("判定：paramJson 估算 ≥%d 字符 或 参数 >%d 条 → 红（点「添加应用」必报 999999）"
          % (pb.SPAN_ERR, pb.COUNT_ERR))
    print("      ≥%d 字符 或 >%d 条 → 黄（接近悬崖，建议收敛；工程目标 ≤%d 字符）"
          % (pb.SPAN_WARN, pb.COUNT_WARN, pb.SPAN_WARN))
    print("=" * 88)
    print("%-4s %-34s %-6s %-4s %-7s %s" % ("级别", "机器人", "线上版", "条数", "估算", "参数版本"))
    print("-" * 88)
    icon = {"red": "🔴", "yellow": "🟡", "green": "🟢"}
    for x in rows:
        if not x["count"] and x["level"] == "green":
            continue   # 没参数的机器人不占版面
        tail = ",".join("v%d" % v for v in x["param_versions"]) or "-"
        if x["full_scan"]:
            tail = "无线上版本 → 全量捞取；参数版本 " + tail
        elif x["process_count"] > 1:
            tail += "（跨 %d 个流程，估算为上界）" % x["process_count"]
        print("%-4s %-34s %-6s %-4d %-7d %s" % (
            icon[x["level"]], x["name"][:32],
            x["app_version"] if x["app_version"] >= 0 else "无",
            x["count"], x["span"], tail))

    if red:
        print("\n🔴 高危（发布者账号点「添加应用」会整包 999999）：")
        for x in red:
            print("   · %s（%s）%d 条 / %d 字符%s —— 占用最大：%s"
                  % (x["name"], x["robot_id"], x["count"], x["span"],
                     "，**无线上版本导致全量捞**" if x["full_scan"] else "",
                     "、".join(x["top"])))
        print("   处置：①把静态值（URL/目录/开关/轮次）写死进节点，参数降到 %d 条以内；"
              "②确认该机器人有 online=1 的 robot_version 记录（没有就会全量捞）。" % pb.COUNT_WARN)
    if full:
        print("\n⚠️ 无线上版本（%d 个）：`添加应用` 会全量捞历史版本 + 模块参数，条数随时可能翻几倍。"
              % len(full))
        for x in full:
            print("   · %s（%s）当前 %d 条 / %d 字符"
                  % (x["name"], x["robot_id"], x["count"], x["span"]))
    if landmine:
        print("\n💣 现在安全、但线上版本一失效就会爆（%d 个）：" % len(landmine))
        for x in landmine:
            print("   · %s（%s）当前 v%s %d 条/%d 字符 → 全量 %d 条/%d 字符"
                  % (x["name"], x["robot_id"], x["app_version"] if x["app_version"] >= 0 else "?",
                     x["count"], x["span"], x["worst_count"], x["worst_span"]))
        print("   成因：`robot_version` 表没有 online=1 的记录时，`getAllParams` 的版本条件被跳过；"
              "MES 2026-11-05 的事故即此。处置：清理历史版本参数（deleted=1），别只靠线上版本兜着。")

    print("\n汇总：🔴 %d · 🟡 %d · 无线上版本 %d · 💣 线上版本失效后爆 %d · 机器人共 %d 个"
          % (len(red), len(yellow), len(full), len(landmine), len(robots)))
    if not red:
        print("✅ 全部在安全线内")
    return 2 if (args.strict and red) else 0


if __name__ == "__main__":
    sys.exit(main())
