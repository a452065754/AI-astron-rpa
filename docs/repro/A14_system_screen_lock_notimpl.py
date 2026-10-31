# -*- coding: utf-8 -*-
"""A14 · System.screen_lock / screen_unlock 在设计器里摆着，实现却是 raise NotImplementedError()

现象：原子字典（设计器左侧「原子能力」面板）里能查到
`System.screen_lock` 和 `System.screen_unlock`，参数面板也正常渲染，
拖进流程、填好参数、点运行 —— 立刻抛：

    NotImplementedError

真因：`astronverse/system/system.py` 里这两个原子的实现体就是一行占位：

    system.py:152   def screen_lock():   raise NotImplementedError()
    system.py:192   def screen_unlock(...):  raise NotImplementedError()

也就是说：平台把「未实现」的能力原样暴露给了用户，没有在原子元数据里标注
「暂不支持 / 需要 Windows 凭据 / 需要管理员权限」。

本脚本做两件事：
  1. 动态：注册表里查得到这两个原子（设计器里确实摆得出来），且真调一次必然抛
  2. 静态：扫一遍整个 astronverse 引擎源码，把**所有**「注册了但实现体只有
     raise NotImplementedError」的原子列出来 —— 一次给平台报全，避免逐个试

运行：
    "%APPDATA%/astron-rpa/python_core/python.exe" docs/repro/A14_system_screen_lock_notimpl.py
"""
import ast
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import (  # noqa: E402
    NOT_REPRODUCED,
    REPRODUCED,
    fail_detail,
    main_guard,
    report,
)

TARGETS = ["System.screen_lock", "System.screen_unlock"]


def _engine_roots():
    """astronverse 是命名空间包（没有 __init__.py），__file__ 为 None，改用 __path__。"""
    import astronverse

    roots = []
    for p in list(getattr(astronverse, "__path__", []) or []):
        if os.path.isdir(p):
            roots.append(p)
    if not roots:
        roots = [os.path.dirname(os.path.dirname(os.path.abspath(inspect.getfile(sys.modules["astronverse.system.system"]))))]
    return roots


def _scan_file(path, root):
    """扫单个文件：找出「注册为原子、但函数体只有 raise NotImplementedError」的原子。"""
    hits = []
    try:
        with open(path, encoding="utf-8") as f:
            src = f.read()
        tree = ast.parse(src)
    except Exception:  # noqa: BLE001
        return hits
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # 找 atomicMg.atomic(...) 装饰器
        group, explicit_key = None, None
        for dec in node.decorator_list:
            try:
                txt = ast.unparse(dec)
            except Exception:  # noqa: BLE001
                continue
            if ".atomic(" not in txt and not txt.endswith(".atomic"):
                continue
            for kw in dec.keywords or []:
                if kw.arg == "key":
                    try:
                        explicit_key = ast.literal_eval(kw.value)
                    except Exception:  # noqa: BLE001
                        explicit_key = None
            if dec.args:
                try:
                    group = ast.literal_eval(dec.args[0])
                except Exception:  # noqa: BLE001
                    group = None
        if group is None and explicit_key is None:
            continue
        # 函数体是不是只有 raise NotImplementedError(...)
        body = [s for s in node.body if not isinstance(s, ast.Expr)]
        if len(body) != 1 or not isinstance(body[0], ast.Raise):
            continue
        exc = body[0].exc
        name = ""
        if isinstance(exc, ast.Call):
            name = getattr(exc.func, "id", "") or getattr(exc.func, "attr", "")
        elif exc is not None:
            name = getattr(exc, "id", "")
        if name != "NotImplementedError":
            continue
        key = explicit_key or f"{group}.{node.name}"
        hits.append((key, os.path.relpath(path, root), node.lineno))
    return hits


def scan_unimplemented_atoms():
    """扫整个引擎源码，列出所有空实现占位原子。"""
    hits = []
    for root in _engine_roots():
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                if fn.endswith(".py"):
                    hits.extend(_scan_file(os.path.join(dirpath, fn), root))
    return sorted(set(hits))


def run():
    from astronverse.actionlib.atomic import atomicMg
    from astronverse.system.system import System

    # ---------------- 1. 动态 ----------------
    print("原子注册表（atomicMg.atomic_dict）里的 System.screen* ：")
    registered = sorted(k for k in atomicMg.atomic_dict if k.startswith("System.screen"))
    for k in registered:
        print(f"  {k}")
    for k in TARGETS:
        print(f"  {k} 是否已注册 = {k in atomicMg.atomic_dict}")

    print("\n真调一次：")
    errors = {}
    for name in ("screen_lock", "screen_unlock"):
        fn = getattr(System, name, None)
        if fn is None:
            errors[name] = "函数不存在"
            print(f"  {name}: 函数不存在")
            continue
        try:
            fn()
            errors[name] = None
            print(f"  {name}: 正常返回（未复现）")
        except Exception as e:  # noqa: BLE001
            errors[name] = fail_detail(e)
            print(f"  {name}: 抛 {errors[name]}")

    # ---------------- 2. 静态全量扫描 ----------------
    hits = scan_unimplemented_atoms()
    print(f"\n全引擎扫描：{len(hits)} 个原子「注册了但实现体是 raise NotImplementedError」")
    for key, path, ln in hits:
        print(f"  {key:38s} {path}:{ln}")

    # ---------------- 判定 ----------------
    bad = [n for n, e in errors.items() if e and "NotImplementedError" in e]
    missing_reg = [k for k in TARGETS if k not in atomicMg.atomic_dict]

    if not bad:
        return report(
            "A14",
            "System.screen_lock / screen_unlock 是空实现",
            "这两个原子要么真实现，要么在原子元数据里标注「暂不支持」",
            f"调用结果：{errors}；注册情况：{registered}",
            NOT_REPRODUCED,
            "本版本已实现（或已隐藏该原子），可关闭该条。",
        )

    return report(
        "A14",
        "screen_lock / screen_unlock 在设计器里可拖可用，实现却是 raise NotImplementedError()",
        "原子字典里摆出来的能力应当真能执行；未实现的能力应该在元数据里明确标注"
        "「暂不支持 / 需要管理员权限」，而不是拖进流程才炸",
        f"动态：System.screen_lock 抛 {errors.get('screen_lock')}，"
        f"System.screen_unlock 抛 {errors.get('screen_unlock')}；"
        f"注册表里 {'能查到' if not missing_reg else '查不到'} {registered}；"
        f"静态全量扫描：引擎里共 {len(hits)} 个原子是空实现占位 —— "
        + "、".join(f"{k}({p}:{l})" for k, p, l in hits),
        REPRODUCED,
        "复现步骤：① 设计器 → 原子能力 → 搜「锁屏」/「screen_lock」；"
        "② 拖 `System.screen_lock` 进流程并运行；③ 抛 NotImplementedError。"
        "修复建议（三选一，优先级从高到低）："
        "① 短期：把这两个原子从原子字典里摘掉，或在元数据加 `unsupported` 标记，"
        "让设计器直接灰掉并提示「暂不支持」；"
        "② 中期：基于 ctypes 调用 user32.dll 的 LockWorkStation() 实现锁屏，"
        "解锁需要校验域账号口令（PwdType.PASSWORD / RSA 两套参数已经在签名里了）；"
        "③ 长期：给原子元数据补「支持平台 / 前置权限 / 实现状态」三个字段，"
        "让上层能统一过滤。"
        + (f"顺带请平台确认其余 {len(hits) - 2} 个同类占位原子的处理方式。"
           if len(hits) > 2 else "全引擎扫描下来同类占位原子就这两个。"),
    )


main_guard(run)
