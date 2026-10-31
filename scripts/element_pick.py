#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
星辰开源 · 元素自动拾取器（Element Picker）
=============================================

解决的真问题：RPA 网页流程必须先在设计器里**人工点选**元素，AI 无法独立完成
「生成流程 → 校验 → 真跑」的闭环。本脚本让 AI 用一条命令把元素写进元素库。

## 原理（全部来自本机逆向实证，非猜测）

1. 设计器的网页元素定位**完全交给浏览器插件**：
   `astronverse/locator/core/web_locator.py:77` → `POST http://127.0.0.1:9082/browser/transition`
   body `{browser_type, data: <path>, key: "checkElement"}`。
   也就是说：**元素能不能被找到，只取决于 path 对象**，与「谁生成它」无关。

2. 插件 `content.js` 里 `formatElementInfo(el)` 会一次性产出
   `cssSelector + pathDirs + xpath + tag + text`，这正是设计器人工拾取时
   存进 `c_element.element_data` 的 `path` 字段。它对外暴露为动作
   **`elementFromSelect`** —— 传一个 cssSelector 进去，返回完整的拾取结果。
   于是「人工点选」被降维成「给一个选择器」。

3. 落库走官方 HTTP 接口（body 结构取自渲染进程 `usePickStore-*.js` 的调用方）：
   `POST /api/robot/element/create`
   `{type:"common", robotId, groupName, element:{commonSubType:"single",
     name, icon:"", imageId:"", parentImageId:"", elementData}}`
   → `{elementId, groupId}`。图片非必填，传空串即可（元素仍可定位）。

4. `elementData` 里 `xpath/cssSelector/url/attr.value` 都包了一层
   `{"rpa":"special","value":[{"type":"other","value":"..."}]}`。这层包装是
   「允许插变量」的语法糖，运行时由 `workflowlib/params.py:127`
   `_recursive_convert_params` 递归解包成纯字符串。本脚本按真实存储格式写入，
   保证设计器再次打开元素时不会显示异常。

## 工作流

    # 1. 看桥接与当前标签页状态
    python element_pick.py --status

    # 2. 只探测不落库（安全，推荐先跑）
    python element_pick.py --probe "#kw" --url https://www.baidu.com

    # 3. 真正写入元素库
    python element_pick.py --css "#kw" --name 搜索框 --url https://www.baidu.com

    # 4. 校验已有元素当前是否还能命中（不改任何数据）
    python element_pick.py --check 1979487552478244864

    # 5. 清理
    python element_pick.py --delete 1979487552478244864

## 安全约束

- `--probe` 只读，不写库。
- `--check` 只读，不写库。
- 只有 `--css/--xpath`（带 `--name`）和 `--delete` 会改数据，且都打印结果 id。
- 不碰流程节点树、不动 API Key、不动 Casdoor。
- 元素按 robotId 隔离：**必须与将来运行该流程的机器人一致**，否则运行时
  `element_detail(project_id=robotId, elementId)` 取不到（这是最容易踩的坑）。
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import rpa_config as cfg

# 浏览器插件桥接默认端口（astronverse/browser_bridge/config.py:18）
BRIDGE_PORT = 9082
DEFAULT_ROBOT_ID = "2065231113695461380"
DEFAULT_GROUP = "chrome"
DEFAULT_BROWSER = "chrome"

# 设计器把窗口类名固定为 Chrome_WidgetWin_1（见 locator/__init__.py BROWSER_UIA_WINDOW_CLASS）
CHROME_WINDOW_CLASS = "Chrome_WidgetWin_1"
CHROME_WINDOW_SUFFIX = " - Google Chrome"


