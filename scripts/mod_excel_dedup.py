# -*- coding: utf-8 -*-
"""Excel 按列去重子流程：按指定列删掉重复行。

## 为什么用 Python 子流程

「按某列的重复值去重」需要**跨行记忆已见的值**，原子只能逐行读、没有
集合/字典去重能力，所以走 Python 子流程。

## 干什么

读 `src_excel`（第一个 sheet）→ 按 `key_col`（列名或 A/1）判断重复 →
每个键只保留 `keep`（first 第一条 / last 最后一条）→ 写出新文件。

## 安全底线

1. 只读源文件，只写新文件；重名自动编号不覆盖。
2. 空值行：默认当作一组保留（`keep_blank=true`），可关。
3. 列名找不到直接报错，不瞎猜。
4. 返回去掉了多少行，便于核对。
"""
from typing import Any

import os
import re

try:
    from astronverse.workflowlib import logger
except Exception:
    import logging
    logger = logging.getLogger("mod_excel_dedup")

import openpyxl


def _col_to_idx(token):
    token = (token or "").strip()
    if not token:
        return None
    if token.isdigit():
        return int(token) - 1
    if re.match(r"^[A-Za-z]+$", token):
        s = 0
        for ch in token.upper():
            s = s * 26 + (ord(ch) - 64)
        return s - 1
    return None


def _unique(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 1
    while os.path.exists("%s(%d)%s" % (base, i, ext)):
        i += 1
    return "%s(%d)%s" % (base, i, ext)


def main(args) -> Any:
    src = (args.get("src_excel") or "").strip()
    col = (args.get("key_col") or "").strip()
    out_file = (args.get("out_file") or "").strip()
    keep = (args.get("keep") or "first").strip().lower()
    keep_blank = str(args.get("keep_blank") or "true").strip().lower() in ("true", "1", "yes", "是")

    if not src or not os.path.isfile(src):
        raise RuntimeError("源 Excel 不存在：{}".format(src))
    if not col:
        raise RuntimeError("必须指定 key_col（按哪一列去重）")

    wb = openpyxl.load_workbook(src, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if not rows:
        raise RuntimeError("源 Excel 是空的")
    header = list(rows[0])

    ci = _col_to_idx(col)
    if ci is None:
        ncol = col.strip().lower()
        ci = next((i for i, v in enumerate(header) if str(v).strip().lower() == ncol), None)
        if ci is None:
            raise RuntimeError("找不到列名：{}（表头有 {}）".format(col, [str(h) for h in header]))
    if ci >= len(header):
        raise RuntimeError("列 {} 超出表头范围（共 {} 列）".format(ci + 1, len(header)))

    seen = {}
    order = []
    blanks = 0
    for r in rows[1:]:
        if not any("" if x is None else str(x).strip() for x in r):
            continue  # 全空行直接丢
        key = r[ci] if ci < len(r) else None
        k = "" if key is None else str(key).strip()
        if k == "":
            blanks += 1
            if not keep_blank:
                continue
            k = "（空）"
        if k in seen:
            if keep == "last":
                seen[k] = list(r)
            continue
        seen[k] = list(r)
        order.append(k)

    out_rows = [seen[k] for k in order]
    removed = len(rows) - 1 - len(out_rows)

    nwb = openpyxl.Workbook()
    nws = nwb.active
    if header:
        nws.append(header)
    for r in out_rows:
        nws.append(r)

    if not out_file:
        base, ext = os.path.splitext(src)
        out_file = base + "_去重" + (ext or ".xlsx")
    out_dir = os.path.dirname(out_file)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    dst = _unique(out_file)
    nwb.save(dst)
    nwb.close()

    logger.info("去重完成：{} 行 → {} 行（去掉 {} 行，空值 {} 行）".format(
        len(rows) - 1, len(out_rows), removed, blanks))
    return {
        "ok": True,
        "out_file": dst,
        "in_rows": len(rows) - 1,
        "out_rows": len(out_rows),
        "removed": removed,
        "blank_keys": blanks,
        "key_col": col,
        "keep": keep,
    }
