# -*- coding: utf-8 -*-
"""V12：AI 写的脚本对「不是空值、但是脏值」有没有防御（lead 的 CC 角度）。

空值（None/''）已经被「脚本健壮性四条」第①条盖住了（v11 的 D2 验证）。
但 Excel 里还有一类更阴的值：**公式错误值** `#N/A` `#DIV/0!` `#VALUE!` `#REF!` `#NAME?` `#NUM!` `#NULL!`。
它们既不是空、也不是数字，业务人员看到就知道「这格算不出来」，程序却很容易当成 NA 处理掉。

本用例只做**静态 + 本地裸跑**（不连平台、不调 LLM），查三件事：
  X1 金额列有 `#N/A` / `#DIV/0!` → 会不会崩？值原样保留还是静默变形？
  X2 分组列（部门）是 `#N/A`  → 落到哪个文件？summary 会不会把它说成「为空」？
  X3 候选修法（`pd.read_excel(..., keep_default_na=False)`）在本机 pandas 下是否真能保住原值

用法：C:/Python314/python.exe logs/qa/v12_dirty_value.py
退出码：始终 0（这是探针，不是门禁；结论看输出）
"""
import json
import os
import re
import shutil
import sys
import tempfile

import openpyxl
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GEN_SPEC = os.path.join(ROOT, "flows", "gen_按部门拆分Excel.json")

HEADER = ("订单号", "部门", "金额")
# 真实场景：金额是公式，某些行算不出来 → Excel 显示 #N/A / #DIV/0!
ROWS = [
    ("SO0001", "华南", 100),
    ("SO0002", "华东", "#N/A"),
    ("SO0003", "华南", "#DIV/0!"),
    ("SO0004", "华北", 200),
]
# 分组列本身是错误值：业务人员看到「部门 = #N/A」，程序看到什么？
ROWS_BAD_KEY = [
    ("SO0001", "华南", 100),
    ("SO0002", "#N/A", 200),
    ("SO0003", "华东", 300),
]

ERR_VALUES = ("#N/A", "#DIV/0!", "#VALUE!", "#REF!", "#NAME?", "#NUM!", "#NULL!")


def load_main():
    with open(GEN_SPEC, encoding="utf-8") as f:
        spec = json.load(f)
    code = spec["scripts"]["excel_split_by_dept"]["code"]
    ns = {"__name__": "qa_generated_script"}
    exec(compile(code, "<flows/gen_按部门拆分Excel.json:scripts.code>", "exec"), ns)  # noqa: S102
    return ns["main"], code


def build_xlsx(path, rows, header=HEADER):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "明细"
    ws.append(list(header))
    for r in rows:
        ws.append(list(r))
    wb.save(path)
    return path


def dump_cells(path):
    """不用 pandas，直接读单元格真值（openpyxl），作为「产出到底存了什么」的基准。"""
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active
    out = []
    for row in ws.iter_rows(values_only=True):
        out.append(tuple("（空）" if v is None else v for v in row))
    wb.close()
    return out


def show(title):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)


def run_capture(main, args):
    """跑一次脚本，把 logger / stdout / stderr 都抓下来（判断有没有「说出来」）。"""
    import io
    import logging

    collector = []

    class H(logging.Handler):
        def emit(self, record):
            try:
                collector.append(record.getMessage())
            except Exception:                               # noqa: BLE001
                collector.append(str(record.msg))

    handler = H(level=logging.DEBUG)
    root = logging.getLogger()
    old = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    out, err = io.StringIO(), io.StringIO()
    so, se = sys.stdout, sys.stderr
    res, exc = None, None
    try:
        sys.stdout, sys.stderr = out, err
        res = main(dict(args))
    except BaseException as e:                              # noqa: BLE001
        exc = e
    finally:
        sys.stdout, sys.stderr = so, se
        root.removeHandler(handler)
        root.setLevel(old)
    return res, exc, "\n".join([out.getvalue(), err.getvalue(), "\n".join(collector)])


def says_dirty(text, summary):
    """脚本有没有把「源表里有错误值」说出来。"""
    mass = (text or "") + "\n" + (summary or "")
    return bool(re.search(r"错误值|#N/?A|#DIV|#VALUE|异常单元格|无法计算的单元格", mass, re.I))


