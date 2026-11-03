#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一键安装：把「AI 操作 RPA」装到这台机器上（装完就能用自然语言生成流程）。

## 用户视角只有两步

1. 说一句「帮我装这个项目」（AI 跑本脚本）
2. 把 API Key 发过来（AI 再跑一次带 `--key`）

## 它到底做了什么（全部幂等，重复跑不会坏）

| 步骤 | 动作 | 可跳过 |
| --- | --- | --- |
| 1 | 检查 Python 版本 + 装 `requirements.txt`（主链路只要 requests） | `--skip-deps` |
| 2 | 写 `rpa_config.json`（服务端地址 / Key / 机器人 id / 客户端 Python） | — |
| 3 | 把技能包复制到**客户端真正读取的技能目录**（见下） | `--no-skill` |
| 4 | 把 MCP 服务登记进**客户端真正读取的 mcp.json**（先备份） | `--no-register-mcp` |
| 5 | 跑 `doctor.py` 自检并打印结论 | `--skip-doctor` |

## 装到哪（2026-11-03 修正，原实现装错目录）

不同客户端读的目录不一样，**装错地方 = 装完不生效**（技能不触发、MCP 工具列表里也没有）：

| 客户端 | 技能目录 | MCP 配置 |
| --- | --- | --- |
| WorkBuddy 桌面客户端 | `~/.workbuddy/skills/` | `~/.workbuddy/mcp.json` |
| CodeBuddy CLI（历史路径） | `~/.workbuddy-ai/skills/` | `~/.workbuddy-ai/mcp.json` |

本脚本按「父目录存在」自动探测，**两处都存在的就两处都写**（幂等，不会互相覆盖）；
要指定就 `--skill-dir` / `--mcp-file`。

## 用法

    # 只装依赖 + 技能包 + MCP（还不知道 Key）
    python scripts/install.py

    # 拿到 Key 后补配置
    python scripts/install.py --key <API_KEY> --host 192.168.76.130:32742 --robot-id <robotId>

    # 给别的 MCP 客户端（Claude Desktop 等）生成配置片段
    python scripts/install.py --print-mcp-config
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
CONFIG_PATH = os.path.join(ROOT, "rpa_config.json")
SKILL_SRC = os.path.join(ROOT, "skill", "astron-rpa-flow")
MCP_SERVER = os.path.join(ROOT, "mcp", "server.py")

HOME = os.path.expanduser("~")

# 客户端技能目录 / MCP 配置的候选落点。**顺序即优先级**（第一个是主落点）。
# 2026-11-03 实测教训：原来只写 ~/.workbuddy-ai/，而 WorkBuddy 桌面客户端读的是
#   ~/.workbuddy/skills + ~/.workbuddy/mcp.json → 装完技能不触发、MCP 工具列表里也没有，
#   但 doctor 全绿（因为它当时不查这两项）。所以改成「探测父目录 + 多落点都写」。
SKILL_CANDIDATES = [
    (os.path.join(HOME, ".workbuddy", "skills"), "WorkBuddy 客户端"),
    (os.path.join(HOME, ".workbuddy-ai", "skills"), "CodeBuddy CLI（历史路径）"),
]
MCP_CANDIDATES = [
    (os.path.join(HOME, ".workbuddy", "mcp.json"), "WorkBuddy 客户端"),
    (os.path.join(HOME, ".workbuddy-ai", "mcp.json"), "CodeBuddy CLI（历史路径）"),
]

STEPS = []


def pick_targets(candidates, override=""):
    """挑出真实存在的落点：父目录在 → 就用它；给了 override → 只用它。

    父目录都不在（客户端没装 / HOME 不同）→ 退回第一个主落点，由调用方创建。
    """
    if override:
        return [(override, "手动指定")]
    out = [(p, label) for p, label in candidates if os.path.isdir(os.path.dirname(p))]
    return out or [candidates[0]]


def step(name, state, detail=""):
    STEPS.append({"name": name, "state": state, "detail": detail})
    icon = {"ok": "✅", "skip": "⏭️", "warn": "⚠️", "fail": "❌"}.get(state, "·")
    print("%s %-18s %s" % (icon, name, detail))


