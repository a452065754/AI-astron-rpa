# -*- coding: utf-8 -*-
"""A3 · 生成类原子的「覆盖已存在文件」—— 分两组对照，别一概而论

⚠️ 本条原结论「目标文件存在就必失败」**已被本脚本推翻**，实际分成两种场景：

  组1  目标文件已存在、但没有任何程序打开它（= 上一轮运行的残留）
       → Word SaveAs 能正常覆盖，**行为正确**。
  组2  同一个 Word 实例已经打开过这个路径，再 SaveAs 到同一路径
       （= 一条流程里连续两次生成同名文档，最常见的无人值守场景）
       → 失败，且错误被**包装成「Word文件读取失败，请检查文件是否损坏！」**

根因在 `astronverse/word/core.py:79-84`：
    if exist_type == FileExistenceType.OVERWRITE:
        # os.remove(file_path)      ← 被注释掉了
        return file_path
删文件那一步被注释，于是「覆盖」实际退化成「直接 SaveAs 到已占用路径」；
而 `docx.py:188-192` 的 except 又把真实 COM 错误吞成一句误导文案。

运行：
    "%APPDATA%/astron-rpa/python_core/python.exe" docs/repro/A3_overwrite_existing.py
"""
import os
import sys

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

NAME = "每日报告"


def _quit_word():
    """尽力关掉引擎复用的 Word 实例，避免复现结束后残留进程。"""
    try:
        from astronverse.word.docx import WordDocumentCore

        inst = getattr(WordDocumentCore, "word_application_instance", None)
        if inst is not None:
            inst.Quit()
            WordDocumentCore.word_application_instance = None
    except Exception:  # noqa: BLE001
        pass


def run():
    from docx import Document

    from astronverse.word import ApplicationType, FileExistenceType
    from astronverse.word.docx import Docx

    work = tmpdir("A3", str(os.getpid()))  # 每次独立目录：残留 Word 进程会锁住旧文件
    target = os.path.join(work, NAME + ".docx")

    # 用 python-docx 造一个「上一轮残留」的有效文档（不占用 Word 实例）
    d = Document()
    d.add_paragraph("这是上一轮运行留下的旧内容，本应被覆盖。")
    d.save(target)
    print(f"预备：残留文件 {target}（{os.path.getsize(target)} 字节，未被打开）")

    # ---------- 组1：文件存在但未被占用 ----------
    g1_ok, g1_err = False, None
    try:
        _doc1, p1 = Docx.create_docx(
            file_path=work,
            file_name=NAME,
            default_application=ApplicationType.DEFAULT,
            visible_flag=False,
            exist_handle_type=FileExistenceType.OVERWRITE,
        )
        g1_ok = os.path.exists(p1)
        print(f"组1 残留文件覆盖 -> {'成功，' + str(os.path.getsize(p1)) + ' 字节' if g1_ok else '返回路径不存在'}")
    except Exception as e:  # noqa: BLE001
        g1_err = fail_detail(e)
        print(f"组1 残留文件覆盖 -> 异常：{g1_err}")

    # ---------- 组2：同一实例已打开该路径，再覆盖 ----------
    g2_ok, g2_err = False, None
    try:
        _doc2, _p2 = Docx.create_docx(
            file_path=work,
            file_name=NAME,
            default_application=ApplicationType.DEFAULT,
            visible_flag=False,
            exist_handle_type=FileExistenceType.OVERWRITE,
        )
        g2_ok = True
        print("组2 同实例重复覆盖 -> 成功（本版本已修）")
    except Exception as e:  # noqa: BLE001
        g2_err = fail_detail(e)
        print(f"组2 同实例重复覆盖 -> 异常：{g2_err}")

    # ---------- 源码事实：OVERWRITE 分支不删文件 ----------
    try:
        from astronverse.word.core import IDocumentCore

        probe = os.path.join(work, "残留探针.docx")
        Document().save(probe)
        before = os.path.exists(probe)
        ret = IDocumentCore.handle_existence(probe, FileExistenceType.OVERWRITE)
        still_there = os.path.exists(probe)
        print(
            f"handle_existence(已存在文件, OVERWRITE) -> 返回 {ret!r}；"
            f"调用前存在={before}，调用后仍存在={still_there}（应为 False）"
        )
    except Exception as e:  # noqa: BLE001
        still_there = None
        print(f"handle_existence 探测失败：{fail_detail(e)}")

    _quit_word()

    actual = []
    actual.append("组1（残留文件、无人占用）：" + ("覆盖成功，行为正确" if g1_ok else f"失败 —— {g1_err}"))
    actual.append("组2（同实例重复生成同名）：" + ("成功" if g2_ok else f"失败 —— {g2_err}"))
    if still_there is not None:
        actual.append(f"handle_existence(OVERWRITE) 调用后文件{'仍存在（未删除）' if still_there else '已被删除'}")
    actual_txt = "；".join(actual)

    if g1_err:
        return report(
            "A3",
            "生成类原子覆盖已存在文件",
            "组1、组2 都能覆盖同名文件",
            actual_txt,
            BLOCKED,
            "组1（连无人占用的残留文件都覆盖不了）说明当前环境的 Word/WPS 与本文差异较大，请按实际结果记录。",
        )

    if g2_ok:
        return report(
            "A3",
            "生成类原子覆盖已存在文件",
            "组1、组2 都能覆盖同名文件",
            actual_txt,
            NOT_REPRODUCED,
            "本版本已能在同一实例内覆盖同名文档，可关闭该条。",
        )

    return report(
        "A3",
        "同一流程内重复生成同名文档 → 报「文件损坏」（错误文案误导）",
        "一条流程里连续两次用 exist_handle_type=OVERWRITE 生成同名文档，第二次应覆盖第一次，"
        "产出可正常打开的新文档",
        actual_txt,
        REPRODUCED,
        "注意边界：**文件存在本身不构成失败**（组1 就成功了），只有「文件正被当前 Word 实例打开」时才失败。"
        "所以这不是 P0 阻断项，而是两处小问题叠加："
        "(1) `word/core.py:83` 的 `os.remove(file_path)` 被注释掉，覆盖前不释放占用；"
        "(2) `word/docx.py:188-192` 把真实 COM 错误吞成「请检查文件是否损坏」，"
        "用户会去查文件本身，而真因是文件被占用。建议放开 os.remove，并把原始异常链带上。",
    )


main_guard(run)
