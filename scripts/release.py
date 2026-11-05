#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一键发版：一条命令完成 bump → 生成清单 → commit → tag → push → 线上校验。

## 为什么不再手动串命令

`version.py` 单独用时要敲 5 条命令（bump / add / commit / tag / push / publish-json），
顺序错一个就出事，而且**最容易错的是最后一步**：忘了 `--publish-json`，
代码推上去了、清单没跟上，同事那边永远查不到新版。

更隐蔽的是 **push 成功 ≠ 同事能看到**。所以本脚本最后一步会**真去读一次
Contents API**，确认远端能读到新版本号才算发版完成。

## 一次发版实际发生了什么

```
VERSION           1.0.0 → 1.0.1
CHANGELOG.md      顶部插入本次变更（自动从 git log 归类）
version.json      重写（version/commit/date/breaking/min_supported/highlights）
    ↓ git commit -m "chore(release): v1.0.1"
    ↓ git tag -a v1.0.1
    ↓ git push origin main + tag
    ↓ GitHub Actions 收到 tag → 自动建 Release（正文取 CHANGELOG 该段）
    ↓ 轮询 Contents API 直到读到 1.0.1（最多 40s）
```

## 用法

    python scripts/release.py --kind patch              # 修 bug 发补丁版
    python scripts/release.py --kind minor              # 新增能力
    python scripts/release.py --kind minor --breaking   # 破坏性变更（会强制所有人更新）
    python scripts/release.py --version 1.2.0           # 直接指定版本号
    python scripts/release.py --kind patch --dry-run    # 只看会发生什么，不动文件
    python scripts/release.py --kind patch --no-push    # 本地做好，先不推

## 红线

- 工作区必须干净。带着半成品发版，同事拉到的就是半成品。
- tag 已存在就拒绝（重打 tag 会让已更新的人和新更新的人拿到不同内容）。
- push 前所有步骤都在本地完成。push 失败时**本地已就绪**，修好网络后
  `python scripts/release.py --retry-push` 直接补推，不会重复 bump。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import version as vs  # noqa: E402

ROOT = vs.ROOT
BRANCH_DEFAULT = "main"


# ---------------------------------------------------------------- 小工具
def _git(*args: str, timeout: int = 120) -> tuple[int, str, str]:
    try:
        r = subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except (OSError, subprocess.SubprocessError) as e:
        return 1, "", str(e)


def ok(msg: str) -> None:
    print("  ✅ " + msg)


def bad(msg: str) -> None:
    print("  ❌ " + msg)


def warn(msg: str) -> None:
    print("  ⚠️  " + msg)


def rule(title: str) -> None:
    print("\n" + title)


# ---------------------------------------------------------------- 前置检查
def preflight(allow_dirty: bool, allow_branch: bool) -> tuple[bool, str]:
    """返回 (能否继续, 当前分支)。"""
    rule("1/6 前置检查")

    rc, _, _ = _git("rev-parse", "--is-inside-work-tree")
    if rc != 0:
        bad("当前目录不是 git 仓库")
        return False, ""
    ok("git 仓库")

    rc, branch, _ = _git("rev-parse", "--abbrev-ref", "HEAD")
    branch = branch or BRANCH_DEFAULT
    if rc != 0 or not branch:
        bad("读不到当前分支")
        return False, ""
    if branch != BRANCH_DEFAULT and not allow_branch:
        bad("当前在 %s，不在 %s（确实要从这里发版就加 --allow-branch）" % (branch, BRANCH_DEFAULT))
        return False, branch
    ok("分支 %s" % branch)

    rc, remote, _ = _git("remote", "get-url", "origin")
    if rc != 0 or not remote:
        bad("没有 origin 远端")
        return False, branch
    ok("远端 %s" % remote)

    rc, dirty, _ = _git("status", "--porcelain")
    changed = [ln for ln in dirty.splitlines() if ln.strip()]
    if changed and not allow_dirty:
        bad("工作区不干净（%d 个文件未提交）——先提交或 stash，别带着半成品发版" % len(changed))
        for ln in changed[:8]:
            print("       " + ln)
        if len(changed) > 8:
            print("       ... 另有 %d 个" % (len(changed) - 8))
        return False, branch
    if changed:
        warn("工作区有 %d 个未提交改动（--allow-dirty 已放行，这些改动**不会**被打进本次发版）" % len(changed))
    else:
        ok("工作区干净")

    return True, branch


