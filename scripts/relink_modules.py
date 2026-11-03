#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把平台上 `Script.module` 节点重建成**设计器认的结构**（2026-11-03 定稿）。

## 为什么需要这个脚本

2026-11-02 那版生成器把 `Script.module` 的两个参数写错了（用户目视发现）：

    错的：content      = [{"type":"other","value":"<moduleId>"}]      ← 数组
          module_param = "[{\\"varName\\":…}]"                        ← JSON 字符串
          c_param 里没有 module_id 级的参数定义

    对的（从用户手工搭的 `导入MES` 流程取到的标准答案）：
          content      = "<moduleId>"                                ← 纯字符串
          module_param = [{"varId":"<c_param.id>","varName":…,
                           "varValue":{"rpa":"special","value":[{"type":"other","value":…}]}}]
          c_param      = module_id=<moduleId>, process_id=NULL, var_direction=0

错的后果：设计器打开节点顶栏报「请求参数解析失败」、参数表格「暂无数据」，参数改不了。
生成器已修（`flow_generator.build_process_content`），但**已落库的历史流程还是旧结构**，
本脚本负责把它们就地重建。

## 用法

    python scripts/relink_modules.py --dry-run     # 只盘点：哪些流程/节点需要修，缺什么
    python scripts/relink_modules.py               # 全量修（幂等，可反复跑）
    python scripts/relink_modules.py --robot-name "按部门拆分Excel"   # 只修一个机器人
    python scripts/relink_modules.py --run         # 修完本地真跑（配合 --robot-name）

## 语义保持原则（改结构不改行为）

- `varValue` 原本是 `p_var`（引用流程参数）/ `var`（循环变量）的，**原样保留**；
- 原本是字面量的，包成 `{"rpa":"special","value":[{"type":"other","value":…}]}`；
- 节点的其他 inputList 项、advanced、exception 一律不动。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import publish_flow as pf  # noqa: E402

SCRIPT_ATOM = "Script.module"
JSON_PREFIX = "$json:"


# ------------------------------- 读平台 -------------------------------
def read_nodes(ck, robot_id, pid):
    raw = pf.read_process(ck, robot_id, pid)
    return json.loads(raw) if isinstance(raw, str) else raw


def robot_list(ck):
    d = pf.ok(pf.api("/api/robot/robot-design/design-list",
                     {"pageNo": 1, "pageSize": 500}, ck), "列机器人")
    return (d.get("records") if isinstance(d, dict) else d) or []


# ------------------------------- 结构解析 -------------------------------
def _flow_param_defaults(ck, robot_id, pid):
    """流程参数的默认值（`content` 写成 `$p:module_id` 的历史流程要用它还原成字符串）。"""
    out = {}
    for p in pf.list_params(ck, robot_id, pid) or []:
        if isinstance(p, dict) and p.get("varName") is not None:
            out[p["varName"]] = p.get("varValue")
    return out


def _parse_content(raw, defaults):
    """把各种历史形态的 content 归一成 moduleId 字符串，返回 (值, 说明)。

    历史形态（都真实存在）：
      - `[{"type":"other","value":"2117..."}]`     → 数组包字面量
      - `[{"type":"p_var","value":"module_id"}]`   → 数组包**流程参数引用**（最常见，
        这种最容易踩：直接取 `value` 会拿到参数名 `"module_id"` 当成模块 id 落库，
        真跑报 `无法导入模块: .module_id`）
      - `{"rpa":"special","value":[…]}`            → rpa 包裹
      - `"$p:module_id"` / `"2117..."`             → 字符串（引用 / 直给）
    """
    if isinstance(raw, list):
        for it in raw:
            if not isinstance(it, dict):
                continue
            t = it.get("type")
            v = it.get("value")
            if t == "p_var":
                return str(defaults.get(v) or ""), "数组(p_var:%s) → 流程参数默认值" % v
            if t == "var":
                return "", "数组里是运行时变量 %r，无法静态确定 moduleId" % (v,)
            return str(v), "数组 → 字符串"
        return "", "数组为空"
    if isinstance(raw, dict):
        parts = raw.get("value") if isinstance(raw.get("value"), list) else [raw]
        return _parse_content(parts, defaults)
    if raw is None:
        return "", "缺失"
    s = str(raw)
    if s.startswith("$p:"):
        name = s[3:]
        return str(defaults.get(name) or ""), "$p:%s → 默认值" % name
    # 兜底：content 被落成了「流程参数名」（旧版 p_var 处理错误的产物，真跑报
    # `无法导入模块: .module_id`）。moduleId 是纯数字雪花号，纯标识符就去参数里找。
    if s and not s.isdigit() and s in defaults:
        return str(defaults.get(s) or ""), "content 是流程参数名 %s → 默认值" % s
    return s, ""


