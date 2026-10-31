# -*- coding: utf-8 -*-
"""
mod_pdf_layout —— PDF → 保留版式的可编辑 Word（版面分析重建）

## 和 mod_pdf_ocr 的区别（为什么要多一个）

`mod_pdf_ocr` 只把 OCR 文字按行倒进 Word，产出是「94 行纯文本」：没有字号分级、
没有居中、没有表格、没有页脚。用户 2026-10-30 明确反馈「转出来的 PDF 格式和
原件不一致」，所以要的是**版面还原**，不是文字搬运。

## 做法

1. **渲染**：`astronverse.pdf` 原子把每页 PDF 转成 PNG（扫描件和文本层都适用）
2. **识别**：Windows 自带 OCR（离线免费），**先把页面放大 3 倍再识别**——
   实测 1 倍时「5018.84」会识别成「501 & 84」，3 倍就对了
3. **框线检测**（纯像素，本模块最关键的一步）：
   - 二值化后找**长横线**：用「间隙容忍的游程」——PDF 里 0.5px 的线渲染出来是
     交替明暗的亚像素线，直接数暗像素只能拿到一半，会把线判丢
   - 两条横线之间的带状区域里找**长竖线**，且用**严格阈值**（灰度 < 120）：
     扫描件常有一层浅灰竖条纹（灰度 130~215），用宽松阈值会把它们当边框，
     导致两张表被粘成一张、列数翻倍
   - 「相邻两横线之间夹着 ≥2 根竖线」→ 同属一张表，顺着链下去就是整张表。
     **不要按词坐标聚类**：中文正文每行也有几十个词，按 x 聚类会把整段正文
     误判成表格（本模块 v1 就踩过这个坑）
4. **填格**：把词按中心点落进框线围出的网格；边界「在不在」用**像素有没有暗点**
   判断（不用线段长度——扫描件的边框常有断口），缺失则视为合并单元格。
   先算出**互不重叠**的矩形再合并，避免 python-docx 的
   `requested span not rectangular`
5. **文字块**：字号以正文行高中位数为基准按比例分级、居中判定、缩进、加粗
   （用**墨量**即笔画密度判断，OCR 不给字重）
6. **页眉页脚**：原件每页重复的公司抬头和地址电话 → 写进 Word 的节页眉/页脚，
   这样它们不占正文空间，也不会因为正文变长被挤到下一页
7. **重建**：python-docx 生成 docx（真表格 + 真字号 + 真对齐 + 精确行距 + 页边距
   照原件反推），完全可编辑

## 输入 args

    src_pdf    源 PDF 路径
    out_dir    输出目录
    out_name   输出文件名（不带 .docx）
    ps1        win_ocr.ps1 路径
    lang       OCR 语言，默认 zh-Hans-CN
    keep_table 是否还原表格，默认 true
    ocr_scale  OCR 前放大倍数，默认 3

## 输出

    {"ok":True,"docx":路径,"pages":n,"tables":k,"paragraphs":m,"chars":c}

## 已知边界（诚实说明，不假装全能）

- **图片/logo 不还原**：扫描件整页就是一张图，无法把 logo 从页面里分离出来
- **手写签名/手写字不还原**：OCR 认不出手写，那一格会是空的或乱码
- **表格底纹、边框粗细不还原**：统一成细实线单线框
- **跨页表格不合并**：原件跨页的两段会还原成两张表（内容不丢）
- **OCR 固有错字**：个别汉字会认错（本例「总计」被认成「。计」）。数字和小数点
  已做归一化（`·`/`，`/`％` → `.`/`%`），汉字错字需人工改——好在产物是可编辑 Word
"""
from typing import Any

import gc
import glob
import json
import os
import re
import statistics
import subprocess
import tempfile

from astronverse.workflowlib import logger

# A4 尺寸（pt），用于 px → pt 换算
_A4_H_PT = 842.0
_A4_W_PT = 595.0

# 横线二值化阈值：PDF 的细线渲染出来很浅，取 245 才抓得住
_LINE_TH = 245
# 竖线/边界判定阈值：只认近黑色，排除扫描件的浅灰条纹（灰度 130~215）
_STRICT_TH = 120


# ------------------------------------------------------------------ 图像
def _load_gray(img_path: str, max_w: int = 2600):
    """读灰度图 → (ndarray, scale)。scale = 分析图宽 / 原图宽。"""
    import numpy as np
    from PIL import Image

    im = Image.open(img_path).convert("L")
    w, h = im.size
    scale = 1.0
    if w > max_w:
        nh = int(h * max_w / float(w))
        im = im.resize((max_w, nh), Image.LANCZOS)
        scale = max_w / float(w)
    return np.asarray(im, dtype=np.uint8), scale


def _segments(mask, gap: int = 3) -> list:
    """一维布尔数组 → [(起点, 终点)]，间距 ≤ gap 的碎段合并（亚像素线补偿）。"""
    import numpy as np

    xs = np.flatnonzero(mask)
    if xs.size == 0:
        return []
    brk = np.flatnonzero(np.diff(xs) > gap)
    return [(int(s[0]), int(s[-1])) for s in np.split(xs, brk + 1) if s.size]


def _h_rules(arr, min_len_ratio: float = 0.28) -> list:
    """找长横线 → [{"y1","y2","segs":[(x1,x2)]}]。"""
    H, W = arr.shape
    dark = arr < _LINE_TH
    need = W * min_len_ratio
    out = []
    for y in range(H):
        segs = [s for s in _segments(dark[y], 3) if s[1] - s[0] + 1 >= need]
        if not segs:
            continue
        if out and y - out[-1]["y2"] <= 8:      # 8px 内的当成同一条（粗线/双线）
            out[-1]["y2"] = y
            out[-1]["segs"].extend(segs)
        else:
            out.append({"y1": y, "y2": y, "segs": list(segs)})
    for r in out:
        xs = [s[0] for s in r["segs"]] + [s[1] for s in r["segs"]]
        r["x1"], r["x2"] = min(xs), max(xs)
    return out


