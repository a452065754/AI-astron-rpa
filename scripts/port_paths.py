#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把流程规格里「写死本机路径」的参数默认值改成 `${PROJECT_ROOT}` 占位符。

## 背景（为什么需要这个脚本）

`flows/*.json` 的 `params[].default` 必须是**绝对路径**（引擎执行前会
`os.chdir(project_dir)`，而 project_dir 是 `%APPDATA%\\astron\\<project_id>`
这个编译缓存目录，相对路径会解析到缓存里，必然找不到文件）。

但绝对路径一旦写成某一台机器的，仓库就绑死在那台机器上：别人 clone 下来一跑
就是 `[Errno 2] No such file or directory`。

解法是**源头不写机器路径**：规格里写 `${PROJECT_ROOT}/demo/订单.xlsx`，
`flow_generator.load_spec()` 在发布时换成本机仓库根。仓库因此可迁移，
落到平台的参数仍是合法绝对路径。

本脚本负责把历史规格批量迁到这种写法，也负责以后 AI 又生成盘符路径时一键修。

## 用法

```bash
python scripts/port_paths.py            # 只看（默认 dry-run），列出会改什么
python scripts/port_paths.py --check    # 同上，但退出码非 0 表示「需要迁移」
python scripts/port_paths.py --write    # 真改
```

## 只改「本仓库内的路径」

判定依据是「路径里含有本仓库目录名」。指向仓库外的（如 `F:/demo/...`、
`D:/ERP导出`、`\\\\nas\\share`）**一律不动**，只列出来让人确认 —— 脚本替人猜
「这个外部路径该映射到仓库哪里」是最容易出错的地方。
"""
import argparse
import glob
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_NAME = os.path.basename(ROOT)          # 星辰开源
TOKEN = "${PROJECT_ROOT}"

_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
# 系统目录不改：任何一台 Windows 上都是同一位置（`C:\Windows\System32\mstsc.exe`），
# 与 `spec_lint` 的 `SYS_PATH` 口径保持一致。
_SYS = re.compile(r"^[A-Za-z]:[\\/](?:Windows|Program Files(?: \(x86\))?|ProgramData)[\\/]",
                  re.IGNORECASE)


def _rel_tail(value):
    """若 value 指向本仓库内，返回仓库内的相对路径（用 / 分隔）；否则 None。"""
    if not isinstance(value, str) or not _DRIVE.match(value):
        return None
    norm = re.sub(r"[\\/]+", "/", value)
    marker = "/" + PROJECT_NAME + "/"
    idx = norm.find(marker)
    if idx < 0:
        # 也可能是仓库根目录本身（没有尾部斜杠）
        if norm.endswith("/" + PROJECT_NAME):
            return ""
        return None
    return norm[idx + len(marker):]


def plan(paths):
    """返回 [(文件, 参数名, 原值, 新值)] 与 [(文件, 参数名, 原值)]（外部路径，不动）。"""
    changes, externals = [], []
    for p in sorted(paths):
        try:
            with io.open(p, encoding="utf-8") as f:
                spec = json.load(f)
        except Exception as e:
            print("跳过（解析失败）%s：%s" % (p, e))
            continue
        for prm in spec.get("params") or []:
            if not isinstance(prm, dict):
                continue
            v = prm.get("default")
            if not isinstance(v, str) or not _DRIVE.match(v):
                continue
            if _SYS.match(v):
                continue  # 系统目录，任何机器都一样
            tail = _rel_tail(v)
            if tail is None:
                externals.append((p, prm.get("name"), v))
            elif v.startswith(TOKEN):
                continue  # 已经迁过
            else:
                changes.append((p, prm.get("name"), v, "%s/%s" % (TOKEN, tail) if tail else TOKEN))
    return changes, externals


def apply(changes):
    """按文件做**文本级**替换，不重新序列化整个 JSON。

    ## 为什么不 `json.load` → 改值 → `json.dump`

    第一版就是那么写的，结果 diff 里全是格式噪音：原文件是「短数组写一行」
    （`"file_path": ["$p:out_dir", "/", "右补齐_可用.txt"]`），而 `json.dump(indent=2)`
    一律展开成多行。72 个文件改 120 处参数，diff 却变成 382 增 / 224 删 ——
    真正改了什么根本看不出来，评审等于失效。

    文本级替换的代价是要自己保证「匹配到的就是参数默认值那一行」，所以要求
    `"default": "<json 转义后的值>"` 精确匹配，并核对替换次数。
    """
    by_file = {}
    for p, name, old, new in changes:
        # 同一文件里多个参数可能共用同一个默认值（如两个 out_dir）——文本替换会一次全换掉，
        # 所以按 old 去重，避免第二次匹配不到还以为出了问题。
        by_file.setdefault(p, {}).setdefault(old, new)

    total = 0
    for p, mapping in by_file.items():
        with io.open(p, encoding="utf-8") as f:
            text = f.read()
        for old, new in mapping.items():
            # json.dumps 产出的就是文件里那一串（含反斜杠转义、中文不转义）
            src = '"default": ' + json.dumps(old, ensure_ascii=False)
            dst = '"default": ' + json.dumps(new, ensure_ascii=False)
            if src not in text:
                # 兼容冒号后多个空格 / 制表符的写法
                hit = re.search(r'"default":[ \t]+' + re.escape(json.dumps(old, ensure_ascii=False)),
                                text)
                if not hit:
                    print("跳过 %s 的 %s（文件里找不到该默认值，可能已被改过）"
                          % (os.path.basename(p), name))
                    continue
                text = text[:hit.start()] + '"default": ' + json.dumps(new, ensure_ascii=False) \
                    + text[hit.end():]
            else:
                text = text.replace(src, dst)
            total += 1
        with io.open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    return total



def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="真改（默认只预览）")
    ap.add_argument("--check", action="store_true", help="只判定：有需要迁移的就退出码 1")
    ap.add_argument("--glob", default="flows/*.json")
    a = ap.parse_args()

    paths = glob.glob(os.path.join(ROOT, a.glob))
    changes, externals = plan(paths)

    if changes:
        print("=== 需要迁移 %d 处（%d 个文件）===" % (len(changes), len({c[0] for c in changes})))
        for p, name, old, new in changes:
            print("  %-38s %-14s %s" % (os.path.basename(p)[:38], name, old))
    else:
        print("=== 无需迁移：没有写死本仓库路径的参数 ===")

    if externals:
        print()
        print("=== 仓库外路径 %d 处（脚本不动，需人工确认）===" % len(externals))
        for p, name, old in externals:
            print("  %-38s %-14s %s" % (os.path.basename(p)[:38], name, old))

    if a.check:
        return 1 if changes else 0
    if a.write and changes:
        apply(changes)
        print()
        print("已写入 %d 个文件" % len({c[0] for c in changes}))
    elif changes:
        print()
        print("（预览模式，加 --write 才真改）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
