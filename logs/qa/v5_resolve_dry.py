# -*- coding: utf-8 -*-
"""V5：`ai_flow.resolve_scripts(..., dry=True)` dry 模式验证（**不建模块、不发网络请求**）。

验证：
  1) 只落盘 `scripts/mod_gen_<名字>.py`，不调用 `publish_flow.create_module` /
     `save_module` / `open_module`（用 unittest.mock 打桩，桩被调用即失败）；
  2) 节点 `content` 在 dry 模式下**不**被替换成假 moduleId；
  3) 引用不存在的脚本时抛清晰错误（SystemExit）；
  4) dry 模式不写台账 logs/script_modules.json、不写 docs/原子需求池.md（无服务端副作用
     的承诺要连本地记录一起干净，否则下一次真跑会误判「模块已存在」）。

用法：C:/Python314/python.exe logs/qa/v5_resolve_dry.py
"""
import copy
import json
import os
import shutil
import sys
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import ai_flow as af              # noqa: E402
import publish_flow as pf        # noqa: E402

QA_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT_NAME = "qa_dry_run_split"
GEN_FILE = os.path.join(ROOT, "scripts", af.MOD_GEN_PREFIX + SCRIPT_NAME + ".py")

CODE = '''\
import os
import openpyxl


def main(args):
    src_file = args.get("src_file")
    out_dir = args.get("out_dir")
    if not src_file or not os.path.exists(src_file):
        raise RuntimeError("源文件不存在：%s" % src_file)
    os.makedirs(out_dir, exist_ok=True)
    return {"ok": True, "groups": 0, "skipped": 0, "files": []}
'''


def build_spec(bad_ref=False):
    mp = [{"varName": "src_file", "varValue": "$p:src_file"},
          {"varName": "out_dir", "varValue": "$p:out_dir"}]
    content = "$script:这个脚本不存在" if bad_ref else "$script:" + SCRIPT_NAME
    return {
        "name": "QA-dry-resolve",
        "params": [
            {"name": "src_file", "type": "Str", "default": "F:\\qa\\in\\订单.xlsx"},
            {"name": "out_dir", "type": "Str", "default": "F:\\qa\\out"},
        ],
        "scripts": {SCRIPT_NAME: {
            "name": "QA dry-run 脚本",
            "why": "命中判定 B-2：输出文件数取决于运行时数据",
            "maintain_hint": "改输出目录 → 改参数 out_dir；改逻辑需找开发改 Python",
            "code": CODE,
        }},
        "nodes": [{
            "atom": "Script.module",
            "alias": "拆分（自动脚本）",
            "inputs": {"content": content,
                       "module_param": "$json:" + json.dumps(mp, ensure_ascii=False)},
            "out": {"program_script": "split_result"},
        }],
    }


def snapshot():
    """记录 dry 运行前后的本地副作用文件状态。"""
    return {
        "ledger_exists": os.path.exists(af.SCRIPT_LEDGER),
        "ledger_size": os.path.getsize(af.SCRIPT_LEDGER) if os.path.exists(af.SCRIPT_LEDGER) else -1,
        "pool_size": os.path.getsize(af.ATOM_POOL) if os.path.exists(af.ATOM_POOL) else -1,
        "gen_file_exists": os.path.exists(GEN_FILE),
    }


