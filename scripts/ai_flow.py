#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 流程交付流水线：自然语言 → 生成 → 校验 → 本地真跑 → 评审单 →（人工通过后）发布。

## 这就是标准作业流程（用户 2026-10-30 明确）

    我发自然语言 → 你生成流程 → 直接校验好 → 通知我
    → 人工运行评审 → 不通过反馈 AI 修改 → 再验证
    → 通过后我让你发布/调用（或你自己处理）

本脚本把「生成 → 校验 → 真跑 → 出评审单」压成一条命令，**AI 每次交付都必须走它**，
不允许「只生成不校验」或「校验过就当能跑」。

## 三个阶段

| 阶段 | 命令 | 副作用 |
| --- | --- | --- |
| `draft` | `--nl "..." --stage draft` | 只写 `docs/generated_flow.json`，不落库、不执行 |
| `review`（默认） | `--nl "..."` | 写进机器人**主流程** + 本地真跑 + 出评审单 |
| `publish` | `--spec flows/x.json --stage publish --version N` | 发布机器人版本（**必须人工评审通过后**才用） |

## 为什么写主流程

设计器点「编辑」默认打开主流程，人工评审就是在那儿点运行。写到子流程用户会看到空白
（2026-10-30 用户实测踩过）。

## 评审反馈怎么走

规格文件是唯一契约：`flows/<流程名>.json`。用户说「第 3 步参数不对」→ 改这个文件 → 重跑
同一条命令。落库是幂等的（覆盖主流程内容），不会产生重复流程。
"""
import argparse
import copy
import hashlib
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import flow_generator as fg
import nl_params as nlp
import self_heal
import publish_flow as pf
import param_budget as pb   # 参数预算（平台缺陷 A20）
import publish_gate as pg   # 发布闸门：默认草稿，发布须显式确认
import version as verchk   # 版本闸门：落后提示，breaking 拒绝运行（别叫 ver：main 里已占用）
import run_flow_local as rfl
import error_hints as eh
import traceback_map as tm

try:
    import requests
except ImportError:  # invoke 阶段才需要
    requests = None

import rpa_config as cfg

ROOT = os.path.dirname(HERE)
SPEC_DIR = os.path.join(ROOT, "flows")
REVIEW_DIR = os.path.join(ROOT, "docs", "reviews")
WATCH_DIRS = [os.path.join(ROOT, "demo")]

# 内嵌脚本落盘位置（`scripts/mod_gen_<脚本名>.py`）：给用户事后查看/改/复用。
# 前 6 个手工脚本叫 `mod_xxx.py`，内嵌脚本统一带 `mod_gen_` 前缀，一眼能分开。
MOD_GEN_PREFIX = "mod_gen_"
# 脚本名 → 平台 moduleId 的本地台账。
#
# 为什么需要台账：`create_module` 每次调用都会**新建**一条模块记录（没有「按名查重」的
# 官方接口），没有台账的话每跑一次 ai_flow 就给同一个机器人生成一堆重复模块。
#
# ⚠️ **台账不是事实来源，只是「省一次 API 调用」的优化**（2026-11-02 用户拍板）。
#    反例：台账里有 id，但模块在平台上已被删掉 → 直接拿 id 去用会在半路报
#    「模块不存在」。所以每次命中缓存前**必须** `pf.open_module()` 探活，
#    读不到就当成 miss 重新建（见 `_ensure_module`）。
#    文件名带 `module` 前缀、放进 logs/ 且已在 .gitignore 里：moduleId 是本环境专属的，
#    换设备/换服务端全是废号，绝不能入库。
SCRIPT_LEDGER = os.path.join(ROOT, "logs", "module_ledger.json")
# 旧路径（历史版本用过）。只读兼容，不再写入，读到就顺手迁到新路径。
SCRIPT_LEDGER_LEGACY = os.path.join(ROOT, "logs", "script_modules.json")

# 原子需求池：脚本 → 原子 的沉淀线索（见 `_atom_demand_update`）
ATOM_POOL = os.path.join(ROOT, "docs", "原子需求池.md")
ATOM_POOL_HEADER = """# 原子需求池（脚本沉淀线索）

> **谁写**：`ai_flow.resolve_scripts()` 每成功上传一个内嵌脚本就自动更新一行（幂等）。
> **为什么**：产品的验收标准是「非专业业务人员能看懂、能做微调」。脚本业务人员看不懂也改不动，
> 所以它只是**兜底**；同一种脚本模式被**多个不同流程**需要 = 这类需求高频 = 应该把它做成**真原子**
> （业务人员直接拖一个节点、填参数就能用）。这样脚本就不是技术债，而是原子需求的**来源**。
>
> **「出现次数」怎么算（重要）**：按**不同流程名**去重计数，不是按运行次数。
> 同一条流程跑 100 次也只算 1 次 —— 那只是「同一个人反复跑」，不构成原子需求；
> 3 条**不同的**流程都需要它，才值得做成原子。
> 「流程名」列用 `、` 分隔记录所有出现过的流程，`首次出现 / 最后出现` 记录时间跨度。
>
> **怎么用**：`出现次数 ≥ 3` 的条目提给开发做原子；做完后把对应流程改回原子拼装，
> 并在「建议沉淀成的原子」列后面标 `（已沉淀）`。
>
> 判定条款（规格里 `scripts.<名>.why` 必须写明命中哪一条，`spec_lint` 会校验）：
> `B-1` 跨迭代累计状态｜`B-2` 输出个数/文件名取决于运行时数据｜
> `B-3` 复杂数据变换（分组/透视/转置/关联）｜`B-4` 精确控制文件格式细节。

