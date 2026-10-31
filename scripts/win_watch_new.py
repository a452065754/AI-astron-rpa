#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""监视桌面上「新出现的顶层窗口」并记日志（只读）。

用途：验证 Dialog.* 弹窗到底有没有真的渲染出来 —— 弹窗是设计器前端画的，
不是引擎画的，所以「窗口真的出现在桌面上」才是不可伪造的证据。

    python scripts/win_watch_new.py --duration 60 --interval 0.2 --out demo/_gui_out/dlg_watch.log
"""

import argparse
import os
import time

import win32gui

# 这些是常驻窗口，出现/消失与本次测试无关，只记录新出现的
IGNORE_TITLE_PARTS = ("Program Manager", "Program Manager", "Windows 输入体验")


def snapshot():
    rows = set()

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            t = win32gui.GetWindowText(hwnd)
            if t.strip():
                rows.add((hwnd, win32gui.GetClassName(hwnd), t))

    win32gui.EnumWindows(cb, None)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, default=60)
    ap.add_argument("--interval", type=float, default=0.2)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    lines = []

    def emit(s):
        lines.append(s)
        print(s, flush=True)
        if args.out:
            os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
            with open(args.out, "a", encoding="utf-8") as f:
                f.write(s + "\n")

    known = snapshot()
    emit("=== 监视开始 === 已有窗口 {} 个".format(len(known)))
    t0 = time.time()
    seen = set()
    while time.time() - t0 < args.duration:
        now = snapshot()
        for hwnd, cls, title in now - known:
            if any(p in title for p in IGNORE_TITLE_PARTS):
                continue
            if hwnd in seen:
                continue
            seen.add(hwnd)
            l, t, r, b = win32gui.GetWindowRect(hwnd)
            emit(
                "{:7.2f}s  新窗口 hwnd={} cls={} 矩形=({},{},{}x{}) 标题={!r}".format(
                    time.time() - t0, hwnd, cls, l, t, r - l, b - t, title
                )
            )
        gone = known - now
        for hwnd, cls, title in gone:
            if hwnd in seen:
                emit("{:7.2f}s  窗口消失 hwnd={} 标题={!r}".format(time.time() - t0, hwnd, title))
        known = now
        time.sleep(args.interval)
    emit("=== 监视结束 === 共发现 {} 个新窗口".format(len(seen)))


if __name__ == "__main__":
    main()
