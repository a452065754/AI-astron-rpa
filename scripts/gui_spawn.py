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
import shlex
import subprocess
import sys
import time

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def split_args(text):
    """把 --args 字符串拆成参数列表。

    用 posix=False 拆（Windows 路径里的反斜杠不能被当转义符），再把成对引号剥掉
    —— posix=False 会把引号保留在 token 里，直接传给 Popen 会导致「文件名带引号」→ 找不到文件。
    """
    if not text:
        return []
    parts = shlex.split(text, posix=False)
    out = []
    for p in parts:
        if len(p) >= 2 and p[0] == '"' and p[-1] == '"':
            p = p[1:-1]
        out.append(p)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    ap.add_argument("--args", default="")
    ap.add_argument("--wait", type=float, default=3.0)
    args = ap.parse_args()

    cmd = [args.exe] + split_args(args.args)
    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB
    # DETACHED_PROCESS 下标准句柄不连控制台，子进程里的 print(flush=True) 会抛 OSError
    # 直接把进程打死（表现为「窗口刚出现就没了」）。所以显式重定向到 DEVNULL。
    redir = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    try:
        p = subprocess.Popen(cmd, creationflags=flags, close_fds=True, cwd="C:\\", **redir)
    except OSError:
        # 有些环境不允许 breakaway（不在 job 里），退一步只 detached
        p = subprocess.Popen(
            cmd,
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
            cwd="C:\\",
            **redir,
        )
    time.sleep(args.wait)
    print(f"已启动 pid={p.pid} 命令={cmd}")
    print("（本脚本退出后窗口应仍在；用 scripts/win_probe.py 确认）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
