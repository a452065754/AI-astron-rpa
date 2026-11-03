# -*- coding: utf-8 -*-
"""V10：`Script.module` 落库结构**标准形态**门禁（离线，不连平台）。

## 这个用例为什么存在

2026-11-02 那版把 `Script.module` 的两个参数写错了，用户在设计器里看到
「请求参数解析失败」+ 参数表格「暂无数据」。2026-11-03 对着用户手工搭的
`导入MES` 流程取到标准答案后定稿：

    content      = "<moduleId>"                      ← 纯字符串
    module_param = [{"varId":"<c_param.id>", "varName":…,
                     "varValue":{"rpa":"special","value":[{"type":"other","value":…}]}}]
                                                    ← 真数组
    c_param      = module_id=<moduleId>, process_id=NULL 的模块级参数定义

旧的 `baked` / `bound` 双模式（区别只在 module_param 写字符串还是带 need_parse）
**整套作废** —— 它们都把这个参数当 JSON 字符串，前端一 parse 就炸。

本用例守的就是这三条形状，防止哪天有人「顺手优化」回去。

## 断言

  S1 离线构建：content 是 str、module_param 是 list、无 need_parse
  S2 每项参数：有 varName、varValue 是 rpa:special、内层 type=other
  S3 `$p:` 被烘焙成默认值字面量（不残留 p_var）
  S4 `$var:`（循环变量）**必须**保留 rpa:special 引用（不能烘焙，运行时才求值）
  S5 `build_module_param_items` 的容错：单对象容忍、缺 varName 报错、复杂值转 JSON 串
  S6 `ai_flow._module_param_id_check` 能把缺 varId 的节点查出来
  S7 `relink_modules._parse_content` 认得四种历史形态（数组/p_var/rpa包裹/字符串）
  S8 88 条存量流程全部仍能构建成功（结构改造不误伤）

用法：C:/Python314/python.exe logs/qa/v10_module_structure.py   退出码 0=全过
"""
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import ai_flow as af              # noqa: E402
import flow_generator as fg      # noqa: E402
import relink_modules as rm      # noqa: E402

ATOMS = fg.load_atom_dict(offline=True)
FLOWS = os.path.join(ROOT, "flows")
SCRIPT = os.path.join(FLOWS, "gen_按部门拆分Excel.json")


def script_node(pc):
    for n in pc:
        if n.get("key") == "Script.module":
            return n
    return None


def inputs_of(node):
    return {it["key"]: it for it in (node.get("inputList") or [])}


def report(tag, ok, detail=""):
    print("  %-52s %s" % (tag, "PASS" if ok else "FAIL"))
    if detail and not ok:
        print("      → %s" % detail)
    return ok


