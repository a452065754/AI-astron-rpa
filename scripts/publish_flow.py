# -*- coding: utf-8 -*-
"""
星辰开源 · 路线 B 落库工具（publish_flow.py）
=============================================
把 flow_generator.py 生成的 process_content 写进 `.130`，让流程在设计器里可见、可执行。

为什么不需要用户手工贴 token（2026-10-30 实证）：
  - 设计器是 Electron 客户端（`D:\\Program Files\\astron-rpa`），`resources/conf.yaml` 里
    `remote_addr: http://192.168.76.130:32742/`，`app_auth_type: casdoor` —— 它就是个纯 UI，
    所有读写都打同一套服务端 HTTP 接口（客户端代码里就是 `POST /api/robot/process/save`）。
    所以「操作服务端」= 「操作客户端保存」，数据完全等价。
  - 客户端把每个服务端的会话 cookie 存在
    `%APPDATA%/astron-rpa/.cookie.json`（key = `<host>:<port>.SESSION`），
    该 cookie 对 `/api/rpa-auth/check-session` 校验通过、`/api/rpa-auth/user/info` 能取到当前用户。
  - 因此本工具直接复用**用户自己的本地会话**，不破解、不注入、不需要用户粘贴任何凭据。

真实接口契约（源码实证，勿凭猜）：
  - 建机器人：`POST /api/robot/robot-design/create`  body=`RobotDesign`（仅 `name` 必填）
  - 建流程：  `POST /api/robot/process/create`       body=`{robotId, processName, processContent?}`
  - 存流程：  `POST /api/robot/process/save`         body=`{robotId, processId, processJson, mode}`
              —— 注意字段是 **processJson（字符串）**，不是 process_content；流程必须已存在
  - 读流程：  `POST /api/robot/process/process-json` body=`{processId, robotId}`

用法：
  python publish_flow.py --flow docs/generated_flow.json --dry-run
  python publish_flow.py --flow docs/generated_flow.json --robot-name "AI生成流程_测试"
"""
import argparse
import json
import os
import re
import sys

import requests

import rpa_config as cfg

# 环境相关的东西一律从 rpa_config 取（迁移时只改 rpa_config.json / 环境变量）
TIMEOUT = 20


# ----------------------------- 会话 -----------------------------
def load_session(host=None):
    """从设计器客户端本地 cookie 文件取会话（用户自己的登录态）。"""
    host = host or cfg.host_port()
    cookie_file = cfg.cookie_file()
    if not os.path.exists(cookie_file):
        raise SystemExit(f"找不到设计器 cookie 文件：{cookie_file}\n请先打开设计器登录一次。")
    with open(cookie_file, "r", encoding="utf-8") as f:
        ck = json.load(f)
    rec = ck.get(host) or ck.get(f"{host}.SESSION")
    if not rec:
        hosts = [k for k in ck if not k.endswith(".SESSION")]
        raise SystemExit(f"cookie 里没有 {host} 的会话；已有：{hosts}\n请在设计器里连一次该服务端。")
    val = rec["SESSION"]["key_value"]
    return val if val.startswith("SESSION=") else f"SESSION={val}"


def api(path, body=None, cookie=None, method="POST"):
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Cookie": cookie,
    }
    url = cfg.gateway_base() + path
    if method == "GET":
        r = requests.get(url, headers=headers, timeout=TIMEOUT)
    else:
        r = requests.post(url, headers=headers,
                          data=json.dumps(body or {}, ensure_ascii=False).encode("utf-8"),
                          timeout=TIMEOUT)
    try:
        return r.json()
    except Exception:
        raise SystemExit(f"{path} 返回非 JSON（HTTP {r.status_code}）：{r.text[:300]}")


def ok(resp, what):
    code = str(resp.get("code"))
    if code not in ("0000", "000000", "0"):
        raise SystemExit(f"{what} 失败：code={code} message={resp.get('message') or resp.get('msg')}")
    return resp.get("data")


