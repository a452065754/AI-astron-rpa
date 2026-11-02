# -*- coding: utf-8 -*-
"""V7（附加，非 team-lead 必列项）：两处容易被忽略的链路，离线可测。

A) `flow_generator.build_process_content()` 对 `content: "$script:名字"` 的**透传**行为：
   在 `resolve_scripts()` 之前构建（draft 阶段/离线校验）时，必须原样落字面量，
   不能被当成「未定义变量引用」而抛错，也不能被悄悄改写。
B) `ai_flow._atom_demand_update()` 的幂等规则：同名 + 同条款 → 次数 +1，不新增行；
   换条款 → 新增行。（用临时文件顶替 docs/原子需求池.md，不污染真文件）

用法：C:/Python314/python.exe logs/qa/v7_bonus.py
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import ai_flow as af              # noqa: E402
import flow_generator as fg      # noqa: E402

ATOMS = fg.load_atom_dict(offline=True)
QA_DIR = os.path.dirname(os.path.abspath(__file__))
CODE = ('import os\n\n\ndef main(args):\n    src = args.get("src_file")\n'
        '    return {"ok": True, "groups": 0, "skipped": 0, "files": []}\n')


def spec_with_content(content):
    mp = [{"varName": "src_file", "varValue": "$p:src_file"}]
    return {
        "name": "QA-build-passthrough",
        "params": [{"name": "src_file", "type": "Str", "default": "F:\\qa\\in\\a.xlsx"}],
        "scripts": {"s": {"name": "QA", "why": "命中判定 B-2：x",
                          "maintain_hint": "改参数", "code": CODE}},
        "nodes": [{"atom": "Script.module", "alias": "拆分（自动脚本）",
                   "inputs": {"content": content,
                              "module_param": "$json:" + json.dumps(mp, ensure_ascii=False)},
                   "out": {"program_script": "r"}}],
    }


def content_of(flat):
    """从 build_process_content 的扁平节点里取出 Script.module 的 content 值。"""
    for n in flat:
        for p in (n.get("inputList") or []) if "inputList" in n else []:
            pass
    return flat


def main():
    results = {}
    print("=" * 78)
    print("A) build_process_content 对 $script: 占位是否透传")
    print("=" * 78)
    for label, content in (("$script:s（未解析）", "$script:s"),
                           ("纯数字 moduleId（历史）", "2116342817880481792"),
                           ("$p:module_id（历史）", "$p:module_id")):
        spec = spec_with_content(content)
        if label.startswith("$p:"):
            spec["params"].append({"name": "module_id", "type": "Str",
                                   "default": "2116342817880481792"})
        try:
            flat = fg.build_process_content(spec, ATOMS)
            got = None
            for n in flat:
                # 扁平结构里参数在 inputList / params 字段下，按 key 找 content
                for key in ("inputList", "params", "inputs"):
                    for item in (n.get(key) or []):
                        if isinstance(item, dict) and item.get("key") == "content":
                            got = item.get("value")
            print("\n  %s" % label)
            print("    构建成功：是")
            print("    落库 content = %r" % (got,))
            if content.startswith("$script:"):
                keep = got == [{"type": "other", "value": content}] or got == content or \
                    (isinstance(got, list) and any(isinstance(x, dict) and
                                                   x.get("value") == content for x in got))
                print("    原样保留占位（未被当变量引用、未被改写）：%s"
                      % ("PASS" if keep else "FAIL"))
                results["A-1 $script: 占位透传"] = keep
            else:
                print("    历史写法仍能构建：PASS")
                results["A-2 历史写法（%s）仍能构建" % label] = True
        except Exception as e:                                   # noqa: BLE001
            print("\n  %s" % label)
            print("    ❌ 构建失败：%s: %s" % (type(e).__name__, e))
            results["A-%s 构建通过" % label] = False

    print("\n" + "=" * 78)
    print("B) _atom_demand_update 幂等（临时文件，不碰 docs/原子需求池.md）")
    print("=" * 78)
    tmpdir = tempfile.mkdtemp(prefix="qa_pool_")
    pool = os.path.join(tmpdir, "原子需求池.md")
    old = af.ATOM_POOL
    af.ATOM_POOL = pool
    try:
        # 语义已于 2026-11-02 由用户拍板改过：行按「脚本名 + 条款」唯一，
        # 但「出现次数」= **不同流程名的个数**（不是运行次数）。
        # 所以这里验 4 件事：
        #   1) 同一流程连跑 3 次 → 仍 1 行、次数=1（不是 3）
        #   2) 3 条不同流程都要它 → 仍 1 行、次数=3、流程名用 、 串起来
        #   3) 换条款 → 新增一行
        #   4) 老 6 列格式的行被就地升级为 7 列、次数按「不同流程数」重算
        item = {"name": "QA拆分", "why": "命中判定 B-2：输出个数取决于数据",
                "atom_suggestion": "Excel 按列拆表"}

        def rows_of(p):
            return [ln for ln in open(p, encoding="utf-8").read().splitlines()
                    if ln.startswith("|") and "excel_split" in ln]

        seq = []
        for _ in range(3):
            seq.append(af._atom_demand_update("QA流程", "excel_split", item))
        rows = rows_of(pool)
        print("  同一流程连续 3 次 → 返回 %s" % (seq,))
        print("  表里 excel_split 行数 = %d" % len(rows))
        for r in rows:
            print("    %s" % r)
        idem = len(rows) == 1 and seq[-1][1] == 1
        print("  同一流程跑 3 次只算 1 次（按不同流程名去重）：%s"
              % ("PASS" if idem else "FAIL"))
        results["B-1 幂等（按不同流程名去重）"] = idem

        for flow in ("QA流程B", "QA流程C"):
            c = af._atom_demand_update(flow, "excel_split", item)
        rows3 = rows_of(pool)
        print("\n  再加 2 条不同流程 → 返回 %s；行数 %d" % (c, len(rows3)))
        for r in rows3:
            print("    %s" % r)
        distinct = len(rows3) == 1 and c[1] == 3 and "QA流程B" in rows3[0] and "QA流程C" in rows3[0]
        print("  3 条不同流程 → 1 行、次数=3、流程名串联：%s" % ("PASS" if distinct else "FAIL"))
        results["B-2 不同流程才累计次数"] = distinct

        item2 = dict(item, why="命中判定 B-3：分组聚合原子做不到")
        c2 = af._atom_demand_update("QA流程", "excel_split", item2)
        rows2 = rows_of(pool)
        print("\n  换条款 B-3 → 返回 %s；行数 %d" % (c2, len(rows2)))
        for r in rows2:
            print("    %s" % r)
        newrow = len(rows2) == 2 and c2[0] == "B-3"
        print("  换条款新增一行：%s" % ("PASS" if newrow else "FAIL"))
        results["B-3 换条款算新需求"] = newrow

        # 老 6 列格式升级（历史台账里的写法）
        old_line = "| 2026-10-01 | 老流程A、老流程B | legacy_split | B-2 | Excel 拆表 | 7 |"
        with open(pool, "w", encoding="utf-8") as f:
            f.write(af.ATOM_POOL_HEADER + old_line + "\n")
        c3 = af._atom_demand_update("老流程C", "legacy_split", item)
        rows4 = [ln for ln in open(pool, encoding="utf-8").read().splitlines()
                 if ln.startswith("|") and "legacy_split" in ln]
        print("\n  老 6 列行 + 新流程 → 返回 %s" % (c3,))
        for r in rows4:
            print("    %s" % r)
        upgraded = (len(rows4) == 1 and c3[1] == 3
                    and rows4[0].count("|") == 8          # 7 列 → 8 个竖线
                    and "7" not in rows4[0].split("|")[-2].strip())
        print("  老格式就地升级为 7 列、次数按不同流程重算=3：%s"
              % ("PASS" if upgraded else "FAIL"))
        results["B-4 老 6 列格式自动升级"] = upgraded
    finally:
        af.ATOM_POOL = old
        for f in os.listdir(tmpdir):
            os.remove(os.path.join(tmpdir, f))
        os.rmdir(tmpdir)

    print("\n" + "=" * 78)
    print("V7 判定汇总")
    print("=" * 78)
    for k, v in results.items():
        print("  %-46s %s" % (k, "PASS" if v else "FAIL"))
    bad = [k for k, v in results.items() if not v]
    print("\n未通过：%s" % (", ".join(bad) if bad else "无"))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
