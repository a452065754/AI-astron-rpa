# -*- coding: utf-8 -*-
"""A2 · PDF.convert_img_to_pdf —— 任何入参形式都必失败

根因：`astronverse/pdf/__init__.py` 的 `_handle_files_input()` **通篇没有 return**。
    函数体只有 if / elif 三支，每支都只做「把 files_path 重新赋值」，然后函数结束 → 返回 None。
    调用点 `pdf/pdf.py:579`：image_files = _handle_files_input(image_files)
                            → PDFCore.images_to_pdf(None, ...) → 图片列表为空 → 直接抛错。

复现手法：三种入参形态（list / 单条 str / `"[a.png,b.png]"` 字面量字符串）全跑一遍，
再单独调一次 `_handle_files_input` 把返回值打出来（就是 None），根因即闭环。

运行：
    "%APPDATA%/astron-rpa/python_core/python.exe" docs/repro/A2_convert_img_to_pdf.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import (  # noqa: E402
    NOT_REPRODUCED,
    REPRODUCED,
    fail_detail,
    main_guard,
    report,
    tmpdir,
)


def make_pngs(folder):
    """造两张 200x120 的纯色 PNG 作为输入（不依赖外部素材）。"""
    from PIL import Image

    paths = []
    for i, color in enumerate([(220, 40, 40), (40, 120, 220)], start=1):
        p = os.path.join(folder, f"输入图{i}.png")
        Image.new("RGB", (200, 120), color).save(p)
        paths.append(p)
    return paths


def run():
    from astronverse.pdf import FileExistenceType, ImageLayoutType
    from astronverse.pdf import _handle_files_input
    from astronverse.pdf.pdf import PDF

    work = tmpdir("A2")
    out_dir = tmpdir("A2", "out")
    imgs = make_pngs(work)
    print("输入图片：")
    for p in imgs:
        print(f"  {p}  ({os.path.getsize(p)} 字节)")

    # ---- 根因直击：helper 的返回值 ----
    probe = _handle_files_input(list(imgs))
    print(f"\n_handle_files_input([两路径]) 返回值 = {probe!r}  →  type={type(probe).__name__}")

    # 带引号的 Python 字面量（literal_eval 能过），确认「转换成功也不返回」
    quoted = repr(list(imgs))
    try:
        probe_q = _handle_files_input(quoted)
        print(f"_handle_files_input({quoted[:40]}...) 返回值 = {probe_q!r}")
    except Exception as e:  # noqa: BLE001
        probe_q = "EXC"
        print(f"_handle_files_input(带引号字面量) 异常：{fail_detail(e)}")

    # 不带引号的 [a,b]：前端 file_type=files 最常见的形态，看是否直接炸
    try:
        _handle_files_input("[" + ",".join(imgs) + "]")
        bare_err = None
    except Exception as e:  # noqa: BLE001
        bare_err = fail_detail(e)
        print(f"_handle_files_input([无引号路径,...]) 异常：{bare_err}")

    # ---- 三种入参全跑 ----
    forms = [
        ("list 形态", list(imgs)),
        ("单条 str", imgs[0]),
        ("带引号字面量 '['a','b']'", repr(list(imgs))),
    ]
    ok, errs = [], []
    for label, val in forms:
        try:
            r = PDF.convert_img_to_pdf(
                image_files=val,
                save_dir=out_dir,
                new_file_name=f"A2_{len(ok) + len(errs)}",
                layout_type=ImageLayoutType.SINGLE_PAGE,
                exist_handle_type=FileExistenceType.RENAME,
            )
            ok.append((label, r))
        except Exception as e:  # noqa: BLE001
            errs.append((label, fail_detail(e)))

    print("")
    for label, msg in errs:
        print(f"  {label:22s} -> 异常：{msg}")
    for label, r in ok:
        print(f"  {label:22s} -> 返回值 {r!r}")

    if len(ok) == len(forms):
        return report(
            "A2",
            "PDF.convert_img_to_pdf 必失败",
            "三种入参都能生成 PDF",
            "三种入参全部成功 → 该版本已修",
            NOT_REPRODUCED,
            "本版本已修复，可关闭该条。",
        )

    if probe is not None:
        return report(
            "A2",
            "PDF.convert_img_to_pdf 必失败",
            "`_handle_files_input` 返回规整后的路径列表",
            f"helper 现在返回 {probe!r}（已有 return），但仍有入参形态失败",
            NOT_REPRODUCED,
            f"根因貌似已修，残余失败：{[e for _, e in errs]}",
        )

    note = (
        "修复建议：在 `astronverse/pdf/__init__.py` 的 `_handle_files_input` 末尾补 "
        "`return files_path`（三支都落到同一出口）。该函数 12 行里一条 return 都没有。"
    )
    if bare_err:
        note += (
            f" 附带发现：传入无引号的 `[路径1,路径2]`（前端 files 选择器最自然的输出形态）时，"
            f"`ast.literal_eval` 先炸在 {bare_err}，建议改为按逗号切分或容忍无引号。"
        )

    return report(
        "A2",
        "PDF.convert_img_to_pdf 必失败（_handle_files_input 缺 return）",
        "传入两张 PNG、指定保存目录，生成一个至少 1 页的 PDF，并返回该 PDF 的绝对路径",
        f"{len(errs)}/{len(forms)} 种入参形态直接抛异常（见上）；"
        f"`_handle_files_input` 对 list 与带引号字面量两种形态均返回 None",
        REPRODUCED,
        note,
    )


main_guard(run)