# ----------------------------- 业务动作 -----------------------------
def whoami(cookie):
    u = ok(api("/api/rpa-auth/user/info", cookie=cookie, method="GET"), "取当前用户")
    return (u or {}).get("loginName") or (u or {}).get("id")


def find_robot(cookie, name):
    """按名字找机器人。注意 design-list 返回的字段是 robotName（不是 name）。"""
    d = ok(api("/api/robot/robot-design/design-list", {"pageNo": 1, "pageSize": 200}, cookie), "列机器人")
    recs = d.get("records") if isinstance(d, dict) else (d or [])
    for x in recs or []:
        if (x.get("robotName") or x.get("name") or "") == name:
            return x.get("robotId")
    return None


def create_robot(cookie, name):
    d = ok(api("/api/robot/robot-design/create", {"name": name}, cookie), "建机器人")
    rid = d if isinstance(d, str) else (d or {}).get("robotId")
    if not rid:
        rid = find_robot(cookie, name)
    return rid


def list_processes(cookie, robot_id):
    """列出机器人下所有流程。注意 `/process/name` 返回的是「下一个建议名」，不是列表。"""
    d = ok(api("/api/robot/process/all-data", {"robotId": robot_id}, cookie), "列流程")
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        return d.get("records") or []
    return []


def create_process(cookie, robot_id, name):
    d = ok(api("/api/robot/process/create",
               {"robotId": robot_id, "processName": name, "processContent": ""}, cookie), "建流程")
    if isinstance(d, str):
        return d
    if isinstance(d, dict):
        return d.get("processId") or d.get("id")
    return None


def save_process(cookie, robot_id, process_id, process_json):
    body = {"robotId": robot_id, "processId": process_id,
            "processJson": process_json, "mode": "EDIT_PAGE"}
    return ok(api("/api/robot/process/save", body, cookie), "保存流程")


def delete_process(cookie, robot_id, process_id):
    return ok(api("/api/robot/process/delete",
                  {"robotId": robot_id, "processId": process_id}, cookie), "删流程")


def read_process(cookie, robot_id, process_id):
    """读回流程内容。实测该接口 data 直接是 processJson 字符串（不是对象）。"""
    d = ok(api("/api/robot/process/process-json",
               {"robotId": robot_id, "processId": process_id}, cookie), "读流程")
    if isinstance(d, str):
        return d
    if isinstance(d, dict):
        return d.get("processContent") or d.get("processJson")
    return None


def create_module(cookie, robot_id, content, module_name=""):
    """用**官方接口**新建一个 Python 子流程模块并写入内容，返回 moduleId。

    为什么不用 SQL 插（这是可迁移性的关键）：
    - 官方建的模块 `creator_id` = 当前登录用户 → **发布时平台自己会把它复制到发布版本**。
      2026-10-30 实测：建完发 v8，`c_module` 自动多出 `robot_version=8` 的行。
    - 手工 SQL 插入的行 `creator_id` 是旧格式 `'73'`，平台复制时按 creator_id 过滤 →
      找不到 → 才有「本地真跑成功、openapi 报『该模块不存在』」这个坑。

    结论：**新环境一律走这个函数，不需要碰数据库。**
    """
    d = ok(api("/api/robot/module/create",
               {"robotId": robot_id, "moduleName": module_name}, cookie), "创建模块")
    mid = (d or {}).get("moduleId")
    if not mid:
        raise SystemExit(f"创建模块没返回 moduleId：{d}")
    save_module(cookie, robot_id, mid, content)
    return str(mid)


def save_module(cookie, robot_id, module_id, content):
    """写入模块内容（新建后写、或改内容都走它）。"""
    return ok(api("/api/robot/module/save",
                  {"robotId": robot_id, "moduleId": module_id, "moduleContent": content,
                   "breakpoint": None, "mode": "EDIT_PAGE"}, cookie), "保存模块")


