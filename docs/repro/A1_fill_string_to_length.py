# -*- coding: utf-8 -*-
"""A1 · StringProcess.fill_string_to_length —— 右补齐必崩

复现步骤：同一函数、同一组入参，只改 fill_type。左补齐正常、右补齐抛 TypeError。
根因：`dataprocess/string.py` 里两个分支用了不同的长度变量——
    左补齐： (add_str * n)[0 : total_length_int - len(string_data)]
    右补齐： (string_data + add_str * n)[0:total_length]     ← 用了 str 类型的 total_length
`total_length` 在字典里声明为 Str，函数开头也只做了 `int(total_length)` 到局部变量
`total_length_int`，所以 `[0:total_length]` 一定拿到字符串切片下标。

运行：
    "%APPDATA%/astron-rpa/python_core/python.exe" docs/repro/A1_fill_string_to_length.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import (  # noqa: E402
    BLOCKED,
    NOT_REPRODUCED,
    REPRODUCED,
    fail_detail,
    main_guard,
    report,
)

STRING_DATA = "ab"
ADD_STR = "x"
TOTAL = "8"          # 字典标 Str，界面上填的就是这个
EXPECT = "abxxxxxx"  # 补到 8 位


def _call(fill_type_name):
    from astronverse.dataprocess import FillStringType
    from astronverse.dataprocess.string import StringProcess

    ft = getattr(FillStringType, fill_type_name)
    return StringProcess.fill_string_to_length(
        string_data=STRING_DATA, add_str=ADD_STR, total_length=TOTAL, fill_type=ft
    )


def run():
    # ---- 对照组 1：左补齐，应当成功 ----
    left_err, left_val = None, None
    try:
        left_val = _call("LEFT")
    except Exception as e:  # noqa: BLE001
        left_err = fail_detail(e)

    # ---- 实验组：右补齐 ----
    right_err, right_val = None, None
    try:
        right_val = _call("RIGHT")
    except Exception as e:  # noqa: BLE001
        right_err = fail_detail(e)

    print(f"入参：string_data={STRING_DATA!r} add_str={ADD_STR!r} total_length={TOTAL!r}(Str)")
    print(f"  fill_type=LEFT  -> {'异常：' + left_err if left_err else repr(left_val)}")
    print(f"  fill_type=RIGHT -> {'异常：' + right_err if right_err else repr(right_val)}")

    if left_err:
        # 连左补齐都不行，说明环境/版本和本文不符，不硬判
        return report(
            "A1",
            "右补齐 ValueError / TypeError",
            f"LEFT 与 RIGHT 都返回补到 8 位的字符串（{EXPECT!r}）",
            f"LEFT 也失败了：{left_err}",
            BLOCKED,
            "左补齐本应正常。当前版本连它都失败，说明缺陷面更大或版本不同，请一并核查。",
        )

    if right_err is None:
        return report(
            "A1",
            "右补齐 ValueError / TypeError",
            f"RIGHT 返回 {EXPECT!r}",
            f"RIGHT 正常返回 {right_val!r}",
            NOT_REPRODUCED,
            "本版本右补齐已修好，可关闭该条。",
        )

    # 右补齐崩了 → 缺陷成立。进一步证明「换成 int 就正常」，锁定根因在类型
    hint = ""
    try:
        from astronverse.dataprocess import FillStringType
        from astronverse.dataprocess.string import StringProcess

        v = StringProcess.fill_string_to_length(
            string_data=STRING_DATA, add_str=ADD_STR, total_length=8, fill_type=FillStringType.RIGHT
        )
        hint = f"对照实验：total_length 传 int(8) 时右补齐正常返回 {v!r} → 根因确认为切片下标类型，不是算法错。"
    except Exception as e:  # noqa: BLE001
        hint = f"对照实验（total_length 传 int）也失败：{fail_detail(e)}"

    return report(
        "A1",
        "StringProcess.fill_string_to_length 右补齐必崩",
        f"fill_type=RIGHT、total_length='8'(Str) 时返回补到 8 位的 {EXPECT!r}；"
        "字典已把 total_length 声明为 Str，界面/流程里正常填法就是字符串",
        f"抛出 {right_err}",
        REPRODUCED,
        hint
        + " 修复建议：把右补齐也改成 [0:total_length_int]，与左补齐分支保持一致"
        "（dataprocess/string.py 第 237 行）。",
    )


main_guard(run)
