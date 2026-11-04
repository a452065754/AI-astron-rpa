# -*- coding: utf-8 -*-
"""
星辰开源 · 路线 B 流程生成器（flow_generator.py）
================================================
核心目的：自然语言 → AI 生成「固定的 RPA 流程」（Astron RPA process_content 节点树）。

设计要点（均来自 docs/开发文档-AI操作RPA-OpenAPI集成.md §12 / §13 的实证结论）：
  - 落库格式是「扁平 JSON 数组」，节点用 `key`(原子全名) 标识，顺序=执行顺序。
  - 参数值三型：other=字面量 / var=变量 / element=引用 c_element 元素库。
  - 标准尾部：advanced（前后延迟）+ exception（出错策略/重试）。
  - 生成器从 `.130` 的 `c_atom_meta_new` 实时拉取原子 schema，保证 key/参数/输出都真实存在；
    离线时用 `docs/atom_dict_cache.json` 缓存。
  - 写库（`/api/robot/process/save`）需要 Casdoor 会话 token，属红线操作；默认只生成+校验，
    显式 `--write` + 环境变量 RPA_SESSION_TOKEN 才尝试落库（且需用户授权）。

用法：
  python flow_generator.py --purpose excel_export
  python flow_generator.py --nl "把订单表导成新文件"
  python flow_generator.py --purpose excel_export --write --robot-id 2065231113695461380
"""
import argparse
import base64
import json
import os
import re
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rpa_config as cfg
from element_relocator import ServerDb, unwrap_special  # SQL 兜底通道（默认关闭）

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_PATH = os.path.join(HERE, "docs", "atom_dict_cache.json")
ELEMENT_CACHE = os.path.join(HERE, "docs", "element_cache.json")
DEFAULT_ROBOT_ID = 2065231113695461380

# 演示用真实数据目录（模板默认路径必须指向**真实存在**的文件，
# 否则流程跑起来第一步就报「找不到文件」——2026-10-30 用户实测踩到）。
# 以前这里写死本机绝对路径 `F:\AI-Workbench\10-Projects\星辰开源\demo`，
# 换台机器 clone 下来，内置模板生成的流程默认参数全是坏路径。改成从 `HERE` 推。
DEMO_DIR = os.path.join(HERE, "demo")
DEMO_XLSX = os.path.join(DEMO_DIR, "订单.xlsx")
DEMO_TXT = os.path.join(DEMO_DIR, "源文件.txt")

# 规格里「仓库内路径」的占位符（2026-11-03）。
#
# ## 为什么不能改成相对路径（这是本项改造最关键的结论）
#
# 直觉方案是把默认值写成 `demo/订单.xlsx`，让引擎按 cwd 解析。**行不通**：
# 引擎执行前会 `os.chdir(project_dir)`（`astronverse/executor/debug/bdb.py::cmd_start`），
# 而 `project_dir` 是 `%APPDATA%\astron\<project_id>` —— **编译缓存目录**
# （`Config.gen_core_path = {venv_run_dir}/astron/`）。相对路径会解析到那个缓存里，
# 必然「找不到文件」。所以默认值只能是绝对路径。
#
# 于是换个方向：**规格里不写某一台机器的路径，只写占位符**，发布时替换成本机仓库根。
# 仓库因此可迁移（clone 到任何目录都成立），而落到平台的参数仍然是合法绝对路径。
#
# ## 用在哪里（边界很重要）
#
# 只替换 `params[].default` 与 `scripts.<名>.params[].default`。
# **不能替换 `nodes[].inputs`**：那里以 `$` 开头的值表示「引用上游变量名」，
# `${PROJECT_ROOT}` 会被当成变量名 `{PROJECT_ROOT}` 去找。
PROJECT_ROOT_TOKEN = "${PROJECT_ROOT}"

# 内嵌脚本占位前缀（2026-11-02）。
#
# 规格里 `Script.module` 的 `content` 是一个普通的 Str 参数，正常写模块 id 或 `$p:module_id`。
# 但「AI 现场写脚本」这件事没法提前知道模块 id —— 模块是**运行时才建**的。所以允许先写
# `"content": "$script:脚本名"` 这个**占位符**：生成器原样透传（不解析、不报错），
# 由 `ai_flow.resolve_scripts()` 在建节点树之前换成真实 moduleId。
#
# 这里只做「透传」，不做任何替换 —— 生成器必须能在没有服务端会话的情况下产出节点树
# （离线校验、draft 阶段、spec_lint 都依赖这一点）。
SCRIPT_REF_PREFIX = "$script:"
SCRIPT_ATOM_KEY = "Script.module"

# `Script.module` 节点的落库结构（2026-11-03 用户目视确认后定稿）。
#
# 曾经有过 `baked` / `bound` 两种模式（区别只在 module_param 写成死字面量还是保留
# `rpa:special` 引用），**那个权衡已经作废** —— 因为它俩都错了：错在把 `module_param`
# 整块当**JSON 字符串**塞、把 `content` 包成 `[{type,value}]` 数组。
#
# 平台设计器真正认的结构（从用户手工搭的 `导入MES` 流程取到的标准答案）：
#
#   c_param（模块级参数定义）：module_id=<moduleId>, process_id=NULL, var_direction=0,
#                             var_type=Str, var_value='[{"type":"other","value":"默认值"}]'
#            ↓ 每行的 id 就是下面 varId
#   节点 inputList：
#     content      = "<moduleId>"                  ← **纯字符串**，不是数组
#     module_param = [ {"varId": "<c_param.id>", "varName": "src_file",
#                       "varValue": {"rpa":"special","value":[{"type":"other","value":"…"}]}} ]
#                                                ← **真数组**，不是 JSON 字符串
#
# 缺任何一块的后果（都实测过）：
#   - 缺 c_param 模块级参数 → 节点参数面板「暂无数据」；
#   - module_param 是字符串  → 前端 parse 失败，顶栏报「请求参数解析失败」；
#   - content 是数组        → 前端按字符串取模块 id 取不到。
#
# 这套结构同时解决了两件事：设计器**能显示**、参数**能改且真生效**（改 out_dir 后
# 产出落到新目录，2026-11-03 实测）。所以不需要「流程参数绑定」那套迂回方案。
#
# varId 来自服务端，离线构建时拿不到 → `build_process_content()` 收 `module_param_ids`；
# 没有映射时 varId 写空串（结构仍合法，只是设计器参数表格里那一行认不到，需要
# `relink_modules.py` 补齐）。

# 字典声明多个输出、但 Python 实现只 `return` 一个值的原子（坑 145）。
# 对这类原子：只按 spec 显式声明的输出生成**一个**变量；不声明就不解包。
SINGLE_RETURN_ATOMS = {
    "DataTable.copy_data",   # 声明 4 个（copied_cell/row/column/area），实现只 return 被复制的数据
    "DataTable.read_data",   # 声明 4 个（cell/row/column/area_info），实现按 read_type 只 return 一个
    # 声明 8 个（get_ele_attr/value/html/link/text/position/selected/style），
    # 实现按 get_type 只 return 一个值；按 8 个解包必然错位（复现包 A5）
    "BrowserElement.element_operation",
}

# 字典里 **一个输出都没声明**、但实现确实 `return` 了值（判断型原子）。
# 想让流程能对返回值下断言，只能在 spec 的 `out` 里显式要，这里按名字补一个变量。
# 例：Window.exist 返回 bool（window/window.py:49 `return True/False`），
#     字典 outputList 为空 —— 不补这一条，判断结果就只能靠「节点有没有抛异常」猜。
EXTRA_OUTPUT_ATOMS = {
    "Window.exist": ["window_exist"],
}


# ----------------------------- 原子字典（真实 schema） -----------------------------
def _parse_atom(raw):
    """解析 atom_content；失败时尝试修复双重转义（两个反斜杠 -> 一个）。返回 (obj, repaired)。"""
    try:
        return json.loads(raw), False
    except Exception:
        pass
    try:
        return json.loads(raw.replace("\\\\", "\\")), True
    except Exception:
        return None, False


def _write_cache(d):
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def load_atom_dict(offline=False):
    """原子字典（与 `c_atom_meta_new` 同源的数据）。

    **优先走官方接口** `/api/robot/atom-new/all` —— 这样迁移到新环境时完全不需要
    数据库访问。接口失败退本地缓存（随包分发的快照），最后才退 SQL 兜底通道。
    """
    if offline and os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)

    # 1) 官方接口
    try:
        import publish_flow as _pf
        ck = _pf.load_session()
        arr = _pf.api("/api/robot/atom-new/all", {}, ck)
        if isinstance(arr, list) and arr:
            d = {}
            for it in arr:
                k = it.get("atomKey")
                raw = it.get("atomContent")
                if not k or raw is None:
                    continue
                text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
                obj, _ = _parse_atom(text)
                if obj is not None:
                    d[k] = obj
            if d:
                _write_cache(d)
                return d
    except (SystemExit, Exception) as e:
        first = str(e).splitlines()[0][:90] if str(e) else type(e).__name__
        print(f"      · 原子字典接口不可用（{first}），改本地缓存")

    # 2) 本地缓存
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        print(f"      · 原子字典用本地缓存（{len(d)} 个）")
        return d

    # 3) SQL 兜底
    if not cfg.db_conf():
        raise SystemExit(
            "拿不到原子字典：接口不可用、无本地缓存、SQL 通道也没开。\n"
            "  办法：确认设计器已登录（python scripts/doctor.py 看「登录态」）")
    db = ServerDb()
    try:
        rows = db.sql_rows("SELECT atom_key, atom_content FROM c_atom_meta_new")
        d = {}
        for r in rows:
            if len(r) < 2:
                continue
            raw = "\t".join(r[1:])  # 内容若含 tab，sql_rows 会切成多列，这里拼回
            obj, _ = _parse_atom(raw)
            if obj is not None:
                d[r[0]] = obj
        _write_cache(d)
        return d
    finally:
        db.close()


# ----------------------------- 值包装（三型） -----------------------------
def mk_val(kind, v):
    return [{"type": kind, "value": v}]


def resolve_input(raw, var_names, param_names=None):
    """raw 可能是：字面量 / `$变量名`（上游节点输出）/ `$p:参数名`（流程参数）。

    `$p:xxx` 编译成 `{"type":"p_var","value":"xxx"}`。引擎会把它渲染成裸变量名，
    并在 `def main(args)` 开头生成 `xxx = args.get("xxx", 默认值)` —— 所以
    openapi 的 `params` 可以覆盖默认值（2026-10-30 读引擎 flow/flow.py 实证）。

    `$script:xxx` 是**内嵌脚本占位**：原样落成字面量，等 `ai_flow.resolve_scripts()`
    在落库前换成真实 moduleId（见 `SCRIPT_REF_PREFIX` 的注释）。
    """
    if isinstance(raw, list):
        # 片段拼接：[ "$p:img_dir", "/", "$p:prefix", "_1.png" ] → 引擎按 `+` 拼接求值。
        # 引擎 flow/params.py `_param_to_eval`：含 p_var/var 时 need_eval=True，
        # 字面量片段转 repr、变量片段保留裸名，再用 `+` 连起来（2026-10-30 读源码实证）。
        pieces = []
        for piece in raw:
            pieces.extend(resolve_input(piece, var_names, param_names))
        return pieces
    if isinstance(raw, str) and raw.startswith(SCRIPT_REF_PREFIX):
        # 内嵌脚本占位：**不校验、不改写**，原样当字面量落进节点。
        # 注意必须放在 `$p:` / `$` 分支之前——否则会被当成变量引用而报
        # 「变量引用 $script:xxx 未在任何上游节点 output 中定义」。
        return mk_val("other", raw)
    if isinstance(raw, str) and raw.startswith("$p:"):
        name = raw[3:]
        if param_names is not None and name not in param_names:
            raise ValueError(f"参数引用 $p:{name} 未在规格的 params 中声明")
        return mk_val("p_var", name)
    if isinstance(raw, str) and raw.startswith("$"):
        name = raw[1:]
        if name not in var_names:
            raise ValueError(f"变量引用 ${name} 未在任何上游节点 output 中定义")
        return mk_val("var", name)
    return mk_val("other", raw)


