#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""原子覆盖报告：357 个原子，现有流程到底用了多少、还差哪些。

## 为什么需要

「有 300 多个原子」和「只有 14 条流程」不是矛盾——原子是零件，流程是产品。
但**哪些零件已经用上、哪些还没用、哪些根本不需要**，必须能一眼看清，
否则「下一步做什么」全靠拍脑袋。

本脚本只读，不改任何东西。

## 用法

    python coverage_report.py                  # 总览：按域统计已用/未用
    python coverage_report.py --domain Docx    # 某域未用原子清单
    python coverage_report.py --atom Docx.replace   # 某个原子被哪些流程用了
    python coverage_report.py --json
"""
import argparse
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import flow_generator as fg

ROOT = os.path.dirname(HERE)
FLOWS = os.path.join(ROOT, "flows")

ELEMENT_TYPES = {"WebPick", "UiaPick", "CvPick", "Pick", "Element"}

# 作废 key：字典 c_atom_meta_new 里残留，但**前端不生成、引擎也不认**，不应计入分母。
# 三重依据（2026-10-31）：
#   1) 引擎 token.py 的 TokenType 只有 IfEnd/ForEnd/TryEnd/GroupEnd 四个 End
#   2) 前端 atomKeyMap.ts 的 LOOP_END_MAP 生成收尾节点也只有这四个
#   3) 173 条真实流程 process_content 里，下面 8 个 key 出现次数均为 0
# Code.Process 是旧版子流程 key，前端已改用 Script.process（atomKeyMap.ts 注明 ProcessOld）
DEPRECATED_KEYS = {
    "Code.ForStepEnd", "Code.ForListEnd", "Code.ForDictEnd", "Code.WhileEnd",
    "Code.ElseEnd", "Code.ElseIfEnd", "Code.CatchEnd", "Code.FinallyEnd",
    "Code.Process",
    # Docx：字典残留但类里没有这两个方法，真实 183 条流程 0 命中；
    # 现行 key 是 open_docx / read_docx（分别 5 次 / 1 次命中）。
    "Docx.open_document", "Docx.read_document_content",
}

# 手工验证已覆盖的原子：能力已真跑通过，但无法走「规格 JSON → 生成器 → 落库」标准链路，
# 因此 flows/*.json 里没有对应节点、scan_flows() 扫不到。这里把它们单独计为「已覆盖」，
# 避免能力明明打通了却在覆盖率数字上漏掉。每条必须给出验证脚本 + 取证文件，可追溯。
# 值：{脚本, 说明}
MANUAL_COVERED = {
    # 组件节点 key 是 Code.Component.<动态雪花 componentId>，无法静态写死在规格里；
    # 走 scripts/component_e2e.py 动态建组件 → 挂载 → 主流程调用 → 真跑取证（5/5 通过）。
    "Script.component": {
        "script": "scripts/component_e2e.py",
        "note": "运行组件：建组件→填子流程→发版→挂载→主流程 Code.Component.<id> 调用，真跑 5/5 通过（demo/_component_out/组件已执行.txt）",
    },
}

DOMAIN_CN = {
    "Excel": "Excel 表格", "Docx": "Word 文档", "PDF": "PDF 文档",
    "File": "文件", "Folder": "文件夹", "DataTable": "数据表",
    "Code": "流程控制", "Script": "Python 脚本", "StringProcess": "字符串处理",
    "ListProcess": "列表处理", "DictProcess": "字典处理", "MathProcess": "数学计算",
    "TimeProcess": "时间日期", "DataConvertProcess": "数据转换", "DataProcess": "数据处理",
    "Network": "网络/HTTP", "Email": "邮件", "Encrypt": "加解密",
    "BrowserSoftware": "浏览器（客户端）", "BrowserElement": "浏览器元素（需拾取）",
    "IeBrowserSoftware": "IE 浏览器", "IeBrowserElement": "IE 元素（需拾取）",
    "Gui": "桌面界面", "Window": "窗口", "System": "系统", "Mouse": "鼠标",
    "Keyboard": "键盘", "ComputerUse": "屏幕自动化", "CV": "视觉识别",
    "VerifyCode": "验证码（需拾取）", "OpenApi": "开放接口", "Agent": "智能体",
    "ChatAI": "大模型对话", "DocumentAI": "文档智能", "Enterprise": "企业系统",
    "SAP": "SAP", "Report": "报表", "Software": "软件", "Dialog": "对话框",
    "WinEle": "Windows 元素", "Smart": "智能", "RecruitAI": "招聘智能",
    "ContractAI": "合同智能", "BrowserScript": "浏览器脚本",
    "IeBrowserScript": "IE 脚本",
}


def scan_flows():
    """{原子: [流程文件名...]}"""
    used = {}
    for p in sorted(glob.glob(os.path.join(FLOWS, "*.json"))):
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        name = d.get("name") or os.path.basename(p)[:-5]
        for n in d.get("nodes") or []:
            k = (n or {}).get("atom")
            if k:
                used.setdefault(k, [])
                if name not in used[k]:
                    used[k].append(name)
    return used


def needs_element(a):
    for p in (a.get("inputList") or []):
        if isinstance(p, dict) and p.get("required") and p.get("types") in ELEMENT_TYPES:
            return True
    return False


def main():
    ap = argparse.ArgumentParser(description="原子覆盖报告（只读）")
    ap.add_argument("--domain", default="", help="看某域的未用原子")
    ap.add_argument("--atom", default="", help="看某个原子被哪些流程用了")
    ap.add_argument("--offline", action="store_true", default=True, help="用本地缓存（默认）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    atoms = fg.load_atom_dict(offline=True)
    used = scan_flows()
    # 合并手工验证覆盖：把 MANUAL_COVERED 里确实存在于原子字典的 key 计入 used。
    # 标记来源为「手工验证」（区别于真正被流程规格引用的），供 --atom 查询时展示。
    manual_hit = [k for k in MANUAL_COVERED if k in atoms]
    for k in manual_hit:
        used.setdefault(k, []).append("（手工验证：%s）" % MANUAL_COVERED[k]["script"])

    # 单个原子
    if a.atom:
        k = a.atom
        if k not in atoms:
            print("❌ 没有原子：%s" % k)
            return 2
        users = used.get(k) or []
        if a.json:
            print(json.dumps({"atom": k, "title": atoms[k].get("title"), "flows": users},
                             ensure_ascii=False, indent=2))
        else:
            print("%s — %s" % (k, atoms[k].get("title")))
            print("被 %d 条流程使用：%s" % (len(users), "、".join(users) or "（没有，属于未覆盖能力）"))
        return 0

    # 按域统计（作废 key 直接剔除，不进分母）
    stats = {}
    for k, atom in atoms.items():
        if k in DEPRECATED_KEYS:
            continue
        d = k.split(".")[0]
        s = stats.setdefault(d, {"total": 0, "used": 0, "elem": 0, "unused": []})
        s["total"] += 1
        if needs_element(atom):
            s["elem"] += 1
        if k in used:
            s["used"] += 1
        else:
            s["unused"].append(k)

    valid_atoms = {k: v for k, v in atoms.items() if k not in DEPRECATED_KEYS}
    dep_n = len(atoms) - len(valid_atoms)
    total = len(valid_atoms)
    used_n = sum(1 for k in valid_atoms if k in used)
    zero_elem = sum(1 for k in valid_atoms.values() if not needs_element(k))

    if a.domain:
        d = a.domain
        hit = {k: v for k, v in stats.items() if k.lower() == d.lower()}
        if not hit:
            print("❌ 没有这个域：%s" % d)
            print("   可用域：" + ", ".join(sorted(stats)))
            return 2
        dom, s = list(hit.items())[0]
        if a.json:
            print(json.dumps({"domain": dom, "total": s["total"], "used": s["used"],
                              "unused": s["unused"]}, ensure_ascii=False, indent=2))
            return 0
        print("%s 域（%s）：共 %d 个原子，已用 %d，未用 %d"
              % (dom, DOMAIN_CN.get(dom, ""), s["total"], s["used"], len(s["unused"])))
        print("-" * 66)
        for k in s["unused"]:
            tag = "（需元素）" if needs_element(atoms[k]) else ""
            print("  %-30s %s%s" % (k, atoms[k].get("title") or "", tag))
        return 0

    # 总览
    if a.json:
        print(json.dumps({"total": total, "used": used_n, "zero_element": zero_elem,
                          "deprecated_excluded": dep_n,
                          "flows": len(glob.glob(os.path.join(FLOWS, "*.json"))),
                          "domains": {d: {"total": v["total"], "used": v["used"]}
                                      for d, v in stats.items()}}, ensure_ascii=False, indent=2))
        return 0

    nflows = len(glob.glob(os.path.join(FLOWS, "*.json")))
    print("原子覆盖报告")
    print("=" * 66)
    print("流程规格 %d 条 · 原子总数 %d · 已用 %d · 覆盖率 %.1f%%"
          % (nflows, total, used_n, used_n * 100.0 / total))
    print("（已剔除 %d 个作废 key：字典残留但前端不生成/编译器不认，见 DEPRECATED_KEYS）" % dep_n)
    if manual_hit:
        print("（含 %d 个手工验证原子：%s —— 已真跑通过但无法静态落规格，见 MANUAL_COVERED）"
              % (len(manual_hit), "、".join(manual_hit)))
    print("零元素可跑 %d 个（其余 %d 个需要界面元素）" % (zero_elem, total - zero_elem))
    print("  ↳ 需要元素的不再是阻塞项：element_pick.py 可自动拾取并写库（见 README「元素自动拾取」）")
    print()
    print("%-22s %6s %6s %6s  %s" % ("域", "原子", "已用", "未用", "说明"))
    print("-" * 66)
    for d, s in sorted(stats.items(), key=lambda x: -(x[1]["total"] - x[1]["used"])):
        print("%-22s %6d %6d %6d  %s"
              % (d, s["total"], s["used"], len(s["unused"]), DOMAIN_CN.get(d, "")))
    print("-" * 66)
    print("\n看某域还没用什么：--domain Excel")
    print("看某原子被谁用了：--atom Excel.read_excel")
    return 0


if __name__ == "__main__":
    sys.exit(main())
