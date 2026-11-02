# -*- coding: utf-8 -*-
"""V11：产品承诺「业务人员能看懂、能做微调」的**静态 + 本地运行**证据。

不连平台、不用 LLM：把 `flows/gen_按部门拆分Excel.json` 里 AI 写的 `scripts.code`
原样取出来，在**本机**用 pandas 跑，验证「业务人员只改流程参数」能到什么程度：

  T1 改 `split_column`（部门 → 区域）→ 产出文件随之改变      （改参数真的生效）
  T2 改错列名（不存在的列）→ 抛**中文** RuntimeError         （改错有可读报错）
  T3 改 `out_dir` → 产出落到新目录                           （改目录生效）
  T4 只读第一个 sheet（无 `sheet_name` 参数）→ 想拆第 2 个表必须改代码（**可微调边界**）
  T5 同参数重跑 → 静默覆盖，无「是否覆盖」参数（**可微调边界**）
  T6 空值分组 → 产出 `nan.xlsx`（可读性小瑕疵）

用法：C:/Python314/python.exe logs/qa/v11_tunability.py
"""
import json
import os
import sys
import tempfile

import openpyxl

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GEN_SPEC = os.path.join(ROOT, "flows", "gen_按部门拆分Excel.json")

ROWS_SHEET1 = [
    ("订单号", "部门", "区域", "金额"),
    ("SO0001", "华南", "南区", 100),
    ("SO0002", "华东", "东区", 200),
    ("SO0003", "华南", "南区", 300),
    ("SO0004", "华北", "北区", 400),
]
ROWS_SHEET2 = [
    ("订单号", "部门", "金额"),
    ("SO0005", "西北", 500),
    ("SO0006", "西北", 600),
]


def build_xlsx(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "明细"
    for r in ROWS_SHEET1:
        ws.append(r)
    ws2 = wb.create_sheet("第二张表")
    for r in ROWS_SHEET2:
        ws2.append(r)
    wb.save(path)
    return path


def load_main():
    spec = json.load(open(GEN_SPEC, encoding="utf-8"))
    code = spec["scripts"]["excel_split_by_dept"]["code"]
    ns = {"__name__": "qa_generated_script"}
    exec(compile(code, "<flows/gen_按部门拆分Excel.json:scripts.code>", "exec"), ns)  # noqa: S102
    return ns["main"], code


def out_names(d):
    return sorted(f for f in os.listdir(d) if f.endswith(".xlsx"))


def run_case(title, main, src, split_column, out_dir):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)
    print("  args = {src_file: %r, split_column: %r, out_dir: %r}"
          % (os.path.basename(src), split_column, os.path.relpath(out_dir, tempfile.gettempdir())))
    try:
        r = main({"src_file": src, "split_column": split_column, "out_dir": out_dir})
    except Exception as e:                                     # noqa: BLE001
        print("  抛出：%s: %s" % (type(e).__name__, e))
        return None, e
    print("  返回：%s" % json.dumps(
        {k: (v if k != "outputs" else [os.path.basename(x) for x in v])
         for k, v in r.items()}, ensure_ascii=False))
    print("  产出文件：%s" % (out_names(out_dir) or "（无）"))
    return r, None