# ----------------------------- 固定流程模板 -----------------------------
# 每个节点：atom=原子全名；alias=中文别名；out={schema输出key: 变量名}；
# inputs={schema输入key: 字面量 或 "$变量名"}。
# 模板即「固定的 RPA 流程」：AI 依意图选中模板 → 填参 → 拼节点树。
TEMPLATES = {
    "excel_export": {
        "name": "Excel订单表导出小流程",
        "nl_examples": ["把订单表导成新文件", "做个excel拆分流程", "导出这个表格"],
        # 流程参数（落 c_param）：默认值保证「不传参也能跑」，运行时可由 openapi 覆盖
        "params": [
            {"name": "src_file", "type": "Str", "default": DEMO_XLSX,
             "describe": "源 Excel 文件路径"},
            {"name": "out_dir", "type": "Str", "default": DEMO_DIR,
             "describe": "输出目录"},
            {"name": "out_name", "type": "Str", "default": "导出结果",
             "describe": "输出文件名（不带扩展名）"},
        ],
        "nodes": [
            {
                "atom": "Excel.open_excel", "alias": "打开源表",
                "out": {"open_excel_obj": "src_excel"},
                "inputs": {
                    "file_path": "$p:src_file",
                    "default_application": "Excel",
                    "visible_flag": True,
                    "update_links": False,
                },
            },
            {
                "atom": "Excel.read_excel", "alias": "读取数据",
                "out": {"read_excel_contents": "sheet_data"},
                # 注意 read_range 是枚举 cell/row/column/area/all（不是 "A1:Z1000"）
                "inputs": {
                    "excel": "$src_excel",
                    "sheet_name": "Sheet1",
                    "read_range": "all",
                    "start_col": "", "end_col": "", "cell": "",
                    "row": 1, "column": "", "start_row": 1, "end_row": 1,
                    "read_display": True, "trim_spaces": True, "replace_none": True,
                },
            },
            {
                "atom": "Excel.create_excel", "alias": "新建结果表",
                "out": {"create_excel_obj": "dst_excel", "excel_path": "dst_path"},
                # create_excel 内部会 file_name += ".xlsx"，所以这里**不能带扩展名**
                "inputs": {
                    "file_path": "$p:out_dir",
                    "file_name": "$p:out_name",
                    "default_application": "Excel",
                    "visible_flag": True,
                    "exist_handle_type": "overwrite",
                },
            },
            {
                # 真正把数据落地的节点。没有它，流程「成功」但产出是空表
                # （2026-10-30 用 openpyxl 打开产出文件才发现 A1:A1 空表）。
                # read_excel(read_range=all) 返回「行×列的二维列表」，
                # edit_excel(edit_range=area) 正好吃这个结构，从 A1 开始铺开。
                "atom": "Excel.edit_excel", "alias": "写入数据",
                "out": {},
                "inputs": {
                    "excel": "$dst_excel",
                    "edit_range": "area",
                    "sheet_name": "",       # 空 = 用结果表的第一个 sheet，避免猜名字
                    "start_col": "A",
                    "start_row": "1",
                    "value": "$sheet_data",  # 二维列表，引擎按原值传参
                    "edit_type": "overwrite",
                },
            },
            {
                "atom": "Excel.save_excel", "alias": "保存结果",
                "out": {},
                # save_type=save 时 file_path/file_name 会被忽略（另存为才用）
                "inputs": {
                    "excel": "$dst_excel",
                    "save_type": "save",
                    "file_path": "",
                    "file_name": "",
                    "exist_handle_type": "overwrite",
                    "close_flag": True,
                },
            },
            {
                # 必须显式关源表：save_excel 的 close_flag 只关「结果表」，
                # 源表会一直挂在 Excel 里 → 残留进程 + `~$订单.xlsx` 锁文件，
                # 文件被占用后连改名都做不到（2026-10-30 真机执行后实测踩到）。
                # save_type_one=abort（不保存）：我们只读源表，绝不能反写。
                "atom": "Excel.close_excel", "alias": "关闭源表",
                "out": {},
                "inputs": {
                    "close_range_flag": "one",
                    "excel": "$src_excel",
                    "save_type_one": "abort",
                    "save_type_all": "abort",
                    "file_path": "", "file_name": "",
                    "exist_handle_type": "rename",
                    "pkill_flag": False,
                },
            },
        ],
    },
    # 第二个固定流程：文本文件转存（零元素依赖，证明模板体系可跨领域复用）
    "text_file_copy": {
        "name": "文本文件转存小流程",
        "nl_examples": ["把源文件内容转存成 utf-8", "复制文本文件内容", "转换文件编码"],
        # 参数化：源/目标路径都能在运行时覆盖（openapi params）
        "params": [
            {"name": "src_file", "type": "Str", "default": DEMO_TXT,
             "describe": "源文本文件路径"},
            {"name": "dst_file", "type": "Str", "default": DEMO_DIR + r"\目标文件.txt",
             "describe": "目标文件路径（含文件名）"},
        ],
        "nodes": [
            {
                "atom": "File.file_read", "alias": "读取源文件",
                "out": {"read_file_content": "file_text"},
                "inputs": {
                    "file_path": "$p:src_file",
                    "read_type": "all",
                    "encode_type": "default",
                },
            },
            {
                "atom": "File.file_write", "alias": "写出目标文件",
                "out": {"write_file_path": "out_path"},
                "inputs": {
                    "file_path": "$p:dst_file",
                    "msg": "$file_text",
                    "file_option": "create",
                    "write_type": "overwrite",
                    "encode_type": "utf-8",
                },
            },
        ],
    },
    # 第三个固定流程：网页元素点击（含元素槽位，元素由生成器从 c_element 自动取真实引用）
    "web_click_element": {
        "name": "网页元素点击小流程",
        "nl_examples": ["打开京东点一下商品", "在网页上点某个元素", "web 点击流程"],
        "params": [
            {"name": "url", "type": "Str", "default": "https://www.jd.com/",
             "describe": "要打开的网页地址"},
        ],
        "nodes": [
            {
                "atom": "BrowserSoftware.browser_open", "alias": "打开浏览器",
                "out": {"web_open": "web"},
                "inputs": {
                    "url": "$p:url",
                    "browser_type": "chrome",
                    "timeout": 20,
                    "timeout_handle_type": "execError",
                    "wait_load_success": True,
                    "open_with_incognito": False,
                },
            },
            {
                "atom": "BrowserElement.click", "alias": "点击元素",
                "out": {},
                "inputs": {
                    "browser_obj": "$web",
                    "element_timeout": 10,
                    "button_type": "click",
                    "simulate_flag": False,
                    "scroll_into_center": True,
                },
                # 元素槽位：web 元素由生成器从 c_element 取（目标 robot 名下）
                "elements": {"element_data": "web"},
            },
            {
                "atom": "BrowserSoftware.browser_close", "alias": "关闭浏览器",
                "out": {},
                "inputs": {"browser_obj": "$web"},
            },
        ],
    },
    # 扩展点：新增固定流程只需在此加一个模板，生成器会自动按真实 schema 校验。
}


_PATH_RX = re.compile(r"[A-Za-z]:[\\/][^\s\"'“”‘’，。；、]*")
_NAMED_RX = re.compile(r"[\w\u4e00-\u9fa5\-_.()（）]{2,80}\.(?:pdf|docx?|xlsx?|csv|txt)\b", re.I)
_EXT_WORD = ((".pdf", " pdf "), (".docx", " docx "), (".doc", " doc "),
             (".xlsx", " excel "), (".xls", " excel "), (".csv", " csv "),
             (".txt", " txt "))


def _strip_paths(s):
    """把路径/文件名从话里抹掉，只留扩展名当「文件类型」线索。

    为什么必须抹：`把 F:\\…\\星辰开源\\demo\\20260914报废_已转正.pdf 的第1到2页存到 …`
    这句话里路径贡献了十几个 token，把「第1到2页」这个真正的意图淹掉了，
    结果「抽取指定页」和「PDF合并」打平 0.50 → 被判「拿不准」。
    抹掉后扩展名还留着（`.pdf` → `pdf`），所以「PDF vs Word」这类区分不受影响。
    """
    def rep(m):
        p = m.group(0).lower()
        for ext, word in _EXT_WORD:
            if p.endswith(ext):
                return word
        return " "

    s = _PATH_RX.sub(rep, s or "")
    return _NAMED_RX.sub(rep, s)


# 页码话术：`第1到2页` / `前3页` / `1-2页`
_PAGE_HINT = re.compile(r"第?\s*\d+\s*页?\s*[-~～至到]\s*\d+\s*页|前\s*\d+\s*页|第\s*\d+\s*页")

# 批量话术。**故意不含「文件夹」「整个文件夹」**——「把这个文件夹里的PDF合并成一个」
# 也是文件夹，但它是单次操作；只有「都/全部/每个/批量」才是真的要对每个文件做一遍。
_BATCH_HINT = re.compile(r"都|全部|所有|批量|每个|逐个|遍历|每一个")


def _spec_has_loop(spec):
    """规格里有没有循环块（决定它能不能真的处理「一批」文件）。"""
    for n in spec.get("nodes") or []:
        atom = (n.get("atom") or "").lower()
        if any(k in atom for k in ("for", "loop", "while", "foreach", "iterate")):
            return True
    return False


def _nl_tokens(s):
    """把一句中文/英文混排的话切成可比较的 token。

    - ASCII 词：`word` / `pdf` / `excel`（小写）
    - 中文：取 2 字滑窗（"表格提取" → 表格/格提/提取），单字串保留单字

    不用分词库（迁移到新环境少一个依赖），2-gram 对这种短指令足够。
    """
    s = (s or "").lower()
    toks = set(re.findall(r"[a-z0-9]+", s))
    for run in re.findall(r"[\u4e00-\u9fa5]+", s):
        if len(run) == 1:
            toks.add(run)
        for i in range(len(run) - 1):
            toks.add(run[i:i + 2])
    return toks


def _norm_nl(s):
    """归一化：小写 + 去掉空格与标点，只留可比较的字符。"""
    return re.sub(r"[\s\-_，。、,.;；:：!！?？\"'（）()\[\]【】/\\]+", "", (s or "").lower())


def _kw_score(nl_norm, keywords):
    """关键词命中分：命中最长的那个关键词，2 字=0.67、3 字及以上=1.0。

    中文短指令用 token 覆盖率算很不稳（「只保留第1到2页」和「合并PDF」会打平），
    所以规格里显式写 `keywords`，做**子串命中**——可预测、可解释、不依赖分词库。
    """
    best = 0.0
    for kw in keywords or []:
        k = _norm_nl(kw)
        if k and k in nl_norm:
            best = max(best, min(1.0, len(k) / 3.0))
    return best


