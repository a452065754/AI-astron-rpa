#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V17 门禁：**默认草稿，发布必须显式确认**（`publish_gate`）。

## 为什么有这个门禁

2026-11-05 A20 事故复盘的最后一块拼图，由用户指出：

    「只有发布后才会进入『添加应用』的列表，你后台发布了。」

生产库里证据是硬的 —— `robot_execute.create_time` 与 `robot_version.create_time`
**逐秒一致**（Py测试 03:20:03、MES 02:18:56），说明**发布这一步会自动生成执行态记录**，
机器人就此进入列表；列表接口把 `paramJson` 整段塞进响应、过日志脱敏正则，
参数一多就爆栈。而草稿（只有 `robot_design` + `c_process` + `c_param`）永远不进列表 ——
库里 3 份 MES 草稿各 25 条参数长期无事，就是反证。

⇒ 事故链上 AI 唯一「自主决定」的环节就是发布。这条规则必须由代码强制，不能靠记性：

  A. 默认阶段 = draft（只生成+校验，零写操作）
  B. `--stage publish` / `--stage expose` 不带 `--confirm-publish` → 退出码 2，且不联网
  C. 库层 `publish_flow.publish()` 默认 `confirm=False` → 其他脚本也绕不过去
  D. `RPA_ALLOW_PUBLISH=1` 是唯一旁路（无人值守流水线用），仍算显式确认
  E. 规格里写 `"publish": true` **不产生授权**，只提示
  F. draft/review/invoke 不受影响（门禁不能误伤）

跑法：`python logs/qa/v17_draft_default.py`   退出码 0 = 通过

⚠️ 本脚本不 import `ai_flow`/`publish_flow`（它们依赖 `requests`，系统 python 可能没有），
   改用 AST 静态检查 + 借客户端 python 跑真实 CLI。客户端 python 找不到时才降级为
   「只用静态检查」，并在结果里如实标注。
"""
import ast
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

SPEC = os.path.join(ROOT, "flows", "MES包装标签生成.json")
AI_FLOW = os.path.join(ROOT, "scripts", "ai_flow.py")
PUB_FLOW = os.path.join(ROOT, "scripts", "publish_flow.py")


def client_python():
    """优先用 rpa_config 指定的客户端 python（装了 requests）。"""
    try:
        cfg = json.load(open(os.path.join(ROOT, "rpa_config.json"), encoding="utf-8"))
        p = ((cfg.get("client") or {}).get("python") or "").strip()
        if p and os.path.isfile(p):
            return p
    except Exception:
        pass
    return ""


def func_def(path, name):
    """AST 取顶层函数定义（不执行模块）。"""
    tree = ast.parse(open(path, encoding="utf-8").read())
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    return None


def main():
    import publish_gate as pg

    results = []
    py = client_python()

    # A. 默认阶段必须是 draft（不是 review —— review 会写设计态）
    src = open(AI_FLOW, encoding="utf-8").read()
    default_draft = ('default=pg.DEFAULT_STAGE' in src and pg.DEFAULT_STAGE == "draft")
    results.append(("A 默认阶段=draft", default_draft,
                    "publish_gate.DEFAULT_STAGE=%r · argparse 引用=%s"
                    % (pg.DEFAULT_STAGE, "default=pg.DEFAULT_STAGE" in src)))

    # B. CLI 拦截：publish 不带开关必须退出码 2，且给出解释
    if py:
        e = dict(os.environ)
        e.pop("RPA_ALLOW_PUBLISH", None)
        p = subprocess.run([py, "scripts/ai_flow.py", "--spec", SPEC, "--stage", "publish",
                            "--version", "99", "--offline"], cwd=ROOT, env=e,
                           capture_output=True, text=True, timeout=180)
        out = (p.stdout or "") + (p.stderr or "")
        blocked = p.returncode == pg.EXIT_CODE and "确认" in out and pg.CONFIRM_FLAG in out
        results.append(("B CLI 拦 publish", blocked,
                        "rc=%d（期望 %d）" % (p.returncode, pg.EXIT_CODE)))
        p2 = subprocess.run([py, "scripts/ai_flow.py", "--spec", SPEC, "--stage", "expose",
                             "--offline"], cwd=ROOT, env=e, capture_output=True, text=True,
                            timeout=180)
        results.append(("B2 CLI 拦 expose", p2.returncode == pg.EXIT_CODE,
                        "rc=%d" % p2.returncode))
    else:
        results.append(("B CLI 拦 publish", False,
                        "找不到可用的客户端 python（rpa_config.client.python），无法跑真 CLI"))

    # C. 库层默认拒绝：publish() 签名里 confirm 必须是 False（静态查，避免 import requests）
    fn = func_def(PUB_FLOW, "publish")
    c_ok = False
    detail = "没找到 publish()"
    if fn is not None:
        args = [a.arg for a in fn.args.args]
        defaults = fn.args.defaults
        pos = {n: d for n, d in zip(args[len(args) - len(defaults):], defaults)}
        node = pos.get("confirm")
        c_ok = (node is not None and isinstance(node, ast.Constant) and node.value is False)
        body = ast.dump(fn)
        c_ok = c_ok and "require" in body and "publish_gate" in body
        detail = "confirm 默认=%s · 函数内调用闸门=%s" % (
            getattr(node, "value", "缺失"), "require" in body)
    results.append(("C 库层默认拒绝", c_ok, detail))

    # D. 显式确认 / 环境变量放行（不放行等于 publish 永久不可用）
    gate_ok = pg.require("publish", True) is None
    old = os.environ.get("RPA_ALLOW_PUBLISH")
    os.environ[pg.ENV_ALLOW] = "1"
    try:
        env_ok = pg.require("expose", False) is None
    finally:
        if old is None:
            os.environ.pop(pg.ENV_ALLOW, None)
        else:
            os.environ[pg.ENV_ALLOW] = old
    results.append(("D 确认后放行", gate_ok and env_ok,
                    "confirm flag=%s · env=%s" % (gate_ok, env_ok)))

    # E. 规格里的 publish 字段不产生授权
    note = pg.check_spec_flags({"publish": True})
    results.append(("E 规格字段不授权", ("不产生授权" in note), (note or "无提示")[:56]))

    # F. 其它阶段不被误伤
    unaffected = all(pg.require(s, False) is None for s in ("draft", "review", "invoke"))
    results.append(("F 不误伤其它阶段", unaffected, "draft/review/invoke 均放行"))

    ok = sum(1 for _, o, _ in results if o)
    print("=" * 78)
    for n, o, d in results:
        print(" %s %-18s %s" % ("✅" if o else "❌", n, d))
    print("=" * 78)
    print("结论：%d/%d 通过%s" % (ok, len(results),
                                "" if py else "（未跑真 CLI：无客户端 python）"))
    print("=" * 78)
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