def open_module(cookie, robot_id, module_id, version=0):
    """读模块内容；不存在返回 None。"""
    try:
        d = ok(api("/api/robot/module/open",
                   {"robotId": robot_id, "moduleId": module_id,
                    "robotVersion": int(version), "mode": "EDIT_PAGE"}, cookie), "读模块")
    except SystemExit:
        return None
    return (d or {}).get("moduleContent")


def module_exists_in_version(cookie, robot_id, module_id, version):
    """模块在指定发布版本里是否存在 —— **纯 API 判定，不读库**（可迁移的关键）。"""
    return open_module(cookie, robot_id, module_id, version) is not None


def publish(cookie, robot_id, name, version, introduction="AI 生成流程", update_log="", online=1,
            module_ids=None, repair_modules=False):
    """发布机器人版本。

    `version` 必须是**比最新版本大的正整数**（首次 = 1），否则报「版本号错误」。
    `online` 在实体 `RobotVersion` 里是 **Integer（0/1）**，传布尔会 `500002 请求参数解析失败`。
    注意：发布机器人版本 **不会** 让它进 openapi 可执行列表 —— 那要用
    `upsert_openapi_workflow()`（前端走 `/api/rpa-openapi/workflows/upsert`）。

    `module_ids`：要校验是否被带进发布版本的 Python 子流程模块。
    **平台自己会复制**（2026-10-30 实测：用官方接口建的模块，发布 v8 后自动出现
    `robot_version=8` 的行）。所以这里默认只**校验**，不写库；只有 `repair_modules=True`
    才走 SQL 补丁（给历史上手工插入、creator_id 是旧格式的模块用）。
    """
    body = {
        "robotId": robot_id, "name": name, "version": int(version),
        "introduction": introduction, "updateLog": update_log or f"v{version}",
        "useDescription": "", "category": "", "online": int(online),
    }
    r = ok(api("/api/robot/robot-version/publish", body, cookie), "发布机器人")

    mids = [str(m) for m in (module_ids or []) if m]
    if not mids:
        return r
    missing = [m for m in mids if not module_exists_in_version(cookie, robot_id, m, version)]
    if not missing:
        print(f"      ✅ {len(mids)} 个 Python 子流程模块已在 v{version}（发布时平台自动复制）")
        return r

    print(f"      ⚠ v{version} 缺 {len(missing)} 个模块：{missing}")
    if repair_modules:
        try:
            n = clone_modules_for_version(robot_id, int(version))
            print(f"      · SQL 补丁已复制 {n} 个模块")
        except (Exception, SystemExit) as e:
            print(f"      ⚠ SQL 补丁失败：{e}")
    else:
        print("      · 若是手工 SQL 插入的模块（creator_id 不是当前登录用户），平台复制时会漏掉。")
        print("        正确做法：用 create_module() 重建（官方接口建的，发布会自动复制）。")
        print("        临时应急：加 --repair-modules 走 SQL 补丁（需要 db.enabled=true）。")
    return r


def next_version(cookie, robot_id):
    """取「下一个可用版本号」。

    实测 `/robot-version/latest-info` 返回的 `version` 已经是**下一个**（发布 v4 后它返回 5），
    所以直接用它；若发布时报「版本号错误」再 +1 重试（见 `publish_auto`）。
    """
    d = ok(api("/api/robot/robot-version/latest-info", {"robotId": robot_id}, cookie), "查版本")
    v = (d or {}).get("version")
    return int(v) if v else 1


