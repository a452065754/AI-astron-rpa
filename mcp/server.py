#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""星辰开源 · MCP 服务（零依赖实现）

## 这是什么

把「自然语言 → 固定 RPA 流程」的整条流水线，暴露成 **MCP 工具**，任何支持
Model Context Protocol 的客户端（Claude Desktop / Cursor / WorkBuddy 等）都能直接调用。

协议实现：JSON-RPC 2.0 over stdio，**每行一条 JSON 消息**（UTF-8，消息内不含换行）。
只实现 MCP 的必要子集：`initialize` / `notifications/initialized` / `tools/list` /
`tools/call` / `ping`。不依赖 `mcp` 官方包，`python mcp/server.py` 直接能跑。

## 为什么不用官方 SDK

迁移优先：目标机器上可能装不上第三方包。整个项目主链路也只依赖 `requests`，
MCP 层保持零依赖，`git clone` 完就能用。

## 注意

- **stdout 只走协议**，所有日志/调试一律写 stderr（否则客户端解析会崩）。
- 调用的脚本需要 `requests`；本服务会自动挑一个装了 `requests` 的 Python
  （`RPA_PYTHON` 环境变量 > 当前解释器 > 常见路径）。

## 启动

    python mcp/server.py

客户端配置（Claude Desktop 示例）：

    {
      "mcpServers": {
        "astron-rpa": {
          "command": "python",
          "args": ["F:/AI-Workbench/10-Projects/星辰开源/mcp/server.py"]
        }
      }
    }
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
FLOWS = os.path.join(ROOT, "flows")

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "astron-rpa-flow", "version": "1.0.0"}


# --------------------------------------------------------------------------
# 工具函数
# --------------------------------------------------------------------------
def log(*a):
    """日志只写 stderr，stdout 留给协议。"""
    sys.stderr.write(" ".join(str(x) for x in a) + "\n")
    sys.stderr.flush()


_PY_CACHE = {}


