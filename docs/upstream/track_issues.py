#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上游 issue 进度巡检（iflytek/astron-rpa #909~#918）。

用途
----
把「关注官方修复进度」变成一条可重复执行的命令：拉取已上报 issue 的最新状态，
与本地基线（`_issue_baseline.json`）比对，**只输出有变化的条目**，并刷新
`ISSUE_STATE.md` 快照。无变化时不产生噪音，适合挂自动化定时跑。

用法
----
    python docs/upstream/track_issues.py              # 比对 + 更新基线 + 重写快照
    python docs/upstream/track_issues.py --dry-run    # 只看，不写文件
    python docs/upstream/track_issues.py --json       # 机器可读输出

凭据
----
优先取环境变量 `GITHUB_TOKEN`，否则用本机 git 凭据（`git credential fill`）。
两者都没有时退出码 3，不影响项目其他流程。

判定口径（重要）
--------------
- issue 被 close ≠ 已修复。**只有 verify_main.py 复跑翻成 FIXED 才算修好**，
  本脚本只负责「提醒去看」。
- 维护者留言「已修复」但没关 → 同样只算「有回复」，不算 FIXED。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

try:  # Windows 控制台默认 GBK，中文会炸
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

REPO = "iflytek/astron-rpa"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
PUB = os.path.join(HERE, "_published.json")
BASE = os.path.join(HERE, "_issue_baseline.json")
SNAP = os.path.join(HERE, "ISSUE_STATE.md")
TZ = timezone(timedelta(hours=8))


# ---------------------------------------------------------------- 凭据 / HTTP
def token() -> str | None:
    t = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if t:
        return t.strip()
    try:
        out = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            capture_output=True, text=True, timeout=20,
        ).stdout
    except Exception:
        return None
    for line in out.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    return None


def api(path: str, tok: str) -> dict:
    req = urllib.request.Request(
        "https://api.github.com" + path,
        headers={
            "Authorization": "token " + tok,
            "Accept": "application/vnd.github+json",
            "User-Agent": "astron-rpa-issue-tracker",
        },
    )
    return json.load(urllib.request.urlopen(req, timeout=40))


def issue_numbers() -> list[int]:
    """已上报的 issue 号：_published.json 的值 + 历史遗留（909 早期单独提的）。"""
    nums: list[int] = []
    if os.path.exists(PUB):
        for url in json.load(open(PUB, encoding="utf-8")).values():
            try:
                nums.append(int(str(url).rstrip("/").rsplit("/", 1)[1]))
            except (ValueError, IndexError):
                pass
    nums = sorted(set(nums))
    # 909 是 A20，比批量上报早一天，历史上没进 _published.json
    for legacy in (909,):
        if legacy not in nums:
            nums.append(legacy)
    return sorted(nums)


def fetch(nums: list[int], tok: str) -> list[dict]:
    rows = []
    for n in nums:
        d = api("/repos/%s/issues/%d" % (REPO, n), tok)
        rows.append({
            "number": d["number"],
            "title": d["title"],
            "state": d["state"],
            "state_reason": d.get("state_reason"),
            "comments": d["comments"],
            "labels": sorted(l["name"] for l in d.get("labels", [])),
            "created_at": d["created_at"],
            "updated_at": d["updated_at"],
            "closed_at": d.get("closed_at"),
            "html_url": d["html_url"],
        })
    return rows


# ---------------------------------------------------------------- 比对
def diff(old: dict, new: list[dict]) -> list[str]:
    """返回人类可读的变化描述；old 结构 {str(n): row}。"""
    changes: list[str] = []
    for row in new:
        k = str(row["number"])
        o = old.get(k)
        if not o:
            changes.append("#%d 新纳入跟踪：%s" % (row["number"], row["state"]))
            continue
        tag = "#%d" % row["number"]
        if o.get("state") != row["state"]:
            changes.append("%s 状态：%s → **%s**%s" % (
                tag, o.get("state"), row["state"],
                "（reason=%s）" % row["state_reason"] if row.get("state_reason") else ""))
        if o.get("comments", 0) != row["comments"]:
            changes.append("%s 评论数：%s → **%s**（维护者可能回复了）"
                           % (tag, o.get("comments", 0), row["comments"]))
        if o.get("labels", []) != row["labels"]:
            changes.append("%s label：%s → **%s**"
                           % (tag, o.get("labels") or "无", row["labels"] or "无"))
        if o.get("updated_at") != row["updated_at"]:
            changes.append("%s 更新时间：%s → %s" % (
                tag, o.get("updated_at"), row["updated_at"]))
    # 基线里有、本次拉不到的（极少，可能是被删）
    for k, o in old.items():
        if not any(str(r["number"]) == k for r in new):
            changes.append("#%s 拉取不到（可能被删除或转私有）" % k)
    return changes


