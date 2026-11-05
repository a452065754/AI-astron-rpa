# -*- coding: utf-8 -*-
"""把 docs/upstream/issues/*.md 逐份提交到 iflytek/astron-rpa。

用法：
    python docs/upstream/publish_issues.py --dry-run        # 只打印标题与字数
    python docs/upstream/publish_issues.py                  # 真的提交
    python docs/upstream/publish_issues.py --only 01,02     # 只提交指定编号

约定：每个 md 的第一行 `# xxx` 作为 issue 标题，其余作为正文。
提交结果写入 docs/upstream/_published.json（编号 -> url）。
"""
import argparse
import glob
import io
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

OWNER, REPO = "iflytek", "astron-rpa"
HERE = os.path.dirname(os.path.abspath(__file__))
ISSUE_DIR = os.path.join(HERE, "issues")
OUT_JSON = os.path.join(HERE, "_published.json")


def token():
    out = subprocess.run(["git", "credential", "fill"],
                         input="protocol=https\nhost=github.com\n\n",
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    raise SystemExit("no github token in git credential")


def api(method, path, payload=None, tok=None):
    url = "https://api.github.com" + path
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "token " + tok,
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "astron-rpa-issue-reporter",
    })
    try:
        return json.load(urllib.request.urlopen(req, timeout=90)), None
    except urllib.error.HTTPError as e:
        return None, (e.code, e.read().decode("utf-8", "replace")[:800])


def read_issue(path):
    raw = io.open(path, encoding="utf-8").read()
    lines = raw.split("\n")
    assert lines[0].startswith("# "), "%s 第一行不是 # 标题" % path
    title = lines[0][2:].strip()
    body = "\n".join(lines[1:]).lstrip("\n")
    return title, body


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default="")
    args = ap.parse_args()

    only = {x.strip() for x in args.only.split(",") if x.strip()}
    files = sorted(glob.glob(os.path.join(ISSUE_DIR, "*.md")))
    files = [f for f in files if not only or os.path.basename(f)[:2] in only]
    if not files:
        raise SystemExit("没有匹配的 md")

    published = {}
    if os.path.exists(OUT_JSON):
        published = json.load(open(OUT_JSON, encoding="utf-8"))

    tok = None if args.dry_run else token()
    for f in files:
        title, body = read_issue(f)
        key = os.path.basename(f)
        if key in published:
            print("[SKIP] %s 已提交过：%s" % (key, published[key]))
            continue
        if args.dry_run:
            print("[DRY]  %s\n       标题：%s\n       正文：%d 字符 / %d 行"
                  % (key, title, len(body), body.count("\n") + 1))
            continue

        d, err = api("POST", "/repos/%s/%s/issues" % (OWNER, REPO),
                     {"title": title, "body": body, "labels": ["bug"]}, tok)
        if err:
            print("[FAIL] %s -> %s\n%s" % (key, err[0], err[1]))
            continue
        published[key] = d["html_url"]
        print("[OK]   #%s %s\n       %s（labels: %s）"
              % (d["number"], title[:60], d["html_url"],
                 ",".join(l["name"] for l in d.get("labels", [])) or "无"))
        json.dump(published, open(OUT_JSON, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        time.sleep(1)  # 温和一点，避免触发二级限流

    if not args.dry_run:
        print("\n已记录到", OUT_JSON)


if __name__ == "__main__":
    main()
