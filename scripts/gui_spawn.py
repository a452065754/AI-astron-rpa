#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""脱离父进程启动一个 GUI 程序（让窗口在脚本退出后还能活着）。

为什么需要：从工具/脚本里 `Start-Process` 或 `Popen` 起的 GUI，会随父进程树一起被清理，
而「桌面元素」类流程必须有活窗口，且窗口得活到**流程结束之后**，才能在外面核对
窗口矩形（Window.set_size）、前台窗口（Window.top）这些结果。

做法：DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP，不继承控制台、不共用进程组。

    "%APPDATA%/astron-rpa/python_core/python.exe" scripts/gui_spawn.py --exe C:\\Windows\\System32\\mstsc.exe
"""

import argparse
import subprocess
import sys
import time

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    ap.add_argument("--args", default="")
    ap.add_argument("--wait", type=float, default=3.0)
    args = ap.parse_args()

    cmd = [args.exe] + ([args.args] if args.args else [])
    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB
    try:
        p = subprocess.Popen(cmd, creationflags=flags, close_fds=True, cwd="C:\\")
    except OSError:
        # 有些环境不允许 breakaway（不在 job 里），退一步只 detached
        p = subprocess.Popen(
            cmd, creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP, close_fds=True, cwd="C:\\"
        )
    time.sleep(args.wait)
    print(f"已启动 pid={p.pid} 命令={cmd}")
    print("（本脚本退出后窗口应仍在；用 scripts/win_probe.py 确认）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