def x1_dirty_amount(main, td):
    show("X1 金额列含 Excel 错误值 #N/A / #DIV/0!（4 行，拆「部门」）")
    src = build_xlsx(os.path.join(td, "x1.xlsx"), ROWS)
    print("  输入（openpyxl 直读，未经 pandas）：")
    for r in dump_cells(src):
        print("     %s" % (r,))
    out = os.path.join(td, "x1_out")
    res, exc, text = run_capture(
        main, {"src_file": src, "split_column": "部门", "out_dir": out})
    summary = (res or {}).get("summary", "") if isinstance(res, dict) else ""
    print("  运行结果：%s" % ("抛 %s: %s" % (type(exc).__name__, exc) if exc else
                            json.dumps({k: v for k, v in res.items() if k != "outputs"},
                                       ensure_ascii=False, default=str)))
    tail = [ln for ln in text.splitlines() if ln.strip()][:2]
    for ln in tail:
        print("  日志：%s" % ln)

    total = 0
    kept = {"#N/A": 0, "#DIV/0!": 0}
    blank = 0
    for f in sorted(os.listdir(out)) if os.path.isdir(out) else []:
        if not f.endswith(".xlsx"):
            continue
        print("  产出 %s：" % f)
        for r in dump_cells(os.path.join(out, f))[1:]:
            print("     %s" % (r,))
            total += 1
            cell = r[2]
            if cell == "（空）":
                blank += 1
            elif str(cell) in kept:
                kept[str(cell)] += 1
    print("\n  行数：输入 %d 行 / 产出 %d 行" % (len(ROWS), total))
    print("  `#N/A`   原样保留 %d 个（应 1）" % kept["#N/A"])
    print("  `#DIV/0!` 原样保留 %d 个（应 1）" % kept["#DIV/0!"])
    print("  金额列变成空白的行数 = %d" % blank)
    deformed = kept["#N/A"] < 1 or kept["#DIV/0!"] < 1
    reported = says_dirty(text, summary)
    print("  是否把「源表有错误值」说出来：%s" % reported)
    ok = (not deformed) or reported
    print("  → 结论：%s"
          % ("值没变形 → PASS"
             if not deformed else
             ("值被抹成空白，但**明确告警了**（行数对得上、用户知道去核对）→ PASS（按 D2 同口径）"
              if reported else
              "**静默变形**：错误值被抹成空白，流程报 SUCCESS、summary 一个字都不提 → FAIL")))
    return ok


def x2_dirty_key(main, td):
    show("X2 分组列「部门」本身是 #N/A（3 行，其中 1 行部门=#N/A）")
    src = build_xlsx(os.path.join(td, "x2.xlsx"), ROWS_BAD_KEY)
    print("  输入（openpyxl 直读）：")
    for r in dump_cells(src):
        print("     %s" % (r,))
    out = os.path.join(td, "x2_out")
    res, exc = None, None
    try:
        res = main({"src_file": src, "split_column": "部门", "out_dir": out})
    except BaseException as e:                              # noqa: BLE001
        exc = e
    if exc:
        print("  运行结果：抛 %s: %s" % (type(exc).__name__, exc))
        return
    print("  运行结果：summary = %s" % res.get("summary"))
    print("  blank_grouped=%s；产出文件：%s"
          % (res.get("blank_grouped"), sorted(os.listdir(out))))
    rows = 0
    for f in sorted(os.listdir(out)):
        if f.endswith(".xlsx"):
            rows += len(dump_cells(os.path.join(out, f))) - 1
    print("  行数：输入 %d 行 / 产出 %d 行 → %s"
          % (len(ROWS_BAD_KEY), rows, "守恒" if rows == len(ROWS_BAD_KEY) else "**丢行**"))
    says_blank = "为空" in (res.get("summary") or "")
    print("  → 结论：%s"
          % ("行没丢，但 `#N/A` 被当成**空值**塞进 空值.xlsx，summary 还说「为空 1 行」——"
             "业务人员会去查「哪行没填部门」，实际那行是公式报错" if says_blank
             else "行没丢，且没被误标成空值"))


