#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""nl2spec 对照基准（bench）

回答一个具体问题：**同一批需求，让模型从零生成，和人工/协作写出来的规格差多远？**

做法：拿已经**人工评审通过**的 14 条流程当标准答案，把需求改写成「用户会怎么说话」，
让 `nl2spec` 从零生成，比对：能不能过 lint（几轮收敛）、选了哪些原子、与标准答案的重合度。

为什么用这 14 条：它们是本项目**唯一被真人逐条点「运行」确认过**的成果，
是现成的 ground truth。别的题（demo 流程）证不了「有没有用」。

用法：

    python scripts/nl2spec_bench.py                  # 全跑一遍
    python scripts/nl2spec_bench.py --only pdf_merge # 只跑一条
    python scripts/nl2spec_bench.py --model xxx      # 换模型对比
    python scripts/nl2spec_bench.py --out docs/x.md  # 指定报告路径

输出：控制台对照表 + `docs/nl2spec-bench-<日期>.md` + 同名 .json（原始数据）。
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import flow_generator as fg          # noqa: E402
import nl2spec as n2                 # noqa: E402
import spec_lint as sl               # noqa: E402

# (id, 用户话术, 参照规格文件)。话术刻意**不提流程名**，只说要做的事。
CASES = [
    ("pdf_merge", "把 F:\\demo\\a.pdf 和 F:\\demo\\b.pdf 合并成一个 PDF，放到 F:\\demo\\out",
     "PDF合并小流程.json"),
    ("word2pdf", "把 F:\\demo\\报表.docx 转成 PDF",
     "Word转PDF小流程.json"),
    ("file_rename", "把 F:\\demo\\in 文件夹里的文件批量重命名成 改名_序号",
     "文件批量重命名小流程.json"),
    ("excel_split", "把 Excel 表格按某一列的值拆成多个文件，每个值一个文件",
     "Excel按条件拆表小流程.json"),
    ("compress", "把 F:\\demo\\in 文件夹压缩成 zip，再解压到 F:\\demo\\out",
     "文件夹压缩解压小流程.json"),
    ("excel_merge", "把 F:\\demo\\in 里的多个 xlsx 合并成一张表",
     "Excel多表合并小流程.json"),
    ("excel_dedup", "把 Excel 表格按某一列去重，保留第一次出现的行",
     "Excel按列去重小流程.json"),
    ("word_text", "把 Word 文档里的文字全部提取出来存成 txt",
     "Word提取文本小流程.json"),
    ("pdf_pages", "把 PDF 的第 1 到 2 页抽出来另存成一个新 PDF",
     "PDF抽取指定页小流程.json"),
    ("excel_stat", "统计 Excel 表格有多少行多少列",
     "Excel统计行列小流程.json"),
    ("excel_replace", "把 Excel 表格里的旧值查找替换成新值",
     "Excel查找替换小流程.json"),
    ("word_replace", "把多个 Word 文档里的旧公司名批量替换成新公司名",
     "Word批量替换小流程.json"),
    ("pdf2excel", "把 PDF 里的表格提取出来转成 Excel",
     "PDF表格转Excel小流程.json"),
    ("archive", "把文件夹里的文件按扩展名分到不同子文件夹",
     "文件按扩展名归档小流程.json"),
]


def atoms_of(spec):
    return [n.get("atom") for n in (spec.get("nodes") or []) if n.get("atom")]


