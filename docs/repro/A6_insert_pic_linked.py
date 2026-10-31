# -*- coding: utf-8 -*-
"""A6 · Excel.insert_pic 插进去的是「链接图」，不是嵌入图

底层实现（`excel/core_win/worksheet.py:259`）：
    picture = worksheet.Pictures().Insert(image_path)
`Pictures().Insert` 在 Excel COM 里等于「插入文件链接」——图片数据**不进工作簿**，
只写一个指向本机绝对路径的外部引用。后果：
  · 把 xlsx 发给别人 / 换台机器 / 源图被清理 → 图片全部显示为空框或裂图
  · 上传到服务端、走邮件附件，同样丢图

对照组用同一个 Excel 实例、同一张图，改调
    Shapes.AddPicture(path, LinkToFile=False, SaveWithDocument=True)
产出的工作簿里就有 `xl/media/image1.png`。

判定方式：直接解包 xlsx 看结构，不靠肉眼。
  · 缺陷特征：`xl/media/` 不存在或为空；`xl/drawings/drawing1.xml` 里是 `<a:blip r:link=...>`，
    且 `xl/drawings/_rels/drawing1.xml.rels` 里是 `TargetMode="External"` + 本机绝对路径
  · 正确特征：`xl/media/` 下有图片二进制；blip 用 `r:embed` 指向包内关系

运行：
    "%APPDATA%/astron-rpa/python_core/python.exe" docs/repro/A6_insert_pic_linked.py
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


def make_png(path):
    from PIL import Image

    Image.new("RGB", (320, 180), (30, 144, 255)).save(path)


def office_running(prefix):
    """本机是否已有该类 Office 进程（用来判断该不该在收尾时关掉应用）。"""
    try:
        import psutil

        return any(
            (p.info.get("name") or "").lower().startswith(prefix) for p in psutil.process_iter(["name"])
        )
    except Exception:  # noqa: BLE001
        return True  # 判不出来就当有，宁可不关


def quit_excel_if_ours(started_by_us):
    if not started_by_us:
        return
    try:
        import win32com.client as win32

        app = win32.GetActiveObject("Excel.Application")
        app.Quit()
    except Exception:  # noqa: BLE001
        pass


def inspect_xlsx(path):
    """解包看图片是嵌入还是外链。"""
    info = {"media": [], "blip_link": 0, "blip_embed": 0, "external_rels": []}
    with zipfile.ZipFile(path) as z:
        info["media"] = [n for n in z.namelist() if n.startswith("xl/media/")]
        for n in z.namelist():
            if n.startswith("xl/drawings/") and n.endswith(".xml") and "_rels" not in n:
                x = z.read(n).decode("utf-8", "replace")
                info["blip_link"] += x.count("r:link=")
                info["blip_embed"] += x.count("r:embed=")
            if n.startswith("xl/drawings/_rels/") and n.endswith(".rels"):
                x = z.read(n).decode("utf-8", "replace")
                if 'TargetMode="External"' in x:
                    for seg in x.split("<Relationship"):
                        if 'TargetMode="External"' in seg:
                            info["external_rels"].append(seg.split('Target="')[-1].split('"')[0])
    return info


def run():
    from openpyxl import Workbook

    from astronverse.excel import ApplicationType, FileExistenceType, ImageSizeType, SaveType
    from astronverse.excel.excel import Excel

    work = tmpdir("A6", str(os.getpid()))  # 每次独立目录：残留 Excel 进程会锁住旧文件
    excel_started_by_us = not office_running("excel")
    png = os.path.join(work, "待插入图.png")
    make_png(png)

    # 空白工作簿
    book = os.path.join(work, "插图测试.xlsx")
    wb = Workbook()
    wb.active.title = "Sheet1"
    wb.save(book)

    excel = Excel.open_excel(file_path=book, default_application=ApplicationType.EXCEL)
    try:
        Excel.insert_pic(
            excel=excel,
            sheet_name="Sheet1",
            insert_pos="B2",
            pic_path=png,
            pic_size_type=ImageSizeType.AUTO,
        )
        out_atom = os.path.join(work, "原子插图结果.xlsx")
        if os.path.exists(out_atom):
            os.remove(out_atom)
        Excel.save_excel(
            excel=excel,
            file_path=work,
            file_name="原子插图结果",
            save_type=SaveType.SAVE,
            exist_handle_type=FileExistenceType.OVERWRITE,
        )
    except Exception as e:  # noqa: BLE001
        return report(
            "A6",
            "Excel.insert_pic 插入链接图",
            "插图后保存，xlsx 内自带图片二进制",
            f"原子调用失败，无法取到证据：{fail_detail(e)}",
            BLOCKED,
            "需要本机 Excel/WPS 可用。",
        )
    finally:
        try:
            Excel.close_excel(excel=excel, save=False)
        except Exception:  # noqa: BLE001
            pass
        quit_excel_if_ours(excel_started_by_us)

    # save_type=SAVE 时会忽略 file_path/file_name，结果仍写回原文件
    candidates = [out_atom, book]
    atom_file = next((p for p in candidates if os.path.exists(p)), None)
    if atom_file is None:
        return report(
            "A6",
            "Excel.insert_pic 插入链接图",
            "保存后得到一个可解包的 xlsx",
            "没找到保存结果，无法取证",
            BLOCKED,
        )

    a = inspect_xlsx(atom_file)
    print(f"原子产出：{atom_file}")
    print(f"  xl/media 内容      = {a['media'] or '（空）'}")
    print(f"  drawing 里 r:link   = {a['blip_link']} 处")
    print(f"  drawing 里 r:embed  = {a['blip_embed']} 处")
    print(f"  外链目标            = {a['external_rels']}")

    # ---------- 对照组：Shapes.AddPicture(LinkToFile=False, SaveWithDocument=True) ----------
    ctrl_ok, ctrl_desc = False, "未执行"
    try:
        import win32com.client as win32

        book2 = os.path.join(work, "对照嵌入结果.xlsx")
        if os.path.exists(book2):
            os.remove(book2)
        app = None
        try:
            app = win32.DispatchEx("Excel.Application")
            app.Visible = False
            app.DisplayAlerts = False
            wb2 = app.Workbooks.Add()
            ws2 = wb2.Worksheets(1)
            ws2.Shapes.AddPicture(png, False, True, 10, 10, 320, 180)
            wb2.SaveAs(book2)
            wb2.Close(SaveChanges=False)
        finally:
            if app is not None:
                app.Quit()
        c = inspect_xlsx(book2)
        ctrl_ok = bool(c["media"])
        ctrl_desc = f"xl/media={c['media']}，r:embed={c['blip_embed']} 处，r:link={c['blip_link']} 处"
        print(f"对照组产出：{book2}")
        print(f"  {ctrl_desc}")
    except Exception as e:  # noqa: BLE001
        ctrl_desc = f"对照组执行失败（不影响判定）：{fail_detail(e)}"
        print(f"  {ctrl_desc}")

    linked = (not a["media"]) and (a["blip_link"] > 0 or a["external_rels"])

    if not linked:
        return report(
            "A6",
            "Excel.insert_pic 插入链接图",
            "xl/media 下有图片二进制，drawing 用 r:embed 指向包内",
            f"xl/media={a['media']}，r:link={a['blip_link']}，r:embed={a['blip_embed']}",
            NOT_REPRODUCED,
            "本版本的 insert_pic 已经改成嵌入方式，可关闭该条。",
        )

    return report(
        "A6",
        "Excel.insert_pic 插入的是外部链接图（工作簿不带图）",
        "插完图保存，xlsx 自己带图片数据（xl/media/ 下有图片，drawing 用 r:embed），"
        "换机器/转发/删掉源图后图片仍然显示",
        f"原子产出里 xl/media 为{'空' if not a['media'] else a['media']}，"
        f"drawing 用 r:link 外链（{a['blip_link']} 处），外链目标指向本机路径 {a['external_rels']}；"
        f"对照组 Shapes.AddPicture 产出 {ctrl_desc}",
        REPRODUCED,
        "修复建议：把 `worksheet.py:259` 的 `worksheet.Pictures().Insert(path)` 换成 "
        "`worksheet.Shapes.AddPicture(path, LinkToFile=False, SaveWithDocument=True, ...)`，"
        "两者签名不同，尺寸/位置需要跟着调整。",
    )


main_guard(run)
