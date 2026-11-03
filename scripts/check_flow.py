# -*- coding: utf-8 -*-
"""
星辰开源 · 流程版本核对（check_flow.py）
==========================================

只读，不写任何数据。用途：确认**服务端上实际存的是什么版本**。

为什么需要它（2026-11-03 踩过的坑）：
  设计器客户端在「关闭某个流程标签」时，会把内存里的流程内容回存到服务端。
  如果设计器打开该流程的时间早于 AI 落库的时间，关闭时就会用旧内容
  把新写入的流程覆盖掉 —— 表现是「AI 说写好了，设计器里还是旧流程」，
  而 AI 侧的回读校验（历史上只判非空）会误报「一致」。

用法：
    python check_flow.py                      # 默认核对机器人「MES包装标签生成」
    python check_flow.py --robot "机器人名"    # 按名字
    python check_flow.py --robot 1234567890    # 按 robotId
    python check_flow.py --robot "机器人名" --expect flows/xxx.json
        # --expect 会比对节点数，不一致直接给出差异，方便判断是否被覆盖

⚠️ 必须用**设计器自带的 python_core** 跑（依赖 requests，系统 python 没有）：
    "%APPDATA%/astron-rpa/python_core/python.exe" scripts/check_flow.py ...
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import publish_flow as pf

DEFAULT_ROBOT = "MES包装标签生成"


def resolve_robot(ck, name_or_id):
    """名字 → robotId；纯数字则当 id 用。"""
    s = str(name_or_id).strip()
    if s.isdigit():
        return s, s
    rid = pf.find_robot(ck, s)
    if not rid:
        raise SystemExit(f"没找到机器人「{s}」，请检查名字（或直接给 robotId）")
    return rid, s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--robot", default=DEFAULT_ROBOT, help="机器人名或 robotId")
    ap.add_argument("--expect", default="", help="流程规格 json，用来比对节点数")
    args = ap.parse_args()

    ck = pf.load_session()
    rid, label = resolve_robot(ck, args.robot)
    print(f"机器人：{label}（{rid}）  当前用户：{pf.whoami(ck)}")

    procs = pf.list_processes(ck, rid)
    if not procs:
        raise SystemExit("该机器人下没有任何流程")
    for p in procs:
        print("  流程：%s（%s）" % (p.get("processName") or p.get("name"),
                                  p.get("processId") or p.get("resourceId")))

    main_p = next((p for p in procs if (p.get("processName") or p.get("name")) == "主流程"), None)
    if not main_p:
        raise SystemExit("没有「主流程」——设计器「编辑」默认打开的就是主流程")
    pid = main_p.get("processId") or main_p.get("resourceId")

    raw = pf.read_process(ck, rid, pid) or ""
    try:
        nodes = json.loads(raw)
    except Exception:
        raise SystemExit(f"流程内容不是合法 JSON（{len(raw)} 字节）：{raw[:200]}")

    print(f"\n主流程：{len(raw)} 字节 / {len(nodes)} 节点")
    for i, n in enumerate(nodes, 1):
        print("  %2d %-28s %s" % (i, n.get("key"), n.get("alias")))

    if args.expect:
        spec = json.load(open(args.expect, encoding="utf-8"))
        want = len(spec.get("nodes") or [])
        w_aliases = [n.get("alias") for n in spec["nodes"]]
        have = [n.get("alias") for n in nodes]
        if want == len(nodes):
            print(f"\n✅ 与规格一致：{want} 节点")
        else:
            print(f"\n❌ 与规格不一致：规格 {want} 节点 / 服务端 {len(nodes)} 节点")
        missing = [a for a in w_aliases if a not in have]
        extra = [a for a in have if a not in w_aliases]
        if missing:
            print("   服务端缺少：", missing)
        if extra:
            print("   服务端多出：", extra)
        if missing or extra:
            print("   → 很可能被设计器「关闭流程时回存旧内容」覆盖。"
                  "请先在设计器里关掉该流程，再重新落库。")


if __name__ == "__main__":
    main()