def _v_rules(arr, y1: int, y2: int, th: int = _STRICT_TH,
             need: float = 0.85, gap: int = 4) -> list:
    """带状区域 [y1,y2] 里的竖线 x 坐标（升序）。

    `need` 取 0.85：真边框贯穿整个行带（覆盖率 1.0），而汉字的竖笔画只占行高的
    0.6~0.75，取 0.7 会把字里的竖笔当成列边界，一张 9 列的表能拆成 28 列。
    """
    import numpy as np

    h = y2 - y1 + 1
    if h < 10:
        return []
    band = arr[y1:y2 + 1] < th
    hits = []
    for x in range(band.shape[1]):
        xs = np.flatnonzero(band[:, x])
        if xs.size == 0:
            continue
        brk = np.flatnonzero(np.diff(xs) > gap)
        segs = np.split(xs, brk + 1)
        if max(int(s[-1] - s[0] + 1) for s in segs) >= h * need:
            hits.append(x)
    if not hits:
        return []
    groups, cur = [], [hits[0]]
    for x in hits[1:]:
        if x - cur[-1] <= 4:
            cur.append(x)
        else:
            groups.append(int(round(sum(cur) / float(len(cur)))))
            cur = [x]
    groups.append(int(round(sum(cur) / float(len(cur)))))
    return groups


def _cluster(values: list, tol: float) -> list:
    """一维聚类：把相近的坐标并成一个，返回升序中心。"""
    out = []
    for v in sorted(values):
        if out and abs(v - out[-1]) <= tol:
            out[-1] = (out[-1] + v) / 2.0
        else:
            out.append(float(v))
    return out


def _h_covers(arr, y1: int, y2: int, x1: float, x2: float, need: float = 0.55) -> bool:
    """横线在 [x1,x2] 这段是否真的画了。

    判据用「这段 x 上有多少列存在暗点」，不用线段长度——扫描件的边框常有断口，
    按长度判会把一整行误判成合并单元格。

    `need` 取 0.55：真边框的覆盖率是 1.0，而被合并单元格里的**文字**正好压在
    这条线上时覆盖率只有 0.2~0.4，取 0.55 能把两者分开（v3 用 0.12 时，
    「产成品」这类竖排居中文字会把合并单元格误判成有边框）。
    """
    band = arr[max(0, y1 - 1):y2 + 2, int(x1) + 3:int(x2) - 2] < 150
    if band.size == 0:
        return False
    return float(band.any(axis=0).mean()) >= need


def _v_covers(arr, x: int, y1: int, y2: int, need: float = 0.5) -> bool:
    """竖线 x 是否覆盖了 [y1,y2]（同样只认近黑色）。

    窗口给到 ±3px：列坐标是跨行带聚类出来的均值，单行里的实际竖线可能偏几个像素。
    """
    if y2 - y1 < 6:
        return False
    col = arr[y1:y2 + 1, max(0, x - 3):x + 4] < _STRICT_TH
    return float(col.any(axis=1).mean()) >= need


def _find_tables(arr) -> list:
    """检测所有带框线的表格 → [{y1,y2,x1,x2,rows,hlines,cols}]。"""
    H, W = arr.shape
    rules = _h_rules(arr)
    if len(rules) < 3:
        return []

    link, band_v = [], []
    for a, b in zip(rules, rules[1:]):
        dy = b["y1"] - a["y2"]
        vs = _v_rules(arr, a["y2"] + 3, b["y1"] - 3) if 18 <= dy <= 0.45 * H else []
        link.append(len(vs) >= 2)
        band_v.append(vs)

    tables, i = [], 0
    while i < len(link):
        if not link[i]:
            i += 1
            continue
        j = i
        while j < len(link) and link[j]:
            j += 1
        rs = rules[i:j + 1]
        # 横线挨得太近（双线/粗线没合干净）只留一条，保证 rows 与 hlines 对齐
        keep = []
        for r in rs:
            if keep and r["y1"] - keep[-1][0] <= 10:
                keep[-1][1] = r["y2"]
                continue
            keep.append([r["y1"], r["y2"]])

        cols = _cluster([x for k in range(i, j) for x in band_v[k]], max(8.0, 0.006 * W))
        # 注意：**不要**按「某个 x 被多少行带认可」来过滤列。
        # 合并单元格多的表（如评审表的「评审人及意见」跨 5 列）内部竖线只在表头行出现，
        # 一过滤就把整张表压成 2 列。
        if len(keep) >= 3 and len(cols) >= 2:
            tables.append({
                "y1": keep[0][0], "y2": keep[-1][1],
                "x1": int(cols[0]), "x2": int(cols[-1]),
                "rows": [k[0] for k in keep], "hlines": keep,
                "cols": [int(c) for c in cols],
            })
        i = j + 1
    return tables


# ------------------------------------------------------------ 表格填格
def _join_words(words: list) -> str:
    """词拼回文本：汉字之间不加空格，数字/字母与汉字之间加一个空格。"""
    def is_ascii(ch):
        return ch.isascii() and ch.isalnum()

    def is_cjk(ch):
        return "\u4e00" <= ch <= "\u9fff"

    out = ""
    for w in words:
        t = (w.get("t") or "").strip()
        if not t:
            continue
        if out:
            a, b = out[-1], t[0]
            if (is_ascii(a) and is_ascii(b)) or (is_ascii(a) and is_cjk(b)) \
                    or (is_cjk(a) and is_ascii(b)):
                out += " "
        out += t
    return out.strip()


_NUM_LIKE = re.compile(r"^[\d\s·，,．.。、:：;；%％\-+／/OoQlI|LSBZ&Gg]+$")
_CONFUSE = {"O": "0", "o": "0", "Q": "0", "l": "1", "I": "1", "|": "1", "L": "1",
            "S": "5", "s": "5", "B": "8", "Z": "2", "z": "2",
            "G": "6", "g": "9"}