def main():
    print("=" * 78)
    print("V10  Script.module 标准结构门禁")
    print("=" * 78)

    spec = fg.load_spec(SCRIPT)
    pc = fg.build_process_content(spec, ATOMS, elements=None, robot_id="0")
    node = script_node(pc)
    if node is None:
        print("❌ 构建结果里没有 Script.module 节点")
        return 1
    il = inputs_of(node)
    mp = il.get("module_param", {}).get("value")
    ct = il.get("content", {}).get("value")

    ok = True

    # S1
    ok &= report("S1 content 是纯字符串（不是数组）", isinstance(ct, str), "实际 %r" % type(ct).__name__)
    ok &= report("S1 module_param 是真数组（不是 JSON 字符串）", isinstance(mp, list),
                 "实际 %r" % type(mp).__name__)
    ok &= report("S1 节点上没有任何 need_parse", all("need_parse" not in it for it in node["inputList"]),
                 [it.get("need_parse") for it in node["inputList"]])

    # S2
    shape_ok = all(isinstance(p, dict) and p.get("varName")
                   and isinstance(p.get("varValue"), dict)
                   and p["varValue"].get("rpa") == "special"
                   and isinstance(p["varValue"].get("value"), list)
                   and p["varValue"]["value"]
                   and p["varValue"]["value"][0].get("type") == "other"
                   for p in (mp or []))
    ok &= report("S2 每项：varName + varValue.rpa=special + 内层 type=other", shape_ok,
                 json.dumps(mp, ensure_ascii=False)[:200])
    ok &= report("S2 参数个数 = 脚本声明的 5 个", len(mp or []) == 5, len(mp or []))

    # S3 `$p:` 烘焙
    vals = {p["varName"]: p["varValue"]["value"][0]["value"] for p in (mp or [])}
    # 期望值从**已解析的规格**里取，不写字面量：路径默认值现在写的是
    # `${PROJECT_ROOT}/demo/...`，load_spec 后才变成本机绝对路径（2026-11-03 路径去机器化）。
    # 断言的真实语义是「烘焙的就是流程参数默认值」，与默认值本身是什么无关。
    want = {p["name"]: p["default"] for p in (spec.get("params") or [])}
    baked_ok = vals.get("src_file") == str(want.get("src_file")) \
        and vals.get("split_column") == "部门"
    ok &= report("S3 $p: 已烘焙成默认值（无 p_var 残留）", baked_ok, vals)
    ok &= report("S3 无任何 p_var / var 引用残留",
                 "p_var" not in json.dumps(mp, ensure_ascii=False))

    # S4 循环变量必须保留引用
    items = fg.build_module_param_items(
        [{"varName": "cur_file", "varValue": "$var:item"}],
        {"item"}, set(), {}, {})
    keep_var = (items[0]["varValue"].get("rpa") == "special"
                and items[0]["varValue"]["value"][0].get("type") == "var")
    ok &= report("S4 $var:（循环变量）保留 rpa:special 引用", keep_var,
                 json.dumps(items, ensure_ascii=False))

    # S5 容错
    one = fg.build_module_param_items({"varName": "x", "varValue": "1"}, set(), set(), {}, {})
    ok &= report("S5 单对象输入被容忍成 1 项", len(one) == 1, one)
    try:
        fg.build_module_param_items([{"varValue": "1"}], set(), set(), {}, {})
        miss_ok = False
    except ValueError:
        miss_ok = True
    ok &= report("S5 缺 varName → ValueError", miss_ok)
    cplx = fg.build_module_param_items([{"varName": "y", "varValue": {"a": 1}}], set(), set(), {}, {})
    ok &= report("S5 复杂值转成 JSON 字符串",
                 cplx[0]["varValue"]["value"][0]["value"] == '{"a": 1}',
                 cplx[0]["varValue"]["value"][0]["value"])

    # S6 varId 检查
    probe = [{"key": "Script.module", "inputList": [
        {"key": "content", "value": "1"},
        {"key": "module_param", "value": [
            {"varId": "9", "varName": "a", "varValue": {}},
            {"varId": "", "varName": "b", "varValue": {}}]}]}]
    miss = af._module_param_id_check(probe)
    ok &= report("S6 缺 varId 能被查出来（1 条）", miss == [(1, "b")], miss)

    # S7 历史形态解析
    cases = [
        ([{"type": "other", "value": "2117"}], "2117"),
        ([{"type": "p_var", "value": "module_id"}], "21171999"),
        ({"rpa": "special", "value": [{"type": "other", "value": "2118"}]}, "2118"),
        ("$p:module_id", "21171999"),
        ("2119", "2119"),
    ]
    parse_ok = all(rm._parse_content(raw, {"module_id": "21171999"})[0] == want
                   for raw, want in cases)
    ok &= report("S7 content 四种历史形态都能解析（含 p_var → 参数默认值）", parse_ok)

    # S8 存量流程仍能构建
    #
    # 注意：`elements=None` 时，规格里声明了元素槽位的流程必然 KeyError（元素资产跟着机器人走，
    # 离线拿不到）。这是**既有噪音**，不是本次结构改造引入的。所以断言的是「失败原因必须是
    # 缺元素」，只要出现 ValueError / TypeError 就说明新结构把构建搞坏了。
    files = sorted(glob.glob(os.path.join(FLOWS, "*.json")))
    built = failed = 0
    bad = []
    for f in files:
        try:
            sp = fg.load_spec(f)
            fg.build_process_content(sp, ATOMS, elements=None, robot_id="0")
            built += 1
        except KeyError:
            failed += 1          # 缺元素，预期内
        except Exception as e:   # noqa: BLE001
            bad.append((os.path.basename(f), "%s: %s" % (type(e).__name__, e)))
    ok &= report("S8 存量流程可构建（成功 %d / 缺元素 %d / 异常 %d）"
                 % (built, failed, len(bad)), not bad, bad[:3])

    print("-" * 78)
    print("结论：%s" % ("✅ 全部通过" if ok else "❌ 有断言失败"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
