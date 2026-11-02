#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""能力自举验证（capability bootstrap）：在新环境确认「AI 搭建 RPA 流程」这项能力是否完整可用。

**为什么要这个脚本**：本项目要迁移的**不是** `flows/` 里那 84 条已生成的流程，
**而是「把人话变成能在 Astron RPA 上真跑起来的流程」这项能力**。
能力由 5 样东西支撑：配置会话、原子字典、生成器、本机实现、平台连通。
任何一样缺失，能力就是断的。这个脚本一条命令把它们全验一遍。

用法：
    python scripts/capability_bootstrap.py            # 静态 + 连通性（不落库，安全）
    python scripts/capability_bootstrap.py --live     # 额外做「真落库 + 真跑」端到端验收（会在平台建一个自检机器人）
    python scripts/capability_bootstrap.py --offline  # 只用本地缓存的原子字典，不连平台

退出码：0 = 能力完好；1 = 有硬性缺失（看 ❌ 项）
"""
import os
import sys
import json
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

RESULTS = []


def chk(no, name, fn):
    """执行一项检查并记录。fn 返回 (ok: bool, detail: str)。"""
    try:
        ok, detail = fn()
    except BaseException as e:                              # noqa: BLE001
        ok, detail = False, f"{type(e).__name__}: {e}"
    RESULTS.append((no, name, ok, detail))
    print(f"[{no}] {'✅' if ok else '❌'} {name}")
    for line in str(detail).splitlines():
        print(f"       {line}")
    return ok


# ---------------------------------------------------------------- 内置最小规格
# 刻意不读 flows/*.json：这个自检必须能在一个「只有 scripts/ 没有 flows/」的环境里跑通，
# 否则就证明不了"迁移的是能力而不是流程"。
MINIMAL_SPEC = {
    "name": "能力自检_最小流程",
    "nl_examples": ["把一段文本按分隔符拆开、去重、再拼回去"],
    "keywords": ["能力自检", "bootstrap"],
    "params": [
        {"name": "text", "type": "Str", "default": "c,a,b,a", "describe": "待处理文本"},
        {"name": "sep", "type": "Str", "default": ",", "describe": "分隔符"},
    ],
    "nodes": [
        {"atom": "StringProcess.split_string_to_list", "alias": "拆成列表",
         "out": {"split_list_from_string": "parts"},
         "inputs": {"string_data": "$p:text", "separator": "$p:sep"}},
        {"atom": "ListProcess.get_unique_list", "alias": "去重",
         "out": {"unique_list_data": "uniq"},
         "inputs": {"list_data": "$parts"}},
        {"atom": "StringProcess.merge_list_to_string", "alias": "拼回字符串",
         "out": {"merged_string_from_list": "joined"},
         "inputs": {"list_data": "$uniq", "separator": "-"}},
    ],
}


# ---------------------------------------------------------------- 各项检查
def c_python():
    v = sys.version_info
    missing = []
    for m in ("requests",):
        try:
            __import__(m)
        except ImportError:
            missing.append(m)
    ok = v >= (3, 8) and not missing
    return ok, f"Python {v.major}.{v.minor}.{v.micro} @ {sys.executable}\n缺失依赖: {missing or '无'}"


def c_config():
    import rpa_config as cfg
    p = os.path.join(ROOT, "rpa_config.json")
    if not os.path.exists(p):
        return False, f"缺少 {p}\n  → 从旧机器的 handover/ 目录拷过来（它不在 git 里）"
    fields = {}
    for fn in ("host_port", "gateway_base", "api_key", "cookie_file"):
        f = getattr(cfg, fn, None)
        try:
            fields[fn] = f() if callable(f) else None
        except BaseException as e:                          # noqa: BLE001
            fields[fn] = f"<{type(e).__name__}>"
    empty = [k for k, v in fields.items() if not v]
    # 凭据脱敏：只报「有没有、多长」，绝不回显明文
    shown = {}
    for k, v in fields.items():
        if k == "api_key" and v:
            shown[k] = f"{str(v)[:4]}***（长度 {len(str(v))}）"
        else:
            shown[k] = v
    lines = [f"{k} = {v}" for k, v in shown.items()]
    return (not empty), "\n".join(lines) + ("\n空缺字段: " + str(empty) if empty else "")


def c_session():
    import publish_flow as pf
    ck = pf.load_session()
    return bool(ck), f"会话已加载（{ck[:12]}…，长度 {len(ck)}）"


def c_platform():
    import publish_flow as pf
    ck = pf.load_session()
    d = pf.ok(pf.api("/api/robot/robot-design/design-list",
                     {"pageNo": 1, "pageSize": 200}, ck), "design-list")
    recs = d.get("records") if isinstance(d, dict) else (d or [])
    names = [r.get("robotName") for r in recs][:5]
    return True, f"平台连通，机器人 {len(recs)} 个（示例: {names}）"


def c_atoms(offline=False):
    import flow_generator as fg
    d = fg.load_atom_dict(offline=offline)
    domains = {}
    for k in d:
        domains[k.split(".")[0]] = domains.get(k.split(".")[0], 0) + 1
    top = sorted(domains.items(), key=lambda x: -x[1])[:6]
    return len(d) > 300, f"原子字典 {len(d)} 条，{len(domains)} 个域（最大: {top}）"


def c_generator(offline=False):
    """核心检查：规格 JSON → 节点树。这一项通过就说明「AI 能搭流程」的主体能力在。"""
    import flow_generator as fg
    d = fg.load_atom_dict(offline=offline)
    pc = fg.build_process_content(MINIMAL_SPEC, d, elements=None, robot_id="0", strict=True)
    nodes = json.loads(pc) if isinstance(pc, str) else pc
    nodes = nodes if isinstance(nodes, list) else (nodes.get("process_content") or [])
    # 抽查节点结构是否符合平台契约
    n0 = nodes[0]
    has = {"atom": bool(n0.get("atom") or n0.get("key")),
           "inputList": isinstance(n0.get("inputList"), list),
           "outputList": isinstance(n0.get("outputList"), list)}
    bad = [k for k, v in has.items() if not v]
    detail = (f"内置最小规格 {len(MINIMAL_SPEC['nodes'])} 节点 → 节点树 {len(nodes)} 节点，"
              f"结构校验: {'通过' if not bad else '异常 ' + str(bad)}")
    return len(nodes) == len(MINIMAL_SPEC["nodes"]), detail


def c_runtime():
    """本机引擎实现（astronverse）：没有它，生成得再对也跑不起来。"""
    base = os.path.expandvars(os.path.join(os.environ.get("APPDATA", ""), "astron-rpa", "python_core"))
    pkg = os.path.join(base, "Lib", "site-packages", "astronverse")
    if not os.path.isdir(pkg):
        return False, (f"未找到 {pkg}\n  → 本机没装 Astron RPA 设计器，或装在别处。"
                       "没有它流程无法真跑（可以生成，但不能验证）")

    def has(m):
        return os.path.isdir(os.path.join(pkg, m))

    CORE = ["excel", "pdf", "script", "dialog", "browser", "network",
            "dataprocess", "datatable", "email", "actionlib", "locator"]
    ORCH = ["scheduler", "executor", "trigger"]      # 决定能否无人值守
    AI = ["ai", "cua", "openapi", "smart"]           # 有实现，卡在服务端凭据

    miss_core = [m for m in CORE if not has(m)]
    got_orch = [m for m in ORCH if has(m)]
    got_ai = [m for m in AI if has(m)]
    ok = not miss_core
    detail = (f"引擎: {pkg}\n"
              f"  核心模块 {len(CORE) - len(miss_core)}/{len(CORE)}"
              + (f"（缺 {miss_core}）" if miss_core else "") + "\n"
              f"  调度/执行模块: {got_orch or '无'}"
              + ("（平台原生支持定时任务）" if got_orch else "（无 → 不能无人值守）") + "\n"
              f"  AI 类模块: {got_ai or '无'}（有实现，但需服务端配 key 才可用）")
    return ok, detail


def c_live():
    """端到端验收：真落库 + 真跑（会在平台上建一个名叫「能力自检_最小流程」的机器人）。"""
    import subprocess
    spec_path = os.path.join(ROOT, "_bootstrap_spec.json")
    with open(spec_path, "w", encoding="utf-8") as f:
        json.dump(MINIMAL_SPEC, f, ensure_ascii=False, indent=2)
    try:
        py = sys.executable
        r = subprocess.run([py, os.path.join(HERE, "ai_flow.py"),
                            "--spec", spec_path, "--robot-name", "能力自检_最小流程"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           cwd=ROOT)
        out = (r.stdout or "") + (r.stderr or "")
        ok = ("IS_PASS" in out) or ("SUCCESS" in out)
        tail = "\n".join([l for l in out.splitlines() if any(
            k in l for k in ("IS_PASS", "SUCCESS", "FAIL", "错误", "写入", "新建"))][:8])
        return ok, tail or out[-500:]
    finally:
        try:
            os.remove(spec_path)
        except OSError:
            pass


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="额外做真落库+真跑端到端验收（会建机器人）")
    ap.add_argument("--offline", action="store_true", help="只用本地缓存原子字典，不连平台")
    args = ap.parse_args()

    print("=" * 64)
    print("能力自举验证：AI 搭建 RPA 流程能力是否完整")
    print(f"项目根目录: {ROOT}")
    print("=" * 64)

    checks = [
        (1, "Python 与依赖", c_python),
        (2, "配置（rpa_config.json）", c_config),
    ]
    if not args.offline:
        checks += [(3, "登录会话（设计器 cookie）", c_session),
                   (4, "平台连通（机器人列表）", c_platform)]
    checks += [
        (5, "原子字典（可用原子清单）", lambda: c_atoms(args.offline)),
        (6, "生成器（规格 → 节点树）★核心", lambda: c_generator(args.offline)),
        (7, "本机引擎实现（astronverse）", c_runtime),
    ]
    if args.live and not args.offline:
        checks += [(8, "端到端（真落库 + 真跑）", c_live)]

    for no, name, fn in checks:
        chk(no, name, fn)

    okn = sum(1 for r in RESULTS if r[2])
    tot = len(RESULTS)
    print("\n" + "=" * 64)
    print(f"结果: {okn}/{tot} 项通过")
    fails = [r for r in RESULTS if not r[2]]
    if not fails:
        print("✅ 能力完好 —— 这套环境可以直接用来「人话 → 流程」。")
        if not args.live:
            print("   （想连跑通验证加 --live，会在平台建一个「能力自检_最小流程」机器人）")
    else:
        print("❌ 能力不完整，按下面缺失项补齐：")
        for no, name, _, detail in fails:
            print(f"   - [{no}] {name}: {str(detail).splitlines()[0]}")
        if any(r[0] in (2, 3, 4) for r in fails):
            print("\n   配置/会话类缺失 → 从旧机器拷 handover/ 目录，按其中 MANIFEST.md 放回原位；")
            print("   然后跑 `python scripts/handover_export.py --check --src <包路径>` 校验。")
    print("=" * 64)
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
