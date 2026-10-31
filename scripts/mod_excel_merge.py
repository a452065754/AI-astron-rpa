# -*- coding: utf-8 -*-
"""Excel 多表合并子流程：把文件夹里多个 xlsx 合成一张表。

## 为什么用 Python 子流程

原子能做到「读一张表」「写一行」，但「把 N 张表的行追加到同一张表」需要
**跨文件累计行号**（第 2 个文件要写在第一个文件最后一行之后）。原子没有
变量自增/算术赋值，循环里拿不到「当前已写行数」，所以按降级顺序走 Python。

## 干什么

扫 `in_dir` 第一层所有匹配 `pattern` 的 xlsx → 只取每本第一个 sheet →
保留第一张表的表头 → 其余表的**数据行**依次追加 → 写出 `out_file`。

## 安全底线

1. 只读源文件，只写一个新文件。
2. `out_file` 已存在则自动变 `<名>(1).xlsx`，不覆盖。
3. 表头不一致时**不静默合并**：把差异写进报告并跳过该文件。
4. 空表/无数据的文件跳过并记录。
"""
from typing import Any

import os
import re

try:
    from astronverse.workflowlib import logger
except Exception:
    import logging
    logger = logging.getLogger("mod_excel_merge")

import openpyxl


def _norm(v):
    return "" if v is None else str(v).strip()


def _unique(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 1
    while os.path.exists("%s(%d)%s" % (base, i, ext)):
        i += 1
    return "%s(%d)%s" % (base, i, ext)


def main(args) -> Any:
    in_dir = (args.get("in_dir") or "").strip()
    out_file = (args.get("out_file") or "").strip()
    pattern = (args.get("pattern") or ".xlsx").strip().lower()
    copy_header = str(args.get("copy_header") or "true").strip().lower() in ("true", "1", "yes", "是")

    if not in_dir or not os.path.isdir(in_dir):
        raise RuntimeError("待合并的文件夹不存在：{}".format(in_dir))
    if not out_file:
        raise RuntimeError("必须指定 out_file（合并后的输出文件）")

    files = [os.path.join(in_dir, f) for f in sorted(os.listdir(in_dir))
             if f.lower().endswith(pattern) and not f.startswith("~$")
             and os.path.isfile(os.path.join(in_dir, f))]
    if not files:
        raise RuntimeError("文件夹里没有匹配 {} 的文件：{}".format(pattern, in_dir))

    header = None
    merged = []
    used, skipped = [], []

    for p in files:
        try:
            wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
            ws = wb.active
            rows = list(ws.iter_rows(values_only=True))
            wb.close()
        except Exception as e:
            skipped.append({"file": os.path.basename(p), "why": "读取失败：{}".format(e)})
            continue
        if not rows:
            skipped.append({"file": os.path.basename(p), "why": "空表"})
            continue
        h = [_norm(x) for x in rows[0]]
        if header is None:
            header = h
        elif h != header:
            skipped.append({"file": os.path.basename(p),
                            "why": "表头与第一张表不一致（{} vs {}）".format(h[:4], header[:4])})
            continue
        data = [r for r in rows[1:] if any(_norm(x) for x in r)]
        if not data:
            skipped.append({"file": os.path.basename(p), "why": "没有数据行"})
            continue
        merged.extend(data)
        used.append({"file": os.path.basename(p), "rows": len(data)})

    if header is None or not merged:
        raise RuntimeError("没有可合并的数据（{} 个文件全部跳过）".format(len(skipped)))

    nwb = openpyxl.Workbook()
    nws = nwb.active
    if copy_header:
        nws.append(header)
    for r in merged:
        nws.append(list(r))

    out_dir = os.path.dirname(out_file)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    dst = _unique(out_file)
    nwb.save(dst)
    nwb.close()

    logger.info("合并完成：{} 个文件 → {}（{} 行）".format(len(used), os.path.basename(dst), len(merged)))
    if skipped:
        for s in skipped:
            logger.warning("跳过 {}：{}".format(s["file"], s["why"]))

    return {
        "ok": True,
        "out_file": dst,
        "files_used": len(used),
        "rows": len(merged),
        "columns": len(header),
        "detail": used,
        "skipped": skipped,
    }
