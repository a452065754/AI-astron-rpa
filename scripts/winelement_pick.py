#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""桌面元素自动拾取器（UIA / 窗口 / 图像）—— 无需人工点选。

## 解决什么问题

`Window.*` 类原子要 `WinPick`，`WinEle.*` 类原子要 UIA 元素，`CV.*` 类原子要图像元素。
设计器里这三类都得**人工用鼠标点**，AI 到这一步就停。本脚本把「人工点」
降维成「给一个控件描述」，然后用**引擎自己的拾取代码**生成元素数据。

## 原理（全部来自本机逆向实证）

1. UIA 元素数据由 `astronverse/picker/engines/uia_picker.py` 的
   `UIAElement(control).path()` 生成 —— 从目标控件往上走到窗口/桌面，
   每层收 `tag_name/cls/name/value/index + checked + disable_keys`，
   结构 `{version, type:"uia", app, path:[...], img:{self}}`。
   本脚本直接调用它，因此产出的数据与设计器人工拾取**完全同构**。

2. 运行时定位走 `astronverse/locator/locator.py` → `uia_locator.UIAFactory.find`：
   先按 `path[0]` 的 cls/name/app 枚举窗口句柄，再逐层比对
   `tag_name/cls/name/value`（`disable_keys` 里的跳过），`index` 是弱匹配。
   所以**第一层必须是窗口**，中间的层级不能想当然省掉。

3. 落库走官方接口 `POST /api/robot/element/create`（复用 element_pick 的实现），
   元素按 robotId 隔离，必须与将来运行流程的机器人一致。

## 用法

    # 0. 先看清窗口里有什么（只读）
    python winelement_pick.py --title "星辰RPA测试" --list

    # 1. 只生成不落库，并且**反查自证**（推荐先跑）
    python winelement_pick.py --title "星辰RPA测试" --cls RichEditD2DPT --probe

    # 2. 真正写进元素库
    python winelement_pick.py --title "星辰RPA测试" --cls RichEditD2DPT \
        --element-name "记事本_文本编辑器" --robot-id 2115990391806255104

    # 3. 校验已有元素还能不能定位到（只读）
    python winelement_pick.py --check 1234567890 --robot-id ...

## 自证口径

`--probe` 打印两件事：
  1. 生成的 path（每层属性）；
  2. **把这份数据喂给运行时定位器 `locator.locator()`，看它定位回来的控件
     与原始控件是否同一个**（比对 RuntimeId / 类名 / 矩形）。
「生成成功」不算成功，定位回同一个控件才算。
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import rpa_config as cfg  # noqa: E402

DEFAULT_ROBOT_ID = "2115990391806255104"


def _auto():
    import uiautomation

    return uiautomation


# --------------------------------------------------------------------------- #
# 找窗口 / 找控件
# --------------------------------------------------------------------------- #
def top_windows(auto):
    root = auto.GetRootControl()
    return list(root.GetChildren())


def find_window(auto, title):
    """按标题子串找顶层窗口。优先 WindowControl，兼容 PaneControl 型窗口。"""
    cands = [c for c in top_windows(auto) if title in (c.Name or "")]
    if not cands:
        return None
    cands.sort(key=lambda c: 0 if c.ControlTypeName == "WindowControl" else 1)
    return cands[0]


def walk(control, depth, level=0):
    """深度优先遍历，产出 (控件, 层深)。"""
    yield control, level
    if level >= depth:
        return
    try:
        children = control.GetChildren()
    except Exception:
        return
    for ch in children:
        yield from walk(ch, depth, level + 1)


def match(control, cls=None, name=None, ctype=None, auto_id=None):
    if cls and (control.ClassName or "") != cls:
        return False
    if name and name not in (control.Name or ""):
        return False
    if ctype and (control.ControlTypeName or "") != ctype:
        return False
    if auto_id and (control.AutomationId or "") != auto_id:
        return False
    return True


def find_controls(root, depth, **kw):
    hits = []
    for ctrl, level in walk(root, depth):
        if ctrl is root:
            continue
        if match(ctrl, **kw):
            hits.append((ctrl, level))
    return hits


# --------------------------------------------------------------------------- #
# 生成元素数据 + 自证
# --------------------------------------------------------------------------- #
class _StubSvc:
    """`path()` 末尾会读 `svc.data["pick_type"]` 判断是不是「相似元素」拾取。

    这里不是交互式拾取，给个空 data，让 pick_type 为空 → 走普通单元素分支。
    """

    data = {}


