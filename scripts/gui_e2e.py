#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
星辰开源 · Gui 域（桌面鼠标键盘）全链路端到端验证
=================================================

## 为什么 Gui 域要单独造靶场

`Gui.*` 8 个原子是**裸鼠标 / 裸键盘**操作：没有元素概念，只有屏幕绝对坐标。
桌面现成窗口满足不了两条：

1. **坐标必须能精确推导** —— `Gui.mouse_move` / `Gui.mouse_drag` 用的是屏幕绝对坐标。
   靶场页面左上角放一块 80x80 纯品红方块，抓屏扫色得到视口原点的屏幕坐标，
   页面内目标的 CSS 坐标 + 原点 = 屏幕绝对坐标。扫不到就说明 DPI 不是 100%，
   脚本直接报错，绝不给出错位的坐标。
2. **结果必须能被外部读到** —— 靶场把事件写进 `document.title`（浏览器维护，
   流程碰不到，无法伪造），同时渲染进页面里的日志面板（最终截图肉眼可见）。

## 证据是双通道互相印证

| 通道 | 来源 | 证明什么 |
|---|---|---|
| 窗口标题 `GUI靶场\\|K:...\\|E:...` | 外部 `win32gui.GetWindowText` | 点击/滚轮/拖拽/按键**真的发生了**，且 client 坐标与流程里填的屏幕坐标对得上 |
| 取证文件里的鼠标位置 | 流程内 `Gui.mouse_position` 原子 | 指针**真的**被移动到了目标绝对坐标 |
| 外部 `GetWindowRect(mstsc)` | win32gui | 相对窗口移动的终点与外部独立测量一致 |

## 关于被拾取窗口（远程桌面连接）

`Gui.mouse_move_new` 的 `active_window` 分支需要一个窗口元素，元素库里的
`mstsc_窗口`（path[0] = {cls:"#32770", name:"远程桌面连接"}）正好能被
`window_find` → `win32gui.FindWindow` 命中。

**mstsc 不是单实例**（实测连开两个会得到两个同名窗口，位置还一样），所以：
- 流程里**不**调用 `Software.open`（否则会多出一个同名窗口，FindWindow 命中错的那个）
- 由本脚本预先打开**唯一**一个，并 `SetWindowPos` 移到屏幕右下角，
  保证它既不遮挡 Chrome 靶场，位置又完全已知

## 用法

    "%APPDATA%/astron-rpa/python_core/python.exe" scripts/gui_e2e.py \
        --robot-id 2115990391806255104 \
        --flow flows/桌面鼠标键盘操作小流程.json
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import gui_demo_page as page

ROOT = os.path.dirname(HERE)
OUT_DIR = os.path.join(ROOT, "demo", "_gui_out")

MSTSC_EXE = r"C:\Windows\System32\mstsc.exe"
MSTSC_TITLE = "远程桌面连接"
# 被拾取窗口挪到屏幕右下角：Chrome 靶场窗口占 (80,60)-(1280,860)，避开它
MSTSC_POS = (1420, 560)
# 引擎内部 window_info 会给窗口矩形加 WINDOW_SHADOW_OFFSET=9 的阴影补偿
WINDOW_SHADOW_OFFSET = 9

# 靶场页里三种纯色边框（用来扫出精确像素矩形）
COLOR_INPUT = (0, 255, 102)   # #00FF66 输入框边框
COLOR_DRAG = (255, 204, 0)    # #FFCC00 拖拽区边框

REL_X, REL_Y = 60, 40         # 相对窗口移动的偏移
KEYS_TEXT = "RPA-GUI-KEY"


def log(msg):
    print(msg, flush=True)


# --------------------------------------------------------------------------- #
# 1. 准备：唯一 mstsc 窗口 + Chrome 靶场
# --------------------------------------------------------------------------- #
def close_mstsc():
    """关掉所有已存在的「远程桌面连接」窗口，保证后面只有一个。"""
    import win32con
    import win32gui

    hits = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and MSTSC_TITLE in win32gui.GetWindowText(hwnd):
            hits.append(hwnd)

    win32gui.EnumWindows(cb, None)
    for h in hits:
        try:
            win32gui.PostMessage(h, win32con.WM_CLOSE, 0, 0)
        except Exception:
            pass
    return len(hits)


