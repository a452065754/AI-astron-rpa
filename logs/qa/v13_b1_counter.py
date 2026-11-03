# -*- coding: utf-8 -*-
"""V13：D1「累计状态硬拼」提示的收窄是否准确（待办 11b 的回归门禁）。

背景（2026-11-03 复核结论）：
    `条件分支与While循环小流程`（28 节点 / While + Continue + ForDict）一直被 D1 提示
    「确认是否存在累计状态被硬拼」。人工复核 + 真跑证据（demo/_code_out 四份文件）证明
    它是**正经写法**：计数器由 `DataProcess.set_variable_value` 初始化、
    `MathProcess.self_calculation_number` 每轮自增、`Code.While` 判条件 —— 原子本来就能表达
    标量计数器。所以 D1 收窄为：这套组合齐了就不提示；没齐（用 ForList + 一堆 If 假装计数器）
    仍然提示。

本门禁只读，不落库、不跑流程、不改 flows/：
    C:/Python314/python.exe logs/qa/v13_b1_counter.py

判定：
    F1 真实流程 `条件分支与While循环小流程`            → 0 错 0 警（D1 已豁免）
    F2 真流程去掉「初始化」（只留自增 + While）          → 仍告警
    F3 真流程去掉「自增」（只留初始化 + While）          → 仍告警
    F4 嵌套循环 + 一堆 If 假装计数器（无变量原子）        → 仍告警
    F5 计数器组合齐但节点 < 12                          → 不告警（阈值口径未变）
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import flow_generator as fg          # noqa: E402
import spec_lint as sl               # noqa: E402

ATOMS = fg.load_atom_dict(offline=True)

D1_MARK = "累计状态"


def _params():
    return [
        {"name": "items", "type": "Str", "default": "a,b,c", "describe": "待遍历的数据"},
        {"name": "total", "type": "Str", "default": "3", "describe": "循环轮数"},
    ]


def _forlist(alias, out_item, out_idx):
    return {"atom": "Code.ForList", "alias": alias,
            "inputs": {"list": "$p:items"},
            "out": {"item": out_item, "index": out_idx}}


def _print(alias, msg):
    return {"atom": "Report.print", "alias": alias,
            "inputs": {"report_type": "info", "msg": msg}, "out": {}}


def _if(alias, left, right):
    return {"atom": "Code.If", "alias": alias,
            "inputs": {"args1": left, "condition": "==", "args2": right}, "out": {}}


def _end(alias):
    return {"atom": "Code.ForEnd", "alias": alias, "inputs": {}, "out": {}}


def _ifend(alias="判断结束"):
    return {"atom": "Code.IfEnd", "alias": alias, "inputs": {}, "out": {}}


def _init(alias="计数器初始化"):
    return {"atom": "DataProcess.set_variable_value", "alias": alias,
            "inputs": {"value": "0", "variable_type": "int"},
            "out": {"variable_var": "i"}}


def _step(alias="计数器加1"):
    return {"atom": "MathProcess.self_calculation_number", "alias": alias,
            "inputs": {"number": "$i", "add_sub": "add", "add_sub_number": 1},
            "out": {"self_calculation_number": "i"}}


def _while(alias="未到轮数就继续"):
    return {"atom": "Code.While", "alias": alias,
            "inputs": {"args1": "$i", "condition": "<", "args2": "$p:total"}, "out": {}}


def spec(nodes):
    return {"name": "QA-D1收窄对照", "params": _params(), "nodes": nodes}


def case_hard_spiked():
    """F4：嵌套循环 + 一堆 If 假装计数器，全程没有变量原子。"""
    nodes = [_forlist("外层遍历", "item", "oi"),
             _forlist("内层遍历", "item2", "ii")]
    for k in range(1, 4):
        nodes += [_if("假装第%d轮" % k, "$ii", str(k)),
                  _print("打印%d" % k, "第 %d 轮" % k),
                  _ifend()]
    nodes += [_print("收尾打印1", "done1"),
              _print("收尾打印2", "done2"),
              _print("收尾打印3", "done3"),
              _end("内层结束"), _end("外层结束")]
    return spec(nodes)


def case_no_init():
    """F2：有自增 + While，但没初始化（i 由自增原子自己产出）。"""
    nodes = [_forlist("遍历", "item", "idx"),
             {"atom": "MathProcess.self_calculation_number", "alias": "直接自增",
              "inputs": {"number": "1", "add_sub": "add", "add_sub_number": 1},
              "out": {"self_calculation_number": "i"}},
             _while(),
             _print("轮次", ["第 ", "$i", " 轮"]),
             _end("While结束")]
    nodes += [_print("填充%d" % k, "填 %d" % k) for k in range(1, 7)]
    nodes += [_end("外层结束")]
    return spec(nodes)


def case_no_step():
    """F3：有初始化 + While，但没自增（永远 0 < total，会死循环）。"""
    nodes = [_forlist("遍历", "item", "idx"),
             _init(),
             _while(),
             _print("轮次", ["第 ", "$i", " 轮"]),
             _end("While结束")]
    nodes += [_print("填充%d" % k, "填 %d" % k) for k in range(1, 7)]
    nodes += [_end("外层结束")]
    return spec(nodes)


def case_short_counter():
    """F5：计数器组合齐，但只有 7 个节点（阈值 12 未触发）。"""
    nodes = [_forlist("遍历", "item", "idx"),
             _init(), _while(), _step(),
             _print("轮次", ["第 ", "$i", " 轮"]),
             _end("While结束"), _end("外层结束")]
    return spec(nodes)


def d1_hits(errs, warns):
    return [w for w in warns if D1_MARK in (w.get("msg") or "")]


def run(name, spec_obj, expect_d1, note):
    errs, warns = sl.lint(spec_obj, ATOMS)
    hits = d1_hits(errs, warns)
    got = bool(hits)
    ok = (got == expect_d1) and not errs
    print("\n%s %s" % ("[PASS]" if ok else "[FAIL]", name))
    print("     %s" % note)
    print("     节点 %d 个 · 错 %d / 警 %d · D1 命中 %s（预期 %s）"
          % (len(spec_obj["nodes"]), len(errs), len(warns),
             "是" if got else "否", "是" if expect_d1 else "否"))
    for e in errs:
        print("       [E] %s" % e["msg"])
    for w in warns:
        print("       [W] %s" % w["msg"])
    return ok


def main():
    print("=" * 78)
    print("V13 · D1（累计状态硬拼）提示收窄回归")
    print("=" * 78)

    results = []

    real = os.path.join(ROOT, "flows", "条件分支与While循环小流程.json")
    with open(real, encoding="utf-8") as f:
        real_spec = fg.load_spec(real) if hasattr(fg, "load_spec") else json.load(f)
    results.append(run("F1 真实流程（计数器正经写法）", real_spec, False,
                       "条件分支与While循环小流程：init + step + While 齐 → D1 应豁免"))

    results.append(run("F2 缺初始化（只留自增 + While）", case_no_init(), True,
                       "没 set_variable_value → 计数器来源不明 → 仍提示"))
    results.append(run("F3 缺自增（只留初始化 + While）", case_no_step(), True,
                       "没 self_calculation_number → 疑似死循环式硬拼 → 仍提示"))
    results.append(run("F4 嵌套循环 + 一堆 If 假装计数器", case_hard_spiked(), True,
                       "没有任何变量原子 → 正是 D1 要抓的形态 → 仍提示"))
    results.append(run("F5 组合齐但节点 < 12", case_short_counter(), False,
                       "阈值口径未变：节点数不够就不提示"))

    print("\n" + "=" * 78)
    print("合计：%d/%d 通过" % (sum(results), len(results)))
    print("=" * 78)
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
