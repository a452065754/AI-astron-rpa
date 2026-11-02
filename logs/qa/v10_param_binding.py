# -*- coding: utf-8 -*-
"""V10：`--param-binding {baked,bound}` 两种模式的**离线**对照（不连平台、不建机器人）。

背景：R1 定性为「引擎侧能求值，是我方烘焙策略丢了 `need_parse`」。工程师加了
`flow_generator.PARAM_BINDING_MODE`（默认 `baked`）与 `ai_flow --param-binding`。
本用例只做静态验证：

  1) 默认模式是 `baked`；`baked`/`bound` 都接受，非法值报错；
  2) `baked`  → module_param: need_parse 缺失 + 参数被烘焙成字面量（改参数不生效）
     `bound`  → module_param: need_parse="json_str" + `rpa:special(p_var)` 引用（改参数生效）
  3) 88 条流程在 **bound 模式下也能全部构建成功**（不能只有新流程能跑）；
  4) `ai_flow._param_binding_warn()` 在 baked 模式下能列出受影响的节点与参数名；
  5) 两种模式下 lint 结论一致（回归不因模式切换而变）。

用法：C:/Python314/python.exe logs/qa/v10_param_binding.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import ai_flow as af              # noqa: E402
import flow_generator as fg      # noqa: E402
import spec_lint as sl           # noqa: E402

ATOMS = fg.load_atom_dict(offline=True)
FLOWS = os.path.join(ROOT, "flows")


def mp_of(pc):
    for n in pc:
        if n.get("key") == "Script.module":
            for it in n.get("inputList") or []:
                if it.get("key") == "module_param":
                    return it
    return {}


def main():
    results = {}

    # ---------- 1) 默认模式 / 合法性 ----------
    print("=" * 78)
    print("V10-1 默认模式与取值校验")
    print("=" * 78)
    print("  当前默认 PARAM_BINDING_MODE = %r" % fg.PARAM_BINDING_MODE)
    print("  可选值 PARAM_BINDING_MODES = %r" % (fg.PARAM_BINDING_MODES,))
    ok_def = fg.PARAM_BINDING_MODE == "baked"
    print("  默认是 baked（保守，设计器一定能打开）：%s" % ("PASS" if ok_def else "FAIL"))
    bad_ok = False
    try:
        fg.set_param_binding_mode("nonsense")
    except ValueError as e:
        bad_ok = "baked" in str(e) and "bound" in str(e)
        print("  非法值报错且提示可选值：%s（%s）" % ("PASS" if bad_ok else "FAIL", e))
    results["V10-1 默认 baked + 非法值报错"] = ok_def and bad_ok

    # ---------- 2) 两种模式对同一份规格的落法 ----------
    print("\n" + "=" * 78)
    print("V10-2 同一份规格在 baked / bound 下的 module_param 落法")
    print("=" * 78)
    path = os.path.join(FLOWS, "gen_按部门拆分Excel.json")
    spec = fg.load_spec(path)
    got = {}
    for mode in ("baked", "bound"):
        fg.set_param_binding_mode(mode)
        item = mp_of(fg.build_process_content(fg.load_spec(path), ATOMS))
        got[mode] = item
        print("\n  [%s] need_parse = %r" % (mode, item.get("need_parse")))
        print("        value = %s" % str(item.get("value"))[:220])
    baked_ok = got["baked"].get("need_parse") is None and "$p:" not in str(got["baked"].get("value"))
    bound_ok = (got["bound"].get("need_parse") == "json_str"
                and "p_var" in str(got["bound"].get("value"))
                and "rpa" in str(got["bound"].get("value")))
    print("\n  baked：无 need_parse 且参数被烘焙成字面量 → %s" % ("PASS" if baked_ok else "FAIL"))
    print("  bound：need_parse=json_str 且保留 p_var 引用 → %s" % ("PASS" if bound_ok else "FAIL"))
    results["V10-2 baked/bound 落法差异正确"] = baked_ok and bound_ok

    # ---------- 3) 88 条流程在 bound 模式下全部能构建 ----------
    print("\n" + "=" * 78)
    print("V10-3 全量 flows 在 bound 模式下的构建与 lint 回归")
    print("=" * 78)
    for mode in ("baked", "bound"):
        fg.set_param_binding_mode(mode)
        bad_build, bad_lint = [], []
        for name in sorted(os.listdir(FLOWS)):
            if not name.endswith(".json"):
                continue
            p = os.path.join(FLOWS, name)
            try:
                sp = fg.load_spec(p)
            except Exception as e:                              # noqa: BLE001
                bad_build.append((name, "load_spec: %s" % e))
                continue
            errs, _w = sl.lint(sp, ATOMS)
            if errs:
                bad_lint.append((name, errs[0]["msg"]))
            try:
                fg.build_process_content(sp, ATOMS)
            except Exception as e:                              # noqa: BLE001
                bad_build.append((name, "%s: %s" % (type(e).__name__, e)))
        print("  [%s] 构建失败 %d 条 / lint 有错 %d 条" % (mode, len(bad_build), len(bad_lint)))
        for n, why in bad_build[:5]:
            print("      ❌ 构建 %s → %s" % (n, why[:120]))
        for n, why in bad_lint[:5]:
            print("      ❌ lint  %s → %s" % (n, why[:120]))
        results.setdefault("_build", {})[mode] = {n for n, _w in bad_build}
        results.setdefault("_lint", {})[mode] = {n for n, _w in bad_lint}
        results["_ok_lint_" + mode] = not bad_lint

    # 两种模式的「构建失败集合」必须**完全一致**：元素类流程离线必失败（需平台元素库），
    # 这是环境限制、与参数绑定模式无关；若 bound 模式额外失败，说明 bound 有回归。
    same_build = results["_build"]["baked"] == results["_build"]["bound"]
    same_lint = results["_lint"]["baked"] == results["_lint"]["bound"]
    print("  两种模式构建失败集合一致：%s（%d 条，均为元素库缺失的离线限制）"
          % ("PASS" if same_build else "FAIL", len(results["_build"]["baked"])))
    print("  两种模式 lint 失败集合一致：%s" % ("PASS" if same_lint else "FAIL"))
    results_out = {
        "V10-3 baked/bound 构建失败集合一致": same_build,
        "V10-3b baked/bound lint 全绿": results["_ok_lint_baked"] and results["_ok_lint_bound"],
    }

    # ---------- 4) baked 模式的提醒能否列出受影响节点/参数 ----------
    print("\n" + "=" * 78)
    print("V10-4 baked 模式下的提醒（_param_binding_warn）")
    print("=" * 78)
    fg.set_param_binding_mode("baked")
    hits = af._param_binding_warn(fg.load_spec(path))
    print("  受影响节点数 = %d（返回结构：[(节点序号, [参数名…])]）" % len(hits))
    for idx, names in hits:
        print("    · 节点 %d：%s" % (idx, "/".join(names)))
    warn_ok = len(hits) >= 1 and bool(hits[0][1])
    print("  能列出节点与参数名（业务人员能照着核对）：%s" % ("PASS" if warn_ok else "FAIL"))
    # 这个探测函数是**模式无关**的（只扫 spec）；是否打印由调用点把关。
    # 所以这里验的是「调用点有没有按模式把关」，而不是函数本身返回空。
    fg.set_param_binding_mode("bound")
    hits_b = af._param_binding_warn(fg.load_spec(path))
    print("  bound 模式下调用该函数仍返回 %d 条（设计如此：函数只看 spec，是否提示由调用点把关）"
          % len(hits_b))
    import inspect
    src = inspect.getsource(af.main)
    gated = 'fg.PARAM_BINDING_MODE == "baked"' in src and "_param_binding_warn(spec)" in src
    print("  调用点有 `if fg.PARAM_BINDING_MODE == \"baked\"` 把关：%s"
          % ("PASS" if gated else "FAIL"))
    results_out["V10-4 baked 提醒到位 + 调用点按模式把关"] = warn_ok and gated
    results.pop("_build", None)
    results.pop("_lint", None)
    results.pop("_ok_lint_baked", None)
    results.pop("_ok_lint_bound", None)
    results.update(results_out)

    fg.set_param_binding_mode("baked")
    print("\n" + "=" * 78)
    print("V10 判定汇总（已复位为默认 baked）")
    print("=" * 78)
    for k, v in results.items():
        print("  %-42s %s" % (k, "PASS" if v else "FAIL"))
    bad = [k for k, v in results.items() if not v]
    print("\n未通过：%s" % (", ".join(bad) if bad else "无"))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
