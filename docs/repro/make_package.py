# -*- coding: utf-8 -*-
"""把复现包打成 zip，方便直接发给平台方。

    python make_package.py
    python make_package.py --out D:/临时

产物：dist/星辰RPA_平台缺陷复现包_v1.1.11_<日期>.zip
包内结构保持 docs/repro/ 下的相对路径，解压即可 `run_all.py` 直接跑。
"""
import argparse
import datetime as dt
import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKIP_DIRS = {"_tmp", "__pycache__", "dist", ".git"}
SKIP_EXT = {".pyc", ".pyo"}

# 明确列出要打的文件（避免把调试产物带出去）
INCLUDE = [
    "README.md",
    "patches.md",
    "run_all.py",
    "_harness.py",
    "_dict_evidence.json",
    "make_package.py",
]


def collect():
    files = []
    for name in INCLUDE:
        p = os.path.join(HERE, name)
        if os.path.exists(p):
            files.append(name)
    for name in sorted(os.listdir(HERE)):
        if name.startswith("A") and name.endswith(".py"):
            files.append(name)
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "dist"))
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    version = "v1.1.11"
    try:
        ver_file = os.path.join(HERE, "..", "..", "VERSION")
        if os.path.exists(ver_file):
            with open(ver_file, encoding="utf-8") as f:
                version = f.read().strip() or version
    except Exception:  # noqa: BLE001
        pass

    stamp = dt.datetime.now().strftime("%Y%m%d")
    zip_path = os.path.join(args.out, f"星辰RPA_平台缺陷复现包_{version}_{stamp}.zip")

    files = collect()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for name in files:
            z.write(os.path.join(HERE, name), arcname=os.path.join("repro", name))

    print(f"已生成：{zip_path}")
    print(f"包含 {len(files)} 个文件：")
    for name in files:
        print(f"  repro/{name}")
    print(f"\n大小：{os.path.getsize(zip_path) / 1024:.1f} KB")
    print("发给对方后，解压 → 用客户端自带 python 跑 run_all.py 即可。")


if __name__ == "__main__":
    main()
