# -*- coding: utf-8 -*-
"""A9 · IE 系原子整块缺失（字典/前端有，发布包不带模块）

字典里 `IeBrowser*` 一共 35~36 个原子，设计器面板上能正常拖出来；但发布包
（`%APPDATA%/astron-rpa/python_core/Lib/site-packages/astronverse/`）里根本没有
`iebrowser` 这个包，流程跑到那一行就是 `ModuleNotFoundError: No module named
'astronverse.iebrowser'`。

也就是说：**要么一起发、要么一起不发**。现在发的是「字典 + 前端」，没发实现。

本脚本三件事：
  1. `import astronverse.iebrowser` 看能不能导入
  2. 在 site-packages 下搜有没有 iebrowser 目录
  3. 列出同目录下其它存在的域包作为对照（证明不是安装路径找错了）

运行：
    "%APPDATA%/astron-rpa/python_core/python.exe" docs/repro/A9_iebrowser_missing.py
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

CONTROL_MODULES = ["excel", "word", "pdf", "network", "browser", "datatable"]


def run():
    import astronverse

    # astronverse 是命名空间包（没有 __file__），路径从 __path__ 取
    pkg_dir = str(list(astronverse.__path__)[0])
    print(f"astronverse 包目录：{pkg_dir}")

    # 1. 直接导入
    ie_err = None
    try:
        importlib.import_module("astronverse.iebrowser")
        print("import astronverse.iebrowser -> 成功")
    except Exception as e:  # noqa: BLE001
        ie_err = fail_detail(e)
        print(f"import astronverse.iebrowser -> 失败：{ie_err}")

    # 2. 目录层面确认
    ie_dir = os.path.join(pkg_dir, "iebrowser")
    print(f"存在 {ie_dir} 目录？ {os.path.isdir(ie_dir)}")
    hits = [n for n in os.listdir(pkg_dir) if "ie" in n.lower()]
    print(f"包目录下名字含 'ie' 的条目：{hits or '（无）'}")

    # 3. 对照组：其它域包都在
    ctrl = {}
    for m in CONTROL_MODULES:
        try:
            importlib.import_module(f"astronverse.{m}")
            ctrl[m] = True
        except Exception:  # noqa: BLE001
            ctrl[m] = False
    print(f"对照组导入结果：{ {k: ('✔' if v else '✘') for k, v in ctrl.items()} }")
    ctrl_ok = any(ctrl.values())

    if ie_err is None:
        return report(
            "A9",
            "IE 系原子整块缺失",
            "字典里出现的 IeBrowser* 原子，其实现模块应能导入",
            "astronverse.iebrowser 能正常导入",
            NOT_REPRODUCED,
            "本版本已把 IE 模块打进来，可关闭该条。",
        )

    if not ctrl_ok:
        return report(
            "A9",
            "IE 系原子整块缺失",
            "至少对照组里的域包能导入，证明是在正确的 site-packages 上检查",
            f"对照组也全部导入失败，说明解释器/包路径不对：{ {k: ctrl[k] for k in ctrl} }",
            "BLOCKED",
            "请用设计器自带 python_core 运行本脚本。",
        )

    return report(
        "A9",
        "IE 系原子整块缺失（字典 35+ 个 key，实现包不存在）",
        "字典里出现的每个原子都能找到实现；若暂不支持 IE，就不该在字典/前端里暴露这些原子",
        f"`import astronverse.iebrowser` 报 {ie_err}；包目录下没有 iebrowser，"
        f"名字含 ie 的条目={hits or '无'}；对照组 {sum(ctrl.values())}/{len(ctrl)} 个域包正常",
        REPRODUCED,
        "建议二选一：① 发布包里带上 iebrowser；② 在字典里给 IeBrowser* 打 unsupported 标记、"
        "前端隐藏，避免用户/AI 生成了跑不了的流程。"
        "（本机也确实没装 IE，所以无法验证功能本身，只能确认「缺模块」这一层。）",
    )


main_guard(run)
