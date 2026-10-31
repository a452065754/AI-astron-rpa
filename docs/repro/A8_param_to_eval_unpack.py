# -*- coding: utf-8 -*-
"""A8 · Param._param_to_eval 返回值个数不一致 → "too many values to unpack"

`astronverse/executor/flow/params.py` 里同一个函数三条 return：

    if len(pieces) == 1:      return pieces[0], need_eval              # 2 个值
    if need_eval:             return "+".join(...), need_eval          # 2 个值
    else:                     return "".join(pieces), need_eval, need_eval   # 3 个值 ← 多了一个

而唯一调用点（同文件第 155 行）只有两个接收者：

    value, need_eval = self._param_to_eval(self.pre_param_handler(data), gv=gv)

触发条件很具体：参数值是**多段（≥2 段）且全部是静态文本**（type 为 str/other，
不含任何变量引用），此时 need_eval=False 落到第三条 return。

用户视角：把一个节点的文本参数拆成两段（比如「库存：」「100 件」）写成两个
纯文本片段，流程**编译期**就报 `ValueError: too many values to unpack (expected 2)`，
错误信息完全指不到根因。

运行：
    "%APPDATA%/astron-rpa/python_core/python.exe" docs/repro/A8_param_to_eval_unpack.py
"""
import ast
import inspect
import os
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import (  # noqa: E402
    NOT_REPRODUCED,
    REPRODUCED,
    fail_detail,
    main_guard,
    report,
)

# 多段纯静态文本（引擎里 type 只有 str / other 两种算静态）
STATIC_MULTI = [
    {"type": "str", "value": "库存："},
    {"type": "str", "value": "100 件"},
]
# 对照组：掺一个变量引用，会走 need_eval 分支（2 个返回值）
WITH_VAR = [
    {"type": "str", "value": "库存："},
    {"type": "var", "value": "stock_num"},
]


def count_returns(fn):
    """静态数一数函数里每条 return 的返回值个数。"""
    src, start = inspect.getsourcelines(fn)   # start = 函数定义所在的真实行号
    tree = ast.parse(textwrap.dedent("".join(src)))
    fn_node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
    rows = []
    for node in ast.walk(fn_node):
        if isinstance(node, ast.Return) and node.value is not None:
            n = len(node.value.elts) if isinstance(node.value, ast.Tuple) else 1
            rows.append((start + node.lineno - 1, n))   # 换算成文件里的绝对行号
    return rows


def run():
    from astronverse.executor.flow.params import Param

    returns = count_returns(Param._param_to_eval)
    print("源码里 _param_to_eval 的每条 return（行号, 返回值个数）：")
    for ln, n in returns:
        print(f"  params.py:{ln}  -> {n} 个")
    distinct = sorted({n for _, n in returns})

    p = Param(None)  # 这两个方法不依赖 svc

    # ---------- 复现：多段纯静态 ----------
    ls = p.pre_param_handler([dict(x) for x in STATIC_MULTI])
    print(f"\n输入（多段纯静态）：{STATIC_MULTI}")
    print(f"pre_param_handler 后：{ls}")
    ret = p._param_to_eval(ls)
    print(f"_param_to_eval 返回：{ret!r}  →  {type(ret).__name__}，元素 {len(ret) if isinstance(ret, tuple) else 1} 个")

    unpack_err = None
    try:
        value, need_eval = ret  # 完全照抄 params.py:155 的写法
        print(f"按调用点写法解包 → 成功：value={value!r} need_eval={need_eval!r}")
    except Exception as e:  # noqa: BLE001
        unpack_err = fail_detail(e)
        print(f"按调用点写法解包 → 异常：{unpack_err}")

    # ---------- 对照：含变量引用 ----------
    ls2 = p.pre_param_handler([dict(x) for x in WITH_VAR])
    ret2 = p._param_to_eval(ls2)
    ctrl = "未执行"
    try:
        v2, n2 = ret2
        ctrl = f"返回 {len(ret2)} 个值，解包正常（value={v2!r}, need_eval={n2!r}）"
    except Exception as e:  # noqa: BLE001
        ctrl = f"同样失败：{fail_detail(e)}"
    print(f"\n对照（掺一个变量）：返回 {ret2!r} → {ctrl}")

    if unpack_err is None:
        return report(
            "A8",
            "_param_to_eval 返回值个数不一致",
            "三条 return 的返回值个数一致（都是 2 个）",
            f"多段纯静态时返回 {len(ret) if isinstance(ret, tuple) else 1} 个值，但调用点能正常解包；"
            f"源码 return 个数集合={distinct}，对照结果：{ctrl}",
            NOT_REPRODUCED,
            "本版本已修（或调用点改成了可变解包），可关闭该条。",
        )

    return report(
        "A8",
        "_param_to_eval 有一处 return 多返回一个值，调用点解包崩溃",
        "节点参数是「两段纯文本」时，能正常拼成 '库存：100 件' 并编译通过",
        f"多段纯静态时 `_param_to_eval` 返回 {len(ret)} 个值，调用点 `value, need_eval = ...` 抛 "
        f"{unpack_err}；源码 return 个数集合={distinct}（第 "
        + "、".join(str(ln) for ln, n in returns if n == 3)
        + f" 行多返回一个）；对照：{ctrl}",
        REPRODUCED,
        "修复建议：把 params.py 里 `return \"\".join(pieces), need_eval, need_eval` 改成 "
        "`return \"\".join(pieces), need_eval`（删掉多余的第三个）。"
        "顺带把函数签名注解 `-> (Any, bool)` 改成 `-> tuple[Any, bool]`。",
    )


main_guard(run)