def open_mstsc():
    """打开唯一的 mstsc 窗口并挪到固定位置，返回 (hwnd, rect)。"""
    import win32con
    import win32gui

    close_mstsc()
    time.sleep(1.0)

    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    CREATE_BREAKAWAY_FROM_JOB = 0x01000000
    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB
    try:
        subprocess.Popen([MSTSC_EXE], creationflags=flags, close_fds=True, cwd="C:\\")
    except OSError:
        # 某些环境不允许 breakaway（进程在 job 里），退一步只 detached
        subprocess.Popen(
            [MSTSC_EXE],
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
            cwd="C:\\",
        )

    h = None
    deadline = time.time() + 20
    while time.time() < deadline:
        hits = []

        def cb(hwnd, _):
            if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd) == MSTSC_TITLE:
                hits.append(hwnd)

        win32gui.EnumWindows(cb, None)
        if len(hits) == 1:
            h = hits[0]
            break
        if len(hits) > 1:
            raise SystemExit("✗ 出现了 {} 个「{}」窗口，不是预期内的唯一窗口".format(len(hits), MSTSC_TITLE))
        time.sleep(0.4)
    if not h:
        raise SystemExit("✗ 20 秒内没等到「{}」窗口".format(MSTSC_TITLE))

    # 挪到固定位置（不激活、不改尺寸，避免抢焦点 / 改布局）
    SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0004, 0x0010
    win32gui.SetWindowPos(
        h, 0, MSTSC_POS[0], MSTSC_POS[1], 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
    )
    time.sleep(0.6)
    l, t, r, b = win32gui.GetWindowRect(h)
    log("· 被拾取窗口 hwnd={} 标题={!r} 矩形=({},{},{}x{})".format(h, MSTSC_TITLE, l, t, r - l, b - t))
    return h, (l, t, r - l, b - t)


