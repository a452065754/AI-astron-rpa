#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
星辰开源 · P1 PoC —— AI → RPA OpenAPI 调度（路线 A）
=====================================================

自然语言 (NL) → DeepSeek 解析意图 → 匹配已发布流程 → 填参
→ POST /workflows/execute-async → 轮询 GET /executions/{id}

设计要点（遵循 docs/交接清单.md / 开发文档-AI操作RPA-OpenAPI集成.md）：
  - 对外网关只用 32742，列表端点是 /workflows/get（不是 /workflows）。
  - API Key 仅能「读 + 执行」，不能 upsert/管 Key（写流程属路线 B，需会话）。
  - 鉴权二选一：X-API-Key 或 Authorization: Bearer。本脚本用 X-API-Key。
  - DeepSeek 缺失时走「本地规则解析」兜底，PoC 可离线跑通主链路。

运行：
  python scripts/dispatch_poc.py "执行 沙发上等着，商店=测试店，商品=测试商品"
  python scripts/dispatch_poc.py --interactive
  python scripts/dispatch_poc.py --nl "帮我跑沙发流程，商店名 小明超市，商品名 可乐" --no-llm

环境变量（均可选，缺省为本地联调默认值，勿外发到公开仓库）：
  RPA_BASE         OpenAPI 网关基址
  RPA_API_KEY      OpenAPI Key（默认用交接文档记录的本地联调 Key）
  DEEPSEEK_API_KEY DeepSeek Key；缺省则自动降级到本地规则解析
  DEEPSEEK_BASE    DeepSeek OpenAI 兼容基址
  DEEPSEEK_MODEL   模型名
