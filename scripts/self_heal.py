# -*- coding: utf-8 -*-
"""失败自愈：**只做「改了自己一定不会更糟」的那几件事**，然后重跑一次。

## 为什么范围这么窄

用户要的是「跑失败了 AI 自己改改再跑，别每次都来问我」。但自动改错比不改更糟 ——
比如「目标 Word 被占用」自动删文件、或者把源路径猜错，会毁数据。所以这里立三条规矩：

1. **只动「不存在的输出目录」和「能唯一定位的输入路径」**，这两类改了不会丢东西；
2. **只重跑一次**（`MAX_HEAL = 2`），反复试错只会浪费时间、掩盖真问题；
3. **不能自愈就明说原因 + 人该做什么**，不假装在修。

## 目前能自愈的

| 场景 | 动作 |
| --- | --- |
| 输出目录不存在（预检 `outdir_missing` / `out_parent_missing`） | 建目录 |
| 输入路径不存在，但同名文件在项目里唯一存在 | 把参数改到那个文件 |
| 目标文件被 Word/WPS/预览占用 | ❌ 不能自愈，告诉你关哪个程序 |
| 模块不存在 / 编译失败 / 元素缺失 | ❌ 不能自愈，属于流程或环境问题 |
"""
import os
import re

import nl_params as nlp

MAX_HEAL = 2


def _param_map(spec):
    return {p["name"]: p for p in (spec.get("params") or []) if p.get("name")}


def _is_output_dir(spec, path):
    """这个目录是不是「流程要往外写」的目录（是才敢建）。

    两种来源都算：`*_dir` 参数本身就是它；或者它是某个输出文件参数的父目录。
    """
    np = os.path.normcase(os.path.normpath(path))
    for name, p in _param_map(spec).items():
        v = str(p.get("default") or "")
        if not v or not os.path.isabs(v):
            continue
        vn = os.path.normcase(os.path.normpath(v))
        if name.endswith("_dir") or name == "out_dir":
            if np == vn:
                return True
        elif np == os.path.dirname(vn):
            return True
    return False


def plan_preflight(spec, pf_res, root=None):
    """预检阶段：把「输出目录不存在」自己建好。返回 `(actions, blocked)`。"""
    actions, blocked = [], []
    for it in (pf_res or {}).get("items") or []:
        if it.get("level") != "error":
            continue
        code = it.get("code") or ""
        if code not in ("outdir_missing", "out_parent_missing"):
            if code in ("outdir_ro", "out_ro", "permission_denied"):
                blocked.append(f"{it.get('msg')} —— 权限问题，AI 改不了，需要你换个目录或提权")
            continue
        # 从报错文案里把路径捞回来（文案形如「… 目录不存在：<路径>（父目录存在…）」）
        msg = it.get("msg") or ""
        m = re.search(r"[A-Za-z]:[\\/][^（(，,、；;]*", msg)
        path = (m.group(0).strip().rstrip("\\/") if m else "")
        if not path or not os.path.isabs(path):
            continue
        # 只建「输出类」目录：输入目录不存在意味着源没有，建个空目录后重跑只会产出空结果
        if not _is_output_dir(spec, path):
            continue
        actions.append({
            "desc": f"创建输出目录：{path}",
            "fn": lambda p=path: os.makedirs(p, exist_ok=True),
        })
    return actions, blocked


def plan_run(spec, run_result, root=None):
    """运行失败后：只处理「输入路径找不到但项目里能唯一定位」这一种。"""
    actions, blocked = [], []
    hints = (run_result or {}).get("hints") or []
    ids = {h.get("id") for h in hints}
    text = "\n".join(str(x) for x in ((run_result or {}).get("errors") or []))
    text += "\n" + " ".join(str(h.get("hit") or "") for h in hints)

    if ids & {"word_locked", "office_locked_generic", "com_error"}:
        blocked.append("目标文件被 Word/WPS/预览占用了 —— 这个 AI 不能替你关，"
                       "关掉那个文件后重跑同一条命令即可")
    if ids & {"module_missing", "module_no_main", "module_content_corrupt"}:
        blocked.append("Python 子流程模块有问题（不存在 / 没 main / 内容坏了）—— "
                       "属于流程或部署问题，需要重新 make_module 后再跑")
    if ids & {"element_missing", "element_timeout"}:
        blocked.append("页面元素资产缺失或超时 —— 要在设计器里对目标机器人重新拾取元素")

    # 输入路径不存在 → 项目里同名文件唯一存在就改参数
    if ids & {"no_such_file", "path_invalid_msg"}:
        params = _param_map(spec)
        for name, p in params.items():
            v = str(p.get("default") or "")
            if not v or os.path.exists(v):
                continue
            if name.endswith("_dir"):
                continue
            hit = nlp._resolve_relative(os.path.basename(v), root)
            if hit and os.path.exists(hit):
                actions.append({
                    "desc": f"{name} 改成项目里找到的同名文件：{hit}",
                    "fn": lambda n=name, h=hit: params[n].__setitem__("default", h),
                })
                break

    return actions, blocked


def run_actions(actions):
    """执行动作，返回 `(成功数, 失败说明)`。单个动作失败不中断其它。"""
    ok, fails = 0, []
    for a in actions:
        try:
            a["fn"]()
            ok += 1
        except Exception as e:
            fails.append(f"{a['desc']} → {e}")
    return ok, fails