# ---------------------------------------------------------------- 1. 依赖
def install_deps(skip=False):
    if skip:
        step("依赖", "skip", "--skip-deps")
        return True
    try:
        import requests  # noqa: F401
        step("依赖", "ok", "requests 已安装（%s）" % requests.__version__)
        return True
    except ImportError:
        pass
    req = os.path.join(ROOT, "requirements.txt")
    if not os.path.exists(req):
        step("依赖", "warn", "没有 requirements.txt，跳过")
        return True
    print("    正在安装依赖（首次约 10~30 秒）...")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-r", req, "-q"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        step("依赖", "fail", "pip 安装失败：" + (r.stderr or "").strip()[:200])
        print("    手动装：%s -m pip install -r requirements.txt" % sys.executable)
        return False
    try:
        import importlib
        importlib.invalidate_caches()
        import requests  # noqa: F401
        step("依赖", "ok", "requests 安装成功（%s）" % requests.__version__)
        return True
    except ImportError:
        step("依赖", "warn", "装完了但当前进程还导不到，重开终端再试")
        return True


# ---------------------------------------------------------------- 2. 配置
def write_config(key="", host="", robot_id="", client_python="", port=0, scheme=""):
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}

    def put(sec, k, v):
        if v in (None, "", 0):
            return False
        cfg.setdefault(sec, {})[k] = v
        return True

    changed = []
    if put("gateway", "host", host):
        changed.append("gateway.host")
    if put("gateway", "scheme", scheme):
        changed.append("gateway.scheme")
    if host:
        cfg.setdefault("openapi", {})["base"] = "http://%s/api/rpa-openapi" % host
        changed.append("openapi.base")
    if put("openapi", "api_key", key):
        changed.append("openapi.api_key")
    if put("client", "python", client_python):
        changed.append("client.python")
    if put("client", "local_gateway_port", int(port) if port else 0):
        changed.append("client.local_gateway_port")
    if put("defaults", "robot_id", robot_id):
        changed.append("defaults.robot_id")

    if not os.path.exists(CONFIG_PATH) and not changed:
        # 首次安装、没给任何参数：生成一份空壳（字段留空，靠 conf.yaml 自动探测）
        cfg.setdefault("gateway", {}).setdefault("host", "")
        cfg.setdefault("openapi", {}).setdefault("base", "")
        cfg.setdefault("openapi", {}).setdefault("api_key", "")
        cfg.setdefault("client", {}).update({"python": "", "local_gateway_port": 13159})
        cfg.setdefault("defaults", {}).setdefault("robot_id", "")

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    step("配置", "ok" if changed else "skip",
         ("写入 %s" % ", ".join(changed)) if changed else "无新参数，保持原样：%s" % CONFIG_PATH)
    return cfg


# ---------------------------------------------------------------- 3. 技能包
def install_skill(skip=False, skill_dir=""):
    if skip:
        step("技能包", "skip", "--no-skill")
        return
    if not os.path.isdir(SKILL_SRC):
        step("技能包", "warn", "仓库里没有 skill/astron-rpa-flow/，跳过")
        return
    for base, label in pick_targets(SKILL_CANDIDATES, skill_dir):
        dst = os.path.join(base, "astron-rpa-flow") if os.path.basename(base) != "astron-rpa-flow" else base
        os.makedirs(dst, exist_ok=True)
        for name in os.listdir(SKILL_SRC):
            s = os.path.join(SKILL_SRC, name)
            d = os.path.join(dst, name)
            if os.path.isdir(s):
                shutil.copytree(s, d, dirs_exist_ok=True)
            else:
                shutil.copy2(s, d)
        # 告诉技能「项目在哪」（技能被复制到别处后也能找到本仓库）
        with open(os.path.join(dst, "PROJECT_ROOT.txt"), "w", encoding="utf-8") as f:
            f.write(ROOT)
        step("技能包", "ok", "%s ← %s" % (dst, label))


# ---------------------------------------------------------------- 4. MCP 登记
def mcp_entry():
    return {
        "command": sys.executable,
        "args": [MCP_SERVER],
        "env": {"RPA_PYTHON": sys.executable},
    }


def _register_one(path, label):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
        except Exception:
            # 坏了不覆盖，先备份
            bak = path + ".bak"
            shutil.copy2(path, bak)
            step("MCP 登记", "warn", "%s 无法解析，已备份到 %s，跳过" % (path, bak))
            return
    servers = data.setdefault("mcpServers", {})
    old = servers.get("astron-rpa")
    servers["astron-rpa"] = mcp_entry()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    step("MCP 登记", "ok", "%s（%s · %s）" % (path, "更新" if old else "新增", label))