# ---------------------------------------------------------------- 版本计算
def next_version(kind: str, explicit: str) -> str:
    cur = vs.semver(vs.version())
    if explicit:
        v = explicit.lstrip("v")
        if not re.fullmatch(r"\d+\.\d+\.\d+", v):
            raise SystemExit("❌ 版本号格式应为 X.Y.Z，收到 %r" % explicit)
        if vs.cmp_ver(v, "%d.%d.%d" % cur) <= 0:
            raise SystemExit("❌ 新版本 %s 必须大于当前 v%s" % (v, "%d.%d.%d" % cur))
        return v
    idx = {"major": 0, "minor": 1, "patch": 2}[kind]
    nxt = list(cur)
    nxt[idx] += 1
    for i in range(idx + 1, 3):
        nxt[i] = 0
    return "%d.%d.%d" % tuple(nxt)


# ---------------------------------------------------------------- 发布后校验
def verify_remote(expect: str, tries: int = 8, gap: float = 5.0) -> bool:
    """轮询 Contents API，确认远端真能读到新版本。

    push 返回 0 只说明 git 传上去了，不代表同事的 `--check` 立刻能读到。
    这里真读一次，把「我以为发了」变成「确实发了」。
    """
    for i in range(1, tries + 1):
        doc = vs.fetch_github()
        got = str((doc or {}).get("version") or "")
        if got == expect:
            print("     第 %d 次读取：v%s ✅" % (i, got))
            return True
        print("     第 %d 次读取：%s，等待 %.0fs 重试" % (i, ("v" + got) if got else "未读到", gap))
        if i < tries:
            time.sleep(gap)
    return False


def notify_text(ver: str, breaking: bool, highlights: list[str]) -> str:
    lines = ["📢 项目已更新到 v%s（%s）" % (ver, datetime.now().strftime("%Y-%m-%d")), ""]
    if breaking:
        lines += ["⚠️ **本次为破坏性变更**：不更新将无法运行（旧版会被拦住）。", ""]
    for h in highlights[:3]:
        lines.append("· " + h)
    lines += ["",
              "更新方式（在项目目录执行，配置文件不会被动）：",
              "  python scripts/install.py --update",
              "",
              "更新完自查：python scripts/doctor.py"]
    if not breaking:
        lines += ["", "（不更新也能继续用，只是没有本次的改进）"]
    return "\n".join(lines)