# --------------------------------------------------------------------------- #
# 通道一：浏览器插件桥接（读页面、定位元素）
# --------------------------------------------------------------------------- #
def _post_json(url, payload, timeout=20):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def bridge(key, data=None, browser_type=DEFAULT_BROWSER, timeout=15):
    """调浏览器插件动作。返回插件的 `data` 段；失败抛 RuntimeError。

    优先直连 9082（桥接本体）；若被占用/未起，退化到设计器本地网关
    `13159/browser_connector/browser/transition`（运行时走的就是这条路）。
    """
    payload = {
        "browser_type": browser_type,
        "data": data or {},
        "key": key,
        "time_out": timeout,
    }
    candidates = ["http://127.0.0.1:{}/browser/transition".format(BRIDGE_PORT)]
    try:
        candidates.append(
            "http://127.0.0.1:{}/browser_connector/browser/transition".format(
                cfg.local_gateway_port()
            )
        )
    except Exception:
        pass

    last = None
    for url in candidates:
        try:
            raw = _post_json(url, payload, timeout=timeout + 5)
        except Exception as e:  # 连接层失败 → 换下一个通道
            last = e
            continue
        if raw.get("code") != "0000":
            last = RuntimeError("桥接拒绝请求：{}".format(raw.get("msg")))
            continue
        inner = raw.get("data") or {}
        code = inner.get("code")
        if code == "0000":
            return inner.get("data")
        if code == "5002":
            raise RuntimeError("元素未找到：{}".format(inner.get("msg")))
        if code == "5004":
            raise RuntimeError(
                "插件不支持该动作「{}」（{}），请升级设计器内置插件".format(key, inner.get("msg"))
            )
        raise RuntimeError("插件返回错误 {}：{}".format(code, inner.get("msg")))

    raise RuntimeError(
        "连不上浏览器插件桥接（已试 {}）。\n"
        "  原因通常是：①设计器没开 ②Chrome 没装/没启用 RPA 插件 ③浏览器没启动。\n"
        "  底层错误：{}".format("、".join(candidates), last)
    )


def browser_status():
    """当前活动标签页的 url / title / tabId。"""
    return {
        "url": bridge("getUrl"),
        "title": bridge("getTitle"),
        "tab_id": bridge("getTabId"),
    }


# --------------------------------------------------------------------------- #
# 打开页面（元素只能在「当前活动标签页」上拾取）
# --------------------------------------------------------------------------- #
def find_chrome():
    """定位 chrome.exe：注册表 App Paths → 常见安装位。"""
    try:
        import winreg

        keys = [
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
        ]
        for root, sub in keys:
            try:
                with winreg.OpenKey(root, sub) as k:
                    p = winreg.QueryValueEx(k, "")[0]
                if p and os.path.exists(p):
                    return p
            except OSError:
                continue
    except ImportError:
        pass

    for p in (
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ):
        if os.path.exists(p):
            return p
    return ""


def open_url(url, timeout=25, browser_type=DEFAULT_BROWSER):
    """把浏览器导航到 url，并等到插件报告的就是这个页面。

    插件本身没有 navigate 动作（background.js 只有 getUrl/getTitle/closeTab），
    设计器 `browser_open` 的做法是「带 URL 启动浏览器进程」（browser_software.py:114）。
    这里照抄同一套做法，保证行为与设计器一致。
    """
    if browser_type != "chrome":
        raise SystemExit("当前只实现了 chrome 的导航；其它浏览器请在 --url 省略后手工切页面。")

    exe = find_chrome()
    if not exe:
        raise SystemExit("没找到 chrome.exe，请用 --no-open 并手工把浏览器切到目标页")

    # --new-window 保证新页面成为活动标签页（拾取只认活动标签页）
    subprocess.Popen([exe, url, "--new-window"], close_fds=True)
    return wait_for_url(url, timeout=timeout)


def _same_page(a, b):
    """宽松比对：忽略末尾斜杠、忽略 query 顺序差异。"""
    try:
        pa, pb = urllib.parse.urlsplit(a), urllib.parse.urlsplit(b)
        return (pa.netloc.lower() == pb.netloc.lower()) and (
            pa.path.rstrip("/") == pb.path.rstrip("/")
        )
    except Exception:
        return a == b


def wait_for_url(url, timeout=25, interval=0.8):
    """轮询到活动标签页变成目标页；返回最终的 (url, title)。"""
    deadline = time.time() + timeout
    seen = ""
    while time.time() < deadline:
        try:
            seen = bridge("getUrl", timeout=5)
        except Exception:
            time.sleep(interval)
            continue
        if _same_page(seen, url):
            title = ""
            try:
                title = bridge("getTitle", timeout=5)
            except Exception:
                pass
            return seen, title
        time.sleep(interval)
    raise SystemExit(
        "等不到目标页面成为活动标签页。\n"
        "  期望：{}\n  当前：{}\n"
        "  请确认浏览器已装上 RPA 插件、且没有被其它窗口抢走焦点；\n"
        "  也可以手工把标签页切过去后加 --no-open 重跑。".format(url, seen or "(未知)")
    )


