# -*- coding: utf-8 -*-
"""A13 · Dialog 的四个输入类原子用裸 done.wait()（无超时）→ 无人应答就永久挂起

现象：`Dialog.input_box` / `Dialog.select_box` / `Dialog.select_time_box` /
`Dialog.select_file_box` 四个原子，在**没有人去点弹窗**时（比如 openapi 跑流程、
脚本跑流程、CI 跑流程、或者设计器弹窗被关掉但没走完回调），
流程会**永久卡住**，不报错、不超时、不返回，线程一直占着。

真因：这四个原子的等待写成 `done.wait()`，**没有超时参数**：

    dialog/dialog.py:267   input_box        done.wait()
    dialog/dialog.py:329   select_box       done.wait()
    dialog/dialog.py:420   select_time_box  done.wait()
    dialog/dialog.py:539   select_file_box  done.wait()

而同文件里的两个兄弟原子是有超时的：

    dialog/dialog.py:174   message_box      wait_with_timeout(..., wait_time=wait_time)
    dialog/dialog.py:621   custom_box       wait_with_timeout(..., wait_time=wait_time)

`done` 这个 Event 只在回调 `callback_func` 被调用时才会 set，而回调只可能由
设计器那边（人点了按钮）触发 —— 没人应答就永远等下去。

本脚本：
  1. 静态：用 AST 把「谁用裸 wait()、谁用 wait_with_timeout」列清楚
  2. 动态：把这四个原子各丢进一个线程，6 秒后看线程是否还活着
  3. 对照：message_box(wait_time=3) 应当 3 秒左右自己返回

运行：
    "%APPDATA%/astron-rpa/python_core/python.exe" docs/repro/A13_dialog_no_timeout_hang.py
"""
import ast
import inspect
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import (  # noqa: E402
    NOT_REPRODUCED,
    REPRODUCED,
    fail_detail,
    main_guard,
    report,
)

WAIT_SECONDS = 6

# 四个「裸 wait()」的原子（函数名 → 调用参数）
HANGERS = [
    ("input_box", {}),
    ("select_box", {}),
    ("select_time_box", {}),
    ("select_file_box", {}),
]


def static_probe():
    """AST 层面统计：每个原子函数里的等待写法。"""
    from astronverse.dialog.dialog import Dialog

    src_path = inspect.getsourcefile(Dialog)
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Dialog")

    rows = []
    for fn in cls.body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if fn.name.startswith("_"):
            continue
        bare, timed = [], []
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Attribute) and f.attr == "wait":
                    # Event.wait(timeout=None) → 裸 wait
                    bare.append(node.lineno if not node.args else node.lineno)
                elif isinstance(f, ast.Name) and f.id == "wait_with_timeout":
                    timed.append(node.lineno)
        if bare or timed:
            rows.append((fn.name, bare, timed))
    return src_path, rows


def run():
    from astronverse.actionlib.atomic import atomicMg
    from astronverse.dialog.dialog import Dialog

    # 保证走「没有 WS」的分支（脚本/openapi 环境本来就没有 WS）
    atomicMg.cfg().pop("WS", None)

    # ---------------- 1. 静态 ----------------
    src_path, rows = static_probe()
    print("静态分析：", src_path)
    print(f"  {'原子':20s}{'裸 done.wait()':22s}wait_with_timeout")
    bare_fns, timed_fns = [], []
    for name, bare, timed in rows:
        print(f"  {name:20s}{str(bare):22s}{timed}")
        if bare:
            bare_fns.append(name)
        if timed:
            timed_fns.append(name)
    print(f"\n裸 wait() 的原子：{bare_fns}")
    print(f"有超时的原子：{timed_fns}")

    # ---------------- 2. 动态：四个原子是否真的挂死 ----------------
    print(f"\n动态验证（每个等 {WAIT_SECONDS} 秒）：")
    still_alive = []
    detail = []
    for name, kwargs in HANGERS:
        fn = getattr(Dialog, name)
        box = {}

        def target(fn=fn, kwargs=kwargs, box=box):
            try:
                box["ret"] = fn(**kwargs)
            except Exception as e:  # noqa: BLE001
                box["err"] = fail_detail(e)

        th = threading.Thread(target=target, daemon=True)
        t0 = time.time()
        th.start()
        th.join(timeout=WAIT_SECONDS)
        alive = th.is_alive()
        if alive:
            still_alive.append(name)
        detail.append(f"{name}: {'仍挂起' if alive else '已返回'}（{time.time() - t0:.1f}s）{box.get('err', '')}")
        print(f"  {detail[-1]}")

    # ---------------- 3. 对照：message_box 有超时 ----------------
    t0 = time.time()
    ctrl_ret, ctrl_err = "<未返回>", ""
    try:
        ctrl_ret = Dialog.message_box(message_content="A13 对照", wait_time=3)
    except Exception as e:  # noqa: BLE001
        ctrl_err = fail_detail(e)
    ctrl_elapsed = time.time() - t0
    print(f"  对照 message_box(wait_time=3): {ctrl_elapsed:.1f}s 后返回，ret={ctrl_ret!r} {ctrl_err}")
    ctrl_ok = ctrl_elapsed < WAIT_SECONDS + 2

    # ---------------- 判定 ----------------
    expected = [n for n, _ in HANGERS]
    if not still_alive:
        return report(
            "A13",
            "Dialog 四个输入类原子无超时挂死",
            "无人应答时应在超时后退出，而不是永久等待",
            f"四个原子在 {WAIT_SECONDS} 秒内都返回了：{detail}；"
            f"静态裸 wait() 的原子={bare_fns}",
            NOT_REPRODUCED,
            "本版本已加了超时保护，可关闭该条。",
        )

    return report(
        "A13",
        "input_box / select_box / select_time_box / select_file_box 用裸 done.wait()，永久挂起",
        "无人应答（openapi / 脚本 / CI 执行，或弹窗被关掉没走回调）时，"
        "这四个 Dialog 原子应按 wait_time 超时退出并给出明确结果，而不是永久占住线程",
        f"静态：dialog.py 里用裸 done.wait() 的原子={bare_fns}，"
        f"用 wait_with_timeout 的={timed_fns}；"
        f"动态：{'、'.join(still_alive)} 在 {WAIT_SECONDS} 秒后线程仍存活（{'; '.join(detail)}）；"
        f"对照 message_box(wait_time=3) {ctrl_elapsed:.1f} 秒返回 ret={ctrl_ret!r}，"
        f"证明同文件里已有超时机制，只是这四个没用上",
        REPRODUCED,
        f"复现步骤：① 起一个只含 `Dialog.input_box` 的流程；"
        f"② 用脚本方式（不是设计器点运行）执行；③ 流程停在 input_box 不动，"
        f"线程 100% CPU 占用为 0 但永不返回，openapi 侧表现为请求一直挂着。"
        f"修复建议：把这四处 `done.wait()` 统一改成 "
        f"`wait_with_timeout(lambda: done.is_set(), reset_timeout_on_activity=auto_check, "
        f"wait_time=wait_time)`（与 message_box / custom_box 一致），"
        f"并给四个原子补 wait_time 参数；超时后按 auto_check 返回默认值或抛业务异常。"
        f"另外请确认：openapi / 脚本执行时，Dialog.* 是否属于支持场景？"
        f"如果不支持，应在原子元数据里标注前置条件，别让调用方无声挂死。"
    )


main_guard(run)
