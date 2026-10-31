#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""规格校验器（spec lint）：AI 写完流程规格，先过这一关。

## 为什么要单独做

`ai_flow.py --stage draft` 也会校验，但它是「构建 + 校验」一体的：规格写错时
`build_process_content` 直接抛 `ValueError`/`KeyError`，你只看到一行异常，
不知道**是第几个节点、哪个参数、该怎么改**。

本脚本把规格当**文本**逐项检查，报错一律带「第几个节点 + 怎么改」，
让 AI 能自己改到通过（自纠错闭环）。

## 检查项

| 类别 | 检查 |
| --- | --- |
| 结构 | `name` / `nodes` 是否存在、节点是否为空、参数名是否重复 |
| 原子 | 原子是否存在（不存在给相近候选）、必填参数是否齐全、是否写了 schema 外的参数 |
| 枚举 | 参数带 `options` 时，字面量取值是否合法（列出可选值） |
| 引用 | `$p:x` 是否已声明、`$x` / `$var:x` 是否在上游节点产出、`$json:` 能否解析 |
| 配对 | `Code.ForList` 必须有 `Code.ForEnd`、`Code.If` 必须有 `Code.IfEnd` |
| 迁移 | `Script.module` 是否把 `module_id` 做成了流程参数（换环境才不用改流程） |

## 用法

    python spec_lint.py flows/Excel按条件拆表小流程.json
    python spec_lint.py flows/x.json --json
    python spec_lint.py flows/x.json --offline     # 用本地原子缓存
