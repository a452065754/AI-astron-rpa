#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Script.component（运行组件）端到端：建组件 → 填子流程 → 发版 → 挂载 → 主流程调用 → 真跑取证。

## 组件机制（服务端 Java + 引擎 Python 源码双向实证，2026-10-31）

组件在 Astron RPA 里**本质是「robotId = componentId 的子流程」**：

1. `GET /api/robot/component/create?componentName=X`
   → `ComponentServiceImpl.createComponent`：生成 `componentId`(雪花id)，
     插 `component` 记录，并**以 componentId 作为 robotId 建一条 CProcess**（"主流程"，processContent="[]"）。
     返回 `{robotId=componentId, processId=xxx}`（注意：返回体里字段名是 robotId，值就是 componentId）。

2. 填组件内容：`POST /api/robot/process/save`（robotId=componentId, processId, processJson=节点树）。
   `saveProcessContent` 不区分「机器人/组件」，且内部还会 `componentDao.updateTransformStatus(...)`，
   明确支持组件流程的保存。

3. 发版：`GET /api/robot/component-version/next-version?componentId=` → `POST /api/robot/component-version/create`
   （组件必须有版本，挂载时 `addComponentUse` 用 `componentVersionDao.getLatestVersion`）。

4. 挂载到主机器人：`POST /api/robot/component-robot-use/add`（mode=EDIT_PAGE, robotId=主机器人, componentId）。
   前端 `robot.ts:178` 同样传 `mode: 'EDIT_PAGE'`。

5. 主流程调用组件：节点 key 写 **`Code.Component.<componentId>`**（纯数字 id，不带 c 前缀）。
   引擎 `storage.py:232` 兼容层把它转成 `Script.component` 并注入 `component=<id>`；
   `params.py:149` 标 `special="component"`；`syntax/__init__.py` `InputParam.show_value()`
   生成 `Script.component(component=component(<id>))`；`package.tpl` 的 `component(id)` 从
   component_info 查 `component_file_name="c<id>.main.py"` → splitext 得 `c<id>.main`；
   `Script.component` 再 `import_module(".main", package="c<id>")` 执行组件编译产物。

## 用法

    "%APPDATA%/astron-rpa/python_core/python.exe" scripts/component_e2e.py
    "%APPDATA%/astron-rpa/python_core/python.exe" scripts/component_e2e.py --keep      # 跑完不删组件
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable
OUT_DIR = os.path.join(ROOT, "demo", "_component_out")

sys.path.insert(0, HERE)
import publish_flow as PF  # noqa: E402

DEFAULT_ROBOT_ID = "2115990391806255104"   # 主机器人（目标 robot）
HOST = "192.168.76.130:32742"


def log(msg):
    print(msg, flush=True)


# --------------------------------------------------------------------------- #
# 1. 组件生命周期（建 → 填 → 发版 → 挂载）
# --------------------------------------------------------------------------- #
def create_component(cookie, name):
    d = PF.ok(PF.api(f"/api/robot/component/create?componentName={name}",
                     cookie=cookie, method="GET"), "建组件")
    cid = str(d.get("robotId") or d.get("componentId"))
    pid = str(d.get("processId"))
    if not cid or not pid:
        raise SystemExit(f"组件 create 返回缺 id：{d}")
    log(f"· 组件已建：componentId={cid} processId={pid}")
    return cid, pid


def save_component_content(cookie, cid, pid, nodes):
    body = {"robotId": cid, "processId": pid,
            "processJson": json.dumps(nodes, ensure_ascii=False), "mode": "EDIT_PAGE"}
    PF.ok(PF.api("/api/robot/process/save", body, cookie), "填组件内容")
    log(f"· 组件内容已填（{len(nodes)} 个节点）")


def publish_component(cookie, cid, name):
    nv = PF.ok(PF.api(f"/api/robot/component-version/next-version?componentId={cid}",
                      cookie=cookie, method="GET"), "取下一版本")
    nv = int(nv or 1)
    body = {"componentId": cid, "nextVersion": nv, "updateLog": "AI 生成组件",
            "name": name, "icon": "", "introduction": "E2E 测试组件"}
    PF.ok(PF.api("/api/robot/component-version/create", body, cookie), "组件发版")
    log(f"· 组件已发版 version={nv}")
    return nv


def attach_component(cookie, robot_id, cid):
    body = {"mode": "EDIT_PAGE", "robotId": str(robot_id), "componentId": cid}
    PF.ok(PF.api("/api/robot/component-robot-use/add", body, cookie), "挂载组件")
    log(f"· 组件已挂到机器人 {robot_id}")