# ---------------------------------------------------------------- 主流程
def main() -> int:
    ap = argparse.ArgumentParser(
        description="一键发版：bump → version.json → commit → tag → push → 线上校验")
    ap.add_argument("--kind", choices=["major", "minor", "patch"], default="patch",
                    help="版本级别（默认 patch）")
    ap.add_argument("--version", default="", help="直接指定版本号，如 1.2.0")
    ap.add_argument("--breaking", action="store_true", help="破坏性变更：旧版会被拒绝运行")
    ap.add_argument("--note", default="", help="一句话说明，写进 CHANGELOG")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不改文件不推送")
    ap.add_argument("--no-push", action="store_true", help="本地 commit + tag，先不推送")
    ap.add_argument("--retry-push", action="store_true",
                    help="上次 push 失败时补推（不重新 bump）")
    ap.add_argument("--allow-dirty", action="store_true", help="允许工作区有未提交改动")
    ap.add_argument("--allow-branch", action="store_true", help="允许从非 main 分支发版")
    ap.add_argument("--skip-verify", action="store_true", help="跳过线上校验（不建议）")
    a = ap.parse_args()

    # ---- 补推模式：完全不碰版本文件
    if a.retry_push:
        cur = vs.version()
        tag = "v" + cur
        rule("补推 v%s" % cur)
        rc, _, err = _git("push", "origin", "HEAD")
        if rc != 0:
            bad("push 分支失败：%s" % err[:200])
            return 1
        ok("分支已推")
        rc, _, err = _git("push", "origin", tag)
        if rc != 0 and "already exists" not in err and "up-to-date" not in err:
            bad("push tag 失败：%s" % err[:200])
            return 1
        ok("tag %s 已推" % tag)
        return 0 if (a.skip_verify or verify_remote(cur)) else 1

    good, branch = preflight(a.allow_dirty, a.allow_branch)
    if not good:
        return 1

    # ---- 算版本
    rule("2/6 计算版本")
    cur = vs.version()
    new = next_version(a.kind, a.version)
    tag = "v" + new
    print("  v%s → v%s%s" % (cur, new, "（BREAKING）" if a.breaking else ""))

    rc, _, _ = _git("rev-parse", "--verify", "refs/tags/%s" % tag)
    if rc == 0:
        bad("tag %s 已存在。重打 tag 会让已更新的人和新更新的人拿到不同内容，拒绝。" % tag)
        print("       要么换版本号（--version），要么先删：git tag -d %s && git push origin :%s"
              % (tag, tag))
        return 1
    ok("tag %s 可用" % tag)

    if a.dry_run:
        rule("3/6 dry-run：以下文件将被修改")
        print("  VERSION        %s → %s" % (cur, new))
        print("  CHANGELOG.md   顶部插入 v%s 段" % new)
        print("  version.json   重写（version=%s, breaking=%s）" % (new, a.breaking))
        rule("4/6 dry-run：将执行的 git 命令")
        print("  git add VERSION CHANGELOG.md version.json")
        print("  git commit -m \"chore(release): %s\"" % tag)
        print("  git tag -a %s" % tag)
        print("  git push origin %s && git push origin %s" % (branch, tag))
        rule("5/6 dry-run：预计的同事通知")
        print(notify_text(new, a.breaking, ["（dry-run 不生成）"]))
        print("\n（dry-run 结束，未改动任何文件）")
        return 0

    # ---- 改文件
    rule("3/6 写版本文件")
    vs.bump(_kind_for(cur, new), breaking=a.breaking, note=a.note, explicit=new)
    ok("VERSION = %s" % new)

    doc = vs.publish_json(breaking=a.breaking)
    if doc["version"] != new:
        bad("version.json 写成了 %s，与目标 %s 不一致（bump 逻辑异常）" % (doc["version"], new))
        return 1
    ok("version.json 已更新（min_supported=%s, %d 条亮点）"
       % (doc["min_supported"], len(doc["highlights"])))
    for h in doc["highlights"]:
        print("       · " + h)

    rc, head, _ = _git("rev-parse", "HEAD")
    doc["commit"] = head[:7] if rc == 0 else "unknown"
    with open(vs.DIST_FILE, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
        f.write("\n")

    # ---- commit + tag
    rule("4/6 提交并打 tag")
    rc, _, err = _git("add", "VERSION", "CHANGELOG.md", "version.json")
    if rc != 0:
        bad("git add 失败：%s" % err[:200])
        return 1
    msg = "chore(release): %s" % tag
    if a.breaking:
        msg += "\n\nBREAKING: 本次为破坏性变更，旧版将被拒绝运行。"
    rc, _, err = _git("commit", "-m", msg)
    if rc != 0:
        bad("git commit 失败：%s" % err[:300])
        return 1
    ok("commit 已建")

    rc, _, err = _git("tag", "-a", tag, "-m", "Release %s" % tag)
    if rc != 0:
        bad("git tag 失败：%s" % err[:200])
        return 1
    ok("tag %s 已建" % tag)

    if a.no_push:
        rule("5/6 已按要求停在本地")
        print("  推送：python scripts/release.py --retry-push")
        return 0

    # ---- push
    rule("5/6 推送到 origin")
    rc, _, err = _git("push", "origin", branch)
    if rc != 0:
        bad("push 分支失败：%s" % err[:300])
        print("\n本地 commit 和 tag 都在，网络恢复后执行：")
        print("  python scripts/release.py --retry-push")
        return 1
    ok("分支 %s 已推" % branch)

    rc, _, err = _git("push", "origin", tag)
    if rc != 0:
        bad("push tag 失败：%s" % err[:300])
        print("\n分支已推、tag 未推。恢复后：python scripts/release.py --retry-push")
        return 1
    ok("tag %s 已推（GitHub Actions 将自动建 Release）" % tag)

    # ---- 线上校验
    rule("6/6 线上校验（确认同事真能读到）")
    verified = a.skip_verify or verify_remote(new)
    if verified and not a.skip_verify:
        ok("Contents API 已返回 v%s" % new)
    elif not verified:
        bad("已推送但远端仍读不到 v%s" % new)
        print("  可能原因：GitHub 侧延迟 / 仓库地址变更 / 网络抖动")
        print("  复查：python scripts/version.py --sources")

    # ---- 收尾
    rule("发版完成")
    print("  Release 页：https://github.com/%s/releases/tag/%s" % (vs.repo_slug() or "<owner/repo>", tag))
    print("  Actions：  https://github.com/%s/actions" % (vs.repo_slug() or "<owner/repo>"))
    print("\n--- 直接复制给同事 ---\n")
    print(notify_text(new, a.breaking, doc["highlights"]))
    return 0 if verified else 1


def _kind_for(cur: str, new: str) -> str:
    """--version 指定时反推 bump 级别，让 CHANGELOG 标题与版本号一致。"""
    a, b = vs.semver(cur), vs.semver(new)
    if b[0] != a[0]:
        return "major"
    if b[1] != a[1]:
        return "minor"
    return "patch"


if __name__ == "__main__":
    sys.exit(main())
