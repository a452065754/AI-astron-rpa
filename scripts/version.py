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

同事的机器只读一个 **1KB 的 `version.json`**（**已入库，在仓库根**），不拉整个仓库：

```json
{"version": "1.0.3", "commit": "6e96cb8", "date": "2026-11-08",
 "breaking": false, "min_supported": "1.0.0",
 "highlights": ["A20 三道参数门禁", "发布默认改草稿"]}
```

### 为什么默认走 GitHub Contents API 而不是 raw 地址

2026-11-05 实测（当前网络出口）：

| 端点 | 结果 | 结论 |
| --- | --- | --- |
| `api.github.com/repos/.../contents/version.json` | **200** | ✅ 可用，实时、无 CDN 缓存 |
| `github.com/.../raw/...` | 302 → 超时 | ❌ 跟随重定向后落到 raw，断 |
| `raw.githubusercontent.com` | **连不上** | ❌ 不能用 |
| `cdn.jsdelivr.net` / `ghproxy` / `gitmirror` | 全 000 | ❌ 不能用 |

所以**主源是 Contents API**（返回 base64，本地解一次即可）。它唯一的约束是匿名 60 次/小时 ——
配了 24h 缓存后每人每天 1 次，绰绰有余；要更高额度就设 `GITHUB_TOKEN`（5000 次/小时）。

配置在 `rpa_config.json` 的 `update.sources`，**有序列表，逐个尝试直到成功**：

```json
"update": {
  "sources": [
    "github",
    "https://raw.githubusercontent.com/owner/repo/main/version.json",
    "\\\\fileserver\\astron\\version.json"
  ],
  "cache_hours": 24
}
```

- `"github"` 是占位符，`owner/repo` 自动从 `git remote get-url origin` 解析，**不用硬编码**
- 也支持任意 http(s) / 本地路径 / `file://`
- 老配置只有 `version_url` 时自动退化成单源，行为不变
- 一个都取不到 → 判定 `unknown`，**静默放行**（离线是常态）

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
    python scripts/version.py --publish-json             # 生成仓库根 version.json 供入库
    python scripts/version.py --sources                  # 逐个探测源，打印连通性与耗时（排障用）

退出码：`0` 最新 / `1` 落后（仅提示）/ `3` 取不到远端 / `12` 必须更新（拦）

真正发版不要手动串这些命令，用 `python scripts/release.py`（见该文件）。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
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
DIST_FILE = os.path.join(ROOT, "version.json")   # 入库，仓库根（不是 dist/，dist 是打包产物）

DEFAULT_SOURCES = ["github"]      # 占位符，owner/repo 从 git remote 解析
CACHE_HOURS = 24
TIMEOUT = 3.0                     # api.github.com 国内 300~800ms，1.5s 太紧
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
def repo_slug() -> str:
    """从 git remote 解析 `owner/repo`，取不到返回空串。

    不硬编码仓库名：同事可能从 fork 或 Gitea 镜像拉，硬编码就查错版本了。
    """
    for remote in ("origin", "upstream", "gitea"):
        url = _run(["git", "remote", "get-url", remote])
        m = re.search(r"github\.com[:/]+([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", url or "")
        if m:
            return "%s/%s" % (m.group(1), m.group(2))
    return ""


def _gh_headers() -> dict:
    h = {"User-Agent": "astron-rpa-version-check",
         "Accept": "application/vnd.github+json"}
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:                                  # 匿名 60/h → 认证 5000/h
        h["Authorization"] = "Bearer " + tok
    return h