"""

import argparse
import json
import os
import re
import sys
import time

try:
    import requests
except ImportError:
    sys.stderr.write("缺少依赖 requests，请先: pip install requests\n")
    sys.exit(2)


# ----------------------------- 配置 -----------------------------
BASE = os.getenv("RPA_BASE", "http://192.168.76.130:32742/api/rpa-openapi")
# Key 只从环境变量或 rpa_config.json 取，不在代码里写死
def _api_key():
    k = os.getenv("RPA_API_KEY")
    if k:
        return k
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import rpa_config as _cfg
        return _cfg.api_key()
    except Exception:
        return ""

API_KEY = _api_key()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE = os.getenv("DEEPSEEK_BASE", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

HEADERS = {"X-API-Key": API_KEY, "Content-Type": "application/json"}
TIMEOUT = 30


# ------------------------- RPA OpenAPI 客户端 -------------------------
class RpaClient:
    """封装 Astron RPA OpenAPI 的只读 + 执行能力（路线 A）。"""

    def list_workflows(self):
        """GET /workflows/get —— 返回 records 列表（含 name/project_id/parameters）。"""
        r = requests.get(f"{BASE}/workflows/get", headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        payload = r.json()
        if payload.get("code") not in ("0000", None, ""):
            raise RuntimeError(f"list_workflows 返回非预期: {payload}")
        return (payload.get("data") or {}).get("records") or []

    def get_workflow(self, project_id):
        """GET /workflows/get/{project_id} —— 含 metadata + 入参定义。"""
        r = requests.get(f"{BASE}/workflows/get/{project_id}", headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    def execute_async(self, project_id, parameters):
        """
        触发异步执行。
        依据 openapi-service 源码（app/schemas/workflow.py::ExecutionCreate）：
          - 路由 /workflows/execute-async（pid 放 body，不进路径）
          - 执行参数字段名为 params（dict），不是 parameters
          - 模型 extra="forbid"，body 只能含声明字段
        兼容写法：优先路径不带 pid 的官方路由，回退到 /{pid}/execute-async。
        """
        body = {"project_id": str(project_id), "params": parameters or {}}
        candidates = [
            f"{BASE}/workflows/execute-async",
            f"{BASE}/workflows/{project_id}/execute-async",
        ]
        last_exc = None
        for url in candidates:
            try:
                r = requests.post(url, headers=HEADERS, json=body, timeout=TIMEOUT)
                if r.status_code in (200, 201, 202):
                    return self._extract_exec_id(r.json())
                last_exc = f"{url} -> {r.status_code} {r.text[:300]}"
            except Exception as e:  # noqa
                last_exc = f"{url} -> {e}"
        raise RuntimeError(f"execute_async 全部失败: {last_exc}")

    @staticmethod
    def _extract_exec_id(payload):
        """从响应里尽力抠出 execution id。"""
        if isinstance(payload, dict):
            for k in ("execution_id", "executionId", "id", "exec_id", "task_id"):
                if k in payload and payload[k]:
                    return str(payload[k])
        raise RuntimeError(f"无法从响应解析 execution id: {payload}")

    def get_execution(self, exec_id):
        """GET /executions/{id} —— 轮询状态/结果。"""
        r = requests.get(f"{BASE}/executions/{exec_id}", headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    def poll_execution(self, exec_id, interval=3, max_tries=60):
        """轮询直到终态；返回最终状态 dict。
        状态枚举（ExecutionStatus）：PENDING/RUNNING/COMPLETED/FAILED/CANCELLED/UNKNOWN/TIMEOUT
        """
        terminal = {"completed", "failed", "cancelled", "timeout"}
        for i in range(max_tries):
            st = self.get_execution(exec_id)
            status = self._status_of(st)
            print(f"  [轮询 {i + 1}] 状态={status}  raw={self._short(st)}")
            if status in terminal:
                return st
            time.sleep(interval)
        raise TimeoutError(f"执行未在 {max_tries * interval}s 内结束: {exec_id}")

    @staticmethod
    def _status_of(payload):
        """多字段尝试拿到状态字符串。"""
        if isinstance(payload, dict):
            for k in ("status", "state", "execution_status", "result"):
                v = payload.get(k)
                if isinstance(v, str):
                    return v.lower()
                if isinstance(v, dict):
                    return str(v.get("status", v)).lower()
        return "unknown"

    @staticmethod
    def _short(payload, n=200):
        s = json.dumps(payload, ensure_ascii=False)
        return s if len(s) <= n else s[:n] + "..."


# --------------------------- 意图解析层 ---------------------------
def parse_intent_with_deepseek(nl: str, workflow_names: list) -> dict:
    """
    调用 DeepSeek（OpenAI 兼容）把自然语言解析成结构化意图：
      { "workflow_hint": <流程名线索>, "parameters": {store_name, goods_name}, "confidence": 0~1 }
    返回 dict。
    """
    from openai import OpenAI  # 懒加载，缺包时不影响本地兜底

    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE)
    name_list = "、".join(workflow_names) or "（无已知流程）"
    system = (
        "你是 RPA 调度助手。用户用中文自然语言下达执行 RPA 流程的指令。"
        "请输出严格 JSON："
        '{"workflow_hint": "用户想执行的流程名（尽量用已知流程名中的一个）",'
        '"parameters": {"store_name": "商店名称(若提及)", "goods_name": "商品名称(若提及)"},'
        '"confidence": 0到1}'
        "已知可调度流程名：" + name_list + "。"
        "若用户未提供某参数，对应字段填空字符串；不要编造。"
    )
    resp = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": nl},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
    )
    content = resp.choices[0].message.content
    data = json.loads(content)
    return data


def parse_intent_local(nl: str, workflow_names: list) -> dict:
    """
    本地规则兜底：不依赖任何 LLM，纯正则/子串匹配。
    支持：
      - 商店/商品： "商店=xx" "商品=yy" "商店名 xx" "商品名 yy" "店 xx" "货 yy"
      - 流程名： 在已知流程名里做子串命中（NL 含流程名 或 流程名含 NL 关键词）
    """
    params = {"store_name": "", "goods_name": ""}

    # 商店/商品 抽取（支持 = 、：、空格 分隔）
    patterns = {
        "store_name": [r"商店[名称]*\s*[:=：]\s*([^\s，,。；;]+)",
                        r"店[名称]*\s*[:=：]\s*([^\s，,。；;]+)",
                        r"店铺\s*[:=：]\s*([^\s，,。；;]+)"],
        "goods_name": [r"商品[名称]*\s*[:=：]\s*([^\s，,。；;]+)",
                        r"货[名称]*\s*[:=：]\s*([^\s，,。；;]+)",
                        r"物品\s*[:=：]\s*([^\s，,。；;]+)"],
    }
    for key, pats in patterns.items():
        for pat in pats:
            m = re.search(pat, nl)
            if m:
                params[key] = m.group(1).strip()
                break

    # 流程名匹配：取与 NL 重叠度最高的已知流程
    hint = ""
    best_score = 0
    for name in workflow_names:
        # 双向子串
        if name and name in nl:
            score = len(name)
        elif name:
            # 字符交集比例
            inter = sum(1 for c in name if c in nl)
            score = inter / max(len(name), 1)
        else:
            score = 0
        if score > best_score:
            best_score = score
            hint = name
    confidence = 0.9 if best_score >= len(hint) / 2 else 0.4
    return {"workflow_hint": hint, "parameters": params, "confidence": confidence}


def match_workflow(workflows, hint):
    """在流程列表里按 hint 选最匹配的一条。返回 (record, score)。"""
    best, best_score = None, 0.0
    for wf in workflows:
        name = wf.get("name", "")
        if not hint:
            continue
        if hint in name or name in hint:
            score = 1.0
        else:
            inter = sum(1 for c in hint if c in name)
            score = inter / max(len(hint), 1)
        if score > best_score:
            best_score, best = score, wf
    return best, best_score


# ------------------------------ 编排 ------------------------------
def dispatch(nl: str, use_llm: bool, interactive: bool):
    client = RpaClient()

    print("① 拉取可调度流程列表 ...")
    workflows = client.list_workflows()
    print(f"  共 {len(workflows)} 条：")
    for wf in workflows:
        params = [p.get("varName") for p in wf.get("parameters", [])]
        print(f"   - {wf.get('name')}  (project_id={wf.get('project_id')}, 入参={params})")

    print("\n② 解析自然语言意图 ...")
    names = [w.get("name", "") for w in workflows]
    if use_llm and DEEPSEEK_API_KEY:
        try:
            intent = parse_intent_with_deepseek(nl, names)
            print(f"  [DeepSeek] {json.dumps(intent, ensure_ascii=False)}")
        except Exception as e:  # noqa
            print(f"  [DeepSeek 失败，降级本地解析] {e}")
            intent = parse_intent_local(nl, names)
    else:
        intent = parse_intent_local(nl, names)
        print(f"  [本地解析] {json.dumps(intent, ensure_ascii=False)}")

    hint = intent.get("workflow_hint", "")
    print(f"\n③ 匹配目标流程（hint='{hint}'）...")
    target, score = match_workflow(workflows, hint)
    if not target:
        print("  ✗ 未匹配到任何流程，退出。")
        return 1
    print(f"  命中：{target.get('name')}  (project_id={target.get('project_id')}, 匹配度={score:.2f})")
    if score < 0.5 and not interactive:
        print("  ⚠ 匹配度偏低，建议用 --interactive 确认。")

    # 填参：用流程定义的入参 varName 拉平，缺失的提示/留空
    print("\n④ 组装执行参数 ...")
    required = [p.get("varName") for p in target.get("parameters", [])]
    provided = intent.get("parameters", {})
    final_params = {}
    for var in required:
        if var in provided and provided[var]:
            final_params[var] = provided[var]
        else:
            if interactive:
                val = input(f"  请填写入参 [{var}]（{_describe(target, var)}）：").strip()
                final_params[var] = val
            else:
                final_params[var] = ""
                print(f"  入参 [{var}] 未从指令解析到，留空（{_describe(target, var)}）。")
    print(f"  最终参数：{json.dumps(final_params, ensure_ascii=False)}")

    print("\n⑤ 触发异步执行 ...")
    exec_id = client.execute_async(target.get("project_id"), final_params)
    print(f"  execution_id={exec_id}")

    print("\n⑥ 轮询执行结果 ...")
    result = client.poll_execution(exec_id)
    print("\n=== 执行结果 ===")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _describe(wf, var):
    for p in wf.get("parameters", []):
        if p.get("varName") == var:
            return p.get("varDescribe") or p.get("varType") or ""
    return ""


# ------------------------------ 入口 ------------------------------
def main():
    ap = argparse.ArgumentParser(description="星辰开源 P1 PoC：AI→RPA OpenAPI 调度")
    ap.add_argument("--nl", help="自然语言指令，例如 '执行 沙发上等着，商店=测试店，商品=测试商品'")
    ap.add_argument("--interactive", action="store_true", help="缺失参数时交互补全")
    ap.add_argument("--no-llm", action="store_true", help="强制走本地规则解析（不调 DeepSeek）")
    args = ap.parse_args()

    if args.interactive:
        nl = args.nl or input("请输入自然语言指令：")
    else:
        if not args.nl:
            ap.error("请提供 --nl '...' 或加 --interactive")
        nl = args.nl

    use_llm = not args.no_llm
    try:
        sys.exit(dispatch(nl, use_llm=use_llm, interactive=args.interactive))
    except requests.exceptions.ConnectionError as e:
        sys.stderr.write(
            f"\n✗ 网络不可达（{BASE}）。本 PoC 需在能访问 192.168.76.130:32742 的环境运行。\n"
            f"  详情：{e}\n"
        )
        sys.exit(3)
    except Exception as e:  # noqa
        sys.stderr.write(f"\n✗ 调度失败：{e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