def clone_modules_for_version(robot_id, version, from_version=0):
    """把设计态（robot_version=0）的 Python 子流程模块复制到指定发布版本。

    为什么必须做：`c_module` 是**按版本隔离**的。执行器取模块走
    `/api/robot/module/open`，参数带 `robotVersion=<发布版本>`；而手工插入的模块
    只有 `robot_version=0` 一行，于是「本地真跑（设计态）成功、openapi 调用
    （发布态）报『该模块不存在』」——坑 66。发布接口不会替我们复制。

    内容用 `HEX()`/`X'..'` 走，绕开 `mysql -e` 的二次转义（中文/反斜杠会被吃掉）。
    """
    from element_relocator import ServerDb

    db = ServerDb()
    try:
        rows = db.sql_rows(
            f"SELECT module_id, module_name, HEX(module_content), COALESCE(breakpoint,0) "
            f"FROM c_module WHERE robot_id='{robot_id}' AND robot_version={int(from_version)} "
            f"AND deleted=0")
        if not rows:
            return 0
        exist = {r[0] for r in db.sql_rows(
            f"SELECT module_id FROM c_module WHERE robot_id='{robot_id}' "
            f"AND robot_version={int(version)} AND deleted=0")}
        max_id = db.sql_rows("SELECT COALESCE(MAX(id),0) FROM c_module")[0][0]
        next_id = int(max_id)
        n = 0
        for module_id, module_name, hex_content, bp in rows:
            if module_id in exist:
                continue
            next_id += 1
            name_hex = (module_name or "").encode("utf-8").hex().upper()
            stmt = (
                "INSERT INTO c_module (id, module_id, module_content, module_name, deleted, "
                "create_time, update_time, robot_id, robot_version, breakpoint) VALUES ("
                f"{next_id}, {int(module_id)}, X'{hex_content}', "
                f"CONVERT(X'{name_hex}' USING utf8mb4), 0, NOW(), NOW(), "
                f"{int(robot_id)}, {int(version)}, {int(bp or 0)})")
            db.sql_exec(stmt)
            n += 1
        return n
    finally:
        db.close()


def publish_auto(cookie, robot_id, name, introduction="AI 生成流程", **kw):
    """自动取下一个版本号并发布，返回实际发布的版本号。"""
    v = next_version(cookie, robot_id)
    for _ in range(3):
        try:
            publish(cookie, robot_id, name, v, introduction=introduction, **kw)
            return v
        except SystemExit as e:
            if "版本号错误" not in str(e):
                raise
            v += 1
    raise SystemExit("发布失败：连续三次版本号冲突")


def list_params(cookie, robot_id, process_id):
    """列流程参数（c_param）。"""
    return ok(api("/api/robot/param/all",
                  {"robotId": robot_id, "processId": process_id, "mode": "EDIT_PAGE"}, cookie),
              "查流程参数") or []


def sync_params(cookie, robot_id, process_id, params):
    """把规格里的 `params` 同步成服务端流程参数（`c_param`）。

    - 已存在（按 varName 匹配）→ 默认值/描述有变化才 update
    - 不存在 → add
    - 服务端多出来的参数**不删**（可能是用户手工加的），只在返回值里列出

    `params`: [{"name","type","default","describe"}]
    节点里用 `$p:name` 引用 → `{"type":"p_var","value":"name"}`；
    引擎编译成 `name = args.get("name", 默认值)`，openapi 的 `params` 可覆盖默认值。
    """
    existing = list_params(cookie, robot_id, process_id)
    by_name = {p.get("varName"): p for p in existing if isinstance(p, dict)}
    added, updated = [], []
    for p in params or []:
        name = p.get("name")
        body = {
            "varDirection": 0, "varName": name, "varType": p.get("type") or "Str",
            "varValue": str(p.get("default") if p.get("default") is not None else ""),
            "varDescribe": p.get("describe") or "", "robotId": robot_id, "processId": process_id,
        }
        old = by_name.get(name)
        if old is None:
            ok(api("/api/robot/param/add", body, cookie), f"新增参数 {name}")
            added.append(name)
        else:
            changed = (str(old.get("varValue") or "") != body["varValue"]
                       or str(old.get("varDescribe") or "") != body["varDescribe"]
                       or str(old.get("varType") or "") != body["varType"])
            if changed:
                body["id"] = old.get("id")
                ok(api("/api/robot/param/update", body, cookie), f"更新参数 {name}")
                updated.append(name)
    extra = [n for n in by_name if n not in {p.get("name") for p in (params or [])}]
    return {"added": added, "updated": updated, "extra_on_server": extra,
            "total": len(by_name) + len(added)}