def register_mcp(skip=False, mcp_file=""):
    if skip:
        step("MCP 登记", "skip", "--no-register-mcp")
        return
    for path, label in pick_targets(MCP_CANDIDATES, mcp_file):
        _register_one(path, label)


def print_mcp_config():
    snippet = {"mcpServers": {"astron-rpa": mcp_entry()}}
    print(json.dumps(snippet, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------- 5. 自检
def doctor(skip=False):
    if skip:
        step("环境自检", "skip", "--skip-doctor")
        return
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "doctor.py")],
                       cwd=ROOT, capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    lines = [x for x in out.splitlines() if x.strip()]
    bad = [x for x in lines if "❌" in x]
    warn = [x for x in lines if "⚠️" in x and not x.startswith("结论")]
    if bad:
        step("环境自检", "fail", "%d 项阻塞" % len(bad))
        for x in bad:
            print("     " + x.strip())
    elif warn:
        step("环境自检", "warn", "可跑主链路，%d 项能力不可用" % len(warn))
    else:
        step("环境自检", "ok", "全部通过")
    tail = [x for x in lines if x.startswith("结论")]
    if tail:
        print("     " + tail[-1].strip())


def _mask(cfg):
    """给机器读的输出里不能带明文凭据（会进日志/终端历史）。"""
    import copy
    c = copy.deepcopy(cfg or {})
    for sec, keys in (("openapi", ("api_key",)),
                      ("db", ("password", "ssh_password"))):
        for k in keys:
            v = (c.get(sec) or {}).get(k)
            if v:
                c[sec][k] = v[:4] + "***" if len(v) > 6 else "***"
    return c


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="一键安装 AI 操作 RPA（依赖 / 配置 / 技能包 / MCP）")
    ap.add_argument("--key", default="", help="openapi API Key")
    ap.add_argument("--host", default="", help="服务端网关，如 192.168.76.130:32742")
    ap.add_argument("--scheme", default="", help="http / https")
    ap.add_argument("--robot-id", default="", help="默认机器人 id")
    ap.add_argument("--client-python", default="", help="设计器自带 Python 路径")
    ap.add_argument("--local-port", type=int, default=0, help="设计器本地网关端口（默认 13159）")
    ap.add_argument("--skip-deps", action="store_true")
    ap.add_argument("--no-skill", action="store_true")
    ap.add_argument("--skill-dir", default="", help="技能装到哪（默认自动探测 ~/.workbuddy/skills 等）")
    ap.add_argument("--no-register-mcp", action="store_true")
    ap.add_argument("--mcp-file", default="", help="MCP 配置写哪个文件（默认自动探测 ~/.workbuddy/mcp.json 等）")
    ap.add_argument("--skip-doctor", action="store_true")
    ap.add_argument("--print-mcp-config", action="store_true", help="只打印 MCP 配置片段后退出")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    a = ap.parse_args()

    if a.print_mcp_config:
        print_mcp_config()
        return 0

    if not a.json:
        print("=" * 62)
        print("AI 操作 RPA · 一键安装")
        print("  项目目录：%s" % ROOT)
        print("  Python  ：%s" % sys.executable)
        print("=" * 62)

    ok_deps = install_deps(a.skip_deps)
    cfg = write_config(a.key, a.host, a.robot_id, a.client_python, a.local_port, a.scheme)
    install_skill(a.no_skill, a.skill_dir)
    register_mcp(a.no_register_mcp, a.mcp_file)
    doctor(a.skip_doctor)

    if a.json:
        print(json.dumps({"steps": STEPS, "config": _mask(cfg), "ok": ok_deps}, ensure_ascii=False))
        return 0 if ok_deps else 1

    print("=" * 62)
    has_key = bool((cfg.get("openapi") or {}).get("api_key"))
    if not has_key:
        print("下一步：把 API Key 发我，我执行")
        print("  python scripts/install.py --key <API_KEY> [--host <host:port>] [--robot-id <id>]")
    else:
        print("已配置好。现在可以直接说人话了，例如：")
        print('  python scripts/ai_flow.py --nl "把这张表按部门拆成多个文件"')
        print("  或在 AI 里直接说需求（技能包 / MCP 已就位）")
    print("=" * 62)
    return 0 if ok_deps else 1


if __name__ == "__main__":
    sys.exit(main())