def _get_json(url: str, timeout: float = TIMEOUT, headers: dict | None = None) -> dict | None:
    """GET 一个 JSON。任何异常返回 None —— 版本检查永远不能把人拦住。"""
    try:
        req = urllib.request.Request(
            url, headers=headers or {"User-Agent": "astron-rpa-version-check"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            doc = json.loads(r.read().decode("utf-8"))
        return doc if isinstance(doc, dict) else None
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def fetch_github(slug: str = "", ref: str = "main", timeout: float = TIMEOUT) -> dict | None:
    """GitHub Contents API 读 `version.json`。

    为什么不用 raw：2026-11-05 实测 `raw.githubusercontent.com` **直接连不上**，
    `github.com/.../raw/...` 是 302 指过去、跟随同样断。只有 `api.github.com` 通。
    代价是返回 base64（解一次即可）+ 匿名 60 次/小时（配 24h 缓存后无感）。
    """
    slug = slug or repo_slug()
    if not slug:
        return None
    url = "https://api.github.com/repos/%s/contents/version.json?ref=%s" % (slug, ref)
    d = _get_json(url, timeout=timeout, headers=_gh_headers())
    if not d or not d.get("content"):
        return None
    try:
        doc = json.loads(base64.b64decode(d["content"]).decode("utf-8"))
    except (ValueError, KeyError, TypeError):
        return None
    if isinstance(doc, dict) and doc.get("version"):
        doc.setdefault("_source", "github:%s@%s" % (slug, ref))
        return doc
    return None


def fetch_github_release(slug: str = "", timeout: float = TIMEOUT) -> dict | None:
    """次级源：`releases/latest` 只有 tag，**没有** breaking / min_supported / highlights。

    只够回答「我落后了吗」，不够判断「必须更新吗」。标记 `_thin` 让上层降级处理。
    """
    slug = slug or repo_slug()
    if not slug:
        return None
    d = _get_json("https://api.github.com/repos/%s/releases/latest" % slug,
                  timeout=timeout, headers=_gh_headers())
    tag = str((d or {}).get("tag_name") or "").lstrip("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+", tag):
        return None
    return {"version": tag, "_thin": True, "highlights": [],
            "date": str((d or {}).get("published_at") or "")[:10],
            "_source": "github-release:%s" % slug}


_RAW_RE = re.compile(r"https?://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/")


def fetch_one(src: str, timeout: float = TIMEOUT) -> dict | None:
    """按源的形态分派：`github` → Contents API / URL → HTTP / 其余 → 本地路径。"""
    if src.strip().lower() in ("github", "gh"):
        return fetch_github(timeout=timeout)

    if src.startswith(("http://", "https://")):
        # 用户照网上教程填 raw 地址时自动改走 API —— 国内 raw 根本连不上
        m = _RAW_RE.match(src)
        if m:
            doc = fetch_github("%s/%s" % (m.group(1), m.group(2)), ref=m.group(3), timeout=timeout)
            if doc:
                return doc
        return _get_json(src, timeout=timeout)

    path = src[7:] if src.startswith("file://") else src
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) and doc.get("version") else None
    except (OSError, ValueError):
        return None


def update_cfg() -> dict:
    """返回 `{"sources": [...], "cache_hours": N}`。兼容只有 `version_url` 的老配置。"""
    cfg = {"sources": list(DEFAULT_SOURCES), "cache_hours": CACHE_HOURS}
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            up = (json.load(f) or {}).get("update") or {}
    except (OSError, ValueError):
        return cfg
    src = up.get("sources")
    if isinstance(src, list) and src:
        cfg["sources"] = [str(s) for s in src if str(s).strip()]
    elif up.get("version_url"):                       # 向后兼容
        cfg["sources"] = [str(up["version_url"])]
    try:
        cfg["cache_hours"] = float(up.get("cache_hours") or CACHE_HOURS)
    except (TypeError, ValueError):
        pass
    return cfg


def fetch_remote(sources: list[str] | None = None,
                 timeout: float = TIMEOUT) -> tuple[dict | None, str]:
    """按顺序试每个源，**第一个成功即返回**。全失败返回 `(None, "")`。"""
    for s in (sources if sources is not None else update_cfg()["sources"]):
        doc = fetch_one(s, timeout=timeout)
        if doc:
            return doc, str(doc.get("_source") or s)
    return None, ""


def probe_sources(timeout: float = TIMEOUT) -> list[dict]:
    """逐个源实测连通性 —— `--sources` 用，排障时比猜快。"""
    cfg = update_cfg()
    out = []
    for s in cfg["sources"]:
        t0 = time.perf_counter()
        doc = fetch_one(s, timeout=timeout)
        ms = int((time.perf_counter() - t0) * 1000)
        out.append({"source": s, "ok": bool(doc), "ms": ms,
                    "version": (doc or {}).get("version", ""),
                    "detail": (doc or {}).get("_source") or ("取不到" if not doc else "直读")})
    return out


def _read_cache() -> dict | None:
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_cache(remote: dict, source: str = "") -> None:
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"checked_at": datetime.now().isoformat(timespec="seconds"),
                       "source": source, "remote": remote}, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def check(use_cache: bool = True, timeout: float = TIMEOUT) -> dict:
    """比对本地与远端。返回 level ∈ {ok, stale, block, unknown}。

    `unknown` 表示所有源都取不到（离线 / 没发版 / 网络受限）——
    **不把它当错误**，多人环境里离线是常态，不能因此拦住人干活。
    """
    cfg = update_cfg()
    hours = float(cfg.get("cache_hours") or CACHE_HOURS)
    remote, source = None, ""

    if use_cache:
        c = _read_cache()
        if c and c.get("remote"):
            try:
                age = datetime.now() - datetime.fromisoformat(c["checked_at"])
                if age < timedelta(hours=hours):
                    remote, source = c["remote"], c.get("source") or "cache"
            except (KeyError, ValueError):
                pass

    if remote is None:
        remote, source = fetch_remote(cfg["sources"], timeout=timeout)
        if remote:
            _write_cache(remote, source)

    me = local()
    res = {"local": me, "remote": remote, "source": source, "label": source,
           "sources": cfg["sources"], "url": source, "level": "unknown"}
    if not remote or not remote.get("version"):
        return res

    rv = str(remote["version"]).lstrip("v")
    min_ok = str(remote.get("min_supported") or "0.0.0").lstrip("v")
    behind = cmp_ver(me["version"], rv) < 0
    res.update({"remote_version": rv, "behind": behind,
                "delta": [semver(me["version"])[i] - semver(rv)[i] for i in range(3)]})

    if remote.get("_thin"):
        # release 只有 tag，没有 breaking/min_supported —— 最多只敢判「落后」，绝不拦人
        res["level"] = "stale" if behind else "ok"
        res["thin"] = True
        return res

    if remote.get("breaking") or cmp_ver(me["version"], min_ok) < 0:
        res["level"] = "block" if behind else "ok"
    elif behind:
        res["level"] = "stale"
    else:
        res["level"] = "ok"
    return res