# ---------------------------------------------------------------- 快照
def write_snapshot(rows: list[dict], changes: list[str]) -> None:
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    o = sum(1 for r in rows if r["state"] == "open")
    c = len(rows) - o
    L = [
        "# 上游 issue 跟踪快照",
        "",
        "> 自动生成，勿手改。生成时间：**%s**（UTC+8）" % now,
        "> 重新生成：`python docs/upstream/track_issues.py`",
        "",
        "| Issue | 状态 | 评论 | label | 最后更新 | 标题 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        L.append("| [#%d](%s) | %s | %d | %s | %s | %s |" % (
            r["number"], r["html_url"], r["state"], r["comments"],
            ",".join(r["labels"]) or "—", (r["updated_at"] or "")[:10], r["title"][:58]))
    L += [
        "",
        "**合计**：%d 个 · open **%d** · closed **%d**" % (len(rows), o, c),
        "",
        "## 本次变化",
        "",
    ]
    L += (["- " + x for x in changes] if changes else ["（无变化）"])
    L += [
        "",
        "> ⚠️ **issue 被关闭不等于已修复**。必须跑 `python docs/upstream/verify_main.py`，" ,
        "> 结论从 `CONFIRMED` 翻成 `FIXED` 才算真的修好；然后把 `平台缺陷清单.md` §0.5",
        "> 与 §1 对应行的 📤 改成 ✅。",
        "",
    ]
    open(SNAP, "w", encoding="utf-8").write("\n".join(L))


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description="巡检上游 issue 进度")
    ap.add_argument("--dry-run", action="store_true", help="不写基线/快照")
    ap.add_argument("--json", action="store_true", help="输出机器可读结果")
    a = ap.parse_args()

    tok = token()
    if not tok:
        print("!! 取不到 GitHub 凭据（GITHUB_TOKEN 与 git credential 都没有），跳过本次巡检")
        return 3

    nums = issue_numbers()
    try:
        rows = fetch(nums, tok)
    except urllib.error.HTTPError as e:
        print("!! GitHub API 失败 %s：%s" % (e.code, e.read().decode("utf-8", "replace")[:300]))
        return 4
    except urllib.error.URLError as e:
        print("!! 网络不可达：%s" % e)
        return 4

    old, old_at = {}, ""
    if os.path.exists(BASE):
        raw = json.load(open(BASE, encoding="utf-8"))
        old, old_at = raw.get("issues", {}), raw.get("_checked_at", "")

    changes = diff(old, rows)

    if a.json:
        print(json.dumps({"checked_at": datetime.now(TZ).isoformat(timespec="seconds"),
                          "total": len(rows), "changes": changes, "issues": rows},
                         ensure_ascii=False, indent=1))
    else:
        print("巡检 %d 个 issue（%s）" % (len(rows), REPO))
        for r in rows:
            print("  #%-4d %-7s 评论 %-2d label=%s" % (
                r["number"], r["state"], r["comments"], ",".join(r["labels"]) or "—"))
        print()
        if changes:
            print("发现 %d 处变化：" % len(changes))
            for x in changes:
                print("  · " + x)
            print("\n→ 下一步：python docs/upstream/verify_main.py 复跑确认是否真的修好")
        else:
            print("无变化（与 %s 基线一致）" % (old_at or "上次"))

    if not a.dry_run:
        json.dump({"_checked_at": datetime.now(TZ).strftime("%Y-%m-%d %H:%M"),
                   "_repo": REPO,
                   "issues": {str(r["number"]): r for r in rows}},
                  open(BASE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        write_snapshot(rows, changes)

    return 0


if __name__ == "__main__":
    sys.exit(main())
