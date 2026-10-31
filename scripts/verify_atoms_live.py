# -*- coding: utf-8 -*-
"""
星辰开源 · 原子真机预演（verify_atoms_live.py）
==============================================
不经过 RPA 调度器，直接用**设计器自带的 python_core** 调用原子函数，验证
「原子 + 我们生成的参数」在真实机器上能否跑通。

为什么需要它（2026-10-30 实战教训）：
  `validate()` 只能校验 schema 层面的合法性（参数是否齐全、枚举是否在选项内），
  但**校验不出实现层行为**，例如：
    - `Excel.create_excel` 内部会 `file_name += ".xlsx"` → 传 "导出结果.xlsx" 会变成双扩展名；
    - `Excel.save_excel` 在 `save_type=save` 时**忽略** file_path/file_name；
    - `Excel.read_excel` 的 `read_range` 是枚举，传 "A1:Z1000" 会走到 NotImplementedError。
  这些都只有真跑一次才会暴露。

用法（必须用客户端的 python，不是本机任意 python）：
  "%APPDATA%/astron-rpa/python_core/python.exe" scripts/verify_atoms_live.py --flow excel
  "%APPDATA%/astron-rpa/python_core/python.exe" scripts/verify_atoms_live.py --flow file
  ... --flow all

注意：引擎运行前会把字符串参数转成枚举（params._recursive_convert_params），
直接调函数需要自己传枚举成员，否则字符串 != 枚举成员。
"""
import argparse
import os
import sys

DEMO = r"F:\AI-Workbench\10-Projects\星辰开源\demo"
SRC_XLSX = os.path.join(DEMO, "订单.xlsx")
SRC_TXT = os.path.join(DEMO, "源文件.txt")
OUT_XLSX_NAME = "导出结果"          # create_excel 会自己补 .xlsx
OUT_TXT = os.path.join(DEMO, "目标文件.txt")


def check_excel():
    from astronverse.excel.excel import Excel
    from astronverse.excel import ReadRangeType, SaveType, FileExistenceType, ApplicationType

    print("[excel] 1/4 open_excel")
    src = Excel.open_excel(file_path=SRC_XLSX, default_application=ApplicationType.EXCEL,
                           visible_flag=False, password="", update_links=False)

    print("[excel] 2/4 read_excel(read_range=ALL)")
    data = Excel.read_excel(excel=src, sheet_name="Sheet1", read_range=ReadRangeType.ALL,
                            start_col="", end_col="", cell="", row=1, column="",
                            start_row=1, end_row=1, read_display=True,
                            trim_spaces=True, replace_none=True)
    print(f"        读到 {len(data)} 行，首行={data[0] if data else None}")

    print("[excel] 3/4 create_excel")
    dst, dst_path = Excel.create_excel(file_path=DEMO, file_name=OUT_XLSX_NAME,
                                       default_application=ApplicationType.EXCEL,
                                       visible_flag=False,
                                       exist_handle_type=FileExistenceType.OVERWRITE,
                                       password="")
    print(f"        目标={dst_path} 已生成={os.path.exists(dst_path)}")

    print("[excel] 4/4 save_excel")
    Excel.save_excel(excel=dst, save_type=SaveType.SAVE, file_path="", file_name="",
                     exist_handle_type=FileExistenceType.OVERWRITE, close_flag=True)
    try:
        Excel.close_excel(excel=src, save_type_one=SaveType.ABORT)
    except Exception:
        pass
    return os.path.exists(dst_path) and len(data) > 0


def check_file():
    from astronverse.system.file import File
    from astronverse.system import ReadType, EncodeType, StateType, WriteType

    print("[file] 1/2 file_read")
    txt = File.file_read(file_path=SRC_TXT, read_type=ReadType.ALL, encode_type=EncodeType.DEFAULT)
    print(f"        读到 {len(txt)} 字符")

    print("[file] 2/2 file_write")
    if os.path.exists(OUT_TXT):
        os.remove(OUT_TXT)
    File.file_write(file_path=OUT_TXT, file_option=StateType.CREATE, msg=txt,
                    write_type=WriteType.OVERWRITE, encode_type=EncodeType.UTF8)
    ok = os.path.exists(OUT_TXT) and open(OUT_TXT, encoding="utf-8").read() == txt
    print(f"        写出={OUT_TXT} 回读一致={ok}")
    return ok


def main():
    ap = argparse.ArgumentParser(description="原子真机预演（用设计器 python_core）")
    ap.add_argument("--flow", default="all", choices=["excel", "file", "all"])
    args = ap.parse_args()

    if not os.path.isdir(DEMO):
        print(f"✗ demo 目录不存在：{DEMO}（先生成测试数据）")
        return 2

    results = {}
    if args.flow in ("excel", "all"):
        try:
            results["excel"] = check_excel()
        except Exception as e:
            print(f"✗ excel 失败：{type(e).__name__}: {e}")
            results["excel"] = False
    if args.flow in ("file", "all"):
        try:
            results["file"] = check_file()
        except Exception as e:
            print(f"✗ file 失败：{type(e).__name__}: {e}")
            results["file"] = False

    print("\n=== 结果 ===")
    for k, v in results.items():
        print(f"  {k:6s} {'✅ 通过' if v else '❌ 失败'}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
