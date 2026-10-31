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
            vals = it.get("value") or []
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


def module_ids_of(spec):
    """从规格里找出 Python 子流程模块 id。

    模块 id 是**环境相关**的（新环境要重新建），所以在规格里它是一个流程参数
    （如 `module_id`），迁移时只改这个参数的默认值，流程本身不用动。
    """
    out = []
    for p in spec.get("params") or []:
        name = (p.get("name") or "").lower()
        val = str(p.get("default") or "").strip()
        if "module" in name and val.isdigit():
            out.append(val)
    return out


def do_expose(cookie, robot_id, spec, process_id, repair_modules=False):
    """发布版本 + 登记进 openapi 列表 —— 做完这两步，API Key 才能调用。

    注意：`parameters` 必须把**真实流程参数表**登记进去，否则 openapi 会按空 schema
    校验入参，传任何参数都报 `403 Arguments do not match the tool input schema`。
    """
    mids = module_ids_of(spec)
    v = pf.publish_auto(cookie, robot_id, spec.get("name") or "AI生成流程",
                        introduction=(spec.get("nl_examples") or ["AI 生成流程"])[0],
                        module_ids=mids, repair_modules=repair_modules)
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
    ap.add_argument("--stage", choices=["draft", "review", "publish", "expose", "invoke"], default="review",
                    help="draft=只生成校验 / review=落库+真跑+评审单（默认） / "
                         "publish=发布版本 / expose=发布+登记 openapi 列表 / invoke=用 API Key 调用执行")
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
    args = ap.parse_args()


    robot_id = args.robot_id
    print("=" * 64)
    print("AI 流程交付流水线")
    print("=" * 64)

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
    print("[2/6] 加载原子字典与元素库（.130 实时）...")
    atom_dict = fg.load_atom_dict(offline=args.offline)

    # 规格声明了 element_requirements 就先补齐元素（缺什么拾什么，幂等）。
    # 这一步让「网页流程」不再需要人进设计器点元素——AI 用一条命令就能拿到。
    if spec.get("element_requirements") and not args.skip_element_pick:
        print("      检查规格声明的元素要求...")
        try:
            fg.ensure_elements(spec, robot_id=robot_id, auto_pick=not args.offline)
        except Exception as e:
            print(f"      ❌ 元素补齐失败：{e}")
            print("      可先单独跑：python element_pick.py --status")
            return 2

    elements = None
    try:
        elements = fg.load_elements(offline=args.offline, robot_id=robot_id)
    except Exception as e:
        print(f"      ⚠️ 元素库加载失败（{e}），跳过元素核对")
    print(f"      原子 {len(atom_dict)} · 元素 {len(elements or {})}")

    # ---------- 3. 构建 + 校验 ----------
    print("[3/6] 构建节点树并校验...")
    try:
        pc = fg.build_process_content(spec, atom_dict, elements=elements, robot_id=robot_id)
    except KeyError as e:
        # 典型：需要真实元素（元素按 robot 隔离），裸抛堆栈对用户毫无意义
        msg = e.args[0] if e.args else str(e)
        print(f"      ❌ {msg}")
        print("      说明：元素资产跟着机器人走，只能在设计器里对该机器人「拾取元素」后再跑。")
        print("            没有元素也能跑的流程见 flows/ 里零元素的那几条（PDF/Word/Excel 系列）。")
        return 2
    check = fg.validate(pc, atom_dict, elements=elements, robot_id=robot_id,
                        params={p["name"]: p for p in (spec.get("params") or [])})
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
    pf.save_process(ck, robot_id, pid, json.dumps(pc, ensure_ascii=False))
    back = pf.read_process(ck, robot_id, pid)
    print(f"      用户 {user} · 主流程 {pid} · 写入 {len(json.dumps(pc, ensure_ascii=False))} 字节"
          f" · 回读一致={bool(back) and len(back) > 0}")

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
        r = pf.publish(ck, robot_id, spec["name"], ver)
        print(f"      发布结果：{r}")
        return 0

    if args.stage == "expose":
        print("[expose] 发布版本 + 登记 openapi 可执行列表...")
        v = do_expose(ck, robot_id, spec, pid, repair_modules=args.repair_modules)
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
            pc = fg.build_process_content(spec, atom_dict, elements=elements, robot_id=robot_id)
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