def get_last_execute_error(robot_id, execute_id=None):
    """取最近一次执行的**具体失败原因**。

    openapi 的 `result.msg` 只会给「执行错误」（code 5001），真正的原因
    （如「填写文件路径有误，请输入正确的路径！」+ traceback）在
    `robot_execute_record.execute_log` 里，且该字段可能被截断成非法 JSON，
    所以用正则提取 `msg_str` 而不是 json.loads。
    """
    if not cfg.db_conf():
        return {"error": "SQL 通道未启用（rpa_config.json 里 db.enabled=false），读不到 execute_log",
                "hints": [],
                "tip": "失败原因可先用 `python scripts/error_hints.py --log all` 从执行器日志翻译；"
                       "确实需要读库再开 db.enabled"}
    try:
        from element_relocator import ServerDb
    except (Exception, SystemExit) as e:  # 没有 SSH 依赖时降级
        return {"error": f"无法加载 ServerDb：{e}", "hints": []}
    db = ServerDb()
    where = f"execute_id='{execute_id}'" if execute_id else f"robot_id='{robot_id}'"
    rows = db.sql_rows(
        f"SELECT execute_id, result, error_reason, execute_log FROM robot_execute_record "
        f"WHERE {where} ORDER BY id DESC LIMIT 1")
    if not rows:
        return {"error": "没有执行记录"}
    eid, result, reason, log = rows[0]
    msgs, seen = [], set()
    noise = {"执行错误", "开始初始化...", "初始化完成", "开始执行", "运行成功"}
    for raw in re.findall(r'"msg_str"\s*:\s*"((?:[^"\\]|\\.)*)"', log or ""):
        # 日志本身是「JSON 里套 JSON」，中文常被双重转义（\\u5f00 → \u5f00 → 开），
        # 所以要连续解两次；解不动就保留原文。
        m = raw
        for _ in range(3):
            try:
                m2 = json.loads('"' + m + '"')
            except Exception:
                break
            if m2 == m:
                break
            m = m2
        if m and m not in seen and m not in noise:
            seen.add(m)
            msgs.append(m)
    out = {"execute_id": eid, "result": result, "error_reason": reason,
           "detail": msgs[-5:]}
    # 顺手翻译：openapi 的「执行错误」外壳 → 真因 + 怎么修
    try:
        import error_hints as eh
        out["hints"] = eh.translate("\n".join([str(reason or "")] + msgs))
    except Exception:
        out["hints"] = []
    return out


def upsert_openapi_workflow(cookie, project_id, name, version, description="",
                            english_name=None, status=1, parameters="[]",
                            example_project_id=None):
    """把机器人登记进 openapi 可执行列表（`openai_workflows`），`/workflows/get` 才看得到。

    入参来自 openapi-service 的 `WorkflowBase`（`app/schemas/workflow.py`）：
      project_id / name / english_name / description / version / status / parameters / example_project_id
    - `parameters` 是 **JSON 字符串**（如 `"[]"`），不是数组。
    - `status=1` 才是启用。
    - 幂等：project_id 已存在则更新（仅限同一用户，否则报 belongs to another user）。
    """
    body = {
        "project_id": str(project_id), "name": name, "version": int(version),
        "status": int(status), "parameters": parameters,
        "description": description, "english_name": english_name or "",
    }
    if example_project_id:
        body["example_project_id"] = str(example_project_id)
    d = ok(api("/api/rpa-openapi/workflows/upsert", body, cookie), "登记 openapi 工作流")
    w = (d or {}).get("workflow") or {}
    return {"action": (d or {}).get("action"), "version": w.get("version"), "status": w.get("status")}


