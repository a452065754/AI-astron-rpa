#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
星辰开源 · Gui（桌面鼠标键盘）测试靶场
=========================================

为什么造一个网页当靶子：`Gui.*` 8 个原子全是**裸鼠标/裸键盘**操作，没有元素概念，
所以必须有一块「屏幕上的已知像素区域」来当操作对象，而且操作结果要能被外部读到。

三条硬要求：

1. **坐标可精确推导** —— `Gui.mouse_move` / `Gui.mouse_drag` 用的是**屏幕绝对坐标**。
   页面左上角放一个 80x80 的纯品红 `#FF00FF` 方块（`#origin`），抓屏扫色就能拿到
   视口原点的屏幕坐标；页内所有目标的 CSS 坐标加上这个原点 = 屏幕绝对坐标。
   扫描不到就说明 DPI 不是 100%，脚本直接报错而不是给出错位的坐标。
2. **结果可被外部读取** —— 事件写进 `document.title`（浏览器维护，流程碰不到，无法伪造），
   同时也渲染进 `#log` 面板（最终截图里肉眼可见）。
3. **不能被别的窗口挡住** —— 独立 user-data-dir、固定窗口位置、靶场元素自己占住点击点。

## 页面事件记录格式（标题里）

    GUI靶场|K:<按键序列>|E:<事件序列>

事件序列用逗号分隔：
- `C<clientX>x<clientY>`  一次点击（client 坐标 = 视口内坐标）
- `W<D|U>`               一次滚轮（向下 D / 向上 U）
- `D<sx>,<sy>><ex>,<ey>` 一次拖拽（起点 client 坐标 > 终点 client 坐标）
- `I:<值>`               输入框内容变化

client 坐标与屏幕绝对坐标的换算关系由外部独立测量（视口原点），所以
「页面记下的 client 坐标」能和「流程里填的屏幕绝对坐标」互相印证 —— 这就是
`Gui.mouse_move` / `Gui.mouse` 真的作用在绝对屏幕坐标上的证据。

## 用法

    python scripts/gui_demo_page.py            # 生成并启动
    python scripts/gui_demo_page.py --status   # 只看状态（窗口矩形 / 视口原点 / 事件日志）
    python scripts/gui_demo_page.py --close     # 关掉
"""

import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(os.path.dirname(HERE), "demo", "_gui_out", "site")
PROFILE_DIR = os.path.join(os.path.dirname(HERE), "demo", "_gui_out", "chrome-profile-gui")

WIN_TITLE_TAG = "GUI靶场"
WIN_X, WIN_Y, WIN_W, WIN_H = 80, 60, 1200, 800

# 视口原点标记色（页面左上角 80x80 纯色方块）
ORIGIN_COLOR = (255, 0, 255)  # #FF00FF
ORIGIN_SIZE = 80

# 页面内布局（CSS 像素，相对视口左上角）
CSS_INPUT = (140, 220, 360, 48)   # 输入框：键盘输入落点
CSS_DRAG = (140, 330, 320, 140)   # 拖拽区
CSS_LOG = (560, 220, 580, 420)    # 事件日志面板

# 空白区（测 Gui.mouse / Gui.mouse_wheel）
CSS_CLICK = (600, 520)


def write_html():
    html = """<!doctype html>
<html><head><meta charset="utf-8"><title>{tag}</title>
<style>
  html,body{{margin:0;padding:0;overflow:hidden;height:100%;
            font-family:Consolas,'Courier New',monospace;background:#101820;color:#e8e8e8}}
  /* 视口原点标记：外部靠扫这个纯色方块反推视口在屏幕上的绝对位置 */
  #origin{{position:absolute;left:0;top:0;width:{os}px;height:{os}px;
           background:rgb{oc};z-index:9}}
  #t{{position:absolute;left:{ix}px;top:{iy}px;width:{iw}px;height:{ih}px;
      box-sizing:content-box;border:4px solid #00FF66;background:#ffffff;color:#000;
      font:22px Consolas,monospace;padding:2px 6px;outline:none;z-index:3}}
  #drag{{position:absolute;left:{dx}px;top:{dy}px;width:{dw}px;height:{dh}px;
         box-sizing:content-box;border:4px solid #FFCC00;background:#1c2733;
         color:#ffcc00;font-size:20px;padding:8px;z-index:2}}
  #log{{position:absolute;left:{lx}px;top:{ly}px;width:{lw}px;height:{lh}px;
       box-sizing:content-box;border:3px solid #3399FF;background:#0b1016;
       padding:8px;font:15px Consolas,monospace;white-space:pre-wrap;
       overflow:hidden;z-index:2}}
  #hint{{position:absolute;left:140px;top:120px;font-size:18px;color:#9fb3c8;z-index:2}}
