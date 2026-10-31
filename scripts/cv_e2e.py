#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
星辰开源 · CV 全链路端到端验证（一次跑完：起靶场 → 拾取图像元素 → 发布流程 → 真跑 → 取证）
================================================================================

## 为什么必须「一条命令跑完」

从工具里启动的 GUI 进程会随**这次命令结束**被清理（job 对象收尾）。之前试过
`subprocess.Popen(detached)` 和 PowerShell `Start-Process`，命令一结束 Chrome 就没了
（`Start-Process` 那次 3 秒后还有 10 个进程，命令返回后再查是 0 个）。
WMI `Win32_Process.Create` 被安全策略直接拦。

所以把「起靶场 → 抓屏定位 → 拾取落库 → 发布流程 → 真跑 → 取证」全塞进**同一个进程**，
Chrome 作为它的子进程活到最后。这也正好符合 CV 测试的本质：靶场必须在整条链路里一直
可见。

## 靶场与证据设计

见 `cv_demo_page.py` 的模块注释。要点：
  - 噪声背景平铺 → 任何一块裁剪都足够独特，模板匹配稳定命中
  - 品红/青色纯色边框 → 抓屏扫色即可拿到控件精确像素矩形（不依赖 DPI 换算）
  - JS 把事件写进 `document.title` → **流程碰不到标题栏**，标题就是不可伪造的行为证据

## 用法

    "%APPDATA%/astron-rpa/python_core/python.exe" scripts/cv_e2e.py \
        --robot-id 2115990391806255104 \
        --flow flows/图像元素CV操作小流程.json

    # 只到「拾取落库」为止，不发布/不跑流程
    ... scripts/cv_e2e.py --stop-after pick
