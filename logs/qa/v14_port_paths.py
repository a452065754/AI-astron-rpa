#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V14 门禁：路径去机器化（`${PROJECT_ROOT}` 占位符）。

## 为什么有这个门禁

2026-11-03 之前，`flows/*.json` 里 **72 条流程 / 120 处**参数默认值写的是
`F:\\AI-Workbench\\10-Projects\\星辰开源\\demo\\...` —— 在本机跑得好好的，
别人 clone 下来一跑就是 `[Errno 2]`。

治理办法不是「改成相对路径」（引擎执行前 `os.chdir(project_dir)`，
project_dir 是 `%APPDATA%\\astron\\<project_id>` 这个**编译缓存目录**，
相对路径会解析到缓存里），而是**规格里写 `${PROJECT_ROOT}`、发布时替换成仓库根**。

这个门禁守三件事，任何一条破了都会让「换机器可用」重新失效：

  A. `flows/` 里没有写死盘符路径的参数默认值（系统目录如 `C:\\Windows\\...` 豁免）
  B. `flow_generator.load_spec()` 真把占位符换成了本机绝对路径，且路径存在性可判
  C. 换一个「仓库根」时，解析结果跟着变（模拟 clone 到别的目录）—— 这条是核心，
     只测 A/B 的话，把占位符替换成写死的路径也能过

跑法：`python logs/qa/v14_port_paths.py`   退出码 0 = 通过
"""
import copy
import glob
import json
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import flow_generator as fg   # noqa: E402
import spec_lint as sl        # noqa: E402

DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
SYS = re.compile(r"^[A-Za-z]:[\\/](?:Windows|Program Files(?: \(x86\))?|ProgramData)[\\/]",
                 re.IGNORECASE)

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("  %s %-52s %s" % ("✅" if ok else "❌", name, detail))


def main():
    print("=" * 78)
    print("V14 路径去机器化门禁")
    print("=" * 78)

    # ---------- A. flows 里不许有写死的盘符路径 ----------
    print("\n[A] flows/*.json 的参数默认值")
    bad, total = [], 0
    for p in sorted(glob.glob(os.path.join(ROOT, "flows", "*.json"))):
        with open(p, encoding="utf-8") as f:
            spec = json.load(f)
        for prm in spec.get("params") or []:
            v = (prm or {}).get("default")
            if not isinstance(v, str) or not DRIVE.match(v):
                continue
            total += 1
            if not SYS.match(v):
                bad.append("%s/%s=%s" % (os.path.basename(p), prm.get("name"), v))
    check("A1 无写死盘符路径", not bad,
          "盘符路径 %d 处（其中非系统目录 %d 处）" % (total, len(bad)))
    for x in bad[:5]:
        print("       ", x)

    tok = 0
    for p in glob.glob(os.path.join(ROOT, "flows", "*.json")):
        with open(p, encoding="utf-8") as f:
            spec = json.load(f)
        for prm in spec.get("params") or []:
            if isinstance((prm or {}).get("default"), str) and \
                    fg.PROJECT_ROOT_TOKEN in prm["default"]:
                tok += 1
    check("A2 占位符已铺开", tok >= 100, "使用 ${PROJECT_ROOT} 的参数 %d 处" % tok)

    # ---------- B. load_spec 真解析 ----------
    print("\n[B] load_spec 解析")
    sample = os.path.join(ROOT, "flows", "Excel订单表导出小流程.json")
    spec = fg.load_spec(sample)
    left = [p["name"] for p in spec["params"]
            if isinstance(p["default"], str) and fg.PROJECT_ROOT_TOKEN in p["default"]]
    check("B1 占位符全部被替换", not left, "残留 %s" % (left or "无"))
    abs_ok = all(os.path.isabs(p["default"]) for p in spec["params"]
                 if isinstance(p["default"], str) and re.search(r"[\\/]", p["default"]))
    check("B2 解析结果是绝对路径", abs_ok)
    src = [p["default"] for p in spec["params"] if p["name"] == "src_file"][0]
    check("B3 解析结果指向本仓库", src.startswith(fg.HERE), src)
    check("B4 指向的文件真实存在", os.path.exists(src), src)

    # ---------- C. 换仓库根，结果跟着变（模拟 clone 到别处） ----------
    print("\n[C] 换仓库根（模拟 clone 到别的目录）")
    with tempfile.TemporaryDirectory() as tmp:
        fake = os.path.join(tmp, "another-machine", "AI-astron-rpa")
        os.makedirs(fake)
        # 只搬 scripts + 一个流程，不复制整个 demo（这里只验路径推导，不验文件存在）
        import shutil
        shutil.copytree(os.path.join(ROOT, "scripts"), os.path.join(fake, "scripts"))
        os.makedirs(os.path.join(fake, "flows"))
        shutil.copy2(sample, os.path.join(fake, "flows"))
        # 子进程里跑，避免污染当前进程的 HERE
        code = (
            "import sys,os;sys.path.insert(0,r'%s');"
            "import flow_generator as fg;"
            "s=fg.load_spec(os.path.join(r'%s','flows','Excel订单表导出小流程.json'));"
            "print(fg.HERE);print(s['params'][0]['default'])" % (os.path.join(fake, "scripts"), fake)
        )
        import subprocess
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             encoding="utf-8", errors="replace")
        lines = [x for x in out.stdout.strip().splitlines() if x]
        new_root = lines[0] if lines else ""
        new_src = lines[1] if len(lines) > 1 else ""
        check("C1 新根生效", os.path.normcase(new_root) == os.path.normcase(fake),
              "解析出的根 = %s" % new_root)
        check("C2 路径跟着换到新根", new_src.startswith(new_root) and new_root,
              new_src)

    # ---------- D. 规则 1.8 的判定口径（含反例） ----------
    print("\n[D] spec_lint 规则 1.8 判定口径")
    atoms = fg.load_atom_dict(offline=True)
    base = json.load(open(sample, encoding="utf-8"))

    def fires(path_value):
        s = copy.deepcopy(base)
        s["params"][0]["default"] = path_value
        errs, _ = sl.lint(s, atoms)
        return bool([e for e in errs if "写死了本机路径" in (e.get("msg") or "")])

    cases = [
        ("D1 本机仓库路径 → 报错", r"F:\AI-Workbench\10-Projects\星辰开源\demo\订单.xlsx", True),
        ("D2 占位符 → 放行", "${PROJECT_ROOT}/demo/订单.xlsx", False),
        ("D3 系统路径 → 豁免", r"C:\Windows\System32\mstsc.exe", False),
        ("D4 仓库外盘符 → 报错", r"F:\demo\订单.xlsx", True),
        ("D5 纯文件名 → 放行", "导出结果", False),
        ("D6 UNC 共享盘 → 放行", r"\\nas\share\订单.xlsx", False),
    ]
    for label, val, want in cases:
        check(label, fires(val) == want, val[:44])

    s = copy.deepcopy(base)
    s["params"][0]["default"] = r"D:\ERP导出\订单.xlsx"
    s["allow_abs_path"] = True
    errs, _ = sl.lint(s, atoms)
    check("D7 allow_abs_path 逃生口",
          not [e for e in errs if "写死了本机路径" in (e.get("msg") or "")])

    # ---------- 汇总 ----------
    ok = sum(1 for _, o, _ in results if o)
    print("\n" + "=" * 78)
    print("结论：%d/%d 通过" % (ok, len(results)))
    if ok != len(results):
        print("失败项：")
        for n, o, d in results:
            if not o:
                print("  ❌ %s %s" % (n, d))
    print("=" * 78)
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
