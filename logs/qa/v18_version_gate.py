#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V18 门禁：版本比对与「破坏性变更必须拦」的回归测试。

为什么要有
----------
`scripts/version.py` 决定了两件事：
① 同事机器要不要提示更新；② **breaking 时必须拦住 ai_flow**。
这两件事一旦判错，后果不对称：
- 该提示没提示 → 同事继续用旧版，你复现不了他的 bug（烦，但能救）
- 该拦没拦 → 旧脚本按新契约跑，产出脏数据（可能查不出来）

所以把判定逻辑做成可回归的用例，而不是每次手点。

跑法
----
    python logs/qa/v18_version_gate.py

覆盖 12 条：semver 比较 3 条 / 分级判定 4 条 / banner 2 条 / 离线静默 1 条 / bump 进位 2 条。
用例全部用假的 fetch_remote，不联网。
"""
import io
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
    (PASS if cond else FAIL).append(name)
    print("%s %-46s %s" % ("✅" if cond else "❌", name, detail))


def fake(remote):
    """把 fetch_remote 换成固定返回值；None 模拟不可达。"""
    V.fetch_remote = lambda url, timeout=1.5: remote


def run_check(local_ver, remote):
    """在指定本地版本上跑 check（临时改写 VERSION，跑完还原）。"""
    orig = open(V.VERSION_FILE, encoding="utf-8").read()
    try:
        open(V.VERSION_FILE, "w", encoding="utf-8").write(local_ver + "\n")
        fake(remote)
        V.CACHE_FILE = os.path.join(tempfile.gettempdir(), "_v18_no_cache.json")
        if os.path.exists(V.CACHE_FILE):
            os.remove(V.CACHE_FILE)
        return V.check(use_cache=False)
    finally:
        open(V.VERSION_FILE, "w", encoding="utf-8").write(orig)


def main():
    print("=" * 72)
    print("V18 版本门禁回归")
    print("=" * 72)

    # ---------- 1. semver 比较 ----------
    ck("1.0.10 > 1.0.9（按数字比，不按字符串）", V.cmp_ver("1.0.10", "1.0.9") == 1)
    ck("1.1.0 > 1.0.99（次版本优先）", V.cmp_ver("1.1.0", "1.0.99") == 1)
    ck("v1.0.0 == 1.0.0（前缀 v 不影响）", V.cmp_ver("v1.0.0", "1.0.0") == 0)

    # ---------- 2. 分级判定 ----------
    r = run_check("1.0.0", {"version": "1.0.0", "breaking": False, "min_supported": "1.0.0"})
    ck("同版本 → ok（不打扰）", r["level"] == "ok", r["level"])

    r = run_check("1.0.0", {"version": "1.0.1", "breaking": False, "min_supported": "1.0.0"})
    ck("落后补丁版 → stale（只提示）", r["level"] == "stale", r["level"])

    r = run_check("1.0.0", {"version": "1.1.0", "breaking": True, "min_supported": "1.0.0"})
    ck("标记 breaking → block（必须拦）", r["level"] == "block", r["level"])

    r = run_check("1.0.0", {"version": "1.2.0", "breaking": False, "min_supported": "1.1.0"})
    ck("低于 min_supported → block（不依赖 breaking 标志）", r["level"] == "block", r["level"])

    # ---------- 3. banner ----------
    fake({"version": "1.0.1", "breaking": False, "min_supported": "1.0.0",
          "highlights": ["A20 参数门禁", "发布默认草稿"]})
    V.CACHE_FILE = os.path.join(tempfile.gettempdir(), "_v18_no_cache2.json")
    if os.path.exists(V.CACHE_FILE):
        os.remove(V.CACHE_FILE)
    text, blocked = V.banner(use_cache=False)
    ck("banner：落后时给提示且不阻塞", bool(text) and "⬆️" in text and not blocked, (text or "").splitlines()[0] if text else "None")

    fake({"version": "2.0.0", "breaking": True, "min_supported": "1.5.0",
          "highlights": ["spec 格式变更"]})
    V.CACHE_FILE = os.path.join(tempfile.gettempdir(), "_v18_no_cache3.json")
    if os.path.exists(V.CACHE_FILE):
        os.remove(V.CACHE_FILE)
    text, blocked = V.banner(use_cache=False)
    ck("banner：breaking 时给拦截且有「不能继续」", bool(text) and blocked and "不能继续" in text,
       "blocked=%s" % blocked)

    # ---------- 4. 离线不吵人 ----------
    fake(None)
    V.CACHE_FILE = os.path.join(tempfile.gettempdir(), "_v18_no_cache4.json")
    if os.path.exists(V.CACHE_FILE):
        os.remove(V.CACHE_FILE)
    r = V.check(use_cache=False)
    text, blocked = V.banner(use_cache=False)
    ck("远端不可达 → unknown，且不打印、不阻塞", r["level"] == "unknown" and text is None and not blocked)

    # ---------- 5. bump 进位（隔离目录，不碰真 VERSION） ----------
    tmp = tempfile.mkdtemp(prefix="v18_")
    try:
        shutil.copytree(os.path.join(ROOT, "scripts"), os.path.join(tmp, "scripts"))
        open(os.path.join(tmp, "VERSION"), "w", encoding="utf-8").write("1.2.3\n")
        code = (
            "import sys;sys.path.insert(0,%r);import version as V;"
            "print(V.bump('patch'))" % os.path.join(tmp, "scripts"))
        import subprocess
        a = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        b = subprocess.run([sys.executable, "-c",
                            "import sys;sys.path.insert(0,%r);import version as V;print(V.bump('major'))"
                            % os.path.join(tmp, "scripts")],
                           capture_output=True, text=True, timeout=60)
        ck("bump：1.2.3 patch → 1.2.4", a.stdout.strip() == "1.2.4", a.stdout.strip() or a.stderr[-80:])
        ck("bump：1.2.4 major → 2.0.0（低位归零）", b.stdout.strip() == "2.0.0", b.stdout.strip() or b.stderr[-80:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("-" * 72)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：" + "、".join(FAIL))
        return 1
    print("结论：V18 全绿")
    return 0


if __name__ == "__main__":
    sys.exit(main())
