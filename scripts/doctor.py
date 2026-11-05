#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""环境自检（doctor）：换到全新环境后**第一条要跑的命令**。

## 为什么需要

用户 2026-10-30 明确：这套东西要做成可迁移的插件形态，换环境要能直接跑。
但换环境会变的东西有六类（服务端地址、登录态、API Key、设计器路径、机器人 id、
Python 子流程模块 id），任何一项不对都会表现成「流程跑不通」，很难一眼看出是哪一项。

`doctor` 把这些**逐项探测 + 给出修法**，全部只读，不改任何东西。

## 检查项

| 项 | 级别 | 不通过的后果 |
| --- | --- | --- |
| 配置解析（gateway / openapi / 客户端 python） | ❌ | 后面的都做不了 |
| 服务端 TCP 可达 | ❌ | 接口全失败 |
| 设计器登录态（cookie 里有该 host 的 SESSION） | ❌ | 写流程会 401 |
| 会话有效（调一次 design-list） | ❌ | 同上 |
| 设计器本地网关（13159） | ⚠️ | 只是本地真跑不了，落库/发布仍可 |
| 客户端自带 Python | ⚠️ | 本地真跑不了 |
| 目标机器人 + 主流程 | ❌ | 落库没地方写 |
| openapi API Key | ⚠️ | invoke 阶段用不了 |
| Python 依赖（requests / paramiko） | ⚠️ | 对应功能不可用（playwright 已不需要） |
| 技能包（AI 侧接线） | ⚠️ | AI 不会自动触发这套能力（只能用命令行） |
| MCP 登记（AI 侧接线） | ⚠️ | AI 看不到 `rpa_*` 工具，只能自己拼命令 |
| SQL 通道（可选） | ℹ️ | 只影响 execute_log 读取与模块 SQL 补丁 |

## 用法

