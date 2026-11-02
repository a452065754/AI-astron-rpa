# -*- coding: utf-8 -*-
"""V11 固定回归：产品承诺「业务人员能看懂、能做微调」的静态 + 本地运行证据。

做法：把 `flows/gen_按部门拆分Excel.json` 里 AI 自己写的 `scripts.code` **原样**取出来，
在**本机**用 pandas 跑（不连平台、不调 LLM、不建机器人、不落库），用 4 组固定数据当门禁：

  D1 正常      3 行 / 2 个部门        → 2 个文件、3 行守恒
  D2 空值      其中 1 行分组列为空    → 3 行守恒（空值单独成组）**或**明确报「跳过 1 行」，
                                       绝不静默少行                    ← 历史缺口 T6（P1）
  D3 重复运行  同一目录连跑 2 次        → 第 2 次不改写第 1 次的产出
                                       （改名 `xxx (1).xlsx` 或按参数拒绝）← 历史缺口 T5（P2）
  D4 错误列名  split_column 不存在列   → 中文 RuntimeError，且不改动任何文件

改 prompt / 改生成器 / 重生成流程后，都要把本文件跑一遍：4 组全 PASS 才算「能微调」没退化。

用法：C:/Python314/python.exe logs/qa/v11_tunability.py
退出码：4 组全 PASS → 0；有 FAIL → 1（可直接当门禁）
"""
import hashlib
import io
import json
import logging
import os
import re
import shutil
import sys
import tempfile

import openpyxl

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GEN_SPEC = os.path.join(ROOT, "flows", "gen_按部门拆分Excel.json")

HEADER = ("订单号", "部门", "区域", "金额")
ROWS_D1 = [
    ("SO0001", "华南", "南区", 100),
    ("SO0002", "华东", "东区", 200),
    ("SO0003", "华南", "南区", 300),
]
ROWS_D2 = [
    ("SO0001", "华南", "南区", 100),
    ("SO0002", None, "东区", 200),          # ← 分组列为空的那一行（T6 的病灶）
    ("SO0003", "华东", "南区", 300),
]
ROWS_SHEET2 = [
    ("SO0009", "西北", "西区", 900),
]

CJK = re.compile(r"[\u4e00-\u9fff]")


# --------------------------------------------------------------------------- #
# 基础设施
# --------------------------------------------------------------------------- #
class _LogCollector(logging.Handler):
    """把脚本 logger.info 写出的「人话摘要」抓下来（astronverse 不在时走 stdlib logging）。"""

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.lines = []

    def emit(self, record):
        try:
            self.lines.append(record.getMessage())
        except Exception:                                   # noqa: BLE001
            self.lines.append(str(record.msg))


def build_xlsx(path, rows, header=HEADER, sheet2=None):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "明细"
    ws.append(list(header))
    for r in rows:
        ws.append(list(r))
    if sheet2 is not None:
        ws2 = wb.create_sheet("第二张表")
        ws2.append(list(header))
        for r in sheet2:
            ws2.append(list(r))
    wb.save(path)
    return path


def load_main():
    """从交付物里取脚本原文并 exec；返回 (main, code)。"""
    with open(GEN_SPEC, encoding="utf-8") as f:
        spec = json.load(f)
    script = spec["scripts"]["excel_split_by_dept"]
    code = script["code"]
    ns = {"__name__": "qa_generated_script"}
    exec(compile(code, "<flows/gen_按部门拆分Excel.json:scripts.code>", "exec"), ns)  # noqa: S102
    return ns["main"], code, script


def run_script(main, args):
    """跑一次脚本，返回 (result, exc, 全过程文本)。全过程文本含 stdout/stderr/logger。"""
    collector = _LogCollector()
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(collector)
    root.setLevel(logging.DEBUG)
    out, err = io.StringIO(), io.StringIO()
    result, exc = None, None
    try:
        with _redirect(out, err):
            result = main(dict(args))
    except BaseException as e:                              # noqa: BLE001
        exc = e
    finally:
        root.removeHandler(collector)
        root.setLevel(old_level)
    text = "\n".join([out.getvalue(), err.getvalue(), "\n".join(collector.lines)])
    return result, exc, text


class _redirect(object):
    """轻量 stdout/stderr 重定向（不依赖 contextlib.redirect_* 的嵌套语义）。"""

    def __init__(self, out, err):
        self.out, self.err = out, err

    def __enter__(self):
        self._so, self._se = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = self.out, self.err
        return self

    def __exit__(self, *a):
        sys.stdout, sys.stderr = self._so, self._se
        return False


def xlsx_files(d):
    if not os.path.isdir(d):
        return []
    return sorted(f for f in os.listdir(d) if f.lower().endswith(".xlsx"))