def main_test():
    results = {}
    main, code = load_main()
    print("=" * 78)
    print("V11 被测脚本：flows/gen_按部门拆分Excel.json → scripts.excel_split_by_dept.code")
    print("=" * 78)
    print("  代码 %d 行；入口 def main(args) 存在：%s"
          % (len(code.splitlines()), "def main(args):" in code))

    td = tempfile.mkdtemp(prefix="qa_tunable_")
    src = build_xlsx(os.path.join(td, "销售明细.xlsx"))
    print("  造了输入：%s（sheet1=明细 %d 行 / sheet2=第二张表 %d 行）"
          % (os.path.basename(src), len(ROWS_SHEET1) - 1, len(ROWS_SHEET2) - 1))

    # T1 改 split_column：部门 → 区域
    d1 = os.path.join(td, "out_by_dept")
    r1, _ = run_case("T1 按「部门」拆（原始参数）", main, src, "部门", d1)
    n1 = out_names(d1)
    ok1 = r1 and r1.get("ok") and sorted(n1) == sorted(["华南.xlsx", "华东.xlsx", "华北.xlsx"])
    print("  → 3 个部门 → 3 个文件：%s" % ("PASS" if ok1 else "FAIL"))
    results["T1 按部门拆分（含中文文件名）"] = ok1

    d2 = os.path.join(td, "out_by_region")
    r2, _ = run_case("T1b **业务人员只改 split_column**（部门 → 区域）", main, src, "区域", d2)
    n2 = out_names(d2)
    ok2 = r2 and r2.get("ok") and sorted(n2) == sorted(["南区.xlsx", "东区.xlsx", "北区.xlsx"])
    print("  → 分组跟着换、文件名也换了：%s" % ("PASS" if ok2 else "FAIL"))
    results["T1b 改 split_column 生效（可微调）"] = ok2

    # T2 改错列名 → 中文可读报错
    r3, e3 = run_case("T2 **改错列名**（\"科室\" 不存在）", main, src, "科室", os.path.join(td, "out_bad"))
    ok3 = (e3 is not None and isinstance(e3, RuntimeError)
           and "科室" in str(e3) and "不存在" in str(e3))
    print("  → 抛 RuntimeError 且是中文、点名了列名：%s" % ("PASS" if ok3 else "FAIL"))
    results["T2 改错列名 → 中文可读报错"] = ok3

    # T3 改 out_dir
    d4 = os.path.join(td, "另一个输出目录")
    r4, _ = run_case("T3 **改 out_dir**（换成带中文的新目录）", main, src, "部门", d4)
    ok4 = r4 and r4.get("ok") and os.path.isdir(d4) and len(out_names(d4)) == 3
    print("  → 落到新目录、目录自动创建：%s" % ("PASS" if ok4 else "FAIL"))
    results["T3 改 out_dir 生效（含自动建目录）"] = ok4

    # T4 只读第一个 sheet（说明 sheet_name 不可调）
    all_out = set()
    for d in (d1, d2):
        all_out |= set(out_names(d))
    sheet2_only = {str(r[1]) for r in ROWS_SHEET2[1:]}          # {"西北"}
    hit = sheet2_only & {os.path.splitext(x)[0] for x in all_out}
    print("\n" + "=" * 78)
    print("T4 想拆**第二张表**（sheet2 里部门只有「西北」）")
    print("=" * 78)
    print("  说明：代码是 pd.read_excel(src_file)，无 sheet_name 参数 → 只读第一张表")
    print("  两次运行出现过的文件名：%s" % sorted(all_out))
    print("  「西北」是否出现过：%s" % ("是" if hit else "否"))
    print("  → sheet 名不是流程参数，业务人员要拆第二张表必须找开发改代码：%s"
          % ("PASS（如实体现边界）" if not hit else "FAIL"))
    results["T4 sheet 名不可调（边界如实暴露）"] = (not hit)

    # T5 重跑是否静默覆盖
    print("\n" + "=" * 78)
    print("T5 同参数再跑一次（是否有「是否覆盖」参数）")
    print("=" * 78)
    before = {f: os.path.getmtime(os.path.join(d1, f)) for f in out_names(d1)}
    r5, _ = run_case("T5 重跑（文件已存在）", main, src, "部门", d1)
    after = {f: os.path.getmtime(os.path.join(d1, f)) for f in out_names(d1)}
    overwritten = [f for f in before if after.get(f, 0) != before[f]]
    print("  → 被重写的文件：%s" % (overwritten or "（无）"))
    print("  → 结论：重跑会**静默覆盖**已有结果，流程里没有「是否覆盖」参数、也没有告警。")
    print("     业务人员想保留上一版必须自己先改 out_dir —— 这是可微调边界上的缺口（P2）。")
    results["T5 覆盖行为可被业务人员控制（期望有 overwrite 参数）"] = (not overwritten)

    # T6 空值分组：会不会静默丢行
    src_nan = os.path.join(td, "有空值.xlsx")
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in (("订单号", "部门"), ("SO1", "华南"), ("SO2", None), ("SO3", "华东")):
        ws.append(r)
    wb.save(src_nan)
    d6 = os.path.join(td, "out_nan")
    r6, _ = run_case("T6 部门列有空单元格（输入 3 行数据，其中 1 行部门为空）", main, src_nan, "部门", d6)
    n6 = out_names(d6)
    written_rows = 0
    for f in n6:
        w = openpyxl.load_workbook(os.path.join(d6, f))
        written_rows += max(0, w.active.max_row - 1)
    print("  → 产出文件 %s，数据行合计 %d 行（输入 3 行）" % (n6, written_rows))
    print("  → 空部门那一行：%s"
          % ("**被静默丢弃**（groupby 默认 dropna）——业务人员会拿到少一行的结果，且没有任何提示"
             if written_rows < 3 else "被保留"))
    results["T6 空值行不被静默丢弃"] = (written_rows >= 3)

    # 清理
    for p in (src, src_nan):
        if os.path.exists(p):
            os.remove(p)
    import shutil
    shutil.rmtree(td, ignore_errors=True)

    print("\n" + "=" * 78)
    print("V11 判定汇总（T5/T6 的 FAIL = 已记录的 P2 缺口，不是阻断项）")
    print("=" * 78)
    for k, v in results.items():
        print("  %-40s %s" % (k, "PASS" if v else "FAIL"))
    return 0


if __name__ == "__main__":
    sys.exit(main_test())
