# -*- coding: utf-8 -*-
"""Excel 按条件拆表子流程：把一张大表按某一列的值拆成多个文件。

## 为什么用 Python 子流程而不是纯原子

`Excel.read_excel` 能读成二维数组、`Excel.edit_excel`/`create_excel` 能写，但
「按某列的不同值各生成一个文件」是**分组 + 多文件输出**，原子拼不出来
（原子一次只能写一个目标）。所以走 Python 子流程（降级顺序：原子 → Python → 新原子）。

## 干什么

读 `src_excel` → 按 `split_col` 这一列的值分组 → 每组生成一个 `<前缀>_<值>.xlsx`
（保留表头）。例如一张「报销单」按「部门」列拆，得到 `部门A.xlsx` `部门B.xlsx`。

## 安全底线

1. **不删不改源文件**，只往外写新文件。
2. **不覆盖**：目标重名自动变 `<前缀>_<值>(1).xlsx`。
3. 空值行默认跳过（也可 `--skip_blank=false` 单独成一组「（空）」）。
4. 列名找不到就报错，不瞎猜。
5. 只复制**值**（不复制单元格格式/公式），产物是可编辑 Excel，够用；
   要保格式是另一个需求，不在本模块范围。
"""
from typing import Any

import os
import re
import time

try:
    from astronverse.workflowlib import logger
except Exception:
    import logging
    logger = logging.getLogger("mod_excel_split")

import openpyxl


def _col_to_idx(token):
    """`A`→0、`1`→0、`部门`→None（返回 None 表示按列名匹配）。"""
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


def _norm_fn(v):
    """值转成文件名片段：去掉非法字符、截断。"""
    s = re.sub(r'[\\/:*?"<>|]', "_", str(v))
    return (s.strip() or "空")[:60]


def main(args) -> Any:
    src = (args.get("src_excel") or "").strip()
    col = (args.get("split_col") or "").strip()
    out_dir = (args.get("out_dir") or "").strip() or (os.path.dirname(src) or ".")
    prefix = (args.get("out_name") or "").strip() or "拆分"
    copy_header = str(args.get("copy_header") or "true").strip().lower() in ("true", "1", "yes", "是")
    skip_blank = str(args.get("skip_blank") or "true").strip().lower() in ("true", "1", "yes", "是")

    if not src or not os.path.isfile(src):
        raise RuntimeError("源 Excel 不存在：{}".format(src))
    if not col:
        raise RuntimeError("必须指定 split_col（按哪一列拆分）")
    os.makedirs(out_dir, exist_ok=True)

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
            raise RuntimeError("找不到列名：{}（表头有 {})".format(col, [str(h) for h in header]))
    if ci >= len(header):
        raise RuntimeError("列 {} 超出表头范围（共 {} 列）".format(ci + 1, len(header)))

    groups = {}
    for r in rows[1:]:
        if ci >= len(r):
            continue
        key = r[ci]
        if key is None or str(key).strip() == "":
            if skip_blank:
                continue
            key = "（空）"
        groups.setdefault(str(key), []).append(list(r))

    created = []
    for key, grp in groups.items():
        nwb = openpyxl.Workbook()
        nws = nwb.active
        if copy_header and header:
            nws.append(header)
        for r in grp:
            nws.append(r)
        fn = "{}_{}.xlsx".format(prefix, _norm_fn(key))
        dst = os.path.join(out_dir, fn)
        base, ext = os.path.splitext(dst)
        i = 1
        while os.path.exists(dst):
            dst = "{}({}).{}".format(base, i, ext)
            i += 1
        nwb.save(dst)
        nwb.close()
        created.append({"key": str(key), "file": os.path.basename(dst), "rows": len(grp)})
        logger.info("拆分：{} → {} ({} 行)".format(key, os.path.basename(dst), len(grp)))

    logger.info("完成：{} 个分组，生成 {} 个文件".format(len(groups), len(created)))
    return {
        "ok": True,
        "src_excel": src,
        "split_col": col,
        "out_dir": out_dir,
        "count": len(created),
        "groups": {c["key"]: c["rows"] for c in created},
        "files": [c["file"] for c in created],
        "detail": created,
    }