def snapshot(d):
    """{文件名: (mtime_ns, size, md5)} —— 判断 D3 有没有被静默覆盖时以 md5 为准（比文件名/mtime 硬）。"""
    snap = {}
    for f in xlsx_files(d):
        p = os.path.join(d, f)
        st = os.stat(p)
        with open(p, "rb") as fh:
            digest = hashlib.md5(fh.read()).hexdigest()     # noqa: S324 —— 只用来比对内容是否变化
        snap[f] = (st.st_mtime_ns, st.st_size, digest)
    return snap


def count_data_rows(path):
    """数某个 xlsx 第一个 sheet 的**非空数据行**（跳过表头与整行全空）。"""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.active
        n = 0
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i == 0:
                continue
            if all(v is None or (isinstance(v, str) and not v.strip()) for v in row):
                continue
            n += 1
        return n
    finally:
        wb.close()


def total_rows(d):
    return sum(count_data_rows(os.path.join(d, f)) for f in xlsx_files(d))


def reports_skipped(text, n):
    """脚本有没有「明确报告跳过 n 行」——D2 允许这种替代方案。"""
    pats = [
        r"跳过\s*%d\s*行" % n,
        r"丢弃\s*%d\s*行" % n,
        r"忽略\s*%d\s*行" % n,
        r"skip(?:ped)?\s*%d\s*rows?" % n,
        r"^\s*skipped\s*[:=]\s*%d\s*$" % n,
    ]
    return any(re.search(p, text, re.I | re.M) for p in pats)


def show_result(result, exc, text):
    if exc is not None:
        print("  抛出：%s: %s" % (type(exc).__name__, exc))
    if result is not None:
        brief = dict(result)
        if isinstance(brief.get("outputs"), list):
            brief["outputs"] = [os.path.basename(x) for x in brief["outputs"]]
        print("  返回：%s" % json.dumps(brief, ensure_ascii=False, default=str))
    tail = [ln for ln in text.splitlines() if ln.strip()][-3:]
    for ln in tail:
        print("  日志：%s" % ln)


# --------------------------------------------------------------------------- #
# 四个固定用例
# --------------------------------------------------------------------------- #
def case_d1(main, root_dir):
    """D1 正常：3 行 / 2 个部门 → 2 个文件、3 行守恒。"""
    print("\n" + "=" * 80)
    print("D1 正常：3 行数据、2 个部门（华南 2 行 / 华东 1 行）")
    print("=" * 80)
    src = build_xlsx(os.path.join(root_dir, "d1.xlsx"), ROWS_D1)
    out = os.path.join(root_dir, "d1_out")
    result, exc, text = run_script(
        main, {"src_file": src, "split_column": "部门", "out_dir": out})
    show_result(result, exc, text)
    files = xlsx_files(out)
    rows = total_rows(out)
    print("  产出文件：%s" % (files or "（无）"))
    print("  行数守恒：产出 %d 行 / 输入 %d 行" % (rows, len(ROWS_D1)))
    ok_files = sorted(files) == sorted(["华南.xlsx", "华东.xlsx"])
    ok_rows = rows == len(ROWS_D1)
    ok_ret = exc is None and isinstance(result, dict) and result.get("ok") is True
    print("  断言：文件数符合预期=%s / 行数守恒=%s / 返回 ok=%s"
          % (ok_files, ok_rows, ok_ret))
    return ok_files and ok_rows and ok_ret, {"files": files, "rows": rows}


def case_d2(main, root_dir):
    """D2 空值：1 行分组列为空 → 行数守恒，或明确报「跳过 1 行」。绝不静默少行。"""
    print("\n" + "=" * 80)
    print("D2 空值：3 行数据，其中 1 行「部门」为空（T6 病灶）")
    print("=" * 80)
    src = build_xlsx(os.path.join(root_dir, "d2.xlsx"), ROWS_D2)
    out = os.path.join(root_dir, "d2_out")
    result, exc, text = run_script(
        main, {"src_file": src, "split_column": "部门", "out_dir": out})
    show_result(result, exc, text)
    files = xlsx_files(out)
    rows = total_rows(out)
    ret_text = json.dumps(result, ensure_ascii=False, default=str) if result else ""
    skipped = reports_skipped(text + "\n" + ret_text, len(ROWS_D2) - rows)
    print("  产出文件：%s" % (files or "（无）"))
    print("  行数守恒：产出 %d 行 / 输入 %d 行（差 %d 行）"
          % (rows, len(ROWS_D2), len(ROWS_D2) - rows))
    ok_conserve = rows == len(ROWS_D2)
    ok_reported = (not ok_conserve) and skipped
    if ok_conserve:
        print("  断言：行数守恒=True（空值行被单独成组保留）")
    elif ok_reported:
        print("  断言：行数不守恒，但脚本**明确报了跳过 %d 行** → 可接受"
              % (len(ROWS_D2) - rows))
    else:
        print("  断言：**静默丢弃 %d 行**，既没守恒也没报跳过 → FAIL（P1）"
              % (len(ROWS_D2) - rows))
    return ok_conserve or ok_reported, {"files": files, "rows": rows}


