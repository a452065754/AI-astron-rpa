# -*- coding: utf-8 -*-
"""MES 包装标签打印 —— 未生成计划工单的批量生成（分批直连版）。

## 为什么不点页面上的「全选 → 批量生成」

实测（2026-11-03，`MES_ACCOUNT` 账号）：

| 事实 | 数值 |
| --- | --- |
| `CHBQ.ashx?type=getgd&sfsc=1` 返回待生成 | 29071 条 / 10.99 MB |
| 页面 datagrid | 客户端分页，**一页只渲染 200 行**，共 146 页 |
| 表头「全选」 | 勾的是全部 29071 条（不是当前页） |
| 「批量生成」提交方式 | `GET ...?type=SCtm_Batch&ids='g1','g2',...` |
| URL 长度实测 | 16 KB 可以，20 KB 返回 HTTP 431 |

⇒ 全选 29071 条时前端会拼出约 1.1 MB 的 URL，服务端直接拒收。所以这里按批切分，
每批控制在 URL 安全长度内（默认 200 条 ≈ 9 KB 编码后）。

## 为什么要「走接口」而不是操作网格

「是否生成计划」是 easyui-combobox、纳期是 easyui-datebox，原始控件都被 EasyUI
换成隐藏 input，元素级操作既不稳也读不到真实值。这里直接调用页面自己用的那两个
接口，参数语义与 `searchdata()` / `batch_GEN()` 完全一致（对照 CHBQ.aspx 内联 JS）。

## 安全底线

1. `dry_run=true` **只查不提交**，第一次务必先演练。
2. 单批条数上限 400，超过会被强制截断（防 URL 超长被拒）。
3. `max_rounds` 兜底，异常时不会死循环。
4. 只处理 `sfsc` 命中且纳期 >= 起始日期的工单，绝不"全库扫"。
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request

try:
    from astronverse.workflowlib import logger
except Exception:  # 本地裸跑（没有 astronverse）时退回标准库
    import logging

    logger = logging.getLogger("mes_chbq")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AstronRPA/mes-chbq"

# 服务端 URL 实测上限 16KB 可过、20KB 报 431；留足余量
MAX_QUERY_STRING = 12000
MAX_BATCH = 400


def _truthy(v, default=False):
    if v is None or v == "":
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "y", "是")


def _today():
    return time.strftime("%Y-%m-%d")


# 纳期起传这些值 = 不限下限（用于清历史积压）。
# 为什么需要：页面默认纳期起就是「今天」，而实测库里待生成工单纳期最新只到 2026-09-29，
# 所以用默认值跑每轮都查到 0 条、流程立刻正常结束 —— 看起来像「没干活」。
# 想清积压就得显式传 all。
_UNLIMITED = ("all", "*", "不限", "全部", "none", "0")


def _http(url, timeout=180):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8-sig", errors="replace")


def _log(out_dir, line):
    """每个动作同时：写运行记录 + 打到流程日志，出问题好回溯。"""
    try:
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "运行记录.txt"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:  # 日志写不进去不该中断业务
        logger.warning("写运行记录失败：{}".format(e))
    logger.info(line)


def _fetch_pending(base, sfsc, date_from, date_to):
    """取待生成工单 GID 列表。参数与页面 searchdata() 拼的完全一致。"""
    qs = "?r={}&type=getgd&sfsc={}".format(time.time(), urllib.parse.quote(str(sfsc)))
    if date_from:
        qs += "&KSSJ=" + urllib.parse.quote(str(date_from))
    if date_to:
        qs += "&JSSJ=" + urllib.parse.quote(str(date_to))
    status, text = _http(base + "/webajax/bg/CHBQ.ashx" + qs)
    if status != 200:
        raise RuntimeError("查询待生成工单失败：HTTP {}".format(status))
    try:
        rows = json.loads(text)
    except Exception:
        raise RuntimeError("查询返回不是 JSON，前 200 字符：{}".format(text[:200]))
    ids = []
    for r in rows or []:
        if isinstance(r, dict) and r.get("GID"):
            ids.append(str(r["GID"]))
    return ids


def _chunks(ids, size):
    for i in range(0, len(ids), size):
        yield ids[i:i + size]


def _submit(base, chunk):
    """按页面 batch_GEN() 的格式提交：ids='g1','g2',...（单引号+逗号，整体 URL 编码）。"""
    quoted = ",".join("'{}'".format(x) for x in chunk)
    url = "{}/webajax/bg/CHBQ.ashx?type=SCtm_Batch&ids={}".format(
        base, urllib.parse.quote(quoted, safe="")
    )
    if len(url) > MAX_QUERY_STRING + 200:
        raise RuntimeError("单批 URL 过长（{} 字符），请把 batch_size 调小".format(len(url)))
    status, text = _http(url)
    plain = " ".join(str(text).replace("<br>", " ").replace("<br/>", " ").split())
    return status, plain[:400]


def main(args) -> dict:
    base = (args.get("base_url") or "http://192.168.0.26:81").strip().rstrip("/")
    sfsc = str(args.get("sfsc") or "1").strip()
    raw_from = (args.get("arrival_from") or "").strip()
    if raw_from.lower() in _UNLIMITED:
        date_from = ""                                    # 不传 KSSJ = 不限下限
    else:
        date_from = raw_from or _today()                  # 留空沿用页面默认：纳期 >= 今天
    date_to = (args.get("arrival_to") or "").strip()
    batch_size = int(str(args.get("batch_size") or "200").strip() or 200)
    batch_size = max(1, min(batch_size, MAX_BATCH))
    wait_seconds = float(str(args.get("wait_seconds") or "60").strip() or 60)
    wait_seconds = max(0.0, min(wait_seconds, 3600.0))
    max_rounds = int(str(args.get("max_rounds") or "50").strip() or 50)
    dry_run = _truthy(args.get("dry_run"), True)
    out_dir = (args.get("out_dir") or "").strip()

    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    head = "===== {} | 是否生成计划={} | 纳期 {}~{} | 每批 {} | 等待 {}s | {} =====".format(
        time.strftime("%Y-%m-%d %H:%M:%S"), sfsc, date_from or "不限", date_to or "不限",
        batch_size, int(wait_seconds), "演练（不提交）" if dry_run else "正式生成"
    )
    if out_dir:
        _log(out_dir, head)
    else:
        logger.info(head)

    total_seen = 0          # 累计看到的待生成条数
    total_submitted = 0     # 累计提交条数
    rounds = 0
    remain = 0
    failed_batches = 0

    while rounds < max_rounds:
        ids = _fetch_pending(base, sfsc, date_from, date_to)
        remain = len(ids)
        if remain == 0:
            if rounds == 0 and date_from:
                # 最常见的误判：以为流程没跑，其实是纳期下限把数据全过滤掉了
                msg = ("第 0 轮：纳期 >= {} 没有待生成工单。"
                       "若目的是清历史积压，把 arrival_from 填 all（不限下限）再跑一次。"
                       .format(date_from))
            else:
                msg = "第 {} 轮复查：已无待生成工单".format(rounds)
            _log(out_dir, msg) if out_dir else logger.info(msg)
            break

        total_seen += remain
        chunk = ids[:batch_size]

        if dry_run:
            msg = "【演练】本轮待生成 {} 条，本批将提交 {} 条，未真正提交".format(remain, len(chunk))
            _log(out_dir, msg) if out_dir else logger.info(msg)
            rounds += 1
            break

        status, text = _submit(base, chunk)
        total_submitted += len(chunk)
        rounds += 1
        line = "第 {} 轮 | 待生成 {} 条 | 提交 {} 条 | HTTP {} | {}".format(
            rounds, remain, len(chunk), status, text
        )
        _log(out_dir, line) if out_dir else logger.info(line)

        if status != 200:
            failed_batches += 1
            if failed_batches >= 3:
                raise RuntimeError("连续 3 批提交失败，停止（最后返回：{}）".format(text))

        if wait_seconds:
            time.sleep(wait_seconds)

    ids_left = _fetch_pending(base, sfsc, date_from, date_to)

    summary = {
        "轮次": rounds,
        "各轮待生成合计": total_seen,
        "累计提交": total_submitted,
        "复查剩余": len(ids_left),
        "演练模式": dry_run,
        "纳期起": date_from or "不限",
        "纳期止": date_to or "不限",
        "是否生成计划": sfsc,
    }
    tail = "===== 结束：共 {} 轮，提交 {} 条，复查剩余 {} 条，失败批次 {} =====".format(
        rounds, total_submitted, len(ids_left), failed_batches
    )
    _log(out_dir, tail) if out_dir else logger.info(tail)
    return summary