_SEP = (("·", "."), ("，", "."), (",", "."), ("．", "."), ("。", "."), ("、", "."),
        ("：", "."), (":", "."), ("；", "."), (";", "."), ("％", "%"))


def _fix_cell(text: str):
    """纯数字单元格归一化 → (文本, 是否不确定)。

    OCR 在小号数字上很爱犯三类错：小数点认成 `·`/`，`/`、`、全角 `％`，
    以及 `1.`→`L`、`8.`→`&` 这种字形混淆。这里做保守纠正，
    纠完还留着字母/空格的，标成「不确定」交给用户核对——不硬猜。
    """
    s = (text or "").strip()
    if not s or not _NUM_LIKE.match(s):
        return s, False

    raw = s
    # 「&」夹在数字中间时是「8.」（如 3&69％ = 38.69%），单独出现时只是 8
    s = re.sub(r"(?<=\d)&(?=\d)", "8.", s)
    for a, b in _CONFUSE.items():
        s = s.replace(a, b)
    for a, b in _SEP:
        s = s.replace(a, b)
    s = re.sub(r"\s+", " ", s).strip()

    # 「4951 58」这类：中间那个空格原本是小数点（金额都是两位小数）
    m = re.match(r"^(\d+) (\d{2})(%?)$", s)
    if m:
        s = "{}.{}{}".format(m.group(1), m.group(2), m.group(3))
    else:
        s = s.replace(" ", "")

    uncertain = (s != re.sub(r"\s+", "", raw)) and not re.match(r"^[\d.%\-+]+$", s)
    return s, uncertain


def _build_grid(words: list, tb: dict, arr, scale: float = 1.0,
                px2pt: float = 0.26, page_w_px: int = 0) -> dict:
    """把词落进网格，并算出**互不重叠**的单元格矩形。

    `tb` 的坐标是**分析图**坐标，`words` 是**原图**坐标，用 `scale` 换算。

    返回 {"n_rows","n_cols","cells":[{r1,c1,r2,c2,text}],"heights":[pt..]}
    `heights` 是每行的原始高度（pt）——评审表那种要签字的空行如果不定高，
    Word 会把它们压成一条线，看起来就和原件不像了。
    """
    ys, xs = tb["rows"], tb["cols"]
    n_r, n_c = len(ys) - 1, len(xs) - 1
    if n_r < 1 or n_c < 1:
        return {"n_rows": 0, "n_cols": 0, "cells": []}

    buckets = [[[] for _ in range(n_c)] for _ in range(n_r)]
    for w in words:
        cx = (w["x"] + w["w"] / 2.0) / scale
        cy = (w["y"] + w["h"] / 2.0) / scale
        if not (tb["y1"] - 2 <= cy <= tb["y2"] + 2 and tb["x1"] - 2 <= cx <= tb["x2"] + 2):
            continue
        ci = next((k for k in range(n_c) if xs[k] <= cx <= xs[k + 1]), None)
        ri = next((k for k in range(n_r) if ys[k] <= cy <= ys[k + 1]), None)
        if ri is None or ci is None:
            continue
        buckets[ri][ci].append(w)

    # 边界在不在：h_ok[r][c] = 第 r 行上边界覆盖第 c 列；v_ok[r][c] = 第 c 列左边界覆盖第 r 行
    h_ok = [[True] * n_c for _ in range(n_r + 1)]
    for r in range(1, n_r):
        y1, y2 = tb["hlines"][r]
        for c in range(n_c):
            h_ok[r][c] = _h_covers(arr, y1, y2, xs[c], xs[c + 1])
    v_ok = [[True] * n_c for _ in range(n_r)]
    for r in range(n_r):
        for c in range(1, n_c):
            v_ok[r][c] = _v_covers(arr, xs[c], ys[r], ys[r + 1], 0.5)

    # 贪心切矩形（行优先）：先向下扩（该列下方无边界），再向右扩（跨过的行都无边界）
    taken = [[False] * n_c for _ in range(n_r)]
    cells = []

    def _vmerge_ok(r: int, r2: int, c: int) -> bool:
        """纵向合并是否成立：合并单元格里只有**一整块文字**。

        扫描件里边框常有断口，光看「这里有没有线」会误判。判据用文字的行距：
        真合并格（哪怕两行字，如「总经理/审批」）行与行是正常行距；
        而断线造成的假合并，两段文字分别贴在两行各自居中，间距接近一整行高。
        """
        if r2 <= r:
            return True
        ws = [w for rr in range(r, r2 + 1) for w in buckets[rr][c]]
        if not ws:
            return True
        row_h = statistics.median([ys[k + 1] - ys[k] for k in range(r, r2 + 1)])
        centers = sorted((w["y"] + w["h"] / 2.0) / scale for w in ws)
        merged_c = []
        for v in centers:
            if merged_c and v - merged_c[-1] < 0.5 * row_h:
                merged_c[-1] = (merged_c[-1] + v) / 2.0
            else:
                merged_c.append(v)
        return all(b - a < 0.85 * row_h for a, b in zip(merged_c, merged_c[1:]))

    for r in range(n_r):
        for c in range(n_c):
            if taken[r][c]:
                continue
            r2 = r
            while r2 + 1 < n_r and not h_ok[r2 + 1][c]:
                r2 += 1
            if not _vmerge_ok(r, r2, c):
                r2 = r
            c2 = c
            while c2 + 1 < n_c and all(not v_ok[rr][c2 + 1] for rr in range(r, r2 + 1)):
                c2 += 1
            txt = _join_words([w for rr in range(r, r2 + 1)
                               for cc in range(c, c2 + 1) for w in buckets[rr][cc]])
            txt, uncertain = _fix_cell(txt)
            for rr in range(r, r2 + 1):
                for cc in range(c, c2 + 1):
                    taken[rr][cc] = True
            cells.append({"r1": r, "c1": c, "r2": r2, "c2": c2,
                          "text": txt, "uncertain": uncertain})

    # 长整数没有小数点 → 十有八九是小数点被 OCR 吞了（如 5018.84 → 501884）。
    # 不知道该补在哪一位，所以不硬猜，只标出来让人核对。
    for cd in cells:
        if re.match(r"^\d{5,}$", cd["text"]):
            cd["uncertain"] = True

    heights = [round((ys[k + 1] - ys[k]) / scale * px2pt, 1) for k in range(n_r)]
    # 列宽照原件：不设的话 Word 平均分配，零件号那种长字段会折行，整表被撑高变形
    px2cm = 21.0 / (page_w_px or 1)
    widths = [round((xs[c + 1] - xs[c]) / scale * px2cm, 2) for c in range(n_c)]
    return {"n_rows": n_r, "n_cols": n_c, "cells": cells,
            "heights": heights, "widths": widths}


