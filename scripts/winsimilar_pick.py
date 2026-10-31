#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""相似元素（`picker_type=SIMILAR`）自动拾取器 —— 给 `WinEle.similar` 原子造元素。

## 解决什么问题

`WinEle.similar`（获取相似元素列表）是 WinEle 域最后一个没覆盖的原子。
它的元素**不是普通 UIA 元素**，必须带 `picker_type: "SIMILAR"`，
而这种元素在设计器里**只能靠人工「拾取相似元素」**得到：先点第一个元素、
再点第二个相似的元素，引擎才肯生成。

## 机制（引擎源码实证）

    picker/engines/uia_picker.py:390   pick_type == PickerType.SIMILAR 时
                                        → get_similar_path(strategy_svc, res)
    picker/engines/uia_picker.py:483   逐层比对「旧元素 path」与「新元素 path」：
                                        相同的层标 similar_parent=True（父级链），
                                        第一个不同的层作为「相似子元素模板」
                                        （只留 tag_name，其余 key 全部 disable）
    locator/core/uia_locator.py:196    __find_similar__ 先拿 similar_parent 那几层
                                        去 __find_one__ 找父节点，再在父节点的孩子里
                                        找符合模板的 → 返回列表

两个必须满足的前提（踩过才知道）：

1. **旧元素的 path 是「根 → 叶」顺序**（`path()` 里 `path_list.reverse()` 过），
   `path[0]` 是顶层窗口。
2. **相似元素的父节点必须至少两层**（窗口 + 容器）。
   因为 `uia_locator.py:390` 的判定是 `i == (len(node_list) - 2)`：
   path 只有一层时 `len(node_list)-2 == -1`，而 `i` 初值是 0 → 恒不成立
   → 必然抛「元素无法找到」。所以「窗口 → 容器 → 元素」三层是最低要求
   （资源管理器：窗口→列表视图→列表项；浏览器：窗口→文档→链接，都满足）。

## 用法

    # 只生成 + 自证（不写库）
    python winsimilar_pick.py --title "星辰相似元素靶场" --first "项目一" --second "项目二" --probe

    # 写进元素库
    python winsimilar_pick.py --title "星辰相似元素靶场" --first "项目一" --second "项目二" \
        --element-name "靶场_相似列表项" --robot-id 2115990391806255104
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import rpa_config as cfg  # noqa: E402,F401

DEFAULT_ROBOT_ID = "2115990391806255104"


def _auto():
    import uiautomation

    return uiautomation


def find_window(auto, title):
    root = auto.GetRootControl()
    cands = [c for c in root.GetChildren() if title in (c.Name or "")]
    if not cands:
        return None
    cands.sort(key=lambda c: 0 if c.ControlTypeName == "WindowControl" else 1)
    return cands[0]


def walk(control, depth, level=0):
    yield control, level
    if level >= depth:
        return
    try:
        children = control.GetChildren()
    except Exception:  # noqa: BLE001
        return
    for ch in children:
        yield from walk(ch, depth, level + 1)


def find_items(root, depth, ctype, name):
    """按控件类型 + 名称精确找（同名多个时取第一个）。"""
    hits = []
    for ctrl, _lvl in walk(root, depth):
        if ctrl is root:
            continue
        if (ctrl.ControlTypeName or "") != ctype:
            continue
        if (ctrl.Name or "") != name:
            continue
        hits.append(ctrl)
    return hits


class _StubSvc:
    """`path()` 末尾会读 `svc.data["pick_type"]` / `svc.data["data"]`。"""

    def __init__(self, data=None):
        self.data = data or {}


def build_similar(first_ctrl, second_ctrl):
    """先普通拾取第一个元素，再用它当「旧元素」拾取第二个 → 得到 similar 元素。"""
    from astronverse.picker import PickerType
    from astronverse.picker.engines.uia_picker import UIAElement

    old = UIAElement(control=first_ctrl).path(strategy_svc=_StubSvc({}))
    stub = _StubSvc({"pick_type": PickerType.SIMILAR, "data": old})
    new = UIAElement(control=second_ctrl).path(strategy_svc=stub)
    return old, new


def show_path(tag, data):
    print(f"  {tag}：picker_type={data.get('picker_type')!r} app={data.get('app')!r} "
          f"path {len(data.get('path') or [])} 层 similar_count={data.get('similar_count')}")
    for i, node in enumerate(data.get("path") or []):
        print(f"    {i}. tag={node.get('tag_name')!r:<24} name={(node.get('name') or '')[:16]!r:<20} "
              f"cls={(node.get('cls') or '')[:14]!r:<18} similar_parent={node.get('similar_parent')} "
              f"disabled={node.get('disable_keys')}")


