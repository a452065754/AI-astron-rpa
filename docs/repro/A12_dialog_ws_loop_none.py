# -*- coding: utf-8 -*-
"""A12 · Dialog.* 无头执行必崩：cfg["WS"] 有值，但 Ws.loop 还是 None

现象：任何 `Dialog.*` 原子（message_box / input_box / select_box /
select_time_box / select_file_box / custom_box）在**没有设计器接管**的执行环境里
（脚本方式跑流程、openapi 跑流程、CI 跑流程）一执行就崩：

    AttributeError: 'NoneType' object has no attribute 'call_soon_threadsafe'

真因是三个文件的初始化顺序对不上：

    executor/debug/apis/ws.py:71    Ws.__init__ 里 **无条件** 写
                                    atomicMg.cfg()["WS"] = self
    executor/debug/apis/ws.py:112   send_reply 里
                                    asyncio.run_coroutine_threadsafe(coro, Ws.loop)
    executor/debug/apis/ws.py:226   Ws.loop = loop
                                    ← 只在「起 WebSocket 服务」那条路上才会执行
    dialog/dialog.py:170            ws = atomicMg.cfg().get("WS"); if ws: ws.send_reply(...)

于是：cfg["WS"] 判真 → 进入 send_reply → Ws.loop 仍是类默认值 None
     → run_coroutine_threadsafe(coro, None) → None.call_soon_threadsafe → AttributeError

而且 dialog.py 里 auto_check 的兜底救不了：崩溃发生在兜底**之前**。

本脚本做三件事：
  1. 静态：用 AST 证明 cfg["WS"] 是无条件赋值、Ws.loop 的赋值在另一个函数里、
     且 send_reply 用的就是 Ws.loop
  2. 动态：构造 Ws 实例后调引擎真实的 send_reply → 复现 AttributeError
  3. 动态 + 对照：把 Ws.loop 赋成真实事件循环后再调 → 不再报这个错，
     证明唯一的病根就是 loop 没起来（而不是别的地方坏了）

运行：
    "%APPDATA%/astron-rpa/python_core/python.exe" docs/repro/A12_dialog_ws_loop_none.py
"""
import ast
import asyncio
import inspect
import os
import sys
import warnings

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import (  # noqa: E402
    NOT_REPRODUCED,
    REPRODUCED,
    fail_detail,
    main_guard,
    report,
)

# 复现时会故意让协程没被 await，屏蔽那条 RuntimeWarning 免得干扰阅读
warnings.simplefilter("ignore", RuntimeWarning)

MSG = {"data": {"name": "userform", "option": {"key": "Dialog.message_box"}}}


def _seg(node):
    try:
        return ast.unparse(node)
    except Exception:  # noqa: BLE001
        return "?"


def _funcs(cls_node):
    return {
        n.name: n
        for n in cls_node.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def static_probe():
    """AST 层面把「三处对不上」的位置全部找出来。"""
    from astronverse.executor.debug.apis.ws import Ws

    src_path = inspect.getsourcefile(Ws)
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Ws")
    funcs = _funcs(cls)

    out = {"file": src_path, "loop_default": None, "cfg_ws_in_init": [],
           "loop_assigns": [], "rct_args": []}

    # 类属性 loop = None
    for node in cls.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "loop" for t in node.targets
        ):
            out["loop_default"] = (node.lineno, _seg(node.value))

    for fname, fn in funcs.items():
        for node in ast.walk(fn):
            # atomicMg.cfg()["WS"] = self
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if (
                        isinstance(tgt, ast.Subscript)
                        and isinstance(tgt.value, ast.Call)
                        and _seg(tgt.value).endswith("cfg()")
                        and isinstance(tgt.slice, ast.Constant)
                        and tgt.slice.value == "WS"
                    ):
                        out["cfg_ws_in_init"].append((fname, node.lineno))
                    # Ws.loop = xxx
                    if (
                        isinstance(tgt, ast.Attribute)
                        and tgt.attr == "loop"
                        and isinstance(tgt.value, ast.Name)
                        and tgt.value.id == "Ws"
                    ):
                        out["loop_assigns"].append((fname, node.lineno, _seg(node.value)))
            # asyncio.run_coroutine_threadsafe(coro, Ws.loop)
            if isinstance(node, ast.Call) and "run_coroutine_threadsafe" in _seg(node.func):
                out["rct_args"].append((fname, node.lineno, [_seg(a) for a in node.args]))

    return out