</style></head>
<body>
  <div id="origin"></div>
  <div id="hint">Gui 靶场 · 事件写进标题栏与下方日志面板</div>
  <input id="t" autocomplete="off">
  <div id="drag">拖拽区 DRAG</div>
  <div id="log"></div>
<script>
  var keys = [], evs = [];
  function refresh() {{
    // 事件之间用 ';' 分隔（不是 ','）—— 事件内容本身含逗号（如 D164,354>460,470），
    // 用逗号分隔会在解析时被切碎。
    var s = 'K:' + keys.join(',') + '|E:' + evs.join(';');
    document.title = '{tag}|' + s;
    document.getElementById('log').textContent = s;
  }}
  document.addEventListener('keydown', function (e) {{
    keys.push(e.key); refresh();
  }});
  document.addEventListener('click', function (e) {{
    evs.push('C' + e.clientX + 'x' + e.clientY); refresh();
  }});
  document.addEventListener('wheel', function (e) {{
    evs.push('W' + (e.deltaY > 0 ? 'D' : 'U')); refresh();
  }}, {{passive: true}});
  var ds = null;
  document.addEventListener('mousedown', function (e) {{
    ds = {{x: e.clientX, y: e.clientY}};
  }});
  document.addEventListener('mouseup', function (e) {{
    if (ds) {{
      evs.push('D' + ds.x + ',' + ds.y + '>' + e.clientX + ',' + e.clientY);
      ds = null; refresh();
    }}
  }});
  var t = document.getElementById('t');
  t.addEventListener('input', function () {{
    evs.push('I:' + t.value); refresh();
  }});
  refresh();
