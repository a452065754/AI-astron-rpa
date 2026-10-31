# -*- coding: utf-8 -*-
"""自测：一句话 → 参数。只测抽取层，不落库不执行。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ROOT = os.path.dirname(HERE)

import flow_generator as fg
import nl_params as nlp

CASES = [
    ("把 D:\\单子\\报废单.pdf 还原版面", None),
    ("把 D:\\单子\\报废单.pdf 还原版面，另存成 最终版", None),
    ("把 F:/AI-Workbench/10-Projects/星辰开源/demo/20260914报废_已转正.pdf 转成可编辑word", None),
    ("只保留第 2 到 4 页", None),
    ("把 F:\\demo\\in 里的 pdf 合成一个，输出到 F:\\demo\\out", None),
    ("把这个文件夹的 PDF 都转成 Word", None),
    ("放大 30% 再转", None),
    ("帮我把这个 PDF 拆成两半", None),
    ("订单.xlsx 导出成新表", None),
]


def pick(nl):
    path, score, why = fg.match_spec_file(nl)
    return path, score, why


def run():
    for nl, _ in CASES:
        print("=" * 70)
        print(f"NL: {nl}")
        path, score, why = pick(nl)
        if not path:
            tpl = fg.intent_match(nl)
            print(f"  规格: ❌ 未命中（{why}）  模板兜底={tpl or '无 → 拒绝'}")
            continue
        spec = fg.load_spec(path)
        print(f"  规格: {os.path.basename(path)}  匹配度 {score:.2f}  ({why})")
        rb = nlp.rebase_paths(spec, ROOT)
        for line in nlp.format_rebase(rb):
            print(line)
        nr = nlp.apply(nl, spec)
        if nr["applied"]:
            for k, d in nr["applied"].items():
                print(f"    · {k}: {d['old'] or '(空)'} → {d['new']}  （{d['why']}）")
        else:
            print("    · 没抽到参数，全用默认值")
        for w in nr["warnings"]:
            print(f"    ⚠️ {w}")
        for s in nr["skipped"]:
            print(f"    · 跳过：{s}")


if __name__ == "__main__":
    run()
