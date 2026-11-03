# -*- coding: utf-8 -*-
"""V2 负向用例 + V3 正向用例：验证 spec_lint 能准确报错、错误信息能指导修复。

只读，不落库、不调 LLM、不碰平台。用法：
    C:/Python314/python.exe logs/qa/v2_negative.py
    C:/Python314/python.exe logs/qa/v2_negative.py --only N2

V3（完全正确的含 scripts 规格）用 `--only V3` 单独跑，或默认一起跑。
"""
import argparse
import copy
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import flow_generator as fg          # noqa: E402
import spec_lint as sl               # noqa: E402

ATOMS = fg.load_atom_dict(offline=True)

# 一段「正确」的极简脚本：读 xlsx 按某列唯一值拆成多个文件（约 20 行）
GOOD_CODE = '''\
import os
import openpyxl


def main(args):
    src_file = args.get("src_file")
    split_column = args.get("split_column")
    out_dir = args.get("out_dir")
    if not src_file or not os.path.exists(src_file):
        raise RuntimeError("源文件不存在：%s" % src_file)
    os.makedirs(out_dir, exist_ok=True)
    wb = openpyxl.load_workbook(src_file)
    ws = wb.active
    header = [c.value for c in ws[1]]
    col = header.index(split_column) + 1
    groups = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        groups.setdefault(row[col - 1], []).append(row)
    written = []
    for key, rows in groups.items():
        nb = openpyxl.Workbook()
        ns = nb.active
        ns.append(header)
        for r in rows:
            ns.append(list(r))
        path = os.path.join(out_dir, "%s.xlsx" % key)
        nb.save(path)
        written.append(path)
    return {"ok": True, "groups": len(written), "skipped": 0,
            "files": written[:5]}
'''


def base_spec():
    """完全正确的含 scripts 的最小规格（V3 的基线，负向用例从它派生）。"""
    mp = [{"varName": "src_file", "varValue": "$p:src_file"},
          {"varName": "split_column", "varValue": "$p:split_column"},
          {"varName": "out_dir", "varValue": "$p:out_dir"}]
    return {
        "name": "QA-xlsx按列拆表",
        "nl_examples": ["把订单表按部门列拆成多个文件"],
        "keywords": ["拆表", "按列拆分"],
        "params": [
            {"name": "src_file", "type": "Str",
             "default": "${PROJECT_ROOT}/qa/in/订单.xlsx", "describe": "源 Excel 路径"},
            {"name": "split_column", "type": "Str",
             "default": "部门", "describe": "按哪一列拆"},
            {"name": "out_dir", "type": "Str",
             "default": "${PROJECT_ROOT}/qa/out", "describe": "输出目录"},
        ],
        "scripts": {
            "excel_split": {
                "name": "Excel按列拆表",
                "why": "命中判定 B-2：输出文件数取决于「部门」列的唯一值，原子无法动态生成文件名",
                "maintain_hint": "改拆哪一列 → 改参数 split_column；改输出目录 → 改参数 out_dir；"
                                 "改逻辑需找开发改 Python",
                "atom_suggestion": "Excel 按列唯一值拆分成多表",
                "code": GOOD_CODE,
            }
        },
        "nodes": [
            {"atom": "Script.module",
             "alias": "按部门拆分（自动脚本，改逻辑需开发）",
             "inputs": {"content": "$script:excel_split",
                        "module_param": "$json:" + json.dumps(mp, ensure_ascii=False)},
             "out": {"program_script": "split_result"}},
        ],
    }


def run_lint(spec):
    return sl.lint(spec, ATOMS)


def show(title, spec, judge):
    errs, warns = run_lint(spec)
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)
    print("lint 结果：错误 %d 条 / 警告 %d 条" % (len(errs), len(warns)))
    for e in errs:
        print("  [E] %s" % e["msg"])
        if e.get("fix"):
            print("      fix → %s" % e["fix"])
    for w in warns:
        print("  [W] %s" % w["msg"])
        if w.get("fix"):
            print("      fix → %s" % w["fix"])
    if not errs and not warns:
        print("  （0 错误 0 警告）")
    print("  人工判断（能否照着改）：%s" % judge)
    return errs, warns