def _banner_text(level: str, rv: str, local_v: str, hl: list[str],
                 min_ok: str = "", thin: bool = False) -> tuple[str | None, bool]:
    """把判定结果渲染成给人看的两三行。`banner()` 和 `--demo` 共用，避免两套文案。"""
    tip = "python scripts/install.py --update"
    if level == "block":
        head = ("⛔ 本项目已升级到 v%s，且为**破坏性变更**（或低于最低支持版本），"
                "当前 v%s 不能继续。" % (rv, local_v))
        body = ["   " + h for h in hl[:3]] + ["   更新后重跑：%s" % tip]
        if min_ok:
            body.insert(0, "   最低支持版本：v%s" % min_ok)
        return "\n".join([head] + body), True

    if level == "stale":
        head = "⬆️ 项目有新版本 v%s（当前 v%s）" % (rv, local_v)
        body = ["   · " + h for h in hl[:2]] + ["   → 更新：%s" % tip]
        if thin:
            body.append("   （远端只有版本号，本次无法判断是否破坏性变更）")
        return "\n".join([head] + body), False

    return None, False


def banner(use_cache: bool = True) -> tuple[str | None, bool]:
    """给 ai_flow / doctor 用：返回 (要打印的行, 是否必须中止)。无话可说时返回 (None, False)。"""
    r = check(use_cache=use_cache)
    if r["level"] in ("ok", "unknown"):
        return None, False          # 同版本不打扰；离线更不吵人

    return _banner_text(r["level"], r.get("remote_version", "?"),
                        r["local"]["version"],
                        (r.get("remote") or {}).get("highlights") or [],
                        str((r.get("remote") or {}).get("min_supported") or ""),
                        bool(r.get("thin")))


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


_LAST_HIGHLIGHTS: list[str] = []   # bump() 本次算出的亮点，publish_json() 复用


def _pick_highlights(subjects: list[str], n: int = 3) -> list[str]:
    """从一组 commit 标题里挑亮点。**先「新增」后「修复」**。

    顺序有讲究：亮点是给人看「要不要现在更新」的，新能力比修 bug 更能回答这个问题；
    但修 bug 也不能少（不然只有新增才发版）。docs/chore/test 一律不要。
    """
    feat, other = [], []
    for s in subjects:
        cat = _classify(s)
        txt = re.sub(r"^\w+(\([^)]*\))?[:：]\s*", "", s)[:60]
        if cat == "新增":
            feat.append(txt)
        elif cat in ("修复", "重构"):
            other.append(txt)
    return (feat + other)[:n] or ["常规更新"]


def _highlights(n: int = 3) -> list[str]:
    """取最近提交的亮点。发版时优先用 `_LAST_HIGHLIGHTS`（只算本次区间）。"""
    raw = _run(["git", "log", "-40", "--pretty=%s"])
    return _pick_highlights(raw.splitlines(), n)


