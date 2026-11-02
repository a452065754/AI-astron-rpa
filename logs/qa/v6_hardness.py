# -*- coding: utf-8 -*-
"""V6：原子优先的「防偷懒」约束到底有多硬。

三个检查点：
  1) `spec_lint` 里 `why` 缺失是否 **err 级**（不是 warn）；
  2) `why` 写了但没写命中条款（B-1~B-4）时是 err 还是 warn —— 这决定了「模型偷懒说一句
     『原子做不到』」能否蒙过去；
  3) `nl2spec.generate()` 的自愈闭环是否只看 errors（warns 不阻断 → 上面那条 warn 就放行了）；
  4) `nl2spec.SYSTEM_PROMPT` 里判定表 A（禁止脚本）与 B（允许脚本 4 条）是否都在、A 是否在前。

用法：C:/Python314/python.exe logs/qa/v6_hardness.py
"""
import inspect
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import flow_generator as fg      # noqa: E402
import nl2spec as n2s            # noqa: E402
import spec_lint as sl           # noqa: E402

ATOMS = fg.load_atom_dict(offline=True)
GOOD_CODE = ('import os\n\n\ndef main(args):\n'
             '    src = args.get("src_file")\n'
             '    return {"ok": True, "groups": 0, "skipped": 0, "files": []}\n')


def spec_with(why, hint):
    item = {"name": "QA", "code": GOOD_CODE}
    if why is not None:
        item["why"] = why
    if hint is not None:
        item["maintain_hint"] = hint
    mp = [{"varName": "src_file", "varValue": "$p:src_file"}]
    return {
        "name": "QA-why-hardness",
        "params": [{"name": "src_file", "type": "Str", "default": "F:\\qa\\in\\a.xlsx"}],
        "scripts": {"s": item},
        "nodes": [{"atom": "Script.module", "alias": "拆分（自动脚本）",
                   "inputs": {"content": "$script:s",
                              "module_param": "$json:" + json.dumps(mp, ensure_ascii=False)},
                   "out": {"program_script": "r"}}],
    }


def classify(errs, warns, kw):
    e = [x for x in errs if kw in x["msg"]]
    w = [x for x in warns if kw in x["msg"]]
    return e, w


def main():
    results = {}
    print("=" * 78)
    print("V6  防偷懒约束硬度检查")
    print("=" * 78)

    # ---------- 0) 源码层面：为什么用 err 而不是 warn（注释是否写清） ----------
    src = inspect.getsource(sl._lint_scripts)
    print("\n--- 0) _lint_scripts 注释是否解释了「为什么 why 是 err」 ---")
    print("  含「## 规则 9（why / maintain_hint）为什么是 err」：%s"
          % ("规则 9（why / maintain_hint）为什么是 err" in src))

    # ---------- 1) why 完全缺失 → 必须 err ----------
    print("\n--- 1) why 缺失（None） ---")
    s = spec_with(why=None, hint="改输出目录 → 改参数 out_dir")
    errs, warns = sl.lint(s, ATOMS)
    e, w = classify(errs, warns, "why")
    print("  errs=%d warns=%d" % (len(errs), len(warns)))
    for x in e:
        print("  [E] %s" % x["msg"])
    for x in w:
        print("  [W] %s" % x["msg"])
    ok1 = len(e) >= 1
    print("  → why 缺失是 err 级：%s" % ("PASS" if ok1 else "FAIL"))
    results["V6-1 why 缺失 = err"] = ok1

    # ---------- 2) why 写了但没写 B-x → err 还是 warn？ ----------
    print("\n--- 2) why 写了但没有命中条款（模型偷懒话术） ---")
    lazy = "原子做不到，这个需求太复杂了，所以用脚本"
    s = spec_with(why=lazy, hint="改输出目录 → 改参数 out_dir")
    errs, warns = sl.lint(s, ATOMS)
    e, w = classify(errs, warns, "why")
    print("  errs=%d warns=%d" % (len(errs), len(warns)))
    for x in e:
        print("  [E] %s" % x["msg"])
    for x in w:
        print("  [W] %s" % x["msg"])
    blocked = len(e) >= 1
    print("  → 偷懒话术被 err 拦住：%s" % ("PASS" if blocked else "FAIL（只有警告）"))
    results["V6-2 why 无 B-x 条款被 err 拦住"] = blocked

    # ---------- 3) 自愈闭环是否只看 errors ----------
    print("\n--- 3) nl2spec.generate() 自愈闭环：warns 是否阻断 ---")
    gsrc = inspect.getsource(n2s.generate)
    gates = []
    for ln in gsrc.splitlines():
        st = ln.strip()
        if ("errors" in st or "warns" in st) and ("if" in st or "append" in st or "=" in st):
            gates.append(st)
    for g in gates[:14]:
        print("    | " + g)
    only_errors = ("if errors" in gsrc or "if errors:" in gsrc or "retries" in gsrc) \
        and "if warns" not in gsrc
    print("  generate() 里没有 `if warns:` 这种阻断分支：%s" % ("PASS" if only_errors else "FAIL"))
    print("  → 结论：warns 只记录不阻断，所以 V6-2 若降级为 warn 就等于放行")
    results["V6-3 warns 不阻断（放大 V6-2 影响）"] = only_errors

    # ---------- 4) SYSTEM_PROMPT 判定表 A/B ----------
    print("\n--- 4) SYSTEM_PROMPT 判定表 A（禁止）/ B（允许 B-1~B-4）---")
    sp = n2s.SYSTEM_PROMPT
    i_a = sp.find("# 一、默认路径：用原子拼")
    i_b = sp.find("# 二、只有命中下列任一条，才允许用脚本")
    i_c = sp.find("# 三、用了脚本才需要读这节")
    print("  A 段位置 = %d ；B 段位置 = %d ；C 段位置 = %d" % (i_a, i_b, i_c))
    a_ok = i_a >= 0 and i_b > i_a
    a_block, b_block = sp[i_a:i_b], sp[i_b:i_c if i_c > i_b else len(sp)]
    for kw in ("禁止用脚本", "能用原子 + 循环/条件块表达，就必须用原子"):
        print("  A 段含「%s」：%s" % (kw, kw in a_block))
    for c in ("B-1", "B-2", "B-3", "B-4"):
        print("  B 段含 %s：%s" % (c, c in b_block))
    print("  prompt 要求 why 写清命中条款：%s" % ("命中判定 B-x" in sp or "B-x" in sp))
    ok4 = (a_ok and "禁止用脚本" in a_block
           and all(c in b_block for c in ("B-1", "B-2", "B-3", "B-4")))
    print("  → A 在前 + B 四条齐全：%s" % ("PASS" if ok4 else "FAIL"))
    results["V6-4 判定表 A 在前 + B-1~B-4 齐全"] = ok4

    print("\n" + "=" * 78)
    print("V6 判定汇总")
    print("=" * 78)
    for k, v in results.items():
        print("  %-40s %s" % (k, "PASS" if v else "FAIL"))
    print("\n注意：V6-2 FAIL 的含义 —— 缺 why 是 err（守住了），但「why 写一句废话、不写 B-x」"
          "\n只报 warn，而 generate() 不拦 warn，所以模型偷懒选脚本仍有一条能过的窄路。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