# ------------------------------------------------------------ 文字块
def _visual_rows(lines: list) -> list:
    """把 y 方向重叠的 OCR 行合并成「视觉行」。"""
    if not lines:
        return []
    items = sorted(lines, key=lambda l: (l["y"], l["x"]))
    rows = []
    for ln in items:
        placed = False
        for r in rows:
            top = max(r["y"], ln["y"])
            bot = min(r["y"] + r["h"], ln["y"] + ln["h"])
            if bot - top > 0.5 * min(r["h"], ln["h"]):
                r["words"].extend(ln.get("words") or [])
                r["y"] = min(r["y"], ln["y"])
                r["h"] = max(r["h"], ln["h"])
                r["x"] = min(r["x"], ln["x"])
                r["x2"] = max(r["x2"], ln["x"] + ln["w"])
                placed = True
                break
        if not placed:
            rows.append({"y": ln["y"], "h": ln["h"], "x": ln["x"],
                         "x2": ln["x"] + ln["w"],
                         "words": list(ln.get("words") or [])})
    for r in rows:
        r["words"].sort(key=lambda w: w["x"])
    rows.sort(key=lambda r: r["y"])
    return rows


def _ink_ratio(arr, x1, y1, x2, y2, scale: float) -> float:
    """框内墨量（暗像素占比）——用来判加粗，OCR 不给字重。"""
    if scale <= 0:
        return 0.0
    yy1, yy2 = int(y1 / scale), int(y2 / scale)
    xx1, xx2 = int(x1 / scale), int(x2 / scale)
    sub = arr[max(0, yy1):max(1, yy2 + 1), max(0, xx1):max(1, xx2 + 1)]
    if sub.size == 0:
        return 0.0
    return float((sub < 200).mean())