def bump(kind: str = "patch", breaking: bool = False, note: str = "",
         explicit: str = "") -> str:
    """升版本并写 CHANGELOG。`explicit` 用于直接指定版本号（发版脚本用），
    避免「bump 按 kind 算出 1.1.0，但你要 1.2.0」导致 CHANGELOG 标题对不上。"""
    cur = semver(version())
    if explicit:
        new = explicit.strip().lstrip("v")
        if not re.fullmatch(r"\d+\.\d+\.\d+", new):
            raise SystemExit("❌ 版本号格式应为 X.Y.Z，收到 %r" % explicit)
    else:
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

    # 本次区间（上一个 tag..HEAD）的提交才是这一版的亮点。
    # 不更新这个全局变量的话，publish_json() 会退回去取「最近 40 条」，
    # 把上一版的内容也列进来 —— 同事会以为那些是新东西。
    global _LAST_HIGHLIGHTS
    _LAST_HIGHLIGHTS = _pick_highlights([c["subject"] for c in commits])

    old = ""
    if os.path.exists(CHANGELOG_FILE):
        old = open(CHANGELOG_FILE, encoding="utf-8").read()
        if old.startswith("# "):
            _, _, rest = old.partition("\n")
            # 连紧跟的说明引用块一起剥掉，否则每 bump 一次就多重复一段
            lines = rest.lstrip("\n").splitlines()
            while lines and lines[0].lstrip().startswith(">"):
                lines.pop(0)
            old = "\n".join(lines).lstrip("\n")
    header = ("# 变更记录（CHANGELOG）\n\n"
              "> 由 `python scripts/release.py` 自动生成，别手改格式。\n"
              "> 唯一用途：让人在 30 秒内判断「我要不要更新」——"
              "所以只写「改了什么、影响谁」，实现细节看 git log。\n\n")
    open(CHANGELOG_FILE, "w", encoding="utf-8").write(
        header + "\n".join(head) + "\n" + body + "\n\n" + old)
    return new