# --------------------------------------------------------------------------- #
# 拾取：cssSelector/xpath → 完整 path 对象
# --------------------------------------------------------------------------- #
def probe(selector, by="css", url=None, title=None, visible=True, wait=12):
    """让插件按选择器找元素并产出拾取器原生结构（只读，不落库）。

    `visible=True` 时把 `filterVisible` 交给插件，插件会剔除 display/visibility
    隐藏的节点。**这个开关是必须的**：现代站点普遍把旧版 DOM 留成隐藏节点
    （百度首页的 `form#form` / `input#kw` 就是典型），`querySelector` 命中
    第一个匹配往往正是那个隐藏的，存进元素库后运行时点不到任何东西。
    """
    if by == "xpath":
        payload = {"xpath": selector, "checkType": "customization"}
    else:
        payload = {"cssSelector": selector, "checkType": "customization"}
    if visible:
        payload["filterVisible"] = True

    # domain/url 会被插件原样回填进结果（content.js elementFromSelect 开头解构）
    try:
        cur = bridge("getUrl", timeout=5)
    except Exception:
        cur = url or ""
    payload["url"] = url or cur
    payload["domain"] = urllib.parse.urlsplit(payload["url"]).netloc

    # JS 渲染的页面常常「URL 已到、DOM 未到」，重试几轮比直接报错靠谱（坑 127）
    deadline = time.time() + wait
    last = None
    while True:
        try:
            res = bridge("elementFromSelect", payload)
            items = res if isinstance(res, list) else [res]
            items = [x for x in items if isinstance(x, dict)]
            if items:
                return items
            last = RuntimeError("选择器没命中任何元素：{}".format(selector))
        except Exception as e:
            last = e
        if time.time() >= deadline:
            break
        time.sleep(0.8)

    raise last if last else RuntimeError("拾取失败：{}".format(selector))


def rect_visible(rect):
    """判断插件返回的 rect 是不是「真的占位置」。

    坑 125 实测：不可见节点的 rect 是 `left:0,top:0,width:8,height:8,right:0,bottom:0`
    —— width/height 有值但 right/bottom 是 0，自相矛盾。所以不能用 width>0 判断，
    必须用 `right-left` 与 `bottom-top`。
    """
    if not rect:
        return False
    try:
        return (float(rect.get("right", 0)) - float(rect.get("left", 0))) > 0 and (
            float(rect.get("bottom", 0)) - float(rect.get("top", 0))
        ) > 0
    except (TypeError, ValueError):
        return False


# --------------------------------------------------------------------------- #
# 存储格式转换
# --------------------------------------------------------------------------- #
def wrap_special(v):
    """普通字符串 → 元素库里的「可插变量」包装格式。"""
    if isinstance(v, dict) and v.get("rpa") == "special":
        return v
    return {"rpa": "special", "value": [{"type": "other", "value": v}]}


def unwrap_special(v):
    """反向解包，便于人工核对与日志输出。"""
    if isinstance(v, dict) and v.get("rpa") == "special":
        parts = []
        for it in v.get("value") or []:
            parts.append(str(it.get("value", it.get("data", ""))))
        return "".join(parts)
    return v


def _wrap_attr_values(path_dirs):
    """pathDirs 里每个 attr.value 也要包成 special 格式（与真实元素一致）。"""
    out = []
    for node in path_dirs or []:
        node = dict(node)
        attrs = []
        for a in node.get("attrs") or []:
            a = dict(a)
            a["value"] = wrap_special(a.get("value", ""))
            attrs.append(a)
        node["attrs"] = attrs
        out.append(node)
    return out


