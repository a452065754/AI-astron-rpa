#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V15 门禁：`need_parse="json_str"` 参数必须落成 JSON 字符串。

## 为什么有这个门禁

2026-11-03 打通 `Email.send_email` 时踩到一个**报错完全指错方向**的坑：

    规格里根本没写 `replace_table`（可选参数），流程真跑却在**编译代码阶段**崩：
    TypeError: the JSON object must be str, bytes or bytearray, not list
      at astronverse/executor/flow/params.py:138  ← 引擎内部
      at astronverse/executor/flow/syntax/ast.py:106

根因：`replace_table` 在原子字典里带 `need_parse="json_str"`，引擎对这类参数**直接
`json.loads(value)`**；而生成器按通用规则把「漏写的可选参数」补成
`[{"type":"other","value":""}]`（数组）→ 必崩。

危险点在于**触发条件太自然**（少写一个可选参数）而**报错位置太远**
（堆栈落在引擎里，看不出跟规格有关），下一个人极可能又花一小时二分。

修法：`flow_generator._json_str_or_value()` 对这类参数一律给字符串；
显式传裸数组也会被 `json.dumps`。本门禁守四件事：

  A. 全库静态扫描：没有任何流程节点把 json_str 参数写成数组
  B. 生成器行为：漏写 → 补成**字符串**（这条是回归核心，破了就回到崩溃）
  C. 显式写法：`$json:[]` / 裸数组 / 裸字符串，三种都落成字符串
  D. 防过度修正：普通参数仍必须是 `[{"type","value"}]` 数组，不能被一起改成标量

