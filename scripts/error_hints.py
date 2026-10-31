#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""错误翻译层：把 RPA 引擎/平台的报错翻译成「人话 + 为什么 + 怎么修」。

## 为什么需要这一层

平台报错有两类系统性失真，用户看到的字面意思往往**不是**真因：

1. **文案错位**——`Docx.create_docx` 撞上「目标 docx 被 Word 占用」时，平台弹的是
   「Word 文档读取失败，请检查文件是否损坏！」。用户会去怀疑文件，而真因在同一条
   `executor-*.log` 前面的 `pywintypes.com_error: ... 因为它已在别处打开`。
2. **文案过泛**——openapi 只回 `{"code":"5001","msg":"启动失败"}`，真因
   （`服务器错误: 该模块不存在`）藏在 `robot_execute_record.execute_log` 里。

本模块**只做翻译**，不改变任何执行逻辑：输入一段日志文本，输出
`[{id, sev, title, cause, fix, hit}]`。展示由 `ai_flow.py` / `run_flow_local.py` 负责。

## 用法

```python
import error_hints as eh
hints = eh.translate(log_text)              # 一段日志 → 命中列表
print(eh.format_report(hints))              # 直接打印成给人看的块
d = eh.explain_run(run_result)              # run_flow_local.run() 的返回值
d = eh.explain_detail(pf.get_last_execute_error(...))
```

命令行自测：