def build_element_data(control):
    """用引擎自己的拾取代码生成 elementData（与设计器人工拾取同构）。"""
    from astronverse.picker.engines.uia_picker import UIAElement

    return UIAElement(control=control).path(strategy_svc=_StubSvc())


def verify_locator(element_data, origin_control):
    """把生成的数据交给运行时定位器，看能否定位回**同一个控件**。"""
    from astronverse.locator.locator import locator

    data = dict(element_data)
    data.pop("img", None)  # 定位不需要截图，去掉更快
    res = locator.locator(data)
    got = res.control() if res is not None else None
    if got is None:
        return False, "定位器返回空"

    def rid(c):
        try:
            return c.GetRuntimeId()
        except Exception:
            return None

    same = rid(got) == rid(origin_control) and rid(got) is not None
    detail = (
        f"定位到 {got.ControlTypeName} name={got.Name!r} cls={got.ClassName!r} "
        f"rect={tuple(int(v) for v in (got.BoundingRectangle.left, got.BoundingRectangle.top, got.BoundingRectangle.right, got.BoundingRectangle.bottom))}"
    )
    if not same:
        detail += " | 与目标不是同一个控件（RuntimeId 不同）"
    return same, detail


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--spawn",
        metavar="EXE",
        help="先启动这个程序再拾取。GUI 进程从脚本/工具里启动后往往随父进程一起被清理，"
        "而元素拾取又必须有活窗口，所以把「启动 + 拾取 + 自证」放在同一个进程里做",
    )
    ap.add_argument("--spawn-args", default="", help="--spawn 时传给程序的命令行参数")
    ap.add_argument("--spawn-wait", type=float, default=3.0, help="--spawn 后等几秒再找窗口")
    ap.add_argument("--title", help="窗口标题子串")
    ap.add_argument("--list", action="store_true", help="只列出窗口内控件（只读）")
    ap.add_argument("--depth", type=int, default=6)
    ap.add_argument("--cls", help="类名，如 RichEditD2DPT")
    ap.add_argument("--name", help="控件名（子串匹配），如 文本编辑器")
    ap.add_argument("--type", dest="ctype", help="控件类型，如 DocumentControl")
    ap.add_argument("--auto-id", help="AutomationId，如 AddButton")
    ap.add_argument("--nth", type=int, default=0, help="命中多个时取第几个（默认 0）")
    ap.add_argument(
        "--window-self",
        action="store_true",
        help="拾取窗口本身（给 Window.* 原子用）。path 只会有窗口一层，"
        "因为 WindowsCore.find 只读 path[0] 的 name/cls",
    )
    ap.add_argument("--probe", action="store_true", help="只生成 + 自证，不写库")
    ap.add_argument("--element-name", help="写库时的元素名（--probe 省略时必填）")
    ap.add_argument("--robot-id", default=DEFAULT_ROBOT_ID)
    ap.add_argument("--group", default="AI桌面元素")
    ap.add_argument("--no-img", action="store_true", help="生成时不带截图（减小体积）")
    ap.add_argument("--check", metavar="ELEMENT_ID", help="只校验已有元素能否定位（只读）")
    args = ap.parse_args()

    auto = _auto()

    # ---- 校验已有元素 ----
    if args.check:
        import element_pick as EP

        ed = EP.fetch_element_data(args.robot_id, args.check)
        if not ed:
            print(f"元素 {args.check} 取不到 elementData")
            return 1
        print(f"元素 {args.check}：type={ed.get('type')} app={ed.get('app')!r} path {len(ed.get('path') or [])} 层")
        if ed.get("type") == "uia" and len(ed.get("path") or []) == 1:
            # 窗口元素：Window.* 原子走 FindWindow，不归 UIA 管
            from astronverse.actionlib.types import WinPick
            from astronverse.window.core_win import WindowsCore

            try:
                h = WindowsCore.find(WinPick({"elementData": ed}))
                import win32gui

                print(f"  ✓ WindowsCore.find -> 句柄={h} 标题={win32gui.GetWindowText(h)!r}")
                return 0
            except Exception as e:  # noqa: BLE001
                print(f"  ✗ WindowsCore.find 失败：{e}")
                return 1
        from astronverse.locator.locator import locator

        pure = dict(ed)
        pure.pop("img", None)
        loc = locator.locator(pure)
        ctrl = loc.control() if loc is not None else None
        if ctrl is None:
            print("  ✗ 定位失败：返回空")
            return 1
        r = ctrl.BoundingRectangle
        print(f"  ✓ 定位到 {ctrl.ControlTypeName} name={ctrl.Name!r} cls={ctrl.ClassName!r} rect=({r.left},{r.top},{r.right},{r.bottom})")
        return 0

    if args.spawn:
        import subprocess
        import time

        cmd = [args.spawn] + ([args.spawn_args] if args.spawn_args else [])
        proc = subprocess.Popen(cmd)
        print(f"已启动 {cmd}（pid={proc.pid}），等 {args.spawn_wait}s 让窗口出来")
        time.sleep(args.spawn_wait)

    if not args.title:
        ap.error("必须给 --title（窗口标题子串）")
    win = find_window(auto, args.title)
    if win is None:
        print(f"没找到标题含 {args.title!r} 的窗口；先跑 --list 或 uia_dump.py --windows")
        return 2
    print(f"窗口：{win.Name!r} cls={win.ClassName!r} pid={win.ProcessId}")

    if args.list:
        hits = find_controls(win, args.depth)
        print(f"控件 {len(hits)} 个（层深 ≤ {args.depth}）：")
        for i, (c, level) in enumerate(hits):
            nm = (c.Name or "").replace("\n", " ")[:36]
            print(
                f"  [{i:>3}] {'  ' * level}{c.ControlTypeName:<20} name={nm!r:<40} "
                f"cls={c.ClassName!r:<26} auto={c.AutomationId!r}"
            )
        return 0

    if args.window_self:
        ctrl = win
    else:
        if not (args.cls or args.name or args.ctype or args.auto_id):
            ap.error("要么 --list / --window-self，要么给 --cls/--name/--type/--auto-id 指定控件")

        hits = find_controls(win, args.depth, cls=args.cls, name=args.name, ctype=args.ctype, auto_id=args.auto_id)
        if not hits:
            print("没命中任何控件；先用 --list 看层级和属性")
            return 2
        if len(hits) > 1:
            print(f"命中 {len(hits)} 个，取第 {args.nth} 个：")
            for i, (c, _l) in enumerate(hits[:10]):
                print(f"  [{i}] {c.ControlTypeName} name={c.Name!r} cls={c.ClassName!r}")
        ctrl, _ = hits[min(args.nth, len(hits) - 1)]

    data = build_element_data(ctrl)
    if args.no_img:
        data.pop("img", None)
    print(f"\n目标控件：{ctrl.ControlTypeName} name={ctrl.Name!r} cls={ctrl.ClassName!r}")
    print(f"app={data.get('app')!r}  path {len(data.get('path', []))} 层：")
    for i, node in enumerate(data.get("path", [])):
        print(
            f"  {i}. {node.get('tag_name')!r:<22} name={(node.get('name') or '')[:28]!r:<30} "
            f"cls={node.get('cls')!r:<26} index={node.get('index')} disabled={node.get('disable_keys')}"
        )

    if args.window_self:
        # Window.* 原子不走 UIA locator，而是 win32gui.FindWindow(cls, name)：
        # 只读 path[0] 的 name/cls。所以对窗口元素，UIA 自证不适用（路径只有窗口一层，
        # UIAFactory 需要能枚举到窗口控件的父级），直接验 WindowsCore.find。
        from astronverse.actionlib.types import WinPick
        from astronverse.window.core_win import WindowsCore

        ok = True
        try:
            h = WindowsCore.find(WinPick({"elementData": data}))
            import win32gui

            title = win32gui.GetWindowText(h)
            same_win = h == int(win.NativeWindowHandle)
            print(f"自证（WindowsCore.find，Window.* 原子的真实入口）：")
            print(f"  句柄={h} 标题={title!r} {'✓ 就是目标窗口' if same_win else '✗ 不是目标窗口'}")
            ok = same_win
        except Exception as e:  # noqa: BLE001
            print(f"自证（WindowsCore.find）失败：{e}")
            ok = False
    else:
        ok, detail = verify_locator(data, ctrl)
        print(f"\n自证（喂给运行时定位器）：{'✓ 定位回同一个控件' if ok else '✗ 失败'}")
        print(f"  {detail}")

    if args.probe:
        out = os.path.join("demo", "_gui_out", "element_probe.json")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        print(f"\n（--probe：未写库。elementData 已存 {out}）")
        return 0 if ok else 1

    if not args.element_name:
        ap.error("写库必须给 --element-name")
    if not ok:
        print("\n✗ 自证没过，拒绝写库（写进去也用不了）")
        return 1

    import element_pick as EP

    eid = EP.create_element(args.robot_id, args.group, args.element_name, data)
    print(f"\n✓ 已写元素库：{args.element_name} -> id={eid}（robot={args.robot_id} group={args.group}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
