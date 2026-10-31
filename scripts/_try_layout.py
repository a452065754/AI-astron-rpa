# -*- coding: utf-8 -*-
"""本地直调 mod_pdf_layout 的试验脚本（不进流程，只用来验证模块本身）。"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import mod_pdf_layout as ml


def run(src, out_dir, out_name):
    args = {
        "src_pdf": src,
        "out_dir": out_dir,
        "out_name": out_name,
        "ps1": os.path.join(HERE, "win_ocr.ps1"),
        "lang": "zh-Hans-CN",
    }
    t0 = time.time()
    res = ml.main(args)
    res["seconds"] = round(time.time() - t0, 1)
    return res


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "demo", "20260914报废_已转正.pdf")
    out_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "demo")
    out_name = sys.argv[3] if len(sys.argv) > 3 else "_版面还原试验"
    r = run(src, out_dir, out_name)
    print("RESULT " + json.dumps(r, ensure_ascii=False))
