#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""窗口状态探针（只读）：给窗口类流程留客观现场证据。

输出：前台窗口标题、指定窗口的句柄 / 矩形 / 是否最小化 / 是否置顶。

    "%APPDATA%/astron-rpa/python_core/python.exe" scripts/win_probe.py --title "星辰RPA测试"
"""

import argparse
import sys

import win32con
import win32gui


def find(title):
    hit = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        text = win32gui.GetWindowText(hwnd)
        if title in text:
            hit.append((hwnd, text, win32gui.GetClassName(hwnd)))

    win32gui.EnumWindows(cb, None)
    return hit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", help="窗口标题子串")
    args = ap.parse_args()

    fg = win32gui.GetForegroundWindow()
    print(f"前台窗口：{win32gui.GetWindowText(fg)!r}（句柄 {fg}）")

    if not args.title:
        return 0
    hits = find(args.title)
    if not hits:
        print(f"没找到标题含 {args.title!r} 的可见窗口")
        return 1
    for hwnd, text, cls in hits:
        rect = win32gui.GetWindowRect(hwnd)
        ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        print(
            f"窗口 {hwnd} cls={cls!r} 标题={text!r} "
            f"矩形=({rect[0]},{rect[1]})-({rect[2]},{rect[3]}) "
            f"尺寸={rect[2] - rect[0]}x{rect[3] - rect[1]} "
            f"最小化={bool(win32gui.IsIconic(hwnd))} "
            f"置顶={bool(ex & win32con.WS_EX_TOPMOST)} "
            f"前台={hwnd == fg}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