| 首次出现 | 最后出现 | 流程名 | 脚本名 | 命中条款 | 建议沉淀成的原子 | 出现次数 |
| --- | --- | --- | --- | --- | --- | --- |
"""
# 「流程名」列里多个流程的分隔符（同时也是去重计数依据）
POOL_FLOW_SEP = "、"


def openapi_base():
    return cfg.openapi_base()


def api_key():
    k = cfg.api_key()
    if not k:
        raise SystemExit(
            "没有 openapi API Key。\n"
            "  在 rpa_config.json 里写 {\"openapi\":{\"api_key\":\"xxx\"}}\n"
            "  或设置环境变量 RPA_API_KEY\n"
            "  Key 在平台「开放平台 / API 密钥」页生成（迁移到新环境要重新生成）。")
    return k


def default_robot_id():
    return cfg.default_robot_id() or "2115990391806255104"



def snapshot(dirs):
    """记录目录快照（mtime+size），用于对比「流程跑了之后改动了什么文件」。

    递归扫描（os.walk），否则「把文件移到子目录」这类流程的产出检测不到
    （只扫顶层会漏掉被归位的分类文件与报告）。
    """
    snap = {}
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for root, _dirs, files in os.walk(d, followlinks=False):
            for name in files:
                p = os.path.join(root, name)
                try:
                    st = os.stat(p)
                    snap[p] = (st.st_mtime, st.st_size)
                except OSError:
                    continue
    return snap


def diff_snapshot(before, after):
    created, changed = [], []
    for p, v in after.items():
        if p not in before:
            created.append(p)
        elif before[p] != v:
            changed.append(p)
    return created, changed


def save_spec(spec):
    """规格即契约：落 flows/<流程名>.json，改流程就是改这个文件。"""
    os.makedirs(SPEC_DIR, exist_ok=True)
    safe = re.sub(r"[^\w\u4e00-\u9fa5-]+", "_", spec.get("name") or "flow")
    path = os.path.join(SPEC_DIR, safe + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=2)
    return path


def node_table(process_content, atom_dict):
    """评审单里的节点表：人能看懂「每一步在干什么、关键参数是什么」。"""
    rows = []
    for i, n in enumerate(process_content, 1):
        atom = atom_dict.get(n.get("key")) or {}
        key_params, vars_out = [], []
        for it in n.get("inputList") or []:
            k = it.get("key")
            if str(k).startswith("__"):
                continue
            if it.get("need_parse") == "json_str":
                # 复杂参数（如 Script.module 的 module_param）值是一段 JSON 字符串，
                # 直接贴原文评审单没法看，这里还原成「绑定了哪些变量」。
                raw = it.get("value")
                try:
                    arr = json.loads(raw) if isinstance(raw, str) else []
                    names = [x.get("varName") for x in arr if isinstance(x, dict) and x.get("varName")]
                    key_params.append(f"{k}=绑定 {', '.join(names)}" if names else f"{k}={str(raw)[:50]}")
                except Exception:
                    key_params.append(f"{k}={str(raw)[:50]}")
                continue
            v0 = it.get("value")
            if isinstance(v0, dict):
                # rpa:special 等引用结构（如 js_run 的 input_type），统一当单段处理
                vals = [v0]
            elif isinstance(v0, list):
                vals = v0
            else:
                # 裸标量（被 dynamics 引用的参数落库为裸值，见 flow_generator.
                # _collect_dynamics_refs）：包成单段 other 结构走统一显示逻辑
                vals = [{"type": "other", "value": v0}] if v0 not in (None, "") else []
            if not (vals and isinstance(vals[0], dict)):
                continue
            if len(vals) > 1:
                # 片段拼接：把各段还原成人能读的表达式，如 `img_dir + "/" + img_prefix + "_1.png"`
                pieces = []
                for piece in vals:
                    t, dv = piece.get("type"), piece.get("value")
                    pieces.append(f"${dv}" if t == "var" else (f"$p:{dv}" if t == "p_var" else str(dv)))
                expr = " + ".join(pieces)
                key_params.append(f"{k}={expr if len(expr) <= 70 else expr[:67] + '...'}")
                continue
            v = vals[0]
            if v.get("rpa") == "special":
                # 变量引用：把内部真实值还原出来（如 input_type 的 content）
                inner = v.get("value")
                if isinstance(inner, list) and inner and isinstance(inner[0], dict):
                    s = str(inner[0].get("value"))
                else:
                    s = str(inner)
                key_params.append(f"{k}={s}")
                continue
            if v.get("type") == "var":
                key_params.append(f"{k}=${v.get('value')}")
            elif v.get("type") == "p_var":
                key_params.append(f"{k}=$p:{v.get('value')}")
            elif v.get("type") == "element":
                key_params.append(f"{k}=[元素]{v.get('value')}")
            elif v.get("value") not in ("", None, 0, False):
                s = str(v.get("value"))
                key_params.append(f"{k}={s if len(s) <= 60 else s[:57] + '...'}")
        for it in n.get("outputList") or []:
            for v in it.get("value") or []:
                if isinstance(v, dict) and v.get("type") == "var":
                    vars_out.append(f"{v.get('value')}")
        rows.append({
            "no": i, "key": n.get("key"), "title": atom.get("title") or n.get("alias"),
            "alias": n.get("alias"), "params": key_params, "outs": vars_out,
        })
    return rows


def write_review_sheet(nl, spec, spec_path, process_content, atom_dict, robot_id,
                       process_id, check, run_result, files_created, files_changed,
                       robot_name="", heal_log=None, nl_notes=None):
    os.makedirs(REVIEW_DIR, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    safe = re.sub(r"[^\w\u4e00-\u9fa5-]+", "_", spec.get("name") or "flow")
    path = os.path.join(REVIEW_DIR, f"{ts}_{safe}.md")
    rows = node_table(process_content, atom_dict)

    L = []
    L.append(f"# 流程评审单：{spec.get('name')}\n")
    L.append(f"> 生成时间 {time.strftime('%Y-%m-%d %H:%M:%S')} · 评审人：用户（人工运行）\n")
    L.append("## 1. 需求（我的原话）\n")
    L.append(f"> {nl or spec.get('name')}\n")
    L.append("## 2. 落库位置\n")
    L.append(f"| 项 | 值 |\n| --- | --- |")
    L.append(f"| 机器人 | `{robot_id}`（设计器里找「{robot_name or '当前机器人'}」） |")
    L.append(f"| 流程 | **主流程** `{process_id}` —— 点「编辑」默认打开的就是它 |")
    L.append(f"| 规格文件 | `{os.path.relpath(spec_path, ROOT)}`（要改流程就改它） |")
    L.append(f"| 节点数 | {len(process_content)} |\n")
    L.append("> 说明：主流程**一次只放一条流程**（设计器「编辑」默认打开它），所以它是「当前评审对象」。"
             "覆盖前旧内容会自动归档到 `flows/_archive/`，不会丢。\n")
    if spec.get("params"):
        L.append("### 流程参数（可在运行时覆盖）\n")
        L.append("| 参数 | 类型 | 默认值 | 说明 |\n| --- | --- | --- | --- |")
        for p in spec["params"]:
            L.append(f"| `{p.get('name')}` | {p.get('type')} | `{p.get('default')}` | {p.get('describe')} |")
        L.append("\n调用时传 `params` 即可覆盖默认值，例如："
                 "`--params '{\"" + (spec['params'][0].get('name') or 'x') + "\":\"新值\"}'`\n")
    L.append("## 3. 流程步骤\n")
    L.append("| # | 原子 | 名称 | 关键参数 | 输出变量 |\n| --- | --- | --- | --- | --- |")
    for r in rows:
        L.append(f"| {r['no']} | `{r['key']}` | {r['title']} | "
                 f"{'<br>'.join(r['params']) or '—'} | {', '.join(r['outs']) or '—'} |")
    L.append("")
    L.append("## 4. 机器自检（AI 已完成，不需要你重复）\n")
    L.append(f"- **结构校验**：`IS_PASS: {'YES ✅' if check['is_pass'] else 'NO ❌'}`"
             f"（错误 {len(check['errors'])} / 警告 {len(check['warnings'])}）")
    for e in check["errors"]:
        L.append(f"  - ❌ {e}")
    for w in check["warnings"]:
        L.append(f"  - ⚠️ {w}")
    if run_result is None:
        L.append("- **本地真跑**：未执行（`--no-run`）")
    elif run_result.get("ok"):
        L.append(f"- **本地真跑**：`{run_result.get('status')}` ✅（{run_result.get('elapsed_sec')}s，"
                 f"引擎编译出 `main.py` {run_result.get('generated', {}).get('main.py', '?')} 字节）")
    else:
        L.append(f"- **本地真跑**：`{run_result.get('status')}` ❌ —— {run_result.get('error') or '见日志'}")
        for e in (run_result.get("errors") or [])[:5]:
            L.append(f"  - {e}")
        if run_result.get("deep"):
            _d = run_result["deep"]
            _n = _d.get("node") or {}
            _a = _d.get("atomic") or {}
            if _n.get("node_no"):
                _loc = f"第 {_n['node_no']} 个「{_n.get('alias') or ''}」"
                _loc += f"（`{_n.get('atom_key') or ''}`）"
                _loc += f"，编译产物 `{_n.get('file')}:{_n.get('line')}`"
                if _a.get("file"):
                    _loc += f" → 平台实现 `{_a['file']}:{_a['line']} ({_a.get('func')})`"
                L.append(f"  - 🔍 **失败节点**：{_loc}")
                _root = _d.get("root_exception") or ""
                if _root and _root != _d.get("final_exception"):
                    L.append(f"    - 底层原始异常（真因）：`{_root[:220]}`")
                if _d.get("caution"):
                    L.append(f"    - ⚠️ {_d['caution']}")
        if run_result.get("hints"):
            L.append("- **真因翻译**（平台文案常误导，以下才是实际原因）：")
            for h in run_result["hints"]:
                L.append(f"  - ❌ **{h['title']}**")
                L.append(f"    - 为什么：{h['cause']}")
                L.append(f"    - 怎么修：{h['fix']}")
                if h.get("hit"):
                    L.append(f"    - 日志片段：`{h['hit']}`")
    if files_created:
        L.append(f"- **本次执行新建的文件**：{', '.join(os.path.basename(f) for f in files_created)}")
    if files_changed:
        L.append(f"- **本次执行改动的文件**：{', '.join(os.path.basename(f) for f in files_changed)}")
    if nl_notes:
        L.append("- **参数来自你的这句话**（不是规格里的示例值）：")
        for k, v in nl_notes.items():
            L.append(f"  - `{k}`：{v['old'] or '(空)'} → **{v['new']}**（{v['why']}）")
    if heal_log:
        L.append("- **AI 自愈记录**（失败后自己改的，你看一下对不对）：")
        for h in heal_log:
            L.append(f"  - {h}")
    L.append("")
    L.append("## 5. 请你评审（人工运行）\n")
    L.append(f"打开设计器 → 机器人「{robot_name or '当前机器人'}」→ 点「编辑」→ 顶部「运行」，然后确认：\n")
    L.append("1. **能跑通**：不报错、走到最后一步。")
    L.append("2. **输入对**：打开的源文件是不是你想的那份。")
    L.append("3. **输出对**：产出的文件位置、内容是否符合预期。")
    L.append("4. **干净**：跑完后 Excel/浏览器有没有残留窗口或 `~$xxx` 锁文件。")
    L.append("")
    L.append("## 6. 不通过怎么反馈\n")
    L.append("直接说人话即可，例如：\n")
    L.append("- 「第 2 步不要读 Sheet1，读 Sheet2」")
    L.append("- 「输出文件名改成 月度导出」")
    L.append("- 「最后要关掉结果表」\n")
    L.append(f"我会改 `{os.path.relpath(spec_path, ROOT)}` 后重跑同一条命令，重新出一份评审单。")
    L.append("落库是幂等的，不会产生重复流程。\n")
    L.append("## 7. 通过之后\n")
    L.append("你说一声，我再执行：\n")
    L.append("- **发布**：`python scripts/ai_flow.py --spec <规格> --stage publish --version N`")
    L.append("- **调用**：走 openapi 需要先让机器人进可执行列表（当前未打通，见交接清单 §3）\n")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    return path


def do_invoke(project_id, params=None, timeout=180):
    """用 API Key 通过 openapi 触发执行并等结果。

    这是「AI 真正调用 RPA」的入口：只要机器人已发布版本、并登记进 `openai_workflows`，
    API Key 就能触发。执行的是**已发布版本的快照**（不是设计稿 v0）。
    """
    if requests is None:
        return {"ok": False, "error": "缺少 requests 库"}
    H = {"X-API-Key": api_key()}
    before = snapshot(WATCH_DIRS)
    r = requests.post(f"{openapi_base()}/workflows/execute-async",
                      json={"project_id": str(project_id), "params": params or {}},
                      headers=H, timeout=30)
    if r.status_code not in (200, 202):
        return {"ok": False, "error": f"触发失败 http={r.status_code} {r.text[:200]}"}
    eid = (r.json().get("data") or {}).get("executionId")
    if not eid:
        return {"ok": False, "error": f"未返回 executionId：{r.text[:200]}"}
    print(f"      executionId={eid}，轮询中...")
    d, waited = {}, 0
    while waited < timeout:
        resp = requests.get(f"{openapi_base()}/executions/{eid}", headers=H, timeout=20)
        d = (resp.json().get("data") or {}).get("execution") or {}
        st = d.get("status")
        if st in ("COMPLETED", "FAILED", "CANCELLED", "TIMEOUT"):
            break
        time.sleep(2)
        waited += 2
    after = snapshot(WATCH_DIRS)
    created, changed = diff_snapshot(before, after)
    out = {
        "ok": d.get("status") == "COMPLETED",
        "execution_id": eid, "status": d.get("status"), "version": d.get("version"),
        "result": d.get("result"), "error": d.get("error"),
        "created": created, "changed": changed,
        "start": d.get("start_time"), "end": d.get("end_time"),
    }
    # 失败时补具体原因：openapi 只回「执行错误」，详情在 robot_execute_record.execute_log
    if not out["ok"]:
        try:
            out["detail"] = pf.get_last_execute_error(project_id)
        except Exception as e:
            out["detail"] = {"error": str(e)}
    return out


def _script_refs(spec):
    """找出规格里所有「引用内嵌脚本」的节点，返回 `[(节点序号, 节点dict, 脚本名)]`。

    判定条件：`atom == "Script.module"` 且 `inputs["content"]` 形如 `$script:名字`。
    历史写法（`content` 是纯 moduleId 或 `$p:module_id`）**一律不在返回里** —— 那条路
    走 `make_module.py` + 流程参数，必须继续有效。
    """
    refs = []
    for i, n in enumerate(spec.get("nodes") or [], 1):
        if not isinstance(n, dict) or (n.get("atom") or "") != "Script.module":
            continue
        content = (n.get("inputs") or {}).get("content")
        if isinstance(content, str) and content.startswith(fg.SCRIPT_REF_PREFIX):
            name = content[len(fg.SCRIPT_REF_PREFIX):].strip()
            refs.append((i, n, name))
    return refs


def _module_param_id_check(pc):
    """检查落库节点树里 `Script.module` 的 `varId` 是否都有值，返回缺 id 的 `[(序号, 参数名)]`。

    为什么必须查（2026-11-03 定稿）：设计器打开脚本节点时按 `varId` 去 `c_param` 取参数
    定义；`varId` 为空 = 那一行参数在设计器里认不到、改不了值。结构对不对用户是看不出来的
    —— 只有「点开节点发现参数表是空的/报解析失败」才会暴露，所以要在落库前自己先看一眼。
    """
    missing = []
    for i, n in enumerate(pc or [], 1):
        if not isinstance(n, dict) or (n.get("key") or "") != "Script.module":
            continue
        for it in (n.get("inputList") or []):
            if not isinstance(it, dict) or it.get("key") != "module_param":
                continue
            for p in (it.get("value") or []):
                if isinstance(p, dict) and not str(p.get("varId") or "").strip():
                    missing.append((i, p.get("varName") or "?"))
    return missing


def _script_param_defs(spec, name):
    """把脚本的参数声明转成**模块级参数定义**（`c_param`，`module_id=<moduleId>`）需要的形态。

    返回 `[{"name","type","default","describe"}]`。

    数据来源（优先级从高到低）：
      1. `scripts.<脚本名>.params`：显式声明，能带中文说明 —— **推荐**，AI 生成规格时会写；
      2. 引用该脚本的节点的 `module_param`：只有 varName 和默认值，说明从流程 `params`
         里按同名参数借（`$p:x` 时能对上）。

    为什么默认值要在这里定死：`c_param.var_value` 是设计器参数表格里**初始显示的值**，
    跟节点 `module_param[].varValue` 是两份数据。两边取自同一个 `$p:` 默认值才不会
    「表格里显示 A、实际跑的是 B」。

    含循环变量（`$var:`）的参数无法有静态默认值 → 默认空串（运行时由引擎求值覆盖）。
    """
    flow_params = spec.get("params") or []
    pdesc = {p.get("name"): p.get("describe") or "" for p in flow_params if p.get("name")}
    pdef = {p.get("name"): p.get("default") for p in flow_params if p.get("name")}

    declared = ((spec.get("scripts") or {}).get(name) or {}).get("params") or []
    if declared:
        out = []
        for d in declared:
            nm = (d.get("name") or "").strip() if isinstance(d, dict) else ""
            if not nm:
                continue
            dv = d.get("default", pdef.get(nm, ""))
            out.append({"name": nm, "type": d.get("type") or "Str",
                        "default": "" if dv is None else dv,
                        "describe": d.get("describe") or pdesc.get(nm, "")})
        return out

    for n in spec.get("nodes") or []:
        inputs = n.get("inputs") or {}
        if str(inputs.get("content") or "").strip() != fg.SCRIPT_REF_PREFIX + name:
            continue
        mp = inputs.get("module_param")
        payload = mp
        if isinstance(mp, str) and mp.startswith("$json:"):
            try:
                payload = json.loads(mp[6:])
            except Exception:
                payload = []
        out = []
        for it in (payload or []):
            if not isinstance(it, dict) or not it.get("varName"):
                continue
            nm = it["varName"]
            try:
                dv = fg._bake_refs(it.get("varValue"), pdef)
            except Exception:
                dv = ""            # 含循环变量：运行时才知道值
            if dv is None:
                dv = ""
            if not isinstance(dv, (str, int, float, bool)):
                dv = json.dumps(dv, ensure_ascii=False)
            out.append({"name": nm, "type": "Str", "default": dv,
                        "describe": pdesc.get(nm, "")})
        return out
    return []


def _ledger_load():
    """读脚本模块台账；坏了就当空（台账只是省一次建模块，不该阻塞主链路）。

    新路径 `logs/module_ledger.json` 不存在时回落到旧路径 `logs/script_modules.json`，
    这样从老版本升上来的机器不会白建一遍模块。
    """
    for path in (SCRIPT_LEDGER, SCRIPT_LEDGER_LEGACY):
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        if isinstance(d, dict):
            return d
    return {}


def _ledger_save(d):
    try:
        os.makedirs(os.path.dirname(SCRIPT_LEDGER), exist_ok=True)
        with open(SCRIPT_LEDGER, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
    except OSError as e:
        print(f"      ⚠️ 台账写入失败（不影响本次运行）：{e}")


def _dump_script(name, code):
    """把内嵌脚本落盘到 `scripts/mod_gen_<名字>.py`，返回路径（失败返回空串）。

    **落盘失败不阻塞**：平台模块才是真正执行的东西，本地文件只是给用户看/改的副本。
    文件头注明「改这里不生效」，避免用户改了本地 py 却发现流程没变（这是最容易踩的坑）。
    """
    safe = re.sub(r"[^\w\u4e00-\u9fa5-]+", "_", name or "script")
    path = os.path.join(HERE, MOD_GEN_PREFIX + safe + ".py")
    try:
        with open(path, "w", encoding="utf-8") as f:
            if not code.lstrip().startswith("#!"):
                f.write("# -*- coding: utf-8 -*-\n")
                f.write(f"# 由 ai_flow.resolve_scripts 从规格 scripts.{name}.code 自动落盘。\n")
                f.write("# 用途：让人能直接看/改/复用这段代码。\n")
                f.write("# 注意：改这个文件**不会**影响流程 —— 平台上的模块是独立副本；\n")
                f.write("#       要改行为请改规格里的 scripts 代码后重跑 ai_flow.py。\n")
            f.write(code if code.endswith("\n") else code + "\n")
    except OSError as e:
        print(f"      ⚠️ 脚本 {name} 落盘失败（不阻塞上传）：{e}")
        return ""
    return path


def _create_module_unique(ck, robot_id, code, name):
    """建模块；平台要求「同一机器人下模块名唯一」，重名时自动加后缀重试。

    ## 为什么需要（2026-11-02 实测踩到）

    `create_module` 撞名会直接 `500003 名称重复，请重新命名`。而「重名」是**必然**会发生的：
    台账 `logs/module_ledger.json` 在 .gitignore 里（换机器/清了 logs 就没了），
    或者台账里的 id 探活失败（模块被人在平台上删了）—— 这两种情况都要重建模块，
    而旧模块（或同名的历史模块）还在，名字就被占住了。

    名字只是给人看的显示名（执行走 moduleId），带个后缀完全不影响运行。
    所以这里按 `名字` → `名字_2` → `名字_3` … 重试，绝不因为「名字重复」把整条流水线打死。
    """
    base = (name or "脚本模块").strip() or "脚本模块"
    cands = [base] + ["%s_%d" % (base, i) for i in range(2, 6)]
    last = ""
    for nm in cands:
        try:
            return pf.create_module(ck, robot_id, code, nm)
        except SystemExit as e:                     # pf.ok 用 SystemExit 报错
            last = str(e)
            if "重复" not in last:
                raise                               # 不是撞名（网络/权限…）就别重试
            print("      · 模块名「%s」已被占用，换名重试" % nm)
    raise SystemExit("建模块失败：换到第 %d 个名字仍重复（%s）" % (len(cands), last))


def _ensure_module(ck, robot_id, scope, name, item, code):
    """拿到平台模块 id（必要时新建 / 更新内容），返回 `(moduleId, 说明)`。

    台账 key 带上 `scope`（流程名）：同名脚本出现在两条流程里时**各自建一份**，
    绝不复用 —— 否则改 A 流程的脚本会把 B 流程的模块内容覆盖掉。
    """
    led = _ledger_load()
    key = "%s|%s|%s" % (robot_id, scope or "-", name)
    digest = hashlib.md5(code.encode("utf-8")).hexdigest()
    rec = led.get(key) or {}
    mid = str(rec.get("module_id") or "")

    alive = False
    if mid:
        try:
            alive = pf.open_module(ck, robot_id, mid) is not None
        except (SystemExit, Exception):
            alive = False

    if alive and rec.get("hash") == digest:
        return mid, "（内容未变，复用已有模块）"
    if alive:
        pf.save_module(ck, robot_id, mid, code)
        how = "（内容有变，已覆盖模块内容）"
    else:
        mid = _create_module_unique(ck, robot_id, code, item.get("name") or name)
        # 台账有 id 但探活失败 = 模块在平台上被删了 / 属于别的环境 / id 被手改坏了。
        # 这种情况**必须**新建：宁可多一条模块记录，也不能让流程跑到一半报「模块不存在」。
        how = "（新建；台账旧 id 已失效，探活确认后重建）" if rec.get("module_id") else "（新建）"

    led[key] = {"module_id": mid, "hash": digest, "robot_id": str(robot_id),
                "script": name, "flow": scope or "",
                "updated": time.strftime("%Y-%m-%d %H:%M:%S")}
    _ledger_save(led)
    return mid, how


def _demand_clause(why):
    """从 `why` 里抠出命中的判定条款（B-1~B-4）；写不出就是「未标注」。

    条款定义见 `docs/流程规格DSL.md §11.1`：B-1 跨迭代累计状态 / B-2 输出个数取决于数据 /
    B-3 复杂数据变换 / B-4 精确格式控制。抠不出来说明这条脚本很可能是「模型偷懒选脚本」。
    """
    m = re.search(r"B-[1-4]", str(why or ""))
    return m.group(0) if m else "未标注"


def _atom_demand_update(flow_name, script_name, item):
    """把「这次用了脚本」记进 `docs/原子需求池.md`，返回 `(判定条款, 去重后的流程数)`。

    ## 去重规则（2026-11-02 用户拍板改过一次，现在的才算对）

    行按「**脚本名 + 命中条款**」唯一（换条款算新需求：同一个脚本被用来干两类事，
    说明该拆成两个原子）。但「出现次数」**不是运行次数**，而是**不同流程名的个数**：

        同一流程跑 100 次 → 只算 1 次（同一个人反复跑，不构成原子需求）
        3 条不同流程都要它 → 算 3 次（这才是「这类需求高频」）

    所以流程名写成一列、用 `、` 串起来（`POOL_FLOW_SEP`），次数 = 名字个数。
    同时记「首次出现 / 最后出现」，让人看得出这个需求是刚冒出来还是攒了半年。

    ## 为什么记这个

    脚本是兜底手段，业务人员改不动。同一个模式被 ≥3 条流程需要就说明这类需求高频，
    应做成真原子（业务人员拖一个节点填参数就能用）。这一步把「脚本」从技术债变成
    「原子需求的来源」。
    """
    item = item or {}
    clause = _demand_clause(item.get("why"))
    suggestion = str(item.get("atom_suggestion") or "").strip() \
        or ("把「%s」做成原子" % (item.get("name") or script_name))
    flow = (flow_name or "").strip() or "（未命名）"
    today = time.strftime("%Y-%m-%d")

    lines = []
    if os.path.exists(ATOM_POOL):
        try:
            with open(ATOM_POOL, "r", encoding="utf-8") as f:
                lines = f.read().splitlines()
        except OSError as e:
            print(f"      ⚠️ 原子需求池读取失败（不阻塞）：{e}")
            return clause, 0
    if not lines:
        lines = ATOM_POOL_HEADER.splitlines()

    hit_idx, count = None, 1
    for i, ln in enumerate(lines):
        if not ln.startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) == 7:
            # 新格式：| 首次出现 | 最后出现 | 流程名 | 脚本名 | 命中条款 | 建议 | 次数 |
            first, last, flows, sname, cl, sug, cnt = cells
            if sname != script_name or cl != clause:
                continue
            names = [x for x in flows.split(POOL_FLOW_SEP) if x]
            if flow not in names:
                names.append(flow)
            count = max(1, len(names))
            cells = [first, today, POOL_FLOW_SEP.join(names), sname, cl, sug or suggestion,
                     str(count)]
        elif len(cells) == 6:
            # 老格式（日期 | 流程名 | 脚本名 | 条款 | 建议 | 次数）：读到就顺手升级成新格式。
            # 老格式的次数是「运行次数」，语义不对，一律按「不同流程名个数」重算。
            date, flows, sname, cl, sug, _cnt = cells
            if sname != script_name or cl != clause:
                continue
            names = [x for x in flows.split(POOL_FLOW_SEP) if x]
            if flow not in names:
                names.append(flow)
            count = max(1, len(names))
            cells = [date, today, POOL_FLOW_SEP.join(names), sname, cl, sug or suggestion,
                     str(count)]
        else:
            continue
        lines[i] = "| " + " | ".join(cells) + " |"
        hit_idx = i
        break
    if hit_idx is None:
        lines.append("| %s | %s | %s | %s | %s | %s | 1 |"
                     % (today, today, flow, script_name, clause, suggestion))

    try:
        os.makedirs(os.path.dirname(ATOM_POOL), exist_ok=True)
        with open(ATOM_POOL, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as e:
        print(f"      ⚠️ 原子需求池写入失败（不阻塞）：{e}")
        return clause, count
    return clause, count


def resolve_scripts(ck, robot_id, spec, verbose=True, dry=False):
    """把规格里 `$script:名字` 的内嵌 Python 脚本变成平台模块，并把真实 moduleId 写回节点。

    ## 为什么需要这一步（这是「AI 会写脚本」能力链的最后一环）

    `Script.module` 节点的 `content` 要的是**服务端 moduleId**（雪花号）。AI 写规格时
    不可能知道这个号（模块还没建）。所以规格里先写占位符 `"content": "$script:脚本名"`，
    代码放在顶层 `scripts` 里；本函数负责：

        遍历节点 → 取 scripts[名字].code → 落盘 scripts/mod_gen_<名字>.py
        → create_module（或复用/更新）→ 把 content 换成真实 moduleId
        → 记到 spec["_resolved_modules"]（发布时要用它把模块复制进版本）
        → 记一行到 docs/原子需求池.md（脚本是兜底，反复出现就该做成真原子）

    ## 调用时机（关键）

    必须在 **robot_id 已确定、`build_process_content()` 之前**：节点树的 `content`
    要先换成真 moduleId，否则落库的是一个字符串占位符，设计器里点运行必报错。

    ## 参数

    ck       : `pf.load_session()` 的结果（`dry=True` 时可以为 None）
    robot_id : 目标机器人
    spec     : 规格 dict，本函数会**就地修改** `nodes[].inputs["content"]`
    verbose  : 打印进度
    dry      : True = 只落盘 + 校验引用，不建模块（`--stage draft` 用，避免留下孤儿模块）

    返回：本次涉及的 moduleId 列表（去重、保持顺序）。
    """
    scripts = spec.get("scripts") or {}
    refs = _script_refs(spec)
    if not refs:
        if scripts and verbose:
            print("      ⚠️ 规格定义了脚本 %s，但没有任何节点用 "
                  '"content": "$script:名字" 引用它们（写好的脚本不会被执行）'
                  % list(scripts))
        return []

    # 1) 引用完整性：节点引用的脚本必须在顶层 scripts 里，且代码非空
    missing = []
    for _i, _n, name in refs:
        if not name:
            raise SystemExit("有节点写了 \"content\": \"$script:\" 但脚本名为空")
        if name not in scripts:
            missing.append(name)
    if missing:
        raise SystemExit(
            "节点引用了内嵌脚本 %s，但规格顶层 `scripts` 里没有：\n"
            "  可用脚本名：%s\n"
            '  修法：在 spec 顶层加 "scripts": {"%s": {"code": "def main(args): ..."}}'
            % (sorted(set(missing)), ", ".join(scripts) or "（无）", missing[0]))

    # 2) 每个脚本只处理一次（多个节点可以共用同一个脚本）
    resolved, order = {}, []
    param_ids = {}          # moduleId → {参数名: c_param.id}，供 build_process_content 填 varId
    for name in [x[2] for x in refs]:
        if name in resolved or name in order:
            continue
        order.append(name)

    for name in order:
        item = scripts.get(name) or {}
        if isinstance(item, str):        # 容忍规格里简写成字符串代码（load_spec 会规范化）
            item = {"code": item}
        code = str(item.get("code") or "")
        if not code.strip():
            raise SystemExit("内嵌脚本 %s 的 code 是空的（必须给完整可运行源码）" % name)
        path = _dump_script(name, code)
        if dry:
            resolved[name] = ""
            if verbose:
                print("      · [draft] 脚本 %s 已落盘 %s，跳过上传（draft 不产生服务端副作用）"
                      % (name, os.path.relpath(path, ROOT) if path else "(落盘失败)"))
            continue
        mid, how = _ensure_module(ck, robot_id, spec.get("name") or "", name, item, code)
        resolved[name] = mid
        # 同步**模块级参数定义**（c_param, module_id=<mid>）—— 这是设计器参数表格的数据源，
        # 缺了它就「点开节点参数是空的 / 顶栏报解析失败」（2026-11-03 用户实测的那个 bug）。
        pdefs = _script_param_defs(spec, name)
        sync = pf.sync_module_params(ck, robot_id, mid, pdefs)
        param_ids[mid] = sync["ids"]
        clause, cnt = _atom_demand_update(spec.get("name") or "", name, item)
        if verbose:
            print("[2.5/6] 上传 Python 子流程模块：%s → moduleId=%s（%d 字符）%s"
                  % (name, mid, len(code), how))
            if path:
                print("        本地副本：%s" % os.path.relpath(path, ROOT))
            if pdefs:
                print("        模块参数 %d 个（设计器参数表格里可改）：%s"
                      % (len(param_ids[mid]),
                         "、".join("%s=%s" % (d["name"], d["default"]) for d in pdefs)))
                if sync.get("extra_on_server"):
                    print("        ⚠️ 平台上有脚本没用到的旧参数：%s（保留未删）"
                          % "、".join(sync["extra_on_server"]))
            else:
                print("        ⚠️ 这个脚本没有声明任何参数 → 设计器里没有可改项"
                      "（要可调就给 scripts.%s.params 或节点 module_param 加参数）" % name)
            note = "  ⚠️ ≥3 条流程都需要 → 建议沉淀成原子" if cnt >= 3 else ""
            print("        脚本是兜底手段：%s（%d 条不同流程都需要它，已记 docs/原子需求池.md）%s"
                  % (clause, cnt, note))

    # 3) 把真实 moduleId 写回节点（dry 模式保留 $script: 占位符，便于人工核对）
    if not dry:
        for i, node, name in refs:
            mid = resolved.get(name) or ""
            if not mid:
                raise SystemExit("脚本 %s 没有拿到 moduleId（第 %d 个节点）" % (name, i))
            node.setdefault("inputs", {})["content"] = mid
        mids = []
        for name in order:
            if resolved[name] and resolved[name] not in mids:
                mids.append(resolved[name])
        spec["_resolved_modules"] = mids
        spec["_resolved_param_ids"] = param_ids     # {moduleId: {参数名: c_param.id}}
        return mids
    return []


def module_ids_of(spec):
    """从规格里找出 Python 子流程模块 id（发布时用来核对模块是否被带进版本）。

    两个来源（合并、去重、保持顺序）：

    1. **流程参数**（历史路径）：模块 id 做成流程参数（如 `module_id`），迁移时只改这个
       默认值。`make_module.py` 写回的就是这里，必须继续有效。
    2. **内嵌脚本解析结果**（新路径）：`resolve_scripts()` 把 `$script:名字` 换成的真
       moduleId 记在 `spec["_resolved_modules"]`。这条路径**不往流程参数表里塞 digit
       参数** —— 参数表要登记进 openapi，塞个没人会用的数字参数只会污染接口 schema。
    """
    out = []
    for p in spec.get("params") or []:
        name = (p.get("name") or "").lower()
        val = str(p.get("default") or "").strip()
        if "module" in name and val.isdigit():
            out.append(val)
    for m in spec.get("_resolved_modules") or []:
        m = str(m or "").strip()
        if m and m not in out:
            out.append(m)
    return out


def refresh_modules(cookie, robot_id, spec):
    """发布前把平台上的脚本模块内容刷成规格里的最新代码，返回刷新的个数。

    ## 为什么发布前还要刷一次（2026-11-02 用户指出，P4）

    发布版本时**平台按 `creator_id` 复制模块行**到新版本。如果本地规格里的 `code` 后来
    改过、但没走过 `resolve_scripts`（例如改了 flows/*.json 直接 `--stage expose`），
    平台复制的就是**旧内容** —— 发布出来的版本与代码不一致，最难查的那种问题。
    所以发布前无条件按规格重刷一遍，保证「发布的东西 == 规格里的代码」。

    ## 为什么失败必须报错停（不许 warn 就行）

    这里是发布前的最后一道闸。刷失败还继续发布 = 明确交付一个和代码不一致的版本，
    比直接失败坏得多（用户会以为已发布成功）。所以这里**抛 SystemExit 让整条命令停下来**。

    返回：刷新成功的模块个数（没有内嵌脚本时返回 0）。
    """
    scripts = spec.get("scripts") or {}
    if not scripts:
        return 0
    led = _ledger_load()
    scope = spec.get("name") or ""
    n = 0
    for name, item in scripts.items():
        if isinstance(item, str):          # 容忍简写（load_spec 已规范化，这里兜一层）
            item = {"code": item}
        code = str((item or {}).get("code") or "")
        if not code.strip():
            raise SystemExit("发布前刷新模块失败：脚本 %s 的 code 是空的，拒绝发布" % name)
        key = "%s|%s|%s" % (robot_id, scope, name)
        rec = led.get(key) or {}
        mid = str(rec.get("module_id") or "")
        if not mid:
            raise SystemExit(
                "发布前刷新模块失败：台账里没有 %s 的 moduleId，说明这条规格还没走过 [2.5/6] 上传。\n"
                "  修法：先跑 `python scripts/ai_flow.py --spec <规格> --robot-name <机器人名>` "
                "让它把模块建出来，再 --stage expose" % name)
        if pf.open_module(cookie, robot_id, mid) is None:
            raise SystemExit("发布前刷新模块失败：模块 %s（%s）在平台上打不开，拒绝发布旧代码"
                             % (name, mid))
        pf.save_module(cookie, robot_id, mid, code)
        rec["hash"] = hashlib.md5(code.encode("utf-8")).hexdigest()
        rec["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        led[key] = rec
        n += 1
        print("      · 已刷新模块 %s（%s，%d 字符）" % (name, mid, len(code)))
    _ledger_save(led)
    return n


def do_expose(cookie, robot_id, spec, process_id, repair_modules=False):
    """发布版本 + 登记进 openapi 列表 —— 做完这两步，API Key 才能调用。

    注意：`parameters` 必须把**真实流程参数表**登记进去，否则 openapi 会按空 schema
    校验入参，传任何参数都报 `403 Arguments do not match the tool input schema`。
    """
    mids = module_ids_of(spec)
    # 发布前刷新（P4）：平台发布会按 creator_id 复制模块行，复制的必须是**当前代码**。
    # 失败直接停（refresh_modules 内部抛 SystemExit），绝不「告警后继续发旧代码」。
    if spec.get("scripts"):
        print("      发布前刷新脚本模块内容（平台按当前内容复制进版本）...")
        refresh_modules(cookie, robot_id, spec)
    # 参数体检（平台缺陷 A20）：发布前按服务端真实参数算 paramJson 长度，超线直接不发。
    # 发出去之后再发现，就得面对「添加应用列表 999999」，而且只有发布者账号能复现。
    if process_id:
        pf.guard_param_budget(cookie, robot_id, process_id, strict=True)
    v = pf.publish_auto(cookie, robot_id, spec.get("name") or "AI生成流程",
                        introduction=(spec.get("nl_examples") or ["AI 生成流程"])[0],
                        module_ids=mids, repair_modules=repair_modules, confirm=True)
    print(f"      已发布版本 v{v}")
    params = pf.list_params(cookie, robot_id, process_id) if process_id else []
    pj = json.dumps([{k: p.get(k) for k in
                      ("varDirection", "varName", "varType", "varValue", "varDescribe")}
                     for p in params], ensure_ascii=False) if params else "[]"
    r = pf.upsert_openapi_workflow(cookie, robot_id, spec.get("name") or "AI生成流程", v,
                                   description=(spec.get("nl_examples") or [""])[0],
                                   parameters=pj)
    print(f"      已登记 openapi 列表：{r.get('action')}（version={r.get('version')}, "
          f"status={r.get('status')}, 参数 {len(params)} 个）")
    return v


def resolve_robot_by_name(name):
    """按名字找机器人，找不到就新建，返回 robotId。

    ## 为什么需要这个（换设备的第一道坎）

    机器人 id 是平台生成的雪花号，换一台机器/换一个服务端就全变；而「机器人名」
    是稳定的。老做法要人先去设计器建机器人、抄 id、再 `--robot-id`，换设备必卡这里。
    现在 `--robot-name` 一步到位：有就复用，没有就建。

    命名约定（历史遗留，别改）：早期批量建机器人时把规格名的「小流程」后缀去掉了，
    所以 `Excel统计行列小流程.json` 对应机器人「Excel统计行列」。这里两种都试，
    优先精确匹配，再试去后缀，最后才新建（新建用传入的名字）。
    """
    cands = [name]
    if name.endswith("小流程"):
        cands.append(name[: -len("小流程")])
    else:
        cands.append(name + "小流程")

    ck = pf.load_session()
    for cand in cands:
        rid = pf.find_robot(ck, cand)
        if rid:
            print(f"      机器人「{cand}」已存在：{rid}")
            return rid

    rid = pf.create_robot(ck, name)
    print(f"      新建机器人「{name}」：{rid}")
    return rid


def robot_name_of(cookie, robot_id):
    """按 robot_id 反查机器人显示名（评审单里要写清楚去设计器找哪个机器人）。"""
    try:
        d = pf.ok(pf.api("/api/robot/robot-design/design-list",
                         {"pageNo": 1, "pageSize": 200}, cookie), "列机器人")
        recs = d.get("records") if isinstance(d, dict) else (d or [])
        for x in recs or []:
            if str(x.get("robotId")) == str(robot_id):
                return x.get("robotName") or x.get("name") or ""
    except Exception:
        pass
    return ""


def main():
    ap = argparse.ArgumentParser(description="AI 流程交付流水线（生成→校验→真跑→评审单→发布）")
    ap.add_argument("--nl", default="", help="自然语言需求（如「把订单表导成新文件」）")
    ap.add_argument("--spec", default="", help="流程规格 JSON（AI 产出契约）；与 --nl 二选一")
    ap.add_argument("--robot-id", default=default_robot_id(), help="目标机器人（默认取 rpa_config.json 的 defaults.robot_id）")
    ap.add_argument("--process-name", default="", help="流程名，默认用规格里的 name")
    ap.add_argument("--stage", choices=["draft", "review", "publish", "expose", "invoke"], default=pg.DEFAULT_STAGE,
                    help="draft=只生成校验（**默认**，零写操作） / review=写设计态草稿+真跑+评审单 / "
                         "publish=发布版本（需 --confirm-publish） / "
                         "expose=发布+登记 openapi 列表（需 --confirm-publish） / invoke=用 API Key 调用执行")
    ap.add_argument("--confirm-publish", action="store_true",
                    help="确认要发布到正式服。默认不带：publish/expose 一律拦下。"
                         "只有用户明确要求「发布」时才加，AI 不得自行决定")
    ap.add_argument("--prep-only", action="store_true",
                    help="等价于 --stage draft（只生成+校验，不上传模块）。"
                         "⚠️ 名字叫 prep 但它**仍然会**解析/创建机器人（--robot-name 时），"
                         "所以平台上可能多出一条机器人记录；真正的「零副作用」做不到")
    ap.add_argument("--version", type=int, default=0, help="publish 阶段的版本号（需大于最新）")
    ap.add_argument("--params", default="", help="invoke 阶段的执行参数（JSON 字符串）")
    ap.add_argument("--no-run", action="store_true", help="跳过本地真跑")
    ap.add_argument("--skip-preflight", action="store_true",
                    help="跳过运行前预检（路径存在性/目录可写/目标文件占用）")
    ap.add_argument("--repair-modules", action="store_true",
                    help="发布时用 SQL 补丁把模块复制到发布版本（只给历史手工插入的模块用，需 db.enabled）")
    ap.add_argument("--offline", action="store_true", help="用本地缓存原子字典（不连 .130）")
    ap.add_argument("--skip-batch-check", action="store_true",
                    help="话里是批量意图时也继续（默认拦下，因为单文件流程只会处理一个文件）")
    ap.add_argument("--skip-element-pick", action="store_true",
                    help="不自动拾取元素（规格里的 element_requirements 只检查不创建）")
    ap.add_argument("--no-nl-params", action="store_true",
                    help="不从自然语言里抽参数（全部用规格默认值）")
    ap.add_argument("--robot-name", default="",
                    help="按名字找机器人，找不到就新建（换设备时不用记 robotId）。"
                         "省略则用 --robot-id / rpa_config 的 defaults.robot_id")
    ap.add_argument("--skip-update-check", action="store_true",
                    help="跳过版本自检（离线排障 / 故意用旧版对照时用）")
    args = ap.parse_args()

    # `--prep-only` 只是 `--stage draft` 的更好听的名字（名字来自用户建议：draft 听起来像
    # 「不产生副作用」，其实它会解析/创建机器人）。两者行为完全一致，别让它变成第二套逻辑。
    if args.prep_only:
        args.stage = "draft"

    # 版本闸门（多人协作，2026-11-05）：**必须在任何网络/写操作之前**。
    # 只读 1KB 的 version.json（24h 缓存），不拉仓库；取不到就当离线，静默放行。
    # 落后 → 打一行提示；breaking / 低于 min_supported → 拒绝运行（退出码 12）。
    # 为什么值得拦：旧脚本按新契约跑，产出的流程可能读不回来，而且**当场看不出来**。
    if not args.skip_update_check:
        _line, _must = verchk.banner()
        if _line:
            print(_line)
            print()
        if _must:
            print("   本轮未执行任何操作。更新后重跑同一条命令即可。")
            return 12

    # 发布闸门（2026-11-05，A20 复盘）：必须在**任何网络/写操作之前**拦。
    # 证据：robot_execute.create_time 与 robot_version.publish 时间逐秒一致
    # （Py测试 03:20:03、MES 02:18:56）→ 发布这一步就会把机器人送进「添加应用」列表，
    # 而列表会把 paramJson 整段塞进响应、过日志脱敏正则 → 参数多就爆栈。
    # 所以「草稿随便写，发布必须人点头」。
    pg.require(args.stage, args.confirm_publish)


    robot_id = args.robot_id
    print("=" * 64)
    print("AI 流程交付流水线")
    print("=" * 64)

    # 换设备后 robotId 全变，但「机器人名」是稳定的。给了 --robot-name 就先按名字解析，
    # 解析结果覆盖 robot_id（找不到会新建），这样新机器上一条命令就能跑。
    if args.robot_name:
        robot_id = resolve_robot_by_name(args.robot_name)

    # ---------- 1. 取规格：自然语言 → 已有规格 / 模板 / 直接读规格 ----------
    if args.spec:
        spec = fg.load_spec(args.spec)
        spec_path = os.path.abspath(args.spec)
        print(f"[1/6] 规格：{os.path.relpath(spec_path, ROOT)}（{len(spec['nodes'])} 节点）")
    elif args.nl:
        m_path, m_score, m_why = fg.match_spec_file(args.nl)
        if m_path:
            spec = fg.load_spec(m_path)
            spec_path = os.path.abspath(m_path)
            print(f"[1/6] 自然语言 → 已有规格 `{os.path.relpath(m_path, ROOT)}`"
                  f"（匹配度 {m_score:.2f}，{m_why}；{len(spec['nodes'])} 节点）")
        else:
            tpl_key = fg.intent_match(args.nl)
            if not tpl_key:
                # 不猜：宁可让用户补一句，也不跑错流程（原兜底会静默跑成 Excel 导出）
                print(f"[1/6] ❌ 没听懂这句话（{m_why}）")
                print("      现有可用流程：")
                for fname, pname, ex in fg.spec_catalog():
                    print(f"        · {pname:<24} 例：{ex or '（无示例）'}")
                print("\n      改说法再试，或直接指定规格：--spec flows/<流程名>.json")
                return 2
            spec = copy.deepcopy(fg.TEMPLATES[tpl_key])
            spec_path = save_spec(spec)
            print(f"[1/6] 自然语言 → 内置模板 `{tpl_key}`（{len(spec['nodes'])} 节点）")
            print(f"      未命中已有规格：{m_why}；用模板兜底")
            print(f"      规格已落盘：{os.path.relpath(spec_path, ROOT)}")
    else:
        ap.error("必须给 --nl 或 --spec")
    if args.process_name:
        spec["name"] = args.process_name

    # 规格里写「自动发布」不算授权（闸门只认命令行）。提醒一句，免得两处口径打架。
    _pf_note = pg.check_spec_flags(spec)
    if _pf_note:
        print("ℹ️ " + _pf_note)

    # ---------- 1.2 迁移重挂：规格里的旧机器路径按文件名找回来 ----------
    rb = nlp.rebase_paths(spec, ROOT)
    for line in nlp.format_rebase(rb):
        print(line)

    # ---------- 1.5 从话里抽参数（话里说了什么就改成什么） ----------
    nl_notes = {}
    if args.nl and not args.no_nl_params:
        nr = nlp.apply(args.nl, spec, ROOT)
        nl_notes = nr["applied"] or {}
        if nr["applied"]:
            print("      参数取自这句话：")
            for name, d in nr["applied"].items():
                print(f"        · {name}: {d['old'] or '(空)'} → {d['new']}   （{d['why']}）")
        for s in nr["skipped"]:
            print(f"      ⚠️ {s}")
        batch_hit = any("批量" in w for w in nr["warnings"])
        for w in nr["warnings"]:
            print(f"      ⚠️ {w}")
        if batch_hit and not args.skip_batch_check:
            print("\n❌ 这句话是批量意图，当前流程只能处理一个文件，先不落库。")
            print("   要么改成「处理某个具体文件」，要么加 --skip-batch-check 只跑第一个。")
            return 2

    # ---------- 2. 真实 schema + 元素库 ----------
    print(f"[2/6] 加载原子字典与元素库（{'离线缓存' if args.offline else '服务端实时'}）...")
    atom_dict = fg.load_atom_dict(offline=args.offline)

    # 规格声明了 element_requirements 就先补齐元素（缺什么拾什么，幂等）。
    # 这一步让「网页流程」不再需要人进设计器点元素——AI 用一条命令就能拿到。
    #
    # **draft 阶段不建元素**（2026-11-04 修）：draft 对外承诺「只校验、零副作用」，
    # 但 auto_pick=True 会真的 POST 新建元素。实测跑 MES 流程 draft 时：
    #   · 没给 --robot-name → robot 取 rpa_config 的默认值（AI测试_Word转PDF）
    #   · 于是往那个机器人名下建了 3 个 MES 登录元素（该流程真实 robot 本来就有）
    # 既污染了别的机器人，也违背了 draft 的语义。落库阶段（review 及以后）照旧自动拾取。
    if spec.get("element_requirements") and not args.skip_element_pick:
        print("      检查规格声明的元素要求...")
        auto_pick = (not args.offline) and args.stage != "draft"
        if not auto_pick and not args.offline:
            print("      · draft 只检查不创建（正式落库时才自动拾取）")
        try:
            fg.ensure_elements(spec, robot_id=robot_id, auto_pick=auto_pick)
        except Exception as e:
            if args.stage == "draft":
                # draft 不建元素，缺失只提示不中断（元素是环境前置，不是规格错误）。
                # 但要说清最可能的原因：元素按 robot 隔离，robot 给错就一定找不到。
                print(f"      ⚠️ draft 未创建元素：{e}")
                print(f"      → 元素按机器人隔离。先确认 --robot-name 指向该流程的真实机器人"
                      f"（当前 robot={robot_id}）；元素确实缺失时，正式落库会自动拾取。")
            else:
                print(f"      ❌ 元素补齐失败：{e}")
                print("      可先单独跑：python element_pick.py --status")
                return 2

    elements = None
    try:
        elements = fg.load_elements(offline=args.offline, robot_id=robot_id)
    except Exception as e:
        print(f"      ⚠️ 元素库加载失败（{e}），跳过元素核对")
    print(f"      原子 {len(atom_dict)} · 元素 {len(elements or {})}")

    # ---------- 2.5 内嵌 Python 脚本 → 平台模块 ----------
    # 必须在 [3/6] 之前：节点的 `content` 要从 `$script:名字` 换成真实 moduleId，
    # 否则落库的是个占位符字符串，设计器里点运行直接报错。
    script_refs = _script_refs(spec)
    if script_refs:
        names = ", ".join(sorted({x[2] for x in script_refs}))
        print(f"[2.5/6] 内嵌 Python 脚本 → 平台模块（{len(script_refs)} 个节点引用：{names}）...")
        dry = (args.stage == "draft")
        ck_s = None
        if not dry:
            try:
                ck_s = pf.load_session()
            except SystemExit as e:
                print(f"      ❌ 拿不到设计器会话，无法上传 Python 子流程模块：{e}")
                return 2
            except Exception as e:  # noqa: BLE001
                print(f"      ❌ 设计器会话不可用：{type(e).__name__}: {e}")
                return 2
        try:
            mids = resolve_scripts(ck_s, robot_id, spec, dry=dry)
        except SystemExit as e:
            print(f"      ❌ {e}")
            return 2
        except Exception as e:  # noqa: BLE001
            print(f"      ❌ 脚本模块处理失败：{type(e).__name__}: {e}")
            return 2
        if dry:
            print("      （draft 只落盘不建模块；正式落库时会自动建/更新，无需人工上传 py）")
        elif mids:
            print(f"      模块就绪 {len(mids)} 个：{mids}（发布时会随版本一起复制）")

    # ---------- 3. 构建 + 校验 ----------
    print("[3/6] 构建节点树并校验...")
    try:
        pc = fg.build_process_content(spec, atom_dict, elements=elements, robot_id=robot_id,
                                      module_param_ids=spec.get("_resolved_param_ids"))
    except KeyError as e:
        # 典型：需要真实元素（元素按 robot 隔离），裸抛堆栈对用户毫无意义
        msg = e.args[0] if e.args else str(e)
        print(f"      ❌ {msg}")
        print("      说明：元素资产跟着机器人走，只能在设计器里对该机器人「拾取元素」后再跑。")
        print("            没有元素也能跑的流程见 flows/ 里零元素的那几条（PDF/Word/Excel 系列）。")
        return 2
    check = fg.validate(pc, atom_dict, elements=elements, robot_id=robot_id,
                        params={p["name"]: p for p in (spec.get("params") or [])})
    # 参数预算（平台缺陷 A20）：`Executor.paramJson` 太长会让平台的脱敏正则爆栈，
    # 把该账号整个「添加应用」列表打成 999999。lint 和发布门禁都管这件事，
    # 这里再兜一道 —— 直接把 `--stage draft` 的假绿堵住（draft 不落库也不发布，
    # 但产物会被当成「已验证通过」，等真发出去才发现就晚了）。
    _bud = pb.summary([p for p in (spec.get("params") or []) if isinstance(p, dict)])
    if _bud["level"] == "red" and not spec.get("param_budget_exempt"):
        check["errors"].append(
            "流程参数 %d 条 / paramJson 估算 %d 字符，超过平台爆栈线 —— 静态值请写死进节点"
            % (_bud["count"], _bud["span"]))
        check["is_pass"] = False
    elif _bud["level"] != "green":
        check["warnings"].append(
            "流程参数 %d 条 / paramJson 估算 %d 字符，已接近爆栈线（建议 ≤%d）"
            % (_bud["count"], _bud["span"], pb.SPAN_WARN))
    print(f"      IS_PASS: {'YES ✅' if check['is_pass'] else 'NO ❌'}"
          f"（错误 {len(check['errors'])} / 警告 {len(check['warnings'])}）")
    for e in check["errors"]:
        print(f"      ❌ {e}")
    for w in check["warnings"]:
        print(f"      ⚠️ {w}")
    out_json = os.path.join(ROOT, "docs", "generated_flow.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({"name": spec["name"], "spec_file": spec_path, "check": check,
                   "process_content": pc}, f, ensure_ascii=False, indent=2)

    if args.stage == "draft":
        # 「假绿」提醒（QA 2026-11-02 指出）：draft 不解析内嵌脚本，`$script:名字` 在节点树里
        # 就是一串普通文本，`fg.validate()` 自然 0 错 0 警 —— 但那不代表这份节点树能跑。
        # 不说明的话，`docs/generated_flow.json` 会被当成「已验证通过」的产物。
        if _script_refs(spec):
            print("\n⚠️ draft 结果**不能直接落库**：%d 个节点还是 `$script:名字` 占位符，"
                  "没有换成真 moduleId。" % len(_script_refs(spec)))
            print("   校验是「假绿」——占位符当普通字符串，当然没有错误。")
            print("   要真验证：去掉 --prep-only / --stage draft 跑一次（会建模块并真跑）。")
        print("\n[draft] 仅生成+校验，未落库、未执行。产物：docs/generated_flow.json")
        return 0 if check["is_pass"] else 2
    if not check["is_pass"]:
        print("\n❌ 校验未通过，不落库（先把上面 ❌ 修掉）。")
        return 2

    # ---------- 3.5 运行前预检（路径 / 占用 / 网关） ----------
    pf_res = None
    heal_log = []          # AI 自愈记录，写进评审单让人核对
    if not args.skip_preflight:
        will_run = (args.stage == "review" and not args.no_run)
        print("[3.5] 运行前预检（路径存在性 / 目录可写 / 目标文件是否被占用）...")
        try:
            import preflight as pfc
            pf_res = pfc.check_spec(spec, need_gateway=will_run)
            print(f"      {pfc.summary(pf_res)}")
            rep = pfc.format_report(pf_res)
            if rep:
                print(rep)
        except Exception as e:
            print(f"      ⚠️ 预检自身出错（不阻塞落库）：{e}")
        if pf_res and not pf_res["is_pass"] and will_run:
            # 能自己修的（输出目录不存在）先修，再重检一次；修不了才拦下
            acts, blocked = self_heal.plan_preflight(spec, pf_res, ROOT)
            for b in blocked:
                print(f"      ⛔ 不能自愈：{b}")
            if acts:
                print("      🔧 自己先修（不会丢数据的那几类）：")
                for a in acts:
                    print(f"         · {a['desc']}")
                n_ok, fails = self_heal.run_actions(acts)
                for f_ in fails:
                    print(f"         ⚠️ {f_}")
                heal_log.extend(a["desc"] for a in acts)
                pf_res = pfc.check_spec(spec, need_gateway=will_run)
                print(f"      修完重检：{pfc.summary(pf_res)}")
                rep = pfc.format_report(pf_res)
                if rep:
                    print(rep)
        if pf_res and not pf_res["is_pass"] and will_run:
            print("\n❌ 预检未通过：上面 ❌ 那几条会让这次真跑必然失败，所以先不落库。")
            print("   修完再跑同一条命令（目录不存在就手工建，文件被占用就先关掉）。")
            print("   确认可以忽略时加 --skip-preflight。")
            return 2

    if args.stage == "invoke":
        params = json.loads(args.params) if args.params.strip() else {}
        print("[invoke] 通过 openapi（API Key）触发执行...")
        r = do_invoke(robot_id, params)
        print(f"      status={r.get('status')} version={r.get('version')} "
              f"（{r.get('start')} → {r.get('end')}）")
        if r.get("result"):
            print(f"      result={json.dumps(r['result'], ensure_ascii=False)[:200]}")
        if r.get("error"):
            print(f"      error={r['error']}")
        det = r.get("detail") or {}
        if det.get("detail"):
            print(f"      具体原因（来自执行记录 {det.get('execute_id')}）：")
            for m in det["detail"]:
                print(f"        · {m[:300]}")
        if det.get("hints"):
            print("      真因翻译：")
            print(eh.format_report(det["hints"]))
        for tag in ("created", "changed"):
            if r.get(tag):
                print(f"      {tag}：{[os.path.basename(x) for x in r[tag]]}")
        print("\n结果：", "✅ 执行成功（真实产出已落地）" if r.get("ok") else "❌ 执行失败")
        return 0 if r.get("ok") else 1

    # ---------- 4. 落库到主流程 ----------
    print("[4/6] 写入机器人主流程...")
    ck = pf.load_session()
    robot_name = robot_name_of(ck, robot_id)
    user = pf.whoami(ck)
    procs = pf.list_processes(ck, robot_id)
    main_p = next((p for p in procs if (p.get("processName") or p.get("name")) == "主流程"), None)
    if main_p is None:
        print("      ❌ 该机器人没有「主流程」，请换一个机器人或在设计器里新建")
        return 2
    pid = main_p.get("processId") or main_p.get("resourceId")
    # 主流程只能放一条流程（设计器「编辑」默认打开它）。覆盖前先归档旧内容，
    # 避免「评审新流程时把上一版已通过的流程冲掉且找不回来」。
    try:
        old = pf.read_process(ck, robot_id, pid)
        if old and old.strip() and old != json.dumps(pc, ensure_ascii=False):
            arc = os.path.join(SPEC_DIR, "_archive")
            os.makedirs(arc, exist_ok=True)
            ap_ = os.path.join(arc, f"主流程_{time.strftime('%Y%m%d_%H%M%S')}.json")
            with open(ap_, "w", encoding="utf-8") as f:
                f.write(old)
            print(f"      已归档旧主流程内容：{os.path.relpath(ap_, ROOT)}")
    except SystemExit:
        raise
    except Exception as e:
        print(f"      ⚠️ 旧内容归档失败（不阻塞）：{e}")
    payload = json.dumps(pc, ensure_ascii=False)
    pf.save_process(ck, robot_id, pid, payload)
    back = (pf.read_process(ck, robot_id, pid) or "")
    # 原先是 `bool(back) and len(back) > 0` —— 只要非空就算「一致」，等于没校验。
    # 2026-11-03 踩过：写入 18 节点，回读其实是 12 节点旧版，被报成「一致」，
    # 白跑两轮真跑都没发现。改成真比对内容。
    same = back.strip() == payload.strip()
    print(f"      用户 {user} · 主流程 {pid} · 写入 {len(payload)} 字节"
          f" · 回读 {len(back)} 字节 · 一致={same}")
    if not same:
        print("      ⚠️ 回读内容与写入不一致 —— 服务端存的是另一个版本。")
        print("         常见原因：设计器正打开着该流程，客户端内存里是旧内容，"
              "运行/保存时会把它回存到服务端。")
        print("         处理：在设计器里关掉该流程标签，重新打开一次，然后再运行。")

    # 流程参数（c_param）：不落这个，p_var 引用在运行时就是未定义变量
    if spec.get("params"):
        ps = pf.sync_params(ck, robot_id, pid, spec["params"])
        print(f"      流程参数：新增 {ps['added'] or '—'} / 更新 {ps['updated'] or '—'}"
              f" / 服务端共 {ps['total']} 个")
        if ps["extra_on_server"]:
            print(f"      ⚠️ 服务端还有未声明的参数：{ps['extra_on_server']}（未删）")

    if args.stage == "publish":
        ver = args.version
        print(f"[publish] 发布机器人版本 v{ver} ...")
        # 参数体检（平台缺陷 A20）：超线宁可这次不发，也不要发出一个打挂「添加应用」的版本
        pf.guard_param_budget(ck, robot_id, pid, strict=True)
        # `module_ids` 必须传：publish 会用它核对「模块有没有被带进发布版本」。
        # 不传的话这一步的校验悄悄消失，等 openapi 调用时才报「该模块不存在」（坑 66）。
        # `--stage expose` 走 do_expose 是传了的，两条路径必须一致（QA 2026-11-02 指出）。
        r = pf.publish(ck, robot_id, spec["name"], ver,
                       module_ids=module_ids_of(spec), confirm=True)
        print(f"      发布结果：{r}")
        return 0

    if args.stage == "expose":
        print("[expose] 发布版本 + 登记 openapi 可执行列表...")
        try:
            v = do_expose(ck, robot_id, spec, pid, repair_modules=args.repair_modules)
        except SystemExit as e:
            print(f"      ❌ 发布中止：{e}")
            print("      没有发布任何版本 —— 宁可不发，也不发一个和代码不一致的版本。")
            return 2
        print("\n" + "=" * 64)
        print(f"✅ 已上线可调用：project_id={robot_id}  version=v{v}")
        print(f"   调用：python scripts/ai_flow.py --spec {os.path.relpath(spec_path, ROOT)} --stage invoke")
        print(f"   或直接：POST {openapi_base()}/workflows/execute-async  "
              f"body={{'project_id':'{robot_id}','params':{{}}}}  头 X-API-Key")
        print("=" * 64)
        return 0

    # ---------- 5. 本地真跑（AI 自查，减少用户白跑） ----------
    run_result, created, changed = None, [], []
    if not args.no_run:
        print("[5/6] 本地真跑（客户端引擎，不开界面）...")
        attempt = 0
        while True:
            attempt += 1
            before = snapshot(WATCH_DIRS)
            run_result = rfl.run(robot_id)
            after = snapshot(WATCH_DIRS)
            created, changed = diff_snapshot(before, after)
            if run_result.get("ok"):
                print(f"      {run_result.get('status')} ✅（{run_result.get('elapsed_sec')}s）")
                if created:
                    print(f"      新建：{[os.path.basename(x) for x in created]}")
                if changed:
                    print(f"      改动：{[os.path.basename(x) for x in changed]}")
                break
            print(f"      {run_result.get('status')} ❌ —— {run_result.get('error') or '见日志'}")
            for e in (run_result.get("errors") or [])[:5]:
                print(f"      {e}")
            if run_result.get("deep"):
                print("      深度定位（失败在哪个节点）：")
                print(tm.format_deep(run_result["deep"]))
            if run_result.get("hints"):
                print("      真因翻译：")
                print(eh.format_report(run_result["hints"]))

            # 失败自愈：只改「改了不会更糟」的东西，最多再来两轮
            if attempt > self_heal.MAX_HEAL:
                print(f"      ⛔ 已自动重试 {self_heal.MAX_HEAL} 次，仍失败，交给人看")
                break
            acts, blocked = self_heal.plan_run(spec, run_result, ROOT)
            for b in blocked:
                print(f"      ⛔ 不能自愈：{b}")
            if not acts:
                break
            print("      🔧 按失败原因改参数，然后重跑：")
            for a in acts:
                print(f"         · {a['desc']}")
            n_ok, fails = self_heal.run_actions(acts)
            for f_ in fails:
                print(f"         ⚠️ {f_}")
            if not n_ok:
                break
            heal_log.extend(a["desc"] for a in acts)
            # 参数变了 → 重建节点树并重新落库（否则引擎跑的还是旧参数）
            pc = fg.build_process_content(spec, atom_dict, elements=elements, robot_id=robot_id,
                                          module_param_ids=spec.get("_resolved_param_ids"))
            pf.save_process(ck, robot_id, pid, json.dumps(pc, ensure_ascii=False))
            if spec.get("params"):
                pf.sync_params(ck, robot_id, pid, spec["params"])
            print(f"      已重新落库，准备第 {attempt + 1} 次运行")
    else:
        print("[5/6] 跳过本地真跑（--no-run）")

    # ---------- 6. 评审单 ----------
    print("[6/6] 生成评审单...")
    sheet = write_review_sheet(args.nl or spec.get("name"), spec, spec_path, pc, atom_dict,
                               robot_id, pid, check, run_result, created, changed,
                               robot_name=robot_name, heal_log=heal_log, nl_notes=nl_notes)
    print(f"      {os.path.relpath(sheet, ROOT)}")

    print("\n" + "=" * 64)
    ok_all = check["is_pass"] and (run_result is None or run_result.get("ok"))
    if ok_all:
        print("✅ 已就绪，等你人工评审：")
        print(f"   设计器 → 机器人「{robot_name or '当前机器人'}」→ 点「编辑」→ 顶部「运行」")
        print(f"   评审单：{os.path.relpath(sheet, ROOT)}")
        print("   不通过就把哪一步不对告诉我，我改规格后重跑。")
    else:
        print("⚠️ 机器自检未全绿，先别评审，等我修。")
    print("=" * 64)
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