def _parse_module_param(raw):
    """把各种历史形态的 module_param 归一成 list，返回 (list, 说明)。"""
    if raw is None:
        return [], "缺失"
    if isinstance(raw, list):
        return raw, ""
    s = str(raw)
    body = s[len(JSON_PREFIX):] if s.startswith(JSON_PREFIX) else s
    try:
        obj = json.loads(body)
    except Exception as e:
        raise ValueError("module_param 无法解析成 JSON：%s" % e)
    if isinstance(obj, dict):
        obj = [obj]
    if not isinstance(obj, list):
        raise ValueError("module_param 不是数组：%r" % type(obj).__name__)
    return obj, "JSON 字符串 → 数组"


def _normalize_var_value(vv, defaults):
    """单个参数的 varValue → `{"rpa":"special","value":[…]}`，返回 (新值, 说明)。"""
    if isinstance(vv, dict) and vv.get("rpa") == "special":
        return vv, ""
    if isinstance(vv, str) and vv.startswith("$p:"):
        name = vv[3:]
        d = defaults.get(name)
        return ({"rpa": "special",
                 "value": [{"type": "other", "value": "" if d is None else d}]},
                "$p:%s → 默认值" % name)
    if isinstance(vv, dict) and isinstance(vv.get("value"), list):
        return {"rpa": "special", "value": vv["value"]}, "补 rpa:special 包裹"
    if isinstance(vv, list):
        parts = [{"type": "other", "value": it}] if not isinstance(vv, list) else vv
        return {"rpa": "special", "value": parts}, "补 rpa:special 包裹"
    return ({"rpa": "special", "value": [{"type": "other", "value": vv}]},
            "字面量 → rpa:special")


def _default_of(var_value):
    """从 varValue 里取一个可当默认值的标量（给 c_param.var_value 用）。"""
    for part in (var_value.get("value") or []):
        if isinstance(part, dict) and part.get("type") == "other":
            v = part.get("value")
            return v
    return ""


def fix_node(ck, robot_id, pid, node, defaults, pid_params, dry=False):
    """就地修正一个 Script.module 节点，返回 (改动说明列表, 是否改过)。"""
    changes = []
    inputs = node.get("inputList") or []
    by_key = {}
    for idx, it in enumerate(inputs):
        if isinstance(it, dict) and it.get("key") in ("content", "module_param"):
            by_key[it["key"]] = idx

    # ---- content ----
    c_idx = by_key.get("content")
    raw_content = inputs[c_idx].get("value") if c_idx is not None else None
    mid, why = _parse_content(raw_content, defaults)
    if why:
        changes.append("content：%s" % why)
    if not mid:
        raise ValueError("content 取不到 moduleId（原始值 %r）" % (raw_content,))
    if not isinstance(raw_content, str):
        changes.append("content 类型 %s → str" % type(raw_content).__name__)

    # ---- module_param ----
    m_idx = by_key.get("module_param")
    raw_mp = inputs[m_idx].get("value") if m_idx is not None else None
    try:
        items, why = _parse_module_param(raw_mp)
        if why:
            changes.append("module_param：%s" % why)
    except ValueError as e:
        raise ValueError("module_param 解析失败：%s" % e)

    new_items = []
    for it in items:
        if not isinstance(it, dict) or not it.get("varName"):
            continue
        name = it["varName"]
        vv, why = _normalize_var_value(it.get("varValue"), defaults)
        if why:
            changes.append("参数 %s：%s" % (name, why))
        new_items.append({"varId": str(it.get("varId") or ""),
                          "varName": name, "varValue": vv})

    # ---- 补模块级参数定义（c_param），拿 varId ----
    defs = []
    for it in new_items:
        d = _default_of(it["varValue"])
        if not isinstance(d, (str, int, float, bool)) and d is not None:
            d = json.dumps(d, ensure_ascii=False)
        defs.append({"name": it["varName"], "type": "Str",
                     "default": "" if d is None else d,
                     "describe": pid_params.get(it["varName"], "") or ""})
    if defs:
        if dry:
            # --dry-run 必须**只读**：不能真建 c_param，只推算「会新增几个、varId 会不会变」。
            existing = {p.get("varName"): p
                        for p in pf.list_module_params(ck, robot_id, mid)
                        if isinstance(p, dict)}
            would = [d["name"] for d in defs if d["name"] not in existing]
            if would:
                changes.append("c_param 将新增 %d 个参数定义：%s" % (len(would), "、".join(would)))
            for it in new_items:
                old = str(it.get("varId") or "")
                new = str((existing.get(it["varName"]) or {}).get("id") or "")
                if new and old != new:
                    changes.append("参数 %s 的 varId 将修正 %s → %s"
                                   % (it["varName"], old or "(空)", new))
                    it["varId"] = new
                elif not new and not old:
                    changes.append("参数 %s 缺 varId（c_param 里没有对应行）" % it["varName"])
        else:
            sync = pf.sync_module_params(ck, robot_id, mid, defs, update_existing=False)
            if sync["added"]:
                changes.append("c_param 新增 %d 个参数定义：%s"
                               % (len(sync["added"]), "、".join(sync["added"])))
            for it in new_items:
                old = str(it.get("varId") or "")
                new = sync["ids"].get(it["varName"]) or ""
                if new and old != new:
                    if old:
                        changes.append("参数 %s 的 varId 修正 %s → %s"
                                       % (it["varName"], old, new))
                    it["varId"] = new
            if sync.get("extra_on_server"):
                print("      ⚠️ 模块上有节点没用到的旧参数（保留未删）：%s"
                      % "、".join(sync["extra_on_server"]))
    changed = bool(changes)

    # ---- 回写 inputList（只替换这两项，其余原样） ----
    new_inputs = list(inputs)
    content_entry = {"key": "content", "value": mid}
    mp_entry = {"key": "module_param", "value": new_items}
    if c_idx is None or m_idx is None:
        new_inputs = [content_entry, mp_entry]
        changes.append("inputList 重建（原来缺项）")
        changed = True
    else:
        new_inputs[c_idx] = content_entry
        new_inputs[m_idx] = mp_entry
    node["inputList"] = new_inputs
    return changes, changed