def _has_requests(py):
    if py in _PY_CACHE:
        return _PY_CACHE[py]
    try:
        r = subprocess.run([py, "-c", "import requests"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        _PY_CACHE[py] = (r.returncode == 0)
    except Exception:
        _PY_CACHE[py] = False
    return _PY_CACHE[py]


def py():
    """挑一个装了 requests 的 Python（流水线要用）。"""
    cands = [os.environ.get("RPA_PYTHON"), sys.executable,
             "C:/Python314/python.exe", "C:/Python311/python.exe",
             "python3", "python"]
    for c in cands:
        if c and _has_requests(c):
            return c
    return sys.executable


def run_script(script, args, timeout=600):
    """跑 scripts/ 下的脚本，返回 (ok, 输出文本)。"""
    cmd = [py(), os.path.join(SCRIPTS, script)] + [str(a) for a in args]
    log("[mcp] exec:", " ".join(cmd))
    try:
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "执行超时（%ss）：%s" % (timeout, script)
    out = (r.stdout or b"").decode("utf-8", errors="replace")
    err = (r.stderr or b"").decode("utf-8", errors="replace")
    text = (out + ("\n" + err if err.strip() else "")).strip()
    return r.returncode == 0, text


def list_specs():
    """直接读 flows/*.json（stdlib，不依赖 flow_generator）。"""
    items = []
    if not os.path.isdir(FLOWS):
        return items
    for fn in sorted(os.listdir(FLOWS)):
        if not fn.endswith(".json"):
            continue
        p = os.path.join(FLOWS, fn)
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        items.append({
            "file": fn,
            "name": d.get("name") or fn[:-5],
            "nodes": len(d.get("nodes") or []),
            "examples": (d.get("nl_examples") or [])[:3],
            "params": [x.get("name") for x in (d.get("params") or [])],
        })
    return items


def ok(text):
    return {"content": [{"type": "text", "text": text}], "isError": False}


def bad(text):
    return {"content": [{"type": "text", "text": text}], "isError": True}


# --------------------------------------------------------------------------
# 工具实现
# --------------------------------------------------------------------------
def tool_setup(a):
    """写配置 + 自检。用户发来 KEY 之后调用这个就完成配置。"""
    cfg_path = os.path.join(ROOT, "rpa_config.json")
    cfg = {}
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}

    def put(section, key, val):
        if val in (None, ""):
            return
        cfg.setdefault(section, {})[key] = val

    host = a.get("host") or ""
    put("gateway", "host", host)
    put("gateway", "scheme", a.get("scheme"))
    if host:
        put("openapi", "base", "http://%s/api/rpa-openapi" % host)
    put("openapi", "api_key", a.get("api_key"))
    put("client", "python", a.get("client_python"))
    if a.get("local_gateway_port"):
        put("client", "local_gateway_port", int(a["local_gateway_port"]))
    put("defaults", "robot_id", a.get("robot_id"))

    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    good, out = run_script("doctor.py", [], timeout=180)
    tail = "\n".join(out.splitlines()[-14:])
    return ok("已写入配置：%s\n\n环境自检：\n%s" % (cfg_path, tail))


def tool_doctor(a):
    good, out = run_script("doctor.py", [], timeout=180)
    return ok(out) if good else bad(out)


def tool_list_flows(a):
    specs = list_specs()
    lines = ["共 %d 条流程规格（flows/*.json）：\n" % len(specs)]
    for s in specs:
        lines.append("· **%s**  `%s`（%d 节点）" % (s["name"], s["file"], s["nodes"]))
        if s["examples"]:
            lines.append("    例：%s" % " / ".join(s["examples"]))
        if s["params"]:
            lines.append("    参数：%s" % ", ".join(s["params"]))
    lines.append("\n用法：把用户的话直接给 rpa_generate（先只看命中）或 rpa_run（直接跑）。")
    return ok("\n".join(lines))


def _write_spec(spec_json):
    """把 AI 产出的规格落盘到 flows/，返回文件路径。"""
    if isinstance(spec_json, str):
        d = json.loads(spec_json)
    else:
        d = spec_json
    name = (d.get("name") or "").strip()
    if not name:
        raise ValueError("规格缺少 name")
    import re
    safe = re.sub(r"[^\w\u4e00-\u9fa5-]+", "_", name)
    os.makedirs(FLOWS, exist_ok=True)
    path = os.path.join(FLOWS, safe + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    return path


def tool_generate(a):
    """自然语言 → 匹配/生成规格 → 结构校验（不落库、不执行）。"""
    spec_json = a.get("spec_json")
    nl = a.get("nl") or ""
    if spec_json:
        try:
            path = _write_spec(spec_json)
        except Exception as e:
            return bad("规格写入失败：%s" % e)
        args = ["--spec", path, "--stage", "draft"]
    elif nl:
        args = ["--nl", nl, "--stage", "draft"]
    else:
        return bad("要么给 nl（自然语言），要么给 spec_json（流程规格 JSON）")

    good, out = run_script("ai_flow.py", args, timeout=300)
    return ok(out) if good else bad(out)


def tool_run(a):
    """完整链路：校验 → 预检 → 落库 → 本地真跑 → 评审单。"""
    spec = a.get("spec")
    nl = a.get("nl") or ""
    robot = a.get("robot_id")
    if not spec and not nl:
        return bad("要么给 spec（flows/xxx.json），要么给 nl")
    args = []
    if spec:
        args += ["--spec", spec if os.path.isabs(spec) else os.path.join(FLOWS, os.path.basename(spec))]
    else:
        args += ["--nl", nl]
    if robot:
        args += ["--robot-id", str(robot)]
    if a.get("no_run"):
        args.append("--no-run")
    good, out = run_script("ai_flow.py", args, timeout=900)
    return ok(out) if good else bad(out)


def tool_publish(a):
    spec = a.get("spec")
    ver = a.get("version")
    if not spec or not ver:
        return bad("需要 spec（规格文件）和 version（版本号，要大于最新）")
    args = ["--spec", spec if os.path.isabs(spec) else os.path.join(FLOWS, os.path.basename(spec)),
            "--stage", "publish", "--version", str(ver)]
    if a.get("robot_id"):
        args += ["--robot-id", str(a["robot_id"])]
    good, out = run_script("ai_flow.py", args, timeout=600)
    return ok(out) if good else bad(out)


def tool_invoke(a):
    spec = a.get("spec")
    if not spec:
        return bad("需要 spec（已发布版本的规格文件）")
    args = ["--spec", spec if os.path.isabs(spec) else os.path.join(FLOWS, os.path.basename(spec)),
            "--stage", "invoke"]
    if a.get("params"):
        p = a["params"]
        args += ["--params", p if isinstance(p, str) else json.dumps(p, ensure_ascii=False)]
    if a.get("robot_id"):
        args += ["--robot-id", str(a["robot_id"])]
    good, out = run_script("ai_flow.py", args, timeout=900)
    return ok(out) if good else bad(out)


def tool_make_module(a):
    """本地 .py → 平台 Python 子流程模块（模块按机器人隔离，换环境要重建）。"""
    pyfile = a.get("py")
    if not pyfile:
        return bad("需要 py（本地 Python 文件路径）")
    args = ["--py", pyfile]
    if a.get("new_robot"):
        args += ["--new-robot", a["new_robot"]]
    elif a.get("robot_id"):
        args += ["--robot", str(a["robot_id"])]
    else:
        return bad("需要 robot_id 或 new_robot")
    if a.get("name"):
        args += ["--name", a["name"]]
    if a.get("spec"):
        args += ["--spec", a["spec"]]
    if a.get("module_id"):
        args += ["--module-id", str(a["module_id"])]
    good, out = run_script("make_module.py", args, timeout=300)
    return ok(out) if good else bad(out)


def tool_atom_catalog(a):
    """查可用原子：按域 / 关键词 / 单个原子。写新流程前先看这个。"""
    args = []
    if a.get("domain"):
        args += ["--domain", a["domain"]]
    if a.get("atom"):
        args += ["--atom", a["atom"]]
    if a.get("search"):
        args += ["--search", a["search"]]
    if a.get("detail"):
        args.append("--detail")
    good, out = run_script("atom_catalog.py", args, timeout=180)
    return ok(out) if good else bad(out)


def tool_lint_spec(a):
    """校验流程规格：报错带「第几个节点 + 怎么改」，AI 据此自纠错。"""
    spec_json = a.get("spec_json")
    spec = a.get("spec")
    tmp = None
    if spec_json:
        # 只校验不落盘：写临时文件，避免半成品污染 flows/
        import tempfile
        try:
            d = json.loads(spec_json) if isinstance(spec_json, str) else spec_json
        except Exception as e:
            return bad("规格不是合法 JSON：%s" % e)
        fd, tmp = tempfile.mkstemp(suffix=".json", prefix="spec_draft_")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        path = tmp
    elif spec:
        path = spec if os.path.isabs(spec) else os.path.join(FLOWS, os.path.basename(spec))
    else:
        return bad("要么给 spec（flows/xxx.json），要么给 spec_json")
    try:
        good, out = run_script("spec_lint.py", [path], timeout=180)
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)
    return ok(out) if good else bad(out)


# --------------------------------------------------------------------------
# 工具清单（name → (处理函数, 说明, inputSchema)）
# --------------------------------------------------------------------------
TOOLS = {
    "rpa_setup": (
        tool_setup,
        "配置环境：写入 rpa_config.json（服务端地址 / API Key / 机器人 id / 客户端 Python），然后自动跑一遍环境自检。"
        "用户发来 KEY 之后调用这个。",
        {
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "服务端网关地址，形如 192.168.76.130:32742（留空则用设计器 conf.yaml 自动探测）"},
                "scheme": {"type": "string", "description": "http 或 https，默认 http"},
                "api_key": {"type": "string", "description": "openapi API Key（平台「开放平台/API 密钥」页生成）"},
                "robot_id": {"type": "string", "description": "默认机器人 id"},
                "client_python": {"type": "string", "description": "设计器自带 Python 路径，留空自动探测"},
                "local_gateway_port": {"type": "integer", "description": "设计器本地网关端口，默认 13159"},
            },
        },
    ),
    "rpa_doctor": (
        tool_doctor,
        "环境自检：逐项检查配置/服务端可达/登录态/本地网关/机器人/API Key/依赖，每项给修法。换机器后第一条命令。",
        {"type": "object", "properties": {}},
    ),
    "rpa_list_flows": (
        tool_list_flows,
        "列出当前可用的流程规格（flows/*.json）：名称、示例话术、参数。生成新流程前先看这个，能复用就别新建。",
        {"type": "object", "properties": {}},
    ),
    "rpa_generate": (
        tool_generate,
        "把一句自然语言变成流程并做结构校验（不落库、不执行）。也可以直接提交 AI 写好的规格 JSON 校验。",
        {
            "type": "object",
            "properties": {
                "nl": {"type": "string", "description": "用户的自然语言需求，如「把这张表按部门拆成多个文件」"},
                "spec_json": {"type": "string", "description": "AI 产出的流程规格 JSON 字符串（与 nl 二选一）"},
            },
        },
    ),
    "rpa_run": (
        tool_run,
        "完整交付链路：校验 → 运行前预检 → 写入机器人主流程 → 本地真跑 → 生成评审单。"
        "这是「生成固定 RPA 流程」的主入口，跑完等人工评审。",
        {
            "type": "object",
            "properties": {
                "nl": {"type": "string", "description": "自然语言需求（会先匹配已有规格并抽参数）"},
                "spec": {"type": "string", "description": "规格文件名，如 Excel按条件拆表小流程.json"},
                "robot_id": {"type": "string", "description": "目标机器人 id，留空用配置默认值"},
                "no_run": {"type": "boolean", "description": "true = 只落库不本地真跑"},
            },
        },
    ),
    "rpa_publish": (
        tool_publish,
        "发布机器人版本（必须人工评审通过后才用）。发布后 openapi 才能调用。",
        {
            "type": "object",
            "properties": {
                "spec": {"type": "string", "description": "规格文件名"},
                "version": {"type": "integer", "description": "版本号，要大于当前最新"},
                "robot_id": {"type": "string", "description": "机器人 id"},
            },
            "required": ["spec", "version"],
        },
    ),
    "rpa_invoke": (
        tool_invoke,
        "用 API Key 触发已发布版本的执行（不经过设计器界面），并返回执行结果与产出文件。",
        {
            "type": "object",
            "properties": {
                "spec": {"type": "string", "description": "规格文件名"},
                "params": {"type": "object", "description": "运行参数，覆盖规格默认值"},
                "robot_id": {"type": "string", "description": "机器人 id"},
            },
            "required": ["spec"],
        },
    ),
    "rpa_make_module": (
        tool_make_module,
        "把本地 Python 文件上传成平台的「Python 子流程模块」，并把新 module_id 写回规格。"
        "模块按机器人隔离，换环境/换机器人必须重建。",
        {
            "type": "object",
            "properties": {
                "py": {"type": "string", "description": "本地 .py 路径（相对项目根或绝对）"},
                "robot_id": {"type": "string", "description": "目标机器人 id"},
                "new_robot": {"type": "string", "description": "先新建一个机器人，给名字"},
                "name": {"type": "string", "description": "模块名"},
                "spec": {"type": "string", "description": "把 module_id 写回这个规格文件"},
                "module_id": {"type": "string", "description": "已有模块 id（覆盖内容时给）"},
            },
            "required": ["py"],
        },
    ),
    "rpa_atom_catalog": (
        tool_atom_catalog,
        "查可用原子（357 个，按域分组）。写新流程前必须先用它挑原子：不给参数=看有哪些域；"
        "给 domain=看该域的原子（detail=true 带必填参数与枚举值）；给 atom=看单个原子完整参数表；"
        "给 search=按关键词找。",
        {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "域，如 Excel / File / Docx / PDF / Code / Script"},
                "atom": {"type": "string", "description": "原子全名，如 Excel.read_excel"},
                "search": {"type": "string", "description": "关键词，如 拆分 / 合并"},
                "detail": {"type": "boolean", "description": "true = 带出必填参数与可选枚举值"},
            },
        },
    ),
    "rpa_lint_spec": (
        tool_lint_spec,
        "校验流程规格，报错带「第几个节点 + 具体怎么改」（原子不存在给相近候选、缺参数、枚举非法、"
        "变量未产出、循环未闭合、module_id 没参数化）。AI 写完规格先过这一关，改到无错误再 rpa_run。",
        {
            "type": "object",
            "properties": {
                "spec": {"type": "string", "description": "规格文件名，如 flows/x.json"},
                "spec_json": {"type": "string", "description": "直接提交规格 JSON 字符串（会先落盘再校验）"},
            },
        },
    ),
}


