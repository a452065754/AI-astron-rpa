#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""桌面 UIA 树查看器（只读，不改任何东西、不点任何东西）。

用途：给「窗口/Windows 元素」类流程挑元素时，先看清窗口里的控件层级，
     再决定用哪个控件、以及 path 要固定哪几层。

    # 列出所有顶层窗口（标题 + 类名 + 进程名）
    "%APPDATA%/astron-rpa/python_core/python.exe" scripts/uia_dump.py --windows

    # 按标题子串 dump 某个窗口的控件树
    ... scripts/uia_dump.py --title "星辰RPA测试"
    ... scripts/uia_dump.py --title "星辰RPA测试" --depth 6 --out demo/_gui_out/uia_tree.txt
"""

import argparse
import io
import sys


def _auto():
    import uiautomation

    return uiautomation


def list_windows(auto, keyword=""):
    root = auto.GetRootControl()
    rows = []
    for c in root.GetChildren():
        title = c.Name or ""
        if keyword and keyword.lower() not in title.lower():
            continue
        rows.append((c.ControlTypeName, title, c.ClassName, c.ProcessId))
    return rows


def find_window(auto, title):
    """按标题子串找窗口控件（取第一个命中）。"""
    root = auto.GetRootControl()
    for c in root.GetChildren():
        if title in (c.Name or "") and c.ControlTypeName == "WindowControl":
            return c
    # UWP/某些应用顶层是 PaneControl
    for c in root.GetChildren():
        if title in (c.Name or ""):
            return c
    return None


def dump(control, depth, lines, prefix="", level=0):
    """递归打印控件：控件类型 / 名称 / 类名 / AutomationId / 矩形。"""
    try:
        rect = control.BoundingRectangle
        box = f"({rect.left},{rect.top},{rect.right},{rect.bottom})"
    except Exception:
        box = "(?)"
    name = (control.Name or "").replace("\n", " ")[:40]
    lines.append(
        f"{prefix}{control.ControlTypeName:<22} name={name!r:<44} cls={control.ClassName!r:<26} "
        f"auto={control.AutomationId!r:<18} {box}"
    )
    if level >= depth:
        return
    try:
        children = control.GetChildren()
    except Exception:
        return
    for ch in children:
        dump(ch, depth, lines, prefix + "  ", level + 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", action="store_true", help="只列顶层窗口")
    ap.add_argument("--title", help="窗口标题子串")
    ap.add_argument("--hwnd", type=int, help="直接用窗口句柄（顶层枚举看不到 UWP/WinUI 窗口时用这个）")
    ap.add_argument("--depth", type=int, default=5, help="递归层数，默认 5")
    ap.add_argument("--out", help="把结果写到文件（UTF-8）")
    ap.add_argument("--max", type=int, default=400, help="最多打印多少行")
    args = ap.parse_args()

    auto = _auto()
    lines = []

    if args.windows:
        rows = list_windows(auto, args.title or "")
        lines.append(f"顶层窗口 {len(rows)} 个：")
        for t, title, cls, pid in rows:
            lines.append(f"  {t:<20} pid={pid:<8} cls={cls!r:<28} title={title!r}")
    else:
        if args.hwnd:
            # 先碰一次根控件：让 uiautomation 初始化当前线程的 COM/UIA 客户端。
            # 不初始化直接 ControlFromHandle 会报 COMError「事件无法调用任何订户」。
            auto.GetRootControl()
            win = auto.ControlFromHandle(args.hwnd)
        elif args.title:
            win = find_window(auto, args.title)
        else:
            ap.error("要么 --windows，要么 --title / --hwnd")
        if win is None:
            print(f"没找到标题含 {args.title!r} 的窗口，先跑 --windows --hwnd 看看")
            return 2
        lines.append(f"窗口：{win.Name!r}  cls={win.ClassName!r}  pid={win.ProcessId}")
        dump(win, args.depth, lines)

    text = "\n".join(lines[: args.max])
    print(text)
    if args.out:
        with io.open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"\n（已写入 {args.out}，共 {len(lines)} 行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