```
python scripts/doctor.py                    # 全量自检
python scripts/doctor.py --robot-id 123     # 顺便校验某个机器人
```
"""
import argparse
import importlib
import json
import os
import socket
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import rpa_config as cfg

OK, WARN, BAD, INFO = "ok", "warn", "bad", "info"
_ICON = {OK: "✅", WARN: "⚠️ ", BAD: "❌", INFO: "ℹ️ "}

_rows = []


def add(level, name, detail="", fix=""):
    _rows.append({"level": level, "name": name, "detail": detail, "fix": fix})
    return level == OK


def tcp(hostport, timeout=2.0):
    try:
        host, _, port = hostport.rpartition(":")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return s.connect_ex((host, int(port))) == 0
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser(description="RPA 工具链环境自检（只读）")
    ap.add_argument("--robot-id", default="", help="顺便校验这个机器人（不给则用配置里的默认）")
    args = ap.parse_args()

    print("=" * 70)
    print("RPA 工具链 环境自检")
    print(f"  项目根目录：{ROOT}")
    print(f"  配置文件  ：{cfg.CONFIG_PATH}（{'存在' if os.path.exists(cfg.CONFIG_PATH) else '不存在'}）")
    print("=" * 70)

    # ---------- 1. 配置 ----------
    host = cfg.host_port()
    conf_p = cfg.get("client.conf_yaml", "")
    conf_host, _, _ = cfg.remote_addr(conf_p)
    add(OK if conf_p else WARN, "客户端 conf.yaml",
        f"{conf_p} → remote_addr={conf_host or '未解析'}" if conf_p else "未找到",
        "设计器连哪个服务端由它决定：改 resources/conf.yaml 的 remote_addr；"
        "找不到安装目录时用 RPA_CLIENT_CONF 指定完整路径")

    if host:
        add(OK, "服务端地址", host, "来源见上（conf.yaml 优先）")
    else:
        add(BAD, "服务端地址", "未确定",
            "改设计器 conf.yaml 的 remote_addr，或设 RPA_HOST=ip:port")

    py = cfg.client_python()
    add(OK if py else WARN, "客户端 Python", py or "未找到",
        "装/修复 astron-rpa 设计器；或设 RPA_CLIENT_PYTHON 指向 python_core/python.exe")

    # ---------- 2. 网络与服务端 ----------
    if host:
        alive = tcp(host)
        add(OK if alive else BAD, "服务端可达", host if alive else f"{host} 连不上",
            "确认网络/VPN，确认网关端口（默认 32742）没变")
    else:
        add(BAD, "服务端可达", "跳过（没地址）")

    cookie = cfg.cookie_file()
    has_cookie = os.path.exists(cookie)
    add(OK if has_cookie else BAD, "设计器 cookie 文件", cookie if has_cookie else "不存在",
        "打开设计器登录一次；换机器必须重新登录")

    session_ok = False
    if has_cookie and host:
        try:
            import publish_flow as pf
            pf.load_session()
            session_ok = True
            add(OK, "登录态", f"cookie 里有 {host} 的 SESSION")
        except (SystemExit, Exception) as e:
            add(BAD, "登录态", str(e).splitlines()[0][:120],
                "在设计器里连一次该服务端（Cookie 按 host 存，换 IP 要重连）")
    else:
        add(BAD, "登录态", "跳过")

    # ---------- 3. 接口可用性 ----------
    if session_ok:
        try:
            import publish_flow as pf
            ck = pf.load_session()
            u = pf.whoami(ck)
            add(OK, "会话有效", f"当前用户 {u}")
            pf.ok(pf.api("/api/robot/robot-design/design-list",
                         {"pageNo": 1, "pageSize": 1}, ck), "列机器人")
            add(OK, "设计接口", "design-list 正常")
        except (SystemExit, Exception) as e:
            add(BAD, "会话有效", str(e)[:150], "重新登录设计器")
    else:
        add(BAD, "会话有效", "跳过")

    # ---------- 4. 本地执行链 ----------
    port = cfg.local_gateway_port()
    lg = tcp(f"127.0.0.1:{port}", timeout=1.5)
    add(OK if lg else WARN, "设计器本地网关", f"127.0.0.1:{port} " + ("在线" if lg else "未监听"),
        "本地真跑要靠它取流程/元素：启动设计器客户端即可（不影响落库和发布）")

    # ---------- 5. 目标机器人 ----------
    robot_id = args.robot_id or cfg.default_robot_id()
    if session_ok and robot_id:
        try:
            import publish_flow as pf
            ck = pf.load_session()
            procs = pf.list_processes(ck, robot_id)
            names = [(p.get("processName") or p.get("name")) for p in procs]
            has_main = "主流程" in names
            add(OK if has_main else BAD, "目标机器人", f"{robot_id} 流程 {names}",
                "设计器点「编辑」默认打开主流程；没有主流程就新建一个机器人")
        except (SystemExit, Exception) as e:
            add(BAD, "目标机器人", f"{robot_id} → {str(e)[:120]}",
                "确认 robot_id 正确（rpa_config.json 的 defaults.robot_id）")
    elif not robot_id:
        add(WARN, "目标机器人", "未指定",
            "在 rpa_config.json 写 defaults.robot_id，或命令行 --robot-id")

    # ---------- 6. openapi ----------
    key = cfg.api_key()
    if not key:
        add(WARN, "openapi API Key", "未配置",
            "在平台「开放平台」生成 Key，写进 rpa_config.json 的 openapi.api_key")
    else:
        try:
            import requests
            r = requests.get(f"{cfg.openapi_base()}/workflows/get",
                             headers={"X-API-Key": key}, timeout=10)
            if r.status_code == 200:
                n = len((r.json().get("data") or []))
                add(OK, "openapi", f"HTTP 200，已登记 {n} 个可执行流程")
            else:
                add(WARN, "openapi", f"HTTP {r.status_code} {r.text[:80]}",
                    "Key 无效或服务未起；迁移到新环境要重新生成 Key")
        except Exception as e:
            add(WARN, "openapi", f"{type(e).__name__}: {e}", "检查 openapi 服务与网络")

    # ---------- 7. Python 依赖 ----------
    # playwright 只有「老的元素重定位器」用；网页元素现在走 element_pick.py（调浏览器插件，
    # 不需要 playwright），所以不再提示安装，免得让人以为主链路缺依赖（2026-10-31）。
    for mod, why, fix in (
        ("requests", "HTTP 调用（落库/发布/invoke）", "pip install requests"),
        ("paramiko", "SQL 通道（可选）", "pip install paramiko"),
    ):
        try:
            importlib.import_module(mod)
            add(OK, f"依赖 {mod}", "已安装")
        except ImportError:
            fix_txt = fix
            if mod == "requests":
                # 2026-10-31 实测：用系统 Python 跑 doctor 会因为缺 requests 报一堆假故障
                #（登录态/会话/openapi 跟着一起报错），其实客户端自带 python 里有。
                fix_txt = 'pip install requests；或直接用客户端自带解释器跑："%APPDATA%/astron-rpa/python_core/python.exe" scripts/doctor.py'
            add(WARN if mod != "requests" else BAD, f"依赖 {mod}", f"缺失（{why}）", fix_txt)

    # ---------- 8. SQL 通道 ----------
    if not cfg.db_conf():
        add(INFO, "SQL 通道", "未启用（默认）",
            "主链路不需要它；只有读 execute_log 或给历史模块打补丁才要开 db.enabled")
    else:
        try:
            from element_relocator import ServerDb
            n = ServerDb().sql_rows("select 1")[0][0]
            add(OK if n == "1" else WARN, "SQL 通道", f"select 1 → {n}")
        except (SystemExit, Exception) as e:
            add(WARN, "SQL 通道", str(e)[:150], "检查 ssh 凭据/容器名；或干脆关掉 db.enabled")

    # ---------- 9. AI 侧接线：技能包 + MCP 登记 ----------
    #
    # 2026-11-03 实测出的「假绿灯」：这门自检原来 14 项全绿，但 AI 那边什么都没接到 ——
    # 技能装到了 ~/.workbuddy-ai/skills（CodeBuddy CLI 老路径），WorkBuddy 客户端读的却是
    # ~/.workbuddy/skills；MCP 同理。结果：命令行能跑，AI 却既没有触发词、也没有 rpa_* 工具。
    # 所以这两项必须进自检，否则「环境就绪」是句假话。
    #
    # 只读：不装、不改，只报现状 + 给修法（修法是 `python scripts/install.py`）。
    home = os.path.expanduser("~")
    skill_dirs = [
        (os.path.join(home, ".workbuddy", "skills", "astron-rpa-flow"), "WorkBuddy 客户端", True),
        (os.path.join(home, ".workbuddy-ai", "skills", "astron-rpa-flow"), "CodeBuddy CLI", False),
    ]
    found, broken = [], []
    for d, label, is_primary in skill_dirs:
        if not os.path.isdir(d):
            continue
        skill_md = os.path.join(d, "SKILL.md")
        root_file = os.path.join(d, "PROJECT_ROOT.txt")
        proj = ""
        if os.path.exists(root_file):
            try:
                with open(root_file, encoding="utf-8") as f:
                    proj = f.read().strip()
            except Exception:
                proj = ""
        ok_root = bool(proj) and os.path.isfile(os.path.join(proj, "scripts", "ai_flow.py"))
        if os.path.isfile(skill_md) and ok_root:
            found.append((label, is_primary, d))
        else:
            broken.append((label, d, "缺 SKILL.md" if not os.path.isfile(skill_md)
                           else "PROJECT_ROOT.txt 指向失效：%r" % (proj or "(空)")))

    if broken:
        add(WARN, "技能包", "装了但不可用：" + "；".join("%s %s" % (b[0], b[2]) for b in broken),
            "重跑 python scripts/install.py（会重写 PROJECT_ROOT.txt 指向本仓库）")
    elif not found:
        add(WARN, "技能包", "未安装（AI 不会自动触发这套能力）",
            "python scripts/install.py（自动装到客户端读取的技能目录）")
    elif not any(p for _, p, _ in found):
        # 只在 CodeBuddy CLI 的老目录里有 → WorkBuddy 客户端读不到，等于没装
        add(WARN, "技能包", "只装到了 %s，客户端读的是 ~/.workbuddy/skills" % found[0][0],
            "python scripts/install.py（会同时写客户端目录）")
    else:
        add(OK, "技能包", "；".join("%s → %s" % (lb, d) for lb, p, d in found))

    mcp_files = [
        (os.path.join(home, ".workbuddy", "mcp.json"), "WorkBuddy 客户端", True),
        (os.path.join(home, ".workbuddy-ai", "mcp.json"), "CodeBuddy CLI", False),
    ]
    mcp_ok, mcp_bad = [], []
    for p, label, is_primary in mcp_files:
        if not os.path.isfile(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                servers = (json.load(f) or {}).get("mcpServers") or {}
        except Exception as e:
            mcp_bad.append((label, "%s 解析失败：%s" % (p, e)))
            continue
        entry = servers.get("astron-rpa")
        if not entry:
            mcp_bad.append((label, "%s 里没有 astron-rpa 条目" % os.path.basename(p)))
            continue
        srv = (entry.get("args") or [""])[0]
        if srv and not os.path.isfile(srv):
            mcp_bad.append((label, "server.py 路径失效：%s" % srv))
            continue
        mcp_ok.append((label, is_primary, p))

    if mcp_bad:
        add(WARN, "MCP 登记", "；".join("%s %s" % (lb, msg) for lb, msg in mcp_bad),
            "重跑 python scripts/install.py（会重写 server.py 绝对路径）")
    elif not mcp_ok:
        add(WARN, "MCP 登记", "未登记（AI 看不到 rpa_* 工具）",
            "python scripts/install.py，然后在客户端「连接器」里信任该 MCP 服务")
    elif not any(p for _, p, _ in mcp_ok):
        add(WARN, "MCP 登记", "只登记在 %s，客户端读的是 ~/.workbuddy/mcp.json" % mcp_ok[0][0],
            "python scripts/install.py（会同时写客户端配置）")
    else:
        add(OK, "MCP 登记", "；".join("%s → %s" % (lb, path) for lb, p, path in mcp_ok))

    # ---------- 版本 ----------
    # 报障时贴 doctor 输出 → 顺手带上版本号，省掉「你跑的是哪版」这一轮往返。
    # 远端取不到（离线）不算问题：多人环境里离线是常态。
    try:
        import version as verchk
        r = verchk.check()
        me, rv = r["local"], r.get("remote_version")
        tag = "v%s (%s)" % (me["version"], me["commit"])
        if r["level"] == "block":
            add(BAD, "版本", "%s → 远端 v%s，必须更新（breaking 或低于最低支持）" % (tag, rv),
                "python scripts/install.py --update")
        elif r["level"] == "stale":
            add(WARN, "版本", "%s → 远端 v%s（落后，可继续用）" % (tag, rv),
                "python scripts/install.py --update")
        elif r["level"] == "unknown":
            add(OK, "版本", "%s · 远端未配置或不可达（不影响使用）" % tag)
        else:
            add(OK, "版本", "%s · 已是最新" % tag)
    except Exception as e:  # 版本模块坏了不该挡住整个自检
        add(WARN, "版本", "取不到版本信息：%s" % e)

    # ---------- 输出 ----------
    print()
    for r in _rows:
        print(f" {_ICON[r['level']]} {r['name']:16} {r['detail']}")
        if r["fix"] and r["level"] != OK:
            print(f"    {'':16} → {r['fix']}")

    bad = [r for r in _rows if r["level"] == BAD]
    warn = [r for r in _rows if r["level"] == WARN]
    print("=" * 70)
    if bad:
        print(f"结论：❌ 有 {len(bad)} 项阻塞问题，先按上面 → 的提示修：")
        for r in bad:
            print(f"   · {r['name']}：{r['fix'] or r['detail']}")
        return 2
    if warn:
        print(f"结论：⚠️ 可以跑主链路，但有 {len(warn)} 项能力不可用（见上）。")
        return 1
    print("结论：✅ 环境就绪。下一步：python scripts/ai_flow.py --spec flows/xxx.json --stage draft")
    return 0


if __name__ == "__main__":
    sys.exit(main())