# --------------------------------------------------------------------------- #
# 2. 节点树构造
# --------------------------------------------------------------------------- #
def _mk(kind, v):
    """单值参数：标准格式 [{"type":kind, "value":v}]（引擎 params.py 要求的 list of dict）。"""
    return [{"type": kind, "value": v}]


def _node(atom_key, alias, inputs, out=None, version="1.0.1"):
    ts = int(time.time() * 1000)
    node = {
        "key": atom_key,
        "version": version,
        "id": f"bh{ts}{len(str(time.time_ns()))}",
        "alias": alias,
        "inputList": [{"key": k, "value": v} for k, v in inputs.items()],
        "outputList": [{"key": k, "value": {"type": "var", "value": v}} for k, v in (out or {}).items()],
        "advanced": [
            {"key": "__delay_before__", "value": _mk("other", 0)},
            {"key": "__delay_after__", "value": _mk("other", 0)},
        ],
        "exception": [
            {"key": "__skip_err__", "value": _mk("other", "exit")},
            {"key": "__retry_time__", "value": _mk("other", 0)},
            {"key": "__retry_interval__", "value": _mk("other", 0)},
        ],
    }
    return node


def component_nodes(out_dir):
    """组件内容：一个写文件的子流程。副作用 = 组件执行证据文件。

    注意：file_path / msg 必须用「单片段」字面量（一个 other 值），不能用多片段拼接。
    原因（引擎 bug，坑 166）：`_param_to_eval` 在「多片段 + 全字面量(need_eval=False)」时
    走 `return "".join(pieces), need_eval, need_eval` 返回 3 个值，但调用处
    `value, need_eval = ...` 只解包 2 个 → ValueError。多片段 + 含变量(need_eval=True)
    才走 `return "+".join(...), need_eval`（2 个值），所以含变量引用反而正常。
    """
    import os as _os
    full_path = _os.path.join(out_dir, "组件已执行.txt")
    return [
        _node("File.file_write", "组件内部写文件（组件被调用后应出现此文件）", {
            "file_path": _mk("other", full_path),
            "file_option": _mk("other", "create"),
            "write_type": _mk("other", "overwrite"),
            "msg": _mk("other", "【组件已执行】component 被主流程成功调用。\n组件自身写下的证据文件。\n"),
        }),
    ]


def main_nodes(robot_id, cid, out_dir):
    """主流程：开始标记 → 调组件(Code.Component.<cid>) → 取证。"""
    import os as _os
    comp_node = _node(f"Code.Component.{cid}", f"调用组件 {cid}", {})
    # 组件节点不需要 inputs/outputs：storage.py:232 兼容层会自动注入 component=<cid> 参数
    return [
        _node("File.file_write", "开始标记", {
            "file_path": _mk("other", _os.path.join(out_dir, "组件调用_0_开始.txt")),
            "file_option": _mk("other", "create"),
            "write_type": _mk("other", "overwrite"),
            "msg": _mk("other", f"主流程开始，即将调用组件 componentId = {cid}\n"),
        }),
        comp_node,
        _node("File.file_write", "取证：组件调用后主流程继续执行", {
            "file_path": _mk("other", _os.path.join(out_dir, "组件调用取证.txt")),
            "file_option": _mk("other", "create"),
            "write_type": _mk("other", "overwrite"),
            "msg": _mk("other",
                      "主流程已从组件调用返回，继续执行到取证节点。\n"
                      "若「组件已执行.txt」存在，说明 Script.component 真的把组件跑起来了。\n"),
        }),
    ]


# --------------------------------------------------------------------------- #
# 3. 发布主流程 + 真跑
# --------------------------------------------------------------------------- #
def save_main_flow(cookie, robot_id, name, nodes):
    # 先清掉同名旧流程（多次运行会堆积，引擎 gen_code 会遍历所有子流程逐个编译）
    try:
        procs = PF.ok(PF.api("/api/robot/process/all-data", {"robotId": str(robot_id)}, cookie), "列流程")
        for p in (procs if isinstance(procs, list) else []):
            if isinstance(p, dict) and (p.get("processName") == name or p.get("name") == name):
                pid_old = p.get("processId") or p.get("id")
                if pid_old:
                    PF.ok(PF.api("/api/robot/process/delete",
                                 {"robotId": str(robot_id), "processId": str(pid_old)}, cookie), "删旧流程")
    except SystemExit:
        pass
    pid = PF.create_process(cookie, robot_id, name)
    if isinstance(pid, dict):
        pid = pid.get("processId") or pid.get("id")
    PF.save_process(cookie, robot_id, pid, json.dumps(nodes, ensure_ascii=False))
    log(f"· 主流程已发布：processId={pid}")
    return pid


