# -*- coding: utf-8 -*-
"""V8（Round-3 新增规则的抽查）：spec_lint 在 2026-11-02 后追加的三条「防模型踩坑」错误。

`spec_lint.py` 新增 `_split_var_ext()` 及三类 err：
  R-a `"$clean_dept.xlsx"`            → 「变量 + 扩展名」应写成拼接数组
  R-b `"$key_pos == -1"`              → 「$ 引用里写表达式」应用 Code.If
  R-c 循环体内 `$item` 没产出           → Code.ForList 的 out 要显式声明

目的：① 三条都真的报 err；② 报错文案给出下一步动作；③ 不误伤 87 条老流程（由 v1 覆盖）。

用法：C:/Python314/python.exe logs/qa/v8_newrules.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import flow_generator as fg      # noqa: E402
import spec_lint as sl           # noqa: E402

ATOMS = fg.load_atom_dict(offline=True)


def base(fw_inputs, nodes=None):
    return {
        "name": "QA-新规则抽查",
        "nl_examples": ["x"],
        "params": [{"name": "in_dir", "type": "Str", "default": "F:\\qa\\in"}],
        "nodes": nodes or [
            {"atom": "File.get_file_list",
             "alias": "列文件",
             "inputs": {"folder_path": "F:\\qa\\in"},
             "out": {"file_list": "files"}},
            {"atom": "File.file_write",
             "alias": "写文件",
             "inputs": fw_inputs,
             "out": {"write_file_path": "written"}},
        ],
    }


def run(title, spec, expect_kw, expect_fix_kw):
    errs, warns = sl.lint(spec, ATOMS)
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)
    print("错误 %d 条 / 警告 %d 条" % (len(errs), len(warns)))
    for e in errs:
        print("  [E] %s" % e["msg"])
        if e.get("fix"):
            print("      fix → %s" % e["fix"])
    for w in warns:
        print("  [W] %s" % w["msg"])
    hit = any(expect_kw in e["msg"] for e in errs)
    fix_ok = any(expect_kw in e["msg"] and expect_fix_kw in (e.get("fix") or "") for e in errs)
    print("  命中该错误（err）：%s ；fix 给出下一步动作：%s"
          % ("PASS" if hit else "FAIL", "PASS" if fix_ok else "FAIL"))
    return hit and fix_ok


def main():
    results = {}

    # R-a：变量 + 扩展名
    results["R-a $变量.xlsx 应报「变量+扩展名」"] = run(
        '[R-a] file_path 写 "$clean_dept.xlsx"（想表达「变量 + 扩展名」）',
        base({"file_path": "$clean_dept.xlsx", "file_option": "create",
              "write_type": "overwrite", "msg": "x"}),
        "变量 + 扩展名", "拼接请写成数组")

    # R-b：$ 引用里写表达式
    results["R-b $x == 1 应报「看起来是表达式」"] = run(
        '[R-b] msg 写 "$key_pos == -1"（把条件判断写进引用）',
        base({"file_path": "$files", "file_option": "create",
              "write_type": "overwrite", "msg": "$key_pos == -1"}),
        "看起来是一个表达式", "Code.If")

    # R-c：循环变量没产出（Code.ForList 的 out 写成空）
    loop_nodes = [
        {"atom": "Code.ForList", "alias": "遍历文件",
         "inputs": {"list": "$files"}, "out": {}},
        {"atom": "File.file_write", "alias": "写一个",
         "inputs": {"file_path": ["$item", "_out.txt"], "file_option": "create",
                    "write_type": "overwrite", "msg": "x"},
         "out": {"write_file_path": "written"}},
        {"atom": "Code.ForEnd", "alias": "循环结束", "inputs": {}, "out": {}},
    ]
    results["R-c 循环变量未产出应报 err"] = run(
        "[R-c] Code.ForList 的 out 写成 {}，循环体里引用 $item",
        base({}, nodes=[
            {"atom": "File.get_file_list", "alias": "列文件",
             "inputs": {"folder_path": "F:\\qa\\in"}, "out": {"file_list": "files"}},
        ] + loop_nodes),
        "循环变量 $item 没有产出", "out")

    print("\n" + "=" * 78)
    print("V8 判定汇总")
    print("=" * 78)
    for k, v in results.items():
        print("  %-40s %s" % (k, "PASS" if v else "FAIL"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
