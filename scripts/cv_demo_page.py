#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
星辰开源 · CV（图像元素）测试靶场
================================

为什么要造一个网页当靶子：`CV.*` 5 个原子都要在**真屏幕**上做模板匹配，靶子必须满足
三条硬要求，桌面上的现成窗口一条都满足不了：

1. **像素稳定** —— 不能有动画/光标闪烁/时间戳，否则模板匹配相似度掉下来就误判成「找不到」。
   桌面窗口的标题栏、输入光标、托盘时间都在动。
2. **可定位** —— 我要知道「哪块像素属于哪个可交互控件」，才能对着它取图。现成窗口里的
   控件位置只能靠 UIA 猜，而 CV 元素恰恰是给「UIA 取不到的东西」用的。
3. **可观测副作用** —— 点击/悬停/输入到底有没有落到目标上，必须有**窗口标题 + 像素**
   双通道证据。网页里用 JS 把事件写进 `document.title`，外部用 win32 读标题即可。

## 靶场布局（CSS 像素；本机 1920x1080 @96DPI，CSS px == 屏幕 px）

    (60,60)   <input id=t>   420x60，6px 纯品红 #FF00FF 边框，背景=噪声B
              输入即把 `IN:<值>` 追加进标题 —— 用来验证 CV.image_input
    (60,220)  <div id=box>   200x90，6px 纯青 #00FFFF 边框，背景=噪声C
              mouseenter 即 hue-rotate(120deg) + 标题加 `HOVER` —— 用来验证 CV.hover_image
    其余区域  <div id=bg>    噪声A 平铺
              click 即整页变 #B00020 + 标题加 `CLICK` —— 用来验证 CV.cv_click

两个边框色（#FF00FF / #00FFFF）是**故意选的罕见纯色**：抓屏后按颜色扫描就能拿到
控件的精确像素矩形，不依赖 DPI 换算、不依赖窗口坐标推算。扫不到 = DPI 缩放不是
100%，脚本会直接报错而不是给出错位的矩形。

## 用法

    # 生成页面资产并启动（窗口固定位置/尺寸，独立 user-data-dir，不干扰日常 Chrome）
    python scripts/cv_demo_page.py

    # 只生成不启动
    python scripts/cv_demo_page.py --no-launch

    # 看窗口状态（标题里的事件日志也在这里）
    python scripts/cv_demo_page.py --status

    # 关掉靶场
    python scripts/cv_demo_page.py --close

启动后会打印 `rects.json`（input / box / bg 三个屏幕绝对矩形），直接喂给
`cvelement_pick.py --rect x,y,w,h` 取图。
"""

import argparse
import json
import os
import random
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(os.path.dirname(HERE), "demo", "_cv_out", "site")
RECTS_JSON = os.path.join(os.path.dirname(HERE), "demo", "_cv_out", "rects.json")
PROFILE_DIR = os.path.join(os.path.dirname(HERE), "demo", "_cv_out", "chrome-profile")

WIN_TITLE_TAG = "CV靶场"
WIN_X, WIN_Y, WIN_W, WIN_H = 80, 60, 1200, 800

# 靶场布局常量（改布局必须同步改这两个数组，扫色定位依赖它们）
BORDER_INPUT = (255, 0, 255)  # #FF00FF
BORDER_BOX = (0, 255, 255)  # #00FFFF（悬停前）
BORDER_BOX_HOVERED = (0, 255, 136)  # #00FF88（悬停后，mouseenter 改的）
CSS_INPUT = (60, 60, 420, 60)
CSS_BOX = (60, 220, 200, 90)


# --------------------------------------------------------------------------- #
# 资产
# --------------------------------------------------------------------------- #
def make_noise(path, size=(160, 160), seed=1, hue=(0, 0), gray=True):
    """确定性噪声图。seed 固定 → 每次生成完全一样，模板匹配才稳定。

    **必须 aperiodic（不重复）**：早期版本用 160x160 图片平铺（background-repeat），
    结果模板匹配在别处命中了同样的噪声（置信度 0.925）——平铺图案是严格周期的，
    对周期为 T 的图案，偏移 T 的任意窗口与原来**逐像素相同**，模板天然有歧义。
    所以这里每块图都按实际显示尺寸生成，CSS 一律 `no-repeat` + 1:1 尺寸。

    **必须灰度（R=G=B）**：引擎 `vision/cv_match.py:110/146` 把已经转成 BGR 的模板
    再按 RGB 转灰度（屏幕侧用的是正确的 BGR2GRAY），通道权重错位。彩色目标下这条
    错误会让「逐像素相同」的模板只得到 ~0.92 分，直接低于默认阈值 0.95 → 明明在屏幕上
    却报「找不到」。灰度图下两种写法逐像素等价（已数值验证），所以靶场用灰度噪声，
    把这条平台缺陷隔离掉，测的是 CV 原子本身而不是这个 bug。
    """
    from PIL import Image

    rnd = random.Random(seed)
    img = Image.new("RGB", size)
    px = img.load()
    for y in range(size[1]):
        for x in range(size[0]):
            if gray:
                v = rnd.randrange(30, 226)
                px[x, y] = (v, v, v)
            else:
                r = rnd.randrange(40, 216)
                g = rnd.randrange(40, 216)
                b = rnd.randrange(40, 216)
                if hue[0] or hue[1]:
                    r = min(255, r + hue[0])
                    g = min(255, g + hue[1])
                px[x, y] = (r, g, b)
    img.save(path)
    return path


def write_html():
    html = """<!doctype html>