def _analyze_page(ocr: dict, arr, scale: float, keep_table: bool = True) -> dict:
    """一页 OCR + 页面图 → {blocks, body_h, pitch, body_x, page_w, page_h}。"""
    page_w = int(ocr.get("width") or 1)
    page_h = int(ocr.get("height") or 1)
    rows = _visual_rows(ocr.get("lines") or [])
    meta = {"body_h": 0.0, "pitch": 0.0, "body_x": 0, "page_w": page_w, "page_h": page_h}
    if not rows:
        return {"blocks": [], "meta": meta}

    tables = _find_tables(arr) if keep_table else []
    for t in tables:   # 另存一份「原图」坐标，用于和 OCR 行比对
        t["o"] = {"y1": t["y1"] / scale, "y2": t["y2"] / scale,
                  "x1": t["x1"] / scale, "x2": t["x2"] / scale}

    in_table = {}
    for ti, t in enumerate(tables):
        for k, r in enumerate(rows):
            cy = r["y"] + r["h"] / 2.0
            if t["o"]["y1"] - 2 <= cy <= t["o"]["y2"] + 2:
                in_table[k] = ti

    # 页眉页脚边界：顶部/底部附近那条横线
    other = [r for r in _h_rules(arr)
             if not any(t["y1"] - 6 <= r["y1"] <= t["y2"] + 6 for t in tables)]
    hdr_y = min((r["y2"] for r in other if r["y2"] < 0.20 * arr.shape[0]), default=-1)
    ftr_y = max((r["y1"] for r in other if r["y1"] > 0.85 * arr.shape[0]), default=-1)

    body_rows = [rows[k] for k in range(len(rows)) if k not in in_table]
    heights = [r["h"] for r in body_rows if r["h"] > 0]
    body_h = statistics.median(heights) if heights else 20.0
    inks = [_ink_ratio(arr, r["x"], r["y"], r["x2"], r["y"] + r["h"], scale) for r in body_rows]
    inks = [v for v in inks if v > 0]
    body_ink = statistics.median(inks) if inks else 0.0
    body_x = min((r["x"] for r in body_rows), default=0)

    # 正文行距：相邻正文行 y 差的中位数（只取同一段内的，跳过段落间大空隙）
    ys = sorted(r["y"] for r in body_rows if r["h"] <= body_h * 1.15)
    diffs = [b - a for a, b in zip(ys, ys[1:]) if 0 < b - a < body_h * 2.2]
    pitch = statistics.median(diffs) if diffs else body_h * 1.6

    px2pt = _A4_H_PT / page_h
    # 字号标定：**不要**用字形高度（OCR 的 bbox 含上下留白，会偏大 20%，
    # 直接拿它当字号会让行数变多、整篇多出好几页）。用「行宽 ÷ 字数」最准：
    # 中文一个字的宽度就等于字号。
    cw = []
    for r in body_rows:
        t = _join_words(r["words"])
        n = len(t)
        if n >= 15 and r["x2"] > r["x"]:
            cw.append((r["x2"] - r["x"]) * px2pt / n)
    body_size = statistics.median(cw) if cw else body_h * px2pt
    body_size = max(8.0, min(16.0, body_size))
    meta.update({"body_h": body_h, "pitch": pitch, "body_x": body_x,
                 "body_size": round(body_size, 1)})
    # 记下原件的几何位置，后面用来反推页边距（上边距给多了整篇会多出好几页）
    body_ys = [r["y"] for k, r in enumerate(rows) if k not in in_table]
    body_ys += [t["o"]["y1"] for t in tables]
    meta["first_body_y"] = min(body_ys) if body_ys else 0

    # 正文栏宽：所有正文行的 x 跨度，用来判「这行是不是短到可以居中」
    text_x1 = min((r["x"] for r in body_rows), default=0)
    text_x2 = max((r["x2"] for r in body_rows), default=page_w)
    text_w = max(1, text_x2 - text_x1)

    order = []
    for k, r in enumerate(rows):
        if k in in_table:
            continue
        text = _join_words(r["words"])
        if not text:
            continue
        ratio = r["h"] / body_h if body_h else 1.0
        size = max(8.0, min(30.0, body_size * ratio))
        cx = (r["x"] + r["x2"]) / 2.0
        line_w = r["x2"] - r["x"]
        # 居中：中心贴页面中心 **且** 这行明显短于正文栏宽。
        # 只看「中心对齐」会把顶满整行的正文误判成居中（v2 的坑）。
        centered = abs(cx - page_w / 2.0) < 0.03 * page_w and line_w < 0.6 * text_w
        indent = (r["x"] - body_x) > 0.04 * page_w
        ink = _ink_ratio(arr, r["x"], r["y"], r["x2"], r["y"] + r["h"], scale)
        bold = bool(body_ink and ink > body_ink * 1.15)
        # 标题样：短、不以句读收尾；或中文公文常见的「1、」「2）」「（三）」编号开头
        numbered = bool(re.match(r"^[\(（]?[0-9一二三四五六七八九十]{1,3}[、.．\)）]", text)) \
            and len(text) <= 30
        heading_like = numbered or (len(text) <= 14
                                    and not text.endswith(("。", "；", "，", "、")))
        # 「编制：xxx  审核：xxx」这类落款行不是标题，字大是因为上面有手写批注
        signature = bool(re.match(r"^[\(（]?(编制|审核|批准|制表|校对|会签|签发)", text))

        big = ratio >= 1.35
        short = line_w < 0.7 * text_w
        if hdr_y > 0 and r["y"] < hdr_y:
            kind = "header"
        elif ftr_y > 0 and r["y"] > ftr_y:
            kind = "footer"
        elif big and centered:
            # 文档大标题一定是居中的。「编制：xxx 审核：xxx」这类手写批注字大但
            # 不居中，不能当标题（v4 的坑）
            kind = "title"
        elif numbered or (big and not signature) or (ratio >= 1.12 and short) \
                or (bold and heading_like and not signature):
            # 小标题必须**短**：正文行里带数字/括号时 bbox 会高一点，
            # 只看行高比例会把整行正文误判成小标题（加粗 + 居中）
            kind = "subtitle"
        else:
            kind = "body"

        if kind != "title":
            size = min(size, round(body_size * 1.45, 1))
        if signature:
            # 「编制：xxx 审核：xxx」是落款行，原件就是正文字号，不该加粗放大
            kind = "body"
            size = body_size

        order.append((r["y"], 0, {"kind": kind, "text": text, "size": round(size, 1),
                                  "centered": centered, "indent": indent,
                                  "bold": bold if kind != "body" else False}))

    all_words = [w for r in rows for w in r["words"]]
    for ti, t in enumerate(tables):
        grid = _build_grid(all_words, t, arr, scale, px2pt, page_w)
        if grid["n_rows"] < 1 or grid["n_cols"] < 2:
            continue
        order.append((t["o"]["y1"], 1, {"kind": "table", "grid": grid}))

    order.sort(key=lambda o: (o[0], o[1]))
    return {"blocks": [o[2] for o in order], "meta": meta}


# ------------------------------------------------------------ 生成 Word
def _set_font(run, name: str, size_pt: float, bold: bool = False):
    from docx.shared import Pt
    from docx.oxml.ns import qn

    run.font.name = name
    run.font.size = Pt(size_pt)
    run.font.bold = bold
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(rfonts)
    for attr in ("w:eastAsia", "w:ascii", "w:hAnsi"):
        rfonts.set(qn(attr), name)


def _add_border(paragraph, edge: str = "bottom", sz: int = 8):
    """给段落加一条横线（模拟原件里公司抬头下面的分隔线）。"""
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    pPr = paragraph._p.get_or_add_pPr()
    pbdr = pPr.find(qn("w:pBdr"))
    if pbdr is None:
        pbdr = OxmlElement("w:pBdr")
        pPr.append(pbdr)
    el = OxmlElement("w:" + edge)
    el.set(qn("w:val"), "single")
    el.set(qn("w:sz"), str(sz))
    el.set(qn("w:space"), "1")
    el.set(qn("w:color"), "000000")
    pbdr.append(el)


def _fill_header(section, blocks: list):
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    hdr = section.header
    hdr.is_linked_to_previous = False
    for i, b in enumerate(blocks):
        p = hdr.paragraphs[0] if i == 0 else hdr.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.line_spacing = 1.0
        p.paragraph_format.space_after = 0
        run = p.add_run(b["text"])
        _set_font(run, "宋体", b["size"], bold=True)
    if blocks:
        _add_border(hdr.paragraphs[-1], "bottom", 12)


def _fill_footer(section, blocks: list):
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    ftr = section.footer
    ftr.is_linked_to_previous = False
    for i, b in enumerate(blocks):
        p = ftr.paragraphs[0] if i == 0 else ftr.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        p.paragraph_format.line_spacing = 1.0
        p.paragraph_format.space_after = 0
        run = p.add_run(b["text"])
        _set_font(run, "宋体", min(9.0, b["size"]))
    if blocks:
        _add_border(ftr.paragraphs[0], "top", 8)