# ------------------------------- 主流程 -------------------------------
def scan(ck, only_robot=None, dry=False, do_run=False):
    robots = robot_list(ck)
    total_nodes = fixed_nodes = fixed_procs = 0
    for r in robots:
        rid = str(r.get("robotId") or "")
        rname = r.get("robotName") or r.get("name") or ""
        if only_robot and only_robot not in (rid, rname):
            continue
        procs = pf.list_processes(ck, rid) or []
        for p in procs:
            pid = str(p.get("processId") or p.get("id") or "")
            pname = p.get("processName") or p.get("name") or ""
            if not pid:
                continue
            try:
                nodes = read_nodes(ck, rid, pid)
            except SystemExit:
                continue
            except Exception as e:
                print("  ⚠️ 读流程失败 %s/%s：%s" % (rname, pname, e))
                continue
            if not isinstance(nodes, list):
                continue
            hits = [i for i, n in enumerate(nodes)
                    if isinstance(n, dict) and (n.get("key") or "") == SCRIPT_ATOM]
            if not hits:
                continue
            total_nodes += len(hits)
            defaults = _flow_param_defaults(ck, rid, pid)
            pid_params = {}
            for fp in pf.list_params(ck, rid, pid) or []:
                if isinstance(fp, dict):
                    pid_params[fp.get("varName")] = fp.get("describe") or ""
            print("\n%s / %s  （%d 个 Script.module 节点）" % (rname, pname, len(hits)))
            touched = False
            for i in hits:
                node = nodes[i]
                label = node.get("alias") or node.get("id") or ("节点#%d" % (i + 1))
                try:
                    changes, changed = fix_node(ck, rid, pid, node, defaults, pid_params, dry=dry)
                except ValueError as e:
                    print("  ✗ %s：%s" % (label, e))
                    continue
                if changed:
                    touched = True
                    fixed_nodes += 1
                    print("  · %s 需要修：" % label)
                    for c in changes:
                        print("      - %s" % c)
                else:
                    print("  ✓ %s 已是标准结构" % label)
            if touched:
                fixed_procs += 1
                if not dry:
                    pf.save_process(ck, rid, pid, json.dumps(nodes, ensure_ascii=False))
                    print("  → 已落库")
                    if do_run:
                        _run_local(rid)
    print("\n" + "=" * 64)
    print("共扫描 %d 个 Script.module 节点，其中 %d 个需要修（涉及 %d 个流程）"
          % (total_nodes, fixed_nodes, fixed_procs))
    if dry and fixed_nodes:
        print("这是 --dry-run，没有改动平台。去掉 --dry-run 就会就地修。")
    return 0


def _run_local(robot_id):
    """本地真跑（引擎在本机 python_core 里执行同一个 process_content）。"""
    import subprocess
    cmd = [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "run_flow_local.py"), "--robot-id", robot_id]
    print("    · 本地真跑验证 ...")
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=600)
    except subprocess.TimeoutExpired:
        print("    ⚠️ 真跑超时")
        return
    tail = [ln for ln in (p.stdout or "").splitlines() if ln.strip()][-3:]
    print("    " + (" / ".join(tail) if tail else "（无输出）"))


def main():
    ap = argparse.ArgumentParser(description="把 Script.module 节点重建成设计器认的标准结构")
    ap.add_argument("--dry-run", action="store_true", help="只盘点，不改平台")
    ap.add_argument("--robot-name", default="", help="只处理这个机器人（名字或 robotId）")
    ap.add_argument("--run", action="store_true", help="修完本地真跑一次验证")
    args = ap.parse_args()
    ck = pf.load_session()
    print("=" * 64)
    print("Script.module 结构重建（Py 原子标准结构：content 字符串 + module_param 数组 + c_param）")
    print("=" * 64)
    return scan(ck, only_robot=args.robot_name, dry=args.dry_run, do_run=args.run)


if __name__ == "__main__":
    sys.exit(main())
