#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""换设备打包：把「不在 git 里、但少了就跑不起来」的文件收进 handover/ 目录。

背景：`rpa_config.json`（网关地址 / API Key / 设计器 python 路径）和
`docs/_local_secrets.md`（服务器密码留档）都被 `.gitignore` 忽略，
`git clone` 拿不到。换设备时最容易漏，漏了整条链路直接报「配置缺失」。

用法：
    python scripts/handover_export.py              # 打包到 handover/
    python scripts/handover_export.py --dest D:/u盘/handover
    python scripts/handover_export.py --check      # 只体检，不打包
    python scripts/handover_export.py --check --src D:/旧机器/handover   # 校验拷来的包

打包产物：
    handover/
      rpa_config.json          （必需）
      docs/_local_secrets.md   （可选，只是密码留档）
      MANIFEST.md              （清单 + 新机器落地步骤）

注意：handover/ 也在 .gitignore 里，别指望它进仓库。
"""
import argparse
import datetime
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (相对路径, 是否必需, 说明, 落地位置)
ITEMS = [
    ("rpa_config.json", True,
     "网关地址 / API Key / 设计器 python 路径 / 默认 robotId",
     "项目根目录"),
    ("docs/_local_secrets.md", False,
     "SSH / MySQL / Redis / MinIO / Casdoor 密码留档",
     "docs/"),
]

MANIFEST = """# 换设备交接包

> 生成时间：{ts}
> 来源机器：{host}
> 项目版本：{commit}

## 这个目录是什么

`git clone` **拿不到**的文件。这些文件含 API Key / 密码，被 `.gitignore` 排除，
所以必须像 U 盘一样手工带走。

> ⚠️ **本包是明文凭据，别丢进公共网盘 / 别提交到仓库。**
> ⚠️ **`rpa_config.json` 里的 `defaults.robot_id` 是旧机器的平台 ID，换机后一定失效。**
> 不要用它，跑流程一律带 `--robot-name`（按名字找，找不到自动新建）。

## 落地步骤（新机器上，项目已 clone 之后）

```bash
# 1) 把本目录两个文件放回原位
cp handover/rpa_config.json        <项目根>/
cp handover/docs/_local_secrets.md <项目根>/docs/

# 2) 自检：打印生效配置 + 每项来源，确认没有 "缺失"
python scripts/rpa_config.py

# 3) 环境自检（设计器已装并登录、Chrome 已装 RPA 插件）
python scripts/doctor.py

# 4) 换机必做一次（坑 118，不做 DataTable 全系原子必崩）
mkdir -p "$APPDATA/astron-rpa/astron"

# 5) 盘点平台机器人
python scripts/publish_flow.py --list

# 6) 桥接 + 元素库
python scripts/element_pick.py --status
```

## 包里有什么

{items}

## 包里**故意没有**什么