def _tight_cells(table, top: int = 0, bottom: int = 0, left: int = 40):
    """压缩单元格内边距（单位 dxa，1/20 pt）。

    Word 默认上下各 0.19cm，26 行的明细表光内边距就多出半页；
    原件是紧凑表格，这里把上下内边距清零。
    """
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    tblPr = table._tbl.tblPr
    mar = OxmlElement("w:tblCellMar")
    for edge, val in (("top", top), ("left", left), ("bottom", bottom), ("right", left)):
        el = OxmlElement("w:" + edge)
        el.set(qn("w:w"), str(val))
        el.set(qn("w:type"), "dxa")
        mar.append(el)
    old = tblPr.find(qn("w:tblCellMar"))
    if old is not None:
        tblPr.remove(old)
    tblPr.append(mar)


def _add_table(doc, grid: dict, cell_pt: float = 9.0) -> int:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_ROW_HEIGHT_RULE
    from docx.shared import Pt, Cm

    table = doc.add_table(rows=grid["n_rows"], cols=grid["n_cols"])
    table.style = "Table Grid"
    table.autofit = False
    _tight_cells(table)

    # 列宽照原件
    for ci, w in enumerate(grid.get("widths") or []):
        for cell in table.columns[ci].cells:
            cell.width = Cm(w)

    # 行高照原件（至少这么高，内容多了自己会长）
    for ri, h in enumerate(grid.get("heights") or []):
        if h and h > 6:
            table.rows[ri].height = Pt(h)
            table.rows[ri].height_rule = WD_ROW_HEIGHT_RULE.AT_LEAST
    for cd in grid["cells"]:
        r1, c1, r2, c2 = cd["r1"], cd["c1"], cd["r2"], cd["c2"]
        try:
            cell = table.cell(r1, c1)
            if r2 > r1 or c2 > c1:
                cell = cell.merge(table.cell(r2, c2))
            cell.text = ""
            p = cell.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.line_spacing = 1.0
            p.paragraph_format.space_after = 0
            run = p.add_run(cd["text"])
            _set_font(run, "宋体", cell_pt, bold=(r1 == 0))
        except Exception as e:      # 单个格子失败不影响整张表
            logger.warning("单元格 ({},{})-({},{}) 写入失败：{}".format(r1, c1, r2, c2, e))
    return len(grid["cells"])


def _build_docx(blocks: list, docx_path: str, margins: dict = None,
                header_blocks: list = None, footer_blocks: list = None,
                body_size: float = 12.0, pitch_pt: float = 22.0) -> dict:
    from docx import Document
    from docx.shared import Cm, Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    m = margins or {}
    sec.left_margin = Cm(m.get("left", 1.9))
    sec.right_margin = Cm(m.get("right", 1.9))
    sec.top_margin = Cm(m.get("top", 3.3))
    sec.bottom_margin = Cm(m.get("bottom", 2.3))
    sec.header_distance = Cm(m.get("header", 1.0))
    sec.footer_distance = Cm(m.get("footer", 0.9))

    st = doc.styles["Normal"]
    st.font.name = "宋体"
    st.font.size = Pt(body_size)
    st.paragraph_format.line_spacing = Pt(pitch_pt)
    st.paragraph_format.space_before = Pt(0)
    st.paragraph_format.space_after = Pt(0)

    if header_blocks:
        _fill_header(sec, header_blocks)
    if footer_blocks:
        _fill_footer(sec, footer_blocks)

    n_par = n_tab = n_chars = 0
    for b in blocks:
        if b["kind"] == "pagebreak":
            # 分页符段落压到 1pt：默认它会占一整行，页尾就少放一行正文，
            # 结果最后一句话被挤到下一页单独成页
            from docx.enum.text import WD_BREAK

            p = doc.add_paragraph()
            p.paragraph_format.line_spacing = Pt(1)
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(0)
            run = p.add_run()
            _set_font(run, "宋体", 1)
            run.add_break(WD_BREAK.PAGE)
            continue

        if b["kind"] == "table":
            _add_table(doc, b["grid"], min(9.0, round(body_size * 0.8, 1)))
            n_chars += sum(len(cd["text"]) for cd in b["grid"]["cells"])
            n_tab += 1
            # 表后留一点空隙。**不能**用默认段落（正文行距 23pt，两张表就多占 1.6cm，
            # 整篇会多出一页），这里用一个 10pt 的极窄空段。
            gap = doc.add_paragraph()
            gap.paragraph_format.line_spacing = Pt(4)
            gap.paragraph_format.space_before = Pt(0)
            gap.paragraph_format.space_after = Pt(0)
            _set_font(gap.add_run(""), "宋体", 4)
            continue

        p = doc.add_paragraph()
        if b["kind"] in ("title", "subtitle"):
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER if b["centered"] else WD_ALIGN_PARAGRAPH.LEFT
            run = p.add_run(b["text"])
            _set_font(run, "宋体", b["size"], bold=True)
            # 原件里小标题和正文是同一行距，没有额外段间距；给多了整篇会多出几页
            if b["kind"] == "title":
                p.paragraph_format.line_spacing = Pt(max(pitch_pt, b["size"] * 1.4))
                p.paragraph_format.space_before = Pt(2)
                p.paragraph_format.space_after = Pt(2)
            else:
                p.paragraph_format.line_spacing = Pt(max(pitch_pt, b["size"] * 1.3))
                p.paragraph_format.space_before = Pt(0)
                p.paragraph_format.space_after = Pt(0)
        else:
            p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            if b.get("indent"):
                p.paragraph_format.first_line_indent = Pt(b["size"] * 2)
            run = p.add_run(b["text"])
            _set_font(run, "宋体", b["size"], bold=bool(b.get("bold")))
        n_par += 1
        n_chars += len(b["text"])

    doc.save(docx_path)
    return {"paragraphs": n_par, "tables": n_tab, "chars": n_chars}