def main():
    results = {}
    before = snapshot()
    print("=" * 78)
    print("V5  resolve_scripts(dry=True) —— 不发网络请求")
    print("=" * 78)
    print("运行前：台账存在=%s(大小 %d) / 原子需求池大小=%d / mod_gen 文件存在=%s"
          % (before["ledger_exists"], before["ledger_size"], before["pool_size"],
             before["gen_file_exists"]))
    print("SCRIPT_LEDGER = %s" % os.path.relpath(af.SCRIPT_LEDGER, ROOT))
    print("ATOM_POOL     = %s" % os.path.relpath(af.ATOM_POOL, ROOT))

    spec = build_spec()
    spec_before = copy.deepcopy(spec)

    boom = AssertionError("P0：dry 模式调用了服务端接口（应禁止）")
    with mock.patch.object(pf, "create_module", side_effect=boom) as m_create, \
         mock.patch.object(pf, "save_module", side_effect=boom) as m_save, \
         mock.patch.object(pf, "open_module", side_effect=boom) as m_open, \
         mock.patch.object(af, "pf", pf):
        try:
            mids = af.resolve_scripts(None, "dummy_robot", spec, dry=True)
            raised = None
        except BaseException as e:                              # noqa: BLE001
            mids, raised = None, e

    print("\n--- 1) 调用结果 ---")
    print("返回值 mids = %r" % (mids,))
    print("抛出异常    = %r" % (raised,))
    no_net = (m_create.call_count == 0 and m_save.call_count == 0 and m_open.call_count == 0)
    print("create_module 调用次数 = %d" % m_create.call_count)
    print("save_module   调用次数 = %d" % m_save.call_count)
    print("open_module   调用次数 = %d" % m_open.call_count)
    print("未发任何网络请求：%s" % ("PASS" if no_net and raised is None else "FAIL"))
    results["V5-1 dry 不发网络请求"] = no_net

    print("\n--- 2) 落盘 scripts/mod_gen_*.py ---")
    print("生成文件存在：%s" % os.path.exists(GEN_FILE))
    if os.path.exists(GEN_FILE):
        print("文件路径：%s" % os.path.relpath(GEN_FILE, ROOT))
        with open(GEN_FILE, encoding="utf-8") as f:
            head = f.read()
        print("文件前 4 行：")
        for ln in head.splitlines()[:4]:
            print("   " + ln)
        print("代码体完整（含 def main / args.get）：%s"
              % ("def main(args):" in head and 'args.get("src_file")' in head))
    results["V5-2 落盘 mod_gen_*.py"] = os.path.exists(GEN_FILE)

    print("\n--- 3) 节点 content 是否被替换 ---")
    content = spec["nodes"][0]["inputs"]["content"]
    print("dry 后 content = %r" % content)
    untouched = content == "$script:" + SCRIPT_NAME
    print("仍是 $script: 占位（未写假 moduleId）：%s" % ("PASS" if untouched else "FAIL"))
    print("spec 里没有 _resolved_modules：%s" % ("_resolved_modules" not in spec))
    results["V5-3 content 未被替换"] = untouched and "_resolved_modules" not in spec

    print("\n--- 4) 本地副作用 ---")
    after = snapshot()
    print("台账变化：%s → %s（大小 %d → %d）"
          % (before["ledger_exists"], after["ledger_exists"],
             before["ledger_size"], after["ledger_size"]))
    print("原子需求池大小：%d → %d" % (before["pool_size"], after["pool_size"]))
    clean = (before["ledger_size"] == after["ledger_size"]
             and before["pool_size"] == after["pool_size"])
    print("dry 不写台账 / 不写需求池：%s" % ("PASS" if clean else "FAIL"))
    results["V5-4 dry 无本地记录副作用"] = clean

    print("\n--- 5) 引用不存在的脚本 ---")
    bad = build_spec(bad_ref=True)
    try:
        af.resolve_scripts(None, "dummy_robot", bad, dry=True)
        print("❌ 没有抛错（应 SystemExit）")
        e_ok = False
        msg = ""
    except BaseException as e:                                  # noqa: BLE001
        msg = str(e)
        e_ok = isinstance(e, SystemExit)
        print("异常类型 = %s（期望 SystemExit）：%s" % (type(e).__name__, "PASS" if e_ok else "FAIL"))
        print("异常消息：")
        for ln in msg.splitlines():
            print("   " + ln)
        # 消息是否可用于修复：要提到缺哪个名字 + 列出可用名字 + 修法
        helpful = (("这个脚本不存在" in msg) and (SCRIPT_NAME in msg) and ("修法" in msg))
        print("消息能指导修复（含缺的名字 / 可用名字 / 修法）：%s" % ("PASS" if helpful else "FAIL"))
    results["V5-5 引用不存在脚本抛清晰错误"] = e_ok and helpful

    # 清理本次 dry 落盘的文件
    if os.path.exists(GEN_FILE):
        os.remove(GEN_FILE)
        print("\n（已清理测试生成文件 %s）" % os.path.relpath(GEN_FILE, ROOT))

    print("\n" + "=" * 78)
    print("V5 判定汇总")
    print("=" * 78)
    for k, v in results.items():
        print("  %-34s %s" % (k, "PASS" if v else "FAIL"))
    bad_keys = [k for k, v in results.items() if not v]
    print("\n未通过：%s" % (", ".join(bad_keys) if bad_keys else "无"))
    return 0 if not bad_keys else 1


if __name__ == "__main__":
    sys.exit(main())
