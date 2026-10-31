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
| SQL 通道（可选） | ℹ️ | 只影响 execute_log 读取与模块 SQL 补丁 |

## 用法

```
python scripts/doctor.py                    # 全量自检
python scripts/doctor.py --robot-id 123     # 顺便校验某个机器人
```
"""
import argparse
import importlib
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