def build_element_data(info, page_url, page_title, app=DEFAULT_BROWSER):
    """把插件返回值组装成 c_element.element_data 的完整 JSON（dict）。"""
    path = {
        "matchTypes": info.get("matchTypes") or [],
        # 保留插件给的 checkType：有 pathDirs 时它是 visualization，
        # 这正是人工拾取的默认值，运行时走 pathDirs 分支。
        "checkType": info.get("checkType") or "visualization",
        "xpath": wrap_special(info.get("xpath") or ""),
        "cssSelector": wrap_special(info.get("cssSelector") or ""),
        "pathDirs": _wrap_attr_values(info.get("pathDirs") or []),
        "url": wrap_special(page_url or info.get("url") or ""),
        "shadowRoot": bool(info.get("shadowRoot")),
        "tag": info.get("tag") or "",
        "text": info.get("text") or "",
        "tabTitle": page_title or "",
        "tabUrl": page_url or "",
        "isFrame": bool(info.get("isFrame")),
        "frameId": info.get("frameId") or 0,
    }
    return {
        "version": "1",
        "type": "web",
        "app": app,
        "path": path,
        # 窗口信息用于把浏览器置顶；WebFactory 找窗口时按类名查，这里给的是
        # 设计器写库时的同款结构，保持一致以免设计器打开时判定为「残缺元素」。
        "uiapath": [
            {
                "cls": CHROME_WINDOW_CLASS,
                "name": (page_title or "") + CHROME_WINDOW_SUFFIX,
                "app": app,
                "tag_name": "WindowControl",
                "checked": True,
            }
        ],
        "picker_type": "ELEMENT",
    }


# --------------------------------------------------------------------------- #
# 通道二：平台 HTTP API（写元素库）
# --------------------------------------------------------------------------- #
def _cookie():
    """从设计器本机 cookie 文件取会话（不重复登录，也不依赖 requests）。"""
    path = cfg.cookie_file()
    if not os.path.exists(path):
        raise SystemExit("找不到设计器 cookie 文件：{}\n请先打开设计器登录一次。".format(path))
    with open(path, "r", encoding="utf-8") as f:
        ck = json.load(f)
    host = cfg.host_port()
    rec = ck.get(host) or ck.get("{}.SESSION".format(host))
    if not rec:
        hosts = [k for k in ck if not k.endswith(".SESSION")]
        raise SystemExit("cookie 里没有 {} 的会话；已有：{}".format(host, hosts))
    val = rec["SESSION"]["key_value"]
    return val if val.startswith("SESSION=") else "SESSION={}".format(val)