</script>
</body></html>
""".format(
        tag=WIN_TITLE_TAG,
        os=ORIGIN_SIZE,
        oc=ORIGIN_COLOR,
        ix=CSS_INPUT[0], iy=CSS_INPUT[1], iw=CSS_INPUT[2], ih=CSS_INPUT[3],
        dx=CSS_DRAG[0], dy=CSS_DRAG[1], dw=CSS_DRAG[2], dh=CSS_DRAG[3],
        lx=CSS_LOG[0], ly=CSS_LOG[1], lw=CSS_LOG[2], lh=CSS_LOG[3],
    )
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def find_chrome():
    sys.path.insert(0, HERE)
    import element_pick as ep

    return ep.find_chrome()


def launch():
    exe = find_chrome()
    if not exe:
        raise SystemExit("没找到 chrome.exe")
    url = "file:///" + os.path.join(OUT_DIR, "index.html").replace("\\", "/")
    args = [
        exe,
        "--user-data-dir=" + PROFILE_DIR,
        "--new-window",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        "--hide-crash-restore-bubble",
        "--window-position={},{}".format(WIN_X, WIN_Y),
        "--window-size={},{}".format(WIN_W, WIN_H),
        url,
    ]
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    CREATE_BREAKAWAY_FROM_JOB = 0x01000000
    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB
    try:
        subprocess.Popen(args, creationflags=flags, close_fds=True, cwd="C:\\")
    except OSError:
        subprocess.Popen(
            args,
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
            cwd="C:\\",
        )
    return url


def chrome_hwnd():
    import win32gui

    hits = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and WIN_TITLE_TAG in win32gui.GetWindowText(hwnd):
            hits.append(hwnd)

    win32gui.EnumWindows(cb, None)
    return hits[0] if hits else None


def wait_title(timeout=25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        h = chrome_hwnd()
        if h:
            import win32gui

            return h, win32gui.GetWindowText(h)
        time.sleep(0.4)
    return None, ""


def scan_color(shot, color, tol=10):
    """在截图里按颜色找矩形，返回外接矩形 (x, y, w, h)；扫不到返回 None。"""
    import numpy as np

    arr = np.asarray(shot.convert("RGB"), dtype=np.int16)
    diff = np.abs(arr - np.array(color, dtype=np.int16))
    mask = (diff <= tol).all(axis=2)
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return None
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    return (x0, y0, x1 - x0 + 1, y1 - y0 + 1)


def viewport_origin(shot=None):
    """扫视口原点标记，返回屏幕绝对坐标 (ox, oy)。

    标记方块左上角就是视口左上角（页面 margin/padding 归零、#origin 绝对定位在 0,0）。
    """
    if shot is None:
        import pyautogui

        pyautogui.FAILSAFE = False
        shot = pyautogui.screenshot()
    rect = scan_color(shot, ORIGIN_COLOR)
    if not rect:
        return None
    return (rect[0], rect[1])


def parse_title(title):
    """把标题解析成 {keys: [...], events: [...]}。

    Chrome 的窗口标题 = document.title + " - Google Chrome"，必须先把后缀剥掉，
    否则 " - Google Chrome" 会被当成一条事件。
    """
    out = {"keys": [], "events": []}
    if "|" not in title:
        return out
    tail = title.split("|", 1)[1]
    for seg in tail.split("|"):
        if seg.endswith(" - Google Chrome"):
            seg = seg[: -len(" - Google Chrome")]
        if seg.startswith("K:"):
            out["keys"] = [k for k in seg[2:].split(",") if k]
        elif seg.startswith("E:"):
            # 事件用 ';' 分隔；事件内部可能含逗号，不能再按逗号切
            out["events"] = [e for e in seg[2:].split(";") if e]
    return out


def status(verbose=True):
    import pyautogui
    import win32gui

    h = chrome_hwnd()
    if not h:
        if verbose:
            print("靶场没在跑（找不到标题含「{}」的窗口）".format(WIN_TITLE_TAG))
        return {"alive": False}
    title = win32gui.GetWindowText(h)
    l, t, r, b = win32gui.GetWindowRect(h)
    info = {"alive": True, "hwnd": h, "title": title, "window_rect": (l, t, r - l, b - t)}

    pyautogui.FAILSAFE = False
    shot = pyautogui.screenshot()
    info["screen"] = shot.size
    org = viewport_origin(shot)
    info["viewport_origin"] = org
    if verbose:
        print("窗口标题 : {}".format(title))
        print("窗口矩形 : ({}, {}, {}x{})".format(*info["window_rect"]))
        print("屏幕     : {}x{}".format(*info["screen"]))
        print("视口原点 : {}".format(org))
        p = parse_title(title)
        print("按键序列 : {}".format(p["keys"]))
        print("事件序列 : {}".format(p["events"]))
    return info


def main():
    ap = argparse.ArgumentParser(description="Gui 测试靶场")
    ap.add_argument("--status", action="store_true", help="打印窗口/视口原点/事件日志（只读）")
    ap.add_argument("--close", action="store_true", help="关闭靶场窗口")
    args = ap.parse_args()

    if args.status:
        info = status()
        return 0 if info.get("alive") else 1

    if args.close:
        import win32con
        import win32gui

        h = chrome_hwnd()
        if not h:
            print("靶场没在跑")
            return 0
        win32gui.PostMessage(h, win32con.WM_CLOSE, 0, 0)
        print("✓ 已请求关闭 hwnd={}".format(h))
        return 0

    page = write_html()
    print("· 入口 → {}".format(page))
    url = launch()
    print("· 已启动 {}".format(url))
    h, title = wait_title()
    if not h:
        raise SystemExit("等了 25 秒还没看到标题含「{}」的窗口".format(WIN_TITLE_TAG))
    print("✓ 窗口已就绪 hwnd={} 标题={!r}".format(h, title))
    time.sleep(1.5)
    status()
    return 0


if __name__ == "__main__":
    sys.exit(main())