def run_flow(robot_id, process_id, timeout=180):
    cmd = [PY, os.path.join(HERE, "run_flow_local.py"),
           "--robot-id", str(robot_id), "--process-id", str(process_id),
           "--timeout", str(timeout), "--show-log"]
    log("· 本地真跑主流程…")
    t0 = time.time()
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout + 180)
    return r, time.time() - t0


# --------------------------------------------------------------------------- #
# 4. 主入口
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--robot-id", default=DEFAULT_ROBOT_ID)
    ap.add_argument("--component-name", default="星辰测试组件E2E")
    ap.add_argument("--keep", action="store_true", help="跑完不删组件")
    ap.add_argument("--timeout", type=int, default=180)
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    for f in os.listdir(OUT_DIR):
        try:
            os.remove(os.path.join(OUT_DIR, f))
        except OSError:
            pass

    cookie = PF.load_session(HOST)
    who = PF.whoami(cookie)
    log(f"会话用户：{who}")

    cid = pid = main_pid = None
    try:
        # 1. 建组件
        cid, pid = create_component(cookie, args.component_name)

        # 2. 填组件内容（写文件子流程）
        save_component_content(cookie, cid, pid, component_nodes(OUT_DIR))

        # 3. 发版
        publish_component(cookie, cid, args.component_name)

        # 4. 挂载到主机器人
        attach_component(cookie, args.robot_id, cid)

        # 5. 发布主流程（含 Code.Component.<cid> 节点）
        # save_main_flow 内部会先清同名旧流程，避免多次运行堆积残留
        #（引擎 gen_code 会遍历主机器人的**所有**子流程逐个编译，残留会连累后续运行）
        flow_name = "组件调用小流程"
        main_pid = save_main_flow(cookie, args.robot_id, flow_name,
                                  main_nodes(args.robot_id, cid, OUT_DIR))

        # 6. 真跑
        r, elapsed = run_flow(args.robot_id, main_pid, args.timeout)

        log("")
        log("=" * 78)
        log(f"执行耗时 {elapsed:.1f}s")
        tail = (r.stdout or "").strip().splitlines()[-12:]
        for line in tail:
            log("  " + line)

        # 7. 取证
        checks = []

        def check(name, ok, detail=""):
            checks.append((name, ok, detail))
            log(f"  {'✓' if ok else '✗'} {name}{('：' + detail) if detail else ''}")

        def read(name):
            p = os.path.join(OUT_DIR, name)
            return open(p, encoding="utf-8").read() if os.path.exists(p) else None

        check("主流程跑起来了（开始标记存在）", read("组件调用_0_开始.txt") is not None)
        comp_ev = read("组件已执行.txt")
        check("组件被调用（组件自身写的文件存在）", comp_ev is not None)
        check("组件文件内容正确", bool(comp_ev) and "组件已执行" in comp_ev)
        check("主流程从组件调用返回后继续执行", read("组件调用取证.txt") is not None)
        check("流程整体 SUCCESS", "SUCCESS" in (r.stdout or "") or r.returncode == 0,
              f"rc={r.returncode}")

        ok_all = all(c[1] for c in checks)
        log("=" * 78)
        log(f"结论：{sum(1 for c in checks if c[1])}/{len(checks)} 项通过"
            f" → {'Script.component 已真跑通过' if ok_all else '未全部通过'}")
        return 0 if ok_all else 1

    finally:
        if cid and not args.keep:
            # 先删挂载关系（component-robot-use/delete），再删组件本身。
            # 只删组件（component/delete 是软删 is_shown=0）会残留挂载，
            # 引擎 gen_component 会遍历所有挂载组件逐个编译 → 残留组件会连累后续运行。
            try:
                PF.ok(PF.api("/api/robot/component-robot-use/delete",
                             {"mode": "EDIT_PAGE", "robotId": str(args.robot_id),
                              "componentId": cid}, cookie), "删挂载")
            except SystemExit as e:
                log(f"（删挂载失败：{e}）")
            try:
                PF.ok(PF.api(f"/api/robot/component/delete?componentId={cid}",
                             cookie=cookie, method="GET"), "清理组件")
                log(f"· 已清理测试组件 {cid}")
            except SystemExit as e:
                log(f"（清理组件失败，可手动删：{e}）")


if __name__ == "__main__":
    sys.exit(main())
