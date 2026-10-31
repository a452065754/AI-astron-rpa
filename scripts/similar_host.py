#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""相似元素靶场：窗口 → 列表(ListBox) → 列表项，三层结构。

为什么必须是三层：
`uia_locator.__find_one__` 在 path 只有一层（只有窗口）时，
第 390 行的判定 `i == (len(node_list) - 2)` 恒为 `0 == -1` → 永远不成立，
于是必然抛「元素无法找到」。而 `__find_similar__` 正是拿
「similar_parent 标记的那几层」去调 `__find_one__` 找父节点 ——
所以相似元素的**父节点必须至少是「窗口 + 容器」两层**，
否则无论 path 怎么造都定位不到父节点。

真实界面天然满足：资源管理器（窗口→列表视图→列表项）、
浏览器（窗口→文档→链接）都是三层以上。

用法：
    "%APPDATA%/astron-rpa/python_core/python.exe" scripts/similar_host.py            # 起窗口
    "%APPDATA%/astron-rpa/python_core/python.exe" scripts/similar_host.py --close    # 关窗口
    "%APPDATA%/astron-rpa/python_core/python.exe" scripts/similar_host.py --items 5
"""

import argparse
import sys

TITLE = "星辰相似元素靶场"
ITEMS = ["项目一", "项目二", "项目三", "项目四", "项目五", "项目六"]


def _close():
    import win32con
    import win32gui

    hits = []

    def cb(h, _):
        if win32gui.IsWindowVisible(h) and TITLE in (win32gui.GetWindowText(h) or ""):
            hits.append(h)

    win32gui.EnumWindows(cb, None)
    if not hits:
        print("没找到靶场窗口（可能已经关了）")
        return 1
    for h in hits:
        win32gui.PostMessage(h, win32con.WM_CLOSE, 0, 0)
        print(f"已请求关闭 hwnd={h}")
    return 0


def _run(n_items):
    import win32con
    import win32gui

    hinst = win32gui.GetModuleHandle(None)

    # 必须带 WS_EX_APPWINDOW：不带的话 UIA 的根节点下根本看不到这个窗口
    # （根子节点里会缺它，拾取器按标题找窗口就会失败）
    exstyle = win32con.WS_EX_APPWINDOW | win32con.WS_EX_WINDOWEDGE

    hwnd = win32gui.CreateWindowEx(
        exstyle,
        "#32770",
        TITLE,
        win32con.WS_OVERLAPPEDWINDOW | win32con.WS_VISIBLE | win32con.WS_CLIPCHILDREN,
        120,
        120,
        460,
        360,
        None,
        None,
        hinst,
        None,
    )

    hwnd_lb = win32gui.CreateWindowEx(
        0,
        "LISTBOX",
        "",
        win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.WS_VSCROLL | win32con.LBS_NOTIFY,
        12,
        12,
        424,
        280,
        hwnd,
        None,
        hinst,
        None,
    )

    LB_ADDSTRING = 0x0180
    for name in ITEMS[:n_items]:
        win32gui.SendMessage(hwnd_lb, LB_ADDSTRING, 0, name)

    win32gui.SetForegroundWindow(hwnd)
    try:
        print(f"靶场已就绪 hwnd={hwnd} listbox={hwnd_lb} 项目数={min(n_items, len(ITEMS))}", flush=True)
    except Exception:  # noqa: BLE001  (detached 模式下标准输出可能不可用)
        pass

    # 标准 Win32 消息泵（窗口关闭时收到 WM_QUIT 自动退出）
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--items", type=int, default=4, help="列表项个数（默认 4）")
    ap.add_argument("--close", action="store_true", help="关闭已存在的靶场窗口")
    args = ap.parse_args()
    if args.close:
        return _close()
    return _run(args.items)


if __name__ == "__main__":
    sys.exit(main())