"""

import argparse
import io
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import cvelement_pick as cp
import cv_demo_page as page
import element_pick as ep

ROOT = os.path.dirname(HERE)
OUT_DIR = os.path.join(ROOT, "demo", "_cv_out")

# 靶场三块可操作区域 → 元素名
# inset：从「扫色得到的外框」向内收缩多少像素再取图。
#   为什么要收缩：外框上有 6px 纯彩边框，彩色像素会踩到引擎那条 BGR/RGB 灰度错位缺陷
#   （见 cv_demo_page.make_noise 注释），收缩到内部灰度噪声区即可完全规避。
TARGETS = [
    ("CV靶场_背景噪声", "bg", 0, "整页噪声背景（点击后整页刷暗红）"),
    ("CV靶场_悬停块", "box", 12, "青色边框块内部（mouseenter 触发 hue-rotate）"),
    ("CV靶场_输入框", "input", 12, "品红边框输入框内部（oninput 写标题）"),
]


def log(msg):
    print(msg, flush=True)


# --------------------------------------------------------------------------- #
# 1. 靶场
# --------------------------------------------------------------------------- #
def start_page():
    page.make_assets()
    entry = page.write_html()

    exe = page.find_chrome()
    if not exe:
        raise SystemExit("没找到 chrome.exe")
    url = "file:///" + entry.replace("\\", "/")
    cmd = [
        exe,
        "--user-data-dir=" + page.PROFILE_DIR,
        "--new-window",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        "--hide-crash-restore-bubble",
        "--window-position={},{}".format(page.WIN_X, page.WIN_Y),
        "--window-size={},{}".format(page.WIN_W, page.WIN_H),
        url,
    ]
    proc = subprocess.Popen(cmd, close_fds=True, cwd="C:\\")  # 故意不 detach：要它活到脚本结束
    h, title = page.wait_title(timeout=30)
    if not h:
        proc.kill()
        raise SystemExit("等不到靶场窗口")
    log("✓ 靶场已就绪 hwnd={} 标题={!r}".format(h, title))
    time.sleep(2.0)  # 首帧渲染 + 字体就位，扫色才准
    return proc, h


def locate(h):
    info = page.status(verbose=False)
    rects = info.get("rects") or {}
    if not (rects.get("input") and rects.get("box") and rects.get("bg")):
        raise SystemExit("✗ 扫不到靶场边框色，无法定位控件（页面没渲染 / 被遮挡 / 缩放不是 100%）")
    log("· 窗口矩形 {}  屏幕 {}".format(info["window_rect"], info["screen"]))
    for k in ("bg", "box", "input"):
        log("  {:<6} {}".format(k, rects[k]))

    # 把三块矩形画到截图上存盘：定位错了一眼就能看出来
    try:
        from PIL import ImageDraw

        shot = cp.screenshot()
        d = ImageDraw.Draw(shot)
        colors = {"bg": (0, 255, 0), "box": (255, 128, 0), "input": (0, 128, 255)}
        for k, c in colors.items():
            x, y, w, h = rects[k]
            d.rectangle([x, y, x + w, y + h], outline=c, width=3)
            d.text((x + 4, y + 2), k, fill=c)
        os.makedirs(OUT_DIR, exist_ok=True)
        p = os.path.join(OUT_DIR, "定位校验.png")
        shot.save(p)
        log("  定位标注图 → {}".format(p))
    except Exception as e:
        log("  （定位标注图生成失败：{}）".format(e))
    return rects, info


# --------------------------------------------------------------------------- #
# 2. 拾取三个图像元素（自证 → 上传 → 落库 → 回查）
# --------------------------------------------------------------------------- #
def pick_elements(robot_id, group, rects, similarity):
    shot = cp.screenshot()
    screen = (shot.width, shot.height)

    existing = {}
    for g in ep.list_groups(robot_id):
        for e in g.get("elements") or []:
            existing[e.get("name")] = e.get("id")

    made = {}
    for name, key, inset, desc in TARGETS:
        x, y, w, h = rects[key]
        x, y, w, h = x + inset, y + inset, w - 2 * inset, h - 2 * inset
        rect = (x, y, w, h)
        if x < 0 or y < 0 or x + w > screen[0] or y + h > screen[1]:
            raise SystemExit("{} 矩形越界：{} 屏幕 {}".format(name, rect, screen))
        target = shot.crop((x, y, x + w, y + h))
        ed = cp.build_element_data(target, rect, screen)

        box = cp.match_engine(ed, similarity=similarity, delay=0.4)
        if not box:
            raise SystemExit("✗ {} 自证失败：引擎全屏匹配不到".format(name))
        if not cp.rect_close(box, rect, tol=3):
            raise SystemExit(
                "✗ {} 自证失败：命中 {} ≠ 取图 {}".format(name, [int(v) for v in box], rect)
            )
        log("✓ {:<14} 自证通过 rect={} 说明：{}".format(name, rect, desc))

        # 同名元素先删掉：坐标会变（窗口位置/尺寸），旧元素留着只会点错地方
        if name in existing:
            ep.delete_element(robot_id, existing[name])
            log("  · 已删除旧元素 {}（id={}）".format(name, existing[name]))

        buf = io.BytesIO()
        target.save(buf, format="PNG")
        image_id = cp.upload_png(buf.getvalue(), "{}.png".format(name))
        ep.ensure_group(robot_id, group)
        eid = cp.create_cv_element(robot_id, group, name, ed, image_id=image_id)
        log("  · 已入库 id={} imageId={}".format(eid, image_id))

        ok, msg = cp.check_element(robot_id, eid, similarity=similarity, verbose=False)
        if not ok:
            raise SystemExit("✗ {} 入库后回查失败：{}".format(name, msg))
        log("  · 回查通过（走运行时链路 imageUrl → base64 → 匹配）")
        made[name] = eid
    return made


# --------------------------------------------------------------------------- #
# 3. 发布 + 真跑
# --------------------------------------------------------------------------- #
def publish(flow, robot_id):
    """两步：flow_generator 把规格编译成 process_content（并校验节点树），publish_flow 落库。"""
    gen = [
        sys.executable,
        os.path.join(HERE, "flow_generator.py"),
        "--spec",
        flow,
        "--robot-id",
        str(robot_id),
    ]
    log("· 编译规格 → 节点树（含 validate）…")
    r = subprocess.run(gen, cwd=ROOT, capture_output=True, text=True, timeout=300)
    for line in (r.stdout or "").strip().splitlines()[-10:]:
        log("  " + line)
    if r.returncode != 0:
        raise SystemExit("✗ 生成失败：\n{}\n{}".format(r.stdout, r.stderr))

    generated = os.path.join(ROOT, "docs", "generated_flow.json")
    cmd = [
        sys.executable,
        os.path.join(HERE, "publish_flow.py"),
        "--flow",
        generated,
        "--robot-id",
        str(robot_id),
        "--main",
    ]
    log("· 发布到 .130 …")
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=180)
    for line in (r.stdout or "").strip().splitlines()[-8:]:
        log("  " + line)
    if r.returncode != 0:
        raise SystemExit("✗ 发布失败：\n{}\n{}".format(r.stdout, r.stderr))
    return r


def run_flow(robot_id, process_id, timeout):
    cmd = [
        sys.executable,
        os.path.join(HERE, "run_flow_local.py"),
        "--robot-id",
        robot_id,
        "--timeout",
        str(timeout),
        "--show-log",
    ]
    if process_id:
        cmd += ["--process-id", str(process_id)]
    log("· 本地真跑流程…")
    t0 = time.time()
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout + 120)
    return r, time.time() - t0


# --------------------------------------------------------------------------- #
# 4. 取证
# --------------------------------------------------------------------------- #
def evidence(h, rects):
    import win32gui

    out = {}
    title = win32gui.GetWindowText(h) if win32gui.IsWindow(h) else "(窗口已关闭)"
    out["title"] = title
    log("· 窗口标题：{}".format(title))

    try:
        shot = cp.screenshot()
        px = shot.load()
        W, H = shot.size
        # 采样点都从「扫色得到的真实矩形」推算，不写死坐标
        bgx, bgy, bgw, bgh = rects["bg"]
        ix, iy, iw, ih = rects["input"]
        points = {
            "背景区中心": (bgx + bgw // 2, bgy + bgh // 2),
            "背景区左上": (bgx + 8, bgy + 8),
            "输入框下方": (bgx - 200, iy + ih + 60),
        }
        samples = {}
        for label, (sx, sy) in points.items():
            if 0 <= sx < W and 0 <= sy < H:
                samples[label] = tuple(px[sx, sy][:3])
        out["samples"] = samples
        for k, v in samples.items():
            log("  像素 {} = RGB{}".format(k, v))
        log(
            "  判读：背景区若为 (176,0,32) 左右 = JS 的 click 处理器真的被触发了（整页刷成 #B00020）"
        )
        shot.save(os.path.join(OUT_DIR, "CV最终状态.png"))
        log("  最终状态截图 → {}".format(os.path.join(OUT_DIR, "CV最终状态.png")))
        still = page.scan_border(shot, page.BORDER_INPUT)
        log("  品红边框仍可扫到：{}".format("是 " + str(still) if still else "否（页面已变）"))
        out["border_visible"] = bool(still)

        # —— CV.hover_image 的视觉证据 ——
        # 靶场页的 mouseenter 处理器把悬停块边框从青色 #00FFFF 改成绿色 #00FF88，
        # 这是**页面自己**改的 DOM 样式，流程无法直接触碰，只能靠抓屏取色验证。
        bx, by, bw, bh = rects["box"]
        hovered = page.scan_border(shot, page.BORDER_BOX_HOVERED)
        out["hover_border"] = bool(hovered)
        out["hover_border_rect"] = hovered
        log(
            "  悬停块边框绿色(#00FF88) 扫到：{} {}".format(
                "是" if hovered else "否", hovered if hovered else ""
            )
        )
        # 同时记录边框像素本身，方便人工核对
        edge_samples = {}
        for label, (sx, sy) in {
            "悬停块左边框": (bx + 1, by + bh // 2),
            "悬停块上边框": (bx + bw // 2, by + 1),
            "悬停块右边框": (bx + bw - 2, by + bh // 2),
        }.items():
            if 0 <= sx < W and 0 <= sy < H:
                edge_samples[label] = tuple(px[sx, sy][:3])
        for k, v in edge_samples.items():
            log("  像素 {} = RGB{}".format(k, v))
        out["box_edge_pixels"] = edge_samples
    except Exception as e:
        log("  （抓屏失败，跳过像素取证：{}）".format(e))

    for fn in ("CV取证_0_开始.txt", "CV取证_1_等待结果.txt", "CV取证.txt"):
        p = os.path.join(OUT_DIR, fn)
        out[fn] = os.path.exists(p)
    return out


def debug_match(rects):
    """诊断：同一块裁剪，在两帧之间到底像不像。直接给 cv2 的分数与位置。"""
    import cv2
    import numpy as np

    a = cp.screenshot()
    time.sleep(0.5)
    b = cp.screenshot()
    print("  帧A {} 帧B {}  两帧是否逐像素相同: {}".format(
        a.size, b.size, "是" if np.array_equal(np.asarray(a), np.asarray(b)) else "否"
    ))
    ga = cv2.cvtColor(np.asarray(a), cv2.COLOR_RGB2BGR)
    gb = cv2.cvtColor(np.asarray(b), cv2.COLOR_RGB2BGR)
    ga = cv2.cvtColor(ga, cv2.COLOR_BGR2GRAY)
    gb = cv2.cvtColor(gb, cv2.COLOR_BGR2GRAY)
    for name, key, inset, _ in TARGETS:
        x, y, w, h = rects[key]
        x, y, w, h = x + inset, y + inset, w - 2 * inset, h - 2 * inset
        tpl = ga[y : y + h, x : x + w]
        if tpl.size == 0:
            print("  {:<14} 裁剪为空".format(name))
            continue
        res = cv2.matchTemplate(gb, tpl, cv2.TM_CCOEFF_NORMED)
        _, mx, _, loc = cv2.minMaxLoc(res)
        print(
            "  {:<14} rect={} 帧B最高分={:.4f} @{}".format(name, (x, y, w, h), mx, loc)
        )
        cp.build_element_data  # noqa: B018  仅为提示下面存图用的是同一块
        from PIL import Image

        Image.fromarray(tpl).save(os.path.join(OUT_DIR, "tpl_{}.png".format(key)))

        # 再走一遍引擎真实入口，看它到底返回什么
        pil = a.crop((x, y, x + w, y + h))
        ed = cp.build_element_data(pil, (x, y, w, h), (a.width, a.height))
        got = cp.match_engine(ed, similarity=0.95, delay=0.2)
        print("  {:<14} cp.match_engine → {}".format(name, got))
        print(
            "  {:<14} elementData: img.self 长度={} sr={} pos=({},{})".format(
                name,
                len(ed["img"]["self"]),
                ed["sr"],
                ed["pos"]["self_x"],
                ed["pos"]["self_y"],
            )
        )


def main():
    ap = argparse.ArgumentParser(description="CV 全链路端到端验证")
    ap.add_argument("--robot-id", default="2115990391806255104")
    ap.add_argument("--group", default="图像元素")
    ap.add_argument("--flow", default=os.path.join(ROOT, "flows", "图像元素CV操作小流程.json"))
    ap.add_argument("--similarity", type=float, default=0.95)
    ap.add_argument(
        "--stop-after", choices=["pick", "publish"], default=None, help="只跑到某一步"
    )
    ap.add_argument("--timeout", type=int, default=240)
    ap.add_argument("--debug-match", action="store_true", help="只跑匹配诊断（不落库）")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    # 清掉上一轮取证，避免「旧文件冒充新证据」
    for fn in ("CV取证_0_开始.txt", "CV取证_1_等待结果.txt", "CV取证.txt"):
        p = os.path.join(OUT_DIR, fn)
        if os.path.exists(p):
            os.remove(p)

    proc, h = start_page()
    try:
        rects, info = locate(h)
        if args.debug_match:
            debug_match(rects)
            return 0
        made = pick_elements(args.robot_id, args.group, rects, args.similarity)
        log("· 本轮元素：{}".format(json.dumps(made, ensure_ascii=False)))
        if args.stop_after == "pick":
            log("（按 --stop-after pick 停止）")
            return 0

        publish(args.flow, args.robot_id)
        if args.stop_after == "publish":
            log("（按 --stop-after publish 停止）")
            return 0

        r, elapsed = run_flow(args.robot_id, "", args.timeout)
        log("· 耗时 {:.1f}s  exit={}".format(elapsed, r.returncode))
        tail = (r.stdout or "").strip().splitlines()
        for line in tail[-25:]:
            log("  " + line)
        if r.returncode != 0:
            log("--- stderr ---")
            log((r.stderr or "")[-1500:])

        log("\n=== 取证 ===")
        ev = evidence(h, rects)
        log("\n证据清单：{}".format(json.dumps(ev, ensure_ascii=False, indent=2)))
        return 0 if r.returncode == 0 else 1
    finally:
        # 优雅关闭：直接 terminate 会让 Chrome 下次启动弹「要恢复页面吗」，气泡会盖住页面右上角
        try:
            import win32con
            import win32gui

            if win32gui.IsWindow(h):
                win32gui.PostMessage(h, win32con.WM_CLOSE, 0, 0)
                for _ in range(20):
                    if proc.poll() is not None:
                        break
                    time.sleep(0.1)
            if proc.poll() is None:
                proc.terminate()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
