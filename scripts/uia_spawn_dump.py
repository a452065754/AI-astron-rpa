#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性：启动宿主程序并在同一进程内 dump 它的 UIA 树（避免窗口随父进程/时序消失）。

    "%APPDATA%/astron-rpa/python_core/python.exe" scripts/uia_spawn_dump.py --exe C:\\Windows\\System32\\mstsc.exe --depth 3
"""

import argparse
import subprocess
import sys
import time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    ap.add_argument("--args", default="")
    ap.add_argument("--depth", type=int, default=3)
    ap.add_argument("--wait", type=float, default=3.0)
    args = ap.parse_args()

    cmd = [args.exe] + ([args.args] if args.args else [])
    p = subprocess.Popen(cmd)
    time.sleep(args.wait)

    import uiautomation as auto

    root = auto.GetRootControl()
    cands = [c for c in root.GetChildren() if c.ProcessId == p.pid]
    if not cands:
        # 有些宿主会起子进程（UWP/UWP 容器），退化成按标题找
        print(f"pid={p.pid} 没有直属顶层窗口，列出全部可见顶层窗口：")
        for c in root.GetChildren():
            print(f"  pid={c.ProcessId} {c.ControlTypeName} cls={c.ClassName!r} title={c.Name!r}")
        return 1

    win = cands[0]
    print(f"窗口：{win.Name!r} cls={win.ClassName!r} pid={win.ProcessId} hwnd={win.NativeWindowHandle}")

    def dump(ctrl, level, prefix=""):
        try:
            r = ctrl.BoundingRectangle
            box = f"({r.left},{r.top},{r.right},{r.bottom})"
        except Exception:
            box = "(?)"
        nm = (ctrl.Name or "").replace("\n", " ")[:38]
        print(
            f"{prefix}{ctrl.ControlTypeName:<20} name={nm!r:<42} cls={ctrl.ClassName!r:<22} "
            f"auto={ctrl.AutomationId!r:<14} {box}"
        )
        if level >= args.depth:
            return
        try:
            kids = ctrl.GetChildren()
        except Exception:
            return
        for k in kids:
            dump(k, level + 1, prefix + "  ")

    dump(win, 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