def run_case(case, index, client, retries, verbose=False):
    cid, nl, ref_name = case
    ref_path = os.path.join(ROOT, "flows", ref_name)
    ref = None
    if os.path.exists(ref_path):
        with open(ref_path, "r", encoding="utf-8") as f:
            ref = json.load(f)

    rec = {"id": cid, "nl": nl, "ref": ref_name, "ok": False}
    if ref:
        rec["ref_atoms"] = atoms_of(ref)
        rec["ref_nodes"] = len(ref.get("nodes") or [])
    t0 = time.time()
    try:
        # **刻意不给 few_shot**：标准答案就在 flows/ 里，给了范例等于让模型抄答案，
        # 对照实验就废了。真实使用时 nl2spec 默认会给范例（那是合法收益）。
        spec, hist = n2.generate(nl, index, client, retries=retries,
                                 few_shot=None, verbose=verbose)
        rec.update({
            "ok": True,
            "rounds": len(hist),
            "nodes": len(spec.get("nodes") or []),
            "atoms": atoms_of(spec),
            "name": spec.get("name"),
            "params": [p.get("name") for p in (spec.get("params") or [])],
            "elapsed": round(time.time() - t0, 1),
        })
        # 每轮被 lint 抓到的错误数（看「第一次就有多烂」）
        rec["errors_per_round"] = [len(h.get("errors") or []) for h in hist]
    except Exception as e:
        rec.update({"ok": False, "error": str(e), "elapsed": round(time.time() - t0, 1),
                    "rounds": retries + 1})
        if verbose:
            print("    ❌ %s" % e)

    if ref and rec["ok"]:
        a, b = set(rec["atoms"]), set(rec["ref_atoms"])
        rec["atom_overlap"] = round(len(a & b) / max(1, len(b)), 2)   # 标准答案被覆盖的比例
        rec["atom_extra"] = sorted(a - b)
        rec["atom_missing"] = sorted(b - a)
    # 生成的 spec 一律落盘：报告只能看骨架，判对错要打开看参数、必要时真跑
    if rec["ok"]:
        d = os.path.join(ROOT, "docs", "bench")
        os.makedirs(d, exist_ok=True)
        rec["spec_path"] = os.path.relpath(n2.save_spec(spec, os.path.join(d, cid + ".json")), ROOT)
    return rec


