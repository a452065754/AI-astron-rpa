#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""内嵌脚本同步器：把 `scripts/mod_<名字>.py` 与流程规格里的 `scripts.<名字>.code` 对齐。

## 为什么需要这个工具

`Script.module` 节点的脚本有**两份**：

| 位置 | 谁在用 | 改了会不会生效 |
| --- | --- | --- |
| `flows/x.json` 的 `scripts.<名>.code` | **落库 / 运行时真正执行的** | ✅ 生效 |
| `scripts/mod_<名>.py` | 人（和 AI）改代码、本地 `run_flow_local` 用 | ❌ 不生效 |

两份不一致时，本地测的是新代码、落库的是旧代码 —— 2026-11-04 就踩过：
`mod_mes_chbq.py` 修好了「跑了个寂寞」，但规格里还是旧脚本，
落库后行为没变，而本地跑却是对的。**这类漂移不报错，只是白干。**

## 用法

    # 只读：列出该规格所有内嵌脚本与磁盘副本的差异
    python scripts/sync_script.py --spec flows/MES包装标签生成.json

    # 把磁盘文件写回规格（推荐：先本地跑通，再同步，再落库）
    python scripts/sync_script.py --spec flows/MES包装标签生成.json --name mes_chbq --write

    # 全项目普查（所有规格 × 所有内嵌脚本）
    python scripts/sync_script.py --all

    # 全项目普查并把所有有磁盘副本的都同步（谨慎：会一次改多个规格）
    python scripts/sync_script.py --all --write

## 安全性

写回前会做三道检查，任何一道不过就**不写**：

1. 磁盘文件存在且能被 `ast.parse` 解析（语法错的代码不进规格）；
2. 文件本身能被「读 → 标准序列化」无损往返（否则说明格式非标准，写回会动到无关行）；
3. 写回后再 `json.load` 一次，确认除 `code` 外其它字段完全没变。