def verify_similar(data):
    """把 similar 元素喂给运行时定位器，看它返回几个相似元素、分别是谁。"""
    from astronverse.locator.locator import locator

    pure = dict(data)
    pure.pop("img", None)
    res = locator.locator(pure)
    if res is None:
        return [], "定位器返回空"
    if not isinstance(res, list):
        res = [res]
    names = []
    for loc in res:
        try:
            ctrl = loc.control()
            names.append(ctrl.Name if ctrl else None)
        except Exception as e:  # noqa: BLE001
            names.append(f"<取名失败 {e}>")
    return names, f"定位到 {len(names)} 个"


def spawn_host(items=4, wait=8.0):
    """在**本进程内**拉起靶场（子进程），干活完再收掉。

    为什么不用 gui_spawn.py 的 detached 方式：detached 出来的进程在两次命令之间
    可能被会话清理回收（实测窗口刚出现就没了），拾取就没法做了。
    放在同一个脚本里起子进程最稳 —— 干完 finally 里 terminate 掉。
    """
    import subprocess
    import sys as _sys
    import time

    host = os.path.join(HERE, "similar_host.py")
    cmd = [_sys.executable, host, "--items", str(items)]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"已拉起靶场 pid={proc.pid}（{items} 个列表项）")

    auto = _auto()
    deadline = time.time() + wait
    while time.time() < deadline:
        win = find_window(auto, "靶场")
        if win is not None:
            print(f"靶场窗口已就绪：{win.Name!r} pid={win.ProcessId}")
            return proc
        if proc.poll() is not None:
            print(f"靶场进程已退出 rc={proc.returncode}")
            return proc
        time.sleep(0.5)
    print(f"等 {wait}s 靶场窗口没出现")
    return proc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", default="星辰相似元素靶场", help="靶场窗口标题子串")
    ap.add_argument("--first", default="项目一", help="第一个（作为「旧元素」）的控件名")
    ap.add_argument("--second", default="项目二", help="第二个（相似的那个）控件名")
    ap.add_argument("--ctype", default="ListItemControl", help="控件类型（默认 ListItemControl）")
    ap.add_argument("--depth", type=int, default=5)
    ap.add_argument("--spawn", action="store_true", help="自己拉起靶场（推荐，避免跨命令被回收）")
    ap.add_argument("--items", type=int, default=4, help="靶场列表项个数")
    ap.add_argument("--probe", action="store_true", help="只生成 + 自证，不写库")
    ap.add_argument("--element-name", help="写库时的元素名")
    ap.add_argument("--robot-id", default=DEFAULT_ROBOT_ID)
    ap.add_argument("--group", default="AI桌面元素")
    args = ap.parse_args()

    host_proc = spawn_host(args.items) if args.spawn else None
    try:
        return _do(args)
    finally:
        if host_proc is not None and host_proc.poll() is None:
            host_proc.terminate()
            try:
                host_proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                host_proc.kill()
            print("靶场已收尾")


def _do(args):
    auto = _auto()
    win = find_window(auto, args.title)
    if win is None:
        print(f"没找到标题含 {args.title!r} 的窗口（UIA 根节点下找不到；确认窗口带 WS_EX_APPWINDOW）")
        return 2
    print(f"窗口：{win.Name!r} cls={win.ClassName!r} pid={win.ProcessId}")

    firsts = find_items(win, args.depth, args.ctype, args.first)
    seconds = find_items(win, args.depth, args.ctype, args.second)
    if not firsts:
        print(f"没找到 {args.ctype} {args.first!r}")
        return 2
    if not seconds:
        print(f"没找到 {args.ctype} {args.second!r}")
        return 2
    first, second = firsts[0], seconds[0]
    print(f"旧元素：{first.ControlTypeName} {first.Name!r}")
    print(f"新元素：{second.ControlTypeName} {second.Name!r}")

    old, new = build_similar(first, second)
    print("\n普通元素（旧元素）：")
    show_path("old", old)
    print("\n相似元素（带 similar_parent 标记）：")
    show_path("new", new)

    names, detail = verify_similar(new)
    print(f"\n自证（喂给运行时定位器）：{detail}")
    for i, n in enumerate(names):
        print(f"  [{i}] {n!r}")
    ok = len(names) >= 2
    print(f"  {'✓ 找到多个相似元素' if ok else '✗ 只找到 1 个或 0 个，不构成「相似元素」'}")

    if args.probe:
        out = os.path.join("demo", "_gui_out", "similar_probe.json")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(new, f, ensure_ascii=False, indent=1)
        print(f"\n（--probe：未写库。elementData 已存 {out}）")
        return 0 if ok else 1

    if not args.element_name:
        ap.error("写库必须给 --element-name")
    if not ok:
        print("\n✗ 自证没过，拒绝写库")
        return 1

    import element_pick as EP

    eid = EP.create_element(args.robot_id, args.group, args.element_name, new)
    print(f"\n✓ 已写元素库：{args.element_name} -> id={eid}（robot={args.robot_id} group={args.group}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
