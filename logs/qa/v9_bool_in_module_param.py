# -*- coding: utf-8 -*-
"""V9（N10）：`module_param` 里写 JSON 布尔 / null 会不会在运行期变成 NameError。

背景（工程师 2026-11-02 的 E1 实验结果）：走 `need_parse="json_str"` 的 `module_param`
会被引擎当 **Python 源码**插入（`complex_param_parser(<JSON 原样>)`），而 Python 的
`true/false/null` 是 `True/False/None` —— 写 JSON 布尔会 `NameError`。

这条用例要回答两件事：
  1) 什么样的 `module_param` 会走 `need_parse="json_str"`？（离线看生成结果）
  2) 生成期 `spec_lint` 会不会拦住 JSON 布尔 / null？（期望拦，否则真跑才炸）

用法：C:/Python314/python.exe logs/qa/v9_bool_in_module_param.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import flow_generator as fg      # noqa: E402
import spec_lint as sl           # noqa: E402

ATOMS = fg.load_atom_dict(offline=True)
CODE = ('import os\n\n\ndef main(args):\n'
        '    flag = args.get("flag")\n'
        '    src = args.get("src")\n'
        '    return {"ok": True, "groups": 0, "skipped": 0, "files": []}\n')


def spec_with(mp_list):
    """循环 + 脚本节点：module_param 含 $var:item → 走 need_parse="json_str" 路径。"""
    return {
        "name": "QA-bool-in-module-param",
        "params": [{"name": "flag", "type": "Str", "default": "true"}],
        "scripts": {"s": {"name": "QA", "why": "命中判定 B-2：输出个数取决于数据",
                          "maintain_hint": "改输出目录 → 改参数 out_dir", "code": CODE}},
        "nodes": [
            {"atom": "File.get_file_list", "alias": "列文件",
             "inputs": {"folder_path": "F:\\qa\\in"}, "out": {"file_list": "files"}},
            {"atom": "Code.ForList", "alias": "遍历",
             "inputs": {"list": "$files"}, "out": {"index": "idx", "item": "item"}},
            {"atom": "Script.module", "alias": "处理一个（自动脚本）",
             "inputs": {"content": "$script:s",
                        "module_param": "$json:" + json.dumps(mp_list, ensure_ascii=False)},
             "out": {"program_script": "r"}},
            {"atom": "Code.ForEnd", "alias": "循环结束", "inputs": {}, "out": {}},
        ],
    }


def built_mp(spec):
    """离线构建节点树，取 Script.module 的 module_param 输入项（看 need_parse）。"""
    pc = fg.build_process_content(spec, ATOMS)
    for n in pc:
        if n.get("key") == "Script.module":
            for it in n.get("inputList") or []:
                if it.get("key") == "module_param":
                    return it
    return {}


def check(title, mp_list, expect_blocked):
    spec = spec_with(mp_list)
    errs, warns = sl.lint(spec, ATOMS)
    item = built_mp(spec)
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)
    print("  module_param（规格里写的）= %s"
          % json.dumps(mp_list, ensure_ascii=False))
    print("  构建后 inputList 项 = key=%r need_parse=%r 值(前80)=%r"
          % (item.get("key"), item.get("need_parse"), str(item.get("value"))[:80]))
    print("  → 走 need_parse 路径（值会被当 Python 源码）：%s"
          % ("是" if item.get("need_parse") == "json_str" else "否"))
    print("  lint：错误 %d / 警告 %d" % (len(errs), len(warns)))
    for e in errs:
        print("    [E] %s" % e["msg"])
        if e.get("fix"):
            print("        fix → %s" % e["fix"])
    for w in warns:
        print("    [W] %s" % w["msg"])
    relevant = [e for e in errs if ("布尔" in e["msg"] or "null" in e["msg"]
                                    or "None" in e["msg"] or "Python" in e["msg"])]
    if expect_blocked:
        ok = bool(relevant)
        print("  → 生成期拦住了 JSON 布尔/null：%s" % ("PASS" if ok else "FAIL（漏检）"))
    else:
        ok = not relevant
        print("  → 字符串写法不误报：%s" % ("PASS" if ok else "FAIL（误报）"))
    return ok


def main():
    results = {}
    # N10-a：JSON 布尔 true（模型按「Bool 参数写 true/false」的全局规则很容易这么写）
    results["N10-a JSON 布尔 true 应被拦"] = check(
        "[N10-a] module_param 里写 JSON 布尔 true（Python 里是 True）",
        [{"varName": "flag", "varValue": True},
         {"varName": "src", "varValue": "$var:item"}],
        expect_blocked=True)

    # N10-b：JSON null（Python 里是 None）
    results["N10-b JSON null 应被拦"] = check(
        "[N10-b] module_param 里写 JSON null（Python 里是 None）",
        [{"varName": "flag", "varValue": None},
         {"varName": "src", "varValue": "$var:item"}],
        expect_blocked=True)

    # N10-c：正确写法（布尔写字符串）—— 不该报错
    results["N10-c 字符串 \"true\" 不应误报"] = check(
        '[N10-c] 正确写法：布尔写成字符串 "true"（项目约定）',
        [{"varName": "flag", "varValue": "true"},
         {"varName": "src", "varValue": "$var:item"}],
        expect_blocked=False)

    # N10-d：数字 → 应为 warn（不会 NameError，只是违反「args 全是字符串」契约）
    results["N10-d 数字 3 应 warn 且有后果说明"] = number_case()
    # N10-e：null 分支的 fix 是否指向「空字符串」而不是照抄 true/false
    results["N10-e null 的 fix 指向空串"] = null_fix_wording()

    print("\n" + "=" * 78)
    print("V9 判定汇总")
    print("=" * 78)
    for k, v in results.items():
        print("  %-40s %s" % (k, "PASS" if v else "FAIL"))
    bad = [k for k, v in results.items() if not v]
    print("\n未通过：%s" % (", ".join(bad) if bad else "无"))
    return 0 if not bad else 1


def number_case():
    """数字 `3` 不该被 err 判死（Python 里合法、不会 NameError），但应 warn 说明后果。"""
    spec = spec_with([{"varName": "flag", "varValue": 3},
                      {"varName": "src", "varValue": "$var:item"}])
    errs, warns = sl.lint(spec, ATOMS)
    rel_e = [e for e in errs if "flag" in e["msg"]]
    rel_w = [w for w in warns if "flag" in w["msg"]]
    print("\n" + "=" * 78)
    print("[N10-d] module_param 里写数字 3（Python 合法，不会 NameError）")
    print("=" * 78)
    print("  lint：错误 %d / 警告 %d（只列与 flag 相关的）" % (len(errs), len(warns)))
    for e in rel_e:
        print("    [E] %s" % e["msg"])
    for w in rel_w:
        print("    [W] %s" % w["msg"])
        if w.get("fix"):
            print("        fix → %s" % w["fix"])
    ok = (not rel_e) and bool(rel_w) and any(
        ("AttributeError" in (w.get("fix") or "") or "字符串" in (w.get("fix") or ""))
        for w in rel_w)
    print("  → 不当成 err、但有 warn 说明后果：%s" % ("PASS" if ok else "FAIL"))
    return ok


def null_fix_wording():
    """`null` 的修复建议应该指向「空字符串」，照抄 true/false 会误导。"""
    spec = spec_with([{"varName": "flag", "varValue": None},
                      {"varName": "src", "varValue": "$var:item"}])
    errs, _w = sl.lint(spec, ATOMS)
    rel = [e for e in errs if "flag" in e["msg"]]
    print("\n" + "=" * 78)
    print("[N10-e] null 的错误文案是否指向正确改法（空字符串）")
    print("=" * 78)
    for e in rel:
        print("    [E] %s" % e["msg"])
        print("        fix → %s" % e.get("fix"))
    msg = " ".join([e["msg"] + " " + (e.get("fix") or "") for e in rel])
    fix_text = " ".join((e.get("fix") or "") for e in rel)
    # 判据要严：null 分支的 **fix** 里必须出现「空字符串」或 '""'，
    # 只写「不能是空值」还不够 —— 业务人员/下一个 AI 要照着 fix 改。
    ok = (bool(rel) and "null" in msg
          and ("空字符串" in fix_text or '""' in fix_text or "空串" in fix_text))
    print("  → 提到 null 且 fix 给出「空字符串」改法：%s" % ("PASS" if ok else "FAIL"))
    if not ok:
        print("     说明：fix 只写了 true/false 的改法，对 null 会把人带错（应写 `\"\"`）")
    return ok


if __name__ == "__main__":
    sys.exit(main())
