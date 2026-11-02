# -*- coding: utf-8 -*-
"""V4：`--strategy {auto,atom,script}` 的**静态**行为验证（不发任何网络请求、不调 LLM）。

验证三件事：
  1) 三个取值都被 argparse 接受（跑 --help 与 choices 检查，不进入生成流程）；
  2) `build_messages(...)` 在三种策略下产出的 system prompt 确实不同；
  3) `atom` 策略下确实含「禁止使用脚本」的强约束（且 auto 不含该强约束）。

用法：C:/Python314/python.exe logs/qa/v4_strategy.py
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import flow_generator as fg      # noqa: E402
import nl2spec as n2s            # noqa: E402

NL = "把 F:\\qa\\in\\订单.xlsx 按部门列拆成多个 xlsx 文件，放到 F:\\qa\\out"


def ok(cond):
    return "PASS" if cond else "FAIL"


def main():
    results = {}

    # ---------- 1) argparse 是否接受三个取值（真跑进程，--help 走 argparse 但不生成） ----------
    print("=" * 78)
    print("V4-1 argparse 接受 auto / atom / script")
    print("=" * 78)
    ap_src = None
    for a in n2s.main.__code__.co_consts:
        pass
    # 直接跑 --help，抓 choices 说明
    r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "nl2spec.py"), "--help"],
                       capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
    helptext = (r.stdout or "") + (r.stderr or "")
    line = [ln for ln in helptext.splitlines() if "--strategy" in ln]
    print("退出码：%d" % r.returncode)
    print("--strategy 帮助行：")
    for ln in line:
        print("   " + ln.strip())
    nextline = ""
    if line:
        idx = helptext.splitlines().index(line[0])
        nextline = helptext.splitlines()[idx + 1] if idx + 1 < len(helptext.splitlines()) else ""
        print("   " + nextline.strip())
    accepts = all(x in line[0] + nextline for x in ("auto", "atom", "script"))
    print("  三个取值都出现在 choices 里：%s" % ok(accepts))
    results["V4-1 argparse accepts 3 values"] = accepts

    # ---------- 2) build_messages 三种策略产出的 system prompt 是否不同 ----------
    print("\n" + "=" * 78)
    print("V4-2 build_messages() 三种策略的 system prompt 差异")
    print("=" * 78)
    atoms = fg.load_atom_dict(offline=True)
    index = n2s.AtomIndex(atoms)
    msgs = {s: n2s.build_messages(NL, index, few_shot=None, strategy=s)
            for s in ("auto", "atom", "script")}
    sys_prompt = {s: m[0]["content"] for s, m in msgs.items()}
    print("messages 结构：%d 条（role=%s）"
          % (len(msgs["auto"]), [m["role"] for m in msgs["auto"]]))
    for s in ("auto", "atom", "script"):
        print("  %-7s system prompt 长度 = %6d 字符 ；user 长度 = %6d 字符"
              % (s, len(sys_prompt[s]), len(msgs[s][1]["content"])))
    diff_aa = sys_prompt["auto"] != sys_prompt["atom"]
    diff_as = sys_prompt["auto"] != sys_prompt["script"]
    diff_ts = sys_prompt["atom"] != sys_prompt["script"]
    print("  auto != atom : %s" % ok(diff_aa))
    print("  auto != script: %s" % ok(diff_as))
    print("  atom != script: %s" % ok(diff_ts))
    # user prompt 应完全一致（策略只影响 system）
    same_user = len({msgs[s][1]["content"] for s in msgs}) == 1
    print("  三种策略的 user prompt 完全一致（策略只作用于 system）：%s" % ok(same_user))
    results["V4-2 prompts differ across strategies"] = diff_aa and diff_as and diff_ts and same_user

    # ---------- 3) atom 策略下的强约束 ----------
    print("\n" + "=" * 78)
    print("V4-3 atom 策略下 prompt 的「禁止脚本」强约束")
    print("=" * 78)
    atom_p = sys_prompt["atom"]
    auto_p = sys_prompt["auto"]
    checks = {
        "atom 含「本次强制策略：只用原子」": "【本次强制策略：只用原子" in atom_p,
        "atom 含「禁止使用 Script.module」": "禁止" in atom_p and "Script.module" in atom_p,
        "atom 含 impossible 逃生口": "impossible" in atom_p,
        "atom 强制块确实追加在 SYSTEM_PROMPT 之后": atom_p.startswith(auto_p),
        "auto 不含该强制块": "【本次强制策略：只用原子" not in auto_p,
    }
    for k, v in checks.items():
        print("  %-44s %s" % (k, ok(v)))
    # 把 atom 独有的新增文本打出来（人可复核）
    added = atom_p[len(auto_p):]
    print("\n  atom 相对 auto 追加的文本（共 %d 字）：" % len(added))
    for ln in added.splitlines():
        if ln.strip():
            print("    | " + ln)
    results["V4-3 atom forbids script"] = all(checks.values())

    # ---------- 4) SYSTEM_PROMPT 里判定表 A/B 顺序 ----------
    print("\n" + "=" * 78)
    print("V4-4 SYSTEM_PROMPT 判定表 A（禁止脚本）与 B（允许脚本）的顺序")
    print("=" * 78)
    sp = n2s.SYSTEM_PROMPT
    i_a = sp.find("# 一、默认路径：用原子拼")
    i_b = sp.find("# 二、只有命中下列任一条，才允许用脚本")
    print("  「一、默认路径：用原子拼」位置        = %d" % i_a)
    print("  「二、只有命中……才允许用脚本」位置    = %d" % i_b)
    print("  A 在 B 之前：%s" % ok(0 <= i_a < i_b))
    a_block = sp[i_a:i_b]
    b_block = sp[i_b:sp.find("# 三、")]
    print("  A 段是否含「禁止用脚本」：%s" % ok("禁止用脚本" in a_block))
    for clause in ("B-1", "B-2", "B-3", "B-4"):
        print("  B 段含 %s：%s" % (clause, ok(clause in b_block)))
    has_default_rule = "默认必须用原子拼" in sp
    print("  prompt 第 11 条「默认必须用原子拼」：%s" % ok(has_default_rule))
    results["V4-4 A before B in SYSTEM_PROMPT"] = (
        0 <= i_a < i_b and "禁止用脚本" in a_block
        and all(c in b_block for c in ("B-1", "B-2", "B-3", "B-4")))

    # ---------- 汇总 ----------
    print("\n" + "=" * 78)
    print("V4 判定汇总")
    print("=" * 78)
    for k, v in results.items():
        print("  %-40s %s" % (k, ok(v)))
    bad = [k for k, v in results.items() if not v]
    print("\n未通过：%s" % (", ".join(bad) if bad else "无"))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