<html><head><meta charset="utf-8"><title>{tag}|READY</title>
<style>
  html,body{{margin:0;padding:0;overflow:hidden;height:100%}}
  #bg{{position:fixed;inset:0;background-image:url('noise_bg.png');
       background-repeat:no-repeat;background-size:auto;background-position:0 0;z-index:0}}
  #box{{position:absolute;left:{bx}px;top:{by}px;width:{bw}px;height:{bh}px;
        border:6px solid rgb{bc};background-image:url('noise_box.png');
        background-repeat:no-repeat;background-size:auto;
        z-index:2;box-sizing:content-box}}
  #t{{position:absolute;left:{ix}px;top:{iy}px;width:{iw}px;height:{ih}px;
      border:6px solid rgb{ic};background-image:url('noise_input.png');
      background-repeat:no-repeat;background-size:auto;
      z-index:3;box-sizing:content-box;
      font:24px monospace;color:#ffffff;padding:0 6px;outline:none}}
  #hint{{position:absolute;left:{hx}px;top:{hy}px;z-index:2;font:16px monospace;
         color:#000;background:#fff;padding:4px 8px}}
</style></head>
<body>
  <div id="bg"></div>
  <div id="box"></div>
  <input id="t" autocomplete="off">
  <div id="hint">CV 靶场 · 标题栏会记录事件</div>
<script>
  var log = [];
  function add(s) {{
    log.push(s);
    document.title = '{tag}|' + log.join('|');
  }}
  var bg = document.getElementById('bg');
  var box = document.getElementById('box');
  var t = document.getElementById('t');
  bg.addEventListener('click', function () {{
    bg.style.background = '#B00020';
    add('CLICK');
  }});
  box.addEventListener('mouseenter', function () {{
    // 只改边框色，不用 filter：CSS filter 会把边框一起染色，最终颜色不可预测
    // （实测 hue-rotate(120deg) 把青色转成 240,158,255，无法作为确定断言）。
    // 改成本固定的 #00FF88 后，「边框由青变绿」就是可编程断言的视觉证据。
    // 行为证据仍是标题里的 HOVER（浏览器维护，流程碰不到）。
    box.style.borderColor = '#00FF88';
    if (log.indexOf('HOVER') < 0) add('HOVER');
  }});
  t.addEventListener('input', function () {{
    if (log[log.length - 1] && log[log.length - 1].indexOf('IN:') === 0) {{
      log[log.length - 1] = 'IN:' + t.value;
    }} else {{
      log.push('IN:' + t.value);
    }}
    document.title = '{tag}|' + log.join('|');
  }});