def run():
    from astronverse.actionlib.atomic import atomicMg
    from astronverse.dialog.dialog import Dialog
    from astronverse.executor.debug.apis.ws import Ws

    # ---------------- 1. 静态 ----------------
    st = static_probe()
    print("静态分析：", st["file"])
    if st["loop_default"]:
        print(f"  类属性默认值  line {st['loop_default'][0]}: loop = {st['loop_default'][1]}")
    for fname, ln in st["cfg_ws_in_init"]:
        print(f"  cfg['WS'] 赋值 line {ln}（在 {fname} 里）")
    for fname, ln, val in st["loop_assigns"]:
        print(f"  Ws.loop 赋值 line {ln}（在 {fname} 里）= {val}")
    for fname, ln, args in st["rct_args"]:
        print(f"  run_coroutine_threadsafe line {ln}（在 {fname} 里），实参 = {args}")

    static_bad = (
        any(f == "__init__" for f, _ in st["cfg_ws_in_init"])
        and not any(f == "__init__" for f, _, _ in st["loop_assigns"])
        and any(args[-1] == "Ws.loop" for _, _, args in st["rct_args"])
    )
    print(f"\n静态判定：cfg['WS'] 无条件赋值={static_bad}")

    # ---------------- 2. 动态：真实 send_reply ----------------
    w = Ws(None)
    cfg_ws = atomicMg.cfg().get("WS", None)
    print(f"\n构造 Ws 实例后：cfg['WS'] = {cfg_ws!r}，if ws 判真 = {bool(cfg_ws)}")

    err = None
    try:
        w.send_reply(dict(MSG), 2, lambda *a, **k: None)
        print("send_reply → 正常返回（未复现）")
    except Exception as e:  # noqa: BLE001
        err = fail_detail(e)
        print(f"send_reply → 异常：{err}")

    # ---------------- 2b. 动态：真实 Dialog 原子（用户视角） ----------------
    dialog_err = None
    try:
        Dialog.message_box(message_content="A12 复现", wait_time=2)
        print("Dialog.message_box → 正常返回（未复现）")
    except Exception as e:  # noqa: BLE001
        dialog_err = fail_detail(e)
        print(f"Dialog.message_box → 异常：{dialog_err}")

    # ---------------- 3. 对照：loop 起来之后 ----------------
    ctrl_err = "（没有异常）"
    loop = asyncio.new_event_loop()
    try:
        Ws.loop = loop
        w.send_reply(dict(MSG), 2, lambda *a, **k: None)
    except Exception as e:  # noqa: BLE001
        ctrl_err = fail_detail(e)
    finally:
        Ws.loop = None
        try:
            loop.close()
        except Exception:  # noqa: BLE001
            pass
    print(f"对照（Ws.loop = 真实事件循环）：{ctrl_err}")

    # ---------------- 判定 ----------------
    target = "'NoneType' object has no attribute 'call_soon_threadsafe'"
    hit_err = err and target in err
    hit_dialog = dialog_err and target in dialog_err
    ctrl_clean = target not in (ctrl_err or "")

    if not (hit_err and hit_dialog):
        return report(
            "A12",
            "Dialog.* 无头执行必崩（Ws.loop 未初始化）",
            "没有设计器接管时，Dialog.* 原子应至少能超时退出或给出明确提示，不能抛 AttributeError",
            f"send_reply 异常={err}；Dialog.message_box 异常={dialog_err}；"
            f"静态判定={static_bad}",
            NOT_REPRODUCED,
            "本版本没复现出该 AttributeError（可能已加保护），可关闭该条。",
        )

    return report(
        "A12",
        "cfg['WS'] 无条件赋值 + Ws.loop 只在起 WS 服务时才赋值 → Dialog.* 必崩",
        "无头执行（脚本/openapi/CI）时，Dialog.* 原子应能超时退出或给出明确提示；"
        "至少不该抛 'NoneType' has no attribute 'call_soon_threadsafe'",
        f"构造 Ws 实例即让 cfg['WS'] 判真；随后引擎真实 send_reply 抛 {err}；"
        f"调用引擎真实原子 Dialog.message_box 同样抛 {dialog_err}；"
        f"静态：cfg['WS'] 赋值在 {st['cfg_ws_in_init']}，"
        f"Ws.loop 赋值在 {st['loop_assigns']}，"
        f"send_reply 用的是 Ws.loop；"
        f"对照组（把 Ws.loop 赋成真实事件循环）→ {ctrl_err}，"
        f"{'不再出现该 AttributeError，说明病根确为 loop 未初始化' if ctrl_clean else '仍报同样错误'}",
        REPRODUCED,
        "复现步骤：① 用设计器自带 python_core 起一个进程；"
        "② 跑任意含 Dialog.* 原子的流程（脚本方式或 openapi 方式，不要在设计器里点运行）；"
        "③ 观察报错 AttributeError: 'NoneType' object has no attribute 'call_soon_threadsafe'。"
        "修复建议：① send_reply 里先判 `if Ws.loop is None:`，"
        "没有 loop 时直接抛业务异常「当前环境未连接设计器，无法显示弹窗」而不是 AttributeError；"
        "② 或者把 Ws.loop 的初始化从「起 WS 服务」里挪到 Ws.__init__（起一个后台 loop），"
        "保证 cfg['WS'] 有值时 loop 一定可用；"
        "③ dialog.py 的 auto_check 兜底应放在 ws.send_reply 之前包一层 try。"
        "另外请说明：非设计器环境调用 Dialog.* 到底是否属于支持场景？"
        "如果不支持，应在原子元数据里标注，而不是运行时才崩。",
    )


main_guard(run)