def publish_json(breaking: bool = False) -> dict:
    """生成仓库根的 `version.json`（随代码入库）。

    放仓库根而不是 `dist/`：`dist/` 是打包产物、已在 .gitignore 里，
    清单必须能被 GitHub Contents API 直接读到，所以必须在版本控制的路径上。
    """
    me = local()
    ver = me["version"]
    doc = {
        "version": ver,
        "commit": me["commit"],
        "date": me["date"] or datetime.now().strftime("%Y-%m-%d"),
        "breaking": bool(breaking),
        # 破坏性变更时把门槛抬到当前版本：旧版一律拦下
        "min_supported": ver if breaking else "%d.%d.0" % semver(ver)[:2],
        # 优先用 bump 刚算出的「本次区间」亮点；单独跑 --publish-json 时回退到最近提交
        "highlights": _LAST_HIGHLIGHTS or _highlights(),
    }
    with open(DIST_FILE, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
        f.write("\n")
    return doc


def _prev_version(v: str) -> str:
    """造一个「比 v 低一版」的版本号，用来演示落后场景。"""
    a, b, c = semver(v)
    if c > 0:
        return "%d.%d.%d" % (a, b, c - 1)
    if b > 0:
        return "%d.%d.0" % (a, b - 1)
    return "%d.0.0" % max(a - 1, 0)


def demo() -> int:
    """把四种情况一次演完，用**真实远端数据**，不联网之外不做任何副作用。

    存在的理由：这套机制最容易被问的一句话是「我怎么没看到更新提醒」——
    因为**已是最新时它本来就该静默**。光解释没用，得让人看见。
    """
    print("演示：同事在四种情况下分别会看到什么（用真实远端数据，不改任何文件）\n")
    r = check(use_cache=True)
    remote = r.get("remote") or {}
    rv = str(remote.get("version") or "")
    cur = local()["version"]

    if not rv:
        print("❌ 取不到远端版本，没法演示。先查源：python scripts/version.py --sources")
        return EXIT_UNREACHABLE

    hl = remote.get("highlights") or []
    min_ok = str(remote.get("min_supported") or "")
    older = _prev_version(rv)

    print("远端 v%s　·　你的本地 v%s" % (rv, cur))
    print("缓存命中的源：%s" % (r.get("source") or "-"))
    if cur == rv:
        print("→ 你现在就在最新版上，所以**不会**看到提醒。下面演示落后一版时是什么样。")
    print()

    def show(title, cond, level, thin=False):
        print("── %s" % title)
        print("   触发条件：%s" % cond)
        line, must = _banner_text(level, rv, older, hl, min_ok, thin)
        for ln in (line or "（无输出）").splitlines():
            print("   " + ln)
        print("   是否拦住：%s" % ("会，ai_flow 退出码 12，零副作用" if must else "不会，只提示"))
        print()

    show("情况 1：本地落后（最常见）",
         "本地 v%s < 远端 v%s，且不是破坏性变更" % (older, rv), "stale")
    show("情况 2：破坏性变更",
         "远端 breaking=true，或本地 < min_supported（当前 v%s）" % (min_ok or "-"), "block")

    print("── 情况 3：已是最新（你现在就是这种）")
    print("   banner 返回 None，**什么都不打印**。")
    print("   这是故意的：每次启动都弹「有新版本」会让人习惯性忽略，")
    print("   真该拦人的时候也就失效了。想主动确认就手动跑 --check。")
    print()

    print("── 情况 4：离线 / 所有源都不通")
    print("   同样静默放行，不报错也不阻塞。离线是常态，不能因此拦住人干活。")
    print()

    print("主动查一次（忽略缓存）：python scripts/version.py --check --no-cache")
    return 0


# ---------------------------------------------------------------- CLI
def main() -> int:
    ap = argparse.ArgumentParser(description="版本号：查看 / 比对 / bump / 生成 version.json")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--show", action="store_true", help="打印本地版本")
    g.add_argument("--check", action="store_true", help="与远端比对")
    g.add_argument("--bump", choices=["major", "minor", "patch"], help="升版本并写 CHANGELOG")
    g.add_argument("--changelog", action="store_true", help="打印 git log 摘要（不写文件）")
    g.add_argument("--publish-json", action="store_true", help="生成仓库根 version.json（入库）")
    g.add_argument("--sources", action="store_true", help="逐个探测远端源，打印连通性与耗时")
    g.add_argument("--demo", action="store_true", help="演示四种情况下的提醒文案（回答「怎么没看到提醒」）")
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

    if a.sources:
        cfg = update_cfg()
        print("远端源（按优先级，命中第一个即停）：")
        rows = probe_sources()
        for i, r in enumerate(rows, 1):
            mark = "✅" if r["ok"] else "❌"
            tail = ("v%s · %s" % (r["version"], r["detail"])) if r["ok"] else r["detail"]
            print("  %d. %s %-52s %5dms  %s" % (i, mark, r["source"][:52], r["ms"], tail))
        ok = any(r["ok"] for r in rows)
        print("\n结论：%s" % ("至少一个源可用" if ok else "全部不可用 —— 离线环境属正常，检查会静默放行"))
        print("缓存：%s（%.0f 小时）" % (os.path.relpath(CACHE_FILE, ROOT), cfg["cache_hours"]))
        return 0 if ok else EXIT_UNREACHABLE

    if a.demo:
        return demo()

    if a.bump:
        new = bump(a.bump, breaking=a.breaking, note=a.note)
        print("✅ 版本 → v%s（CHANGELOG 已更新）" % new)
        print("   下一步：python scripts/release.py --version %s   （一键 commit/tag/push/校验）" % new)
        return 0

    if a.publish_json:
        doc = publish_json(breaking=a.breaking)
        print("✅ 已生成 %s" % os.path.relpath(DIST_FILE, ROOT))
        print(json.dumps(doc, ensure_ascii=False, indent=1))
        print("\n这个文件要随代码一起提交，GitHub Contents API 才读得到：")
        print("  git add VERSION CHANGELOG.md version.json && git commit -m \"chore(release): v%s\"" % doc["version"])
        print("  或直接用：python scripts/release.py")
        return 0

    # --check
    r = check(use_cache=not a.no_cache)
    me, rv = r["local"]["version"], r.get("remote_version")
    print("本地 v%s (%s, %s)" % (me, r["local"]["commit"], r["local"]["date"] or "-"))
    if r["level"] == "unknown":
        if not a.quiet:
            print("远端 取不到（试过 %d 个源）—— 离线或未发版，不判定" % len(r["sources"]))
            for s in r["sources"]:
                print("   · " + s)
            print("   排障：python scripts/version.py --sources")
        return EXIT_UNREACHABLE
    print("远端 v%s (%s) · %s" % (rv, (r["remote"] or {}).get("date", "-"),
                                 {"ok": "已是最新", "stale": "落后", "block": "必须更新"}[r["level"]]))
    if r.get("source"):
        print("来源 %s%s" % (r["source"], "（release 只有 tag，不判 breaking）" if r.get("thin") else ""))
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
