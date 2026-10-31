#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""原子目录：给 AI 看的「有哪些原子、每个要什么参数」。

## 为什么需要

AI 要自己拆解流程，第一个问题是「这件事该用哪个原子」。`c_atom_meta_new` 有 357 个原子，
直接把全量 schema 塞给模型既超长又难挑。本脚本做三件事：

1. **按域分组**（Excel / File / Word / PDF / Browser / DataTable / Code / Script ...）
2. **只留决策必需的信息**：key、中文名、必填参数（含枚举可选值）、输出
3. **支持按域/关键词检索**，以及单个原子的完整参数表

## 用法

    python atom_catalog.py                          # 列出所有域 + 原子数
    python atom_catalog.py --domain Excel           # 看 Excel 域有哪些原子
    python atom_catalog.py --domain Excel --detail  # 连必填参数一起看
    python atom_catalog.py --atom Excel.read_excel  # 单个原子完整参数
    python atom_catalog.py --search 拆分            # 按关键词找
    python atom_catalog.py --json                   # 机器可读
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import flow_generator as fg

# 域的中文说明（没有的域直接显示原名）
DOMAIN_CN = {
    "Excel": "Excel 表格",
    "File": "文件/文件夹",
    "Word": "Word 文档",
    "PDF": "PDF 文档",
    "Browser": "浏览器（网页）",
    "BrowserSoftware": "浏览器（客户端）",
    "IeBrowserSoftware": "IE 浏览器",
    "DataTable": "数据表",
    "DataProcess": "数据处理",
    "Code": "流程控制（循环/条件）",
    "Script": "脚本（Python 模块）",
    "System": "系统/窗口",
    "Mouse": "鼠标",
    "Keyboard": "键盘",
    "ComputerUse": "屏幕自动化（CV）",
    "Mail": "邮件",
    "Database": "数据库",
    "Http": "HTTP 请求",
    "Dialog": "对话框",
    "OCR": "OCR 识别",
    "AI": "AI 能力",
    "Report": "报表",
    "Process": "进程",
    "Compress": "压缩/解压",
    "Image": "图片处理",
}


def _req(p):
    """这个参数是不是必填（required 或 formType 明确要求）。"""
    return bool(p.get("required"))


def _opts(p):
    return [o.get("value") if isinstance(o, dict) else o for o in (p.get("options") or [])]


def atom_brief(key, a, detail=False):
    """一个原子的紧凑描述。"""
    lines = ["%s — %s" % (key, a.get("title") or "")]
    if detail:
        ins = [p for p in (a.get("inputList") or []) if isinstance(p, dict)]
        req = [p for p in ins if _req(p)]
        opt = [p for p in ins if not _req(p)]
        if req:
            lines.append("    必填：")
            for p in req:
                s = "      · %s (%s)" % (p.get("key"), p.get("types") or p.get("name") or "")
                o = _opts(p)
                if o:
                    s += "  可选值：" + " / ".join(str(x) for x in o)
                elif p.get("default") not in ("", None):
                    s += "  默认：%s" % p.get("default")
                lines.append(s)
        if opt:
            names = [p.get("key") for p in opt]
            lines.append("    可选：" + ", ".join(names))
        outs = [p.get("key") for p in (a.get("outputList") or []) if isinstance(p, dict)]
        if outs:
            lines.append("    输出：" + ", ".join(outs))
        tip = (a.get("comment") or a.get("helpManual") or "").strip()
        if tip:
            lines.append("    说明：" + tip.splitlines()[0][:120])
    else:
        req = [p.get("key") for p in (a.get("inputList") or []) if isinstance(p, dict) and _req(p)]
        if req:
            lines.append("    必填：" + ", ".join(req))
    return "\n".join(lines)


def domains(atoms):
    d = {}
    for k in atoms:
        dom = k.split(".")[0] if "." in k else "（无域）"
        d.setdefault(dom, []).append(k)
    return dict(sorted(d.items(), key=lambda x: -len(x[1])))


def main():
    ap = argparse.ArgumentParser(description="原子目录（AI 选原子的依据）")
    ap.add_argument("--domain", default="", help="只看某个域，如 Excel / File / Code")
    ap.add_argument("--atom", default="", help="看单个原子的完整参数")
    ap.add_argument("--search", default="", help="按关键词找（匹配 key 与中文名）")
    ap.add_argument("--detail", action="store_true", help="带出必填参数与枚举值")
    ap.add_argument("--offline", action="store_true", help="用本地缓存（不连服务端）")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    a = ap.parse_args()

    atoms = fg.load_atom_dict(offline=a.offline)
    if not atoms:
        print("❌ 原子字典为空（服务端不可达且无缓存）")
        return 2

    # 单个原子
    if a.atom:
        key = a.atom
        if key not in atoms:
            import difflib
            cand = difflib.get_close_matches(key, list(atoms), n=5, cutoff=0.4)
            print("❌ 没有原子：%s" % key)
            if cand:
                print("   相近的有：" + ", ".join(cand))
            return 2
        atom = atoms[key]
        if a.json:
            print(json.dumps({"key": key, "atom": atom}, ensure_ascii=False, indent=2))
        else:
            print(atom_brief(key, atom, detail=True))
        return 0

    # 关键词
    if a.search:
        kw = a.search.lower()
        hit = [(k, v) for k, v in atoms.items()
               if kw in k.lower() or kw in (v.get("title") or "").lower()]
        if a.json:
            print(json.dumps([{"key": k, "title": v.get("title")} for k, v in hit],
                             ensure_ascii=False, indent=2))
            return 0
        print("关键词「%s」命中 %d 个原子：\n" % (a.search, len(hit)))
        for k, v in hit:
            print(atom_brief(k, v, detail=a.detail))
            print()
        return 0

    # 某域
    if a.domain:
        doms = domains(atoms)
        keys = [k for k in atoms if k.split(".")[0].lower() == a.domain.lower()]
        if not keys:
            print("❌ 没有这个域：%s" % a.domain)
            print("   可用域：" + ", ".join("%s(%d)" % (d, len(v)) for d, v in doms.items()))
            return 2
        if a.json:
            print(json.dumps([{"key": k, "title": atoms[k].get("title"),
                               "required": [p.get("key") for p in (atoms[k].get("inputList") or [])
                                            if isinstance(p, dict) and _req(p)]}
                              for k in keys], ensure_ascii=False, indent=2))
            return 0
        cn = DOMAIN_CN.get(a.domain, a.domain)
        print("%s 域（%s）共 %d 个原子：\n" % (a.domain, cn, len(keys)))
        for k in keys:
            print(atom_brief(k, atoms[k], detail=a.detail))
            print()
        return 0

    # 总览
    doms = domains(atoms)
    if a.json:
        print(json.dumps({d: {"count": len(v), "cn": DOMAIN_CN.get(d, d)} for d, v in doms.items()},
                         ensure_ascii=False, indent=2))
        return 0
    print("共 %d 个原子，按域分组：\n" % len(atoms))
    for d, v in doms.items():
        print("  %-22s %-14s %3d 个" % (d, DOMAIN_CN.get(d, ""), len(v)))
    print("\n看某个域：--domain Excel --detail")
    print("看单个  ：--atom Excel.read_excel")
    print("按关键词：--search 拆分")
    return 0


if __name__ == "__main__":
    sys.exit(main())