def v3_positive():
    """V3 正向：正确规格 lint 必须 0 错 0 警；且 load_spec 不报未知字段。"""
    spec = base_spec()
    errs, warns = show("[V3] 完全正确的含 scripts 最小规格", spec,
                       "预期 0 错 0 警（基线正确性）")
    ok_lint = (not errs) and (not warns)

    # load_spec：落盘到**系统临时目录**（不污染 logs/qa 这个入库目录）后加载，
    # 检查 $script: / $json: 是否被破坏
    import tempfile
    _td = tempfile.mkdtemp(prefix="qa_v3_")
    tmp = os.path.join(_td, "_v3_good_spec.json")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(base_spec(), f, ensure_ascii=False, indent=1)
    loaded_ok, detail = True, []
    try:
        loaded = fg.load_spec(tmp)
        content = loaded["nodes"][0]["inputs"]["content"]
        mp = loaded["nodes"][0]["inputs"]["module_param"]
        detail.append("load_spec 通过，无「未知字段 scripts」异常")
        detail.append("nodes[0].inputs.content 加载后 = %r" % content)
        detail.append("module_param 仍以 $json: 开头 = %s" % mp.startswith("$json:"))
        detail.append("scripts 规范化后键 = %s" % sorted(loaded["scripts"]))
        detail.append("scripts.excel_split 字段 = %s" % sorted(loaded["scripts"]["excel_split"]))
        if content != "$script:excel_split":
            loaded_ok = False
            detail.append("❌ content 被改写了（应为 $script:excel_split）")
        if not mp.startswith("$json:"):
            loaded_ok = False
        if "def main(args):" not in loaded["scripts"]["excel_split"]["code"]:
            loaded_ok = False
            detail.append("❌ 代码被改写")
    except Exception as e:                                       # noqa: BLE001
        loaded_ok = False
        detail.append("❌ load_spec 抛异常：%s: %s" % (type(e).__name__, e))
    print("\n  load_spec 验证：")
    for d in detail:
        print("    · %s" % d)
    return ok_lint and loaded_ok


def case_n1():
    spec = base_spec()
    spec["scripts"]["excel_split"]["code"] = GOOD_CODE.replace(
        "import os\nimport openpyxl", "import os\nimport requests2\nimport openpyxl")
    errs, _w = show("[N1] code import 了白名单外的库 requests2（环境离线）", spec,
                    "需说清「哪个库不行」+「环境离线」+「能用哪些库」")
    fixed = base_spec()
    e2, w2 = run_lint(fixed)
    print("  修掉后（改回 openpyxl）：错误 %d / 警告 %d → %s"
          % (len(e2), len(w2), "变绿" if not e2 and not w2 else "仍不绿"))
    return any("requests2" in e["msg"] for e in errs) and not e2 and not w2


def case_n2():
    spec = base_spec()
    spec["scripts"]["excel_split"]["code"] = GOOD_CODE + \
        '    # 额外读一个没在 module_param 里声明的参数\n' \
        '    _ignore = args.get("src_file_backup")\n'
    # 让代码读一个未声明参数：把读语句挪进 main 体内（缩进 4）
    code = GOOD_CODE.replace('    src_file = args.get("src_file")',
                             '    src_file = args.get("src_file")\n'
                             '    legacy = args.get("template_file")')
    spec["scripts"]["excel_split"]["code"] = code
    errs, _w = show("[N2] code 读 args.get(\"template_file\")，但 module_param 只给了另外三个", spec,
                    "需说清「缺哪个参数名」+「运行时会拿到 None」+「怎么补」")
    fixed = base_spec()
    e2, w2 = run_lint(fixed)
    print("  修掉后（删掉那行/补上 module_param）：错误 %d / 警告 %d → %s"
          % (len(e2), len(w2), "变绿" if not e2 and not w2 else "仍不绿"))
    return any("template_file" in e["msg"] for e in errs) and not e2 and not w2


def case_n3():
    spec = base_spec()
    spec["scripts"]["excel_split"]["code"] = GOOD_CODE.replace(
        "def main(args):", "def run(args):")
    errs, _w = show("[N3] code 里没有 def main(args)", spec,
                    "需说清「平台只调 main」+「怎么写入口」")
    fixed = base_spec()
    e2, w2 = run_lint(fixed)
    print("  修掉后：错误 %d / 警告 %d → %s" % (len(e2), len(w2),
                                              "变绿" if not e2 and not w2 else "仍不绿"))
    return any("def main(args)" in e["msg"] for e in errs) and not e2 and not w2


def case_n4():
    spec = base_spec()
    bad = GOOD_CODE.replace('    if not src_file or not os.path.exists(src_file):\n',
                            '    if not src_file or not os.path.exists(src_file)\n')
    spec["scripts"]["excel_split"]["code"] = bad
    errs, _w = show("[N4] code 有语法错误（if 少一个冒号）", spec,
                    "需给出「第几行」+「什么语法错」")
    has_lineno = any(("行" in e["msg"]) and any(ch.isdigit() for ch in e["msg"]) for e in errs)
    fixed = base_spec()
    e2, w2 = run_lint(fixed)
    print("  修掉后：错误 %d / 警告 %d → %s" % (len(e2), len(w2),
                                              "变绿" if not e2 and not w2 else "仍不绿"))
    return has_lineno and not e2 and not w2