# ---------------------------------------------------------------- 入口
def _render(src_pdf: str, work_dir: str) -> list:
    """PDF 每页 → PNG，返回按页序排好的图片路径。"""
    from astronverse.pdf.pdf import PDF
    from astronverse.pdf import PictureType, SelectRangeType, FileExistenceType

    PDF().convert_pdf_to_img(
        file_path=src_pdf, pwd="", image_type=PictureType.PNG,
        select_range=SelectRangeType.ALL, page_range="", save_dir=work_dir,
        prefix="pg", exist_handle_type=FileExistenceType.OVERWRITE)

    def page_no(p):
        base = os.path.splitext(os.path.basename(p))[0]
        digits = "".join(ch for ch in base if ch.isdigit())
        return int(digits) if digits else 0

    files = sorted(glob.glob(os.path.join(work_dir, "pg_*.png")), key=page_no)
    if not files:
        raise RuntimeError("PDF 转图失败：{} 没有产出页面图".format(src_pdf))
    return files


def _ocr_raw(img_path: str, ps1: str, lang: str, tag: str) -> dict:
    """对一张图跑一次 Windows OCR，返回原始 JSON（坐标是这张图的像素）。"""
    out_json = os.path.splitext(img_path)[0] + "." + tag + ".json"
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1,
           "-ImagePath", img_path, "-OutJson", out_json, "-LangTag", lang]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if not os.path.exists(out_json):
        raise RuntimeError("OCR 无输出：{}".format(
            (proc.stderr or proc.stdout or "空")[:200]))
    with open(out_json, "r", encoding="utf-8") as f:
        return json.load(f)


def _strip_bounds(arr, scale: float, H: int, strip_px: int = 900,
                  search: int = 160) -> list:
    """把页面切成若干横条，切点落在**空白行**上（该行暗像素最少）。

    切在文字行上会把一行字劈成两半；条带重叠又会让同一行被识别两次、文字翻倍。
    在空白行处切、且不重叠，两个问题一起解决。
    """
    import numpy as np

    dark = (arr < 200).sum(axis=1)
    n = int(dark.size)
    bounds, y = [], 0
    while y < H - 60:
        target = y + strip_px
        if target >= H - 60:
            bounds.append((y, H))
            break
        lo, hi = int(target - search / 2), int(target + search / 2)
        lo, hi = max(y + 200, lo), min(H - 1, hi)
        if hi <= lo:
            bounds.append((y, H))
            break
        r0, r1 = max(0, int(lo * scale)), min(n, int(hi * scale))
        if r1 <= r0:
            bounds.append((y, H))
            break
        best = int(np.argmin(dark[r0:r1])) + r0
        cut = max(lo, min(hi, int(round(best / scale))))
        bounds.append((y, cut))
        y = cut
    return bounds or [(0, H)]


def _dedupe_lines(lines: list) -> list:
    """按几何重叠去重：同一处文字只留读得最全的那条。"""
    out = []
    for ln in sorted(lines, key=lambda l: -len(l.get("text") or "")):
        dup = False
        for o in out:
            ov = min(o["y"] + o["h"], ln["y"] + ln["h"]) - max(o["y"], ln["y"])
            if ov <= 0.5 * min(o["h"] or 1, ln["h"] or 1):
                continue
            ox = min(o["x"] + o["w"], ln["x"] + ln["w"]) - max(o["x"], ln["x"])
            if ox > 0.5 * min(o["w"] or 1, ln["w"] or 1):
                dup = True
                break
        if not dup:
            out.append(ln)
    return out


def _ocr(img_path: str, ps1: str, lang: str, work_dir: str, upscale: int = 2,
         arr=None, scale: float = 1.0) -> dict:
    """分条带放大后 OCR，再把坐标拼回整页。

    为什么要放大：实测 1 倍时「5018.84」被识别成「501 & 84」，2 倍就对了。
    为什么要切条带：整页放大 2 倍是 4574×6472 ≈ 3000 万像素，Windows OCR 会
    直接静默失败（进程无输出、无报错）。切成 900px 高的条带，每条带只有 800 万
    像素，稳定得多，精度还保住了。
    """
    from PIL import Image

    im = Image.open(img_path)
    W, H = im.size
    k = float(max(1, upscale))
    if arr is None:
        arr, scale = _load_gray(img_path)
    bands = _strip_bounds(arr, scale, H, strip_px=900)

    lines = []
    for bi, (y1, y2) in enumerate(bands):
        crop = im.crop((0, y1, W, y2))
        if k > 1:
            crop = crop.resize((int(crop.width * k), int(crop.height * k)), Image.LANCZOS)
        part = os.path.join(work_dir, "{}_b{}.png".format(
            os.path.splitext(os.path.basename(img_path))[0], bi))
        crop.save(part)
        d = _ocr_raw(part, ps1, lang, "b%d" % bi)
        for ln in d.get("lines") or []:
            ln["y"] = int(round(ln.get("y", 0) / k)) + y1
            ln["x"] = int(round(ln.get("x", 0) / k))
            ln["w"] = int(round(ln.get("w", 0) / k))
            ln["h"] = int(round(ln.get("h", 0) / k))
            for wd in ln.get("words") or []:
                wd["x"] = int(round(wd.get("x", 0) / k))
                wd["y"] = int(round(wd.get("y", 0) / k)) + y1
                wd["w"] = int(round(wd.get("w", 0) / k))
                wd["h"] = int(round(wd.get("h", 0) / k))
            lines.append(ln)

    lines = _dedupe_lines(lines)
    lines.sort(key=lambda l: (l["y"], l["x"]))
    logger.info("OCR：{} 条带，{} 行".format(len(bands), len(lines)))
    # 批量场景下这个函数会被调用几十次：不主动放掉整页位图，内存会一路涨到 MemoryError
    try:
        im.close()
    except Exception:
        pass
    return {"image": img_path, "lang": lang, "width": W, "height": H, "lines": lines}


