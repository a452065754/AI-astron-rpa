# -*- coding: utf-8 -*-
"""
mod_pdf_ocr —— PDF 页面图 → 可编辑文字（本地 OCR）

为什么需要它：
    平台有 `OpenApi.common_ocr` 原子，但它走讯飞云接口，本部署
    XFYUN_API_KEY/SECRET/APP_ID 为空 → 恒 503。Windows 自带 OCR
    可以离线、免费、无密钥地识别中文，但没有对应原子，所以放在
    Python 子流程里，由 `Script.module` 原子调用。

输入 args：
    img_dir  PDF 页面图所在目录（PDF.convert_pdf_to_img 的产出）
    txt_out  合并后的文本输出路径（后续用 File.file_read + Docx 原子建 Word）
    ps1      win_ocr.ps1 的路径
    lang     OCR 语言，默认 zh-Hans-CN
    pattern  页面图通配，默认 *.png

输出：
    写入 txt_out，返回 {"pages": n, "lines": m, "chars": c}
"""
from typing import Any

import glob
import json
import os
import re
import subprocess
import tempfile

from astronverse.workflowlib import logger

# 中日韩字符 + 中文标点
_CJK = r"\u3000-\u303f\u4e00-\u9fff\uff01-\uff60"
_SPACE_BETWEEN_CJK = re.compile(r"(?<=[%s])[ ]+(?=[%s])" % (_CJK, _CJK))

# OCR 常把小数点/百分号识别成全角或近似符号，这里按「数字上下文」纠正
_DECIMAL_NOISE = re.compile(r"(?<=\d)\s*[·．。，,]\s*(?=\d)")
_PERCENT_NOISE = re.compile(r"[％﹪]")
_FULLWIDTH_DIGIT = re.compile(r"[０-９]")


def _clean(text: str) -> str:
    """清洗 OCR 噪声：
    1) 去掉汉字之间的空格（Windows OCR 逐字加空格）
    2) 数字之间被识别成 ·．。，, 的，还原成小数点
    3) 全角百分号 → 半角
    4) 全角数字 → 半角
    """
    text = text.strip()
    prev = None
    while prev != text:
        prev = text
        text = _SPACE_BETWEEN_CJK.sub("", text)
    text = _FULLWIDTH_DIGIT.sub(lambda m: chr(ord(m.group(0)) - 0xFEE0), text)
    text = _PERCENT_NOISE.sub("%", text)
    text = _DECIMAL_NOISE.sub(".", text)
    return text


def _ocr_page(ps1: str, image: str, out_json: str, lang: str) -> dict:
    cmd = [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1,
        "-ImagePath", image, "-OutJson", out_json, "-LangTag", lang,
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if p.returncode != 0:
        raise RuntimeError("OCR 失败 {}: {} {}".format(image, (p.stdout or "")[-300:], (p.stderr or "")[-300:]))
    with open(out_json, "r", encoding="utf-8") as f:
        return json.load(f)


def _group_rows(lines: list) -> list:
    """把 OCR 行按 y 坐标聚成「行」，行内按 x 排序 —— 还原阅读顺序。"""
    items = sorted(lines, key=lambda l: (l.get("y", 0), l.get("x", 0)))
    rows = []
    for it in items:
        h = it.get("h", 20) or 20
        y = it.get("y", 0)
        placed = False
        for row in rows:
            tol = max(12, int(min(row["h"], h) * 0.6))
            if abs(y - row["y"]) <= tol:
                row["items"].append(it)
                row["y"] = min(row["y"], y)
                row["h"] = max(row["h"], h)
                placed = True
                break
        if not placed:
            rows.append({"y": y, "h": h, "items": [it]})
    rows.sort(key=lambda r: r["y"])
    for r in rows:
        r["items"].sort(key=lambda i: i.get("x", 0))
    return rows


def _clear_target(docx_path: str) -> None:
    """删掉同名旧 Word，并把它「删不掉」这件事翻译成人话。

    平台 `Docx.create_docx(exist_handle_type=overwrite)` 是坏的：内部
    `handle_existence` 的 `os.remove` 被注释掉了，直接 SaveAs 到已存在的
    路径，Word 抛 COM 异常，又被吞成「Word 文档读取失败，请检查文件是否损坏」
    —— 这句提示会把人带到完全错误的方向（坑 68）。所以这里提前删，并且
    把「文件被占用」明确说出来。
    """
    if not docx_path or not os.path.exists(docx_path):
        return
    try:
        os.remove(docx_path)
        logger.info("已删除同名旧文件：{}".format(docx_path))
    except PermissionError:
        raise RuntimeError(
            "目标 Word 正在被占用，请先关闭再运行：{}".format(docx_path)
        )
    except OSError as e:
        raise RuntimeError("删除同名旧文件失败（{}）：{}".format(docx_path, e))


def main(args) -> Any:
    img_dir = (args.get("img_dir") or "").strip()
    txt_out = (args.get("txt_out") or "").strip()
    ps1 = (args.get("ps1") or "").strip()
    lang = (args.get("lang") or "zh-Hans-CN").strip()
    pattern = (args.get("pattern") or "*.png").strip()
    docx_path = (args.get("docx_path") or "").strip()

    _clear_target(docx_path)

    for name, val in (("img_dir", img_dir), ("txt_out", txt_out), ("ps1", ps1)):
        if not val:
            raise ValueError("参数 {} 不能为空".format(name))
    if not os.path.isdir(img_dir):
        raise ValueError("img_dir 不存在：{}".format(img_dir))
    if not os.path.isfile(ps1):
        raise ValueError("win_ocr.ps1 不存在：{}".format(ps1))

    pages = sorted(glob.glob(os.path.join(img_dir, pattern)))
    if not pages:
        raise ValueError("{} 下没有匹配 {} 的图片".format(img_dir, pattern))
    logger.info("OCR 待处理页数：{}".format(len(pages)))

    out_lines = []
    total = 0
    tmp_dir = tempfile.mkdtemp(prefix="rpa_ocr_")
    try:
        for idx, image in enumerate(pages, 1):
            data = _ocr_page(ps1, image, os.path.join(tmp_dir, "p{}.json".format(idx)), lang)
            if idx > 1:
                out_lines.append("")          # 页间空行
            out_lines.append("【第 {} 页】".format(idx))
            for row in _group_rows(data.get("lines") or []):
                cells = [_clean(c.get("text", "")) for c in row["items"]]
                cells = [c for c in cells if c]
                if not cells:
                    continue
                out_lines.append("\t".join(cells))
                total += 1
            logger.info("第 {} 页完成，行数 {}".format(idx, len(out_lines)))
    finally:
        for f in glob.glob(os.path.join(tmp_dir, "*")):
            try:
                os.remove(f)
            except OSError:
                pass
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass

    os.makedirs(os.path.dirname(txt_out) or ".", exist_ok=True)
    text = "\n".join(out_lines)
    with open(txt_out, "w", encoding="utf-8") as f:
        f.write(text)

    logger.info("OCR 完成：{} 页 / {} 行 / {} 字 -> {}".format(len(pages), total, len(text), txt_out))
    return {"pages": len(pages), "lines": total, "chars": len(text)}


if __name__ == "__main__":
    print(main({
        "img_dir": r"F:\AI-Workbench\10-Projects\星辰开源\demo\pdf_pages",
        "txt_out": r"F:\AI-Workbench\10-Projects\星辰开源\demo\_ocr_text.txt",
        "ps1": r"F:\AI-Workbench\10-Projects\星辰开源\scripts\win_ocr.ps1",
        "lang": "zh-Hans-CN",
    }))
