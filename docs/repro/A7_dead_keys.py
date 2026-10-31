# -*- coding: utf-8 -*-
"""A7 · 字典里有、实现里没有的「死 key」

原子字典（服务端 `c_atom_meta_new`）声明了这些原子，设计器能拖出来、AI 生成的
流程里也能编译，但**引擎的 Python 实现类里没有对应方法**，跑到那一行就是
AttributeError / 编译期就炸。

本机已确认 3 个（v1.1.11）：
    DataTable.sort_table          ← 类里 16 个方法没有这个
    Docx.open_document            ← 现行实现叫 open_docx
    Docx.read_document_content    ← 现行实现叫 read_docx

本脚本只做一件事：**反射**——按 key 去实现类里找同名方法，找不到就报。
不依赖任何本地缓存，也不需要连服务端。字典侧证据请对照随包的
`_dict_evidence.json`（从 `c_atom_meta_new` 导出的这几个 key 的条目）。

运行：
    "%APPDATA%/astron-rpa/python_core/python.exe" docs/repro/A7_dead_keys.py
"""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import (  # noqa: E402
    NOT_REPRODUCED,
    REPRODUCED,
    fail_detail,
    main_guard,
    report,
)

# key -> (模块, 类名, 方法名)
DEAD_KEYS = {
    "DataTable.sort_table": ("astronverse.datatable.datatable", "DataTable", "sort_table"),
    "Docx.open_document": ("astronverse.word.docx", "Docx", "open_document"),
    "Docx.read_document_content": ("astronverse.word.docx", "Docx", "read_document_content"),
}

# 作为对照：这几个是「现行实现里真实存在」的同模块方法，用来证明反射本身是有效的
CONTROLS = {
    "DataTable.filter_data_table": ("astronverse.datatable.datatable", "DataTable", "filter_data_table"),
    "Docx.open_docx": ("astronverse.word.docx", "Docx", "open_docx"),
    "Docx.read_docx": ("astronverse.word.docx", "Docx", "read_docx"),
}


def probe(table):
    out = []
    for key, (mod_name, cls_name, meth) in table.items():
        try:
            mod = importlib.import_module(mod_name)
            cls = getattr(mod, cls_name)
            has = hasattr(cls, meth)
            out.append((key, has, ""))
        except Exception as e:  # noqa: BLE001
            out.append((key, None, fail_detail(e)))
    return out


def run():
    print("对照组（应当存在，用来验证反射方法本身没错）：")
    ctrl = probe(CONTROLS)
    for key, has, err in ctrl:
        print(f"  {key:34s} {'✔ 存在' if has else '✘ 找不到 ' + err}")
    ctrl_ok = all(h for _, h, _ in ctrl)

    if not ctrl_ok:
        return report(
            "A7",
            "字典里有、实现里没有的死 key",
            "对照组（现行方法名）应当全部存在",
            f"对照组本身就找不全：{ctrl}",
            "BLOCKED",
            "反射路径和当前版本不符，请人工核对类与方法名。",
        )

    print("\n待验原子（字典里声明了，设计器能拖出来）：")
    res = probe(DEAD_KEYS)
    for key, has, err in res:
        print(f"  {key:34s} {'✔ 存在（不算缺陷）' if has else '✘ 实现里没有'}")

    missing = [(k, e) for k, h, e in res if h is False]
    detail = "；".join(f"{k} → 实现类无此方法" for k, _ in missing) or "全部存在"

    if not missing:
        return report(
            "A7",
            "字典里有、实现里没有的死 key",
            "字典里声明的每个原子都能在实现类里找到同名方法",
            f"待验的 3 个 key 现在都存在（{detail}）",
            NOT_REPRODUCED,
            "本版本已补齐或已从字典移除，可关闭该条。",
        )

    return report(
        "A7",
        f"字典声明但实现缺失（{len(missing)} 个死 key）",
        "原子字典里出现的每个 key，引擎实现类里都应有同名方法；"
        "要么补实现，要么从字典里删掉，不能留「能拖出来但跑不了」的原子",
        f"{detail}。作为对照，同模块的现行方法（"
        + "、".join(k for k, h, _ in ctrl if h)
        + "）都能正常反查到，说明不是反射调用方式的问题",
        REPRODUCED,
        "建议：字典侧把 `DataTable.sort_table` 删掉或补实现；`Docx.open_document` / "
        "`Docx.read_document_content` 是改名残留（现行 `open_docx` / `read_docx`），"
        "建议在字典里加 deprecated 标记并在前端隐藏，否则 AI 生成流程时会持续踩坑"
        "（本机 183 条真实流程里这 3 个 key 命中 0 次，说明人工也不会用它们）。",
    )


main_guard(run)