def _clear_target(path: str) -> None:
    """目标文件存在就先删（`create` 的 overwrite 在文件被占用时会失败）。"""
    if not path or not os.path.exists(path):
        return
    try:
        os.remove(path)
        logger.info("已删除同名旧文件：{}".format(path))
    except PermissionError:
        raise RuntimeError("目标 Word 正在被占用，请先关闭再运行：{}".format(path))
    except OSError as e:
        raise RuntimeError("删除同名旧文件失败（{}）：{}".format(e, path))


def _margins(ocr: dict, meta: dict) -> dict:
    """用第 1 页的几何位置反推页边距（cm），让重建后的版心与原件一致。"""
    page_w = int(ocr.get("width") or 0)
    if not page_w:
        return {}
    x1 = meta.get("body_x") or int(0.09 * page_w)
    px_cm = 21.0 / page_w
    left = max(1.0, min(4.5, x1 * px_cm))
    # 上边距：原件的第一个正文块（标题或表格）在哪就从哪开始。
    # 这里给多了，正文整体下移，一页少放一行，整篇就会多出好几页。
    top = 3.3
    fy = meta.get("first_body_y") or 0
    if fy:
        top = max(2.0, min(4.5, fy * px_cm))
    return {"left": round(left, 2), "right": round(max(1.0, left * 0.9), 2),
            "top": round(top, 2), "bottom": 2.3, "header": 1.0, "footer": 0.9}


def main(args) -> Any:
    src_pdf = (args.get("src_pdf") or "").strip()
    out_dir = (args.get("out_dir") or "").strip()
    out_name = (args.get("out_name") or "").strip()
    if not out_name:
        # 批量调用时不给 out_name：按源文件名推导，避免每个文件都覆盖成同一个名字
        stem = os.path.splitext(os.path.basename(src_pdf))[0] if src_pdf else ""
        out_name = (stem + "_版面还原") if stem else "版面还原结果"
    ps1 = (args.get("ps1") or "").strip()
    lang = (args.get("lang") or "").strip() or "zh-Hans-CN"
    keep_table = str(args.get("keep_table", "true")).strip().lower() not in ("false", "0", "no")
    try:
        ocr_scale = max(1, int(args.get("ocr_scale") or 2))
    except (TypeError, ValueError):
        ocr_scale = 2

    if not src_pdf or not os.path.exists(src_pdf):
        raise RuntimeError("源 PDF 不存在：{}".format(src_pdf))
    if not out_dir or not os.path.isdir(out_dir):
        raise RuntimeError("输出目录不存在：{}".format(out_dir))
    if not ps1 or not os.path.exists(ps1):
        raise RuntimeError("找不到 OCR 脚本 win_ocr.ps1：{}".format(ps1))

    docx_path = os.path.join(out_dir, out_name + ".docx")
    _clear_target(docx_path)

    work = tempfile.mkdtemp(prefix="pdf_layout_")
    logger.info("工作目录：{}".format(work))

    pages = _render(src_pdf, work)
    logger.info("已渲染 {} 页".format(len(pages)))

    blocks, header_blocks, footer_blocks, uncertain = [], [], [], []
    first_ocr, first_meta = None, None
    ti_global = 0
    for i, img in enumerate(pages, 1):
        arr, scale = _load_gray(img)
        d = _ocr(img, ps1, lang, work, ocr_scale, arr=arr, scale=scale)
        res = _analyze_page(d, arr, scale, keep_table=keep_table)
        bs = res["blocks"]
        if i == 1:
            first_ocr, first_meta = d, res["meta"]
        body = []
        for b in bs:
            if b["kind"] == "header":
                if i == 1:
                    header_blocks.append(b)
            elif b["kind"] == "footer":
                if i == 1:
                    footer_blocks.append(b)
            else:
                if b["kind"] == "table":
                    ti_global += 1
                    for cd in b["grid"]["cells"]:
                        if cd.get("uncertain"):
                            uncertain.append({"table": ti_global, "page": i,
                                              "cell": "R{}C{}".format(cd["r1"] + 1, cd["c1"] + 1),
                                              "text": cd["text"]})
                body.append(b)
        blocks.extend(body)
        kinds = {}
        for b in body:
            kinds[b["kind"]] = kinds.get(b["kind"], 0) + 1
        logger.info("第 {} 页：{} 个正文块 {}".format(i, len(body), kinds))
        if i < len(pages):
            blocks.append({"kind": "pagebreak"})
        # 每页收尾放掉大数组：批量跑几十个文件时不回收会累积到 MemoryError
        del arr, d, res
        gc.collect()

    m = _margins(first_ocr or {}, first_meta or {})
    px2pt = _A4_H_PT / max(1, int((first_ocr or {}).get("height") or 3236))
    pitch_pt = round((first_meta or {}).get("pitch", 0) * px2pt, 1) or 22.0
    body_size = (first_meta or {}).get("body_size") or 12.0

    stat = _build_docx(blocks, docx_path, margins=m,
                       header_blocks=header_blocks, footer_blocks=footer_blocks,
                       body_size=body_size, pitch_pt=max(pitch_pt, body_size * 1.15))
    logger.info("已生成：{}（{} 段 / {} 表 / {} 字；正文 {}pt / 行距 {}pt；页眉 {} 行 / 页脚 {} 行）".format(
        docx_path, stat["paragraphs"], stat["tables"], stat["chars"],
        body_size, pitch_pt, len(header_blocks), len(footer_blocks)))

    if uncertain:
        logger.warning("有 {} 个表格单元格识别不确定，建议核对：{}".format(
            len(uncertain), "；".join("表{} {}「{}」".format(u["table"], u["cell"], u["text"])
                                     for u in uncertain[:8])))

    return {"ok": True, "docx": docx_path, "pages": len(pages),
            "paragraphs": stat["paragraphs"], "tables": stat["tables"],
            "chars": stat["chars"], "body_size": body_size,
            "line_pitch": pitch_pt, "margins": m,
            "uncertain_cells": uncertain}