def case_n5():
    spec = base_spec()
    spec["nodes"][0]["inputs"]["content"] = "$script:不存在的名字"
    errs, _w = show("[N5] content 引用了不存在的脚本名", spec,
                    "需说清「引用了哪个」+「列出可用脚本名」")
    lists_available = any("excel_split" in (e.get("fix") or "") for e in errs)
    fixed = base_spec()
    e2, w2 = run_lint(fixed)
    print("  修掉后：错误 %d / 警告 %d → %s" % (len(e2), len(w2),
                                              "变绿" if not e2 and not w2 else "仍不绿"))
    return bool(errs) and lists_available and not e2 and not w2


def case_n6():
    spec = base_spec()
    spec["scripts"]["excel_split"].pop("why")
    errs, _w = show("[N6] scripts.x 缺 why 字段", spec,
                    "需 err 级（阻断）+ 说清为什么必须有 why")
    is_err = any("why" in e["msg"] for e in errs)
    # 防止误判：确认它在 errs 里而不是 warns 里
    fixed = base_spec()
    e2, w2 = run_lint(fixed)
    print("  修掉后：错误 %d / 警告 %d → %s" % (len(e2), len(w2),
                                              "变绿" if not e2 and not w2 else "仍不绿"))
    return is_err and not e2 and not w2


def case_n7():
    spec = base_spec()
    spec["scripts"]["excel_split"].pop("maintain_hint")
    errs, _w = show("[N7] scripts.x 缺 maintain_hint 字段", spec,
                    "需 err 级（阻断）+ 说清业务人员改什么")
    is_err = any("maintain_hint" in e["msg"] for e in errs)
    fixed = base_spec()
    e2, w2 = run_lint(fixed)
    print("  修掉后：错误 %d / 警告 %d → %s" % (len(e2), len(w2),
                                              "变绿" if not e2 and not w2 else "仍不绿"))
    return is_err and not e2 and not w2


def case_n8():
    spec = base_spec()
    spec["nodes"][0]["out"] = {}
    errs, warns = show("[N8] out 里没有 program_script", spec,
                       "应为 warn（不阻断）+ 说清后果（返回值无处落地）")
    is_warn = any("program_script" in w["msg"] for w in warns)
    no_err = not errs
    print("  修掉后：", end="")
    fixed = base_spec()
    e2, w2 = run_lint(fixed)
    print("错误 %d / 警告 %d → %s" % (len(e2), len(w2),
                                     "变绿" if not e2 and not w2 else "仍不绿"))
    return is_warn and no_err and not e2 and not w2


def case_n9():
    spec = base_spec()
    spec["scripts"]["excel_split_unused"] = {
        "name": "没人用的脚本",
        "why": "命中判定 B-3：分组聚合原子做不到",
        "maintain_hint": "改分组字段 → 改参数 group_by",
        "code": 'def main(args):\n    return {"ok": True, "groups": 0, "skipped": 0, '
                '"files": []}\n',
    }
    errs, warns = show("[N9] scripts 定义了但没有任何节点引用", spec,
                       "应为 warn（不阻断）+ 说清怎么用或删掉")
    is_warn = any("excel_split_unused" in w["msg"] and "没有" in w["msg"] for w in warns)
    no_err = not errs
    fixed = base_spec()
    e2, w2 = run_lint(fixed)
    print("  修掉后：错误 %d / 警告 %d → %s" % (len(e2), len(w2),
                                              "变绿" if not e2 and not w2 else "仍不绿"))
    return is_warn and no_err and not e2 and not w2


CASES = [("V3", v3_positive), ("N1", case_n1), ("N2", case_n2), ("N3", case_n3),
         ("N4", case_n4), ("N5", case_n5), ("N6", case_n6), ("N7", case_n7),
         ("N8", case_n8), ("N9", case_n9)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    results = {}
    for name, fn in CASES:
        if a.only and a.only != name:
            continue
        try:
            results[name] = bool(fn())
        except Exception as e:                                   # noqa: BLE001
            import traceback
            traceback.print_exc()
            results[name] = False
    print("\n" + "=" * 78)
    print("V2/V3 判定汇总")
    print("=" * 78)
    for name, _fn in CASES:
        if name in results:
            print("  %-4s %s" % (name, "PASS" if results[name] else "FAIL"))
    bad = [k for k, v in results.items() if not v]
    print("\n未通过：%s" % (", ".join(bad) if bad else "无"))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