def start_page():
    """启动 Chrome 靶场并等到渲染完成。"""
    import win32gui

    page.write_html()
    page.launch()
    h, title = page.wait_title(timeout=30)
    if not h:
        raise SystemExit("✗ Chrome 靶场没起来")
    time.sleep(2.0)  # 等首帧渲染完，扫色才准

    # 强制提到 Z 序最前：桌面上还开着微信/元宝/Edge 等窗口，
    # 不置顶的话后续的裸鼠标点击可能被它们吃掉（页面就收不到事件）。
    HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
    SWP_NOMOVE, SWP_NOSIZE, SWP_SHOWWINDOW = 0x0002, 0x0001, 0x0040
    win32gui.SetWindowPos(h, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
    win32gui.SetWindowPos(h, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
    try:
        import win32com.client

        win32com.client.Dispatch("WScript.Shell").SendKeys("%")
        win32gui.SetForegroundWindow(h)
    except Exception as e:
        log("· SetForegroundWindow 尝试失败（不致命）：{}".format(e))
    time.sleep(0.8)
    log("· 靶场窗口 hwnd={} 标题={!r}".format(h, win32gui.GetWindowText(h)))
    return h


def plan_targets(chrome_h):
    """实测视口原点 + 扫出输入框/拖拽区矩形，算出所有屏幕绝对坐标。

    **扫描必须限定在靶场窗口矩形内**：满屏扫色会把别的窗口里的同色像素也算进来
    （实测用户开着 Edge 搜百度，页面里的橙色 #FFCC00 把拖拽区边框框到了 x=16，
    算出来的起点甚至是负的 client 坐标）。
    """
    import pyautogui
    import win32gui

    pyautogui.FAILSAFE = False
    full = pyautogui.screenshot()
    wl, wt, wr, wb = win32gui.GetWindowRect(chrome_h)
    # 多留 8px 余量：窗口阴影/边框会让纯色区比窗口矩形略小
    shot = full.crop((max(wl - 8, 0), max(wt - 8, 0), min(wr + 8, full.size[0]), min(wb + 8, full.size[1])))
    ox_off, oy_off = max(wl - 8, 0), max(wt - 8, 0)

    origin = page.viewport_origin(shot)
    if not origin:
        raise SystemExit("✗ 扫不到视口原点标记色 #FF00FF —— 页面没渲染完或 DPI 不是 100%")
    ox, oy = origin[0] + ox_off, origin[1] + oy_off

    input_rect = page.scan_color(shot, COLOR_INPUT)
    if not input_rect:
        raise SystemExit("✗ 扫不到输入框边框色 #00FF66")
    drag_rect = page.scan_color(shot, COLOR_DRAG)
    if not drag_rect:
        raise SystemExit("✗ 扫不到拖拽区边框色 #FFCC00")

    # 裁剪后的坐标要还原成屏幕绝对坐标
    input_rect = (input_rect[0] + ox_off, input_rect[1] + oy_off, input_rect[2], input_rect[3])
    drag_rect = (drag_rect[0] + ox_off, drag_rect[1] + oy_off, drag_rect[2], drag_rect[3])

    ix, iy, iw, ih = input_rect
    dx, dy, dw, dh = drag_rect
    click_x, click_y = ix + iw // 2, iy + ih // 2
    # 拖拽起点/终点落在拖拽区**内部**（避开边框本身）
    drag_x1, drag_y1 = dx + 24, dy + 24
    drag_x2, drag_y2 = dx + dw - 24, dy + dh - 24
    # 另一个绝对坐标点：拖拽区右侧的空白处
    move_x, move_y = dx + dw + 160, dy + 40

    targets = {
        "click_x": click_x, "click_y": click_y,
        "drag_x1": drag_x1, "drag_y1": drag_y1,
        "drag_x2": drag_x2, "drag_y2": drag_y2,
        "move_x": move_x, "move_y": move_y,
        "rel_x": REL_X, "rel_y": REL_Y,
        "keys_text": KEYS_TEXT,
    }
    log("· 视口原点 = ({}, {})".format(ox, oy))
    log("· 输入框矩形 = {}  → 点击点 = ({}, {})".format(input_rect, click_x, click_y))
    log("· 拖拽区矩形 = {}  → 拖拽 {} -> {}".format(drag_rect, (drag_x1, drag_y1), (drag_x2, drag_y2)))
    log("· mouse_move_new（全屏）目标 = ({}, {})".format(move_x, move_y))
    return targets, (ox, oy), (input_rect, drag_rect)


def backfill_spec(flow, targets):
    """把实测坐标写回规格的 param 默认值（规格留在 flows/ 里，覆盖率统计才认）。"""
    with open(flow, encoding="utf-8") as f:
        spec = json.load(f)
    for p in spec.get("params") or []:
        if p.get("name") in targets:
            p["default"] = targets[p["name"]]
    with open(flow, "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=2)
    log("· 已把实测坐标回填进规格：{}".format(flow))


# --------------------------------------------------------------------------- #
# 2. 发布 + 真跑
# --------------------------------------------------------------------------- #
def publish(flow, robot_id):
    gen = [
        sys.executable, os.path.join(HERE, "flow_generator.py"),
        "--spec", flow, "--robot-id", str(robot_id),
    ]
    log("· 编译规格 → 节点树（含 validate）…")
    r = subprocess.run(gen, cwd=ROOT, capture_output=True, text=True, timeout=300)
    for line in (r.stdout or "").strip().splitlines()[-10:]:
        log("  " + line)
    if r.returncode != 0:
        raise SystemExit("✗ 生成失败：\n{}\n{}".format(r.stdout, r.stderr))

    generated = os.path.join(ROOT, "docs", "generated_flow.json")
    cmd = [
        sys.executable, os.path.join(HERE, "publish_flow.py"),
        "--flow", generated, "--robot-id", str(robot_id), "--main",
    ]
    log("· 发布到 .130 …")
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=180)
    for line in (r.stdout or "").strip().splitlines()[-8:]:
        log("  " + line)
    if r.returncode != 0:
        raise SystemExit("✗ 发布失败：\n{}\n{}".format(r.stdout, r.stderr))
    return r


def run_flow(robot_id, timeout):
    cmd = [
        sys.executable, os.path.join(HERE, "run_flow_local.py"),
        "--robot-id", robot_id, "--timeout", str(timeout), "--show-log",
    ]
    log("· 本地真跑流程…")
    t0 = time.time()
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout + 120)
    return r, time.time() - t0


# --------------------------------------------------------------------------- #
# 3. 取证 + 交叉核对
# --------------------------------------------------------------------------- #
def read_evidence_file():
    p = os.path.join(OUT_DIR, "桌面鼠标键盘取证.txt")
    if not os.path.exists(p):
        return None, ""
    with open(p, encoding="utf-8") as f:
        return p, f.read()


def parse_positions(text):
    """从取证文本里抠出 5 组鼠标位置（流程内 Gui.mouse_position 读到的）。"""
    out = []
    for m in re.finditer(r"^\d\)\s*[^:：]*[:：]\s*(\d+)\s*,\s*(\d+)", text, re.M):
        out.append((int(m.group(1)), int(m.group(2))))
    return out


def evidence(chrome_h, mstsc_h, mstsc_rect, origin, targets, flow_rc, elapsed):
    import win32gui

    out = {}
    title = win32gui.GetWindowText(chrome_h) if win32gui.IsWindow(chrome_h) else "(窗口已关闭)"
    out["title"] = title
    log("· 靶场窗口标题：{}".format(title))

    parsed = page.parse_title(title)
    keys = parsed["keys"]
    evs = parsed["events"]
    out["keys"] = keys
    out["events"] = evs

    ox, oy = origin
    checks = []

    def check(name, ok, detail):
        checks.append((name, ok, detail))
        log("  {} {:<34} {}".format("✓" if ok else "✗", name, detail))

    # —— 点击：client 坐标必须等于 屏幕坐标 - 视口原点 ——
    # 注意 DOM 的 click 事件在「按下+抬起」同一元素时也会触发，
    # 所以拖拽结束（mouseup）会额外带来一次 click —— 第一次才是 Gui.mouse 那次。
    clicks = []
    for e in evs:
        if e.startswith("C"):
            try:
                cx, cy = e[1:].split("x")
                clicks.append((int(cx), int(cy)))
            except Exception:
                pass
    exp_cx = targets["click_x"] - ox
    exp_cy = targets["click_y"] - oy
    check(
        "Gui.mouse 落在预期像素",
        bool(clicks) and clicks[0] == (exp_cx, exp_cy),
        "第 1 次点击 {} ，预期 ({},{})；全部点击：{}".format(
            clicks[0] if clicks else None, exp_cx, exp_cy, clicks
        ),
    )

    # —— 滚轮 ——
    wheels = [e for e in evs if e.startswith("W")]
    check("Gui.mouse_wheel 产生滚轮事件", len(wheels) >= 1, "记录到 {} 次：{}".format(len(wheels), wheels))

    # —— 拖拽 ——
    drags = [e for e in evs if e.startswith("D")]
    exp_ds = (targets["drag_x1"] - ox, targets["drag_y1"] - oy)
    exp_de = (targets["drag_x2"] - ox, targets["drag_y2"] - oy)
    got_d = None
    for e in drags:
        try:
            s, en = e[1:].split(">")
            sx, sy = s.split(",")
            ex, ey = en.split(",")
            got_d = ((int(sx), int(sy)), (int(ex), int(ey)))
        except Exception:
            pass
    check(
        "Gui.mouse_drag 起终点正确",
        got_d == (exp_ds, exp_de),
        "页面记到 {} ，预期 ({},{})->({},{})".format(got_d, exp_ds[0], exp_ds[1], exp_de[0], exp_de[1]),
    )
    # 拖拽的 mouseup 在 DOM 里也会产生一次 click，它必须落在拖拽终点上
    check(
        "拖拽抬键点落在终点",
        len(clicks) >= 2 and clicks[-1] == exp_de,
        "最后一次点击 {} ，拖拽终点预期 ({},{})".format(
            clicks[-1] if clicks else None, exp_de[0], exp_de[1]
        ),
    )

    # —— 键盘 ——
    want = list(KEYS_TEXT)
    missing = [c for c in want if c not in keys]
    check("Gui.keyboard 逐字符送达", not missing, "缺字符：{}（共记到 {} 个按键）".format(missing or "无", len(keys)))
    check(
        "Gui.key_input 组合键送达",
        "Control" in keys and any(k.lower() == "a" for k in keys),
        "按键序列前 20 个：{}".format(keys[:20]),
    )
    inputs = [e for e in evs if e.startswith("I:")]
    check(
        "文字真的进了输入框",
        any(e == "I:" + KEYS_TEXT for e in inputs),
        "页面记到 {}".format(inputs),
    )

    # —— 流程内读到的鼠标位置 ——
    p, text = read_evidence_file()
    out["evidence_file"] = p
    if not p:
        check("流程写出取证文件", False, "找不到 桌面鼠标键盘取证.txt")
    else:
        pos = parse_positions(text)
        out["positions"] = pos
        exp_pos = [
            (targets["click_x"], targets["click_y"]),          # mouse_move 之后
            (targets["click_x"], targets["click_y"]),          # 点击/输入/按键之后（不该动）
            (targets["drag_x2"], targets["drag_y2"]),          # 拖拽之后
            (targets["move_x"], targets["move_y"]),            # mouse_move_new（全屏）之后
            (
                mstsc_rect[0] + WINDOW_SHADOW_OFFSET + REL_X,
                mstsc_rect[1] + WINDOW_SHADOW_OFFSET + REL_Y,
            ),                                                 # 相对窗口移动之后
        ]
        check(
            "Gui.mouse_position 读到的 5 组坐标全对",
            pos == exp_pos,
            "读到 {} ，预期 {}".format(pos, exp_pos),
        )
        # 相对窗口那一组还要和外部独立测的窗口矩形对得上
        if pos and len(pos) == 5:
            l, t, _, _ = mstsc_rect
            check(
                "相对窗口移动终点 = 窗口矩形+阴影补偿+偏移",
                pos[4] == (l + WINDOW_SHADOW_OFFSET + REL_X, t + WINDOW_SHADOW_OFFSET + REL_Y),
                "窗口矩形({},{}) + {} + ({},{}) = ({},{})".format(
                    l, t, WINDOW_SHADOW_OFFSET, REL_X, REL_Y,
                    l + WINDOW_SHADOW_OFFSET + REL_X, t + WINDOW_SHADOW_OFFSET + REL_Y,
                ),
            )

    check("流程执行成功", flow_rc == 0, "exit={} 耗时 {:.1f}s".format(flow_rc, elapsed))

    out["checks"] = checks
    out["passed"] = sum(1 for _, ok, _ in checks if ok)
    out["total"] = len(checks)
    return out


def main():
    ap = argparse.ArgumentParser(description="Gui 域全链路端到端验证")
    ap.add_argument("--robot-id", default="2115990391806255104")
    ap.add_argument("--flow", default=os.path.join(ROOT, "flows", "桌面鼠标键盘操作小流程.json"))
    ap.add_argument("--timeout", type=int, default=240)
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    for fn in ("桌面鼠标键盘取证_0_开始.txt", "桌面鼠标键盘取证.txt"):
        p = os.path.join(OUT_DIR, fn)
        if os.path.exists(p):
            os.remove(p)

    log("=== 1. 准备被拾取窗口（唯一 mstsc）===")
    mstsc_h, mstsc_rect = open_mstsc()

    log("=== 2. 启动 Gui 靶场并实测坐标 ===")
    chrome_h = start_page()
    targets, origin, _rects = plan_targets(chrome_h)

    # 点击点不能落在被拾取窗口上，否则事件会被 mstsc 吃掉
    import win32gui

    ml, mt, mw, mh = mstsc_rect
    if ml <= targets["click_x"] <= ml + mw and mt <= targets["click_y"] <= mt + mh:
        raise SystemExit("✗ 点击点落在被拾取窗口上，事件会被吞掉")

    log("=== 3. 回填坐标并发布 ===")
    backfill_spec(args.flow, targets)
    publish(args.flow, args.robot_id)

    log("=== 4. 真跑流程 ===")
    r, elapsed = run_flow(args.robot_id, args.timeout)
    log("· 耗时 {:.1f}s  exit={}".format(elapsed, r.returncode))
    for line in (r.stdout or "").strip().splitlines()[-20:]:
        log("  " + line)
    if r.returncode != 0:
        log("--- stderr ---")
        log((r.stderr or "")[-1500:])

    log("=== 5. 取证 + 交叉核对 ===")
    ev = evidence(chrome_h, mstsc_h, mstsc_rect, origin, targets, r.returncode, elapsed)

    try:
        import pyautogui

        pyautogui.FAILSAFE = False
        pyautogui.screenshot().save(os.path.join(OUT_DIR, "Gui最终状态.png"))
        log("· 最终状态截图 → {}".format(os.path.join(OUT_DIR, "Gui最终状态.png")))
    except Exception as e:
        log("（截图失败：{}）".format(e))

    log("\n证据清单：{}".format(json.dumps(ev, ensure_ascii=False, indent=2)))
    log("\n结论：{}/{} 项通过".format(ev["passed"], ev["total"]))
    return 0 if ev["passed"] == ev["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
