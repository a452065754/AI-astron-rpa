# -*- coding: utf-8 -*-
"""一键跑完复现包里的全部用例，输出汇总表。

必须用设计器自带的 python_core 运行（里面才有 astronverse）：
    "%APPDATA%/astron-rpa/python_core/python.exe" docs/repro/run_all.py
    ... run_all.py --only A1,A5        # 只跑指定几条
    ... run_all.py --ftp host:port --user u --password p   # 让 A4 顺便真跑 FTP

汇总表里每行的含义：
    REPRODUCED      缺陷成立（平台需要修）
    NOT_REPRODUCED  跑不出问题（可能已修复 / 版本不同）
    BLOCKED         本机缺前置条件（没装 Word/Excel、没开 Chrome、没有 FTP）

退出码：0 = 全部 REPRODUCED 或 NOT_REPRODUCED（结论明确）；
        1 = 有条目 BLOCKED（结论不完整，需要补齐环境）。
"""
import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

CASES = [
    ("A1", "A1_fill_string_to_length.py", "右补齐必崩"),
    ("A2", "A2_convert_img_to_pdf.py", "图片合成 PDF 必失败"),
    ("A3", "A3_overwrite_existing.py", "同流程重复生成同名文档报「文件损坏」"),
    ("A4", "A4_ftp_rename_folder.py", "文件夹改名传错变量（501）"),
    ("A5", "A5_multi_output_mismatch.py", "多输出声明 ≠ 实现"),
    ("A6", "A6_insert_pic_linked.py", "插图是外链不是嵌入"),
    ("A7", "A7_dead_keys.py", "字典有实现无的死 key"),
    ("A8", "A8_param_to_eval_unpack.py", "参数求值返回 3 个值"),
    ("A9", "A9_iebrowser_missing.py", "IE 模块整块缺失"),
    ("A10", "A10_bridge_idle_disconnect.py", "扩展空闲断连不唤醒"),
    ("A11", "A11_insert_sep_no_break.py", "insert_sep 不分段"),
]

VERDICT_RE = re.compile(r"^结论：(REPRODUCED|NOT_REPRODUCED|BLOCKED)$", re.M)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="逗号分隔的编号，如 A1,A5")
    ap.add_argument("--ftp", default="")
    ap.add_argument("--user", default="")
    ap.add_argument("--password", default="")
    ap.add_argument("--keep-output", action="store_true", help="顺便打印每个脚本的完整输出")
    args = ap.parse_args()

    want = {x.strip().upper() for x in args.only.split(",") if x.strip()}
    cases = [c for c in CASES if not want or c[0] in want]

    results = []
    for aid, script, title in cases:
        path = os.path.join(HERE, script)
        if not os.path.exists(path):
            results.append((aid, title, "MISSING", "脚本不存在"))
            continue
        cmd = [PY, path]
        if aid == "A4" and args.ftp:
            cmd += ["--ftp", args.ftp, "--user", args.user, "--password", args.password]

        print(f"\n>>> 运行 {aid} {title} ...", flush=True)
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
        out = (proc.stdout or "") + (proc.stderr or "")
        if args.keep_output:
            print(out)
        m = VERDICT_RE.search(out)
        verdict = m.group(1) if m else f"UNKNOWN(exit={proc.returncode})"
        # 从「实际行为」段落里摘一句给汇总表用
        brief = ""
        seg = re.search(r"实际行为：\n(.*?)\n结论：", out, re.S)
        if seg:
            brief = " ".join(seg.group(1).split())[:110]
        results.append((aid, title, verdict, brief))

    print("\n")
    print("=" * 100)
    print(f"{'#':4s}{'结论':16s}{'用例':40s}关键现象")
    print("-" * 100)
    for aid, title, verdict, brief in results:
        print(f"{aid:4s}{verdict:16s}{title:40s}{brief}")
    print("=" * 100)

    counts = {}
    for _, _, v, _ in results:
        counts[v] = counts.get(v, 0) + 1
    print("合计：" + "、".join(f"{k} {v} 条" for k, v in sorted(counts.items())))
    print("\n口径：REPRODUCED = 缺陷成立（请修）；NOT_REPRODUCED = 跑不出问题；BLOCKED = 本机缺前置条件。")

    return 1 if any(v == "BLOCKED" or v.startswith("UNKNOWN") or v == "MISSING" for _, _, v, _ in results) else 0


if __name__ == "__main__":
    sys.exit(main())
