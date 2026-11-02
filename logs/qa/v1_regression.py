# -*- coding: utf-8 -*-
"""V1 回归基线：遍历 flows/*.json 跑 spec_lint.lint，统计错误/警告并逐条归类。

只读，不落库、不改 flows/。用法：
    C:/Python314/python.exe logs/qa/v1_regression.py

输出：
  1) flows 总数、总错误、总警告
  2) 含 Script.module 的流程逐条：content 形态（纯数字 moduleId / $p: / $script:）与错/警数
  3) 所有警告全文（msg + fix），供人工判断是否为本次改动引入的噪音
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import flow_generator as fg          # noqa: E402
import spec_lint as sl               # noqa: E402


def content_kind(v):
    """判断 Script.module 的 content 属于哪种历史/新写法。"""
    if not isinstance(v, str):
        return "非字符串:%r" % (v,)
    if v.startswith(fg.SCRIPT_REF_PREFIX):
        return "$script:（新内嵌脚本）"
    if v.isdigit():
        return "纯数字 moduleId（历史写法）"
    if v.startswith("$p:"):
        return "$p: 参数引用（历史写法）"
    if v.startswith("$"):
        return "$ 变量引用（历史写法）"
    return "字面量:%s" % v[:20]


def main():
    atoms = fg.load_atom_dict(offline=True)
    flows_dir = os.path.join(ROOT, "flows")
    rows = []
    for name in sorted(os.listdir(flows_dir)):
        if not name.endswith(".json"):
            continue
        p = os.path.join(flows_dir, name)
        try:
            with open(p, encoding="utf-8") as f:
                spec = json.load(f)
        except Exception as e:                                   # noqa: BLE001
            rows.append({"file": name, "fatal": "JSON 解析失败: %s" % e})
            continue
        errs, warns = sl.lint(spec, atoms)
        sm = []
        for i, n in enumerate(spec.get("nodes") or [], 1):
            if isinstance(n, dict) and (n.get("atom") or "") == "Script.module":
                sm.append((i, n.get("alias") or "", content_kind((n.get("inputs") or {}).get("content"))))
        rows.append({"file": name, "errors": errs, "warnings": warns, "script_nodes": sm,
                     "scripts_field": sorted((spec.get("scripts") or {}).keys())})

    tot_e = sum(len(r.get("errors") or []) for r in rows)
    tot_w = sum(len(r.get("warnings") or []) for r in rows)
    fatal = [r for r in rows if r.get("fatal")]

    print("=" * 78)
    print("V1 回归基线")
    print("=" * 78)
    print("flows 总数            : %d" % len(rows))
    print("JSON 解析失败         : %d" % len(fatal))
    print("总错误 err            : %d" % tot_e)
    print("总警告 warn           : %d" % tot_w)
    print("0 错误流程数          : %d" % sum(1 for r in rows if not (r.get("errors") or [])))
    print("0 错误 0 警告流程数   : %d" % sum(1 for r in rows
                                          if not (r.get("errors") or []) and not (r.get("warnings") or [])))

    sm_rows = [r for r in rows if r.get("script_nodes")]
    print("\n含 Script.module 的流程 : %d 条" % len(sm_rows))
    legacy_digit = 0
    for r in sm_rows:
        kinds = sorted({k for _i, _a, k in r["script_nodes"]})
        if any("纯数字" in k for k in kinds):
            legacy_digit += 1
        print("  %-38s 错 %d / 警 %d  节点%d 个  content=%s"
              % (r["file"], len(r.get("errors") or []), len(r.get("warnings") or []),
                 len(r["script_nodes"]), " + ".join(kinds)))
        for i, a, k in r["script_nodes"]:
            print("        #%d %-28s %s" % (i, (a or "-")[:28], k))
    print("\n含「纯数字 moduleId 历史写法」的流程：%d 条（要求：必须全部继续通过）"
          % legacy_digit)

    # ---------- 错误明细 ----------
    print("\n" + "-" * 78)
    print("错误明细（按流程）")
    print("-" * 78)
    any_err = False
    for r in rows:
        for e in r.get("errors") or []:
            any_err = True
            print("[E] %-30s %s" % (r["file"], e["msg"]))
            if e.get("fix"):
                print("      修法：%s" % e["fix"])
    if not any_err:
        print("（无错误）")

    # ---------- 警告明细 ----------
    print("\n" + "-" * 78)
    print("警告明细（全文，供人工判断是否为本次改动引入的噪音）")
    print("-" * 78)
    any_w = False
    for r in rows:
        for w in r.get("warnings") or []:
            any_w = True
            print("[W] %-30s %s" % (r["file"], w["msg"]))
            if w.get("fix"):
                print("      提示：%s" % w["fix"])
    if not any_w:
        print("（无警告）")

    # ---------- 警告归类 ----------
    buckets = {}
    for r in rows:
        for w in r.get("warnings") or []:
            m = w["msg"]
            if m.startswith("scripts."):
                k = "scripts 定义了没人用"
            elif "out 里没有 program_script" in m:
                k = "out 缺 program_script（新规则 7）"
            elif "module_param" in m:
                k = "module_param 相关（新规则）"
            elif "脚本" in m and "why" in m:
                k = "why 未写 B-x（新规则 9）"
            elif "元素" in m:
                k = "元素声明"
            else:
                k = "其他"
            buckets.setdefault(k, []).append((r["file"], m))
    print("\n警告归类：")
    for k, v in sorted(buckets.items(), key=lambda x: -len(x[1])):
        print("  %-34s %d 条" % (k, len(v)))
    if not buckets:
        print("  （无）")

    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "v1_regression.json"),
              "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print("\n明细已写出：logs/qa/v1_regression.json")
    return 0 if tot_e == 0 and not fatal else 1


if __name__ == "__main__":
    sys.exit(main())