def api(path, body=None, params=None):
    """POST 到平台接口。params 走 query（element/all、delete 这类接口只认 query）。"""
    url = "http://{}{}".format(cfg.load()["gateway"]["host"], path)
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        data=json.dumps(body if body is not None else {}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Cookie": _cookie()},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        text = r.read().decode("utf-8")
    try:
        return json.loads(text)
    except Exception:
        raise SystemExit("接口 {} 返回非 JSON：{}".format(path, text[:300]))


def api_ok(res, what):
    if res.get("code") != "000000":
        raise SystemExit("{} 失败：{} {}".format(what, res.get("code"), res.get("message")))
    return res.get("data")


def list_groups(robot_id):
    res = api("/api/robot/element/all", params={"robotId": robot_id, "elementType": "common"})
    return api_ok(res, "读元素库") or []


def find_element(robot_id, element_id):
    for g in list_groups(robot_id):
        for e in g.get("elements") or []:
            if str(e.get("id")) == str(element_id):
                return g, e
    return None, None


def ensure_group(robot_id, group_name):
    """分组不存在就建。返回 groupId。"""
    for g in list_groups(robot_id):
        if g.get("name") == group_name:
            return g.get("id")
    res = api(
        "/api/robot/group/create",
        params={"robotId": robot_id, "groupName": group_name, "elementType": "common"},
    )
    api_ok(res, "建分组")
    for g in list_groups(robot_id):
        if g.get("name") == group_name:
            return g.get("id")
    raise SystemExit("分组 {} 建完却查不到，请在设计器里确认".format(group_name))


def create_element(robot_id, group_name, name, element_data):
    body = {
        "type": "common",
        "robotId": robot_id,
        "groupName": group_name,
        "element": {
            "commonSubType": "single",
            "name": name,
            "icon": "",
            # 图片非必填：传空串，元素照样能被 pathDirs/cssSelector 定位。
            "imageId": "",
            "parentImageId": "",
            "elementData": json.dumps(element_data, ensure_ascii=False),
        },
    }
    data = api_ok(api("/api/robot/element/create", body), "写元素库")
    return (data or {}).get("elementId")


def delete_element(robot_id, element_id):
    api_ok(
        api("/api/robot/element/delete", params={"robotId": robot_id, "elementId": element_id}),
        "删元素",
    )


def fetch_element_data(robot_id, element_id):
    data = api_ok(
        api("/api/robot/element/detail", params={"robotId": robot_id, "elementId": element_id}),
        "读元素详情",
    )
    raw = (data or {}).get("elementData")
    return json.loads(raw) if raw else {}


# --------------------------------------------------------------------------- #
# 校验：拿库里的元素，让插件现场定位一次
# --------------------------------------------------------------------------- #
def check_element(robot_id, element_id, verbose=True):
    """返回 (ok, 说明)。只读。"""
    group, meta = find_element(robot_id, element_id)
    if not meta:
        return False, "元素 {} 不在机器人 {} 的元素库里".format(element_id, robot_id)

    ed = fetch_element_data(robot_id, element_id)
    path = ed.get("path") or {}
    # 还原成插件认识的结构（解包 special）
    payload = {
        "xpath": unwrap_special(path.get("xpath")),
        "cssSelector": unwrap_special(path.get("cssSelector")),
        "pathDirs": _unwrap_attr_values(path.get("pathDirs") or []),
        "checkType": path.get("checkType") or "visualization",
        "matchTypes": path.get("matchTypes") or [],
    }
    if path.get("shadowRoot"):
        payload["shadowRoot"] = True

    try:
        res = bridge("checkElement", payload)
    except Exception as e:
        return False, "定位失败：{}".format(e)

    rects = (res or {}).get("rect") or []
    if not rects:
        return False, "定位返回空矩形"
    vis = [r for r in rects if rect_visible(r)]
    if not vis:
        r0 = rects[0]
        return False, (
            "命中 {} 个，但矩形全为 0（left:{}, top:{}, right:{}, bottom:{}）"
            "——元素在页面上不可见，多半命中了站点保留的隐藏旧节点。\n"
            "  处理办法：换更精确的选择器，或先 --probe 确认命中项 rect 非零。".format(
                len(rects), r0.get("left"), r0.get("top"), r0.get("right"), r0.get("bottom")
            )
        )
    if verbose:
        r0 = vis[0]
        print(
            "  命中 {} 个可见元素；首个 rect = left:{}, top:{}, w:{}, h:{}".format(
                len(vis),
                int(float(r0.get("left"))),
                int(float(r0.get("top"))),
                int(float(r0.get("right"))) - int(float(r0.get("left"))),
                int(float(r0.get("bottom"))) - int(float(r0.get("top"))),
            )
        )
    return True, "命中 {} 个可见元素".format(len(vis))


def _unwrap_attr_values(path_dirs):
    out = []
    for node in path_dirs or []:
        node = dict(node)
        node["attrs"] = [
            dict(a, value=unwrap_special(a.get("value", ""))) for a in (node.get("attrs") or [])
        ]
        out.append(node)
    return out


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def cmd_status():
    print("== 浏览器插件桥接 ==")
    try:
        st = browser_status()
        print("  ✓ url      : {}".format(st["url"]))
        print("  ✓ title    : {}".format(st["title"]))
        print("  ✓ tabId    : {}".format(st["tab_id"]))
    except Exception as e:
        # 桥接不通不影响「看元素库」，只是没法拾取/校验
        print("  ✗ {}".format(str(e).splitlines()[0]))
        print("    （只影响拾取与校验；元素库清单照常可查）")

    print("\n== 元素库（robot={}）==".format(DEFAULT_ROBOT_ID))
    try:
        groups = list_groups(DEFAULT_ROBOT_ID)
    except SystemExit as e:
        print("  ✗ {}".format(e))
        return 1
    total = 0
    for g in groups:
        els = g.get("elements") or []
        total += len(els)
        print("  分组 {:<12} id={}  元素 {} 个".format(g.get("name"), g.get("id"), len(els)))
        for e in els:
            print("      - {}  {}".format(e.get("id"), e.get("name")))
    print("  合计 {} 个".format(total))
    return 0


def cmd_probe(args):
    if args.url:
        if args.no_open:
            print("· 跳过导航，按当前标签页拾取")
        else:
            u, t = open_url(args.url)
            print("· 已打开 {}（{}）".format(u, t))
    items = probe(args.probe, by=args.by, url=args.url, visible=not args.allow_hidden)
    print("· 命中 {} 个元素：".format(len(items)))
    for i, it in enumerate(items[:5]):
        rect = it.get("rect") or {}
        print(
            "  [{}] tag={} text={!r} 可见={}\n      cssSelector={}\n      xpath={}".format(
                i,
                it.get("tag"),
                (it.get("text") or "")[:40],
                "是" if rect_visible(rect) else "否(矩形为 0)",
                it.get("cssSelector"),
                it.get("xpath"),
            )
        )
        print("      pathDirs 层级={}".format(len(it.get("pathDirs") or [])))
    if len(items) > 1:
        print("  ⚠ 选择器命中多个，落库时请用更精确的 cssSelector（或加 :nth-child）")
    return 0


def cmd_create(args):
    if args.url:
        if args.no_open:
            print("· 跳过导航，按当前标签页拾取")
        else:
            u, t = open_url(args.url)
            print("· 已打开 {}（{}）".format(u, t))
    else:
        st = browser_status()
        u, t = st["url"], st["title"]
        print("· 使用当前标签页 {}（{}）".format(u, t))

    items = probe(args.css, by=args.by, url=u, visible=not args.allow_hidden)
    if len(items) > 1 and not args.force:
        raise SystemExit(
            "选择器命中 {} 个元素，拒绝落库（会定位到第一个，容易误伤）。\n"
            "  换更精确的选择器，或加 --force 明确接受第一个。".format(len(items))
        )
    info = items[0]

    # 落库前先卡可见性：存一个不可见元素 = 存一个永远点不到的坑（坑 125）
    if not rect_visible(info.get("rect") or {}):
        r0 = info.get("rect") or {}
        raise SystemExit(
            "拒绝落库：命中的元素不可见（rect left:{}, top:{}, right:{}, bottom:{}）。\n"
            "  这是站点保留的隐藏节点，运行时会「找不到元素」。\n"
            "  请换选择器；确实要存隐藏元素才加 --allow-hidden。".format(
                r0.get("left"), r0.get("top"), r0.get("right"), r0.get("bottom")
            )
        )

    ed = build_element_data(info, u, t, app=args.browser)
    group = args.group
    ensure_group(args.robot_id, group)

    eid = create_element(args.robot_id, group, args.name, ed)
    print("✓ 已写入元素库：name={} id={} group={}".format(args.name, eid, group))

    print("· 立刻回查定位…")
    ok, msg = check_element(args.robot_id, eid)
    if ok:
        print("✓ 校验通过：{}".format(msg))
        print("\n元素 id = {}（流程节点里 elements.element_data 用这个 id）".format(eid))
        return 0
    print("✗ 校验未通过：{}".format(msg))
    print("  元素已入库，但当前页面定位不到。若页面已跳走属正常；")
    print("  若页面没变，请换选择器重来，或先用 --probe 确认。")
    return 1


def cmd_check(args):
    ok, msg = check_element(args.robot_id, args.check)
    print(("✓ " if ok else "✗ ") + msg)
    return 0 if ok else 1


def wait_selector(selector, by="css", timeout=25, interval=0.6):
    """等选择器在页面上真的能命中（返回 True/False）。

    为什么必须有：`open_url` 只等「URL 变成目标地址」，可现代站点是 JS 渲染的，
    URL 到了 DOM 还没到。紧接着去拾取就会得到「元素在第1节点 xxx 处发生变动」
    这种看着像选择器写错、实际只是慢半拍的报错（坑 127）。
    """
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        payload = (
            {"xpath": selector, "checkType": "customization"}
            if by == "xpath"
            else {"cssSelector": selector, "checkType": "customization"}
        )
        payload["filterVisible"] = True
        try:
            res = bridge("checkElement", payload, timeout=8)
            if (res or {}).get("rect"):
                return True
        except Exception as e:
            last = str(e)
        time.sleep(interval)
    if last:
        print("      · 等待选择器超时，最后一次错误：{}".format(last[:120]))
    return False


def ensure_element(robot_id, name, url, selector, by="css", group=DEFAULT_GROUP,
                   visible=True, force=False, verify=True):
    """幂等入口：库里已有同名元素就直接返回，没有才去拾取。

    这是给 `flow_generator.ensure_elements` 用的——流程规格里声明「这个节点要哪个
    元素、元素怎么从页面取」，生成时自动补齐，人不用进设计器点。

    返回 `{"element_id", "element_name", "created": bool}`。
    """
    for g in list_groups(robot_id):
        for e in g.get("elements") or []:
            if e.get("name") == name:
                if verify:
                    ok, msg = check_element(robot_id, e.get("id"))
                    if not ok:
                        raise RuntimeError(
                            "元素「{}」已存在但当前定位不到：{}".format(name, msg)
                        )
                return {"element_id": e.get("id"), "element_name": name, "created": False}

    # 页面不对就先导航过去：插件只能对「当前活动标签页」拾取，
    # 不自己跳页的话，自动化到这一步又得人手工切标签页（那就白自动了）。
    if url:
        try:
            cur = bridge("getUrl", timeout=5)
        except Exception:
            cur = ""
        if not _same_page(cur, url):
            open_url(url)

    # 页面刚打开时 DOM 可能还没渲染完，先等选择器可命中，再谈拾取
    wait_selector(selector, by=by, timeout=25)

    items = probe(selector, by=by, url=url, visible=visible)
    if len(items) > 1 and not force:
        raise RuntimeError(
            "选择器 {} 命中 {} 个元素，拒绝落库（换更精确的选择器或 force=True）".format(
                selector, len(items)
            )
        )
    info = items[0]
    if visible and not rect_visible(info.get("rect") or {}):
        raise RuntimeError("命中的元素不可见（rect 全 0），拒绝落库：{}".format(selector))

    ensure_group(robot_id, group)
    try:
        title = bridge("getTitle", timeout=5)
    except Exception:
        title = ""
    eid = create_element(robot_id, group, name, build_element_data(info, url, title))
    if verify:
        ok, msg = check_element(robot_id, eid, verbose=False)
        if not ok:
            raise RuntimeError("元素「{}」已写入但校验失败：{}".format(name, msg))
    return {"element_id": eid, "element_name": name, "created": True}


def cmd_delete(args):
    group, meta = find_element(args.robot_id, args.delete)
    if not meta:
        raise SystemExit("元素 {} 不存在".format(args.delete))
    delete_element(args.robot_id, args.delete)
    print("✓ 已删除 {}（{}）".format(args.delete, meta.get("name")))
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="星辰开源 · 元素自动拾取器（把「人工点选」变成一条命令）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--status", action="store_true", help="看桥接状态 + 元素库清单")
    ap.add_argument("--probe", metavar="SEL", help="只探测选择器命中情况（只读，不落库）")
    ap.add_argument("--css", metavar="SEL", help="按选择器拾取并写入元素库（需同时给 --name）")
    ap.add_argument("--by", choices=["css", "xpath"], default="css", help="选择器类型，默认 css")
    ap.add_argument("--name", help="元素名称（写库必填）")
    ap.add_argument("--url", help="先导航到这个地址再拾取（省略则用当前标签页）")
    ap.add_argument("--no-open", action="store_true", help="不导航，直接用当前标签页")
    ap.add_argument("--robot-id", default=DEFAULT_ROBOT_ID, help="元素所属机器人（必须与流程一致）")
    ap.add_argument("--group", default=DEFAULT_GROUP, help="元素分组名，不存在会自动建")
    ap.add_argument("--browser", default=DEFAULT_BROWSER, help="浏览器类型，默认 chrome")
    ap.add_argument("--force", action="store_true", help="选择器命中多个时仍取第一个")
    ap.add_argument("--allow-hidden", action="store_true", help="允许落库不可见元素（默认拒绝）")
    ap.add_argument("--check", metavar="ELEMENT_ID", help="校验已有元素当前能否命中（只读）")
    ap.add_argument("--delete", metavar="ELEMENT_ID", help="删除元素")
    args = ap.parse_args()

    if args.status:
        return cmd_status()
    if args.probe:
        return cmd_probe(args)
    if args.check:
        return cmd_check(args)
    if args.delete:
        return cmd_delete(args)
    if args.css:
        if not args.name:
            ap.error("--css 必须同时给 --name（元素库不允许空名字）")
        return cmd_create(args)
    ap.print_help()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as e:
        # 插件侧的业务失败（找不到元素/通道不通）不该以 traceback 糊用户一脸
        sys.stderr.write("\n✗ {}\n".format(e))
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
