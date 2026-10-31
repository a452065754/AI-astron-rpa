# -*- coding: utf-8 -*-
"""A10 · 浏览器桥接：扩展 WebSocket 空闲即断，桥接侧不会唤醒

现象：Chrome 一直开着、扩展（Astron Cowork Plugin）也一直启用，但闲置一段时间后
任何 `Browser*` 原子都报「插件无响应」。此时命令行探一下桥接，返回
`{"code": "1001", "msg": "send uuid empty"}` —— 桥接侧认为这个浏览器没有连接的扩展。

真因不在扩展本身（它连的是 `ws://127.0.0.1:9082/ws`，能连上、也能被注入脚本），
而在 MV3 的 service worker 会被浏览器挂起：WS 随之断开，而桥接侧**只在有连接时
才有通道，连不上不会去主动唤醒扩展**。实测断开后 60 秒内不会自动重连；
只要开一个新窗口/新标签让 worker 醒来，立刻恢复。

本脚本只读日志、不发任何请求，不需要 Chrome 在线：
    "%APPDATA%/astron-rpa/python_core/python.exe" docs/repro/A10_bridge_idle_disconnect.py

判定口径（两个条件同时成立才算复现）：
    1. 存在「连接存活 < 30s」的会话（说明不是人为关闭浏览器，是空闲被挂起）
    2. 该会话断开后到下次连上的空档 > 30s（说明桥接没有主动重连/唤醒机制）

想现场看 1001，加 --probe（要求 Chrome 正在运行）：
    ... A10_bridge_idle_disconnect.py --probe
"""
import argparse
import datetime as dt
import glob
import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import (  # noqa: E402
    NOT_REPRODUCED,
    REPRODUCED,
    fail_detail,
    main_guard,
    report,
)

LOG_DIR = os.path.join(os.environ.get("APPDATA", ""), "astron-rpa", "logs")
PAT = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+) .*_(add|del)_conn \$(\w+)\$")
PROBE_URL = "http://127.0.0.1:13159/browser_connector/browser/transition"


def read_events():
    events = []
    for path in sorted(glob.glob(os.path.join(LOG_DIR, "browser_bridge-*.log"))):
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = PAT.match(line)
                if m:
                    ts = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S.%f")
                    events.append((ts, m.group(2), m.group(3), os.path.basename(path)))
    events.sort(key=lambda x: x[0])
    return events


def sessions(events):
    """把 add/del 配成会话，返回 [开始, 存活秒, 浏览器, 断开时间, 断到下次连上的空档, 来源文件]。"""
    open_at = {}
    rows = []
    for ts, kind, browser, src in events:
        if kind == "add":
            open_at[browser] = ts
        elif kind == "del" and browser in open_at:
            start = open_at.pop(browser)
            rows.append([start, (ts - start).total_seconds(), browser, ts, None, src])
    # 空档只在**同一个日志文件内**配对：跨天/跨进程重启的间隔没有分析意义
    for i, row in enumerate(rows):
        for nxt in rows[i + 1:]:
            if nxt[2] == row[2] and nxt[5] == row[5]:
                row[4] = (nxt[0] - row[3]).total_seconds()
                break
    return rows


def probe():
    body = json.dumps({"browser_type": "chrome", "key": "getTitle", "data": {}, "data_path": ""}).encode()
    req = urllib.request.Request(PROBE_URL, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read().decode("utf-8", "replace")[:300]
    except Exception as e:  # noqa: BLE001
        return f"请求失败：{fail_detail(e)}"


def run():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true", help="额外发一次桥接探测（需要 Chrome 在跑）")
    args = ap.parse_args()

    events = read_events()
    if not events:
        return report(
            "A10",
            "浏览器桥接空闲断连",
            "日志里能读到扩展的连接/断开事件",
            f"{LOG_DIR} 下没有 browser_bridge-*.log 或其中没有 _add_conn/_del_conn 记录",
            "BLOCKED",
            "本脚本靠日志取证，请在跑过浏览器原子、且安装了扩展的机器上运行。",
        )

    rows = sessions(events)
    print(f"日志目录：{LOG_DIR}")
    print(f"读到 {len(events)} 条连接事件，配成 {len(rows)} 次会话（最近 8 次）：")
    print(f"  {'开始时间':20s} {'存活(s)':>8s} {'断到下次连上(s)':>16s}  浏览器")
    for start, life, browser, _end, gap, _src in rows[-8:]:
        gap_txt = "—" if gap is None else f"{gap:.1f}"
        print(f"  {start:%Y-%m-%d %H:%M:%S}  {life:8.1f} {gap_txt:>16s}  {browser}")

    short = [r for r in rows if r[1] < 30]
    idle_big = [r for r in rows if r[1] < 30 and r[4] is not None and r[4] > 30]

    probe_txt = ""
    if args.probe:
        p = probe()
        print(f"\n桥接探测返回：{p}")
        probe_txt = f"；现场探测返回 {p}"

    if not short:
        return report(
            "A10",
            "浏览器桥接空闲断连",
            "存在短命会话（存活 < 30s）",
            f"{len(rows)} 次会话里没有存活 < 30s 的（最短 {min((r[1] for r in rows), default=0):.1f}s）",
            NOT_REPRODUCED,
            "本次日志里没出现空闲断连，可换一段有闲置的日志再跑。",
        )

    stat = (
        f"共 {len(rows)} 次会话，其中 {len(short)} 次存活不足 30 秒"
        f"（最短 {min(r[1] for r in short):.1f}s）；"
        f"{len(idle_big)} 次断开后空档超过 30 秒才重连"
        f"（最长 {max((r[4] for r in idle_big), default=0):.1f}s）"
    )

    if not idle_big:
        return report(
            "A10",
            "浏览器桥接空闲断连",
            "断开后能快速自动重连（空档 < 30s）",
            stat + "；但每次都能在 30 秒内自己连回来",
            NOT_REPRODUCED,
            "只有「断得勤」没有「不重连」，影响较小，可降级为观察项。",
        )

    last = idle_big[-1]
    return report(
        "A10",
        "扩展 WebSocket 空闲即断，桥接侧不唤醒 → 浏览器原子报「插件无响应」",
        "Chrome 开着、扩展启用时，任意时刻调用 Browser* 原子都应立即得到页面数据；"
        "若扩展连接会因空闲被挂起，桥接侧应在调用时主动唤醒它",
        stat
        + f"；最近一次典型：{last[0]:%H:%M:%S} 连上，{last[1]:.1f} 秒后断开，"
        f"隔 {last[4]:.1f} 秒才有下一次连接"
        + probe_txt,
        REPRODUCED,
        "手动验证步骤（需要人看着，无法脚本化）："
        "① 打开 Chrome 并保持扩展启用，什么都不做放 1 分钟；"
        "② `curl -X POST http://127.0.0.1:13159/browser_connector/browser/transition "
        "-H \"Content-Type: application/json\" "
        "-d '{\"browser_type\":\"chrome\",\"key\":\"getTitle\",\"data\":{},\"data_path\":\"\"}'` "
        "→ 返回 `code 1001 send uuid empty`；"
        "③ 在 Chrome 里新开一个标签页，立刻重发同一请求 → 返回 `code 0000` 并带回页面标题。"
        "建议桥接侧在收到调用时先检查连接是否存活，不存活就主动唤起扩展（或让扩展用 "
        "alarms 做定时心跳，而不是依赖 service worker 常驻）。",
    )


main_guard(run)