换行风格（CRLF/LF）按原文件保留，避免整文件 diff。
"""

from __future__ import print_function

import argparse
import ast
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _read_text(path):
    with open(path, "rb") as f:
        return f.read().decode("utf-8")


def _norm(text):
    """换行风格归一：磁盘是 CRLF、规格里存 LF，不归一化会 100% 误报漂移。

    实测：一个 240 行的脚本，内容完全一样，只因 CRLF/LF 差 240 个字符。
    """
    return text.replace("\r\n", "\n")


def _script_path(name):
    return os.path.join(ROOT, "scripts", "mod_%s.py" % name)


def _first_diff(a, b):
    """返回第一处差异的行号（1 起）与两侧内容，便于人眼看。"""
    la, lb = a.splitlines(), b.splitlines()
    for i in range(max(len(la), len(lb))):
        x = la[i] if i < len(la) else "<文件已结束>"
        y = lb[i] if i < len(lb) else "<文件已结束>"
        if x != y:
            return i + 1, x, y
    return None


def inspect(spec_path, only=None):
    """检查一个规格，返回 (rows, spec_obj, raw_text)。

    rows 每项：{"name", "status", "spec_len", "disk_len", "diff"}；
    status ∈ {"same", "drift", "no_disk", "empty"}。
    """
    raw = _read_text(spec_path)
    spec = json.loads(raw)
    scripts = spec.get("scripts") or {}
    rows = []
    for name in sorted(scripts):
        if only and name != only:
            continue
        code = (scripts.get(name) or {}).get("code") or ""
        path = _script_path(name)
        if not os.path.isfile(path):
            rows.append({"name": name, "status": "no_disk",
                         "spec_len": len(code), "disk_len": None, "diff": None})
            continue
        disk = _read_text(path)
        if not code.strip():
            rows.append({"name": name, "status": "empty",
                         "spec_len": 0, "disk_len": len(_norm(disk)), "diff": None})
        elif _norm(disk).strip() == _norm(code).strip():
            rows.append({"name": name, "status": "same",
                         "spec_len": len(_norm(code)), "disk_len": len(_norm(disk)),
                         "diff": None})
        else:
            rows.append({"name": name, "status": "drift",
                         "spec_len": len(_norm(code)), "disk_len": len(_norm(disk)),
                         "diff": _first_diff(_norm(code), _norm(disk))})
    return rows, spec, raw


def write_back(spec_path, spec, raw, name):
    """把磁盘副本写进 spec 并落盘。返回 (ok, msg)。"""
    path = _script_path(name)
    if not os.path.isfile(path):
        return False, "磁盘上没有 %s" % os.path.relpath(path, ROOT)
    disk = _read_text(path)

    # 闸门 1：语法
    try:
        ast.parse(disk, filename=path)
    except SyntaxError as e:
        return False, "磁盘文件语法错误（第 %s 行：%s），拒绝写进规格" % (e.lineno, e.msg)

    # 存进规格的一律归一成 LF：JSON 字符串里塞 CRLF 只会让 diff 变脏，
    # 运行时 exec 两种都认。磁盘文件本身不动（保持 Windows 习惯）。
    disk = _norm(disk)

    # 闸门 2：无损往返（否则写回会动到无关行）
    dumped = json.dumps(spec, ensure_ascii=False, indent=2)
    crlf = "\r\n" in raw
    normalized = raw.replace("\r\n", "\n")
    if dumped != normalized.rstrip("\n") and dumped.rstrip("\n") != normalized.rstrip("\n"):
        return False, ("该规格的排版不是标准 2 空格缩进序列化，写回会改动无关行。"
                       "请手工把代码贴进 scripts.%s.code" % name)

    # 闸门 3：除 code 外不许变
    before = json.loads(json.dumps(spec))
    old_code = (spec["scripts"][name] or {}).get("code")
    spec["scripts"][name]["code"] = disk
    after = json.loads(json.dumps(spec))
    spec["scripts"][name]["code"] = old_code
    b2, a2 = json.loads(json.dumps(before)), json.loads(json.dumps(after))
    b2["scripts"][name].pop("code", None)
    a2["scripts"][name].pop("code", None)
    if b2 != a2:
        return False, "内部错误：写回会改动 code 以外的字段，已中止"

    spec["scripts"][name]["code"] = disk
    out = json.dumps(spec, ensure_ascii=False, indent=2)
    if crlf:
        out = out.replace("\n", "\r\n")
    with open(spec_path, "w", encoding="utf-8", newline="") as f:
        f.write(out)
    return True, "已写回（%d → %d 字符）" % (len(old_code or ""), len(disk))


def _report(spec_path, rows):
    """只打印「有内嵌脚本」的规格 —— 93 条里 80+ 条是纯原子流程，全打出来没人看得见重点。"""
    rel = os.path.relpath(spec_path, ROOT)
    bad = [r for r in rows if r["status"] in ("drift", "empty")]
    if not rows:
        return bad
    print("  %s" % rel)
    for r in rows:
        if r["status"] == "same":
            print("    ✓ %-16s 一致（%d 字符）" % (r["name"], r["spec_len"]))
        elif r["status"] == "drift":
            print("    ✗ %-16s 漂移  规格 %d 字 / 磁盘 %d 字"
                  % (r["name"], r["spec_len"], r["disk_len"]))
            if r["diff"]:
                ln, a, b = r["diff"]
                print("        首个差异在第 %d 行" % ln)
                print("          规格: %s" % a[:110])
                print("          磁盘: %s" % b[:110])
        elif r["status"] == "empty":
            print("    ! %-16s 规格里是空的，磁盘有 %d 字（大概率忘了同步）"
                  % (r["name"], r["disk_len"]))
        else:
            print("    · %-16s 无磁盘副本（规格 %d 字）—— 只能在规格里改"
                  % (r["name"], r["spec_len"]))
    return bad


def main():
    ap = argparse.ArgumentParser(
        description="同步 scripts/mod_<名>.py 与流程规格里的 scripts.<名>.code")
    ap.add_argument("--spec", help="流程规格 JSON 路径")
    ap.add_argument("--all", action="store_true", help="普查 flows/*.json 全部规格")
    ap.add_argument("--name", help="只处理某个脚本名（对应 scripts/mod_<名>.py）")
    ap.add_argument("--write", action="store_true",
                    help="把磁盘文件写回规格（默认只读，只报差异）")
    args = ap.parse_args()

    if not args.spec and not args.all:
        ap.error("要么给 --spec，要么给 --all")

    specs = [args.spec] if args.spec else sorted(glob.glob(os.path.join(ROOT, "flows", "*.json")))
    print("检查 %d 个规格%s" % (len(specs), "（只读）" if not args.write else "（写回模式）"))

    total_drift = 0
    wrote = 0
    with_scripts = 0
    for sp in specs:
        try:
            rows, spec, raw = inspect(sp, only=args.name)
        except Exception as e:
            print("  %s：读取失败 %s" % (os.path.relpath(sp, ROOT), e))
            continue
        if rows:
            with_scripts += 1
        bad = _report(sp, rows)
        total_drift += len(bad)
        if args.write and bad:
            for r in bad:
                ok, msg = write_back(sp, spec, raw, r["name"])
                if ok:
                    wrote += 1
                    print("    → %s：%s" % (r["name"], msg))
                else:
                    print("    → %s：写回失败 —— %s" % (r["name"], msg))
            if wrote:
                # 写回后重新读，保证多脚本规格里第二个脚本用的是新 spec
                try:
                    _, spec, raw = inspect(sp, only=args.name)
                except Exception:
                    pass

    print("\n%d 个规格里有内嵌脚本（其余 %d 个是纯原子流程）"
          % (with_scripts, len(specs) - with_scripts))
    print("合计：漂移/缺失 %d 处%s" % (
        total_drift, "，已写回 %d 处" % wrote if args.write else "（加 --write 可写回）"))
    if total_drift and not args.write:
        print("提示：**落库/运行用的是规格里的代码**，改磁盘文件不生效。先本地跑通 → 再 --write → 再落库。")
    return 0 if (total_drift == 0 or args.write) else 1


if __name__ == "__main__":
    sys.exit(main())
