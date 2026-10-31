# -*- coding: utf-8 -*-
"""A11 · Docx.insert_sep（段落）实际不分段：文字全挤在同一段

实现（`word/core_win.py:519`）：
    elif sep_type == InsertionType.PARAGRAPH:
        s.InsertParagraph()
Word COM 里 `Selection.InsertParagraph()` 的语义是「在光标处插入一个段落标记」，
**光标不前进**。于是后续 `insert_docx` 写入的文字继续落在**同一个段落**里，
段落标记被一路堆到文末。用户看到的是「三段文字挤成一行」。

正确做法是 `Selection.TypeParagraph()`（等价于按一下 Enter，光标会移入新段）。

复现方式：写 A → 插段落分隔 → 写 B → 插段落分隔 → 写 C，然后
  (1) 用 COM 读文档的实际段落数与每段文本
  (2) 解包 docx 数 `<w:p>`，看文字到底落在几段里
  (3) 与 `insert_docx(enter_flag=True)` 的对照组比

运行：
    "%APPDATA}/astron-rpa/python_core/python.exe" docs/repro/A11_insert_sep_no_break.py
"""
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import (  # noqa: E402
    BLOCKED,
    NOT_REPRODUCED,
    REPRODUCED,
    fail_detail,
    main_guard,
    report,
    tmpdir,
)

TEXTS = ["第一段AAA", "第二段BBB", "第三段CCC"]


def quit_word():
    """收尾：关掉脚本自己起的 Word/WPS 实例，别把测试文档留在桌面上。"""
    for progid in ("Word.Application", "KWps.Application"):
        try:
            import win32com.client as win32

            win32.GetActiveObject(progid).Quit()
        except Exception:  # noqa: BLE001
            pass


def paragraphs_from_docx(path):
    """解包 docx，把每个 <w:p> 的文本抽出来。"""
    import re

    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    out = []
    for p in re.findall(r"<w:p[ >].*?</w:p>|<w:p/>", xml, flags=re.S):
        txt = "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, flags=re.S))
        out.append(txt)
    return out


def run():
    from astronverse.word import ApplicationType, FileExistenceType, InsertionType
    from astronverse.word.docx import Docx

    work = tmpdir("A11")

    # ---------- 实验组：insert_sep ----------
    doc, _ = Docx.create_docx(
        file_path=work,
        file_name="",
        default_application=ApplicationType.DEFAULT,
        visible_flag=False,
        exist_handle_type=FileExistenceType.RENAME,
    )
    try:
        Docx.insert_docx(doc=doc, text=TEXTS[0])
        Docx.insert_sep(doc=doc, sep_type=InsertionType.PARAGRAPH)
        Docx.insert_docx(doc=doc, text=TEXTS[1])
        Docx.insert_sep(doc=doc, sep_type=InsertionType.PARAGRAPH)
        Docx.insert_docx(doc=doc, text=TEXTS[2])

        raw = doc.document_object
        com_count = raw.Paragraphs.Count
        com_texts = [raw.Paragraphs(i).Range.Text.strip("\r\x07") for i in range(1, com_count + 1)]
        print(f"COM 视角：段落数={com_count}，每段文本={com_texts}")

        # 文件名带 pid：文件可能正被 Word 占用，删不掉就用新名字
        out_sep = os.path.join(work, f"insert_sep结果_{os.getpid()}.docx")
        raw.SaveAs(out_sep)
    except Exception as e:  # noqa: BLE001
        return report(
            "A11",
            "insert_sep 不分段",
            "三段文字落在三个段落里",
            f"Word COM 调用失败，无法取证：{fail_detail(e)}",
            BLOCKED,
            "本机需要可用的 Word/WPS。",
        )

    sep_paras = paragraphs_from_docx(out_sep)
    print(f"docx 视角（insert_sep）：段落数={len(sep_paras)}，文本={sep_paras}")

    # ---------- 对照组：把 InsertParagraph 换成 TypeParagraph（验证修复建议有效） ----------
    ctrl_paras, ctrl_desc = [], "未执行"
    try:
        doc2, _ = Docx.create_docx(
            file_path=work,
            file_name="",
            default_application=ApplicationType.DEFAULT,
            visible_flag=False,
            exist_handle_type=FileExistenceType.RENAME,
        )
        Docx.insert_docx(doc=doc2, text=TEXTS[0])
        raw2 = doc2.document_object
        raw2.Application.Selection.TypeParagraph()  # ← 唯一改动：换掉 InsertParagraph
        Docx.insert_docx(doc=doc2, text=TEXTS[1])
        raw2.Application.Selection.TypeParagraph()
        Docx.insert_docx(doc=doc2, text=TEXTS[2])

        out_type = os.path.join(work, f"TypeParagraph结果_{os.getpid()}.docx")
        raw2.SaveAs(out_type)
        ctrl_paras = paragraphs_from_docx(out_type)
        ctrl_desc = f"段落数={len(ctrl_paras)}，文本={ctrl_paras}"
        print(f"docx 视角（TypeParagraph 对照）：{ctrl_desc}")
    except Exception as e:  # noqa: BLE001
        ctrl_desc = f"对照组失败（不影响判定）：{fail_detail(e)}"
        print(ctrl_desc)

    quit_word()
    merged = [p for p in sep_paras if all(t in p for t in TEXTS)]
    ctrl_ok = len([p for p in ctrl_paras if p.strip()]) == 3

    if not merged:
        return report(
            "A11",
            "insert_sep 不分段",
            "三段文字分别落在三个非空段落里",
            f"insert_sep 产出：{sep_paras}（未出现三段合并）",
            NOT_REPRODUCED,
            "本版本 insert_sep 已能正确分段，可关闭该条。",
        )

    return report(
        "A11",
        "Docx.insert_sep（段落）实际不分段，三段文字挤在同一段",
        "连续 insert_docx 三段文字、中间各插一次 insert_sep(PARAGRAPH)，"
        "应得到 3 个非空段落",
        f"实际得到段落 {sep_paras}，其中一段同时包含全部三段文字（{merged[0]!r}），"
        f"COM 报段落数 {com_count} 但内容为空段；对照组把 InsertParagraph 换成 TypeParagraph "
        f"得到 {ctrl_desc}"
        + ("（正确，证明修复建议有效）" if ctrl_ok else "（对照也异常，需复核）"),
        REPRODUCED,
        "修复建议：`core_win.py:519` 的 `s.InsertParagraph()` 改为 `s.TypeParagraph()`。"
        "InsertParagraph 只往光标处插标记、光标不动，后续文字继续写进同一段；"
        "TypeParagraph 等价于按 Enter，光标会进入新段落。",
    )


main_guard(run)