"""
import argparse
import difflib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import flow_generator as fg

ROOT = os.path.dirname(HERE)


def _load_missing_impl():
    """读 `docs/atom_missing.json`：字典里有、本机 python_core 没实现的原子。"""
    p = os.path.join(ROOT, "docs", "atom_missing.json")
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("missing") or {}
    except Exception:
        return {}


MISSING_IMPL = _load_missing_impl()

# 常见参数的「怎么写」提示（AI 最容易漏的几个）
PARAM_HINT = {
    "content": '填模块 id，通常写 "$p:module_id"',
    "module_param": '写 "$json:[{\\"varName\\": \\"参数名\\", \\"varValue\\": \\"$p:xxx\\"}]"',
    "excel": "填上游 Excel.open_excel 产出的变量，如 \"$excel_obj\"",
    "file_path": '填文件路径，可用 "$p:src_file" 或字面量',
    "folder_path": '填文件夹路径，可用 "$p:in_dir"',
    "list": '填上游 File.get_file_list 产出的列表变量，如 "$file_list"',
    "args1": "条件左值，如 \"$item\"",
    "args2": "条件右值，如 \"$p:pattern\"",
    "condition": '可选 in / notin / eq / ne / gt / lt 等，如 "in"',
}

PAIRS = {"Code.ForList": "Code.ForEnd", "Code.If": "Code.IfEnd",
         "Code.Try": "Code.TryEnd", "Code.While": "Code.WhileEnd",
         # 循环类块**统一用 Code.ForEnd 收尾**：`Code.ForStepEnd` 编译出来是
         # `None(...)` 必崩（坑 122）；ForList/ForStep 已实测，ForDict 同类处理
         "Code.ForDict": "Code.ForEnd", "Code.ForStep": "Code.ForEnd",
         "Code.Group": "Code.GroupEnd",
         # 业务域自带的循环块（没有专属 End 原子，统一用 Code.ForEnd 收尾）
         # 依据：编译后就是 `for k, v in Excel().loop_excel_content(...):`，见坑 116
         "Excel.loop_excel_content": "Code.ForEnd",
         "DataTable.loop_data_table": "Code.ForEnd",
         "BrowserElement.loop_similar": "Code.ForEnd",
         # 「IF xxx 存在」类原子本身就是开块，同样用 Code.IfEnd 收尾
         "File.file_exist": "Code.IfEnd",
         "Folder.folder_exist": "Code.IfEnd"}

# 编译出来是 `None(...)` 的死原子：字典里有、平台编译不支持，写了必崩
DEAD_ATOMS = {
    "Code.ForStepEnd": "用 Code.ForEnd 收尾（见坑 122）",
    "Code.ForListEnd": "用 Code.ForEnd 收尾（见坑 122）",
    "Code.ForDictEnd": "用 Code.ForEnd 收尾（见坑 122）",
}

# 元素类参数（要靠 elements 槽位从元素库取真实元素，不能写字面量）
ELEMENT_TYPES = {"WebPick", "UiaPick", "CvPick", "Pick", "Element"}


def _walk_refs(value, out):
    """递归收集 $p: / $var: / $xxx 引用。"""
    if isinstance(value, dict):
        for v in value.values():
            _walk_refs(v, out)
    elif isinstance(value, list):
        for v in value:
            _walk_refs(v, out)
    elif isinstance(value, str):
        if value.startswith("$json:"):
            try:
                obj = json.loads(value[6:])
            except Exception:
                out.append(("json_bad", value[:80]))
                return
            _walk_refs(obj, out)
        elif value.startswith("$p:"):
            out.append(("param", value[3:]))
        elif value.startswith("$var:"):
            out.append(("var", value[5:]))
        elif value.startswith("$"):
            out.append(("var", value[1:]))


def lint(spec, atoms):
    errors, warns = [], []

    def err(msg, fix=""):
        errors.append({"msg": msg, "fix": fix})

    def warn(msg, fix=""):
        warns.append({"msg": msg, "fix": fix})

    # ---------- 顶层 ----------
    if not isinstance(spec, dict):
        err("规格不是 JSON 对象", "顶层应是 {name, nl_examples, keywords, params, nodes}")
        return errors, warns
    if not (spec.get("name") or "").strip():
        err("缺少 name", '加 "name": "流程名"')
    nodes = spec.get("nodes") or []
    if not nodes:
        err("nodes 为空", "至少放一个节点，例如 {\"atom\": \"Excel.open_excel\", \"inputs\": {...}}")

    # ---------- 参数表 ----------
    params = spec.get("params") or []
    pnames = []
    for i, p in enumerate(params, 1):
        n = (p or {}).get("name")
        if not n:
            err("第 %d 个流程参数没有 name" % i, '每个参数形如 {"name":"src_file","type":"Str","default":"","describe":"..."}')
            continue
        if n in pnames:
            err("流程参数重名：%s" % n, "删掉重复的那个")
        pnames.append(n)
    pset = set(pnames)

    # ---------- 逐节点 ----------
    produced = set()      # 上游已产出的变量名
    loop_stack = []       # (atom, 节点序号)
    for idx, node in enumerate(nodes, 1):
        if not isinstance(node, dict):
            err("第 %d 个节点不是对象" % idx)
            continue
        atom_key = node.get("atom") or ""
        alias = node.get("alias") or ""
        where = "第 %d 个节点%s" % (idx, "（%s）" % alias if alias else "")

        atom = atoms.get(atom_key)
        if not atom:
            cand = difflib.get_close_matches(atom_key, list(atoms), n=3, cutoff=0.5)
            err("%s：原子不存在 %r" % (where, atom_key),
                ("相近的有：" + " / ".join(cand)) if cand else "用 atom_catalog.py 查可用原子")
            continue

        if atom_key in DEAD_ATOMS:
            err("%s：%s 是死原子（编译成 None 必崩）" % (where, atom_key),
                DEAD_ATOMS[atom_key])
            continue

        # 字典里有、但本机 python_core 没实现（版本不一致，跑了必 AttributeError）
        # 数据来自 `python scripts/atom_impl_check.py` 落盘的 docs/atom_missing.json
        if atom_key in MISSING_IMPL:
            err("%s：本机没有实现 %s（%s）" % (where, atom_key, MISSING_IMPL[atom_key]),
                "换一个原子，或先让平台版本与字典对齐")
            continue

        schema = {p.get("key"): p for p in (atom.get("inputList") or []) if isinstance(p, dict)}
        outs_schema = {p.get("key") for p in (atom.get("outputList") or []) if isinstance(p, dict)}
        inputs = node.get("inputs") or {}
        elem_slots = node.get("elements") or {}

        # 必填 / 多余
        # 注意三条豁免（否则会把正常规格误判成错误）：
        #   1) `dynamics` 非空的参数是**条件显示**的（如 File.get_file_list 的 excel_path
        #      只在 state_type=excel 时出现），当前配置下不需要；
        #   2) 元素槽位（node.elements）会从元素库填真实元素，不需要在 inputs 里写；
        #   3) 有默认值的必填参数缺省时只是用默认值，合并成一条警告，不逐条刷屏。
        defaulted = []
        for k, p in schema.items():
            if not p.get("required") or k in inputs:
                continue
            if k in elem_slots or p.get("dynamics"):
                continue
            dv = p.get("default")
            if p.get("types") in ELEMENT_TYPES:
                err("%s（%s）：缺少元素参数 %s" % (where, atom_key, k),
                    '在节点里加 "elements": {"%s": "web"}（或 uia/cv），由元素库自动填真实元素' % k)
            elif dv not in (None, ""):
                defaulted.append(k)
            else:
                err("%s（%s）：缺少必填参数 %s" % (where, atom_key, k),
                    PARAM_HINT.get(k, "按 atom_catalog.py --atom %s 的参数表补上" % atom_key))
        if defaulted:
            warn("%s（%s）：%d 个必填参数未写，将用默认值：%s"
                 % (where, atom_key, len(defaulted), ", ".join(defaulted)),
                 "默认值不合适时再显式写")
        for k in inputs:
            if k not in schema:
                cand = difflib.get_close_matches(k, list(schema), n=2, cutoff=0.6)
                err("%s（%s）：参数 %s 不在该原子的 schema 里" % (where, atom_key, k),
                    ("是不是想写：" + " / ".join(cand)) if cand else
                    "可用参数：" + ", ".join(sorted(schema)))

        # 枚举
        for k, raw in inputs.items():
            p = schema.get(k) or {}
            opts = [o.get("value") if isinstance(o, dict) else o for o in (p.get("options") or [])]
            if opts and isinstance(raw, str) and not raw.startswith("$") and raw not in opts:
                err("%s（%s）：%s 的取值 %r 不合法" % (where, atom_key, k, raw),
                    "可选：" + " / ".join(str(x) for x in opts))

        # 引用
        refs = []
        _walk_refs(inputs, refs)
        for kind, name in refs:
            if kind == "json_bad":
                err("%s：$json: 内容不是合法 JSON（%s）" % (where, name),
                    '形如 "$json:[{\\"varName\\": \\"src\\", \\"varValue\\": \\"$p:src_file\\"}]"')
            elif kind == "param":
                if name not in pset:
                    cand = difflib.get_close_matches(name, pnames, n=2, cutoff=0.6)
                    err("%s：引用了未声明的流程参数 $p:%s" % (where, name),
                        ("是不是想写：$p:" + " / $p:".join(cand)) if cand else
                        ("已声明：" + (", ".join(pnames) or "（无）")))
            elif kind == "var":
                if name not in produced:
                    cand = difflib.get_close_matches(name, sorted(produced), n=2, cutoff=0.6)
                    err("%s：变量 $%s 在前面没有产出" % (where, name),
                        ("可用变量：" + ", ".join(sorted(produced))) if produced else
                        "先放一个产出该变量的节点（如 File.get_file_list 的 file_list）")

        # out 声明是否在 schema 里
        for ok_, vname in (node.get("out") or {}).items():
            if ok_ not in outs_schema:
                warn("%s（%s）：out 里的 %s 不是该原子的输出项" % (where, atom_key, ok_),
                     "可用输出：" + (", ".join(sorted(outs_schema)) or "（无）"))
            if vname:
                produced.add(vname)

        # 循环/条件配对
        if atom_key in PAIRS:
            loop_stack.append((atom_key, idx, where))
        elif atom_key in PAIRS.values():
            if not loop_stack:
                err("%s：多余的 %s（前面没有对应的开块）" % (where, atom_key), "删掉它")
            else:
                open_atom, open_idx, open_where = loop_stack.pop()
                if PAIRS.get(open_atom) != atom_key:
                    err("%s：%s（第 %d 个节点）没有正确闭合，遇到的是 %s"
                        % (open_where, open_atom, open_idx, atom_key),
                        "应该用 %s 收尾" % PAIRS.get(open_atom, "对应的 End"))

        # Script.module 的迁移提醒
        if atom_key == "Script.module" and "module_id" not in pset:
            warn("%s：Script.module 的模块 id 没做成流程参数" % where,
                 '加一个参数 {"name":"module_id","type":"Str","default":"<模块id>"}，'
                 '并把 content 写成 "$p:module_id"（换环境重建模块时只改参数）')

    for open_atom, open_idx, open_where in loop_stack:
        err("%s：%s 没有闭合" % (open_where, open_atom),
            "在块末尾补一个 {\"atom\": \"%s\", \"inputs\": {}}" % PAIRS.get(open_atom, "Code.ForEnd"))

    return errors, warns


def main():
    ap = argparse.ArgumentParser(description="流程规格校验（报错带定位与修法）")
    ap.add_argument("spec", help="规格文件路径，如 flows/x.json")
    ap.add_argument("--offline", action="store_true", help="用本地原子缓存")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    a = ap.parse_args()

    if not os.path.exists(a.spec):
        print("❌ 文件不存在：%s" % a.spec)
        return 2
    try:
        with open(a.spec, "r", encoding="utf-8") as f:
            spec = json.load(f)
    except Exception as e:
        print("❌ 规格不是合法 JSON：%s" % e)
        return 2

    atoms = fg.load_atom_dict(offline=a.offline)
    if not atoms:
        print("❌ 原子字典为空（服务端不可达且无缓存）")
        return 2

    errors, warns = lint(spec, atoms)
    name = spec.get("name") or os.path.basename(a.spec)

    if a.json:
        print(json.dumps({"name": name, "is_pass": not errors,
                          "errors": errors, "warnings": warns}, ensure_ascii=False, indent=2))
        return 0 if not errors else 1

    print("规格：%s（%s）" % (a.spec, name))
    print("节点 %d 个 · 参数 %d 个" % (len(spec.get("nodes") or []), len(spec.get("params") or [])))
    print("-" * 60)
    if errors:
        print("❌ %d 个错误（必须改完才能落库）：" % len(errors))
        for i, e in enumerate(errors, 1):
            print("  %d. %s" % (i, e["msg"]))
            if e["fix"]:
                print("     → %s" % e["fix"])
    if warns:
        print("⚠️ %d 个警告（不阻塞，但建议改）：" % len(warns))
        for i, w in enumerate(warns, 1):
            print("  %d. %s" % (i, w["msg"]))
            if w["fix"]:
                print("     → %s" % w["fix"])
    if not errors and not warns:
        print("✅ 结构与引用全部通过")
    elif not errors:
        print("✅ 无阻塞问题（可以落库）")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