def match_spec_file(nl, spec_dir=None, min_score=0.5):
    """在 `flows/*.json` 里按 `nl_examples` + `keywords` 找最匹配的规格。

    返回 `(path, score, 命中说明)`；没把握时返回 `(None, 0, 原因)`。

    为什么需要它：`intent_match()` 只有 3 个内置模板，`flows/` 里的固定流程
    （Word转PDF / PDF合并 / PDF表格转Excel …）用自然语言根本命中不了，
    只能靠人记 `--spec` 路径。这个函数把「已有流程」也变成可被自然语言命中的资产。

    判据：`score = max(示例token覆盖率, 关键词命中分)`，取最高；
    并要求**领先第二名 0.12 以上**，否则认为拿不准（宁可回退模板也不猜错流程）。

    两个细节（2026-10-30 加）：
    - **先抹掉路径再算分**（`_strip_paths`）：否则长路径的 token 会淹没真正的意图词；
    - **话里带页码时给「有 page_range 参数」的规格加分**：这是参数层面的硬证据，
      比词面更像「用户就是要抽页」。
    """
    spec_dir = spec_dir or os.path.join(HERE, "flows")
    if not os.path.isdir(spec_dir):
        return None, 0.0, f"规格目录不存在：{spec_dir}"
    nl_clean = _strip_paths(nl)
    nl_tok = _nl_tokens(nl_clean)
    nl_norm = _norm_nl(nl_clean)
    has_page = bool(_PAGE_HINT.search(nl or ""))
    has_batch = bool(_BATCH_HINT.search(nl or ""))
    if not nl_tok:
        return None, 0.0, "自然语言为空"

    scored = []
    for name in sorted(os.listdir(spec_dir)):
        if not name.endswith(".json") or name.startswith("_"):
            continue
        path = os.path.join(spec_dir, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                spec = json.load(f)
        except Exception:
            continue
        examples = list(spec.get("nl_examples") or []) + [spec.get("name") or ""]
        best, best_ex = 0.0, ""
        for ex in examples:
            ex_tok = _nl_tokens(ex)
            if not ex_tok:
                continue
            score = len(nl_tok & ex_tok) / len(ex_tok)
            if score > best:
                best, best_ex = score, ex
        kw = _kw_score(nl_norm, spec.get("keywords"))
        if kw > best:
            best, best_ex = kw, "关键词"
        if has_page and any((p.get("name") or "") == "page_range" for p in spec.get("params") or []):
            best = min(1.0, best + 0.25)
            best_ex = best_ex or "页码话术"
        if has_batch:
            # 用户说了「都/全部/每个」：只有带循环的流程才真能做到。
            # 单文件流程不是「差一点」，是**做不到**，所以降权而不是放着不管。
            if _spec_has_loop(spec):
                best = min(1.0, best + 0.2)
                best_ex = best_ex or "批量话术"
            else:
                best = round(best * 0.6, 4)
        if best > 0:
            scored.append((best, path, best_ex))

    if not scored:
        return None, 0.0, "没有任何规格的例子/关键词能匹配"
    scored.sort(reverse=True)
    top = scored[0]
    second = scored[1][0] if len(scored) > 1 else 0.0
    if top[0] < min_score:
        return None, top[0], f"最高分 {top[0]:.2f} 低于阈值 {min_score}（最像的是 {os.path.basename(top[1])}）"
    if top[0] - second < 0.12:
        return None, top[0], (f"前两名太接近：{os.path.basename(top[1])} {top[0]:.2f} vs "
                             f"{os.path.basename(scored[1][1])} {second:.2f}")
    how = "命中关键词" if top[2] == "关键词" else f"命中示例「{top[2]}」"
    return top[1], top[0], how


def spec_catalog(spec_dir=None):
    """列出所有可用规格：`[(文件名, 流程名, 一句示例)]`。给「没听懂」时提示用户用。"""
    spec_dir = spec_dir or os.path.join(HERE, "flows")
    out = []
    if not os.path.isdir(spec_dir):
        return out
    for name in sorted(os.listdir(spec_dir)):
        if not name.endswith(".json") or name.startswith("_"):
            continue
        try:
            with open(os.path.join(spec_dir, name), "r", encoding="utf-8") as f:
                spec = json.load(f)
        except Exception:
            continue
        exs = spec.get("nl_examples") or []
        out.append((name, spec.get("name") or name, exs[0] if exs else ""))
    return out


def intent_match(nl):
    """极简意图匹配：关键词 → 模板。可后续接 LLM（dispatch_poc 已有 LLM 解析骨架）。

    注意优先级：Excel 关键词先判，否则「导出成新文件」会被「文件」抢走。

    **匹配不上返回空串**（2026-10-30 改）：原来兜底返回 `excel_export`，
    结果「帮我把这个 PDF 拆成两半」会静默跑成「Excel 订单导出」——错得还很像成功。
    宁可明确说「没听懂，候选是这几条」，也不猜。
    """
    nl = (nl or "").lower()
    if any(w in nl for w in ["excel", "表格", "订单", "导出", "导成", "拆分", "工作表", "sheet"]):
        return "excel_export"
    if any(w in nl for w in ["网页", "浏览器", "点击", "web", "点一下", "打开网站"]):
        return "web_click_element"
    if any(w in nl for w in ["文件", "转存", "编码", "txt", "文本"]):
        return "text_file_copy"
    return ""


_WIN_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


def norm_win_path(v):
    """把「Windows 盘符路径」里的斜杠统一成正斜杠安全形态 `\\`。

    背景：规格 JSON 是手写/AI 生成的高频出错点——写成 `F://a//b\\c` 这种混合形态后，
    参数落库、`$p:` 拼接、Python 子流程全都拿到坏路径（坑 64）。这里统一收口：
    只处理 `X:` 开头的绝对路径，URL（http://…）与 UNC（\\\\server\\share）原样放过。
    """
    if not isinstance(v, str) or not _WIN_DRIVE.match(v):
        return v
    return re.sub(r"[\\/]+", lambda m: "\\", v)


def resolve_path_tokens(v):
    """把 `${PROJECT_ROOT}` 换成本仓库根（绝对路径）。非 str 原样返回。

    调用点只有「参数默认值」——见 `PROJECT_ROOT_TOKEN` 的说明：`nodes[].inputs`
    里 `$` 开头表示变量引用，替换会把占位符误当变量名。
    """
    if not isinstance(v, str) or PROJECT_ROOT_TOKEN not in v:
        return v
    return v.replace(PROJECT_ROOT_TOKEN, HERE)


def _normalize_scripts(spec):
    """规范化规格顶层的可选字段 `scripts`（内嵌 Python 子流程源码）。

    结构：`{"脚本名": {"name": 模块显示名, "code": "def main(args): ..."}}`

    ## 为什么要这个字段

    原子只能做「一个动作」，做不了「跨迭代累计状态」和「按数据内容决定输出个数」。
    以前这类需求没出路（AI 只能硬拼 11~17 个原子然后失败）。现在 AI 可以直接在规格里
    写一段 Python，由 `ai_flow.resolve_scripts()` 现场建成平台模块 —— 全程不需要人
    手工上传 py 文件。

    ## 但脚本是**兜底**，不是默认（2026-11-02 用户明确的产品约束）

    产品的验收标准是「**非专业业务人员能看懂、能做微调**」，所以价值排序是
    原子拼装（高可读/高可调）> 脚本（低可读/低可调）> 新增原子（脚本的升级形态）。
    于是每条脚本都必须自带两个**可维护性证据**字段，缺一不可（`spec_lint` 会报错）：

      - `why`           ：为什么原子做不到、命中判定 B-几（防「模型偷懒选脚本」的硬约束）
      - `maintain_hint`：业务人员能改什么（改哪个参数）、什么情况必须找开发改代码

    可选 `atom_suggestion`：建议把它沉淀成什么原子（`ai_flow` 会记进
    `docs/原子需求池.md`，出现 ≥3 次就该做成真原子让业务人员直接拖）。

    ## 这里的边界（很重要）

    只做**结构规范化**，不校验语法、不碰代码内容：
    - 语法/契约/import 白名单由 `spec_lint` 静态查（报错带行号）
    - 真正建模块由 `ai_flow.resolve_scripts()` 做
    - **代码字符串绝不走 `norm_win_path`**：那会把脚本里的路径反斜杠也改写，
      手写代码必须原样透传。

    未声明 `scripts` 时**不往 spec 里塞空字典**（避免给老规格的落盘文件添噪音）。
    """
    sc = spec.get("scripts")
    if sc in (None, "", {}, []):
        return spec
    if not isinstance(sc, dict):
        raise ValueError(
            '规格的 scripts 字段必须是对象：{"脚本名": {"code": "def main(args): ..."}}')
    out = {}
    for name, item in sc.items():
        nm = str(name).strip()
        if not nm:
            raise ValueError("scripts 里有空脚本名")
        if isinstance(item, str):
            # 允许简写成 `"脚本名": "def main(args): ..."`（AI 常这么写，顺手兼容）
            item = {"code": item}
        if not isinstance(item, dict):
            raise ValueError(f"scripts.{nm} 必须是对象或代码字符串")
        code = item.get("code")
        if code is None:
            raise ValueError(f"scripts.{nm} 缺少 code 字段（内嵌脚本必须给完整可运行源码）")
        one = {"name": item.get("name") or nm, "code": str(code)}
        # 可维护性证据字段：原样保留（缺失检查交给 spec_lint，这里不替它判死）
        for k in ("why", "maintain_hint", "atom_suggestion"):
            if item.get(k):
                one[k] = str(item[k])
        # 参数声明（决定设计器参数表格里有哪几行、中文说明写什么）。
        # 不写也能跑 —— 会退化成从节点 module_param 推，但没有中文说明。
        if item.get("params"):
            # 脚本参数默认值同样要解析占位符**并**统一斜杠：占位符替换进来的是
            # `F:\仓库根` + `/demo/x.xlsx` 这种混合形态（`HERE` 用反斜杠、规格里用正斜杠），
            # 直接落库会让设计器参数表格显示成 `F:\a\b/demo/x.xlsx`。
            one["params"] = [
                {"name": d.get("name"), "type": d.get("type") or "Str",
                 "default": norm_win_path(resolve_path_tokens(d.get("default"))),
                 "describe": d.get("describe") or ""}
                for d in item["params"] if isinstance(d, dict) and d.get("name")]
        out[nm] = one
    spec["scripts"] = out
    return spec


def load_spec(path):
    """加载「流程规格」JSON —— 这是 AI/LLM 的产出接口，也是模板的通用化形式。

    格式（与 TEMPLATES 的单模板同构，最小可用）：
      {
        "name": "流程名",
        "nl_examples": ["..."],
        "nodes": [
          {"atom": "Excel.open_excel", "alias": "打开源表",
           "inputs": {"file_path": "C:\\\\订单.xlsx", "excel": "$src_excel"},
           "out": {"open_excel_obj": "src_excel"}}
        ]
      }
    规则：`inputs` 里以 $ 开头表示引用上游输出变量；`out` 把 schema 输出 key 映射成变量名。

    可选字段（都不做「未知字段」拦截，多出来的键原样留在 spec 里）：
      - `params`：流程参数
      - `element_requirements` / `elements`：元素声明与槽位
      - `scripts`：内嵌 Python 子流程源码（见 `_normalize_scripts`）
    """
    with open(path, "r", encoding="utf-8") as f:
        spec = json.load(f)
    if not isinstance(spec, dict) or not isinstance(spec.get("nodes"), list) or not spec["nodes"]:
        raise ValueError("规格文件格式不对：需要 {name, nodes:[{atom, inputs, out}]}")
    for i, n in enumerate(spec["nodes"]):
        if not isinstance(n, dict) or not n.get("atom"):
            raise ValueError(f"规格第 {i} 个节点缺 atom 字段")
        n.setdefault("alias", n["atom"])
        n.setdefault("inputs", {})
        n.setdefault("out", {})
        # 节点输入里的绝对路径同样收口（坑 64）
        for k, v in list(n["inputs"].items()):
            if isinstance(v, str):
                n["inputs"][k] = norm_win_path(v)
            elif isinstance(v, list):
                n["inputs"][k] = [norm_win_path(x) if isinstance(x, str) else x for x in v]
    spec.setdefault("name", os.path.basename(path))
    spec.setdefault("nl_examples", [])
    spec.setdefault("params", [])
    for i, p in enumerate(spec["params"]):
        if not isinstance(p, dict) or not p.get("name"):
            raise ValueError(f"params 第 {i} 项缺 name 字段")
        p.setdefault("type", "Str")
        p.setdefault("default", "")
        p.setdefault("describe", "")
        p["default"] = norm_win_path(resolve_path_tokens(p["default"]))
    _normalize_scripts(spec)
    return spec


# ----------------------------- 构建节点树 -----------------------------
def _coerce_literals(value_list, p):
    """把 Bool/Int/Float 的「字面量」改成 python 类型（坑 69）。

    背景：引擎 `params.py::_param_to_eval` 在 `need_eval=False` 时用 `f"{data}"`
    拼字符串，布尔/数字因此变成 **字符串**（`False` → `"False"`），再被 repr 成
    `'False'` —— 传进原子就是「非空字符串」= 真值：
      - `visible_flag=False` → Word 弹出一个可见的空白文档（用户看到「空白 Word」）
      - `bold_flag=False` → 整段加粗
      - `underline_flag=0` → 整段下划线
    改成 `{"type":"python","value":"False"}` 后 `need_eval=True`，编译出来是裸
    `False`，原子拿到的才是真 bool。
    """
    t = (p.get("types") or "").strip()
    # 只放过「本来就是字符串」的参数；Bool / Int / Float / 数值型枚举（如 UnderLineStyle 的 0/1）
    # 都要转成 python 类型，否则会被 f-string 变成字符串（"0" 也是真值 → 整段加下划线）。
    if t in ("", "Str", "Any"):
        return value_list
    out = []
    for v in value_list:
        if isinstance(v, dict) and v.get("type") == "other" and isinstance(v.get("value"), (bool, int, float)):
            out.append({"type": "python", "value": repr(v["value"])})
        else:
            out.append(v)
    return out


def _json_refs(obj, var_names, param_names=None):
    """把 `$json:` 里的 `"$var:x"` / `"$p:x"` 换成引擎能求值的 special 结构。

    背景（2026-10-30）：`Script.module` 的 `module_param` 只能整块以 JSON 字符串传
    （`need_parse="json_str"`），里面每个 `varValue` 原本只能是**字面量**。于是
    「循环里把当前文件传给子流程」做不到——批量处理卡在这里。

    引擎侧其实支持：`{"rpa":"special","value":[{"type":"var","value":"item"}]}` 会被
    `ComplexParamParser` 编译成表达式 `item`，在运行时从 `main` 帧的局部变量求值
    （读 `astronverse/workflowlib/params.py::_get_auto_context` 实证）。所以这里
    只做一层语法糖转换，让规格里能直接写 `"$var:item"`。
    """
    if isinstance(obj, dict):
        return {k: _json_refs(v, var_names, param_names) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_refs(v, var_names, param_names) for v in obj]
    if isinstance(obj, str):
        if obj.startswith("$var:"):
            name = obj[5:]
            if name not in var_names:
                raise ValueError(f"module_param 里的 $var:{name} 未在任何上游节点 output 中定义")
            return {"rpa": "special", "value": [{"type": "var", "value": name}]}
        if obj.startswith("$p:"):
            name = obj[3:]
            if param_names is not None and name not in param_names:
                raise ValueError(f"module_param 里的 $p:{name} 未在规格的 params 中声明")
            return {"rpa": "special", "value": [{"type": "p_var", "value": name}]}
    return obj


def _contains_var_ref(obj):
    """递归检查 payload 里是否含运行时才知道的引用（`$var:` 循环变量，rpa 形式
    `{"type":"var",...}`）。"""
    if isinstance(obj, dict):
        if obj.get("rpa") == "special":
            for part in (obj.get("value") or []):
                if isinstance(part, dict) and part.get("type") == "var":
                    return True
        return any(_contains_var_ref(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_contains_var_ref(v) for v in obj)
    if isinstance(obj, str):
        return obj.startswith("$var:")
    return False


def _contains_param_ref(obj):
    """递归检查 payload 里是否含 `$p:` 流程参数引用。

    与 `_contains_var_ref`（只看循环变量）分开：两者的处理时机不同 ——
    循环变量运行时才存在，流程参数在参数面板里随时可改，但**都不能烘焙**。
    """
    if isinstance(obj, dict):
        return any(_contains_param_ref(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_contains_param_ref(v) for v in obj)
    if isinstance(obj, str):
        return obj.startswith("$p:")
    return False


def _collect_dynamics_refs(input_list):
    """收集「被其它参数的 dynamics 显示条件引用」的参数名。

    原子 schema 里常见这种定义（例：BrowserElement.input）：
        fill_input.dynamics = [{"key": "$this.fill_input.show",
                                "expression": "return $this.fill_type.value == 'text'"}]
    设计器前端（selfExecuting.ts）求值时 `$this.<key>.value` 取的是 value 字段【原样】：
        value = [{"type":"other","value":"text"}]  → 数组 != 'text' → show=False ❌
        value = "text"                             → 成立         → show=True  ✅
    show=False 的参数会被引擎 params.py:parse_input 直接跳过（隐藏字段的运行时语义），
    且设计器**每次保存都会重算 dynamics 并回写 show** —— 所以这类参数必须落库为
    裸标量，否则用户在设计器里保存一次，被引用参数的 show 就被算成 False，其
    「下游显示参数」（fill_input / js_run 的 content 等）运行时直接消失。
    2026-11-04：js_run 的 input_type 是第一例（「脚本数据为空」），fill_type 是第二例
    （设计器面板「输入内容」整行消失），此处统一泛化处理。
    """
    refs = set()
    for p in input_list:
        for dyn in (p.get("dynamics") or []):
            expr = dyn.get("expression") or ""
            refs.update(re.findall(r"\$this\.([A-Za-z_]\w*)\.value", expr))
    return refs


def _bake_refs(obj, param_defaults):
    """把 `$p:x` 引用 / 内联 `rpa:special(p_var)` 结构解析成**参数默认值字面量**。

    为什么烘焙而不是留引用（2026-11-03 定稿）：`Script.module` 的参数现在写进节点的
    `module_param[].varValue`，业务人员改参数的正道是**在节点参数表格里直接改值**
    （已实测生效）。留 `p_var` 引用的话界面显示的是变量名，改值要绕到流程参数面板去，
    对非专业用户反而更难懂。烘焙成默认值后节点上是「可读可改的真实值」。

    含真正的循环变量（`$var:` / `{"type":"var"}`）无法烘焙 —— 那个值只有运行时才知道，
    由 `_json_refs` 保留引用（引擎会求值，见 `build_module_param_items`）。

    兼容两种规格写法：
      - 规范写法：`"varValue": "$p:src_pdf"`（字符串引用）
      - 历史内联写法：`"varValue": {"rpa":"special","value":[{"type":"p_var",
        "value":"src_pdf"}]}`
    多段拼接（如 `out_dir` + `"\\"` + `out_name` + `".docx"`）会被拼接成字符串。
    """
    if isinstance(obj, dict):
        if obj.get("rpa") == "special":
            parts = obj.get("value") or []
            resolved = []
            for part in parts:
                if not isinstance(part, dict):
                    continue
                t = part.get("type")
                if t == "p_var":
                    name = part.get("value")
                    if name not in param_defaults:
                        raise ValueError(f"module_param 里的 $p:{name} 未在规格的 params 中声明")
                    resolved.append(param_defaults[name])
                elif t == "other":
                    resolved.append(part.get("value"))
                elif t == "var":
                    raise ValueError("module_param 含循环变量 $var:，无法烘焙（应保持 rpa:special）")
                else:
                    resolved.append(part.get("value"))
            if len(resolved) == 1:
                return resolved[0]
            return "".join("" if v is None else str(v) for v in resolved)
        return {k: _bake_refs(v, param_defaults) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_bake_refs(v, param_defaults) for v in obj]
    if isinstance(obj, str):
        if obj.startswith("$p:"):
            name = obj[3:]
            if name not in param_defaults:
                raise ValueError(f"module_param 里的 $p:{name} 未在规格的 params 中声明")
            return param_defaults[name]
        return obj
    return obj


def _as_param_scalar(v):
    """参数值只能放标量；list/dict 这种复杂值转成 JSON 字符串（脚本侧自己 json.loads）。"""
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return json.dumps(v, ensure_ascii=False)


def build_module_param_items(payload, var_names=None, param_names=None,
                             param_defaults=None, id_map=None):
    """把规格里的 `module_param` 声明构造成**设计器认的真数组**（见文件头结构说明）。

    输入（规格写法，随便哪种都行）：
        [{"varName": "src_file", "varValue": "$p:src_file"},
         {"varName": "sheet_name", "varValue": "0"},
         {"varName": "current_file", "varValue": "$var:item"}]
    输出（落库形态）：
        [{"varId": "<c_param.id>", "varName": "src_file",
          "varValue": {"rpa": "special", "value": [{"type": "other", "value": "F:\\…"}]}},
         …]

    两条分支：
      - 含循环变量（`$var:` / `{"type":"var"}`）→ 保留 `rpa:special` 引用让引擎运行时求值；
      - 其余 → `$p:` 烘焙成参数默认值，包成 `{"type":"other"}` 字面量。

    为什么要外面再套一层 `{"rpa":"special","value":[{"type":"other",…}]}`
    而不是直接写 `"F:\\x.xlsx"`：设计器存的样本就是这个形状（`导入MES` 流程实证），
    引擎的 `ComplexParamParser` 也是按 `rpa:special` 识别的。少了这层，脚本拿到的是
    dict 而不是字符串，`args.get("x").strip()` 直接 AttributeError。
    """
    var_names = var_names or set()
    param_defaults = param_defaults or {}
    id_map = id_map or {}
    if isinstance(payload, dict):                 # 容忍单参数写成对象
        payload = [payload]
    if not isinstance(payload, list):
        raise ValueError("Script.module 的 module_param 必须是数组"
                         "（形如 [{\"varName\":…,\"varValue\":…}]），收到 %r" % type(payload).__name__)

    items = []
    for it in payload:
        if not isinstance(it, dict):
            raise ValueError("module_param 的每一项必须是对象，收到 %r" % (it,))
        name = it.get("varName")
        if not name:
            raise ValueError("module_param 有项目缺 varName：%r" % (it,))
        raw = it.get("varValue")
        # 含循环变量 **或** 流程参数引用，都必须保留 rpa:special 让引擎运行时求值。
        #
        # 流程参数为什么不能烘焙（2026-11-04 实测 main.py 定案）：
        # 引擎把每个流程参数生成为 main() 的**局部变量**（`sfsc = args.get("sfsc", '0')`），
        # 而 p_var 引用会被 ComplexParamParser 编译成裸表达式 `sfsc`，在 main 帧求值，
        # 正好取到参数面板的当前值。烘焙成字面量后，改参数就完全没反应
        # —— 这是「sfsc 改成 0 不生效」的根因（模块和 JS 都只拿到落库时的 1）。
        if _contains_var_ref(raw) or _contains_param_ref(raw):
            # 循环变量 / 流程参数：必须运行时求值，保留引用（设计器可能只显示变量名，引擎可跑）
            val = _json_refs(raw, var_names, param_names)
            if not (isinstance(val, dict) and val.get("rpa") == "special"):
                val = {"rpa": "special",
                       "value": [{"type": "other", "value": _as_param_scalar(val)}]}
        else:
            baked = _bake_refs(raw, param_defaults)
            val = {"rpa": "special",
                   "value": [{"type": "other", "value": _as_param_scalar(baked)}]}
        items.append({"varId": str(id_map.get(name) or ""),
                      "varName": name, "varValue": val})
    return items


def build_process_content(template, atom_dict, elements=None, robot_id=DEFAULT_ROBOT_ID,
                          strict=True, module_param_ids=None):
    """把模板/规格构建成落库用的扁平节点数组。

    elements: 传入 c_element 元素库（load_elements 结果）时，节点里声明的「元素槽位」
              （spec["elements"] = {参数key: web|uia|cv}）会自动填真实元素引用；
              否则元素槽位会报错（因为占位元素无法运行）。
    strict:   True = 只用目标 robot 名下的元素（推荐，跨 robot 引用运行时可能解析不到）。
    module_param_ids: `Script.module` 节点的 `varId` 来源，
              `{"<moduleId 或 $script:名字>": {"参数名": "<c_param.id>"}}`。
              由 `ai_flow.resolve_scripts()` 建完模块/参数后传进来；离线构建（lint/draft）
              传 None，varId 写空串 —— 结构合法，只是设计器参数表格认不到，需 relink 补齐。

    规格可选 `params` 段（流程参数，落 c_param）：
        "params": [{"name":"src_file","type":"Str","default":"D:\\x.xlsx","describe":"源文件"}]
    节点里用 `$p:src_file` 引用 → 普通原子走 `{"type":"p_var","value":"src_file"}`；
    `Script.module` 的 `module_param` 里则**烘焙成默认值字面量**（见 `_bake_refs`）。
    """
    param_names = {p.get("name") for p in (template.get("params") or []) if p.get("name")}
    param_defaults = {p.get("name"): p.get("default") for p in (template.get("params") or []) if p.get("name")}
    # 脚本参数的默认值**优先于**流程参数的默认值（2026-11-03 踩到）：
    # 同一个参数可能在流程级声明成 `type=Bool, default=false`（给原子用没问题），
    # 但脚本侧契约是「args 里全是字符串」，`scripts.<名>.params` 里写的是 `"false"`。
    # `$p:overwrite` 烘焙时若取流程参数的 `false`，落库就成了 JSON 布尔 → 引擎求值会
    # `NameError: name 'false' is not defined`。所以脚本声明的默认值优先。
    for _sname, _item in (template.get("scripts") or {}).items():
        if not isinstance(_item, dict):
            continue
        for _d in (_item.get("params") or []):
            if isinstance(_d, dict) and _d.get("name") and _d.get("default") is not None:
                param_defaults[_d["name"]] = _d["default"]
    module_param_ids = module_param_ids or {}
    var_names = {}          # schema 输出 key 的变量名 → 用于 $ 解析（这里用变量名本身）
    var_defined = set()     # 所有已定义的变量名
    nodes = []
    ts = int(time.time() * 1000)

    samples = {}
    if elements:
        samples = pick_samples(elements, robot_id=robot_id, strict=strict)
        if not strict and not samples:
            samples = pick_samples(elements, robot_id=robot_id, strict=False)

    for i, spec in enumerate(template["nodes"]):
        atom_key = spec["atom"]
        if atom_key not in atom_dict:
            raise KeyError(f"第 {i + 1} 个节点的原子 {atom_key} 不在 c_atom_meta_new 中（可能版本/名称变了）")
        atom = atom_dict[atom_key]
        schema_ins = {p.get("key"): p for p in (atom.get("inputList") or []) if isinstance(p, dict)}
        schema_outs = [p.get("key") for p in (atom.get("outputList") or []) if isinstance(p, dict)]
        # 被 dynamics 显示条件引用的参数（如 fill_type / input_type）必须落裸标量，
        # 否则设计器求值 `$this.<key>.value == 'xxx'` 拿到数组 → 下游参数被隐藏/丢弃。
        dyn_ref_names = _collect_dynamics_refs(atom.get("inputList") or [])

        node_id = f"bh{ts}{i}"

        # 输入：元素槽位 > 模板值 > schema 默认值
        input_list = []
        warnings = []
        elem_slots = spec.get("elements") or {}
        for pkey, p in schema_ins.items():
            if pkey in elem_slots:
                slot = elem_slots[pkey]
                sample = pick_element_by_slot(slot, elements, robot_id, samples=samples)
                if not sample:
                    kind = slot if isinstance(slot, str) else (slot.get("kind") or "web")
                    name = "" if isinstance(slot, str) else (slot.get("name") or "")
                    raise KeyError(
                        f"第 {i + 1} 个节点 {atom_key}.{pkey} 需要{KIND_CN.get(kind, kind)}"
                        + (f"「{name}」" if name else "")
                        + f"，但目标 robot {robot_id} 的元素库里没有（用 element_pick.py 拾取）")
                input_list.append({"key": pkey, "value": [{
                    "type": "element", "value": sample["element_name"], "data": sample["element_id"]}]})
            elif pkey in spec["inputs"]:
                raw_in = spec["inputs"][pkey]

                # ---- Script.module 两个参数按平台标准结构单独处理（见文件头） ----
                if atom_key == SCRIPT_ATOM_KEY and pkey == "content":
                    # content 是**纯字符串**（moduleId 或 `$script:` 占位符），不能包成数组。
                    v = raw_in
                    if isinstance(v, str) and v.startswith("$p:"):
                        nm = v[3:]
                        if nm not in param_defaults:
                            raise ValueError(f"Script.module.content 引用的 $p:{nm} 未在规格 params 中声明")
                        dv = param_defaults.get(nm)
                        v = "" if dv is None else str(dv)
                    elif isinstance(v, list):
                        # 兼容老规格把 content 写成 [{type,value}]
                        v = (v[0] or {}).get("value") if v and isinstance(v[0], dict) else ""
                    input_list.append({"key": "content", "value": v if isinstance(v, str) else str(v)})
                    continue
                if atom_key == SCRIPT_ATOM_KEY and pkey == "module_param":
                    if isinstance(raw_in, str) and raw_in.startswith("$json:"):
                        try:
                            payload = json.loads(raw_in[6:])
                        except Exception as e:
                            raise ValueError(f"Script.module.module_param 的 $json: 不是合法 JSON：{e}")
                    else:
                        payload = raw_in
                    mid = (spec["inputs"].get("content") or "")
                    ids = module_param_ids.get(str(mid)) or module_param_ids.get("$script:" + str(mid)) or {}
                    items = build_module_param_items(payload, var_defined, param_names,
                                                     param_defaults, ids)
                    # 真数组，且**不带 need_parse**：need_parse 是给「整块 JSON 字符串」用的，
                    # 这里已经是结构化数组，加了反而会让引擎把每项再当源码求值一次。
                    input_list.append({"key": "module_param", "value": items})
                    continue

                # ---- 被 dynamics 显示条件引用的参数：落库为【裸标量】（泛化规则）。
                #
                # 依据（astron-rpa 前端源码 frontend/packages/web-app/src/views/
                # Arrange/utils/selfExecuting.ts）：
                #   const $this = $formMap            // formMap[key] = 整个 item 对象
                #   $this.<key>.value                 // 取 item.value 的【原样】
                #   expression: "$this.input_type.value == 'content'"
                # 设计器把 value 字段直接拿来比较：
                #   value = [{"type":"str","value":"content"}]  → 数组 == 'content' → false ❌
                #   value = "content"                            → 成立            → true  ✅
                # show=False 时引擎 params.py:parse_input 里 `if not i.get("show", True): continue`
                # 会直接跳过下游参数（content / fill_input），运行时收到空值。
                # 实例：js_run input_type 数组形态 →「脚本数据为空」；
                #       BrowserElement.input fill_type 数组形态 → 设计器「输入内容」整行消失。
                #
                # 关键点：设计器每次**保存**都会重算 dynamics 并回写 show。所以这类参数
                # 只要不是裸标量，用户在设计器里改任何参数保存一次就坏。
                #
                # 引擎侧裸标量与数组解析结果完全一致（已实测 parse_param），对运行零影响。
                # 仅处理纯字面量；$p:/$json: 等引用形态走后面的正常路径（它们本来就不该
                # 被 dynamics 引用，若命中会另行告警）。
                if pkey in dyn_ref_names:
                    if isinstance(raw_in, str) and raw_in.startswith(("$", "[")):
                        warnings.append(f"{atom_key}.{pkey} 被 dynamics 引用但值为引用形态"
                                        f"（{raw_in[:30]}），设计器显示可能异常，建议改为字面量")
                    else:
                        input_list.append({"key": pkey, "value": raw_in})
                        continue

                if isinstance(raw_in, str) and raw_in.startswith("$json:"):
                    # 其余原子的复杂参数：保持「JSON 字符串 + need_parse」的老形态。
                    try:
                        payload = json.loads(raw_in[6:])
                    except Exception as e:
                        raise ValueError(f"{atom_key}.{pkey} 的 $json: 不是合法 JSON：{e}")
                    # 含循环变量：必须运行时求值 → 保留 rpa:special 引用 + need_parse。
                    # BrowserScript.params 含**流程参数**引用（$p:xxx）时同样不能烘焙：
                    # ComplexParamParser._recursive_convert_params 会把 rpa:special(p_var)
                    # 编译成表达式 `gv["xxx"]`，运行时在 main 帧求值（见 workflowlib/
                    # params.py:_get_auto_context）。烘焙成默认值的话，用户在流程参数面板
                    # 把 sfsc 改成 0，JS 里拿到的仍是落库时的 1 —— 「改了不生效」的根因之二。
                    keep_ref = _contains_var_ref(payload) or (
                        atom_key.startswith("BrowserScript.") and pkey == "params"
                        and _contains_param_ref(payload))
                    if keep_ref:
                        payload = _json_refs(payload, var_defined, param_names)
                        input_list.append({"key": pkey,
                                           "value": json.dumps(payload, ensure_ascii=False),
                                           "need_parse": "json_str"})
                    else:
                        # 纯字面量：烘焙成纯 JSON 字符串（不带 need_parse、不带嵌套
                        # rpa:special）。2026-11-04 实测：设计器保存时只有「纯字面量 JSON」
                        # 能原样保留。
                        baked = _bake_refs(payload, param_defaults)
                        input_list.append({"key": pkey,
                                           "value": json.dumps(baked, ensure_ascii=False)})
                else:
                    if p.get("need_parse") == "json_str":
                        # json_str 参数必须落成 JSON 字符串（见 _json_str_or_value）。
                        # 规格里写了裸数组/对象就直接序列化，别让引擎在编译阶段抛 TypeError。
                        val = raw_in if isinstance(raw_in, str) else json.dumps(
                            raw_in, ensure_ascii=False)
                        input_list.append({"key": pkey, "value": val})
                    else:
                        input_list.append({"key": pkey,
                                           "value": resolve_input(raw_in, var_defined, param_names)})
            elif p.get("required"):
                dv = _default_for(p)
                input_list.append({"key": pkey, "value": _json_str_or_value(p, dv)})
                warnings.append(f"{atom_key}.{pkey}(required) 模板未给，用默认值 {dv!r}")
            else:
                # 可选参数也填 schema 真实默认值（不能填 ""：Bool/枚举会被运行时错误强转）
                input_list.append({"key": pkey, "value": _json_str_or_value(p, _default_for(p))})

        # 布尔/数字字面量统一转 python 类型（见 _coerce_literals，坑 69）
        # module_param 排除在外：它是 [{"varId","varName","varValue"}] 结构，不是字面量列表。
        for it in input_list:
            if it.get("key") == "module_param":
                continue
            if isinstance(it.get("value"), list):
                it["value"] = _coerce_literals(it["value"], schema_ins.get(it["key"]) or {})

        # BrowserScript.js_run 显式固定「脚本内容」的显示开关：content 必须显示（show=True），
        # file_path 必须隐藏（show=False）。主修复在上方（input_type 落裸字符串，让设计器的
        # dynamics 求值成立）；这里再写死一遍，保证首次落库时就不依赖设计器重算结果。
        if atom_key.startswith("BrowserScript."):
            for it in input_list:
                if it.get("key") == "content":
                    it["show"] = True
                elif it.get("key") == "file_path":
                    it["show"] = False

        # 输出：按 schema output 的 key 分配变量名（模板指定则用模板名）
        #
        # 特例（坑 145）：以下原子在字典里声明了多个输出，但实现只 `return` 一个值。
        # 引擎按 outputList 的个数生成 `a,b,c,d = atom(...)`，于是那个值会被当成序列
        # 拆成 N 份 —— 元素个数恰好等于 N 时不报错但语义全错（复制一行只拿到第 2 列），
        # 个数不等时直接 ValueError。这类原子必须**只生成 1 个输出变量**。
        out_keys = schema_outs
        if atom_key in SINGLE_RETURN_ATOMS:
            declared = [k for k in schema_outs if k in spec["out"]]
            out_keys = declared[:1]          # 只保留 spec 显式声明的第一个；没声明就不解包
        output_list = []
        for okey in out_keys:
            vname = spec["out"].get(okey)
            if vname is None:
                vname = f"{okey}_{node_id[-4:]}"
            var_defined.add(vname)
            output_list.append({"key": okey, "value": mk_val("var", vname)})

        # 字典没声明输出、实现却有返回值的原子（见 EXTRA_OUTPUT_ATOMS）
        for okey in EXTRA_OUTPUT_ATOMS.get(atom_key, []):
            vname = (spec.get("out") or {}).get(okey)
            if not vname:
                continue
            var_defined.add(vname)
            output_list.append({"key": okey, "value": mk_val("var", vname)})

        node = {
            "key": atom_key,
            "version": atom.get("version", "1.0.1"),
            "id": node_id,
            "alias": spec.get("alias", atom.get("title", atom_key)),
            "inputList": input_list,
            "outputList": output_list,
            "advanced": [
                {"key": "__delay_before__", "value": mk_val("other", 0)},
                {"key": "__delay_after__", "value": mk_val("other", 0)},
            ],
            "exception": [
                # `on_error: "skip"` → 编译成 try/except 包裹，出错继续往下走（`_display_with_skip`）。
                # 典型用法：先删同名旧文件，文件不存在就跳过（坑 68）。
                {"key": "__skip_err__", "value": mk_val("other", spec.get("on_error") or "exit")},
                {"key": "__retry_time__", "value": mk_val("other", 0)},
                {"key": "__retry_interval__", "value": mk_val("other", 0)},
            ],
        }
        if warnings:
            node["_warnings"] = warnings
        nodes.append(node)

    return nodes


def _default_for(p):
    """按 schema 给安全默认值。

    优先级：schema 的 default > options 首个 value > types 兜底 > 参数名启发。
    注意：以前把可选参数一律写成 ""，会让 Bool 参数（simulate_flag/scroll_into_center）
    变成空串、运行时被错误强转 —— 这里必须用真实 schema 默认值。
    """
    if p.get("default") not in (None, ""):
        return p["default"]
    if p.get("options"):
        for o in p["options"]:
            if isinstance(o, dict) and "value" in o:
                return o["value"]
            if not isinstance(o, dict):
                return o
    t = p.get("types")
    if t == "Bool":
        return False
    if t == "Int":
        return 0
    if t == "Float":
        return 0.0
    # 兜底按名字
    name = (p.get("key") or "").lower()
    if "range" in name:
        return "A1:Z1000"
    return ""


def _json_str_or_value(p, dv):
    """按参数形态给出「引擎能接受」的默认值。

    绝大多数参数要的是 `[{"type","value"}]` 包装（`mk_val`）。
    但声明了 `need_parse="json_str"` 的参数例外：引擎 `flow/params.py::parse_param`
    对这类参数直接 `json.loads(value)`，所以 value 必须是**JSON 字符串**，
    给数组会抛 `TypeError: the JSON object must be str, bytes or bytearray, not list`
    ——错误发生在编译代码阶段，堆栈指向引擎内部（params.py:138 / ast.py:106），
    现场看不出跟"漏填了一个可选参数"有关（2026-11-03 实测踩过）。

    典型参数：`Email.send_email.replace_table`、`Dialog.custom_box.design_interface`、
    `BrowserScript.js_run.params`、`Agent.call_astron_agent.astron_workflow`。
    规格里显式写时用 `$json:` 前缀即可。
    """
    if p.get("need_parse") == "json_str":
        if isinstance(dv, str):
            return dv
        return json.dumps(dv, ensure_ascii=False)
    return mk_val("other", dv)


def is_element_pick(p):
    """元素拾取参数（必须传真实 c_element）：formType.type == 'PICK'。"""
    ft = p.get("formType")
    return isinstance(ft, dict) and ft.get("type") == "PICK"


# ----------------------------- 校验 -----------------------------
def _is_reader(atom_key):
    """判断原子是否「只读」——这类原子的 file_path 必须事先存在。

    用**词元**判定而不是前缀：`File.file_read` 的词元是 [file, read]，
    并不以 `read_` 开头，前缀法会漏判（实测踩过）。
    """
    tail = str(atom_key).split(".", 1)[-1].lower()
    tokens = set(tail.split("_"))
    if tokens & {"create", "save", "write", "append", "export"}:
        return False
    return bool(tokens & {"open", "read", "load", "import"})


def _looks_abs(p):
    r"""绝对路径判定（盘符 `X:\`、UNC `\\host`、`/` 开头）。相对路径不检查，避免误报。"""
    if not isinstance(p, str) or not p.strip():
        return False
    return bool(re.match(r"^[A-Za-z]:[\\/]", p) or p.startswith("\\\\") or p.startswith("/"))


def validate(process_content, atom_dict, elements=None, robot_id=None, params=None):
    """校验节点树。传入 elements 时，element 引用会被真实核对（存在性 + robot 归属）。

    含**枚举值合法性检查**：参数 schema 有 options 时，字面量值必须在 options 里。
    （踩过：`Excel.read_excel.read_range` 是枚举 cell/row/column/area/all，
      曾误填 "A1:Z1000" → 运行时必炸，生成阶段就该拦住。）

    含**读取型路径存在性检查**：`open_*/read_*/load_*/import_*` 这类原子传绝对路径时，
    文件必须真实存在，否则流程第一步就抛「文件不存在，请检查路径信息！」。
    （踩过：模板路径写成占位 `C:\订单.xlsx`，schema 完全合法但跑不起来。）

    params: 已声明的流程参数名集合（校验 p_var 引用是否合法）。
    """
    errors, warnings = [], []
    seen_ids = set()
    # 先收集「本流程会创建/写入的路径」，这些路径后面不存在是正常的
    produced = set()
    for node in process_content:
        key = node.get("key") or ""
        if not _is_reader(key):
            for it in node.get("inputList") or []:
                if it.get("key") in ("file_path", "path") or str(it.get("key", "")).endswith("file_path"):
                    _rv = it.get("value")
                    _lv = _rv if isinstance(_rv, list) else [_rv]
                    for v in _lv:
                        if isinstance(v, dict) and isinstance(v.get("value"), str):
                            produced.add(v["value"].replace("/", "\\").lower())
    for node in process_content:
        nid = node.get("id")
        if nid in seen_ids:
            errors.append(f"重复节点 id: {nid}")
        seen_ids.add(nid)
        key = node.get("key")
        if key not in atom_dict:
            errors.append(f"节点 {nid} 的原子 {key} 不存在于 c_atom_meta_new")
            continue
        atom = atom_dict[key]
        schema = {p.get("key"): p for p in (atom.get("inputList") or []) if isinstance(p, dict)}
        schema_ins = set(schema)
        provided = {it.get("key") for it in node.get("inputList") or []}
        for missing in (schema_ins - provided):
            errors.append(f"节点 {nid}({key}) 缺少输入参数 {missing}")
        for extra in (provided - schema_ins):
            warnings.append(f"节点 {nid}({key}) 含 schema 外的输入参数 {extra}")
        # 本原子上「被 dynamics 显示条件引用」的参数：全库 357 个原子中 143 个（40%）
        # 存在这种结构（如 fill_type/input_type/simulate_flag/get_type...）。它们的
        # 落库形态直接决定下游参数在设计器里是否可见、运行时是否被引擎跳过。
        dyn_refs = _collect_dynamics_refs(atom.get("inputList") or [])
        for it in node.get("inputList") or []:
            p = schema.get(it.get("key")) or {}
            opts = [o.get("value") if isinstance(o, dict) else o for o in (p.get("options") or [])]
            # value 支持两种形态：数组 [{type,value}]（常规）与裸标量（被 dynamics 引用的
            # 参数，见 _collect_dynamics_refs）。裸标量必须包成单元素列表再统一核对。
            _raw_v = it.get("value")
            _vals = _raw_v if isinstance(_raw_v, list) else [_raw_v]
            # ---- 形态约束 R1：dynamics 引用参数必须落裸标量 ----
            # 设计器求值 `$this.<key>.value` 取 value 原样：数组形态恒不等于字符串条件
            # → 下游参数 show=False → 设计器隐藏、保存回写、引擎跳过（「脚本数据为空」
            # 「输入内容整行消失」两个实锤案例）。此处校验兜底，防止生成器回退。
            if it.get("key") in dyn_refs:
                if isinstance(_raw_v, list):
                    errors.append(
                        f"节点 {nid}({key}).{it.get('key')} 被 dynamics 显示条件引用，"
                        f"必须落库为裸标量（当前是数组 {_raw_v!r}，设计器会把下游参数算成隐藏）")
                elif isinstance(_raw_v, str) and _raw_v.startswith("$"):
                    warnings.append(
                        f"节点 {nid}({key}).{it.get('key')} 被 dynamics 引用但值是引用形态"
                        f"（{_raw_v}），设计器显示可能异常，建议改字面量")
            for v in _vals:
                if not isinstance(v, dict):
                    # ---- 形态约束 R2：裸标量枚举值同样要校验合法性 ----
                    if opts and isinstance(v, (str, int)) and v not in ("",):
                        if v not in opts:
                            errors.append(
                                f"节点 {nid}({key}).{it.get('key')} 取值 {v!r} 不是合法枚举，"
                                f"可选：{opts}")
                    continue
                if v.get("type") == "other" and opts and v.get("value") not in ("", None):
                    if v.get("value") not in opts:
                        errors.append(
                            f"节点 {nid}({key}).{it.get('key')} 取值 {v.get('value')!r} 不是合法枚举，"
                            f"可选：{opts}")
                # 读取型路径必须真实存在（生成期就拦，别让用户跑到第一步才看见报错）
                pk = str(it.get("key") or "")
                if (v.get("type") == "other" and isinstance(v.get("value"), str)
                        and (pk == "file_path" or pk.endswith("file_path"))
                        and _is_reader(key) and _looks_abs(v["value"])):
                    p = v["value"]
                    if p.replace("/", "\\").lower() not in produced and not os.path.exists(p):
                        errors.append(
                            f"节点 {nid}({key}).{pk} 指向的路径不存在：{p}（流程会在第一步报「文件不存在」）")
                # p_var（流程参数）引用核对
                if v.get("type") == "p_var":
                    pn = v.get("value")
                    if params is not None and pn not in params:
                        errors.append(f"节点 {nid}({key}).{pk} 引用了未声明的流程参数 {pn}")
                    elif params is None:
                        warnings.append(f"节点 {nid}({key}).{pk} 引用了流程参数 {pn}，未核对是否已声明")
                if v.get("type") != "element":
                    continue
                data = v.get("data")
                if elements is None:
                    warnings.append(f"节点 {nid} 引用了元素库（element），未核对 data={data}")
                    continue
                rec = elements.get(str(data))
                if rec is None:
                    errors.append(f"节点 {nid}({key}).{it.get('key')} 引用的元素 data={data} 不在 c_element 中")
                elif robot_id and str(rec.get("robot_id")) != str(robot_id):
                    warnings.append(
                        f"节点 {nid}({key}).{it.get('key')} 引用的元素「{rec.get('element_name')}」"
                        f"属于 robot {rec.get('robot_id')}，非目标 robot {robot_id}")

    # 参数默认值里的路径也要真实存在（否则「不传参」跑起来同样第一步就报错）
    if isinstance(params, dict):
        used_by = {}
        for node in process_content:
            for it in node.get("inputList") or []:
                _raw_v = it.get("value")
                _vals = _raw_v if isinstance(_raw_v, list) else [_raw_v]
                for v in _vals:
                    if isinstance(v, dict) and v.get("type") == "p_var":
                        used_by.setdefault(v.get("value"), set()).add(node.get("key"))
        for pname, p in params.items():
            dv = p.get("default")
            if not isinstance(dv, str) or not _looks_abs(dv):
                continue
            readers = [a for a in used_by.get(pname, ()) if _is_reader(a)]
            if readers and not os.path.exists(dv):
                errors.append(
                    f"流程参数 {pname} 的默认路径不存在：{dv}"
                    f"（被 {readers[0]} 读取；不传参时流程会失败）")
    return {"is_pass": len(errors) == 0, "errors": errors, "warnings": warnings}


# ----------------------------- 单原子节点构建（覆盖测试用） -----------------------------
def build_single_atom_node(atom_key, atom, element_refs=None):
    """为单个原子生成一个节点（填默认值/元素），返回 (node, needs_element, warnings)。

    element_refs: {参数key: {"value": 元素名, "data": element_id}}
                  来自 c_element 的真实元素；未提供的拾取参数用占位符。
    """
    schema_ins = [p for p in (atom.get("inputList") or []) if isinstance(p, dict)]
    schema_outs = [p for p in (atom.get("outputList") or []) if isinstance(p, dict)]
    node_id = f"bh{int(time.time() * 1000)}{atom_key[:6]}"
    input_list, needs_element, warnings = [], False, []

    for p in schema_ins:
        key = p.get("key")
        if is_element_pick(p):
            needs_element = True
            ref = (element_refs or {}).get(key)
            if ref:
                # 真实流程实证格式：{"type":"element","value":元素名,"data":element_id}
                val = {"type": "element", "value": ref.get("value", ""),
                       "data": ref.get("data", "")}
            else:
                # 占位元素引用：真实 value/data 必须来自 c_element
                val = {"type": "element", "value": "_NEED_REAL_ELEMENT_", "data": "_NEED_REAL_ID_"}
            input_list.append({"key": key, "value": [val]})
            if p.get("required") and not ref:
                warnings.append(f"{atom_key}.{key} 是元素拾取(必填)，需真实 c_element 才能运行")
            continue
        # 非元素参数：一律填 schema 真实默认值（"" 会让 Bool/枚举被运行时错误强转）
        input_list.append({"key": key, "value": mk_val("other", _default_for(p))})

    output_list = [{"key": o.get("key"), "value": mk_val("var", f"{o.get('key')}_{node_id[-4:]}")}
                   for o in schema_outs]
    node = {
        "key": atom_key,
        "version": atom.get("version", "1.0.1"),
        "id": node_id,
        "alias": atom.get("title", atom_key),
        "inputList": input_list,
        "outputList": output_list,
        "advanced": [
            {"key": "__delay_before__", "value": mk_val("other", 0)},
            {"key": "__delay_after__", "value": mk_val("other", 0)},
        ],
        "exception": [
            {"key": "__skip_err__", "value": mk_val("other", "exit")},
            {"key": "__retry_time__", "value": mk_val("other", 0)},
            {"key": "__retry_interval__", "value": mk_val("other", 0)},
        ],
    }
    return node, needs_element, warnings


def run_coverage(offline=False):
    """逐个原子可用性覆盖测试：358 原子 → 解析失败 / 可直接生成 / 需真实元素。"""
    repaired = []
    if offline and os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            parsed = json.load(f)
        rows_total, parse_fail = len(parsed), []
    else:
        db = ServerDb()
        try:
            rows = db.sql_rows("SELECT atom_key, atom_content FROM c_atom_meta_new")
        finally:
            db.close()
        rows_total, parse_fail, parsed = len(rows), [], {}
        for r in rows:
            if len(r) < 2:
                parse_fail.append(r[0] if r else "?")
                continue
            raw = "\t".join(r[1:])  # 内容若含 tab，sql_rows 会切成多列，这里拼回
            obj, was = _parse_atom(raw)
            if obj is None:
                parse_fail.append(r[0])
                continue
            parsed[r[0]] = obj
            if was:
                repaired.append(r[0])
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(parsed, f, ensure_ascii=False, indent=2)

    ok, needs = [], []
    for k, c in parsed.items():
        _, ne, warns = build_single_atom_node(k, c)
        rec = {"atom_key": k, "version": c.get("version"), "needs_element": ne,
               "warnings": warns, "n_inputs": len(c.get("inputList") or []),
               "n_outputs": len(c.get("outputList") or [])}
        (needs if ne else ok).append(rec)

    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_rows": rows_total,
        "parse_fail": parse_fail,
        "parse_fail_count": len(parse_fail),
        "parseable": len(parsed),
        "ok_count": len(ok),
        "needs_element_count": len(needs),
        "repaired_on_read": repaired,
        "repaired_on_read_count": len(repaired),
        "ok": ok,
        "needs_element": needs,
    }
    out = os.path.join(HERE, "docs", "atom_coverage_report.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"原子总数(行):           {rows_total}")
    print(f"解析失败/不可用:         {len(parse_fail)}  -> {parse_fail}")
    print(f"可解析:                 {len(parsed)}")
    print(f"  其中读时自动修复(双重转义): {len(repaired)}  -> {repaired}")
    print(f"  ✅ 可直接生成(无元素依赖): {len(ok)}")
    print(f"  🔗 需真实元素(拾取必填):   {len(needs)}")
    print(f"报告已写出:             {out}")
    return report


# ----------------------------- 元素库（真实 c_element） -----------------------------
# 实证结论（2026-10-30）：
#   1) 真实流程里元素参数写作 {"type":"element","value":元素名,"data":element_id}，
#      178/178 个 element.data 都能在 c_element.element_id 命中。
#   2) c_element.element_data 顶层 type 只有三种：web(427) / uia(420) / cv(93)。
#   3) 同一 element_id 会按 robot_id 复制多行，生成流程时应选目标 robot 的那份。
#   4) 拾取参数用 types 决定元素种类：WebPick→web、WinPick→uia、IMGPick→cv。
PICK_TYPES_TO_KIND = {"WebPick": "web", "WinPick": "uia", "IMGPick": "cv"}
KIND_CN = {"web": "网页元素", "uia": "桌面元素", "cv": "图像元素", "batch": "批量抓取结构"}


def pick_element_kind(p):
    """由拾取参数推断所需元素种类。返回 web/uia/cv/batch/None。"""
    ft = p.get("formType") or {}
    use = str((ft.get("params") or {}).get("use") or "")
    if use == "BATCH":
        return "batch"           # 批量抓取：要的是多列结构，不是单个元素
    kind = PICK_TYPES_TO_KIND.get(str(p.get("types")))
    if kind:
        return kind
    if use == "WINDOW":
        return "uia"
    if use == "CV":
        return "cv"
    return None


def _rec_from_element_obj(eid, name, robot_id, obj):
    """element_data(dict) → 精简记录。web/uia/cv 共用同一套字段。"""
    if not isinstance(obj, dict):
        return None
    # path 结构不统一：web/uia 是 dict，个别（批量/图像）是 list，统一兜底
    path = obj.get("path")
    if not isinstance(path, dict):
        path = {}
    xp = path.get("xpath")
    xp = unwrap_special(xp) if isinstance(xp, dict) else (xp if isinstance(xp, str) else "")
    return {
        "element_id": eid,
        "element_name": name,
        "robot_id": robot_id,
        "kind": str(obj.get("type")),
        "app": obj.get("app"),
        "check_type": path.get("checkType"),
        "tag": path.get("tag"),
        "xpath": (xp or "")[:200],
    }


def _api_post(path, params=None):
    """最小 HTTP 客户端（只用 urllib，不引入 requests，便于换机迁移）。

    鉴权走设计器本机 cookie 文件（`%APPDATA%/astron-rpa/.cookie.json`），
    与 publish_flow.load_session 同一来源，不重复登录。
    """
    import urllib.parse
    import urllib.request

    cookie_file = cfg.cookie_file()
    if not os.path.exists(cookie_file):
        raise RuntimeError("找不到设计器 cookie 文件：{}".format(cookie_file))
    with open(cookie_file, "r", encoding="utf-8") as f:
        ck = json.load(f)
    host = cfg.host_port()
    rec = ck.get(host) or ck.get("{}.SESSION".format(host))
    if not rec:
        raise RuntimeError("cookie 里没有 {} 的会话".format(host))
    val = rec["SESSION"]["key_value"]
    cookie = val if val.startswith("SESSION=") else "SESSION={}".format(val)

    url = "http://{}{}".format(cfg.load()["gateway"]["host"], path)
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url, data=b"{}", headers={"Content-Type": "application/json", "Cookie": cookie}
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def _load_elements_api(robot_id):
    """用官方 HTTP 接口拉元素库（不需要 SQL 通道，迁移友好）。"""
    res = _api_post("/api/robot/element/all", {"robotId": robot_id, "elementType": "common"})
    if res.get("code") != "000000":
        raise RuntimeError("读元素库失败：{} {}".format(res.get("code"), res.get("message")))

    out = {}
    for g in res.get("data") or []:
        for e in g.get("elements") or []:
            eid = e.get("id")
            if not eid:
                continue
            d = _api_post("/api/robot/element/detail", {"robotId": robot_id, "elementId": eid})
            if d.get("code") != "000000" or not d.get("data"):
                continue
            raw = d["data"].get("elementData")
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except Exception:
                continue
            rec = _rec_from_element_obj(eid, e.get("name"), robot_id, obj)
            if rec:
                out[eid] = rec
    return out


def load_elements(offline=False, robot_id=None):
    """拉真实元素库 → {element_id: 精简记录}（带 web/uia/cv 分类）。

    取数顺序（前一个失败才退到下一个）：

      1. **官方 HTTP 接口**（首选）。只要设计器登录着就能用，不依赖 SQL 通道，
         迁移到新环境时行为不变。
      2. `c_element` SQL（`db.enabled=true` 时才走；老环境/排查用）。
      3. 本地缓存 `docs/element_cache.json`（纯离线兜底）。

    **元素核对是锦上添花，不该阻塞主链路**：三条路都拿不到就返回空字典，
    后续 `validate(elements=None)` 会自动跳过元素归属检查。
    """
    if offline and os.path.exists(ELEMENT_CACHE):
        with open(ELEMENT_CACHE, "r", encoding="utf-8") as f:
            return json.load(f)

    # 1) HTTP 接口。**注意：空字典 ≠ 失败**——新机器人本来就可能有 0 个元素，
    #    这时若回落到本地缓存，会把别的机器人的元素当成自己的（坑 129，实测踩到）。
    #    所以这里用返回值区分「接口成功但为空」与「接口不可用」。
    if robot_id:
        try:
            out = _load_elements_api(robot_id)
            print(f"      · 元素库经 HTTP 接口拉到 {len(out)} 个")
            os.makedirs(os.path.dirname(ELEMENT_CACHE), exist_ok=True)
            with open(ELEMENT_CACHE, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, indent=2)
            return out
        except Exception as e:
            print(f"      · HTTP 拉元素库失败（{e}），继续降级")

    # 2) SQL 通道
    if cfg.db_conf():
        db = ServerDb()
        try:
            rows = db.sql_rows("SELECT element_id, element_name, robot_id, element_data FROM c_element")
        finally:
            db.close()

        out = {}
        for r in rows:
            if len(r) < 4:
                continue
            raw = "\t".join(r[3:])   # element_data 若含 tab，sql_rows 会切列，这里拼回
            obj = None
            for attempt in (raw, raw.replace("\\\\", "\\")):
                try:
                    obj = json.loads(attempt)
                    break
                except Exception:
                    continue
            rec = _rec_from_element_obj(r[0], r[1], r[2], obj)
            if not rec:
                continue
            # 同一 element_id 多行（按 robot 复制）：优先保留目标 robot 的那份
            old = out.get(r[0])
            if old is None or (robot_id and str(rec["robot_id"]) == str(robot_id)
                               and str(old.get("robot_id")) != str(robot_id)):
                out[r[0]] = rec

        os.makedirs(os.path.dirname(ELEMENT_CACHE), exist_ok=True)
        with open(ELEMENT_CACHE, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        return out

    # 3) 本地缓存
    if os.path.exists(ELEMENT_CACHE):
        with open(ELEMENT_CACHE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        print(f"      · 元素库用本地缓存（{len(cache)} 个）")
        return cache
    print("      · 元素库跳过（HTTP 与 SQL 都不可用，且无缓存；不影响结构校验）")
    return {}


def pick_samples(elements, robot_id=None, strict=False):
    """每种元素种类挑一个「最可用」的真实元素作样本。

    strict=True 时只认目标 robot 名下的元素（因为 c_element 是按 robot 复制的，
    跨 robot 引用在运行时可能解析不到）。
    """
    best = {}
    for rec in elements.values():
        kind = rec.get("kind")
        if kind not in ("web", "uia", "cv"):
            continue
        is_own = bool(robot_id) and str(rec.get("robot_id")) == str(robot_id)
        if strict and not is_own:
            continue
        score = 4 if is_own else 0
        if rec.get("xpath"):
            score += 2
        if rec.get("check_type"):
            score += 1
        if kind not in best or score > best[kind][0]:
            best[kind] = (score, rec)
    return {k: v[1] for k, v in best.items()}


def ensure_elements(template, robot_id=None, auto_pick=True):
    """按规格里的 `element_requirements` 把缺的元素补齐，返回 {名字: element_id}。

    ## 为什么要有这一步

    网页流程的元素是**绑定到页面**的，不可能预置。以前的做法是「人在设计器里点一下」，
    AI 到这一步就得停。现在规格自己声明「要什么元素、从哪取」：

        "element_requirements": [
          {"name": "百度_搜索输入框", "kind": "web",
           "pick": {"url": "https://www.baidu.com", "css": "#chat-textarea"}}
        ]

    生成流程前先跑一遍本函数：库里已有同名元素就复用（幂等，不重复建），
    没有就让 `element_pick.ensure_element` 打开浏览器现场拾取并写库。

    auto_pick=False 时只检查不创建，缺了就报错——适合「元素必须由人工确认」的场景。
    """
    reqs = template.get("element_requirements") or []
    if not reqs:
        return {}

    robot_id = robot_id or DEFAULT_ROBOT_ID
    have = {r.get("element_name") for r in load_elements(robot_id=robot_id).values()}
    result = {}

    for req in reqs:
        name = req.get("name")
        if not name:
            continue
        if name in have:
            result[name] = None      # 交给后面按名字解析真实 id
            print(f"      · 元素「{name}」已在库中，复用")
            continue

        pick = req.get("pick") or {}
        if not pick:
            raise KeyError(f"元素「{name}」不在库中，且规格没给 pick（url/css），无法自动拾取")
        if not auto_pick:
            raise KeyError(f"元素「{name}」不在库中，且当前不允许自动拾取（auto_pick=False）")

        import element_pick as EP

        print(f"      · 元素「{name}」缺失，自动拾取：{pick.get('css') or pick.get('xpath')} @ {pick.get('url')}")
        got = EP.ensure_element(
            robot_id=robot_id,
            name=name,
            url=pick.get("url"),
            selector=pick.get("css") or pick.get("xpath"),
            by="xpath" if pick.get("xpath") and not pick.get("css") else "css",
            batch=req.get("batch"),
        )
        print(f"      · 已{'新建' if got['created'] else '复用'}元素「{name}」id={got['element_id']}")
        result[name] = got["element_id"]
        have.add(name)

    return result


def pick_element_by_slot(slot, elements, robot_id=None, samples=None):
    """按规格里的「元素槽位」取一个真实元素。返回精简记录或 None。

    槽位支持两种写法（向后兼容）：

        "elements": {"element_data": "web"}
            → 老写法：按种类随便挑一个（pick_samples 的打分结果）

        "elements": {"element_data": {"kind": "web", "name": "百度_搜索输入框"}}
            → 新写法：按名字精确取。**网页流程必须用这个**，否则库里元素一多，
              打分挑选会挑到别的站点的元素，运行时定位必然失败。

    为什么要有新写法：元素是按页面绑定的，不是按种类绑定的。「随便一个 web 元素」
    在元素库只有 1 个时能跑通，有 13 个时就是随机炸弹（坑 126）。
    """
    if isinstance(slot, str):
        if samples is None:
            samples = pick_samples(elements or {}, robot_id=robot_id, strict=True)
        return samples.get(slot)

    if not isinstance(slot, dict):
        return None

    name = slot.get("name")
    kind = slot.get("kind")
    if not name:
        # 只给了 kind：退回按种类挑
        if samples is None:
            samples = pick_samples(elements or {}, robot_id=robot_id, strict=True)
        return samples.get(kind or "web")

    cands = [r for r in (elements or {}).values() if r.get("element_name") == name]
    if kind:
        cands = [r for r in cands if r.get("kind") == kind] or cands
    if not cands:
        return None

    # 同名的多条时优先目标 robot 名下、且带 xpath 的
    def score(r):
        s = 4 if (robot_id and str(r.get("robot_id")) == str(robot_id)) else 0
        return s + (2 if r.get("xpath") else 0)

    return sorted(cands, key=score, reverse=True)[0]


def _check_pick_atoms(atoms, elements, samples, robot_id):
    """用给定元素样本逐原子生成节点并校验，返回 (results, ok_cnt)。"""
    results, ok_cnt = [], 0
    for k, atom in atoms.items():
        picks = [p for p in (atom.get("inputList") or [])
                 if isinstance(p, dict) and is_element_pick(p)]
        if not picks:
            continue

        refs, kinds, notes = {}, {}, []
        for p in picks:
            pk = p.get("key")
            kind = pick_element_kind(p)
            kinds[pk] = kind
            if kind == "batch":
                notes.append(f"{pk} 是批量抓取(BATCH)：需要多列结构，单元素不适用")
                kind = "web"   # 退一步用 web 元素占位，仅证明节点可生成
            sample = samples.get(kind or "")
            if sample:
                refs[pk] = {"value": sample["element_name"], "data": sample["element_id"]}
                if robot_id and str(sample.get("robot_id")) != str(robot_id):
                    notes.append(f"{pk} 借用 robot {sample['robot_id']} 的元素（非目标 robot）")
            else:
                notes.append(f"{pk} 需要的 {KIND_CN.get(kind or '', kind)} 在可选元素中不存在")

        node, ne, warns = build_single_atom_node(k, atom, refs)
        res = validate([node], atoms)
        # 硬校验：所有拾取参数都有引用，且 element_id 真实存在于 c_element
        missing_ref = [p.get("key") for p in picks if p.get("key") not in refs]
        bad_id = [r["data"] for r in refs.values() if r.get("data") not in elements]
        is_pass = res["is_pass"] and not missing_ref and not bad_id
        if is_pass:
            ok_cnt += 1
        results.append({
            "atom_key": k,
            "title": atom.get("title"),
            "pick_params": kinds,
            "elements": refs,
            "is_pass": is_pass,
            "missing_ref": missing_ref,
            "bad_element_id": bad_id,
            "errors": res["errors"],
            "warnings": warns + notes,
        })
    return results, ok_cnt


def run_element_check(offline=False, robot_id=DEFAULT_ROBOT_ID):
    """66 个拾取原子 → 用真实 c_element 元素生成节点并校验，回答「带元素的节点能否生成」。

    双档输出：
      - 通用档：元素可跨 robot 借用 → 证明「节点结构 + 元素引用」完全可生成；
      - 严格档：只允许目标 robot 名下的元素 → 反映该 robot 现在真能跑多少。
    """
    atoms = load_atom_dict(offline=offline)
    elements = load_elements(offline=offline, robot_id=robot_id)

    kinds_count = {}
    for rec in elements.values():
        kinds_count[rec.get("kind")] = kinds_count.get(rec.get("kind"), 0) + 1

    own = [r for r in elements.values() if str(r.get("robot_id")) == str(robot_id)]
    own_kinds = {}
    for r in own:
        own_kinds[r.get("kind")] = own_kinds.get(r.get("kind"), 0) + 1

    samples = pick_samples(elements, robot_id=robot_id, strict=False)
    strict_samples = pick_samples(elements, robot_id=robot_id, strict=True)

    results, ok_cnt = _check_pick_atoms(atoms, elements, samples, robot_id)
    strict_results, strict_ok = _check_pick_atoms(atoms, elements, strict_samples, robot_id)
    strict_map = {r["atom_key"]: r for r in strict_results}
    for r in results:
        r["strict_ok"] = bool(strict_map.get(r["atom_key"], {}).get("is_pass"))

    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "robot_id": robot_id,
        "element_total": len(elements),
        "element_kinds": kinds_count,
        "target_robot_elements": len(own),
        "target_robot_kinds": own_kinds,
        "samples": samples,
        "strict_samples": strict_samples,
        "pick_atoms": len(results),
        "ok_count": ok_cnt,
        "fail_count": len(results) - ok_cnt,
        "strict_ok_count": strict_ok,
        "strict_fail_count": len(strict_results) - strict_ok,
        "ok": [r for r in results if r["is_pass"]],
        "fail": [r for r in results if not r["is_pass"]],
        "strict_fail": [r for r in strict_results if not r["is_pass"]],
    }
    out = os.path.join(HERE, "docs", "atom_element_report.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"元素库去重后:           {len(elements)}   分类: {kinds_count}")
    print(f"目标 robot {robot_id} 名下: {len(own)}   分类: {own_kinds or '无'}")
    print("通用档样本（可跨 robot 借用）:")
    for kind, s in samples.items():
        print(f"  {KIND_CN.get(kind, kind):6s} {s['element_name']} "
              f"(id={s['element_id']}, robot={s['robot_id']}, xpath={s['xpath'][:46]})")
    print(f"\n拾取原子数: {len(results)}")
    print(f"  ✅ 通用档 带真实元素可生成: {ok_cnt}")
    print(f"  ❌ 通用档 不可生成:         {len(results) - ok_cnt}")
    for r in report["fail"]:
        print(f"     - {r['atom_key']}: {r['errors'] or r['warnings']}")
    print(f"  ✅ 严格档（仅目标 robot 元素）可生成: {strict_ok}")
    print(f"  ⚠️  严格档需先给目标 robot 建元素:    {len(strict_results) - strict_ok}")
    print(f"报告已写出:             {out}")
    return report


# ----------------------------- 写库（走客户端同款接口） -----------------------------
def write_to_rpa(process_content, robot_id, name):
    """把节点树写进 .130：复用设计器客户端本地会话 + 客户端同款接口。

    2026-10-30 实证纠正了两个早期错误假设：
      1) 鉴权不是 `Authorization: Bearer`，而是客户端本地会话 cookie
         （`%APPDATA%/astron-rpa/.cookie.json` 里的 `SESSION`）；
      2) 保存字段不是 `process_content`，而是 **`processJson`（JSON 字符串）**，
         且流程必须先用 `/process/create` 建出来。
    细节与完整流程见 `scripts/publish_flow.py`。
    """
    try:
        from publish_flow import (load_session, create_process, save_process,
                                  list_processes, read_process, ok)
    except Exception as e:
        return {"error": f"无法加载 publish_flow.py：{e}"}
    try:
        cookie = load_session()
        pj = json.dumps(process_content, ensure_ascii=False)
        existing = [p for p in list_processes(cookie, robot_id)
                    if isinstance(p, dict) and p.get("processName") == name]
        if existing:
            pid = existing[0].get("processId")
        else:
            pid = create_process(cookie, robot_id, name)
        save_process(cookie, robot_id, pid, pj)
        back = read_process(cookie, robot_id, pid)
        return {"robot_id": robot_id, "process_id": pid,
                "saved_bytes": len(pj), "readback_bytes": len(back or ""),
                "verified": back == pj}
    except SystemExit as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


# ----------------------------- 入口 -----------------------------
def main():
    ap = argparse.ArgumentParser(description="星辰开源 · 路线 B 流程生成器")
    ap.add_argument("--purpose", help="固定流程模板名（见 TEMPLATES）")
    ap.add_argument("--nl", help="自然语言意图（会匹配模板）")
    ap.add_argument("--spec", help="流程规格 JSON 路径（AI/LLM 产出接口，见 load_spec）")
    ap.add_argument("--robot-id", type=int, default=DEFAULT_ROBOT_ID, help="写库目标 robot_id")
    ap.add_argument("--offline", action="store_true", help="用缓存的原子字典，不连 .130")
    ap.add_argument("--coverage", action="store_true", help="逐个原子可用性覆盖测试（不建流程）")
    ap.add_argument("--elements", action="store_true",
                    help="66 个元素拾取原子：用真实 c_element 元素生成节点并校验")
    ap.add_argument("--write", action="store_true", help="尝试写库（需 RPA_SESSION_TOKEN，红线操作）")
    args = ap.parse_args()

    if args.coverage:
        run_coverage(offline=args.offline)
        return 0

    if args.elements:
        rep = run_element_check(offline=args.offline, robot_id=args.robot_id)
        return 0 if rep["fail_count"] == 0 else 1

    if args.spec:
        try:
            tpl = load_spec(args.spec)
        except Exception as e:
            ap.error(f"规格文件无法加载：{e}")
        purpose = tpl["name"]
    else:
        purpose = args.purpose or intent_match(args.nl)
        if purpose not in TEMPLATES:
            ap.error(f"未知模板 {purpose}，可选：{list(TEMPLATES)}")
        tpl = TEMPLATES[purpose]

    print(f"[1/4] 加载原子字典（{'离线缓存' if args.offline else '服务端实时'}）...")
    atom_dict = load_atom_dict(offline=args.offline)
    print(f"      原子总数：{len(atom_dict)}")

    # 模板声明了元素槽位时，才去拉元素库（按目标 robot 过滤）
    elements = None
    if any(n.get("elements") for n in tpl["nodes"]):
        # 先补齐：规格里写的是「元素名」，库里没有就会在 validate 阶段被判归属失败。
        # ensure_elements 是幂等的（同名复用），所以每次生成都跑一遍不会重复建元素。
        # 不接这一步的后果：AI 生成网页流程必须停下来等人工去设计器点选（2026-10-31 实测）
        if tpl.get("element_requirements") and not args.offline:
            try:
                got = ensure_elements(tpl, robot_id=args.robot_id)
                if got:
                    print(f"      元素就绪 {len(got)} 个：{list(got)}")
            except Exception as e:  # noqa: BLE001
                print(f"      ⚠️ 元素补齐失败（继续用库里已有的）：{e}")
        elements = load_elements(offline=args.offline, robot_id=args.robot_id)
        own = sum(1 for r in elements.values() if str(r.get("robot_id")) == str(args.robot_id))
        print(f"      元素库：{len(elements)}（目标 robot 名下 {own}）")

    print(f"[2/4] 构建固定流程：{tpl['name']}（{len(tpl['nodes'])} 节点）")
    try:
        pc = build_process_content(tpl, atom_dict, elements=elements, robot_id=args.robot_id)
    except KeyError as e:
        print(f"      ❌ {e.args[0] if e.args else e}")
        return 2

    print("[3/4] 校验节点树...")
    res = validate(pc, atom_dict, elements=elements, robot_id=args.robot_id,
                   params={p["name"]: p for p in (tpl.get("params") or [])})
    print(f"      IS_PASS: {'YES ✅' if res['is_pass'] else 'NO ❌'}")
    for e in res["errors"]:
        print(f"      错误: {e}")
    for w in res["warnings"]:
        print(f"      警告: {w}")

    out = {
        "purpose": purpose,
        "name": tpl["name"],
        "nl_examples": tpl["nl_examples"],
        "robot_id": args.robot_id,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        # 流程参数必须一起带出去：publish_flow 靠它同步服务端 c_param，
        # 少了这一步，节点里的 $p:xxx 在编译出的 main.py 里就是未定义变量（NameError）
        "params": tpl.get("params") or [],
        "process_content": pc,
        "validation": res,
    }
    out_path = os.path.join(HERE, "docs", "generated_flow.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[4/4] 已写出 {out_path}")

    if args.write:
        print("=> --write：尝试落库（需会话 token）")
        print("  ", write_to_rpa(pc, args.robot_id, tpl["name"]))
    else:
        print("=> 未指定 --write：仅生成+校验，未写库（红线保护）。")

    return 0 if res["is_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
