#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
星辰开源 · 图像元素（CV）自动拾取器
====================================

解决的真问题：`CV.*` 那 5 个原子（点击图片 / 悬停图片 / 图像是否存在 / 等待图像 /
图像输入）的入参是 `IMGPick`，元素类型 `type:"cv"`。设计器里必须「框选屏幕一块区域」
才能生成它，AI 无法独立完成 → 这条支线一直测不了。

本脚本把「框选」降维成「给一个矩形」，并且**落库前先用引擎自己的匹配代码自证**。

## 逆向事实（都来自本机代码，不是猜的）

1. 拾取端数据结构 —— `vision_picker/core/core.py:54 json_res()`：
       {version:"1", type:"cv", app:"", path:"",
        img:{self:<目标图 base64 裸串>, parent:<锚点图 base64>},
        pos:{self_x,self_y,parent_x,parent_y},
        sr:{screen_w,screen_h}, picker_type:"ELEMENT"}
   base64 **不带** `data:image/png;base64,` 前缀（`IPickCore.image_to_base64`）。

2. 运行时**不看** `elementData.img` —— `workflowlib/storage.py:140-146`：
       if element_data.get("type") == "cv":
           element_data["img"]["self"]   = element_vision_detail(res["imageUrl"])
           element_data["img"]["parent"] = element_vision_detail(res["parentImageUrl"])
   也就是说 cv 元素的图**必须真上传**（imageId / parentImageId），
   `elementData.img` 里的 base64 只是设计器预览用，运行时会被覆盖。
   → 落库时先 `POST /api/resource/file/upload`（multipart，字段名 `file`）拿 fileId。

3. 匹配算法 —— `vision/cv_match.py:208-222`：
   `img.parent` 为空时走**全屏模板匹配** `cv2.matchTemplate(gray, tpl, TM_CCOEFF_NORMED)`，
   `pos` 完全不参与；只有带锚点时才用 `pos` 算相对位移 + ROI 搜索。
   → 所以自证是**非循环**的：另截一张新图去匹配，能命中同一矩形才算真成立。

4. `type:"cv"` **不能**走 `locator.locator()` —— `locator/locator.py:146`
   直接 `self.locator_handler["cv"]` 会 KeyError（只注册了 uia/web/msaa/jab/sap）。
   cv 元素只能被 `CvCore.match_imgs` 消费。

## 用法

    # 0. 看看屏幕多大、当前前台窗口在哪（挑矩形用）
    python cvelement_pick.py --info

    # 1. 只验证不落库（推荐先跑）：矩形 x,y,w,h 是**屏幕绝对坐标**
    python cvelement_pick.py --rect 100,200,300,80 --probe

    # 2. 按窗口相对坐标取图（不用自己算绝对坐标）
    python cvelement_pick.py --window "远程桌面连接" --rel 40,60,240,40 --probe

    # 3. 真正落库（自动上传图 → create → 回查）
    python cvelement_pick.py --rect 100,200,300,80 --save --name 帮助按钮图 --group 图像元素

    # 4. 校验库里已有的 cv 元素（走运行时的真实链路：imageUrl → base64 → 匹配）
    python cvelement_pick.py --check 2116439143347593216

## 安全约束

- `--info` / `--probe` / `--check` 只读，不写库。
- 只有 `--save` 会写（上传图片 + 建元素），并且先自证、自证不过直接拒绝。
- 全程不碰流程节点树、不动 API Key、不动 Casdoor。
- 元素按 robotId 隔离，必须与将来跑流程的机器人一致（默认沿用 element_pick 的默认值）。

## 已知副作用（引擎自身的行为，非本脚本引入）

