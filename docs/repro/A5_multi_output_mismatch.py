# -*- coding: utf-8 -*-
"""A5 · 多输出声明 ≠ 实现（一类缺陷）

原子在 `@atomicMg.atomic(..., outputList=[...])` 里声明了 N 个输出，实现却只
`return` 一个值。引擎的代码生成器会**完全按 outputList 数量**生成解包语句：

    copied_text, copied_row, copied_column, copied_area = DataTable.copy_data(...)

于是把「一个返回值」硬拆成 N 份。两种后果：
  · 返回值长度 == N  → 不报错，但每个变量拿到的都是**碎片**（静默错位，最危险）
  · 返回值长度 != N  → `ValueError: not enough values to unpack` / `too many values to unpack`

本脚本对 `DataTable.copy_data`(4 输出) 和 `DataTable.read_data`(4 输出) 各做一次：
  (1) 从源码 AST 里读出声明的输出个数与名字
  (2) 真调一次，拿到实现的实际返回值
  (3) 模拟引擎的解包动作，展示拿到的是什么

运行：
    "%APPDATA%/astron-rpa/python_core/python.exe" docs/repro/A5_multi_output_mismatch.py
"""
import ast
import inspect
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
    tmpdir,
)

TARGETS = [
    ("DataTable.copy_data", "copy_data"),
    ("DataTable.read_data", "read_data"),
]


def declared_outputs(mod, fn_name):
    """从 @atomicMg.atomic(outputList=[atomicMg.param('x'), ...]) 里读出输出名列表。"""
    tree = ast.parse(inspect.getsource(mod))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == fn_name):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            for kw in dec.keywords:
                if kw.arg != "outputList" or not isinstance(kw.value, ast.List):
                    continue
                names = []
                for el in kw.value.elts:
                    if isinstance(el, ast.Call) and el.args and isinstance(el.args[0], ast.Constant):
                        names.append(el.args[0].value)
                return names
        return []
    return []


def build_xlsx(path, header, row):
    """造一张小表作为 read_data/copy_data 的数据源。"""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(header)
    ws.append(row)
    wb.save(path)


def simulate_unpack(ret, n):
    """模拟引擎按 n 个输出生成的解包语句。"""
    try:
        values = list(ret) if not isinstance(ret, str) else list(ret)
    except TypeError:
        return f"返回值 {type(ret).__name__} 不可迭代，解包会直接 TypeError"

    if len(values) != n:
        return f"长度 {len(values)} ≠ 声明 {n} → ValueError: {'not enough' if len(values) < n else 'too many'} values to unpack"
    return f"长度恰好 {n}，解包不报错，但每个变量只拿到碎片：{values}"