</script>
</body></html>
""".format(
        tag=WIN_TITLE_TAG,
        bx=CSS_BOX[0],
        by=CSS_BOX[1],
        bw=CSS_BOX[2],
        bh=CSS_BOX[3],
        bc=BORDER_BOX,
        ix=CSS_INPUT[0],
        iy=CSS_INPUT[1],
        iw=CSS_INPUT[2],
        ih=CSS_INPUT[3],
        ic=BORDER_INPUT,
        hx=CSS_BOX[0] + CSS_BOX[2] + 40,
        hy=CSS_BOX[1] + 20,
    )
    path = os.path.join(OUT_DIR, "index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def make_assets():
    """生成三张非重复灰度噪声图，尺寸与显示尺寸 1:1（CSS 里不缩放、不重复）。"""
    os.makedirs(OUT_DIR, exist_ok=True)
    made = {}
    # 背景：只要比视口大就行（视口约 1200x710），1:1 贴到左上角，不重复不平铺
    made["bg"] = make_noise(os.path.join(OUT_DIR, "noise_bg.png"), size=(1600, 900), seed=11)
    made["box"] = make_noise(
        os.path.join(OUT_DIR, "noise_box.png"), size=(CSS_BOX[2], CSS_BOX[3]), seed=22
    )
    made["input"] = make_noise(
        os.path.join(OUT_DIR, "noise_input.png"), size=(CSS_INPUT[2], CSS_INPUT[3]), seed=33
    )
    return made


def find_chrome():
    sys.path.insert(0, HERE)
    import element_pick as ep

    return ep.find_chrome()


# --------------------------------------------------------------------------- #
# 启动 / 状态 / 关闭
# --------------------------------------------------------------------------- #
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
    # 脱离父进程启动：本工具退出后靶场窗口必须还活着（引擎会带走自己启动的子进程）
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


def scan_border(shot, color, tol=10):
    """在截图里按颜色找控件边框，返回其外接矩形 (x,y,w,h)。

    只扫纯色边框像素（内缩 1px 的表面积分），比推算 DPI 可靠得多：
    本机 100% 缩放下边框像素是完全纯净的，扫不到就说明有缩放，直接报错。

    用 numpy 整图向量化（纯 Python 双循环扫 1920x1080 要十几秒，这里 ~30ms）。
    """
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
    rects = {}
    for name, color in (("input", BORDER_INPUT), ("box", BORDER_BOX)):
        rect = scan_border(shot, color)
        rects[name] = rect
    info["rects"] = rects

    if rects.get("input") and rects.get("box"):
        ix, iy, iw, ih = rects["input"]
        bx, by, bw, bh = rects["box"]
        # bg 取输入框右侧的空白噪声区；避开 input / box 与 hint
        bgx, bgy, bgw, bgh = ix + iw + 40, iy + 40, 240, 90
        rects["bg"] = (bgx, bgy, bgw, bgh)
        if verbose:
            print("窗口标题 : {}".format(title))
            print("窗口矩形 : ({}, {}, {}x{})".format(*info["window_rect"]))
            print("屏幕     : {}x{}".format(*info["screen"]))
            print("输入框   : {}".format(rects["input"]))
            print("悬停块   : {}".format(rects["box"]))
            print("背景区   : {}".format(rects["bg"]))
            print("事件日志 : {}".format(title.split("|", 1)[1] if "|" in title else ""))
    else:
        if verbose:
            print("窗口标题 : {}".format(title))
            print("✗ 扫不到边框色。可能原因：")
            print("  ①页面没真正渲染完（等 1-2 秒重跑 --status）")
            print("  ②显示缩放不是 100%（CSS px ≠ 屏幕 px，布局常量要按比例修）")
            print("  ③窗口被其它窗口遮挡")
    return info


def main():
    ap = argparse.ArgumentParser(description="CV 测试靶场")
    ap.add_argument("--no-launch", action="store_true", help="只生成资产，不启动浏览器")
    ap.add_argument("--status", action="store_true", help="打印窗口/矩形/事件日志（只读）")
    ap.add_argument("--close", action="store_true", help="关闭靶场窗口")
    ap.add_argument("--out", default=RECTS_JSON, help="rects.json 输出路径")
    args = ap.parse_args()

    if args.status:
        info = status()
        if info.get("alive") and info.get("rects", {}).get("input"):
            os.makedirs(os.path.dirname(args.out), exist_ok=True)
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "window_rect": info["window_rect"],
                        "rects": {k: list(v) for k, v in info["rects"].items() if v},
                        "title": info["title"],
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            print("· rects.json → {}".format(args.out))
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

    os.makedirs(OUT_DIR, exist_ok=True)
    make_assets()
    page = write_html()
    print("· 页面资产 → {}".format(OUT_DIR))
    print("· 入口     → {}".format(page))

    if args.no_launch:
        return 0

    url = launch()
    print("· 已启动 {}".format(url))
    h, title = wait_title()
    if not h:
        raise SystemExit("等了 25 秒还没看到标题含「{}」的窗口".format(WIN_TITLE_TAG))
    print("✓ 窗口已就绪 hwnd={} 标题={!r}".format(h, title))
    time.sleep(1.5)  # 让首帧渲染完，扫色才准
    status()
    return 0


if __name__ == "__main__":
    sys.exit(main())