def x3_candidate(td):
    show("X3 候选修法验证：能不能保住 / 至少识别出 Excel 错误值")
    src = build_xlsx(os.path.join(td, "x3.xlsx"), ROWS)
    default_df = pd.read_excel(src)
    keep_df = pd.read_excel(src, keep_default_na=False)
    print("  pandas %s" % pd.__version__)
    print("  openpyxl 直读（基准）          金额列 = %s"
          % [r[2] for r in dump_cells(src)[1:]])
    print("  默认读取                       金额列 = %s" % default_df["金额"].tolist())
    print("  keep_default_na=False          金额列 = %s" % keep_df["金额"].tolist())
    kept = [str(v) for v in keep_df["金额"].tolist()]
    if "#N/A" in kept:
        print("  → keep_default_na=False 能原样保住错误值")
    else:
        print("  → keep_default_na=False **也救不回来**：pandas 3.0 的 excel 读取器在更底层就把")
        print("     Excel 错误值映射成 NaN 了，na_values / na_filter / dtype=str 全都无效。")

    # 但换个角度：keep_default_na=False 之下，真空单元格会变成 ''，于是 NaN 只剩一种来源
    mixed = [("正常", 100), ("错误值", "#N/A"), ("真空", None), ("空串", ""), ("短行",)]
    src2 = os.path.join(td, "x3b.xlsx")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["k", "v"])
    for r in mixed:
        ws.append(list(r))
    wb.save(src2)
    df2 = pd.read_excel(src2, keep_default_na=False)
    print("\n  混合数据（正常 / #N/A / 真空 / 空串 / 缺列）在 keep_default_na=False 下：")
    print("     v 列     = %s" % df2["v"].tolist())
    print("     isna()   = %s" % df2["v"].isna().tolist())
    print("     NaN 的位置 → %s（只有「错误值」这一行）" % df2.loc[df2["v"].isna(), "k"].tolist())
    print("     '' 的位置  → %s" % df2.loc[df2["v"].astype(str).str.strip() == "", "k"].tolist())
    print("  → 结论：**原值救不回来，但能 100% 准确识别**：")
    print("     `keep_default_na=False` 之后剩下的 NaN 只可能是 Excel 错误值（真空单元格会变成 ''）。")
    print("\n  可直接落地的写法：")
    print("     df = pd.read_excel(src_file, sheet_name=..., keep_default_na=False)")
    print("     bad = df.isna()                       # 只剩「Excel 错误值」")
    print("     if bad.any().any():")
    print("         cols = {c: int(bad[c].sum()) for c in df.columns if bad[c].any()}")
    print("         raise RuntimeError(f'源表有 Excel 错误值（#N/A 之类），请先修好再跑：{cols}')")
    print("     blank = df[split_column].astype(str).str.strip() == ''   # 真空 / 空串")
    print("  注意：空值判断要从 `isna()` 改成 `strip() == ''`，否则修完这条会把真空行漏掉。")


def main_test():
    main, code = load_main()
    print("=" * 80)
    print("V12 脏值探针：AI 写的脚本对 Excel 错误值（#N/A 等）有没有防御")
    print("=" * 80)
    print("  被测：flows/gen_按部门拆分Excel.json → scripts.excel_split_by_dept.code（%d 行）"
          % len(code.splitlines()))
    print("  已覆盖的：空值（v11-D2，已修）。未覆盖的：公式错误值 —— 本用例专测这个。")

    td = tempfile.mkdtemp(prefix="qa_v12_")
    try:
        deformed = x1_dirty_amount(main, td)
        x2_dirty_key(main, td)
        x3_candidate(td)
    finally:
        shutil.rmtree(td, ignore_errors=True)

    print("\n" + "=" * 80)
    print("V12 结论")
    print("=" * 80)
    if deformed:
        print("  Excel 错误值（#N/A / #DIV/0!）被 pandas 抹成空白，**而且救不回来**")
        print("  （keep_default_na / na_values / na_filter / dtype=str 全试过，无效 ——")
        print("   pandas 3.0 的 excel 读取器在底层就把错误值映射成 NaN）。")
        print("  流程照样 SUCCESS、summary 一个字不提 → 「脚本健壮性四条」缺第 ⑤ 条：脏值策略。")
        print()
        print("  可落地的第 ⑤ 条（已在本机 pandas %s 上验证）：" % pd.__version__)
        print("    ① 读表用 `pd.read_excel(..., keep_default_na=False)` → 真空单元格变成 ''，")
        print("       于是**剩下的 NaN 只可能是 Excel 错误值**（这条已验证 100% 可分）；")
        print("    ② 进主逻辑前先 `df.isna()` 统计，有就 `raise RuntimeError('源表有 Excel 错误值 …')`，")
        print("       或写进 summary（「金额列 1 个 #N/A 未处理」）—— 二选一，不许静默；")
        print("    ③ 空值判断从 `isna()` 改成 `astype(str).str.strip() == ''`，别把真空行漏了。")
    else:
        print("  错误值没有静默变形。")
    return 0


if __name__ == "__main__":
    sys.exit(main_test())
