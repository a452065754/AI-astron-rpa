#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布审计（publish_audit.py）—— 只读查「谁发布了哪些版本、哪些已进添加应用列表」。

为什么要有它（2026-11-05，A20 事故复盘）
--------------------------------------
用户的判断是对的：**只有发布过的机器人才会进「定时任务 → 添加应用」列表**。
生产库里的证据是硬的：

    robot_execute.create_time  ==  robot_version.create_time（逐秒一致）
      · Py测试          v1  2026-11-05 03:20:03  → 执行态也是 03:20:03
      · MES包装标签生成  v1  2026-11-04 02:18:56  → 执行态也是 02:18:56

⇒ 「发布」这一步会**自动**生成 `robot_execute` 记录，机器人就此进入列表；
  而列表接口会把每个机器人的 `paramJson` 整段塞进响应、过日志脱敏正则，参数一多就爆栈。
  草稿（只有 `robot_design` + `c_process` + `c_param`）永远不进列表 ——
  生产库里 3 份 MES 草稿各 25 条参数、长期无事，就是反证。

所以「AI 自己在后台把流程发了」是这条事故链上唯一的自主环节。
`publish_gate.py` 已经把发布默认关掉；这个脚本负责**事后可查**：
定版之后再跑一次，就能看到每个版本是谁、什么时候发的，以及是否已进列表。

用法
----
    python scripts/publish_audit.py                 # 最近 20 个版本
    python scripts/publish_audit.py --limit 60      # 全量（当前 51 个）
    python scripts/publish_audit.py --me b7faea17-12f1-4b6d-9221-15b36919c09b
                                                   # 只看某个账号发的（怀疑是 AI 发的就查这个）

⚠️ **全程只读**：只跑 `SELECT`。需要 `paramiko`，复用 `param_health.query()` 的 SSH 通道。
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import param_health as ph   # noqa: E402  复用 SSH→容器 mysql 通道（只读）

SQL = """
SELECT v.robot_id, IFNULL(d.name,''), v.version, IFNULL(v.online,0),
       IFNULL(v.creator_id,''), DATE_FORMAT(v.create_time,'%%Y-%%m-%%d %%H:%%i:%%s'),
       IFNULL(DATE_FORMAT((SELECT MIN(e.create_time) FROM robot_execute e
                            WHERE e.robot_id = v.robot_id),'%%Y-%%m-%%d %%H:%%i:%%s'),''),
       (SELECT COUNT(*) FROM robot_execute e WHERE e.robot_id = v.robot_id),
       DATE_FORMAT(v.update_time,'%%Y-%%m-%%d %%H:%%i:%%s')
FROM robot_version v
LEFT JOIN robot_design d ON d.robot_id = v.robot_id AND d.deleted = 0
WHERE v.deleted = 0
ORDER BY v.create_time DESC
LIMIT %d
"""


def parse(out):
    rows = []
    for line in out.splitlines():
        if not line.strip():
            continue
        f = line.split("\t")
        if len(f) < 9:
            continue
        rid, name, ver, online, creator, ctime, etime, nexec, utime = f[:9]
        rows.append({
            "robot_id": rid, "name": name, "version": int(ver or 0),
            "online": int(online or 0), "creator": creator,
            "publish_at": ctime, "executor_at": etime,
            "executor": int(nexec or 0),
        })
    return rows


def main():
    ap = argparse.ArgumentParser(description="发布审计（只读）")
    ap.add_argument("--host", default=ph.default_host())
    ap.add_argument("--ssh-user", default=os.environ.get("RPA_SSH_USER", "xudongdong"))
    ap.add_argument("--ssh-password", default=os.environ.get("RPA_SSH_PASSWORD", "1234"))
    ap.add_argument("--container", default="rpa-opensource-mysql")
    ap.add_argument("--db-user", default="root")
    ap.add_argument("--db-password", default=os.environ.get("RPA_DB_PASSWORD", "rpa123456"))
    ap.add_argument("--db", default="rpa")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--me", default="", help="只看这个 creator_id 发布的版本（查「AI 发了什么」用）")
    a = ap.parse_args()

    out = ph.query(a.host, a.ssh_user, a.ssh_password, a.container,
                   a.db_user, a.db_password, a.db, SQL % max(a.limit, 1))
    rows = parse(out)
    if a.me:
        rows = [r for r in rows if r["creator"].startswith(a.me[:8])]

    print("=" * 96)
    print("发布审计 · %s · 只读（发布 = 自动生成 robot_execute = 进「添加应用」列表）" % a.host)
    print("=" * 96)
    print("%-19s %-26s %-5s %-4s %-10s %s" % ("发布时间", "机器人", "版本", "在线", "发布者", "执行态"))
    print("-" * 96)
    for r in rows:
        flag = "已进列表" if r["executor"] else "—"
        same = r["executor_at"] and r["executor_at"][:19] == r["publish_at"][:19]
        print("%-19s %-26s v%-4d %-4s %-10s %s%s" % (
            r["publish_at"], r["name"][:24], r["version"],
            "是" if r["online"] else "", r["creator"][:8],
            flag, "（与发布同秒 → 发布会自动建执行态）" if same else ""))

    ai = [r for r in rows if r["executor"]]
    print("\n汇总：%d 个版本 · 其中 %d 个已带执行态（在列表里）" % (len(rows), len(ai)))
    print("判读：")
    print("  · 想确认「AI 有没有偷偷发布」→ 看发布者，AI 用的是登录账号（通常是同一串 creator_id）")
    print("  · 已进列表的机器人再看参数预算：python scripts/param_health.py")
    print("  · 发布默认已被 publish_gate 关掉；要发必须 --confirm-publish，由人决定")
    return 0


if __name__ == "__main__":
    sys.exit(main())