`CvCore.match_imgs` 会把 `desktop.png` 和 `desktop_filepath_match.png` 写到**当前工作目录**。
本脚本调用前后会 chdir 到临时目录，避免污染仓库；但流程真跑时这两个文件会落在
引擎进程的 CWD（报告里单独记录）。
"""

import argparse
import base64
import io
import json
import os
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import element_pick as ep

DEFAULT_ROBOT_ID = "2115990391806255104"  # 与桌面元素同一台机器人
DEFAULT_GROUP = "图像元素"


# --------------------------------------------------------------------------- #
# 截图 / 取图
# --------------------------------------------------------------------------- #
def screenshot():
    """与引擎完全一样的取图方式（vision/core.py:44 pyautogui.screenshot(region=None)）。"""
    import pyautogui

    pyautogui.FAILSAFE = False
    try:
        return pyautogui.screenshot(region=None)
    except OSError as e:
        raise SystemExit(
            "✗ 抓屏失败：{}\n"
            "  最常见原因：**当前 Windows 会话被锁定**（锁屏/无人登录）。\n"
            "  自查：`LogonUI.exe` 在跑 + `GetForegroundWindow()` 返回 0 就是锁屏。\n"
            "  锁屏时 GDI 抓屏会被拒绝，而 CV 匹配整条链路都依赖真屏 —— 先解锁再跑。".format(e)
        )


def window_rect(title):
    """按标题子串找可见窗口，返回 (hwnd, (x, y, w, h))。"""
    import win32gui

    hits = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        if title in win32gui.GetWindowText(hwnd):
            hits.append(hwnd)

    win32gui.EnumWindows(cb, None)
    if not hits:
        return None, None
    hwnd = hits[0]
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    return hwnd, (left, top, right - left, bottom - top)


# --------------------------------------------------------------------------- #
# elementData 组装（严格对齐 json_res）
# --------------------------------------------------------------------------- #
def img_to_b64(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def b64_to_img(b64):
    from PIL import Image

    return Image.open(io.BytesIO(base64.b64decode(b64)))


def build_element_data(target_img, rect, screen, anchor_img=None, anchor_rect=None):
    """rect = (x, y, w, h) 屏幕绝对坐标；与设计器拾取产出的字段一一对应。"""
    x, y = rect[0], rect[1]
    if anchor_img is not None and anchor_rect is not None:
        ax, ay = anchor_rect[0], anchor_rect[1]
    else:
        ax = ay = ""
    return {
        "version": "1",
        "type": "cv",
        "app": "",
        "path": "",
        "img": {
            "self": img_to_b64(target_img),
            "parent": img_to_b64(anchor_img) if anchor_img is not None else "",
        },
        "pos": {
            "self_x": x,
            "self_y": y,
            "parent_x": ax,
            "parent_y": ay,
        },
        "sr": {"screen_w": screen[0], "screen_h": screen[1]},
        "picker_type": "ELEMENT",
    }


# --------------------------------------------------------------------------- #
# 自证：用引擎自己的匹配代码，在**新的**一张截图上找回来
# --------------------------------------------------------------------------- #
def match_engine(element_data, similarity=0.95, fresh_shot=True, delay=0.35):
    """直接调 `CvCore.match_imgs`（原子内部用的就是它）。

    会在临时目录里跑，避免把 desktop.png / desktop_filepath_match.png 写进仓库。
    返回 match_box 或 None。
    """
    from astronverse.vision.core import CvCore

    old = os.getcwd()
    tmp = tempfile.mkdtemp(prefix="cvpick_")
    try:
        os.chdir(tmp)
        if fresh_shot:
            time.sleep(delay)  # 让屏幕状态稳定，确保匹配的不是「刚截的那一张」
        return CvCore.match_imgs({"elementData": element_data}, match_similarity=similarity)
    finally:
        os.chdir(old)


def rect_close(a, b, tol=2):
    if not a or not b:
        return False
    return all(abs(int(a[i]) - int(b[i])) <= tol for i in range(4))


# --------------------------------------------------------------------------- #
# 平台接口：上传图片 + 建元素
# --------------------------------------------------------------------------- #
def upload_png(png_bytes, filename="image.png"):
    """POST /api/resource/file/upload（multipart，字段名 file）→ fileId。

    路径来自渲染进程 `resource-*.js`：`t.postFormData('/api/resource/file/upload', formData)`；
    设计器建 cv 元素时就是 `imageId = 上传(img.self).fileId`。
    """
    boundary = "----WBPICK" + uuid.uuid4().hex
    body = io.BytesIO()

    def w(s):
        body.write(s.encode("utf-8") if isinstance(s, str) else s)

    w("--{}\r\n".format(boundary))
    w('Content-Disposition: form-data; name="file"; filename="{}"\r\n'.format(filename))
    w("Content-Type: image/png\r\n\r\n")
    w(png_bytes)
    w("\r\n--{}--\r\n".format(boundary))
    data = body.getvalue()

    url = "http://{}/api/resource/file/upload".format(ep.cfg.load()["gateway"]["host"])
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "multipart/form-data; boundary={}".format(boundary),
            "Content-Length": str(len(data)),
            "Cookie": ep._cookie(),
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        text = r.read().decode("utf-8")
    try:
        res = json.loads(text)
    except Exception:
        raise SystemExit("上传接口返回非 JSON：{}".format(text[:300]))
    if res.get("code") != "000000":
        raise SystemExit("上传图片失败：{} {}".format(res.get("code"), res.get("message")))
    data = res.get("data")
    # 实测：`data` 直接就是 fileId 字符串（雪花 id），不是 {fileId: ...} 对象；
    # 但不同版本可能改结构，两种都认。
    if isinstance(data, str) and data:
        return data
    if isinstance(data, dict):
        fid = data.get("fileId") or data.get("id") or data.get("file_id")
        if fid:
            return str(fid)
    raise SystemExit("上传成功但拿不到 fileId：{}".format(json.dumps(res, ensure_ascii=False)[:300]))


def create_cv_element(robot_id, group, name, element_data, image_id="", parent_image_id=""):
    body = {
        "type": "common",
        "robotId": robot_id,
        "groupName": group,
        "element": {
            "commonSubType": "single",
            "name": name,
            "icon": "",
            "imageId": image_id,
            "parentImageId": parent_image_id,
            "elementData": json.dumps(element_data, ensure_ascii=False),
        },
    }
    data = ep.api_ok(ep.api("/api/robot/element/create", body), "写元素库")
    return (data or {}).get("elementId")


# --------------------------------------------------------------------------- #
# 校验：复刻运行时链路 storage.element_detail → CvCore.match_imgs
# --------------------------------------------------------------------------- #
def fetch_image_b64(url):
    """复刻 `storage.element_vision_detail`：GET url，取 data 字段（已是 base64）。"""
    if not url:
        return ""
    full = url if url.startswith("http") else "http://{}{}".format(ep.cfg.load()["gateway"]["host"], url)
    req = urllib.request.Request(full, headers={"Cookie": ep._cookie()})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
    try:
        j = json.loads(raw.decode("utf-8"))
        if isinstance(j, dict) and "code" in j:
            return j.get("data") or ""
        return raw.decode("utf-8") if False else base64.b64encode(raw).decode("utf-8")
    except Exception:
        return base64.b64encode(raw).decode("utf-8")


def check_element(robot_id, element_id, similarity=0.95, verbose=True):
    ok_meta = ep.find_element(robot_id, element_id)
    if not ok_meta[1]:
        return False, "元素 {} 不在机器人 {} 元素库里".format(element_id, robot_id)
    meta = ok_meta[1]

    d = ep.api_ok(
        ep.api("/api/robot/element/detail", params={"robotId": robot_id, "elementId": element_id}),
        "读元素详情",
    )
    ed = json.loads(d.get("elementData") or "{}")
    if ed.get("type") != "cv":
        return False, "该元素不是图像元素（type={}）".format(ed.get("type"))

    # 完全按 storage.element_detail 的做法还原 img
    self_b64 = fetch_image_b64(d.get("imageUrl"))
    parent_b64 = fetch_image_b64(d.get("parentImageUrl"))
    if not self_b64:
        return False, "imageUrl 为空或取不到内容 —— 该 cv 元素没上传过图，运行时必然匹配不到"
    ed.setdefault("img", {})
    ed["img"]["self"] = self_b64
    ed["img"]["parent"] = parent_b64 or ""
    if not ed["img"]["parent"]:
        ed["pos"]["parent_x"] = ""
        ed["pos"]["parent_y"] = ""

    box = match_engine(ed, similarity=similarity)
    if not box:
        return False, "引擎匹配不到（相似度阈值 {}）".format(similarity)
    if verbose:
        print(
            "  命中 rect=(x={}, y={}, w={}, h={})；库内记录 pos=({}, {})".format(
                *[int(v) for v in box], ed["pos"].get("self_x"), ed["pos"].get("self_y")
            )
        )
    return True, "引擎匹配命中"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def resolve_rect(args):
    """把 --rect / --window+--rel 统一成屏幕绝对矩形。"""
    if args.rect:
        parts = [int(float(v)) for v in args.rect.split(",")]
        if len(parts) != 4:
            raise SystemExit("--rect 需要 x,y,w,h 四个整数")
        return tuple(parts), None
    if args.window:
        hwnd, wr = window_rect(args.window)
        if not hwnd:
            raise SystemExit("找不到标题包含「{}」的可见窗口".format(args.window))
        if not args.rel:
            raise SystemExit("--window 需要配合 --rel x,y,w,h（相对窗口左上角）")
        parts = [int(float(v)) for v in args.rel.split(",")]
        if len(parts) != 4:
            raise SystemExit("--rel 需要 x,y,w,h 四个整数")
        return (wr[0] + parts[0], wr[1] + parts[1], parts[2], parts[3]), wr
    raise SystemExit("需要 --rect 或 --window+--rel（或用 --info/--check）")


def cmd_info():
    import win32api

    shot = screenshot()
    print("屏幕（pyautogui 截图）: {}x{}".format(shot.width, shot.height))
    try:
        print(
            "屏幕（win32 逻辑像素）: {}x{}".format(
                win32api.GetSystemMetrics(0), win32api.GetSystemMetrics(1)
            )
        )
    except Exception as e:
        print("win32 尺寸取不到：{}".format(e))
    import win32gui

    h = win32gui.GetForegroundWindow()
    l, t, r, b = win32gui.GetWindowRect(h)
    print(
        "前台窗口: {!r}  hwnd={}  rect=({}, {}, {}x{})".format(
            win32gui.GetWindowText(h), h, l, t, r - l, b - t
        )
    )
    return 0


def cmd_pick(args, do_save):
    rect, win_rect = resolve_rect(args)
    shot = screenshot()
    screen = (shot.width, shot.height)
    x, y, w, h = rect
    if w <= 4 or h <= 4:
        raise SystemExit("矩形太小（{}x{}），模板匹配不可靠".format(w, h))
    if x < 0 or y < 0 or x + w > screen[0] or y + h > screen[1]:
        raise SystemExit(
            "矩形超出屏幕：rect={} 屏幕={}x{}".format(rect, screen[0], screen[1])
        )

    target = shot.crop((x, y, x + w, y + h))
    ed = build_element_data(target, rect, screen)
    print("· 取图 rect=({}, {}, {}x{})  屏幕={}x{}".format(x, y, w, h, screen[0], screen[1]))
    if win_rect:
        print("  窗口矩形=({}, {}, {}x{})".format(*win_rect))

    # ---- 自证 ----
    print("· 自证：用 CvCore.match_imgs 在新截图上找回来…")
    box = match_engine(ed, similarity=args.similarity)
    if not box:
        raise SystemExit(
            "✗ 自证失败：引擎在全屏里匹配不到这块图（相似度 {}）。\n"
            "  常见原因：①矩形取到了空白/纯色区域 ②取图后界面变了 ③矩形太小。\n"
            "  换个更有辨识度的区域重试。".format(args.similarity)
        )
    same = rect_close(box, rect, tol=args.tol)
    print(
        "  匹配到 ({}, {}, {}, {})，与目标{}".format(
            *[int(v) for v in box], "一致 ✓" if same else "不一致 ✗（差 {}）".format(
                [int(box[i]) - int(rect[i]) for i in range(4)]
            )
        )
    )
    if not same and not args.allow_shift:
        raise SystemExit(
            "✗ 自证失败：命中的不是取图位置本身。\n"
            "  说明这块图在屏幕上出现了多处（模板匹配取分值最高的一处），"
            "落库后会点错地方。\n"
            "  换一块更独特的区域，或加 --allow-shift 明确接受。"
        )

    if not do_save:
        png = os.path.join(args.out_dir, "cv_probe.png")
        os.makedirs(args.out_dir, exist_ok=True)
        target.save(png)
        print("✓ 自证通过（未落库）。取图已存：{}".format(png))
        print("  加 --save --name <名字> 即可写入元素库")
        return 0

    if not args.name:
        raise SystemExit("--save 必须同时给 --name")

    # ---- 上传 + 落库 ----
    buf = io.BytesIO()
    target.save(buf, format="PNG")
    print("· 上传目标图到平台…")
    image_id = upload_png(buf.getvalue(), "{}.png".format(args.name))
    print("  imageId={}".format(image_id))

    ep.ensure_group(args.robot_id, args.group)
    eid = create_cv_element(args.robot_id, args.group, args.name, ed, image_id=image_id)
    print("✓ 已写入元素库：name={} id={} group={}".format(args.name, eid, args.group))

    print("· 回查（走运行时真实链路 imageUrl → base64 → 匹配）…")
    ok, msg = check_element(args.robot_id, eid, similarity=args.similarity)
    if ok:
        print("✓ 校验通过：{}".format(msg))
        print("\n元素 id = {}（流程节点 input_data 用这个 id）".format(eid))
        return 0
    print("✗ 校验未通过：{}".format(msg))
    return 1


def main():
    ap = argparse.ArgumentParser(
        description="星辰开源 · 图像元素（CV）自动拾取器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--info", action="store_true", help="打印屏幕尺寸与前台窗口（只读）")
    ap.add_argument("--rect", help="屏幕绝对坐标 x,y,w,h")
    ap.add_argument("--window", help="窗口标题子串（配合 --rel 用相对坐标）")
    ap.add_argument("--rel", help="相对窗口左上角的 x,y,w,h")
    ap.add_argument("--probe", action="store_true", help="只自证，不落库（默认行为）")
    ap.add_argument("--save", action="store_true", help="自证通过后写入元素库")
    ap.add_argument("--name", help="元素名称（--save 必填）")
    ap.add_argument("--robot-id", default=DEFAULT_ROBOT_ID)
    ap.add_argument("--group", default=DEFAULT_GROUP)
    ap.add_argument("--check", metavar="ELEMENT_ID", help="校验已有 cv 元素（只读）")
    ap.add_argument("--similarity", type=float, default=0.95, help="匹配相似度阈值，默认 0.95")
    ap.add_argument("--tol", type=int, default=2, help="自证允许的像素偏差，默认 2")
    ap.add_argument("--allow-shift", action="store_true", help="命中位置与取图位置不同也接受")
    ap.add_argument("--out-dir", default=os.path.join(os.path.dirname(HERE), "demo", "_cv_out"))
    args = ap.parse_args()

    if args.info:
        return cmd_info()
    if args.check:
        ok, msg = check_element(args.robot_id, args.check, similarity=args.similarity)
        print(("✓ " if ok else "✗ ") + msg)
        return 0 if ok else 1
    return cmd_pick(args, do_save=args.save)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