| 不带的 | 为什么 | 新机器怎么来 |
| --- | --- | --- |
| robotId / elementId | 平台换机器会重新生成，抄过去必失效 | `--robot-name` 按名字找，找不到自动新建 |
| `docs/element_cache.json` | 元素按 robotId 隔离，带着会指向旧机器人的元素（坑 126/129） | 自动重新拾取（`element_pick.py`） |
| `flows/_archive/` | 只是本地历史快照 | 不需要 |
| `__pycache__` / `*.log` | 垃圾 | 不需要 |
"""


def rel(p):
    return os.path.join(ROOT, p.replace("/", os.sep))


def human(n):
    for unit in ("B", "KB", "MB"):
        if n < 1024 or unit == "MB":
            return ("%.1f %s" % (n, unit)) if unit != "B" else ("%d B" % n)
        n /= 1024.0


def commit_id():
    """尽力取一下当前 commit，取不到就算了（不能因为 git 不可用而打包失败）。"""
    try:
        import subprocess
        out = subprocess.check_output(
            ["git", "-C", ROOT, "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL)
        return out.decode("utf-8", "replace").strip() or "unknown"
    except Exception:
        return "unknown"


def check(src=None):
    """体检：逐项确认存在、非空、JSON 能解析。返回 (ok, 必需项缺失数)。"""
    base = src or ROOT
    print("体检目录：%s" % base)
    missing_required = 0
    for path, required, desc, _dest in ITEMS:
        full = os.path.join(base, path.replace("/", os.sep))
        tag = "必需" if required else "可选"
        if not os.path.isfile(full):
            if required:
                missing_required += 1
                print("  ✗ [%s] %-24s 缺失 —— %s" % (tag, path, desc))
            else:
                print("  - [%s] %-24s 不存在（不影响主链路）" % (tag, path))
            continue
        size = os.path.getsize(full)
        if size == 0:
            if required:
                missing_required += 1
                print("  ✗ [%s] %-24s 是空文件" % (tag, path))
            else:
                print("  - [%s] %-24s 是空文件" % (tag, path))
            continue
        note = human(size)
        # rpa_config.json 额外验一下 JSON 与关键字段有没有真的填上
        if path.endswith(".json"):
            try:
                with open(full, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception as e:
                missing_required += 1
                print("  ✗ [%s] %-24s JSON 解析失败：%s" % (tag, path, e))
                continue
            empty = config_empty_fields(cfg)
            rid = stale_robot_id(cfg)
            if empty:
                print("  ! [%s] %-24s %s · 这些字段是空的：%s"
                      % (tag, path, note, ", ".join(empty)))
                if rid:
                    print("      └ defaults.robot_id=%s 是本机 ID，换机后失效，别信它（用 --robot-name）" % rid)
                continue
            note += " · 关键字段已填"
            if rid:
                note += " · robot_id 是本机的，换机后作废"
        print("  ✓ [%s] %-24s %s" % (tag, path, note))
    return missing_required == 0, missing_required


# rpa_config.json 里真正影响能不能跑起来的字段（按实际结构）
# 注意 client.python 留空是正常的——rpa_config.py 会自动探测 %APPDATA%/astron-rpa/python_core
KEY_FIELDS = [
    ("gateway", "host"),
    ("openapi", "api_key"),
]


def config_empty_fields(cfg):
    """返回 rpa_config.json 里为空的关键字段，如 ['openapi.api_key']。"""
    empty = []
    for sec, key in KEY_FIELDS:
        v = (cfg.get(sec) or {}).get(key)
        if v is None or (isinstance(v, str) and not v.strip()):
            empty.append("%s.%s" % (sec, key))
    return empty


def stale_robot_id(cfg):
    """defaults.robot_id 是本机平台的 ID，换设备后必然失效。

    不删它（删了有些脚本会报「配置缺失」），但必须喊出来——
    静默沿用旧 ID 比直接报错更坑，会跑到别人的机器人上。
    """
    return ((cfg.get("defaults") or {}).get("robot_id") or "").strip()


def export(dest):
    ok, miss = check()
    if not ok:
        print("\n⚠ 有 %d 个必需文件缺失，先补上再打包（见 docs/待办清单.md §1.1）"
              % miss)
        # 仍然继续打包已有的，避免用户卡住；但退出码非 0
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    os.makedirs(os.path.join(dest, "docs"), exist_ok=True)

    rows = []
    for path, required, desc, _dest in ITEMS:
        src = rel(path)
        if not os.path.isfile(src):
            rows.append("| `%s` | %s | **缺失** | %s |"
                        % (path, "必需" if required else "可选", desc))
            continue
        dst = os.path.join(dest, path.replace("/", os.sep))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        rows.append("| `%s` | %s | %s | %s |"
                    % (path, "必需" if required else "可选",
                       human(os.path.getsize(dst)), desc))
        print("  → 已复制 %s" % path)

    table = "| 文件 | 级别 | 大小 | 内容 |\n| --- | --- | --- | --- |\n" + "\n".join(rows)
    man = MANIFEST.format(
        ts=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        host=os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "?",
        commit=commit_id(), items=table)
    mpath = os.path.join(dest, "MANIFEST.md")
    with open(mpath, "w", encoding="utf-8") as f:
        f.write(man)
    print("  → 已写 MANIFEST.md")
    print("\n交接包就绪：%s" % dest)
    print("把这个目录整个拷到新机器，按 MANIFEST.md 两步放回原位即可。")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="换设备打包：收拢 git 拿不到的运行时文件")
    ap.add_argument("--dest", default=os.path.join(ROOT, "handover"),
                    help="打包目标目录（默认 <项目>/handover）")
    ap.add_argument("--check", action="store_true", help="只体检，不打包")
    ap.add_argument("--src", help="配合 --check：体检别的目录（如拷来的交接包）")
    args = ap.parse_args()

    if args.check:
        ok, miss = check(args.src)
        if ok:
            print("\n✓ 齐全，可以开工")
            return 0
        print("\n✗ 缺 %d 个必需文件 —— 见 docs/待办清单.md §1.1" % miss)
        return 2
    return export(args.dest)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已中断")
        sys.exit(130)
