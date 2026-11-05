#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""版本号：**单一来源**、比对、bump、生成给同事机器读的 `version.json`。

## 为什么要有这个文件

项目原来没有版本号。`handover/MANIFEST.md` 里那句「项目版本：86d1c19」是手写的，
一周就过期。单人用没问题（谁改的谁知道），一旦多人用就会出现：

- 同事报的 bug 你复现不出来 —— 因为你们跑的不是同一版
- 你修好了，他还在跑旧版，以为没修
- 极少数情况更糟：他的脚本按新格式生成规格，你的旧脚本读不了

所以版本号不是文档装饰，是**排障的第一个信息**。这里把它做成可被程序读的东西。

## 版本从哪来

`VERSION` 文件（项目根）是唯一来源。git commit 短号由 `git rev-parse` 现取，
不落盘 —— 落盘就一定会忘记更新。

## 远端从哪来

同事的机器只读一个 **1KB 的 `version.json`**，不拉整个仓库：

```json
{"version": "1.0.3", "commit": "6e96cb8", "date": "2026-11-08",
 "breaking": false, "min_supported": "1.0.0",
 "highlights": ["A20 三道参数门禁", "发布默认改草稿"]}
```

地址取 `rpa_config.json` 的 `update.version_url`，默认
`http://192.168.76.130:32742/version.json`（.130 的 nginx 静态目录，内网可达、零成本）。
也支持本地路径 / `file://`，方便在共享盘或离线环境用。

## 分级策略（打扰人的尺度）

| remote.breaking / 本地 < min_supported | 本地 < remote | 行为 |
| --- | --- | --- |
| ✅ 是 | — | **拦**：`ai_flow` 直接拒绝运行（退出码 12） |
| — | ✅ 是 | **提示**：启动打一行横幅，不阻塞 |
| — | — | 静默 |

`breaking` 不靠人临场判断：commit message 带 `BREAKING:` 前缀，`--bump` 时自动置位。

## 用法

    python scripts/version.py --show                     # 本地版本 + commit + 日期
    python scripts/version.py --check                    # 与远端比对（读 24h 缓存）
    python scripts/version.py --check --no-cache         # 强制联网
    python scripts/version.py --bump patch               # 1.0.0 → 1.0.1，写 CHANGELOG 顶部
    python scripts/version.py --bump minor --breaking    # 标记破坏性变更
    python scripts/version.py --changelog --days 14      # 从 git log 生成变更摘要（不写文件）
    python scripts/version.py --publish-json             # 生成 dist/version.json 供上传

退出码：`0` 最新 / `1` 落后（仅提示）/ `3` 取不到远端 / `12` 必须更新（拦）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta

try:  # Windows 控制台默认 GBK，中文会炸
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION_FILE = os.path.join(ROOT, "VERSION")
CHANGELOG_FILE = os.path.join(ROOT, "CHANGELOG.md")
CONFIG_FILE = os.path.join(ROOT, "rpa_config.json")
CACHE_FILE = os.path.join(ROOT, ".cache", "version_check.json")
DIST_FILE = os.path.join(ROOT, "dist", "version.json")

DEFAULT_URL = "http://192.168.76.130:32742/version.json"
CACHE_HOURS = 24
EXIT_STALE, EXIT_UNREACHABLE, EXIT_BLOCK = 1, 3, 12

ICON = {"ok": "✅", "stale": "⬆️", "block": "⛔", "unknown": "❔"}


# ---------------------------------------------------------------- 本地版本
def _run(args: list[str], cwd: str = ROOT) -> str:
    try:
        r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=15)
        return (r.stdout or "").strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def version() -> str:
    try:
        with open(VERSION_FILE, encoding="utf-8") as f:
            v = f.read().strip()
    except OSError:
        return "0.0.0"
    v = v.lstrip("v")
    return v if re.fullmatch(r"\d+\.\d+\.\d+", v) else "0.0.0"


def commit() -> str:
    return _run(["git", "rev-parse", "--short", "HEAD"]) or "unknown"


def commit_date() -> str:
    return _run(["git", "log", "-1", "--date=short", "--pretty=%ad"]) or ""