# ----------------------------- 入口 -----------------------------
def main():
    ap = argparse.ArgumentParser(description="路线 B 落库：把生成的流程写进 .130")
    ap.add_argument("--flow", default=os.path.join("docs", "generated_flow.json"),
                    help="flow_generator.py 产出的 JSON")
    ap.add_argument("--robot-id", help="写入已有机器人（不给则按 --robot-name 新建/复用）")
    ap.add_argument("--robot-name", default="AI生成流程_测试", help="新建/复用的机器人名")
    ap.add_argument("--process-name", help="流程名（默认取 flow 文件里的 name）")
    ap.add_argument("--main", action="store_true",
                    help="写进「主流程」——设计器点「编辑」默认打开的就是它，不写这里会看到空白")
    ap.add_argument("--dry-run", action="store_true", help="只打印将写入的内容，不调写接口")
    args = ap.parse_args()

    with open(args.flow, "r", encoding="utf-8") as f:
        flow = json.load(f)
    nodes = flow["process_content"]
    pname = "主流程" if args.main else (args.process_name or flow.get("name") or "AI生成流程")
    process_json = json.dumps(nodes, ensure_ascii=False)

    print(f"[1/5] 读取流程：{pname}（{len(nodes)} 节点，{len(process_json)} 字节）")
    for n in nodes:
        print(f"      - {n['key']:32s} {n.get('alias','')}")

    cookie = load_session()
    user = whoami(cookie)
    print(f"[2/5] 会话有效，当前用户：{user}")

    if args.dry_run:
        print("[dry-run] 将调用的写接口：")
        print(f"      POST /api/robot/robot-design/create  {{name: {args.robot_name!r}}}")
        print(f"      POST /api/robot/process/create      {{robotId, processName: {pname!r}}}")
        print(f"      POST /api/robot/process/save        {{robotId, processId, processJson: <{len(process_json)} 字节>}}")
        print("[dry-run] 未写任何数据。")
        return 0

    rid = args.robot_id
    if rid:
        print(f"[3/5] 使用已有机器人：{rid}")
    else:
        rid = find_robot(cookie, args.robot_name)
        if rid:
            print(f"[3/5] 复用同名机器人：{args.robot_name}（{rid}）")
        else:
            rid = create_robot(cookie, args.robot_name)
            print(f"[3/5] 已新建机器人：{args.robot_name}（{rid}）")

    existing = list_processes(cookie, rid)
    same = [p for p in existing
            if isinstance(p, dict) and (p.get("processName") or p.get("name")) == pname]
    if same:
        pid = same[0].get("processId") or same[0].get("id")
        print(f"[4/5] 流程已存在，覆盖保存：{pname}（{pid}）")
    else:
        pid = create_process(cookie, rid, pname)
        print(f"[4/5] 已新建流程：{pname}（{pid}）")

    save_process(cookie, rid, pid, process_json)
    print("[5/5] 已保存，回读校验...")
    content = read_process(cookie, rid, pid)
    same_len = isinstance(content, str) and len(content) == len(process_json)
    print(f"      回读长度：{len(content) if isinstance(content, str) else 'N/A'}（写入 {len(process_json)}）一致={same_len}")
    if isinstance(content, str):
        try:
            got = json.loads(content)
            print(f"      回读节点数：{len(got)} / {len(nodes)}  节点 key 一致="
                  f"{[g.get('key') for g in got] == [n.get('key') for n in nodes]}")
        except Exception as e:
            print(f"      回读内容无法解析：{e}")

    print(f"\n✅ 完成：robotId={rid}  processId={pid}")
    print(f"   在设计器里打开机器人「{args.robot_name}」即可看到流程「{pname}」。")
    print(f"   如需清理：POST /api/robot/process/delete {{robotId, processId}}；"
          f"整个机器人：POST /api/robot/robot-design/delete-robot")
    return 0


if __name__ == "__main__":
    sys.exit(main())
