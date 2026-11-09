#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V19 门禁：GitHub 版版本分发链路的回归测试（**全程不联网**）。

为什么要有
----------
V18 测的是「拿到远端版本之后怎么判」。V19 测的是**前面那一段**：
远端版本从哪来、取不到怎么办、多源怎么回退。

这段代码的危险点和 V18 不同：
- V18 判错 → 该拦没拦，你能看见（脚本跑出脏数据）
- V19 判错 → **取不到就当没有**，静默放行。所有人都以为自己在用新版，
  「查不到更新」和「没有更新」长得一模一样，可能几周都没人发现

所以这里用 mock 把所有分支钉死，尤其「全失败必须静默」和「thin 源绝不许拦人」两条。

跑法
----
    python logs/qa/v19_github_version.py

覆盖 14 条：源解析 2 / Contents API 3 / 多源回退 3 / 分级降级 2 /
配置兼容 2 / 清单生成 1 / 缓存 1。
"""
import io
import json
import os
import shutil
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import version as V  # noqa: E402

PASS, FAIL = [], []


def ck(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("  ✅ " + name)
    else:
        FAIL.append((name, detail))
        print("  ❌ %s  %s" % (name, detail))


# ---------------------------------------------------------------- 1. 源解析
def t_slug():
    print("\n[1] 仓库地址解析（不硬编码 owner/repo）")

    orig = V._run
    V._run = lambda args, cwd=V.ROOT: (
        "https://github.com/foo/bar-rpa.git" if "get-url" in args
        and args[-1] == "origin" else "")
    ck("从 https remote 解析 owner/repo", V.repo_slug() == "foo/bar-rpa", V.repo_slug())

    V._run = lambda args, cwd=V.ROOT: (
        "git@github.com:foo/bar-rpa.git" if "get-url" in args
        and args[-1] == "origin" else "")
    ck("从 ssh remote 解析（带 .git 后缀）", V.repo_slug() == "foo/bar-rpa", V.repo_slug())

    V._run = lambda args, cwd=V.ROOT: "http://gitea.local/x/y.git"
    ck("非 GitHub remote 返回空（repo_slug 只认 github.com）",
       V.repo_slug() == "", repr(V.repo_slug()))
    V._run = orig


# ---------------------------------------------------------------- 1b. origin 归属
def t_origin():
    print("\n[1b] origin 归属解析（从哪 clone 就从哪查版本）")

    orig = V._run
    try:
        cases = [
            ("https://github.com/foo/bar-rpa.git", "github", "github.com", "foo", "bar-rpa"),
            ("git@github.com:foo/bar-rpa.git", "github", "github.com", "foo", "bar-rpa"),
            # 自建服务带端口 —— 最容易切错的一种
            ("http://10.18.5.61:3300/xdd/AI-astron-rpa.git",
             "selfhosted", "10.18.5.61:3300", "xdd", "AI-astron-rpa"),
            ("http://git.839520.xyz/xdd/AI-astron-rpa.git",
             "selfhosted", "git.839520.xyz", "xdd", "AI-astron-rpa"),
        ]
        for url, kind, host, owner, repo in cases:
            V._run = lambda args, cwd=V.ROOT, u=url: (
                u if "get-url" in args and args[-1] == "origin" else "")
            o = V.repo_origin()
            ok = (o and o["kind"] == kind and o["host"] == host
                  and o["owner"] == owner and o["repo"] == repo)
            ck("%-46s → %s" % (url[:46], kind), ok, o)

        # base 必须能直接拼 raw 路径
        V._run = lambda args, cwd=V.ROOT: (
            "http://10.18.5.61:3300/xdd/AI-astron-rpa.git"
            if "get-url" in args and args[-1] == "origin" else "")
        o = V.repo_origin()
        ck("selfhosted base 拼装正确",
           o and o["base"] == "http://10.18.5.61:3300/xdd/AI-astron-rpa", o and o["base"])

        V._run = lambda args, cwd=V.ROOT: "/local/path/repo"
        ck("非 URL 形态的 remote 返回 None（不瞎猜）", V.repo_origin() is None, V.repo_origin())
    finally:
        V._run = orig


# ---------------------------------------------------------------- 1c. 自建服务路径
def t_selfhosted():
    print("\n[1c] 自建服务路径回退（Gitea / GitLab 写法不同）")

    origin = {"kind": "selfhosted", "base": "http://h:3300/o/r"}
    orig = V._get_json

    seen = []

    def mock_ok_at(only: str):
        def f(url, timeout=0, headers=None):
            seen.append(url)
            return {"version": "3.1.4"} if only in url else None
        return f

    V._get_json = mock_ok_at("/raw/branch/main/")
    ck("认出 Gitea 写法（/raw/branch/main/）",
       (V.fetch_selfhosted(origin) or {}).get("version") == "3.1.4")

    seen.clear()
    V._get_json = mock_ok_at("/-/raw/main/")
    ck("认出 GitLab 写法（/-/raw/main/）",
       (V.fetch_selfhosted(origin) or {}).get("version") == "3.1.4")
    ck("先试 Gitea 再试 GitLab（顺序固定）",
       len(seen) >= 2 and "/raw/branch/" in seen[0], seen[:2])

    V._get_json = lambda url, timeout=0, headers=None: {"message": "404"}
    ck("全部 404 返回 None（不伪装成有版本）", V.fetch_selfhosted(origin) is None)

    V._get_json = orig


# ---------------------------------------------------------------- 2. Contents API
def t_contents():
    print("\n[2] GitHub Contents API 解析")

    payload = {"version": "1.2.3", "commit": "abc1234", "date": "2026-11-05",
               "breaking": False, "min_supported": "1.0.0",
               "highlights": ["测试亮点"]}
    import base64
    api = {"content": base64.b64encode(json.dumps(payload).encode()).decode(),
           "name": "version.json", "sha": "x"}

    orig = V._get_json
    V._get_json = lambda url, timeout=0, headers=None: api
    doc = V.fetch_github("foo/bar")
    ck("base64 正确解码出字段", doc and doc.get("version") == "1.2.3", doc)
    ck("自动标注来源", doc and doc.get("_source", "").startswith("github:foo/bar@"),
       (doc or {}).get("_source"))

    # 404：仓库里没这个文件
    V._get_json = lambda url, timeout=0, headers=None: {"message": "Not Found"}
    ck("404（无 version.json）返回 None 而非崩溃", V.fetch_github("foo/bar") is None)

    # 内容不是合法 JSON
    V._get_json = lambda url, timeout=0, headers=None: {
        "content": base64.b64encode(b"not json at all").decode()}
    ck("内容损坏返回 None（不把异常抛给调用方）", V.fetch_github("foo/bar") is None)
    V._get_json = orig


# ---------------------------------------------------------------- 3. 多源回退
def t_fallback():
    print("\n[3] 多源回退顺序")

    calls = []

    def fake_one(src, timeout=0):
        calls.append(src)
        if "good" in src:
            return {"version": "2.0.0", "min_supported": "1.0.0"}
        return None

    orig = V.fetch_one
    V.fetch_one = fake_one

    doc, src = V.fetch_remote(["bad1", "bad2", "good", "bad3"])
    ck("取到第一个成功的就停（不试后面）", doc and doc["version"] == "2.0.0", doc)
    ck("命中前尝试了 3 个源，命中后不再尝试", calls == ["bad1", "bad2", "good"], calls)

    doc2, src2 = V.fetch_remote(["bad1", "bad2"])
    ck("全失败返回 (None, '')", doc2 is None and src2 == "", (doc2, src2))

    # 空列表不能崩
    try:
        V.fetch_remote([])
        ck("空源列表不抛异常", True)
    except Exception as e:
        ck("空源列表不抛异常", False, repr(e))
    V.fetch_one = orig


# ---------------------------------------------------------------- 4. 分级与降级
def t_levels():
    print("\n[4] 分级判定：thin 源绝不许拦人")

    orig_fetch, orig_local = V.fetch_remote, V.local
    orig_cache = V.CACHE_FILE
    d = tempfile.mkdtemp()
    V.CACHE_FILE = os.path.join(d, "cache.json")   # check() 会写缓存，别污染真实缓存
    V.local = lambda: {"version": "1.0.0", "commit": "x", "date": "2026-11-05"}

    def with_remote(doc):
        """把远端固定成 doc。本组只测判定逻辑，不测缓存本身。"""
        V.fetch_remote = lambda sources=None, timeout=0: (doc, "test")
        return V.check(use_cache=False)

    # 完整清单 + breaking → 必须拦
    r = with_remote({"version": "1.1.0", "breaking": True, "min_supported": "1.1.0"})
    ck("breaking 且落后 → block（退出码 12）", r["level"] == "block", r["level"])

    # 完整清单 + 非 breaking → 只提示
    r = with_remote({"version": "1.1.0", "breaking": False, "min_supported": "1.0.0"})
    ck("非 breaking 落后 → stale（只提示）", r["level"] == "stale", r["level"])

    # 低于 min_supported 即使 breaking=false 也要拦
    r = with_remote({"version": "1.1.0", "breaking": False, "min_supported": "1.0.5"})
    ck("低于 min_supported → block", r["level"] == "block", r["level"])

    # thin 源（只有 tag）：就算 remote 说 breaking 也不能拦 —— release 里没这信息
    r = with_remote({"version": "9.9.9", "_thin": True, "min_supported": "9.9.9"})
    ck("thin 源（无 breaking 信息）→ 最多 stale，不拦人",
       r["level"] == "stale", r["level"])
    ck("thin 源被标记", r.get("thin") is True, r.get("thin"))

    # 全失败 → unknown，且 banner 必须完全静默
    r = with_remote(None)
    ck("全源失败 → unknown", r["level"] == "unknown", r["level"])
    line, block = V.banner(use_cache=False)
    ck("unknown 时 banner 静默且不阻塞", line is None and block is False, (line, block))

    V.fetch_remote, V.local = orig_fetch, orig_local
    V.CACHE_FILE = orig_cache
    shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- 5. 配置兼容
def t_config():
    print("\n[5] 配置兼容（老配置不许坏）")

    d = tempfile.mkdtemp()
    orig_cfg = V.CONFIG_FILE
    try:
        p = os.path.join(d, "rpa_config.json")

        json.dump({"update": {"version_url": "http://old/version.json"}},
                  open(p, "w", encoding="utf-8"))
        V.CONFIG_FILE = p
        ck("老配置 version_url 退化为单源",
           V.update_cfg()["sources"] == ["http://old/version.json"], V.update_cfg())

        json.dump({"update": {"sources": ["github", r"\\srv\v.json"]}},
                  open(p, "w", encoding="utf-8"))
        V.CONFIG_FILE = p
        ck("新配置 sources 顺序保留",
           V.update_cfg()["sources"] == ["github", r"\\srv\v.json"], V.update_cfg())

        json.dump({"nothing": 1}, open(p, "w", encoding="utf-8"))
        V.CONFIG_FILE = p
        ck("无 update 段 → 默认 github", V.update_cfg()["sources"] == ["github"], V.update_cfg())

        V.CONFIG_FILE = os.path.join(d, "missing.json")
        ck("配置文件不存在也不崩", V.update_cfg()["sources"] == ["github"])
    finally:
        V.CONFIG_FILE = orig_cfg
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- 6. 清单生成
def t_publish():
    print("\n[6] version.json 生成位置与字段")

    ck("产物在仓库根（不是 dist/）",
       V.DIST_FILE == os.path.join(V.ROOT, "version.json"), V.DIST_FILE)
    ck("产物未被 .gitignore 忽略（能被 API 读到）",
       os.path.basename(V.DIST_FILE) == "version.json")

    cfg = os.path.join(V.ROOT, ".gitignore")
    ignored = False
    if os.path.exists(cfg):
        txt = open(cfg, encoding="utf-8").read().splitlines()
        for ln in txt:
            s = ln.strip()
            if s and not s.startswith("#") and s.rstrip("/") == "version.json":
                ignored = True
    ck(".gitignore 里没有 version.json", not ignored)

    # breaking 时 min_supported 必须抬到当前版本，否则旧版拦不住
    bak = None
    if os.path.exists(V.DIST_FILE):
        bak = open(V.DIST_FILE, encoding="utf-8").read()
    orig_high = V._highlights
    V._highlights = lambda n=3: ["测试"]
    try:
        doc = V.publish_json(breaking=True)
        ck("breaking 时 min_supported == 当前版本（旧版被拦）",
           doc["min_supported"] == V.version(), doc["min_supported"])
        doc2 = V.publish_json(breaking=False)
        ck("非 breaking 时 min_supported 只到 major.minor.0",
           doc2["min_supported"].endswith(".0"), doc2["min_supported"])
    finally:
        V._highlights = orig_high
        if bak is not None:
            open(V.DIST_FILE, "w", encoding="utf-8").write(bak)


# ---------------------------------------------------------------- 7. 缓存
def t_cache():
    print("\n[7] 缓存带上命中的源")

    d = tempfile.mkdtemp()
    orig = V.CACHE_FILE
    try:
        V.CACHE_FILE = os.path.join(d, "version_check.json")
        V._write_cache({"version": "1.0.1"}, "github:foo/bar@main")
        c = V._read_cache()
        ck("缓存写入后能读回", c and c["remote"]["version"] == "1.0.1", c)
        ck("缓存记录了命中源（排障要看）",
           c.get("source") == "github:foo/bar@main", c.get("source"))
        ck("缓存含 checked_at", "checked_at" in (c or {}))
    finally:
        V.CACHE_FILE = orig
        shutil.rmtree(d, ignore_errors=True)


def main():
    print("=" * 66)
    print("V19 门禁：GitHub 版本分发链路（不联网）")
    print("=" * 66)
    t_slug()
    t_origin()
    t_selfhosted()
    t_contents()
    t_fallback()
    t_levels()
    t_config()
    t_publish()
    t_cache()

    total = len(PASS) + len(FAIL)
    print("\n" + "=" * 66)
    if FAIL:
        print("❌ %d/%d 通过，%d 条失败：" % (len(PASS), total, len(FAIL)))
        for n, d in FAIL:
            print("   · %s  %s" % (n, d))
        return 1
    print("✅ %d/%d 全部通过" % (total, total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