```
python error_hints.py --text "pywintypes.com_error: Word 无法保存此文件，因为它已在别处打开"
python error_hints.py --log "%TEMP%/rpa_run_xxx/logs/executor-2026-10-30.log"
```
"""
import argparse
import glob
import os
import re
import sys


def _mk(_id, sev, title, cause, fix, *pats):
    """一条翻译规则。`pats` 任一命中即算命中，规则内按顺序取第一个匹配做 `hit`。"""
    return {"id": _id, "sev": sev, "title": title, "cause": cause, "fix": fix,
            "res": [re.compile(p, re.I) for p in pats]}


# ---------------------------------------------------------------- 规则表
# 顺序 = 展示优先级：越具体、越「真因」的排前面。
# sev: fatal=会直接失败 / warn=可能失败或只是噪音 / info=解释性
PATTERNS = [
    # ---- Office 文件占用（最高频，且平台文案会误导成「文件损坏」） ----
    _mk("word_locked", "fatal",
        "目标 Word 文件正被占用",
        "SaveAs 要覆盖的 .docx 正被 Word / WPS / 预览窗格打开，COM 抛「已在别处打开」。"
        "流程里的 create_docx(overwrite) 并不会先删旧文件（源码里 os.remove 被注释掉了）。",
        "先关闭该 .docx 及后台残留的 WINWORD/WPS 进程；或在规格里加一个 "
        "File.file_delete(on_error=skip) 前置节点，并把输出名换成新名字。",
        r"因为它已在别处打开", r"已在别处打开",
        r"The file is locked", r"being used by another (user|process)"),

    _mk("office_locked_generic", "fatal",
        "目标 Office 文件被其它程序独占",
        "Excel/Word 的 COM 在覆盖保存时拿不到独占写权限。",
        "关掉占用的程序；确认目标目录不是只读、不是 OneDrive 同步中的临时锁定。",
        r"该文件已被其他程序占用", r"文件正在被使用", r"is being used by another"),

    _mk("misleading_word_read", "warn",
        "「文件已损坏」是误导文案，真因通常是占用或路径不存在",
        "astronverse 的 word 原子把 COM 异常统一吞成「Word 文档读取失败，请检查文件是否损坏！」，"
        "真实异常写在同一条日志前面的 com_error / FileNotFound 里。",
        "往上翻日志找 com_error / No such file；按命中的具体原因处理，别去修文件。",
        r"Word 文档读取失败，请检查文件是否损坏",
        r"请检查文件是否损坏"),

    _mk("com_error", "fatal",
        "Office COM 调用失败",
        "Office 未安装/未激活、目标文件被占用、或后台残留 WINWORD/EXCEL 进程；"
        "WPS 对 COM 的兼容也不完整。",
        "关闭所有 Office 窗口与残留进程后重试；确认装的是 MS Office。"
        "排查命令：Get-Process WINWORD,EXCEL,wps,et -EA SilentlyContinue",
        r"pywintypes\.com_error", r"com_error:", r"(-\d{9,})"),

    # ---- Python 子流程模块（c_module）----
    _mk("module_missing", "fatal",
        "发布版本里没有这个 Python 子流程模块",
        "c_module 只有 robot_version=0 的设计态行；执行器按 robotVersion 取模块 → 取不到 → "
        "「服务器错误: 该模块不存在」。",
        "重跑 --stage expose（publish() 内部会调 clone_modules_for_version 复制到该版本）。"
        "仍失败就查 c_module 的 robot_version / deleted 两列。",
        r"该模块不存在", r"模块不存在"),

    _mk("module_no_main", "fatal",
        "子流程模块编译后没有 main 函数",
        "module_content 落库时被转义破坏（换行变成字面 \\n，整个文件成一行注释），"
        "或者模块里确实没写 def main(args)。",
        "从 scripts/mod_*.py 原文重新写入；写回必须走二进制（X'..'），"
        "不要用 mysql -N -B 的文本通道。",
        r"has no attribute 'main'", r"module '\w+' has no attribute"),

    _mk("module_save_reject", "fatal",
        "模块保存被拒：creator_id / robot_version 不匹配",
        "getOneModule / getIdByModuleId 都要求 creator_id = 登录用户 UUID 且 robot_version = 0；"
        "旧数据里 creator_id 是 '73' 这类数字串。",
        "把 c_module.creator_id 改成当前登录用户 UUID（本机是 "
        "b7faea17-12f1-4b6d-9221-15b36919c09b）后重试保存。",
        r"不存在该模块，无法更新"),

    _mk("module_content_corrupt", "fatal",
        "模块内容疑似被转义破坏",
        "模块源码里出现字面 \\n / \\\" —— 说明经过了一次 JSON 或 mysql 文本通道的双重转义。",
        "从磁盘原文重新写入，写完用 HEX() 回读比对长度。",
        r"SyntaxError: unexpected character after line continuation",
        r"invalid syntax.*\\\\n"),

    # ---- 路径 ----
    _mk("no_such_file", "fatal",
        "路径不存在",
        "RPA 流程不会自动创建目录或输入文件；入参指向的路径必须已经存在"
        "（只有「输出文件」本身可以不存在）。",
        "先手工建目录，或在流程最前面加 File.create_folder 节点。",
        r"No such file or directory", r"FileNotFoundError",
        r"cannot find the (file|path)", r"系统找不到指定的路径",
        r"找不到指定的文件", r"The system cannot find the path",
        r"路径不存在", r"文件不存在", r"目录不存在"),

    _mk("path_invalid_msg", "fatal",
        "路径参数非法（平台通用提示）",
        "路径为空、含非法字符，或 / 与 \\ 混写（F://a//b 会被当成分隔符链）导致解析异常。",
        "用绝对路径且只用一个反斜杠；规格里路径统一过 norm_win_path 归一。",
        r"填写文件路径有误", r"请输入正确的路径", r"路径有误"),

    _mk("path_too_long", "fatal",
        "路径过长",
        "Windows 传统 API 上限 260 字符；深层临时目录很容易撞上。",
        "把输出目录缩短（如 D:\\rpa_out），或开启长路径支持。",
        r"path too long", r"文件名或扩展名太长", r"MAX_PATH"),

    _mk("permission_denied", "fatal",
        "权限不足 / 文件被独占",
        "目标目录不可写（Program Files、系统盘根目录），或文件被自身进程打开后又要写。",
        "换到用户目录下的输出路径；确认没有别处持有句柄。",
        r"PermissionError", r"拒绝访问", r"Access is denied", r"WinError 5\b"),

    _mk("no_space", "fatal",
        "磁盘空间不足",
        "输出盘写满，或临时目录所在盘写满。",
        "清理磁盘；把输出改到空间充足的盘。",
        r"No space left", r"磁盘空间不足", r"not enough space"),

    # ---- 变量 / 数据 ----
    _mk("var_undefined", "fatal",
        "流程参数未定义（p_var 引用了不存在的参数）",
        "节点用 $p:xxx 引用参数，但 c_param 里没有这个变量 → 编译出的 Python 没有对应赋值语句。",
        "确认规格 params 里声明了该名字，重跑落库（sync_params）后再发布；"
        "注意 openapi 执行的是已发布版本快照。",
        r"NameError: name '(\w+)' is not defined"),

    _mk("key_error", "fatal",
        "取不到预期字段",
        "上游节点输出里没有这个 key：可能改了别名、该分支没执行到、或输出为空。",
        "检查上游节点 out 是否真的产出该字段；调试时先加一个「打印」节点看真实结构。",
        r"KeyError: '([^']+)'"),

    _mk("bool_as_str", "warn",
        "参数被编译成了字符串（Bool/数字字面量失真）",
        "flow_generator 在 need_eval=False 时用 f-string 拼串，False → 'False'（非空字符串=真值）。"
        "典型后果：visible_flag='False' 仍然弹窗、underline_flag='0' 变成下划线、bold_flag='False' 整段加粗。",
        "该参数改成 {\"type\":\"python\",\"value\":repr(v)}；_coerce_literals() 已按参数类型自动处理。",
        r"TypeError: .{0,40}(bool|str)", r"'str' object cannot be interpreted"),

    _mk("empty_data", "warn",
        "数据为空或行数不足",
        "读到的表/文本是空的，后续按下标取列就越界。",
        "在上游加「是否为空」判断分支；确认源文件真的有数据行（不是只有表头）。",
        r"IndexError: list index out of range"),

    _mk("pdf_no_table", "warn",
        "这个 PDF 里没有可识别的表格",
        "`PDF.extract_forms_from_pdf` 靠 PDF 的**文本层 + 线条结构**识别表格；"
        "扫描件（整页是一张图）没有文本层，识别不到表格，就会报这句。",
        "先用 `PDF.get_pdf_text` 试一下：返回空串说明是扫描件 → 走 OCR 路线；"
        "有文字但没表格线时，用 `PDF.get_pdf_text(txt)` + 规则切分代替。",
        r"所选页面没有表格", r"没有表格", r"no table"),

    _mk("sheet_missing", "fatal",
        "工作表不存在",
        "指定了 sheet 名，但文件里没有这张表（或名字带空格/大小写不同）。",
        "先列 sheet 名再取；或改成按序号取第一张表。",
        r"Worksheet .{0,30}does not exist", r"工作表.{0,10}不存在"),

    # ---- 元素 / UI ----
    _mk("element_missing", "fatal",
        "元素定位失败",
        "元素不在当前 robot 名下（平台元素按 robot 隔离），或目标页面结构已变。",
        "在设计器里对该 robot 重新拾取元素；或用 element_relocator 按锚点重新定位。",
        r"未找到元素", r"元素不存在", r"找不到元素", r"no such element",
        r"Element not found", r"元素查找超时"),

    _mk("element_timeout", "warn",
        "等待超时",
        "目标窗口/元素在超时时间内没出现：程序没启动、被弹窗挡住、或加载太慢。",
        "加大超时；确认目标程序已启动且窗口没最小化；必要时先加「激活窗口」。",
        r"TimeoutError", r"等待超时", r"Playwright.{0,30}Timeout", r"timed out"),

    # ---- 环境 / 依赖 ----
    _mk("gateway_down", "fatal",
        "设计器本地网关未监听",
        "执行引擎不直连服务端，它经客户端本地网关（默认 13159）取流程与元素。",
        "先启动 astron-rpa 设计器客户端，再跑本地执行。",
        r"网关.{0,10}未监听", r"connect_ex", r"Connection refused.{0,20}13159"),

    _mk("designer_python_missing", "fatal",
        "客户端自带 Python 缺失",
        "%APPDATA%\\astron-rpa\\python_core\\python.exe 不存在（设计器未装或装到别处）。",
        "确认设计器安装完整；或显式传 --client-python 指定路径。",
        r"找不到设计器自带 Python"),

    _mk("missing_module_py", "fatal",
        "缺 Python 依赖",
        "子流程跑在客户端自带 Python（python_core）里，该环境没装这个包；"
        "系统 python 装了不算。",
        "%APPDATA%\\astron-rpa\\python_core\\python.exe -m pip install <包名>",
        r"ModuleNotFoundError: No module named '([^']+)'",
        r"ImportError: No module named"),

    _mk("ocr_unavailable", "fatal",
        "OCR / AI 服务不可用",
        "ai-service 的 XFYUN_API_KEY / XFYUN_API_SECRET / XFYUN_APP_ID 为空 → 讯飞返回 401 → "
        "网关对外表现成 503。不是服务没部署。",
        "改用 Windows 自带 OCR 的 Python 子流程（mod_pdf_ocr.py，离线免费）；"
        "或在 .130 上配好讯飞三件套。",
        r"503 Service Unavailable", r"AI 服务不可用", r"xfyun", r"讯飞"),

    _mk("schema_mismatch", "fatal",
        "openapi 入参 schema 与流程参数不一致",
        "登记 openai_workflows 时 parameters 传的是空表，openapi 按空 schema 校验，传任何参数都被拒。",
        "重跑 --stage expose（会把 c_param 真实参数表登记进 parameters）。",
        r"do not match the tool input schema", r"Arguments do not match"),

    # ---- 平台泛化文案（最后兜底，只解释不定位）----
    _mk("start_failed", "warn",
        "openapi 报「启动失败」= 执行器起不来，信息量约等于零",
        "code 5001 是 openapi 的统一外壳；真因在 robot_execute_record.execute_log "
        "或 executor-*.log 里。",
        "按本表其它命中项处理；都没有时直接读 execute_log 原文。",
        r"启动失败"),

    _mk("exec_error_generic", "warn",
        "平台只给了「执行错误」这类空壳文案",
        "openapi 的 result.msg 不携带节点级原因。",
        "用 publish_flow.get_last_execute_error(robot_id) 取 execute_log 里的 msg_str。",
        r'"msg"\s*:\s*"执行错误"', r"执行错误"),
]

# 严重度 → 图标；顺序用于排序
_SEV_ORDER = {"fatal": 0, "warn": 1, "info": 2}
_SEV_ICON = {"fatal": "❌", "warn": "⚠️", "info": "ℹ️"}


# ---------------------------------------------------------------- 对外接口
def translate(text, limit=5):
    """一段日志/报错文本 → 命中列表（按规则表顺序，按 id 去重）。

    `limit` 只保留前 N 条：fatal 优先，其次按规则表顺序。
    """
    if not text:
        return []
    if isinstance(text, (list, tuple)):
        text = "\n".join(str(x) for x in text)
    hits, seen = [], set()
    for p in PATTERNS:
        if p["id"] in seen:
            continue
        for r in p["res"]:
            m = r.search(text)
            if m:
                seen.add(p["id"])
                hits.append({
                    "id": p["id"], "sev": p["sev"], "title": p["title"],
                    "cause": p["cause"], "fix": p["fix"],
                    "hit": (m.group(0) or "").strip()[:120],
                })
                break
    hits.sort(key=lambda h: _SEV_ORDER.get(h["sev"], 9))  # 稳定排序：同级保持规则表顺序
    return hits[:limit] if limit else hits


def format_report(hits, indent="      "):
    """命中列表 → 给人看的文本块（无命中返回空串）。"""
    if not hits:
        return ""
    lines = []
    for h in hits:
        lines.append(f"{indent}{_SEV_ICON.get(h['sev'], '·')} {h['title']}")
        lines.append(f"{indent}   为什么：{h['cause']}")
        lines.append(f"{indent}   怎么修：{h['fix']}")
        if h.get("hit"):
            lines.append(f"{indent}   日志片段：{h['hit']}")
    return "\n".join(lines)


def explain_run(run_result):
    """翻译 `run_flow_local.run()` 的返回值。

    返回 `{"ok", "headline", "hints"}`；成功时 `hints` 为空。
    """
    if not isinstance(run_result, dict):
        return {"ok": False, "headline": "无执行结果", "hints": []}
    if run_result.get("ok"):
        return {"ok": True,
                "headline": f"{run_result.get('status')} 执行成功（{run_result.get('elapsed_sec')}s）",
                "hints": []}
    chunks = [str(run_result.get("error") or "")]
    for k in ("errors", "node_errors", "stderr_tail", "stdout_tail"):
        v = run_result.get(k)
        if isinstance(v, (list, tuple)):
            chunks.extend(str(x) for x in v)
    status = run_result.get("status") or "未拿到状态"
    hints = translate("\n".join(chunks))
    headline = f"{status} 执行失败" + ("（已定位真因）" if hints else "（日志里没找到已知特征，见下方原始行）")
    return {"ok": False, "headline": headline, "hints": hints}


def explain_detail(detail):
    """翻译 `publish_flow.get_last_execute_error()` 的返回值（openapi 失败路径）。"""
    if not isinstance(detail, dict):
        return {"ok": False, "headline": "", "hints": []}
    chunks = [str(detail.get("error_reason") or ""), str(detail.get("error") or "")]
    for m in (detail.get("detail") or []):
        chunks.append(str(m))
    text = "\n".join(chunks)
    hints = translate(text)
    return {"ok": bool(hints),
            "headline": "已从执行记录定位真因" if hints else "执行记录里没匹配到已知特征",
            "hints": hints}


def print_report(hits, indent="      ", empty_hint=""):
    """打印（无命中时可给一句兜底提示）。"""
    txt = format_report(hits, indent=indent)
    if txt:
        print(txt)
    elif empty_hint:
        print(f"{indent}{empty_hint}")


def latest_executor_logs(n=1):
    """最近 n 个执行器日志（`%APPDATA%/astron-rpa/logs/executor-*.log`）。"""
    base = os.path.join(os.environ.get("APPDATA", ""), "astron-rpa", "logs")
    files = sorted(glob.glob(os.path.join(base, "executor-*.log")), key=os.path.getmtime)
    return files[-n:] if files else []


# ---------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser(description="把 RPA 报错翻译成人话（只读日志，不执行流程）")
    ap.add_argument("--text", default="", help="直接给一段报错文本")
    ap.add_argument("--log", default="", help="日志文件路径；给 all 则读最新的执行器日志")
    ap.add_argument("--limit", type=int, default=5, help="最多展示几条（默认 5）")
    args = ap.parse_args()

    if args.log == "all":
        files = latest_executor_logs(1)
        if not files:
            print("找不到 %APPDATA%/astron-rpa/logs/executor-*.log", file=sys.stderr)
            return 2
        args.log = files[0]
        print(f"[日志] {args.log}")

    text = args.text
    if args.log:
        with open(args.log, encoding="utf-8", errors="replace") as f:
            text = f.read()
    if not text:
        ap.error("必须给 --text 或 --log")

    hits = translate(text, limit=args.limit)
    print("=" * 64)
    if hits:
        print(f"命中 {len(hits)} 条已知特征：")
        print(format_report(hits, indent="  "))
    else:
        print("没有命中已知特征 —— 日志里可能是一条新问题，先看原始 ERROR 行：")
        for ln in text.splitlines():
            if "| ERROR" in ln or "Traceback" in ln:
                print("  ", ln.strip()[:200])
    print("=" * 64)
    return 0 if hits else 1


if __name__ == "__main__":
    sys.exit(main())