def md_report(results, meta):
    lines = []
    lines.append("# nl2spec 对照基准（%s）" % meta["date"])
    lines.append("")
    lines.append("> 模型 `%s` · 自愈上限 %d 轮 · 原子字典 %d 可用"
                 % (meta["model"], meta["retries"], meta["usable_atoms"]))
    lines.append("> 题目：14 条**已人工评审通过**的流程，把需求改写成用户话术，让模型从零生成。")
    lines.append("> `原子重合` = 生成出的原子覆盖标准答案原子的比例（越高越接近人的做法）。")
    lines.append("")
    lines.append("| # | 需求 | 结果 | 轮数 | 节点（生成/标准） | 原子重合 | 耗时 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for i, r in enumerate(results, 1):
        status = "✅ 通过" if r["ok"] else "❌ 失败"
        nodes = "%s / %s" % (r.get("nodes", "-"), r.get("ref_nodes", "-"))
        ov = ("%.0f%%" % (r["atom_overlap"] * 100)) if "atom_overlap" in r else "-"
        lines.append("| %d | %s | %s | %s | %s | %s | %ss |"
                     % (i, r["nl"][:34], status, r.get("rounds", "-"), nodes, ov,
                        r.get("elapsed", "-")))
    ok = [r for r in results if r["ok"]]
    ovs = [r["atom_overlap"] for r in results if "atom_overlap" in r]
    lines.append("")
    lines.append("## 汇总")
    lines.append("")
    lines.append("- 通过 **%d/%d**" % (len(ok), len(results)))
    if ok:
        dist = {}
        for r in ok:
            dist[r["rounds"]] = dist.get(r["rounds"], 0) + 1
        lines.append("- 收敛轮数：%s" % "、".join("%d 轮 %d 条" % (k, v) for k, v in sorted(dist.items())))
    if ovs:
        lines.append("- 原子重合度：均值 **%.0f%%**（最低 %.0f%%，最高 %.0f%%）"
                     % (sum(ovs) / len(ovs) * 100, min(ovs) * 100, max(ovs) * 100))
    lines.append("")
    lines.append("## 逐条差异（生成缺了什么 / 多做了什么）")
    lines.append("")
    for r in results:
        if not r.get("ok"):
            continue
        lines.append("### %s" % r["nl"])
        lines.append("")
        lines.append("- 标准答案（%d 节点）：`%s`" % (r.get("ref_nodes", 0), " → ".join(r.get("ref_atoms") or [])))
        lines.append("- 生成结果（%d 节点）：`%s`" % (r.get("nodes", 0), " → ".join(r.get("atoms") or [])))
        if r.get("atom_missing"):
            lines.append("- **少了**：%s" % "、".join(r["atom_missing"]))
        if r.get("atom_extra"):
            lines.append("- **多了**：%s" % "、".join(r["atom_extra"]))
        lines.append("")
    lines.append("## 怎么读这份表")
    lines.append("")
    lines.append("- **重合度不是分数**：人会做最优选择（例如「按列拆表」用 Python 模块一步搞定），")
    lines.append("  模型用纯原子拼也能达成同样效果——所以低重合不等于错。要判对错必须真跑 + 核对产出。")
    lines.append("- 真正要盯的是**失败项**和**轮数**：轮数多说明提示词没讲清楚，失败说明能力边界到了。")
    lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="nl2spec 对照基准（14 条已评审流程）")
    ap.add_argument("--only", default="", help="只跑指定 id（逗号分隔）")
    ap.add_argument("--model", default="", help="覆盖模型")
    ap.add_argument("--retries", type=int, default=3, help="自愈轮数（默认 3）")
    ap.add_argument("--out", default="", help="报告输出路径（.md）")
    ap.add_argument("--offline", action="store_true", help="用本地原子缓存")
    ap.add_argument("--verbose", action="store_true", help="打印每轮细节")
    args = ap.parse_args()

    atoms = fg.load_atom_dict(offline=args.offline)
    if not atoms:
        print("❌ 原子字典为空")
        return 2
    index = n2.AtomIndex(atoms)

    cfg, _notes = n2.load_llm_cfg()
    if args.model:
        cfg["model"] = args.model
    if not cfg["api_key"]:
        print("❌ 没有 LLM API key（先跑 nl2spec.py --check）")
        return 2
    client = n2.LlmClient(cfg)

    cases = CASES
    if args.only:
        want = {x.strip() for x in args.only.split(",") if x.strip()}
        cases = [c for c in CASES if c[0] in want]
        if not cases:
            print("❌ 没有匹配的 id。可选：%s" % ", ".join(c[0] for c in CASES))
            return 2

    print("=" * 70)
    print("nl2spec 对照基准 · %d 条（模型 %s，自愈 %d 轮）" % (len(cases), cfg["model"], args.retries))
    print("=" * 70)
    results = []
    for i, case in enumerate(cases, 1):
        print("\n[%d/%d] %s" % (i, len(cases), case[0]))
        print("  需求：%s" % case[1])
        r = run_case(case, index, client, args.retries, verbose=True)
        results.append(r)
        if r["ok"]:
            print("  → ✅ %d 轮 · %d 节点 · 重合 %.0f%% · %.1fs"
                  % (r["rounds"], r["nodes"], r.get("atom_overlap", 0) * 100, r["elapsed"]))
        else:
            print("  → ❌ %s" % r.get("error"))

    date = time.strftime("%Y-%m-%d")
    meta = {"date": date, "model": cfg["model"], "retries": args.retries,
            "usable_atoms": len(index.usable)}
    out = args.out or os.path.join(ROOT, "docs", "nl2spec-bench-%s%s.md"
                                   % (date, "-partial" if args.only else ""))
    md = md_report(results, meta)
    with open(out, "w", encoding="utf-8") as f:
        f.write(md)
    with open(os.path.splitext(out)[0] + ".json", "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "results": results}, f, ensure_ascii=False, indent=1)

    print("\n" + "=" * 70)
    ok = [r for r in results if r["ok"]]
    ovs = [r["atom_overlap"] for r in results if "atom_overlap" in r]
    print("通过 %d/%d" % (len(ok), len(results)))
    if ovs:
        print("原子重合度均值 %.0f%%" % (sum(ovs) / len(ovs) * 100))
    print("报告：%s" % os.path.relpath(out, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
