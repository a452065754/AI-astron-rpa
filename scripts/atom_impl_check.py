# -*- coding: utf-8 -*-
"""原子字典 vs 本机实现 一致性检查（只读）。

原子字典（`/api/robot/atom-new/list`，357 个）是**服务端**下发的，
本机执行用的是设计器自带的 `python_core/site-packages/astronverse/`。
两者版本不一致时，会出现「字典里有、跑起来 AttributeError」的坑
（如 `DataTable.sort_table`，见坑 118）。

用法：
    python scripts/atom_impl_check.py                # 列全部缺失
    python scripts/atom_impl_check.py --domain DataTable
    python scripts/atom_impl_check.py --json
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(ROOT, "docs", "atom_dict_cache.json")

# 设计器自带的 python（能 import astronverse）
DEFAULT_PY = os.path.expandvars(
    r"%APPDATA%\astron-rpa\python_core\python.exe")

PROBE = r'''
import importlib, json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    items = json.load(f)
missing, ok = [], 0
for mod_name, cls_name, meth, key in items:
    try:
        m = importlib.import_module(mod_name)
    except Exception as e:
        missing.append([key, "%s 导入失败：%s" % (mod_name, e)])
        continue
    obj = getattr(m, cls_name, None)
    if obj is None:
        missing.append([key, "模块里没有类 %s" % cls_name])
        continue
    if not hasattr(obj, meth):
        missing.append([key, "类 %s 没有方法 %s" % (cls_name, meth)])
        continue
    ok += 1
print("__RESULT__" + json.dumps({"ok": ok, "missing": missing}, ensure_ascii=False))
'''


def parse_src(src):
    """`astronverse.excel.excel.Excel().open_excel` → (module, class, method)"""
    if "()" not in src:
        return None
    left, meth = src.split("()", 1)
    meth = meth.lstrip(".")
    mod, _, cls = left.rpartition(".")
    if not mod or not cls or not meth:
        return None
    return mod, cls, meth


def load_atoms():
    if not os.path.exists(CACHE):
        sys.exit("找不到原子字典缓存：%s（先跑一次 atom_catalog.py）" % CACHE)
    with open(CACHE, encoding="utf-8") as f:
        return json.load(f)


def domain_of(key):
    return key.split(".")[0]


def check(atoms, py):
    import tempfile
    items, unparsed = [], []
    for key, a in atoms.items():
        p = parse_src(a.get("src") or "")
        if not p:
            unparsed.append(key)
            continue
        items.append([p[0], p[1], p[2], key])
    fd, _items_file = tempfile.mkstemp(suffix=".json", text=True)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False)
    try:
        out = subprocess.run([py, "-c", PROBE, _items_file],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace")
    finally:
        try:
            os.remove(_items_file)
        except OSError:
            pass
    line = ""
    for ln in (out.stdout or "").splitlines():
        if ln.startswith("__RESULT__"):
            line = ln[len("__RESULT__"):]
    if not line:
        sys.exit("探测失败（设计器 python 不可用？）\n%s\n%s"
                 % ((out.stdout or "")[-800:], (out.stderr or "")[-800:]))
    res = json.loads(line)
    return res, unparsed


def main():
    ap = argparse.ArgumentParser(description="原子字典 vs 本机实现 一致性检查")
    ap.add_argument("--python", default=DEFAULT_PY, help="设计器 python_core 路径")
    ap.add_argument("--domain", help="只看某个域，如 DataTable")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-save", action="store_true",
                    help="不写 docs/atom_missing.json（默认会写，供 spec_lint 用）")
    args = ap.parse_args()

    if not os.path.exists(args.python):
        sys.exit("找不到设计器 python：%s（用 --python 指定）" % args.python)

    atoms = load_atoms()
    res, unparsed = check(atoms, args.python)
    missing = res["missing"]

    # 落盘给 spec_lint / atom_catalog 用：这些原子写了也跑不起来
    if not args.no_save:
        payload = {
            "checked_at": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total": len(atoms),
            "ok": res["ok"],
            "missing": {k: why for k, why in missing},
            "unparsed": unparsed,
        }
        with open(os.path.join(ROOT, "docs", "atom_missing.json"), "w",
                  encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)

    if args.domain:
        missing = [m for m in missing if domain_of(m[0]) == args.domain]

    if args.json:
        print(json.dumps({"total": len(atoms), "ok": res["ok"],
                          "missing": missing, "unparsed": unparsed},
                         ensure_ascii=False, indent=1))
        return

    print("原子实现一致性检查")
    print("=" * 66)
    print("字典 %d 个 · 本机可用 %d · 缺失 %d"
          % (len(atoms), res["ok"], len(res["missing"])))
    if unparsed:
        print("（%d 个 src 格式无法解析，已跳过：%s%s）"
              % (len(unparsed), ", ".join(unparsed[:6]),
                 " …" if len(unparsed) > 6 else ""))
    if not missing:
        print("\n✅ 字典里的原子本机都能调")
        return

    by_dom = {}
    for key, why in missing:
        by_dom.setdefault(domain_of(key), []).append((key, why))
    print("\n本机缺失的原子（写了也跑不起来）：")
    for d in sorted(by_dom, key=lambda x: -len(by_dom[x])):
        print("  %-18s %d 个" % (d, len(by_dom[d])))
        for key, why in by_dom[d]:
            print("    · %-38s %s" % (key, why))


if __name__ == "__main__":
    main()
