#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""窗口状态采样器（只读）：在流程跑的同时记录「前台窗口」和「目标窗口矩形」的变化。

为什么需要：Window.set_size / Window.top 这两个原子**没有返回值**，而桌面流程跑完窗口
往往已经被清理（引擎进程退出会带走它启动的子进程），事后再看就什么都看不到。
所以改成「跑的时候采样」——窗口什么时候出现、矩形什么时候变、什么时候跑到前台，全有记录。

    # 后台采样 40 秒，每 0.2 秒一条
    "%APPDATA%/astron-rpa/python_core/python.exe" scripts/win_watch.py \
        --title "远程桌面连接" --duration 40 --interval 0.2 --out demo/_gui_out/win_watch.log
"""

import argparse
import sys
import time

import win32gui


def foreground():
    h = win32gui.GetForegroundWindow()
    return h, win32gui.GetWindowText(h)


def find_all(title):
    hit = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        t = win32gui.GetWindowText(hwnd)
        if title in t:
            hit.append(hwnd)

    win32gui.EnumWindows(cb, None)
    return hit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True)
    ap.add_argument("--duration", type=float, default=40)
    ap.add_argument("--interval", type=float, default=0.2)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    t0 = time.time()
    lines = []
    last_state = None
    seen = 0
    while time.time() - t0 < args.duration:
        fh, ft = foreground()
        hits = find_all(args.title)
        seen = max(seen, len(hits))
        if hits:
            hwnd = hits[0]
            r = win32gui.GetWindowRect(hwnd)
            size = f"{r[2] - r[0]}x{r[3] - r[1]}"
            front = "FOREGROUND" if (fh == hwnd or args.title in ft) else "background"
            state = (len(hits), r[:2], size, front, ft)
        else:
            state = (0, None, "-", "absent", ft)
        if state != last_state:
            ts = time.time() - t0
            lines.append(
                f"{ts:6.2f}s  目标窗口数={state[0]:<2} 矩形起点={state[1]} 尺寸={state[2]:<10} "
                f"{state[3]:<11} 前台={state[4]!r}"
            )
            last_state = state
        time.sleep(args.interval)

    header = f"# 采样 {args.duration}s / 每 {args.interval}s；目标窗口最多同时出现 {seen} 个\n"
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(lines) + "\n")
    print(header + "\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