def run():
    from astronverse.datatable import datatable as dt_mod
    from astronverse.datatable.datatable import DataTable

    work = tmpdir("A5")
    src = os.path.join(work, "复现数据源.xlsx")
    build_xlsx(src, ["品名", "数量", "金额"], ["茶几", 2, 1800])

    DataTable.import_data_table_from_file(import_file_path=src, sheet_name="Sheet1")
    print(f"数据源已导入：{src}（表头 品名/数量/金额，第 2 行 茶几/2/1800）\n")

    findings = []
    for label, fn_name in TARGETS:
        declared = declared_outputs(dt_mod, fn_name)
        fn = getattr(DataTable, fn_name)
        call = {"read_type": "row", "row": 2, "col": "A",
                "start_row": 1, "start_col": "A", "end_row": 0, "end_col": ""}
        if fn_name == "copy_data":
            call = {"copy_type": "row", "row": 2, "col": "A",
                    "start_row": 1, "start_col": "A", "end_row": 0, "end_col": ""}
        # 枚举参数转成员
        enum_mod = sys.modules[fn.__module__]
        conv = {}
        for k, v in call.items():
            conv[k] = v
        for ename in ("CopyType", "ReadType"):
            et = getattr(enum_mod, ename, None)
            if et is not None:
                key = "copy_type" if ename == "CopyType" else "read_type"
                if key in conv:
                    conv[key] = et(conv[key])

        try:
            ret = fn(**conv)
            ret_desc = repr(ret)
            unpack = simulate_unpack(ret, len(declared) or 1)
        except Exception as e:  # noqa: BLE001
            ret, ret_desc, unpack = None, None, f"调用本身失败：{fail_detail(e)}"

        print(f"【{label}】")
        print(f"  字典声明输出 {len(declared)} 个：{declared}")
        print(f"  实现实际返回：{ret_desc}")
        print(f"  引擎按 {len(declared)} 个输出解包 → {unpack}\n")
        findings.append((label, declared, ret_desc, unpack))

    # ---------- 关键补充：列数恰好 = 声明数时，静默错位（不报错但结果全错） ----------
    src4 = os.path.join(work, "复现数据源_4列.xlsx")
    build_xlsx(src4, ["品名", "数量", "单价", "金额"], ["茶几", 2, 900, 1800])
    DataTable.import_data_table_from_file(import_file_path=src4, sheet_name="Sheet1")
    from astronverse.datatable.datatable import CopyType

    dec4 = declared_outputs(dt_mod, "copy_data")
    try:
        ret4 = DataTable.copy_data(
            copy_type=CopyType.ROW, row=2, col="A", start_row=1, start_col="A", end_row=0, end_col=""
        )
        sim4 = simulate_unpack(ret4, len(dec4) or 1)
        print("【DataTable.copy_data（4 列数据源，列数巧合 = 声明数 4）】")
        print(f"  字典声明输出 {len(dec4)} 个：{dec4}")
        print(f"  实现实际返回：{ret4!r}")
        print(f"  引擎按 4 个输出解包 → {sim4}")
        print(f"  → copied_cell 本应是单个单元格，实际拿到 {list(ret4)[0]!r}；copied_row 本应是整行…全错位\n")
        findings.append(("DataTable.copy_data（4 列）", dec4, repr(ret4), sim4))
    except Exception as e:  # noqa: BLE001
        print(f"4 列用例调用失败：{fail_detail(e)}\n")

    bad = [f for f in findings if f[1] and len(f[1]) > 1 and "恰好" in f[3]]
    fatal = [f for f in findings if f[1] and len(f[1]) > 1 and "ValueError" in f[3]]
    err = [f for f in findings if "调用本身失败" in f[3]]

    lines = [
        f"{lab}：声明 {len(dec)} 个输出 / 实际返回 {ret} / 解包结果 {up}"
        for lab, dec, ret, up in findings
    ]
    actual_txt = "；".join(lines)

    if not bad and not fatal:
        return report(
            "A5",
            "多输出声明 ≠ 实现",
            "声明几个输出就该返回几个值（或总长与声明数一致）",
            actual_txt,
            NOT_REPRODUCED if not err else BLOCKED,
            "本版本两个原子的返回值与声明已经匹配，可关闭该条。",
        )

    shown = (bad + fatal)[0]
    return report(
        "A5",
        f"多输出声明 ≠ 实现（{shown[0]}：声明 {len(shown[1])} 个输出，实现返回 1 个值）",
        "字典声明 N 个输出时，实现应返回 N 元组；否则引擎应按 1 个输出生成代码",
        actual_txt,
        REPRODUCED,
        "危险性在于**静默错位**：当返回值的元素个数恰好等于声明个数时不报错，"
        "变量各自拿到不相干的碎片，流程跑到后面才炸或产出错数据。"
        "已确认同类还有 `BrowserElement.element_operation`（声明 8 个输出）。"
        "修复建议二选一：① 实现改成 return 元组；② 把 outputList 收敛成 1 个（推荐，"
        "因为这 4 个输出在语义上本就是同一个值）。",
    )


main_guard(run)