# --------------------------------------------------------------------------
# JSON-RPC 分发
# --------------------------------------------------------------------------
def handle(req):
    method = req.get("method")
    rid = req.get("id")
    params = req.get("params") or {}

    def result(obj):
        return {"jsonrpc": "2.0", "id": rid, "result": obj}

    def error(code, msg):
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": msg}}

    if method == "initialize":
        return result({
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
        })
    if method == "notifications/initialized" or rid is None:
        return None  # 通知，不回
    if method == "ping":
        return result({})
    if method == "tools/list":
        return result({"tools": [
            {"name": n, "description": d, "inputSchema": s}
            for n, (_fn, d, s) in TOOLS.items()
        ]})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name not in TOOLS:
            return error(-32602, "未知工具：%s" % name)
        fn = TOOLS[name][0]
        try:
            return result(fn(args))
        except Exception as e:
            log("[mcp] tool error:", name, repr(e))
            return result(bad("工具执行异常：%s: %s" % (type(e).__name__, e)))
    return error(-32601, "不支持的方法：%s" % method)


def main():
    log("[mcp] astron-rpa-flow MCP server 启动，根目录：%s" % ROOT)
    log("[mcp] 使用 Python：%s" % py())
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception as e:
            log("[mcp] JSON 解析失败：", e)
            sys.stdout.write(json.dumps({
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32700, "message": "Parse error"}}) + "\n")
            sys.stdout.flush()
            continue
        resp = handle(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