跑法：`python logs/qa/v15_json_str_param.py`   退出码 0 = 通过
"""
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import flow_generator as fg   # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("  %s %-52s %s" % ("✅" if ok else "❌", name, detail))


def build(spec, elements=None, robot_id=None):
    """按规格生成节点树（不需要联网）。

    `elements` / `robot_id` 只对**含元素槽位**的流程有用（如 MES包装标签生成 的
    `"elements": {"element_data": "web"}`）：不传时 `pick_element_by_slot` 拿不到样本，
    会直接抛「元素库里没有」。2026-11-04 加入真实业务流程后，本门禁因此误判为
    「流程写错了」——实际是门禁自己不支持元素类流程。离线元素从
    `docs/element_cache.json` 取（`load_elements(offline=True)` 的同一份数据）。
    """
    return fg.build_process_content(spec, fg.load_atom_dict(offline=True),
                                    elements=elements,
                                    robot_id=robot_id or fg.DEFAULT_ROBOT_ID)


def offline_elements():
    """读离线元素缓存；没有缓存就返回空（不联网，门禁必须可离线复现）。"""
    cache = os.path.join(ROOT, "docs", "element_cache.json")
    if not os.path.exists(cache):
        return {}
    with open(cache, encoding="utf-8") as f:
        return json.load(f)


def robot_of_elements(spec, elements):
    """按 `element_requirements` 的元素名，反推它们属于哪个 robot。

    元素是按 robot 复制的（`pick_samples(strict=True)` 只认目标 robot 名下），
    所以构建前必须先知道 robot 是谁，否则样本为空、依然报「元素库里没有」。
    """
    reqs = spec.get("element_requirements") or []
    names = {r.get("name") for r in reqs if isinstance(r, dict)}
    if not names:
        return None
    for rec in (elements or {}).values():
        if rec.get("element_name") in names and rec.get("robot_id"):
            return str(rec["robot_id"])
    return None


def main():
    print("=" * 78)
    print("V15 need_parse=json_str 参数门禁")
    print("=" * 78)

    atoms = fg.load_atom_dict(offline=True)

    # 从字典里收集「哪些原子的哪些参数是 json_str」
    json_str_params = {}
    for key, meta in atoms.items():
        for p in meta.get("inputList") or []:
            if p.get("need_parse") == "json_str":
                json_str_params.setdefault(key, set()).add(p.get("key") or p.get("name"))
    n_pairs = sum(len(v) for v in json_str_params.values())

    # ---------- A. 全库静态扫描 ----------
    print("\n[A] flows/*.json 里这些参数的落库形态")
    bad, skipped, touched = [], [], 0
    elems = offline_elements()
    for path in sorted(glob.glob(os.path.join(ROOT, "flows", "*.json"))):
        with open(path, encoding="utf-8") as f:
            spec = json.load(f)
        # 只检查真正用到 json_str 原子的流程，避免逐条生成全部 90 条
        used = [n for n in spec.get("nodes") or [] if n.get("atom") in json_str_params]
        if not used:
            continue
        touched += 1
        try:
            nodes = build(spec, elements=elems, robot_id=robot_of_elements(spec, elems))
        except Exception as e:
            msg = str(e)
            # 「元素库里没有」= 门禁自己缺前置数据，不是被检查的 json_str 形态有问题。
            # 判 bad 会把环境缺失伪装成代码缺陷（2026-11-04 踩到），所以单列 skipped，
            # 但**必须打印出来**——静默跳过等于把这条流程移出门禁，比误报更危险。
            if "元素库里没有" in msg:
                skipped.append("%s：%s" % (os.path.basename(path), msg.split("，但")[0]))
            else:
                bad.append("%s 生成失败：%s" % (os.path.basename(path), msg))
            continue
        for node in nodes:
            keys = json_str_params.get(node.get("key")) or set()
            for it in node.get("inputList") or []:
                if it.get("key") in keys and not isinstance(it.get("value"), str):
                    bad.append("%s/%s = %r" % (os.path.basename(path), it.get("key"),
                                               type(it.get("value")).__name__))
    check("A1 json_str 参数一律是字符串", not bad,
          "涉及 %d 条流程 / %d 个参数定义，异常 %d 处，离线跳过 %d 条"
          % (touched, n_pairs, len(bad), len(skipped)))
    for x in skipped[:5]:
        print("      · 跳过（缺前置元素，未校验）：", x)
    for x in bad[:5]:
        print("      !", x)

    if "Email.send_email" not in json_str_params:
        check("A0 字典里找得到样例原子", False, "Email.send_email.replace_table 未标 need_parse")
        return 1

    # ---------- B. 漏写 → 补成字符串 ----------
    print("\n[B] 生成器自动补默认值")
    spec = {
        "name": "V15漏写自检",
        "params": [],
        "nodes": [{"atom": "Email.send_email", "alias": "发信", "out": {},
                   "inputs": {"receiver": "a@b.c", "sender_mail": "a@b.c",
                              "password": "x", "content": "hi"}}],
    }
    nodes = build(spec)
    got = {it.get("key"): it.get("value") for it in nodes[0].get("inputList") or []}
    rt = got.get("replace_table")
    check("B1 漏写 replace_table 时补成字符串", isinstance(rt, str),
          "value=%r (%s)" % (rt, type(rt).__name__))
    check("B2 补出来的值能直接 json.loads", _loadable(rt), "json.loads(%r)" % (rt,))

    # ---------- C. 三种显式写法 ----------
    print("\n[C] 显式写法的归一")
    cases = [
        ("$json:[]", "$json: 空数组"),
        ("[]", "裸空数组"),
        ('[{"origintext":"{{d}}","replacetext":"2026-11-03"}]', "裸对象数组"),
    ]
    for raw, label in cases:
        s = {
            "name": "V15显式",
            "params": [],
            "nodes": [{"atom": "Email.send_email", "alias": "发信", "out": {},
                       "inputs": {"receiver": "a@b.c", "sender_mail": "a@b.c",
                                  "password": "x", "content": "hi",
                                  "replace_table": raw}}],
        }
        try:
            n = build(s)
            v = {it.get("key"): it.get("value") for it in n[0].get("inputList") or []}["replace_table"]
            check("C %s" % label, isinstance(v, str) and _loadable(v),
                  "→ %r" % (v,))
        except Exception as e:
            check("C %s" % label, False, "生成失败：%s" % e)

    # ---------- D. 防过度修正 ----------
    print("\n[D] 普通参数不能被一起改成标量")
    v = {it.get("key"): it.get("value") for it in nodes[0].get("inputList") or []}
    check("D1 普通 Str 参数仍是 [{type,value}] 数组",
          isinstance(v.get("receiver"), list) and v["receiver"]
          and isinstance(v["receiver"][0], dict) and "type" in v["receiver"][0],
          "receiver=%r" % (v.get("receiver"),))

    ok = sum(1 for _, o, _ in results if o)
    print("\n" + "=" * 78)
    print("结论：%d/%d 通过" % (ok, len(results)))
    if ok != len(results):
        print("失败项：")
        for n, o, d in results:
            if not o:
                print("  ❌ %s %s" % (n, d))
    print("=" * 78)
    return 0 if ok == len(results) else 1


def _loadable(v):
    if not isinstance(v, str):
        return False
    if v == "":
        return True          # 引擎把空串当空数组
    try:
        json.loads(v)
        return True
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(main())