def case_d3(main, root_dir):
    """D3 重复运行：同参数连跑 2 次 → 第 2 次不改写第 1 次的产出。"""
    print("\n" + "=" * 80)
    print("D3 重复运行：同一 out_dir 连跑 2 次（T5 病灶）")
    print("=" * 80)
    src = build_xlsx(os.path.join(root_dir, "d3.xlsx"), ROWS_D1)
    out = os.path.join(root_dir, "d3_out")
    args = {"src_file": src, "split_column": "部门", "out_dir": out}
    r1, e1, t1 = run_script(main, args)
    before = snapshot(out)
    print("  第 1 次：%s" % ("成功" if e1 is None else "抛出 %s: %s" % (type(e1).__name__, e1)))
    print("  第 1 次产出：%s" % (sorted(before) or "（无）"))
    r2, e2, t2 = run_script(main, args)
    after = snapshot(out)
    show_result(r2, e2, t2)
    print("  第 2 次产出：%s" % (sorted(after) or "（无）"))
    rewritten = [f for f in before if after.get(f, (None,))[0] != before[f][0]]
    added = sorted(set(after) - set(before))
    refused = e2 is not None or (isinstance(r2, dict) and r2.get("ok") is False)
    print("  被改写的旧文件：%s" % (rewritten or "（无）"))
    print("  新增文件：%s" % (added or "（无）"))
    print("  第 2 次是否主动拒绝/提示：%s" % ("是" if refused else "否"))
    # 观察项（不进门禁）：改名生效了，但用户知不知道「这次写到哪去了」？
    told = any(a in (t2 or "") for a in added) if added else True
    print("  改名是否写进 summary/日志：%s%s"
          % ("是" if told else "否",
             "" if told else " ← 业务人员会去开旧文件，看到上次的数据，以为流程没生效（P2）"))
    ok = (not rewritten) and (bool(added) or refused)
    if rewritten:
        print("  断言：**静默覆盖**了 %d 个已存在文件，既没改名也没拒绝 → FAIL" % len(rewritten))
    elif added:
        print("  断言：旧文件未被改写、产出改名新增 %s → PASS" % added)
    elif refused:
        print("  断言：主动拒绝/提示，文件未动 → PASS")
    else:
        print("  断言：文件未动也没新增，第 2 次等于什么都没做 → FAIL（不算「可控」）")
    return ok, {"rewritten": rewritten, "added": added}


def case_d4(main, root_dir):
    """D4 错误列名 → 中文 RuntimeError，且不改动任何文件。"""
    print("\n" + "=" * 80)
    print("D4 错误列名：split_column 传不存在的列「科室」")
    print("=" * 80)
    src = build_xlsx(os.path.join(root_dir, "d4.xlsx"), ROWS_D1)
    out = os.path.join(root_dir, "d4_out")
    os.makedirs(out, exist_ok=True)
    sentinel = os.path.join(out, "已有文件.xlsx")
    build_xlsx(sentinel, [("SO0000", "华北", "北区", 0)])
    before = snapshot(out)
    result, exc, text = run_script(
        main, {"src_file": src, "split_column": "科室", "out_dir": out})
    show_result(result, exc, text)
    after = snapshot(out)
    ok_err = isinstance(exc, RuntimeError) and "科室" in str(exc) and bool(CJK.search(str(exc)))
    new_files = sorted(set(after) - set(before))
    changed = [f for f in before if after.get(f, (None,))[0] != before[f][0]]
    print("  异常类型：%s%s"
          % ("（无异常）" if exc is None else type(exc).__name__,
             "" if exc is None else " —— %s" % exc))
    print("  是 RuntimeError：%s" % isinstance(exc, RuntimeError))
    print("  报错点名了列名「科室」：%s" % (exc is not None and "科室" in str(exc)))
    print("  报错是中文：%s" % (exc is not None and bool(CJK.search(str(exc)))))
    print("  目录里新增文件：%s / 被改动文件：%s"
          % (new_files or "（无）", changed or "（无）"))
    ok_clean = not new_files and not changed
    print("  断言：中文可读报错=%s / 未动任何文件=%s" % (ok_err, ok_clean))
    return ok_err and ok_clean, {"new": new_files, "changed": changed}


