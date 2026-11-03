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
| 脚本 | `Script.module` 的 `module_param` 结构、`out.program_script`；`content` 写 `$script:名字` 时还查：脚本是否存在、能否 `ast.parse`、有没有 `def main(args)`、**`args.get` 的参数名与 `module_param` 是否对得上**、import 是否在白名单内、**有没有写 `why`（命中判定 B-几）与 `maintain_hint`**、有没有定义了没人用的脚本 |

## 用法

    python spec_lint.py flows/Excel按条件拆表小流程.json
    python spec_lint.py flows/x.json --json
    python spec_lint.py flows/x.json --offline     # 用本地原子缓存
"""
import argparse
import ast
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
    "content": '填模块 id（如 "$p:module_id"），或写 "$script:脚本名" 用顶层 scripts 里的内嵌脚本',
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
         "Code.Try": "Code.TryEnd",
         # While **也用 Code.ForEnd 收尾**（不是 WhileEnd）：
         # token.py 的 special_token_type_end[While] = [ForEnd]，
         # 前端 atomKeyMap.ts 的 LOOP_END_MAP[While] 也写的是 ForEnd（坑 133）
         "Code.While": "Code.ForEnd",
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

# 作废 key：字典 c_atom_meta_new 里还有，但**编译器不认、前端也不生成**。
# 依据（2026-10-31 实测 + 源码双证）：
#   1) 引擎 token.py 的 TokenType 枚举只有 IfEnd / ForEnd / TryEnd / GroupEnd 四个 End
#   2) 前端 atomKeyMap.ts 的 LOOP_END_MAP 生成的收尾节点也只有这四个
#   3) 173 条真实流程里这 8 个 key 出现次数均为 0
# 写了必崩：编译时不在枚举 → 掉进 parse_atomic 当普通原子 → 生成 `None(...)`
DEAD_ATOMS = {
    "Code.ForStepEnd": "用 Code.ForEnd 收尾（坑 122 实测崩）",
    "Code.ForListEnd": "用 Code.ForEnd 收尾（坑 133 作废 key）",
    "Code.ForDictEnd": "用 Code.ForEnd 收尾（坑 133 作废 key）",
    "Code.WhileEnd": "用 Code.ForEnd 收尾（坑 133 作废 key，While 也用 ForEnd）",
    "Code.ElseEnd": "用 Code.IfEnd 收尾（坑 133 作废 key）",
    "Code.ElseIfEnd": "用 Code.IfEnd 收尾（坑 133 作废 key）",
    "Code.CatchEnd": "接 Code.Finally 或 Code.TryEnd（坑 133 作废 key）",
    "Code.FinallyEnd": "用 Code.TryEnd 收尾（坑 133 作废 key）",
    "Code.Process": "旧版子流程 key，改用 Script.process（前端 atomKeyMap.ts: ProcessOld 注释）",
    "Docx.open_document": "旧 key，改用 Docx.open_docx（类里没有 open_document，真实流程 0 命中）",
    "Docx.read_document_content": "旧 key，改用 Docx.read_docx（类里没有 read_document_content，真实流程 0 命中）",
}

# 元素类参数（要靠 elements 槽位从元素库取真实元素，不能写字面量）
ELEMENT_TYPES = {"WebPick", "UiaPick", "CvPick", "Pick", "Element"}

# 系统目录下的绝对路径**不算**「绑死本机」（规则 1.8 豁免）。
#
# 依据：`C:\Windows\System32\mstsc.exe`、`C:\Windows\System32\charmap.exe` 这类
# 路径在任何一台 Windows 上都是同一个位置，换机器 clone 下来照样成立；
# 拦它只会逼人给整条规格加 `allow_abs_path`，反而把真正的检查关掉。
SYS_PATH = re.compile(r"^[A-Za-z]:[\\/](?:Windows|Program Files(?: \(x86\))?|ProgramData)[\\/]",
                      re.IGNORECASE)

# 「标量计数器」的正经写法（D1 提示用它来豁免，避免误伤合法长流程）：
# 先用 `DataProcess.set_variable_value` 初始化一个变量，再在循环体里用
# `MathProcess.self_calculation_number`（add/sub，出参写回同名变量）让它每轮自增/自减，
# `Code.While` 的条件读这个变量。两个原子都在字典里、本机有实现。
# 2026-11-03 复核 `条件分支与While循环小流程`（28 节点 / While + Continue + ForDict）实测：
#   i 初始化 0 → 连跑 3 轮，第 2 轮被 Continue 跳过 → 产出「第 1 轮 / 第 3 轮」，
#   结果文件记「While 结束计数：3」，四份证据文件互相印证。
#   **所以 B-1 里「引擎没有变量赋值/自增原子」的说法是错的**。D1 提示据此收窄：
#   三样齐备 = 正经计数器，不提示；缺任一样（典型「ForList + 一堆 If 假装计数」）照旧提示。
COUNTER_INIT_ATOMS = {"DataProcess.set_variable_value"}
COUNTER_STEP_ATOMS = {"MathProcess.self_calculation_number"}

# ---------------------------- 内嵌脚本（scripts）校验用常量 ----------------------------
#
# 模块运行环境**离线**，第三方库只有这几个（实测 `%APPDATA%/astron-rpa/python_core/Lib/
# site-packages` 里的清单）。白名单外的库「装了也跑不了」，所以在生成期就拦下来 ——
# 真跑到 `import` 才炸的话，一轮真跑要几十秒，代价太高。
THIRD_PARTY_WHITELIST = {
    "openpyxl",     # Excel 读写（最常用）
    "pandas",       # 表格/透视/分组
    "pdfplumber",   # PDF 取文本/版面
    "docx",         # python-docx
    "PIL",          # Pillow 图像
    "numpy",
    "astronverse",  # 平台自带（from astronverse.workflowlib import logger）
    "win32com",     # Excel/Word COM
    "requests",
}


def _allowed_import_roots():
    """允许 import 的顶层模块名 = 解释器自带的标准库 ∪ 第三方白名单。

    标准库不手写清单（手写必漏，且换 Python 版本就过期）：`sys.stdlib_module_names`
    是解释器自己给的，永远与运行环境同步。Python 3.10+ 才有，取不到时退回空集。
    """
    std = set(getattr(sys, "stdlib_module_names", ()) or ())
    return std | THIRD_PARTY_WHITELIST


def _collect_imports(tree):
    """收集脚本里 import 的**顶层**模块名（`from a.b import c` → `a`）。"""
    roots = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                roots.append(a.name.split(".")[0])
        elif isinstance(n, ast.ImportFrom):
            if getattr(n, "level", 0):
                continue        # 相对 import（from . import x）不可能是环境缺库，不查
            if n.module:
                roots.append(n.module.split(".")[0])
    return roots


def _find_main(tree):
    """找模块级 `def main(...)`（平台唯一会调用的入口）。"""
    for n in getattr(tree, "body", []):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "main":
            return n
    return None


def _is_args_name(node):
    return isinstance(node, ast.Name) and node.id == "args"


def _collect_arg_reads(tree):
    """用 ast 收集代码里读了哪些 args 字段：`args.get("x")` / `args["x"]`（单/双引号都算）。

    返回 `(名字集合, 是否有非字面量读取)`。后者指 `args.get(k)` 这种运行时才知道 key 的
    写法 —— 那种没法静态核对，跳过参数一致性检查（否则会误报一堆）。
    """
    names, dynamic = set(), False
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr == "get" and _is_args_name(n.func.value):
            if n.args and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str):
                names.add(n.args[0].value)
            else:
                dynamic = True
        elif isinstance(n, ast.Subscript) and _is_args_name(n.value):
            sl = n.slice
            if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                names.add(sl.value)
            else:
                dynamic = True
    return names, dynamic


def _script_table(spec, err):
    """把规格顶层 `scripts` 规范化成 `{脚本名: {"code": ...}}`（坏结构就地报错）。

    注意：`lint()` 常被直接喂**模型原文**（`nl2spec.extract_json` 的输出，没走
    `flow_generator.load_spec`），所以这里要能容忍「简写成字符串代码」等写法，
    不能假设已经规范化过。
    """
    raw = spec.get("scripts")
    out = {}
    if raw in (None, "", {}, []):
        return out
    if not isinstance(raw, dict):
        err("顶层的 scripts 必须是对象", '形如 {"脚本名": {"code": "def main(args): ..."}}')
        return out
    for k, v in raw.items():
        name = str(k)
        if isinstance(v, str):
            out[name] = {"name": name, "code": v}
        elif isinstance(v, dict):
            out[name] = v
        else:
            err("scripts.%s 必须是对象或代码字符串" % name,
                '写成 {"code": "def main(args): ... return {...}"}')
    return out


def _lint_scripts(spec, scripts, err, warn):
    """`Script.module` 节点 + 内嵌脚本的专项校验（2026-11-02 新增）。

    ## 设计原则（这两条决定了「不误伤老流程」）

    1. **规则 1（module_param）/ 规则 7（out.program_script）对所有 Script.module 生效** ——
       它们是落库必需项，跟模块内容无关。现有 8 条含 Script.module 的流程全部满足。
    2. **脚本内容类校验（规则 2~6、9）只在 `content == "$script:名字"` 时做**。历史写法
       （`content` 是纯 moduleId 或 `$p:module_id`，走 `make_module.py`）本地根本没有代码，
       强行校验只会把已经通过人工评审的流程判错。

    ## 规则 9（why / maintain_hint）为什么是 err

    产品的验收标准是「非专业业务人员能看懂、能做微调」（用户 2026-11-02 明确的约束）。
    脚本天生违反这一条，所以它只能是**兜底**：每条脚本必须自证「命中哪条判定（B-1~B-4）」
    和「业务人员还能改什么」。写不出「为什么原子不行」，基本就等于「其实原子能做、模型偷懒」。

    ## 为什么「args 一致性」是本文件价值最高的一条

    引擎把 `module_param` 的值传进模块时，**args 里没有的 key 就是没有**，`args.get("x")`
    静默返回 None。于是「参数名拼错一个字母」不会报错，只会在跑完 N 分钟后产出一个空文件
    —— 这是最难查的一类失败。静态对一遍 `args.get` 和 `varName` 就能在生成期拦住。
    """
    nodes = spec.get("nodes") or []
    used = set()

    for idx, node in enumerate(nodes, 1):
        if not isinstance(node, dict) or (node.get("atom") or "") != "Script.module":
            continue
        alias = node.get("alias") or ""
        where = "第 %d 个节点%s" % (idx, "（%s）" % alias if alias else "")
        inputs = node.get("inputs") or {}
        content = inputs.get("content")

        # ---------- 规则 1：module_param 结构（对所有 Script.module 生效） ----------
        given_names = []
        mp = inputs.get("module_param")
        if mp is None:
            err("%s：Script.module 缺少 module_param" % where,
                '写 "module_param": "$json:[{\\"varName\\": \\"参数名\\", '
                '\\"varValue\\": \\"$p:xxx\\"}]"；模块不需要参数时写 "$json:[]"')
        elif not isinstance(mp, str) or not mp.startswith("$json:"):
            err("%s：module_param 必须是字符串化的 JSON（以 \"$json:\" 开头）" % where,
                '形如 "$json:[{\\"varName\\": \\"src_file\\", \\"varValue\\": \\"$p:src_file\\"}]"'
                "（写成数组/对象都会被引擎当字符串处理）")
        else:
            payload = None
            try:
                payload = json.loads(mp[len("$json:"):])
            except Exception as e:
                err("%s：module_param 里的 JSON 解析失败：%s" % (where, e),
                    '检查引号与反斜杠转义；形如 "$json:[{\\"varName\\": \\"x\\", '
                    '\\"varValue\\": \\"$p:x\\"}]"')
            if payload is not None and not isinstance(payload, list):
                err("%s：module_param 必须是 JSON **数组**（每项一个参数）" % where,
                    '没有参数就写 "$json:[]"，别写空串或对象')
            elif isinstance(payload, list):
                bad = [i for i, it in enumerate(payload, 1)
                       if not (isinstance(it, dict) and it.get("varName"))]
                if bad:
                    err("%s：module_param 第 %s 项缺 varName"
                        % (where, ", ".join(str(i) for i in bad)),
                        '每项形如 {"varName": "参数名", "varValue": "字面量或 $p:xxx"}')
                given_names = [str(it.get("varName")) for it in payload
                               if isinstance(it, dict) and it.get("varName")]

                # ---------- 规则 1.5：varValue 一律禁止 JSON 布尔/空值（**err**） ----------
                #
                # 为什么是硬拦（用户 2026-11-02 / QA N10 P1；2026-11-03 按新结构复核仍成立）：
                #   1. 落库后每个参数是 `{"rpa":"special","value":[{"type":"other","value":X}]}`，
                #      引擎的 `ComplexParamParser` 会把这段**当表达式求值**（need_eval 路径）。
                #      JSON 的 `true/false/null` 在 Python 里是 `True/False/None`（大小写不符）
                #      → 跑到那一步才 `NameError: name 'true' is not defined`，
                #      报的错与真因毫无关系 —— 典型静默错。
                #   2. 写字符串 "true" 则两条路都安全（引擎给字符串、模块侧本来就要求
                #      `str(args.get("x")).lower() in ("true", "1", "yes")`）。
                #   3. 数字（3 / 3.5）不会 NameError（Python 字面量合法），但同样违反「args 全是
                #      字符串」的模块契约，所以只报 warn，不阻断存量流程。
                for it in payload:
                    if not isinstance(it, dict):
                        continue
                    vn = it.get("varName")
                    if "varValue" not in it:
                        err("%s：module_param 里 %s 缺少 varValue" % (where, vn),
                            '补 "varValue": "字面量" 或 "varValue": "$p:参数名"'
                            '（模块里 args.get 拿到的就是这个值）')
                        continue
                    vv = it.get("varValue")
                    # 文案必须按**类型**分开（QA N10-e）：`null` 的错名是 `name 'null' is not
                    # defined`、改法是空串；照抄布尔分支的「改成 "true" / "false"」等于指错方向，
                    # 而「照着改就能过」正是这条 lint 的全部价值。
                    if isinstance(vv, bool):
                        err("%s：module_param 里 %s 的 varValue 是 %s —— "
                            "不能是 JSON 布尔（true/false）" % (where, vn, json.dumps(vv)),
                            "引擎会把它们当 Python 源码求值，报 "
                            "NameError: name '%s' is not defined。" % json.dumps(vv)
                            + '改成字符串 "true" / "false"')
                    elif vv is None:
                        err("%s：module_param 里 %s 的 varValue 是 null —— "
                            "不能是 JSON 空值" % (where, vn),
                            "引擎会把 null 当 Python 源码求值，报 "
                            "NameError: name 'null' is not defined。"
                            '改成空字符串 ""，或用字符串 "null"（模块侧自己判断）')
                    elif isinstance(vv, (int, float)):
                        # 数字在 Python 里是合法字面量（3 → 3），**不会** NameError，
                        # 所以只 warn（判 err 会把本来能跑的流程判死）；但模块契约要求 str。
                        to = "float" if isinstance(vv, float) else "int"
                        warn("%s：module_param 里 %s 的 varValue 是数字 %s"
                             "（模块拿到的 args 约定全是字符串）" % (where, vn, json.dumps(vv)),
                             '建议改成字符串 "%s"，模块侧用 %s(args.get("%s")) 转换；'
                             "否则 args.get() 拿到的是 %s，.strip()/.lower() 会 AttributeError"
                             % (vv, to, vn, to))

        # ---------- 规则 1.6：sheet 名不许写死（**warn**） ----------
        #
        # AI 生成规格时**看不到源文件里 sheet 叫什么**，写 "Sheet1" / "Sheet" 十有八九猜错，
        # 表现是第一次真跑就 `RuntimeError: 工作表 'Sheet1' 不存在，可用工作表: ['明细']`
        # （2026-11-03 实测踩到）。正确做法是默认留空 ""（脚本按「第一张表」处理）。
        # 只报 warn 不报 err：确实有流程就是固定要读某张表，写死也合理。
        for it in payload:
            if not isinstance(it, dict):
                continue
            vn = str(it.get("varName") or "")
            if "sheet" not in vn.lower():
                continue
            vv = it.get("varValue")
            src = "module_param"
            if isinstance(vv, str) and vv.startswith("$p:"):
                # 常见写法：module_param 引用流程参数，真实默认值在 spec["params"] 里
                pname = vv[3:]
                hit = [p for p in (spec.get("params") or [])
                       if isinstance(p, dict) and p.get("name") == pname]
                if not hit:
                    continue
                vv = hit[0].get("default")
                src = "流程参数 %s 的 default" % pname
            elif not isinstance(vv, str):
                continue
            if vv is None:
                continue
            s = str(vv).strip()
            if s == "" or s.isdigit():
                continue                      # 空 = 第一张表；数字 = 索引，都安全
            warn("%s：%s 的默认值写死了具体 sheet 名 %r（来自 %s）" % (where, vn, vv, src),
                 '建议默认留空 ""（= 第一张表），让业务人员按需填真名；'
                 "写死具体名时源文件一换就跑不起来（实测：写 Sheet1、源表叫「明细」→ 直接报错）")

        # ---------- 规则 1.7：被脚本引用的 `$p:x` 默认值必须是**字符串**（**err**） ----------
        #
        # 脚本契约是「args 里全是字符串」。而流程参数常被声明成 `type=Bool, default=false`
        # （给原子用是对的），一旦脚本 `module_param` 里写 `"$p:overwrite"`，烘焙时就会把
        # 布尔落进 `{"type":"other","value":false}` → 引擎按表达式求值 → `NameError: name
        # 'false' is not defined`（2026-11-03 实测踩到，`_bake_refs` 拿的是流程参数的默认值）。
        #
        # 修法两条路：① 把流程参数的 default 改成字符串 `"false"`（推荐，脚本参数本就该是字符串）；
        # ② 在 `scripts.<名>.params` 里单独声明同名参数（脚本侧默认值优先，生成器已按此实现）。
        for it in payload:
            if not isinstance(it, dict):
                continue
            vv = it.get("varValue")
            if not (isinstance(vv, str) and vv.startswith("$p:")):
                continue
            pname = vv[3:]
            # 脚本侧单独声明了同名参数 → 生成器优先用它的默认值（脚本契约），不算问题
            _sn = (content[len(fg.SCRIPT_REF_PREFIX):].strip()
                   if isinstance(content, str) and content.startswith(fg.SCRIPT_REF_PREFIX) else "")
            _sps = {d.get("name"): d for d in
                    (((spec.get("scripts") or {}).get(_sn) or {}).get("params") or [])
                    if isinstance(d, dict)}
            if pname in _sps and _sps[pname].get("default") is not None:
                continue
            hit = [p for p in (spec.get("params") or [])
                   if isinstance(p, dict) and p.get("name") == pname]
            if not hit:
                continue
            dv = hit[0].get("default")
            if dv is None or isinstance(dv, str):
                continue
            err("%s：%s 引用的流程参数 %s 默认值是 %s（不是字符串）"
                % (where, it.get("varName"), pname, json.dumps(dv, ensure_ascii=False)),
                '脚本的 args 约定全是字符串 —— 把流程参数 %s 的 default 改成字符串 "%s"，'
                '或在 scripts.<名>.params 里单独声明它（脚本侧默认值优先）'
                % (pname, str(dv).lower() if isinstance(dv, bool) else dv))

        # ---------- 规则 7：out.program_script（对所有 Script.module 生效） ----------
        if "program_script" not in (node.get("out") or {}):
            warn("%s：out 里没有 program_script" % where,
                 '模块 main() 的返回值会无处落地（想用返回值就加 '
                 '"out": {"program_script": "模块结果变量名"}）')

        # 以下只在「内嵌脚本」模式下校验（历史写法没有本地代码可查）
        if not (isinstance(content, str) and content.startswith(fg.SCRIPT_REF_PREFIX)):
            continue
        name = content[len(fg.SCRIPT_REF_PREFIX):].strip()
        used.add(name)

        # ---------- 规则 2：脚本必须存在 ----------
        if not name:
            err("%s：content 写成了空脚本名（\"$script:\"）" % where,
                '写成 "content": "$script:脚本名"，脚本名要和顶层 scripts 的键一致')
            continue
        item = scripts.get(name)
        if item is None:
            # 结构本来就不对（`scripts.x` 是列表/数字…）时，`_script_table` 已经报过一条
            # 明确的「必须是对象或代码字符串」。这里再报「顶层没有它」纯属噪音（QA 2026-11-02
            # 实测会多报一条），所以认一下原始 key 就跳过。
            _raw = spec.get("scripts")
            if isinstance(_raw, dict) and name in _raw:
                continue
            err("%s：引用了内嵌脚本 %s，但顶层 scripts 里没有它" % (where, name),
                ("可用脚本名：" + ", ".join(sorted(scripts))) if scripts else
                '在 spec 顶层加 "scripts": {"%s": {"code": "def main(args): ..."}}' % name)
            continue
        code = str((item or {}).get("code") or "")

        # ---------- 规则 2.5：code 必须存在且非空 ----------
        # 缺 code 时后面的 `ast.parse("")` 不报语法错，会一路走到「没有定义 def main(args)」，
        # 那句错误会把人引去补函数，而真因是**整段源码没了**（QA 2026-11-02 指出）。
        if not code.strip():
            err("%s：脚本 %s 缺少 code（内嵌脚本必须给完整可运行的 Python 源码）" % (where, name),
                '补 "code": "def main(args):\\n    ...\\n    return {\\"ok\\": True}"'
                '（代码放在顶层 scripts 里，节点 content 只写 "$script:脚本名"）')
            continue

        # 规则 9：可维护性证据（why / maintain_hint，**都必须是 err**）
        #
        # 产品约束（用户 2026-11-02）：用脚本就等于「业务人员看不懂、改不动」，
        # 所以每条脚本必须自证「原子确实做不到」+「业务人员还能改什么」。
        #
        # 「why 有值但没写 B-x」为什么必须是 err（2026-11-02 由 QA 指出）：
        #   `nl2spec.generate()` 只拿 errors 决定「接受 / 回灌重写」，warns 只记录不判断。
        #   所以写成 warn 等于没有闸 —— 模型只要写一句「原子做不到，太复杂了」就能过，
        #   而那正是「模型偷懒选脚本」的典型话术。必须是 err，才真的逼它落回原子。
        why = str((item or {}).get("why") or "").strip()
        hint = str((item or {}).get("maintain_hint") or "").strip()
        if not why:
            err("%s：脚本 %s 缺少 why（必须写明为什么原子做不到）" % (where, name),
                '加 "why": "命中判定 B-2：输出文件数取决于「部门」列的唯一值，'
                '原子无法动态生成文件名"（判定条款见 docs/流程规格DSL.md §11.1）')
        elif not re.search(r"B-[1-4]", why):
            err("%s：脚本 %s 的 why 没写命中哪条判定（B-1~B-4）" % (where, name),
                '写成「命中判定 B-2：输出文件数取决于「部门」列的唯一值，原子无法动态生成文件名」。'
                '写不出条款 → 说明该用原子拼，别用脚本')
        if not hint:
            err("%s：脚本 %s 缺少 maintain_hint（业务人员改什么、什么必须找开发）" % (where, name),
                '加 "maintain_hint": "改拆哪一列 → 改参数 split_column；'
                '改输出目录 → 改参数 out_dir；改逻辑需找开发改 Python"')

        # ---------- 规则 3：能不能 ast.parse ----------
        try:
            tree = ast.parse(code, filename="scripts.%s" % name)
        except SyntaxError as e:
            err("%s：内嵌脚本 %s 有语法错误（第 %s 行）：%s"
                % (where, name, e.lineno, e.msg),
                "脚本必须是完整可运行的源码；改完语法再跑")
            continue

        # ---------- 规则 4：必须有 def main(args) ----------
        fn = _find_main(tree)
        if fn is None:
            err("%s：内嵌脚本 %s 没有定义 def main(args)" % (where, name),
                "平台只调用 main(args)；入口必须写成 def main(args): ... 并 return 结果")
        elif not (fn.args.args or fn.args.vararg):
            err("%s：内嵌脚本 %s 的 main() 没有参数" % (where, name),
                '签名必须是 def main(args)，参数用 args.get("名字") 取')

        # ---------- 规则 6：import 白名单 ----------
        allowed = _allowed_import_roots()
        bad_imports = sorted({r for r in _collect_imports(tree) if r not in allowed})
        if bad_imports:
            err("%s：内嵌脚本 %s import 了运行环境没有的库：%s"
                % (where, name, ", ".join(bad_imports)),
                "模块运行环境离线，没有这个库（装了也跑不了）。可用第三方库只有：%s，"
                "其余限 Python 标准库" % ", ".join(sorted(THIRD_PARTY_WHITELIST)))

        # ---------- 规则 5：args 一致性（核心） ----------
        reads, dynamic = _collect_arg_reads(tree)
        if dynamic:
            warn("%s：脚本 %s 用非字面量 key 读 args（如 args.get(变量)）" % (where, name),
                 "无法静态核对参数名；建议直接写 args.get(\"参数名\")")
        else:
            for nm in sorted(reads - set(given_names)):
                err("%s：脚本 %s 需要参数 %s，但 module_param 没有提供（运行时会拿到 None）"
                    % (where, name, nm),
                    '在 module_param 里补一项 {"varName": "%s", "varValue": "$p:%s"}'
                    "（或直接给字面量）" % (nm, nm))
            for nm in sorted(set(given_names) - reads):
                warn("%s：module_param 给了 %s，但脚本 %s 没读它" % (where, nm, name),
                     "留着无害（可作日志/预留）；确认不是参数名拼错")

    # ---------- 规则 8：定义了但没人用的脚本 ----------
    for nm in sorted(set(scripts) - used):
        warn("scripts.%s 定义了但没有任何节点引用它" % nm,
             '要执行就加一个节点 "content": "$script:%s"；不用就删掉' % nm)


# 常见扩展名：用来识别「变量 + 扩展名」被写成单个引用的情况（`"$name.xlsx"`）
_FILE_EXTS = (".xlsx", ".xls", ".xlsm", ".csv", ".txt", ".docx", ".doc", ".pdf",
              ".pptx", ".png", ".jpg", ".jpeg", ".json", ".zip", ".md", ".log")


def _split_var_ext(name):
    """`"clean_dept.xlsx"` → `("clean_dept", ".xlsx")`；不像「变量+扩展名」就返回空串。

    只认**白名单后缀**，避免把 `"a.b"` 这类真的含点的变量名误判。
    """
    low = (name or "").lower()
    for ext in _FILE_EXTS:
        if low.endswith(ext) and len(name) > len(ext):
            return name[: -len(ext)], name[-len(ext):]
    return ""


def _fmt_opt(v):
    """枚举值按「JSON 里的真实写法」展示。

    为什么不能直接 `str(v)`：平台 Bool 参数的 options 是 JSON 布尔，`str(True)` 得到
    `True`，模型看到就照抄成字符串 `"True"` —— 而引擎要的是不加引号的 `true`。
    这一处小差别会让流程「能跑但行为相反」（坑 69）。
    """
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, str):
        return '"%s"' % v
    return str(v)


def _type_hint(p):
    t = (p.get("types") or "").strip()
    if t == "Bool":
        return "（这是布尔参数，写 JSON 的 true / false，不要加引号）"
    if t in ("Int", "Float"):
        return "（这是数字参数，写 %s 不加引号）" % t.lower()
    return ""


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
        elif value.startswith(fg.SCRIPT_REF_PREFIX):
            # 内嵌脚本占位（`$script:名字`）：不是变量引用，交给 _lint_scripts 专项校验
            out.append(("script", value[len(fg.SCRIPT_REF_PREFIX):]))
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
    pmap = {p.get("name"): p for p in params if isinstance(p, dict) and p.get("name")}

    # ---------- 规则 1.8：流程参数的默认值不许写死某台机器的盘符路径（**err**） ----------
    #
    # 2026-11-03：`flows/` 里曾有 72 条流程 / 120 处默认值是
    # `F:\AI-Workbench\10-Projects\星辰开源\demo\...`。本机跑得好好的，别人 clone
    # 下来一跑就是 `[Errno 2] No such file or directory` —— 这是「把能力交给别人」
    # 的最后一道坎。
    #
    # 为什么不改成相对路径（`demo/订单.xlsx`）：
    # 引擎执行前 `os.chdir(project_dir)`（`executor/debug/bdb.py::cmd_start`），
    # project_dir 是 `%APPDATA%\astron\<project_id>` 这个**编译缓存目录**
    # （`Config.gen_core_path`）。相对路径会解析到缓存里，必然找不到文件。
    # 所以默认值必须是绝对路径，但**源头**不能是某一台机器的绝对路径。
    #
    # 解法：规格里写 `${PROJECT_ROOT}/demo/xxx`，`flow_generator.load_spec` 在发布时
    # 换成本机仓库根。仓库因此可迁移，落到平台的参数仍是合法绝对路径。
    #
    # 逃生口：确实要指向仓库外的固定位置（如共享盘 `\\nas\data`、`D:\ERP导出`）时，
    # 在规格顶层写 `"allow_abs_path": true`（UNC 本就不拦，只有盘符路径需要）。
    if not spec.get("allow_abs_path"):
        for i, p in enumerate(params, 1):
            v = (p or {}).get("default")
            if not isinstance(v, str) or not re.match(r"^[A-Za-z]:[\\/]", v):
                continue
            if SYS_PATH.match(v):
                continue  # 系统目录（C:\Windows\System32\*.exe 等）任何机器都同一个位置
            tail = v.split("星辰开源", 1)[-1].lstrip("\\/") if "星辰开源" in v else None
            hint = ("改成 \"${PROJECT_ROOT}/%s\"" % tail.replace("\\", "/")) if tail else \
                "改成 \"${PROJECT_ROOT}/demo/...\"；确实在仓库外就加 \"allow_abs_path\": true"
            err("第 %d 个流程参数 %s 的默认值写死了本机路径：%s" % (i, p.get("name"), v),
                "%s —— 换台机器 clone 下来这条路径不存在，流程第一步就报找不到文件" % hint)


    # ---------- 元素要求（网页流程专用） ----------
    # 规格可声明 element_requirements，生成时由 element_pick 自动补齐元素（坑 126）。
    # 校验重点：声明的名字与节点里引用的名字必须对得上，否则生成阶段才会炸。
    req_names = set()
    for i, req in enumerate(spec.get("element_requirements") or [], 1):
        if not isinstance(req, dict):
            err("第 %d 条 element_requirements 不是对象" % i,
                '形如 {"name":"百度_搜索输入框","kind":"web","pick":{"url":"...","css":"..."}}')
            continue
        rn = req.get("name")
        if not rn:
            err("第 %d 条 element_requirements 没有 name" % i, "元素必须有名，生成器按名字取")
            continue
        if rn in req_names:
            err("element_requirements 里元素名重复：%s" % rn, "删掉重复的那条")
        req_names.add(rn)
        kind = req.get("kind")
        if kind and kind not in ("web", "uia", "cv"):
            err("元素「%s」的 kind 非法：%r" % (rn, kind), "只能是 web / uia / cv")
        pick = req.get("pick") or {}
        if not pick:
            warn("元素「%s」没给 pick" % rn,
                 "库里已有同名元素时能用；缺了就生成失败。建议补 pick:{url,css}")
        elif not (pick.get("css") or pick.get("xpath")):
            err("元素「%s」的 pick 没给 css 或 xpath" % rn, "至少给一个选择器")
        elif not pick.get("url"):
            warn("元素「%s」的 pick 没给 url" % rn, "会退化成用当前活动标签页拾取")

    # ---------- 逐节点 ----------
    produced = set()      # 上游已产出的变量名
    loop_stack = []       # (atom, 节点序号)
    nested_loop = False   # 出现过「循环套循环」（D1 提示用）
    used_atoms = set()    # 用到的原子 key（D1 判断计数器是否是正经写法）
    for idx, node in enumerate(nodes, 1):
        if not isinstance(node, dict):
            err("第 %d 个节点不是对象" % idx)
            continue
        atom_key = node.get("atom") or ""
        alias = node.get("alias") or ""
        where = "第 %d 个节点%s" % (idx, "（%s）" % alias if alias else "")
        used_atoms.add(atom_key)

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

        # 槽位写法的两种形态都要能识别：
        #   "web"                        → 按种类挑（老写法，元素库有多个时不可控）
        #   {"kind":"web","name":"..."}  → 按名字精确取（网页流程必须用这个，坑 126）
        for pk, slot in elem_slots.items():
            if isinstance(slot, str):
                if slot not in ("web", "uia", "cv", "batch"):
                    err("%s：元素槽位 %s 的取值非法 %r" % (where, pk, slot),
                        "只能是 web / uia / cv，或 {kind,name} 对象")
                continue
            if not isinstance(slot, dict):
                err("%s：元素槽位 %s 不是字符串也不是对象" % (where, pk), "改回 \"web\" 或 {kind,name}")
                continue
            sn = slot.get("name")
            if not sn:
                warn("%s：元素槽位 %s 只给了 kind" % (where, pk),
                     "按种类挑元素在库里有多个时不可控，建议写死 name")
            elif req_names and sn not in req_names:
                err("%s：元素槽位 %s 引用的「%s」不在 element_requirements 里" % (where, pk, sn),
                    "在顶层 element_requirements 里声明它（含 pick 信息），生成时才会自动拾取")

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
        #
        # 布尔枚举是最贵的一个坑（2026-11-02 由 nl2spec 首次暴露）：
        # 平台把 Bool 参数的 options 声明成 JSON 布尔 `[{"label":"是","value":true},...]`，
        # 而生成器 `_coerce_literals` 只把**真 bool** 编译成 `{"type":"python"}`。
        # 规格里写成字符串 `"False"` 会被 f-string 变成 `'False'` —— 非空字符串 = 真值，
        # 于是 `visible_flag="False"` 反而**显示**窗口（坑 69 的原始事故）。
        # 以前的提示用 `str(True)` → "可选：True / False"，模型照抄成字符串，越改越错；
        # 所以这里统一改用 `_fmt_opt`（布尔出 true/false、字符串带引号）。
        for k, raw in inputs.items():
            p = schema.get(k) or {}
            opts = [o.get("value") if isinstance(o, dict) else o for o in (p.get("options") or [])]
            if opts and isinstance(raw, str) and not raw.startswith("$") and raw not in opts:
                if all(isinstance(o, bool) for o in opts) and raw.strip().lower() in ("true", "false"):
                    lit = raw.strip().lower()
                    err("%s（%s）：%s 是布尔参数，写成了字符串 %r" % (where, atom_key, k, raw),
                        '改成 JSON 布尔 %s（**不要引号**：%s，不是 "%s"）'
                        % (lit, lit, raw.strip()))
                else:
                    err("%s（%s）：%s 的取值 %r 不合法" % (where, atom_key, k, raw),
                        "可选：" + " / ".join(_fmt_opt(x) for x in opts) + _type_hint(p))

        # $json: 用在**路径类字符串参数**上 → 高度可疑，给警告
        #
        # 先说明白：`$json:` 本身不限于 need_parse 参数 —— `ListProcess.create_new_list`
        # 的 custom_list、`DictProcess.create_new_dict` 的 dict_data 都要求传字符串化 JSON，
        # 这 5 条历史流程靠它跑通（全量回归实测）。所以**不能一刀切成错误**。
        # 但路径/文件名参数被包成 `$json:[{"varName":...}]` 必炸：引擎把整段 JSON 文本
        # 当路径（nl2spec 实测踩到）。这类参数名高度可识别，单独提醒。
        _PATHISH = ("path", "file", "dir", "folder", "name")
        for k, raw in inputs.items():
            if not (isinstance(raw, str) and raw.startswith("$json:")):
                continue
            p = schema.get(k) or {}
            if p.get("need_parse") or not any(w in k.lower() for w in _PATHISH):
                continue
            warn("%s（%s）：%s 像是路径/文件名字符串，不该用 $json: 包" % (where, atom_key, k),
                 '直接写值或引用：%s 或 "$p:xxx"（$json: 是给 list/dict 之类结构化参数用的）'
                 % ('"%s"' % (p.get("default") or "值")))

        # 「拼接数组」被写成了字符串
        #
        # 规格里字符串拼接要写成**真 JSON 数组**：["$p:a", "\\\\", "$p:b"]。
        # 模型常输出成字符串 "[\"$p:a\", ...]"（看起来像数组，其实是字面量），
        # 引擎会把它原样当路径 → 真跑报「文件路径:[$p:dst_dir,\,...]有误」。
        # 静态校验以前放过它（因为它是合法字符串），只有真跑才炸。
        for k, raw in inputs.items():
            if not isinstance(raw, str):
                continue
            t = raw.strip()
            if t.startswith("[") and t.endswith("]") and "$" in t:
                err("%s（%s）：%s 把拼接数组写成了字符串" % (where, atom_key, k),
                    '改成真正的 JSON 数组，例如 ["$p:dir", "\\\\", "$p:name"]（去掉外层引号）')

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
            elif kind == "script":
                # `$script:名字` 由 `_lint_scripts` 专门校验（只有那里能列出「可用脚本名」）
                continue
            elif kind == "var":
                if name not in produced:
                    # 把「表达式」当变量名：`"$key_pos == -1"`（nl2spec 实测，模型想写条件判断）。
                    # 引擎不会求值这种东西，只会当变量名找不到 → 报错也看不懂。专门指路。
                    if re.search(r"[\s=!<>+\-*/()]", name):
                        err("%s：$%s 看起来是一个表达式，不是变量名" % (where, name),
                            "条件判断要用 Code.If 节点（inputs: args1 / condition / args2），"
                            "不要在 $ 引用里写表达式")
                        continue
                    # 「变量 + 扩展名」被写成一个引用：`"$clean_dept.xlsx"`。
                    # 报成「变量 $clean_dept.xlsx 没有产出」会误导模型去定义一个叫
                    # `clean_dept.xlsx` 的变量 —— 实测连写 3 轮都不收敛（2026-11-02）。
                    # 真正要教的是「拼接写成数组」。
                    if _split_var_ext(name):
                        base, ext = _split_var_ext(name)
                        err("%s：$%s 看起来是「变量 + 扩展名」的拼接，不是变量名" % (where, name),
                            '拼接请写成数组：["$%s", "%s"]（%s 要由上游节点产出）'
                            % (base, ext, base))
                        continue
                    # 循环变量没产出，几乎只有一个原因：循环块的 out 写成空了。
                    # 引擎不会自动提供 item/index，必须 `"out": {"index":"idx","item":"item"}`
                    # （nl2spec 实测：模型连写 3 轮 `"out": {}` + 引用 `$item`，卡死在这）
                    if name in ("item", "index", "idx") and loop_stack:
                        open_atom = loop_stack[-1][0]
                        err("%s：循环变量 $%s 没有产出" % (where, name),
                            '%s 的 out 要显式声明，写成 "out": {"index":"idx","item":"item"}；'
                            "引擎不会自动提供（你当前这块的 out 是空的）" % open_atom)
                        continue
                    cand = difflib.get_close_matches(name, sorted(produced), n=2, cutoff=0.6)
                    err("%s：变量 $%s 在前面没有产出" % (where, name),
                        ("可用变量：" + ", ".join(sorted(produced))) if produced else
                        "先放一个产出该变量的节点（如 File.get_file_list 的 file_list）")

        # ---------- 字符串里的 `$变量` / `$变量[字段]` ----------
        #
        # 2026-11-02 实测（不是猜的）：
        #   引擎只把**整段等于 `$var`** 的字符串，或**数组分段**里的 `$var` 当变量引用。
        #   `"共生成 $r 个文件"` 这种混在文本里的，落库是**一整个字面量**，运行时会原样打印
        #   `共生成 $r 个文件` —— `$` 根本不会被替换。而 `$r[processed_count]` 更糟：
        #   引擎把变量名当**裸 Python 名** eval，`r[processed_count]` 里的 processed_count
        #   会被当变量名 → NameError（读 executor/flow/params.py `_param_to_eval` 实证）。
        #
        # 为什么这两种必须是 **err**（而不是提醒）：它们**能让流程跑成 SUCCESS**。
        # `Report.print` 不校验内容，业务人员在日志里看到的是一行带 `$` 的原始文本，
        # 只会以为流程坏了。属于「静默错」，必须在生成期拦住（用户 2026-11-02 定为 P1）。
        #
        # 收窄误伤：只有当 `$` 后面那个名字**确实是已知变量/流程参数**时才报错。
        # `"价格 $100"` / 正则里的 `$^` / Windows 路径里的 `$` 都不会命中。
        for k, raw in inputs.items():
            if not isinstance(raw, str) or raw.startswith("$"):
                continue                      # 整段引用是合法写法（含 $json: / $script: / $p:）
            for m in re.finditer(r"\$([A-Za-z_\u4e00-\u9fa5][\w\u4e00-\u9fa5]*)(\s*[\[.])?", raw):
                nm, tail = m.group(1), (m.group(2) or "").strip()
                if nm not in produced and nm not in pset:
                    continue                  # 不是已知变量（可能是 PowerShell/regex 文本），不管
                if tail:
                    err("%s（%s）：%s 的取值 %r 里的 $%s%s… 想「取字段/接字符串」，引擎不支持"
                        % (where, atom_key, k, raw, nm, tail),
                        '脚本返回值是 dict，**没有** `$结果[字段]` 这个语法。要么让模块自己 '
                        'logger/print 输出摘要，要么让模块 return 一段中文摘要字符串，再用整段引用 '
                        '"%s": "$%s"' % (k, nm))
                else:
                    err("%s（%s）：%s 的取值 %r 里含 $ 但不是以 $ 开头（字符串内嵌变量不会被解析，"
                        "引擎会原样打印）" % (where, atom_key, k, raw),
                        '改用数组拼接：["文字", "$%s", "文字"]；或整串只写 "$%s"'
                        % (nm, nm))

        # `Folder.folder_create` 的语义坑（2026-11-02 实测踩到）
        #
        # `target_path` 是**父目录**、`folder_name` 是**子目录名**。模型常把「已经是完整目标
        # 目录」的 out_dir 塞进 target_path，再把最后一段填进 folder_name →
        # 实际建成 `F:\demo\out\split\split`（多一层空目录），业务人员看着莫名其妙。
        if atom_key == "Folder.folder_create":
            tp, fn = inputs.get("target_path"), inputs.get("folder_name")
            base = ""
            if isinstance(tp, str) and tp.startswith("$p:"):
                base = str((pmap.get(tp[3:]) or {}).get("default") or "")
            elif isinstance(tp, str):
                base = tp
            if base and isinstance(fn, str) and fn and not fn.startswith("$"):
                tail = re.split(r"[\\/]+", base.rstrip("\\/"))[-1]
                if tail and tail == fn:
                    warn("%s（%s）：会建成 %s\\%s，比预期多一层目录"
                         % (where, atom_key, base, fn),
                         "target_path 是**父目录**、folder_name 是子目录名。要建的就是 %s 本身时，"
                         "target_path 应写它的父目录；**或者直接删掉这个节点** —— "
                         "脚本里 os.makedirs(out_dir, exist_ok=True) 已经保证目录存在" % base)

        # out 声明是否在 schema 里
        #
        # 例外：字典没声明输出、实现却 return 了值的判断型原子（Window.exist 等），
        # 由 flow_generator.EXTRA_OUTPUT_ATOMS 显式登记，这里同步认它，否则每次跑都告警。
        extra_outs = set(fg.EXTRA_OUTPUT_ATOMS.get(atom_key, []))
        for ok_, vname in (node.get("out") or {}).items():
            if ok_ not in outs_schema and ok_ not in extra_outs:
                warn("%s（%s）：out 里的 %s 不是该原子的输出项" % (where, atom_key, ok_),
                     "可用输出：" + (", ".join(sorted(outs_schema)) or "（无）"))
            if vname:
                produced.add(vname)

        # 循环/条件配对
        if atom_key in PAIRS:
            if loop_stack:
                nested_loop = True        # 循环里再开循环 → 嵌套循环（D1 提示的触发条件之一）
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

        # Script.module 的迁移提醒（**只针对历史写法**：模块 id 做成流程参数的那种）。
        # 内嵌脚本模式（content = "$script:名字"）的模块 id 由 ai_flow.resolve_scripts()
        # 自动建/解析，本来就不该有 module_id 参数 —— 那种情况告警纯属噪音，必须跳过。
        if (atom_key == "Script.module" and "module_id" not in pset
                and not (isinstance(inputs.get("content"), str)
                         and inputs["content"].startswith(fg.SCRIPT_REF_PREFIX))):
            warn("%s：Script.module 的模块 id 没做成流程参数" % where,
                 '加一个参数 {"name":"module_id","type":"Str","default":"<模块id>"}，'
                 '并把 content 写成 "$p:module_id"（换环境重建模块时只改参数）')

    for open_atom, open_idx, open_where in loop_stack:
        err("%s：%s 没有闭合" % (open_where, open_atom),
            "在块末尾补一个 {\"atom\": \"%s\", \"inputs\": {}}" % PAIRS.get(open_atom, "Code.ForEnd"))

    # ---------- 长流程 + 嵌套循环 → 提示「可能把累计状态硬拼成原子了」（D1） ----------
    #
    # 为什么不加「> N 个节点就必须用脚本」这类硬阈值（用户 2026-11-02 拍板）：
    #   15 步表格处理确实该用原子，阈值会误伤合法长流程。
    # 但「节点多 + 嵌套循环」是模型硬拼 B-1（跨迭代累计状态）的高发组合：
    # 模型想模拟计数器，却不用变量原子，只好「再套一层循环 + 一堆 If」。
    # 12 这个数字来自现状：已评审通过的最长流程是 11 个节点。
    # 纯提示，**不改变生成结果**，只是让人有机会复核。
    #
    # 2026-11-03 收窄（复核待办 11b）：如果计数器已经是「set_variable_value 初始化 +
    # self_calculation_number 自增 + Code.While 判条件」这套正经写法，就不该再提示 ——
    # 那正是原子能表达 B-1 的证据，提示只会变成噪音（`条件分支与While循环小流程` 就属这类，
    # 已真跑 3 轮、Continue 跳过第 2 轮、四份证据文件一致）。
    counter_ok = (COUNTER_INIT_ATOMS & used_atoms
                  and COUNTER_STEP_ATOMS & used_atoms
                  and "Code.While" in used_atoms)
    if len(nodes) >= 12 and nested_loop and not counter_ok:
        # 文案不带 ⚠️（QA/用户 2026-11-02）：和上面所有 warn 保持同一格式，
        # 否则在读 lint 输出时它比真错误还像错误，容易盖住真正的 E。
        warn("原子方案 %d 个节点且有嵌套循环，确认是否存在「累计状态」需求被硬拼"
             "（若命中 B-1 应改用脚本）" % len(nodes),
             "B-1 判定见 docs/流程规格DSL.md §11.1；"
             "标量计数器请用 DataProcess.set_variable_value + "
             "MathProcess.self_calculation_number（那样这条提示会自动消失）")

    # ---------- 脚本模块（Script.module + 顶层 scripts） ----------
    # 放在节点循环之后：这些规则要跨节点（同一脚本可能被多个节点引用），
    # 而且需要「全流程引用了哪些脚本」的全局视角（规则 8）。
    _lint_scripts(spec, _script_table(spec, err), err, warn)

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