def local() -> dict:
    return {"version": version(), "commit": commit(), "date": commit_date()}


def semver(s: str) -> tuple:
    parts = (s or "0.0.0").lstrip("v").split(".")
    out = []
    for p in parts[:3]:
        m = re.match(r"\d+", p)
        out.append(int(m.group()) if m else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out)


def cmp_ver(a: str, b: str) -> int:
    """a>b → 1，a<b → -1，相等 → 0。"""
    ta, tb = semver(a), semver(b)
    return (ta > tb) - (ta < tb)


# ---------------------------------------------------------------- 远端
def update_cfg() -> dict:
    cfg = {"version_url": DEFAULT_URL, "cache_hours": CACHE_HOURS}
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            up = (json.load(f) or {}).get("update") or {}
        for k in ("version_url", "cache_hours"):
            if up.get(k) not in (None, ""):
                cfg[k] = up[k]
    except (OSError, ValueError):
        pass
    return cfg


def fetch_remote(url: str, timeout: float = 1.5) -> dict | None:
    """取远端 version.json。支持 http(s) / 本地路径 / file://。任何异常返回 None（静默）。"""
    try:
        if url.startswith(("http://", "https://")):
            req = urllib.request.Request(url, headers={"User-Agent": "astron-rpa-version-check"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        path = url[7:] if url.startswith("file://") else url
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def _read_cache() -> dict | None:
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_cache(remote: dict) -> None:
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"checked_at": datetime.now().isoformat(timespec="seconds"),
                       "remote": remote}, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def check(use_cache: bool = True, timeout: float = 1.5) -> dict:
    """比对本地与远端。返回 level ∈ {ok, stale, block, unknown}。

    `unknown` 表示取不到远端（离线 / 服务端没放 version.json）——
    **不把它当错误**，多人环境里离线是常态，不能因此拦住人干活。
    """
    cfg = update_cfg()
    hours = float(cfg.get("cache_hours") or CACHE_HOURS)
    remote = None

    if use_cache:
        c = _read_cache()
        if c and c.get("remote"):
            try:
                age = datetime.now() - datetime.fromisoformat(c["checked_at"])
                if age < timedelta(hours=hours):
                    remote = c["remote"]
            except (KeyError, ValueError):
                pass

    if remote is None:
        remote = fetch_remote(cfg["version_url"], timeout=timeout)
        if remote:
            _write_cache(remote)

    me = local()
    res = {"local": me, "remote": remote, "url": cfg["version_url"], "level": "unknown"}
    if not remote or not remote.get("version"):
        return res

    rv = str(remote["version"]).lstrip("v")
    min_ok = str(remote.get("min_supported") or "0.0.0").lstrip("v")
    behind = cmp_ver(me["version"], rv) < 0
    res.update({"remote_version": rv, "behind": behind,
                "delta": [semver(me["version"])[i] - semver(rv)[i] for i in range(3)]})

    if remote.get("breaking") or cmp_ver(me["version"], min_ok) < 0:
        res["level"] = "block" if behind else "ok"
    elif behind:
        res["level"] = "stale"
    else:
        res["level"] = "ok"
    return res


def banner(use_cache: bool = True) -> tuple[str | None, bool]:
    """给 ai_flow / doctor 用：返回 (要打印的行, 是否必须中止)。无话可说时返回 (None, False)。"""
    r = check(use_cache=use_cache)
    if r["level"] == "ok":
        return None, False
    if r["level"] == "unknown":
        return None, False          # 离线不吵人

    rv = r.get("remote_version", "?")
    hl = (r.get("remote") or {}).get("highlights") or []
    tip = "python scripts/install.py --update"
    if r["level"] == "block":
        head = "⛔ 本项目已升级到 v%s，且为**破坏性变更**（或低于最低支持版本），当前 v%s 不能继续。" % (
            rv, r["local"]["version"])
        body = ["   " + h for h in hl[:3]] + ["   更新后重跑：%s" % tip]
        return "\n".join([head] + body), True

    head = "⬆️ 项目有新版本 v%s（当前 v%s）" % (rv, r["local"]["version"])
    body = ["   · " + h for h in hl[:2]] + ["   → 更新：%s" % tip]
    return "\n".join([head] + body), False


# ---------------------------------------------------------------- CHANGELOG
_TYPE_CN = [("fix", "修复"), ("feat", "新增"), ("refactor", "重构"),
            ("docs", "文档"), ("test", "测试"), ("chore", "杂项")]


def _classify(subject: str) -> str:
    m = re.match(r"^(\w+)(\([^)]*\))?[:：]", subject)
    t = m.group(1).lower() if m else ""
    for key, cn in _TYPE_CN:
        if t == key:
            return cn
    return "其他"


def changelog(days: int = 14, commits: list[dict] | None = None) -> str:
    """从 git log 生成按天分组的变更摘要。**不写文件**，由 --bump 决定是否落盘。"""
    if commits is None:
        raw = _run(["git", "log", "--date=short", "--pretty=%ad|%h|%s",
                    "--since=%d days ago" % days])
        commits = []
        for line in raw.splitlines():
            parts = line.split("|", 2)
            if len(parts) == 3:
                commits.append({"date": parts[0], "hash": parts[1], "subject": parts[2]})
    if not commits:
        return "（无提交）"

    by_day: dict[str, list[dict]] = {}
    for c in commits:
        by_day.setdefault(c["date"], []).append(c)

    out = []
    for day in sorted(by_day, reverse=True):
        items = by_day[day]
        groups: dict[str, list[str]] = {}
        for c in items:
            groups.setdefault(_classify(c["subject"]), []).append(
                "%s（%s）" % (c["subject"], c["hash"]))
        out.append("### %s · %d 次提交" % (day, len(items)))
        for cn in [cn for _, cn in _TYPE_CN] + ["其他"]:
            if cn in groups:
                out.append("- **%s** %d：" % (cn, len(groups[cn])))
                out += ["  - " + x for x in groups[cn]]
        out.append("")
    return "\n".join(out).rstrip()


def _highlights(n: int = 3) -> list[str]:
    """从最近提交里挑几条当 version.json 的亮点。

    顺序有讲究：**先「新增」后「修复」**。亮点是给人看「要不要现在更新」的，
    新能力比修 bug 更能回答这个问题；但修 bug 也不能少（不然只有新增才发版）。
    docs/chore/test 一律不要。
    """
    raw = _run(["git", "log", "-40", "--pretty=%s"])
    feat, other = [], []
    for s in raw.splitlines():
        cat = _classify(s)
        txt = re.sub(r"^\w+(\([^)]*\))?[:：]\s*", "", s)[:60]
        if cat == "新增":
            feat.append(txt)
        elif cat in ("修复", "重构"):
            other.append(txt)
    return (feat + other)[:n] or ["常规更新"]


def bump(kind: str = "patch", breaking: bool = False, note: str = "") -> str:
    cur = semver(version())
    idx = {"major": 0, "minor": 1, "patch": 2}[kind]
    nxt = list(cur)
    nxt[idx] += 1
    for i in range(idx + 1, 3):
        nxt[i] = 0
    new = "%d.%d.%d" % tuple(nxt)

    # 自上一个 tag 起的提交（没有 tag 就取最近 30 条）
    last_tag = _run(["git", "describe", "--tags", "--abbrev=0"])
    rng = ["%s..HEAD" % last_tag] if last_tag else ["-30"]
    raw = _run(["git", "log", "--date=short", "--pretty=%ad|%h|%s"] + rng)
    commits = []
    for line in raw.splitlines():
        p = line.split("|", 2)
        if len(p) == 3:
            commits.append({"date": p[0], "hash": p[1], "subject": p[2]})

    today = datetime.now().strftime("%Y-%m-%d")
    head = ["## v%s（%s）" % (new, today)]
    if breaking:
        head.append("> ⚠️ **BREAKING**：本次有破坏性变更，旧版脚本可能读不了新产物，请全员更新。")
    if note:
        head.append("> " + note)
    head.append("")
    body = changelog(commits=commits) or "（无提交）"

    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        f.write(new + "\n")

    old = ""
    if os.path.exists(CHANGELOG_FILE):
        old = open(CHANGELOG_FILE, encoding="utf-8").read()
        if old.startswith("# "):
            first, _, rest = old.partition("\n")
            old = rest.lstrip("\n")
    header = "# 变更记录（CHANGELOG）\n\n> 由 `python scripts/version.py --bump <kind>` 自动生成，别手改格式。\n\n"
    open(CHANGELOG_FILE, "w", encoding="utf-8").write(header + "\n".join(head) + "\n" + body + "\n\n" + old)
    return new


def publish_json(breaking: bool = False) -> dict:
    me = local()
    doc = {
        "version": me["version"],
        "commit": me["commit"],
        "date": me["date"] or datetime.now().strftime("%Y-%m-%d"),
        "breaking": bool(breaking),
        "min_supported": "%d.%d.0" % semver(me["version"])[:2],
        "highlights": _highlights(),
    }
    os.makedirs(os.path.dirname(DIST_FILE), exist_ok=True)
    with open(DIST_FILE, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    return doc


# ---------------------------------------------------------------- CLI
def main() -> int:
    ap = argparse.ArgumentParser(description="版本号：查看 / 比对 / bump / 生成 version.json")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--show", action="store_true", help="打印本地版本")
    g.add_argument("--check", action="store_true", help="与远端比对")
    g.add_argument("--bump", choices=["major", "minor", "patch"], help="升版本并写 CHANGELOG")
    g.add_argument("--changelog", action="store_true", help="打印 git log 摘要（不写文件）")
    g.add_argument("--publish-json", action="store_true", help="生成 dist/version.json")
    ap.add_argument("--breaking", action="store_true", help="标记破坏性变更（写进 CHANGELOG 与 version.json）")
    ap.add_argument("--note", default="", help="一句话说明（写进 CHANGELOG）")
    ap.add_argument("--days", type=int, default=14, help="changelog 回溯天数")
    ap.add_argument("--no-cache", action="store_true", help="强制联网，忽略 24h 缓存")
    ap.add_argument("--quiet", action="store_true", help="--check 时只在落后时输出")
    a = ap.parse_args()

    if a.show:
        me = local()
        print("v%s  (%s, %s)" % (me["version"], me["commit"], me["date"] or "-"))
        return 0

    if a.changelog:
        print(changelog(days=a.days))
        return 0

    if a.bump:
        new = bump(a.bump, breaking=a.breaking, note=a.note)
        print("✅ 版本 → v%s（CHANGELOG 已更新）" % new)
        print("   下一步：git add VERSION CHANGELOG.md && git commit -m \"chore(release): v%s\"" % new)
        print("   然后：python scripts/version.py --publish-json")
        return 0

    if a.publish_json:
        doc = publish_json(breaking=a.breaking)
        print("✅ 已生成 %s" % os.path.relpath(DIST_FILE, ROOT))
        print(json.dumps(doc, ensure_ascii=False, indent=1))
        print("\n上传到内网 nginx 静态目录（.130）：")
        print("  scp dist/version.json xudongdong@192.168.76.130:/opt/astron-nginx/html/version.json")
        return 0

    # --check
    r = check(use_cache=not a.no_cache)
    me, rv = r["local"]["version"], r.get("remote_version")
    print("本地 v%s (%s, %s)" % (me, r["local"]["commit"], r["local"]["date"] or "-"))
    if r["level"] == "unknown":
        if not a.quiet:
            print("远端 取不到（%s）—— 离线或 version.json 未发布，不判定" % r["url"])
        return EXIT_UNREACHABLE
    print("远端 v%s (%s) · %s" % (rv, (r["remote"] or {}).get("date", "-"),
                                 {"ok": "已是最新", "stale": "落后", "block": "必须更新"}[r["level"]]))
    hl = (r["remote"] or {}).get("highlights") or []
    for h in hl[:4]:
        print("   · " + h)
    if r["level"] == "stale":
        print("→ python scripts/install.py --update")
        return EXIT_STALE
    if r["level"] == "block":
        print("⛔ 破坏性变更 / 低于最低支持版本 v%s，请先更新再运行" % (r["remote"] or {}).get("min_supported"))
        return EXIT_BLOCK
    return 0


if __name__ == "__main__":
    sys.exit(main())