# --------------------------------------------------------------------------- #
# 附加观察（不计入门禁，用于报告里说明可微调边界）
# --------------------------------------------------------------------------- #
def observe(main, root_dir, code):
    print("\n" + "=" * 80)
    print("附加观察（不计入 PASS/FAIL 门禁，写进报告当「可微调边界」）")
    print("=" * 80)

    src = build_xlsx(os.path.join(root_dir, "obs.xlsx"), ROWS_D1, sheet2=ROWS_SHEET2)

    d = os.path.join(root_dir, "obs_by_region")
    r, e, _ = run_script(main, {"src_file": src, "split_column": "区域", "out_dir": d})
    print("  O1 只改 split_column（部门→区域）：产出 %s → %s"
          % (xlsx_files(d) or "（无）",
             "改参数即生效" if sorted(xlsx_files(d)) == ["东区.xlsx", "南区.xlsx"]
             else "未按预期生效"))

    d2 = os.path.join(root_dir, "obs_newdir", "含中文路径")
    r, e, _ = run_script(main, {"src_file": src, "split_column": "部门", "out_dir": d2})
    print("  O2 只改 out_dir（换带中文的新目录）：目录自动创建=%s / 产出 %d 个文件"
          % (os.path.isdir(d2), len(xlsx_files(d2))))

    d3 = os.path.join(root_dir, "obs_sheet2")
    r, e, _ = run_script(main, {"src_file": src, "split_column": "部门",
                                "out_dir": d3, "sheet_name": "第二张表"})
    west = {str(r2[1]) for r2 in ROWS_SHEET2}
    tuned = bool(west & set(os.path.splitext(f)[0] for f in xlsx_files(d3)))
    if "sheet_name" not in code:
        print("  O3 想拆第 2 张表（sheet2 只有「西北」）：代码无 sheet_name 参数 → "
              "业务人员要拆第二张表必须找开发改代码（可微调边界）")
    else:
        print("  O3 想拆第 2 张表：传 sheet_name=「第二张表」→ 产出 %s → sheet 可调=%s"
              % (xlsx_files(d3) or "（无）", "是" if tuned else "否"))

    # O3b：数字取值的语义坑 —— 0 被特判成「第一张表」，那 1 是不是第二张表？
    if "sheet_name" in code:
        d4 = os.path.join(root_dir, "obs_sheet_idx")
        r, e, _ = run_script(main, {"src_file": src, "split_column": "部门",
                                    "out_dir": d4, "sheet_name": "1"})
        print("  O3b sheet_name=1（业务人员直觉=第二张表）→ %s"
              % ("产出 %s" % xlsx_files(d4) if e is None
                 else "抛 %s: %s" % (type(e).__name__, e)))
        print("      结论：0=第一张表，但 1≠第二张表（被当成 sheet 名「1」查）。"
              "数字既不是纯索引也不是纯名字，口径不自洽 —— "
              "P2 建议：纯数字按索引、非数字按名字。")

    print("  O4 可调项（params）：见 flows/gen_按部门拆分Excel.json 的 params 与 maintain_hint")


# --------------------------------------------------------------------------- #
def main_test():
    main, code, script = load_main()
    print("=" * 80)
    print("V11 固定回归（4 组）｜被测：flows/gen_按部门拆分Excel.json → scripts.code")
    print("=" * 80)
    print("  代码 %d 行；入口 def main(args)：%s"
          % (len(code.splitlines()), "def main(args):" in code))
    print("  声明参数（args.get 应与之完全一致）：%s"
          % sorted(re.findall(r"args\.get\(['\"]([^'\"]+)['\"]\)", code)))
    print("  maintain_hint：%s" % script.get("maintain_hint", "（缺）"))

    root_dir = tempfile.mkdtemp(prefix="qa_v11_")
    results, details = {}, {}
    try:
        for key, fn in (("D1 正常（行数/文件数守恒）", case_d1),
                        ("D2 空值不静默丢行", case_d2),
                        ("D3 重复运行不静默覆盖", case_d3),
                        ("D4 错误列名 → 中文报错且不动文件", case_d4)):
            try:
                ok, info = fn(main, root_dir)
            except Exception as e:                          # noqa: BLE001
                print("  用例自身异常：%s: %s" % (type(e).__name__, e))
                ok, info = False, {"error": str(e)}
            results[key] = ok
            details[key] = info

        observe(main, root_dir, code)
    finally:
        shutil.rmtree(root_dir, ignore_errors=True)

    print("\n" + "=" * 80)
    print("V11 判定汇总（4 组固定回归）")
    print("=" * 80)
    for k, v in results.items():
        print("  %-32s %s" % (k, "PASS" if v else "FAIL"))
    failed = [k for k, v in results.items() if not v]
    print("\n  通过 %d/%d" % (len(results) - len(failed), len(results)))
    if failed:
        print("  未通过：%s" % "、".join(failed))
        print("  说明：D2/D3 的 FAIL 是「脚本健壮性」缺口，改 SYSTEM_PROMPT 后重生成流程即可转 PASS。")
        return 1
    print("  全部 PASS：业务人员只改参数即可安全使用；改错有中文报错；重复跑不丢数据。")
    return 0


if __name__ == "__main__":
    sys.exit(main_test())
