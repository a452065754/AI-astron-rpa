#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V16 门禁：流程参数预算（`paramJson` 长度）必须卡在爆栈线之前。

## 为什么有这个门禁

2026-11-05 正式服的「添加应用」按钮整包报 999999，根因是平台
`SensitiveDataSanitizer` 里第一个正则 `QUOTED_ARRAY_VALUE` 用递归实现匹配
`Executor.paramJson`，递归深度 ≈ 字符数 → 参数一多就 StackOverflowError，
异常冒泡出切面 → 整个账号的定时任务列表变 999999（只有发布者自己能复现）。

结论本身写进文档没用 —— 下一个 AI 不知道，下一个流程照样会踩。
本门禁守四件事：

  A. 长度模型精度：用 `.183` 生产库 MES 的 6 条真实参数回算，必须 ≈ 1037 字符
     （模型在 `scripts/param_budget.py`，`ParamDto` 真实 7 字段）
  B. lint 会拦：12 条参数的规格必须被 `spec_lint` 判 **err**，且提示里指出要砍哪个
  C. lint 不误伤：6 条典型参数必须判绿；8 条短参数必须是红/黄（不能静默放行）
  D. 豁免可控：带 `param_budget_exempt` 时降级为 warn，且 warn 文案必须带「已豁免」
  E. 全库回归：`flows/` 下所有规格 0 个「参数预算」err（现有规格要么合规要么显式豁免）

跑法：`python logs/qa/v16_param_budget.py`   退出码 0 = 通过
"""
import glob
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import flow_generator as fg   # noqa: E402
import param_budget as pb     # noqa: E402
import spec_lint as sl        # noqa: E402

# MES包装标签生成 v3 主流程的 6 条真实参数（2026-11-05 落库后的状态，真实 inner = 1037）
MES_6 = [
    {"varName": "arrival_from", "varType": "Str", "varValue": "", "varDescribe": "纳期起"},
    {"varName": "arrival_to", "varType": "Str", "varValue": "", "varDescribe": "纳期止"},
    {"varName": "dry_run", "varType": "Str", "varValue": "true", "varDescribe": "演练模式"},
    {"varName": "password", "varType": "Str",
     "varValue": '[{"type":"other","value":"XXXXXX"}]', "varDescribe": "MES 密码"},
    {"varName": "sfsc", "varType": "Str",
     "varValue": '[{"type":"other","value":"1"}]', "varDescribe": "生成计划 0/1/-1"},
    {"varName": "username", "varType": "Str",
     "varValue": '[{"type":"other","value":"RPAX"}]', "varDescribe": "MES 账号"},
]


def _spec(n_params, describe="参数"):
    """造一份最小可 lint 的规格：只有参数表，节点用一个必填参数齐全的原子。"""
    params = [{"name": "p%d" % i, "type": "Str", "default": "v%d" % i,
               "describe": describe} for i in range(n_params)]
    return {"name": "参数预算测试", "params": params,
            "nodes": [{"atom": "Excel.open_excel", "alias": "打开",
                       "inputs": {"file_path": "${PROJECT_ROOT}/demo/订单.xlsx"}}]}


def _budget_msgs(spec, atoms):
    errs, warns = sl.lint(spec, atoms)
    return ([e["msg"] + " | " + e["fix"] for e in errs if "paramJson" in e["msg"]],
            [w["msg"] for w in warns if "paramJson" in w["msg"]])


def main():
    atoms = fg.load_atom_dict(offline=True)
    results = []

    # A. 模型精度（生产数据回算）
    span = pb.estimate_span(MES_6)
    results.append(("A 模型精度", span == 1037, "MES 6 条参数估算 %d（真实 1037）" % span))

    # B. 12 条参数必须被拦
    e, w = _budget_msgs(_spec(12), atoms)
    _hint = "".join(e)
    results.append(("B lint 拦截", bool(e) and "占用最大的几个" in _hint,
                    "12 条 → err=%d warn=%d ｜ %s" % (len(e), len(w), (e or ["—"])[0][:90])))

    # C. 6 条必须放行
    e6, w6 = _budget_msgs(_spec(6), atoms)
    results.append(("C 不误伤 6 条", not e6 and not w6, "6 条 → err=%d warn=%d" % (len(e6), len(w6))))

    # C2. 8 条短参数不许静默放行（红或黄都算拦住，只是提示强弱）
    e8, w8 = _budget_msgs(_spec(8), atoms)
    results.append(("C2 8 条有提示", bool(e8 or w8), "8 条 → err=%d warn=%d" % (len(e8), len(w8))))

    # D. 豁免降级为 warn，且文案可见
    ex = _spec(12)
    ex["param_budget_exempt"] = "V16 门禁自测"
    e_ex, w_ex = _budget_msgs(ex, atoms)
    results.append(("D 豁免可控", not e_ex and any("已豁免" in x for x in w_ex),
                    "豁免后 err=%d warn 含『已豁免』=%s"
                    % (len(e_ex), any("已豁免" in x for x in w_ex))))

    # E. 全库回归：现有 flows/ 里不许有「参数预算」err
    bad = []
    total = 0
    for name in sorted(glob.glob(os.path.join(ROOT, "flows", "*.json"))):
        total += 1
        try:
            spec = json.load(io.open(name, encoding="utf-8"))
        except Exception:
            continue
        errs, _warns = sl.lint(spec, atoms)
        if [x for x in errs if "paramJson" in x["msg"]]:
            bad.append(os.path.basename(name))
    results.append(("E 全库无预算 err", not bad, "%d 条规格，超限 %d 条 %s"
                    % (total, len(bad), ("：" + ", ".join(bad[:3])) if bad else "")))

    ok = sum(1 for _, o, _ in results if o)
    print("=" * 78)
    for n, o, d in results:
        print(" %s %-16s %s" % ("✅" if o else "❌", n, d))
    print("=" * 78)
    print("结论：%d/%d 通过" % (ok, len(results)))
    print("=" * 78)
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
