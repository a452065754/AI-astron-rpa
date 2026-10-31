# -*- coding: utf-8 -*-
"""复现包公共夹具（仅标准库，不引第三方依赖）。

设计目标：把「复现」做成**可判定的断言**，而不是靠人看日志。

每个 A*.py 脚本跑完返回三种状态之一：
    REPRODUCED  缺陷在当前环境成立（平台需要修）
    NOT_REPRODUCED  按文档步骤跑不出问题（可能已修复，或环境版本不同）
    BLOCKED     本脚本复现所需的前置条件不具备（如没装 Word）

退出码：0=REPRODUCED, 2=NOT_REPRODUCED, 3=BLOCKED
这样 `run_all.py` 或 CI 可以直接判。
"""
import os
import sys
import traceback

REPRODUCED = "REPRODUCED"
NOT_REPRODUCED = "NOT_REPRODUCED"
BLOCKED = "BLOCKED"

_EXIT = {REPRODUCED: 0, NOT_REPRODUCED: 2, BLOCKED: 3}

# Windows 控制台默认 GBK，中文断言文本会炸，这里强制 utf-8（失败就忽略）
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = os.path.join(HERE, "_tmp")


def tmpdir(*parts):
    """复现包自己的临时目录，路径不存在会自动建。"""
    p = os.path.join(TMP, *parts)
    os.makedirs(p, exist_ok=True)
    return p


def _wrap(text, width=96):
    out, line = [], ""
    for ch in text:
        line += ch
        if len(line) >= width:
            out.append(line)
            line = ""
    if line:
        out.append(line)
    return "\n".join(out)


def report(aid, title, expected, actual, verdict, note=""):
    """打印一条复现结论并返回退出码。"""
    print("")
    print("=" * 78)
    print(f"[{aid}] {title}")
    print("-" * 78)
    print("期望行为：")
    print(_wrap(expected, 92))
    print("实际行为：")
    print(_wrap(actual, 92))
    print(f"结论：{verdict}")
    if note:
        print("备注：")
        print(_wrap(note, 92))
    print("=" * 78)
    return _EXIT.get(verdict, 1)


def fail_detail(exc):
    """把异常打成「类型 + 消息 + 出错的最后一帧」一行摘要。"""
    tb = traceback.extract_tb(exc.__traceback__)
    where = ""
    if tb:
        last = tb[-1]
        where = f"（{os.path.basename(last.filename)}:{last.lineno} in {last.name}）"
    return f"{type(exc).__name__}: {exc}{where}"


def main_guard(fn):
    """统一出口：脚本里 raise 的任何非预期异常都当 BLOCKED 报，不静默。"""
    try:
        code = fn()
    except ImportError as e:
        print(f"BLOCKED：引擎模块导入失败 —— {e}")
        print("请用设计器自带解释器运行：%APPDATA%/astron-rpa/python_core/python.exe")
        code = _EXIT[BLOCKED]
    except Exception as e:
        print(f"BLOCKED：脚本自身执行异常 —— {fail_detail(e)}")
        traceback.print_exc()
        code = _EXIT[BLOCKED]
    sys.exit(code)
