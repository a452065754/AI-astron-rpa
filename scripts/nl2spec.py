#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""nl2spec —— 自然语言直出流程规格（L3 产品层入口）

一句话：**人话 → LLM 直出 spec → 机器校验 → 错了回灌重写（自愈）→ 通过才落盘**。

与 `ai_flow.py --nl` 的区别（很重要，别混）：

| | `ai_flow.py --nl` | `nl2spec.py` |
| --- | --- | --- |
| 怎么做 | **关键词匹配已有规格**（`flows/` 里找最像的） | **LLM 现场生成新规格** |
| 能不能做没做过的事 | 不能，只能复用 | 能 |
| 要不要 AI 会话在场 | 要（靠 AI 手写 spec） | **不要**，程序自己跑完 |
| 失败怎么办 | 报「没听懂」 | 把 lint 报错**回灌**给模型重写（最多 N 轮） |

所以这是把「AI 搭流程」从**人机协作**变成**程序能力**的那一步。

------------------------------------------------------------
用法
------------------------------------------------------------
    # 0) 先看 LLM 配好没有（不联网、不花钱）
    python scripts/nl2spec.py --check

    # 1) 主用法：人话 → 规格文件
    python scripts/nl2spec.py "把 D:\\in 下所有 xlsx 合并成一张表，输出到 D:\\out\\汇总.xlsx"

    # 2) 生成完直接落库真跑（调 ai_flow.py，真建机器人、真执行）
    python scripts/nl2spec.py "..." --run --robot-name 我的新流程

    # 3) 完全离线自证链路（不需要 key、不联网）
    python scripts/nl2spec.py --selftest

    # 4) 回放录制的真实响应（复现某次生成，不花钱）
    python scripts/nl2spec.py "..." --replay docs/reviews/nl2spec_demo

    # 5) 起个 HTTP 接口给别的系统调
    python scripts/nl2spec.py --serve --port 8899

------------------------------------------------------------
LLM 配置（三种来源，优先级从高到低）
------------------------------------------------------------
    1. 环境变量  NL2SPEC_API_KEY / NL2SPEC_BASE / NL2SPEC_MODEL
                 也认 DEEPSEEK_API_KEY / OPENAI_API_KEY 等常见名
    2. rpa_config.json 的 "llm": {"base": "...", "model": "...", "api_key": "..."}
    3. 都没有 → 报错并告诉你填哪里（不会静默降级成关键词匹配，那会骗人）

任何 **OpenAI 兼容** 的 /chat/completions 都能用（DeepSeek、OpenAI、vLLM、Ollama、one-api…）。
"""
import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import flow_generator as fg          # noqa: E402  原子字典 + 规格加载
import spec_lint as sl               # noqa: E402  规格校验（自愈闭环的裁判）

DEFAULT_FLOW_DIR = os.path.join(ROOT, "flows")
DEFAULT_REPLAY_DIR = os.path.join(ROOT, "docs", "reviews")

# provider 预设：没配 base 时按 provider 猜一个
PROVIDERS = {
    "deepseek": ("https://api.deepseek.com/v1", "deepseek-chat"),
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini"),
    "ollama": ("http://127.0.0.1:11434/v1", "qwen2.5:14b"),
    "moonshot": ("https://api.moonshot.cn/v1", "moonshot-v1-32k"),
}


# ============================================================
# 1. LLM 配置
# ============================================================
def _read_rpa_config():
    p = os.path.join(ROOT, "rpa_config.json")
    if not os.path.exists(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def load_llm_cfg(provider=""):
    """返回 (cfg, notes)。cfg 里 api_key 可能是空（--check 时要能解释清楚缺什么）。"""
    notes = []
    llm = (_read_rpa_config().get("llm") or {})
    provider = provider or os.getenv("NL2SPEC_PROVIDER") or llm.get("provider") or "deepseek"
    base, model = PROVIDERS.get(provider, ("", ""))

    def pick_env(*names):
        for n in names:
            v = os.getenv(n)
            if v:
                notes.append("%s ← 环境变量 %s" % (n.lower(), n))
                return v
        return ""

    api_key = pick_env("NL2SPEC_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
                       "AICHAT_API_KEY", "LLM_API_KEY")
    if not api_key and llm.get("api_key"):
        api_key = llm["api_key"]
        notes.append("api_key ← rpa_config.json llm.api_key")
    base = pick_env("NL2SPEC_BASE", "DEEPSEEK_BASE", "OPENAI_BASE_URL") or llm.get("base") or base
    if not base:
        base = PROVIDERS.get(provider, ("", ""))[0]
    model = pick_env("NL2SPEC_MODEL", "DEEPSEEK_MODEL") or llm.get("model") or model

    cfg = {
        "provider": provider,
        "base": (base or "").rstrip("/"),
        "model": model or "",
        "api_key": api_key or "",
        "timeout": int(os.getenv("NL2SPEC_TIMEOUT", "180")),
        "temperature": float(os.getenv("NL2SPEC_TEMPERATURE", "0.2")),
    }
    if not notes:
        notes.append("全部来自内置默认（没有 env，也没有 rpa_config.json 的 llm 段）")
    return cfg, notes


def mask(k):
    if not k:
        return "(空)"
    return "%s…%d 位" % (k[:4], len(k))


def http_chat(cfg, messages, json_mode=True):
    """调 OpenAI 兼容的 /chat/completions。用 requests（项目已有，不引新依赖）。"""
    import requests
    url = cfg["base"] + "/chat/completions"
    body = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": cfg["temperature"],
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    headers = {"Content-Type": "application/json"}
    if cfg["api_key"]:
        headers["Authorization"] = "Bearer " + cfg["api_key"]
    r = requests.post(url, headers=headers, json=body, timeout=cfg["timeout"])
    if r.status_code == 400 and json_mode:
        # 有些兼容实现不认 response_format，去掉重试一次
        body.pop("response_format", None)
        r = requests.post(url, headers=headers, json=body, timeout=cfg["timeout"])
    if r.status_code >= 400:
        raise RuntimeError("LLM 接口返回 %d：%s" % (r.status_code, r.text[:500]))
    data = r.json()
    try:
        return data["choices"][0]["message"]["content"]
    except Exception:
        raise RuntimeError("LLM 响应结构不认识：%s" % json.dumps(data, ensure_ascii=False)[:500])


class LlmClient:
    """薄封装：可注入 transport（自测/回放用），记录每次原始响应。"""

    def __init__(self, cfg, transport=None, recorder=None):
        self.cfg = cfg
        self.transport = transport
        self.recorder = recorder
        self.calls = []

    def chat(self, messages, json_mode=True):
        t0 = time.time()
        if self.transport is not None:
            text = self.transport(messages)
        else:
            text = http_chat(self.cfg, messages, json_mode=json_mode)
        rec = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "model": self.cfg.get("model"),
            "messages": messages,
            "response": text,
            "elapsed": round(time.time() - t0, 2),
        }
        self.calls.append(rec)
        if self.recorder:
            self.recorder(rec)
        return text


# 原子参数陷阱（**全部来自真跑实测**，不是猜的）。
#
# 为什么需要这张表：平台原子字典只给「参数名 + 类型 + 枚举」，**不给语义**。
# 于是「output_path 要目录还是完整文件名」「file_option 默认会不会报错」这类信息
# 在写规格时完全看不见 —— lint 全绿、真跑才炸。
# 每条都是 2026-11-02 nl2spec 真跑踩出来的，写在这里让**生成期**就能避开，
# 而不是每一条都靠真跑试错（真跑要建机器人，几十秒一次）。
ATOM_HINTS = {
    "File.file_write": "`file_option` 默认 `error`：**目标文件不存在会直接报错**。"
                       '写新文件要显式给 `"file_option": "create"`（或先用 File.file_create）。',
    "Docx.convert_format": "`output_path` 是**目录**不是文件路径（文件名走 `output_name`）；"
                           "另：`output_file_type=txt` 在本平台会 `NameError: Document`（平台缺陷），"
                           "要提取文字请用 `Docx.read_docx` + `File.file_write`。",
    "Docx.read_docx": "`doc` 要传 `Docx.open_docx` 的输出（DocumentObject），不能传路径；"
                      "输出 key 是 `doc_data`。",
    "Excel.save_excel": "要覆盖已存在的文件时，`exist_handle_type` 设 `overwrite`，否则会另存一份。",
    "File.file_create": "`dst_path` 是**目录**，文件名放 `file_name`；两个都是必填。",
    "File.get_file_list": "**不能按扩展名过滤**（列全部文件）。只处理某种文件请用 `File.file_search`。",
}


# ============================================================
# 2. 原子索引（给模型看的「有哪些原子」）
# ============================================================
STOP_ZH = {"一个", "这个", "那个", "所有", "可以", "帮我", "我们", "进行", "然后", "以及",
           "文件", "其中", "需要", "要求", "输出", "输入"}

# 域提示词：命中就把**整个域**的原子拉进候选（带详细参数）。
#
# 为什么需要：纯 2-gram 检索会翻车 —— 实测「把 xlsx 合并成一张表」匹配不到
# `Excel.read_excel`（原子的说明里写的是"读取 Excel"，没有"xlsx"这个词），
# 反而因为"合并"命中了 `PDF.merge_pdf_files`。域是比词更稳的先验。
# 各域原子数都不大（最大 Excel 35），整域给详参也就 1-2k token，划算。
DOMAIN_HINTS = [
    ("Excel", ("excel", "xlsx", "xls", "工作表", "工作簿", "单元格", "sheet", "表格",
               "公式", "透视", "排序", "筛选", "列宽", "批注", "合并单元格")),
    ("PDF", ("pdf",)),
    ("Docx", ("word", "docx", "文档", "段落", "页眉")),
    ("File", ("文件名", "重命名", "扩展名", "txt", "读文件", "写文件", "复制", "移动", "删除")),
    ("Folder", ("文件夹", "目录", "遍历", "压缩", "解压", "zip", "批量")),
    ("Network", ("ftp", "http", "下载", "上传", "接口", "请求")),
    ("System", ("截图", "窗口", "进程", "命令", "剪贴板", "打印机", "锁屏")),
    ("Gui", ("鼠标", "键盘", "拖动", "坐标", "屏幕")),
    ("Code", ("循环", "遍历", "条件", "判断", "重试", "异常", "逐个", "每一个")),
    ("StringProcess", ("字符串", "文本", "替换", "截取", "拼接", "拆分", "正则", "大小写", "去掉")),
    ("ListProcess", ("列表", "数组", "去重", "集合")),
    ("DataTable", ("数据表", "行列", "数据表筛选")),
    ("Email", ("邮件", "邮箱", "发送给")),
    ("Script", ("python", "脚本", "子流程", "模块")),
    ("Dialog", ("弹窗", "对话框", "提示框", "选择框")),
    ("MathProcess", ("计算", "数学", "四舍五入")),
    ("TimeProcess", ("时间", "日期", "定时")),
    ("Encrypt", ("加密", "解密", "md5", "哈希")),
    ("WinEle", ("控件", "桌面元素")),
    ("CV", ("图像识别", "找图")),
    ("BrowserSoftware", ("浏览器", "网页", "网址", "url", "打开网站", "登录", "网页截图")),
    ("BrowserElement", ("网页元素", "点击按钮", "输入框", "表单")),
]


def _tokens(text):
    t = (text or "").lower()
    toks = set(re.findall(r"[a-z0-9_]{2,}", t))
    zh = re.sub(r"[^\u4e00-\u9fff]+", " ", t)
    for seg in zh.split():
        if len(seg) < 2:
            continue
        for i in range(len(seg) - 1):
            bg = seg[i:i + 2]
            if bg not in STOP_ZH:
                toks.add(bg)
    return toks


class AtomIndex:
    """只暴露「能用」的原子：剔除死原子与本机无实现的原子。

    这两个名单来自实战（`spec_lint.DEAD_ATOMS` / `docs/atom_missing.json`）：
    **字典里有、但跑了必炸**。不剔除的话 LLM 有概率选到，白白浪费一轮。
    """

    def __init__(self, atoms):
        self.all = atoms
        self.usable = {k: v for k, v in atoms.items()
                       if k not in sl.MISSING_IMPL and k not in sl.DEAD_ATOMS}
        self._docs = {}
        for k, a in self.usable.items():
            doc = " ".join([k, a.get("title") or "", a.get("comment") or ""])
            self._docs[k] = _tokens(doc)
        # 简单 df 统计用于加权（越少见的词越有区分度）
        self._df = {}
        for toks in self._docs.values():
            for tk in toks:
                self._df[tk] = self._df.get(tk, 0) + 1
        self._n = max(1, len(self._docs))

    # ---------- 全量清单（紧凑，一行一个域） ----------
    def overview(self):
        by_dom = {}
        for k, a in self.usable.items():
            dom = k.split(".")[0] if "." in k else "其他"
            title = re.sub(r"\s+", "", (a.get("title") or ""))[:16]
            by_dom.setdefault(dom, []).append("%s(%s)" % (k, title) if title else k)
        lines = []
        for dom in sorted(by_dom, key=lambda d: -len(by_dom[d])):
            lines.append("%s[%d]: %s" % (dom, len(by_dom[dom]), " | ".join(sorted(by_dom[dom]))))
        return "\n".join(lines)

    # ---------- 单个原子详参（压成一两行） ----------
    def detail(self, keys):
        blocks = []
        for k in keys:
            a = self.usable.get(k)
            if not a:
                continue
            blocks.append("- " + k + " — " + (a.get("title") or ""))
            req, opt = [], []
            for p in (a.get("inputList") or []):
                if not isinstance(p, dict) or not p.get("key"):
                    continue
                name = p["key"]
                typ = p.get("types") or ""
                opts = [o.get("value") if isinstance(o, dict) else o for o in (p.get("options") or [])]
                seg = name + ("(" + str(typ) + ")" if typ else "")
                if opts:
                    seg += " 取值:" + "/".join(str(x) for x in opts[:12])
                elif p.get("default") not in (None, ""):
                    seg += " 默认:" + str(p.get("default"))[:24]
                (req if p.get("required") else opt).append(seg)
            if req:
                blocks.append("    必填: " + "; ".join(req))
            if opt:
                blocks.append("    可选: " + "; ".join(opt))
            outs = [p.get("key") for p in (a.get("outputList") or []) if isinstance(p, dict)]
            if outs:
                blocks.append("    输出: " + ", ".join(outs))
            tip = (a.get("comment") or "").strip().splitlines()
            if tip:
                blocks.append("    说明: " + tip[0][:100])
            if k in ATOM_HINTS:
                blocks.append("    ⚠️ " + ATOM_HINTS[k])
        return "\n".join(blocks)

    # ---------- 按需求检索（给详参用，省 token） ----------
    def hit_domains(self, nl):
        low = (nl or "").lower()
        return {d for d, words in DOMAIN_HINTS if any(w in low for w in words)}

    def search(self, nl, k=44):
        """候选原子 = 命中域的全部原子（保证不漏）∪ 词面检索 top（跨域补充）。"""
        toks = _tokens(nl)
        doms = self.hit_domains(nl)
        import math
        scored = {}
        for key, dtoks in self._docs.items():
            dom = key.split(".")[0]
            s = 0.0
            hit = toks & dtoks
            if hit:
                s += sum(math.log(1.0 + self._n / max(1, self._df.get(t, 1))) for t in hit)
            if dom in toks:            # 用户直接点名了域（如 "excel"）
                s += 2.0
            if dom in doms:            # 域提示命中：整域抬到候选线以上
                s += 3.0
            if s > 0:
                scored[key] = s
        ranked = [k2 for k2, _ in sorted(scored.items(), key=lambda x: -x[1])]
        return ranked[:k]


# ============================================================
# 3. 提示词（这是 L3 的「大脑配方」，改这里等于改生成质量）
# ============================================================
SYSTEM_PROMPT = """你是 Astron RPA 的「流程规格（spec）生成器」。用户给一句中文需求，你输出一份 spec JSON。

# 输出纪律
- **只输出一个 JSON 对象**，不要解释、不要 ``` 围栏、不要注释、不要多余文字。
- JSON 必须是能直接 `json.loads` 的严格格式：字符串用双引号，Windows 路径里的反斜杠写成 `\\\\`。

# spec 结构（只认这 5 个字段）
{
  "name": "流程名（中文，别带空格）",
  "nl_examples": ["用户可能怎么说（2-3 条）"],
  "keywords": ["关键词3-5个"],
  "params": [{"name":"src_file","type":"Str","default":"D:\\\\demo\\\\a.xlsx","describe":"源文件"}],
  "nodes": [{"atom":"原子名","alias":"中文节点名","inputs":{...},"out":{"原子输出key":"变量名"}}]
}

# 硬性规则（违反必被校验器打回）
1. `params[].default` **绝对不能是空串**，必须是真实可用的值（设计器拿到空串会直接报 WinError 3）。
2. `nodes[].inputs` 的 key 必须**逐字**来自该原子的参数表，拼错不报错但会静默失效。
3. `out` 的 key 必须逐字来自该原子的「输出」列表；左侧是原子的输出 key，右侧是你起的变量名。
4. 引用写法：
   - `"$p:名字"` 引用流程参数（必须已在 params 里声明）
   - `"$变量名"` 引用**前面节点** out 里起的变量名
   - `["$p:前缀","_","$idx"]` 数组表示字符串拼接
   - `"$json:[...]"` 给需要 JSON 字符串的参数（如 module_param / process_param）
   - `"$item"` / `"$idx"` 只在 `Code.ForList` 循环体内有效
5. 枚举参数只能用参数表里给出的「取值」，**不能写中文描述**。
6. **`Bool` 参数写 JSON 布尔 `true` / `false`，绝对不要写字符串 `"True"` / `"False"`。**
   引擎对非空字符串一律视为真值 —— `"visible_flag": "False"` 会**把窗口显示出来**（正好相反）。
   数字参数同理：写 `3`，不要写 `"3"`。
7. 循环：`Code.ForList` 开头 → 中间是循环体 → 用 `Code.ForEnd` 收尾（**不是** ForListEnd）。
   条件：`Code.If` → `Code.IfEnd`。缺 End 会被校验器判错。
8. **优先用最少节点**：3-6 个节点能做完就别写 10 个。能用 `$p:` 参数解决的，不要用循环变量。
9. 只使用「可用原子清单」里的原子。清单外的原子不存在，写了必被驳回。
10. **路径/文件名参数**（`*_path` / `*_file` / `*_name` / `*_dir`）直接写字符串或 `"$p:x"` 引用，
    **不要**用 `$json:` 包起来 —— `$json:` 是给 list/dict 之类结构化参数用的。

# 选原子容易踩的三个点
- **参数类型是 `DocxObj` / `ExcelObj` / `DocumentObject` / `*Object` 这类「对象」的**，
  要传**上游打开节点的输出**（如 `"doc": "$doc_obj"`），**不能写文件路径**。
  典型：`Docx.convert_format` 的 `doc`、`Docx.read_docx` 的 `doc`、`Excel.*` 的 `excel`。
  走法：先 `Docx.open_docx` / `Excel.open_excel`，把输出变量传给后续节点。
- `File.get_file_list` **不能按扩展名过滤**，它列全部文件。要「只处理某种文件」用 `File.file_search`
  （有 search_pattern），或者列全之后再筛。
- 输出文件**不要放进输入目录**，否则它自己会被当成输入，越跑越大。

# 高频错误（校验器最爱抓这些）
- **布尔参数写成字符串**（`"visible_flag": "False"`）→ 引擎当真值，行为与预期相反，必须写 `false`。
- 引用了没在 params 声明的 `$p:x`，或引用了上游没产出的 `$x`。
- 必填元素参数（WebPick/CvPick 类）没给 → 需要 `"elements": {"参数名": "web"}`，且顶层要有
  `element_requirements` 声明。**没有明确要求就别做网页/桌面元素类流程**（新机器人元素库是空的）。
- `need_parse` 参数（module_param、process_param、部分 options）写成数组 → 必须写成字符串化的 JSON。

# 干活前的自我检查
输出前默念：① 每个 param 的 default 都非空？② 每个 inputs 的 key 都在参数表里？
③ 每个 `$x` 都在前面 out 里出现过？④ 循环都有 ForEnd？⑤ 只有清单内的原子？"""


def feedback_prompt(errors, warns, index, spec):
    """把 lint 报错变成「给模型的修改指令」（自愈闭环的核心）。"""
    parts = ["你上一次输出的 spec 没有通过机器校验。请**逐条修掉下面的错误**，然后重新输出**完整**的 spec JSON。"]
    parts.append("\n【必须修掉的错误】")
    for i, e in enumerate(errors, 1):
        parts.append("%d. %s" % (i, e["msg"]))
        if e.get("fix"):
            parts.append("   → %s" % e["fix"])
    parts.append("\n【这几类错误的标准改法，照做即可】")
    parts.append('· 布尔参数：写 true / false（不加引号）。写成 "True" / "False" / 1 / 0 都是错的 ——')
    parts.append("  引擎把非空字符串当真值，行为会正好相反。")
    parts.append("· 参数名：从下面「准确参数表」里逐字复制，不要自己造名字。")
    parts.append("· 变量引用：`$x` 的 x 必须是前面某个节点 out 里定义过的名字（区分大小写）。")
    parts.append("· 循环变量：循环块的 out 必须写全，例如 "
                 '"out": {"index":"idx","item":"item"}，否则循环体里的 `$item` 拿不到东西。')
    # 附上「当前 spec 的节点 → 产出变量」，让模型有全局视角（它常常只盯着报错那个节点改）
    nodes = spec.get("nodes") or []
    if nodes:
        parts.append("\n【你当前 spec 的节点与产出（左=节点，右=它产出的变量）】")
        for i, n in enumerate(nodes, 1):
            outs = n.get("out") or {}
            produced = ", ".join(v for v in outs.values() if v) or "（无产出）"
            parts.append("%d. %s（%s）→ %s" % (i, n.get("atom"), n.get("alias") or "", produced))
    if warns:
        parts.append("\n【建议一并改掉（不阻塞）】")
        for i, w in enumerate(warns[:8], 1):
            parts.append("%d. %s" % (i, w["msg"]))
            if w.get("fix"):
                parts.append("   → %s" % w["fix"])
    # 错误里点到的原子，把详参补给它（避免它再次拼错参数名）
    keys = set()
    for e in errors:
        for m in re.finditer(r"([A-Z][A-Za-z]+\.[a-z_0-9]+)", e["msg"]):
            keys.add(m.group(1))
    keys = [k for k in keys if k in index.usable][:8]
    if keys:
        parts.append("\n【这些原子的准确参数表】")
        parts.append(index.detail(keys))
    parts.append("\n记住：只输出 JSON 对象本身，不要围栏、不要解释。")
    return "\n".join(parts)


# ============================================================
# 4. JSON 抽取（模型输出不总是干净的）
# ============================================================
def _first_json_object(s):
    """从字符串里抠第一个完整的 {...}（跳过字符串内的花括号）。"""
    start = s.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(s)):
            ch = s[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    cand = s[start:i + 1]
                    for attempt in (cand, re.sub(r",\s*([}\]])", r"\1", cand)):
                        try:
                            obj = json.loads(attempt)
                            if isinstance(obj, dict):
                                return obj
                        except Exception:
                            pass
                    break
        start = s.find("{", start + 1)
    return None


def extract_json(text):
    if not text or not text.strip():
        raise ValueError("模型返回空内容")
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        obj = _first_json_object(m.group(1))
        if obj is not None:
            return obj
    obj = _first_json_object(text)
    if obj is not None:
        return obj
    raise ValueError("没找到合法 JSON（前 120 字：%s）" % text.strip()[:120])


# ============================================================
# 5. 生成 + 自愈闭环
# ============================================================
def build_messages(nl, index, few_shot=None):
    user = ["【需求】", nl.strip(), ""]

    user.append("【可用原子清单】（格式 `原子名(中文名)`，| 分隔；**只能从这里选**）")
    user.append(index.overview())
    user.append("")

    cand = index.search(nl)
    if cand:
        user.append("【本需求最相关的原子详参】")
        user.append(index.detail(cand))
        user.append("")

    if few_shot:
        user.append("【参考结构范例】（**只学结构，路径/参数一律以本次需求为准**）")
        user.append(json.dumps(few_shot, ensure_ascii=False, indent=1)[:3000])
        user.append("")

    user.append("现在输出这份需求的 spec JSON。")
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "\n".join(user)}]


def _pick_few_shot(nl):
    """找一个结构相似的历史规格当范例（只取 8 节点以内的小流程，免得撑爆 prompt）。"""
    try:
        path, score, _why = fg.match_spec_file(nl)
    except Exception:
        return None
    if not path or score < 0.25:
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            sp = json.load(f)
    except Exception:
        return None
    if not isinstance(sp, dict) or not sp.get("nodes") or len(sp["nodes"]) > 8:
        return None
    return {k: sp[k] for k in ("name", "params", "nodes") if k in sp}


def generate(nl, index, client, retries=2, few_shot=None, verbose=True):
    """返回 (spec, history)。history 记录每轮原文、错误、耗时 —— 出问题能复盘。"""
    messages = build_messages(nl, index, few_shot=few_shot)
    history = []
    last_err = None
    for rnd in range(1, retries + 2):
        if verbose:
            print("  · 第 %d 轮：请求模型（%s）..." % (rnd, client.cfg.get("model") or "?"), flush=True)
        raw = client.chat(messages)
        item = {"round": rnd, "raw": raw}
        try:
            spec = extract_json(raw)
        except ValueError as e:
            item["errors"] = [{"msg": "输出不是合法 JSON：%s" % e, "fix": ""}]
            history.append(item)
            last_err = str(e)
            if verbose:
                print("    ❌ 返回的不是 JSON，回灌要求重写")
            messages = messages + [
                {"role": "assistant", "content": (raw or "")[:1500]},
                {"role": "user", "content": "你上面的输出不是合法 JSON（%s）。请直接输出 JSON 对象本身，"
                                            "不要任何解释和围栏。" % e},
            ]
            continue

        errors, warns = sl.lint(spec, index.all)
        item["errors"] = errors
        item["warns"] = warns
        item["nodes"] = len(spec.get("nodes") or [])
        history.append(item)

        if not errors:
            if verbose:
                print("    ✅ 校验通过（%d 节点，%d 条提醒）" % (len(spec.get("nodes") or []), len(warns)))
            return spec, history

        last_err = "%d 个错误" % len(errors)
        if verbose:
            print("    ⚠️ 校验没过（%d 错 / %d 提醒），回灌修复：" % (len(errors), len(warns)))
            for e in errors[:5]:
                print("       - " + e["msg"])
        messages = messages + [
            {"role": "assistant", "content": json.dumps(spec, ensure_ascii=False)},
            {"role": "user", "content": feedback_prompt(errors, warns, index, spec)},
        ]
    raise RuntimeError("自愈 %d 轮仍未通过（最后问题：%s）。看 --dump-dir 里的录制了解每轮细节。"
                       % (retries + 1, last_err))


# ============================================================
# 5.5 真跑验证闭环（静态校验抓不到的那一半）
# ============================================================
#
# 为什么必须有这一层（2026-11-02 实测）：
#   `spec_lint` 只能保证「结构合法、引用正确、原子存在」。它管不了**参数语义**：
#   `Docx.convert_format` 的 `output_path` 要的是**目录**，模型传了完整文件路径 →
#   lint 全绿（IS_PASS: YES），真跑却报「Word文件路径有误」。此外平台还有「某个原子
#   在某个用法下必崩」的缺陷（如 `convert_format` 转 txt 触发 `NameError: Document`），
#   这种只有真跑才暴露。
#   所以 L3 的完整闭环是：**生成 → 静态校验 → 落库真跑 → 拿到真实报错 → 回灌重写**。
VERIFY_FEEDBACK = """你上一次输出的规格**已经写进机器人并真跑了一次**，引擎报错如下（真实运行日志，不是静态检查）：

%s

请根据真实报错修正，重新输出**完整**的 spec JSON。判断思路：
- 若是「参数值不对」（路径有误、文件名已存在、类型不符）→ 改参数值或参数语义。注意平台有些参数
  语义与名字不符，例如导出类原子的 `output_path` 常常要**目录**而不是完整文件名。
- 若是 `NameError` / `AttributeError` / `TypeError` / `KeyError` 这类 → 说明该原子在当前平台版本
  对这个用法有缺陷，**换一条实现路径**（换等价原子组合），不要硬修同一个参数。
- 若是「文件/目录不存在」→ 把参数默认值改成需求里给的路径。
只输出 JSON，不要解释。"""


def _run_ai_flow(spec_path, robot_name, timeout=420):
    """调 ai_flow 落库 + 真跑，返回 (是否成功, 完整日志)。"""
    import subprocess
    cmd = [sys.executable, os.path.join(HERE, "ai_flow.py"), "--spec", spec_path]
    if robot_name:
        cmd += ["--robot-name", robot_name]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "（ai_flow 超时 %ds）" % timeout
    out = (p.stdout or "") + "\n" + (p.stderr or "")
    return ("SUCCESS ✅" in out), out


def _error_digest(log, limit=28):
    """从 ai_flow 的日志里捞出「能指导修改」的行。

    只留报错、真因、失败节点、预检结论 —— 全量日志有几百行，塞给模型既贵又噪。
    """
    keep = []
    for line in (log or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if any(k in s for k in ("FAIL", "真因", "失败发生在", "ERROR", "Error", "error:",
                                "Exception", "预检", "IS_PASS", "No such file",
                                "不存在", "已存在", "路径有误", "不支持", "Traceback")):
            keep.append(s[:300])
    return "\n".join(keep[-limit:]) or (log or "")[-1500:]


def verify_loop(nl, index, client, spec, out_path, robot_name, rounds=2, verbose=True):
    """生成 → 真跑 → 失败回灌 → 再真跑。返回 (spec, log, 用了几轮, 是否最终成功)。"""
    log = ""
    for i in range(rounds + 1):
        save_spec(spec, out_path)
        if verbose:
            print("  · 真跑第 %d 次（%s）..." % (i + 1, robot_name or "默认机器人"), flush=True)
        ok, log = _run_ai_flow(out_path, robot_name)
        if ok:
            if verbose:
                print("    ✅ 真跑 SUCCESS")
            return spec, log, i, True
        digest = _error_digest(log)
        if verbose:
            print("    ❌ 真跑失败，关键报错：")
            for ln in digest.splitlines()[:4]:
                print("       " + ln[:110])
        if i == rounds:
            break
        msgs = build_messages(nl, index) + [
            {"role": "assistant", "content": json.dumps(spec, ensure_ascii=False)},
            {"role": "user", "content": VERIFY_FEEDBACK % digest},
        ]
        if verbose:
            print("    ↻ 把真实报错回灌，让模型改...", flush=True)
        try:
            cand = extract_json(client.chat(msgs))
        except Exception as e:
            if verbose:
                print("    ⚠️ 模型没给出合法 JSON：%s" % e)
            continue
        errs, _w = sl.lint(cand, index.all)
        if errs:
            if verbose:
                print("    ⚠️ 改完静态校验不过（%d 错），先补一轮静态自愈" % len(errs))
            # 静态能修的别浪费一次真跑（真跑要建机器人、几十秒）
            msgs2 = msgs + [
                {"role": "assistant", "content": json.dumps(cand, ensure_ascii=False)},
                {"role": "user", "content": feedback_prompt(errs, _w, index, cand)},
            ]
            try:
                cand2 = extract_json(client.chat(msgs2))
                if not sl.lint(cand2, index.all)[0]:
                    cand = cand2
                    if verbose:
                        print("    ✅ 静态自愈后通过")
            except Exception:
                pass
        spec = cand
    return spec, log, rounds, False


def apply_sets(spec, pairs):
    """`--set 参数=值`：把真实路径/真实列名塞进规格参数默认值。

    为什么需要：模型不知道你的文件在哪，它只会编一个 `D:\\demo\\x.xlsx`。
    与其反复纠正模型，不如让用户一条命令把真实值钉进去（也顺带解决了「平台参数表
    会覆盖默认值」的前置问题 —— 落库时用的就是这里的值）。
    """
    changed = []
    params = {p.get("name"): p for p in (spec.get("params") or []) if p.get("name")}
    for item in pairs or []:
        if "=" not in item:
            continue
        k, v = item.split("=", 1)
        k, v = k.strip(), v.strip()
        if k in params:
            old = params[k].get("default")
            params[k]["default"] = v
            changed.append((k, old, v, "流程参数"))
            continue
        # 不在 params 里：把它当成节点里 `$p:k` 的隐形参数，补一个声明（否则 lint 报未声明）
        for n in spec.get("nodes") or []:
            if any(isinstance(x, str) and x == "$p:" + k for x in (n.get("inputs") or {}).values()):
                spec.setdefault("params", []).append(
                    {"name": k, "type": "Str", "default": v, "describe": "命令指定"})
                changed.append((k, None, v, "新增参数"))
                break
    return changed


# ============================================================
# 6. 落盘 / 录制 / 回放
# ============================================================def slugify(s):
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "_", (s or "").strip())
    return s.strip("_")[:40] or "generated"


def save_spec(spec, out_path):
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return out_path


def make_recorder(dump_dir):
    if not dump_dir:
        return None
    os.makedirs(dump_dir, exist_ok=True)

    def rec(item):
        n = len(os.listdir(dump_dir)) + 1
        p = os.path.join(dump_dir, "call_%02d.json" % n)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(item, f, ensure_ascii=False, indent=1)

    return rec


def make_replay_transport(replay_dir, verbose=True):
    """回放录制好的响应：按轮次顺序取出 `call_*.json` 里的 response。
    用来「不花钱复现某次生成」，也是 CI 里验 prompt 改动影响的手段。"""
    if not os.path.isdir(replay_dir):
        raise SystemExit("回放目录不存在：%s" % replay_dir)
    files = sorted(f for f in os.listdir(replay_dir) if re.match(r"call_\d+\.json$", f))
    if not files:
        raise SystemExit("回放目录里没有 call_*.json：%s" % replay_dir)
    texts = []
    for fn in files:
        with open(os.path.join(replay_dir, fn), "r", encoding="utf-8") as f:
            texts.append(json.load(f).get("response") or "")
    state = {"i": 0}
    if verbose:
        print("  · 回放模式：%d 条录制响应（不联网）" % len(texts))

    def transport(_messages):
        i = state["i"]
        state["i"] = i + 1
        return texts[min(i, len(texts) - 1)]

    return transport


# ============================================================
# 7. 离线自证（不需要 key，验证「抽取→校验→回灌→收敛」真的通）
# ============================================================
SELFTEST_GOOD = {
    "name": "自测_拆分去重拼接",
    "nl_examples": ["把一段文本按逗号拆开、去重、再用横线拼回去"],
    "keywords": ["自测", "拆分", "去重"],
    "params": [
        {"name": "text", "type": "Str", "default": "c,a,b,a", "describe": "待处理文本"},
        {"name": "sep", "type": "Str", "default": ",", "describe": "分隔符"},
    ],
    "nodes": [
        {"atom": "StringProcess.split_string_to_list", "alias": "拆成列表",
         "out": {"split_list_from_string": "parts"},
         "inputs": {"string_data": "$p:text", "separator": "$p:sep"}},
        {"atom": "ListProcess.get_unique_list", "alias": "去重",
         "out": {"unique_list_data": "uniq"},
         "inputs": {"list_data": "$parts"}},
        {"atom": "StringProcess.merge_list_to_string", "alias": "拼回字符串",
         "out": {"merged_string_from_list": "joined"},
         "inputs": {"list_data": "$uniq", "separator": "-"}},
    ],
}


def _selftest_bad_copy():
    """一份「像模型第一次输出」的坏规格：参数名拼错 + 引用了没产出的变量。"""
    bad = json.loads(json.dumps(SELFTEST_GOOD, ensure_ascii=False))
    bad["nodes"][0]["inputs"] = {"string_data": "$p:text", "separatorr": "$p:sep"}   # 拼错
    bad["nodes"][1]["inputs"] = {"list_data": "$part"}                              # 变量名错
    return bad


def selftest(verbose=True):
    print("=" * 64)
    print("nl2spec 离线自证（不联网、不需要 API key）")
    print("=" * 64)
    ok = True

    atoms = fg.load_atom_dict(offline=True)
    if not atoms:
        print("❌ 原子字典为空（docs/atom_dict_cache.json 缺失）")
        return 2
    index = AtomIndex(atoms)
    print("[1/6] 原子字典：总 %d，可用 %d（已剔除死原子 %d / 无实现 %d）"
          % (len(atoms), len(index.usable), len(sl.DEAD_ATOMS), len(sl.MISSING_IMPL)))

    # 干净的模型输出一定能过 lint（否则是样例本身坏了）
    e0, _ = sl.lint(json.loads(json.dumps(SELFTEST_GOOD, ensure_ascii=False)), atoms)
    print("[2/6] 正确规格预先体检：%s" % ("✅ 0 错" if not e0 else "❌ %d 错" % len(e0)))
    if e0:
        for e in e0:
            print("      - " + e["msg"])
        ok = False

    bad = _selftest_bad_copy()
    e1, _ = sl.lint(bad, atoms)
    print("[3/6] 故意写坏的规格：lint 抓到 %d 个错误 %s" % (len(e1), "✅" if e1 else "❌ 一个都没抓到"))
    for e in e1[:3]:
        print("      - " + e["msg"])
    ok = ok and bool(e1)

    # 关键：模型的「第二次回答」要能收到错误信息（不然自愈是假的）
    state = {"n": 0, "saw_feedback": False}

    def stub(messages):
        state["n"] += 1
        user = messages[-1]["content"]
        if state["n"] == 1:
            return "好的，下面是规格：\n```json\n" + json.dumps(bad, ensure_ascii=False) + "\n```"
        if "必须修掉的错误" in user:
            state["saw_feedback"] = True
        return json.dumps(SELFTEST_GOOD, ensure_ascii=False)

    cfg = {"provider": "stub", "base": "", "model": "stub", "api_key": "", "timeout": 10, "temperature": 0}
    client = LlmClient(cfg, transport=stub)
    spec, hist = generate("把一段文本拆开去重再拼回去", index, client, retries=1, verbose=verbose)
    rounds = len(hist)
    print("[4/6] 自愈闭环：%d 轮收敛，第 2 轮收到错误回灌：%s"
          % (rounds, "✅" if state["saw_feedback"] else "❌"))
    ok = ok and rounds == 2 and state["saw_feedback"]

    e2, _ = sl.lint(spec, atoms)
    print("[5/6] 收敛后的规格：%s" % ("✅ 0 错" if not e2 else "❌ %d 错" % len(e2)))
    ok = ok and not e2

    # 落盘 + 读回（走正式链路，确保 ai_flow 能直接吃）
    tmp = os.path.join(ROOT, "logs", "nl2spec_selftest.json")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    save_spec(spec, tmp)
    back = fg.load_spec(tmp)
    same = (len(back["nodes"]) == len(spec["nodes"])
            and [n["atom"] for n in back["nodes"]] == [n["atom"] for n in spec["nodes"]])
    print("[6/6] 落盘 → load_spec 读回：%s（%s）" % ("✅ 一致" if same else "❌ 不一致",
                                                os.path.relpath(tmp, ROOT)))
    ok = ok and same

    print("\n" + ("✅ 自证通过：生成链路（抽取 / 校验 / 回灌 / 收敛 / 落盘）离线可跑通。"
                  if ok else "❌ 自证失败，看上面哪一步是 ❌。"))
    print("   注意：这只证明**链路通**，不证明**模型生成的流程对**。")
    print("   判断后者必须配真 key 跑真实需求，并核对产出文件。")
    return 0 if ok else 1


# ============================================================
# 8. HTTP 接口（给别的系统调）
# ============================================================
def serve(index, client, port=8899, retries=2):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def _json(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.rstrip("/") in ("", "/health"):
                return self._json(200, {"ok": True, "model": client.cfg.get("model"),
                                        "usable_atoms": len(index.usable)})
            self._json(404, {"ok": False, "error": "GET /health 才有东西"})

        def do_POST(self):
            if not self.path.rstrip("/").endswith("/nl2spec"):
                return self._json(404, {"ok": False, "error": "POST /nl2spec"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                req = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            except Exception as e:
                return self._json(400, {"ok": False, "error": "body 不是 JSON：%s" % e})
            nl = (req.get("nl") or "").strip()
            if not nl:
                return self._json(400, {"ok": False, "error": "缺少 nl 字段"})
            try:
                spec, hist = generate(nl, index, client, retries=retries, verbose=False)
            except Exception as e:
                return self._json(502, {"ok": False, "error": str(e)})
            out = ""
            if req.get("save", True):
                out = req.get("out") or os.path.join(DEFAULT_FLOW_DIR, slugify(spec.get("name")) + ".json")
                save_spec(spec, out)
            return self._json(200, {"ok": True, "spec": spec, "path": out,
                                    "rounds": len(hist), "model": client.cfg.get("model")})

        def log_message(self, fmt, *args):
            sys.stderr.write("[nl2spec] " + (fmt % args) + "\n")

    print("nl2spec HTTP 服务已启动：http://127.0.0.1:%d" % port)
    print("  GET  /health")
    print("  POST /nl2spec   body: {\"nl\": \"把 D:\\\\in 下的 xlsx 合并成一张\", \"save\": true}")
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
    return 0


# ============================================================
# 9. CLI
# ============================================================
def cmd_check(cfg, notes, index):
    print("=" * 64)
    print("nl2spec 环境体检")
    print("=" * 64)
    print("provider : %s" % cfg["provider"])
    print("base     : %s" % (cfg["base"] or "❌ 空"))
    print("model    : %s" % (cfg["model"] or "❌ 空"))
    print("api_key  : %s" % mask(cfg["api_key"]))
    for n in notes:
        print("           来源：%s" % n)
    print("原子可用 : %d 个" % len(index.usable))
    if not cfg["api_key"] and "ollama" not in (cfg["base"] or ""):
        print("\n❌ 没有 API key。三选一：")
        print("   ① 环境变量：set NL2SPEC_API_KEY=sk-xxx")
        print('   ② rpa_config.json 加一段："llm": {"provider":"deepseek","api_key":"sk-xxx"}')
        print("   ③ 本地 Ollama（免 key）：set NL2SPEC_BASE=http://127.0.0.1:11434/v1")
        return 2
    if not cfg["base"] or not cfg["model"]:
        print("\n❌ base 或 model 为空。")
        return 2
    print("\n✅ 配置齐全。下一步：")
    print('   python scripts/nl2spec.py "把 D:\\in 下的 xlsx 合并成一张" --dry')
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="自然语言直出 RPA 流程规格（L3）：人话 → LLM 出 spec → 校验 → 自愈 → 落盘",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="例：\n"
               '  python scripts/nl2spec.py "把 D:\\\\in 下的 xlsx 合并成一张"\n'
               '  python scripts/nl2spec.py --selftest\n'
               "  python scripts/nl2spec.py --check\n")
    ap.add_argument("nl", nargs="*", help="自然语言需求（可带空格，不用加引号也行）")
    ap.add_argument("--out", default="", help="规格输出路径（默认 flows/gen_<名字>.json）")
    ap.add_argument("--provider", default="", help="deepseek / openai / ollama / moonshot")
    ap.add_argument("--model", default="", help="覆盖模型名")
    ap.add_argument("--base", default="", help="覆盖接口 base（OpenAI 兼容）")
    ap.add_argument("--retries", type=int, default=2, help="自愈轮数（默认 2，即最多 3 次请求）")
    ap.add_argument("--no-few-shot", action="store_true", help="不给相似历史规格当范例")
    ap.add_argument("--dry", action="store_true", help="只生成校验，不写文件")
    ap.add_argument("--dump-dir", default="", help="把每轮请求/响应录到目录（可 --replay 复现）")
    ap.add_argument("--replay", default="", help="回放录制目录（不联网、不花钱）")
    ap.add_argument("--run", action="store_true", help="生成后调 ai_flow 落库 + 本地真跑（跑挂了不会自动改）")
    ap.add_argument("--verify", action="store_true",
                    help="真跑验证闭环：落库真跑 → 失败把真实报错回灌给模型重写（推荐）")
    ap.add_argument("--verify-rounds", type=int, default=2, help="--verify 的修复轮数（默认 2）")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="覆盖流程参数的默认值（真实路径/列名），可多次：--set src_file=F:\\a.xlsx")
    ap.add_argument("--robot-name", default="", help="配合 --run：按名字找/建机器人")
    ap.add_argument("--offline", action="store_true", help="用本地原子缓存（不连 .130）")
    ap.add_argument("--check", action="store_true", help="只体检 LLM 配置")
    ap.add_argument("--selftest", action="store_true", help="离线自证链路（不需要 key）")
    ap.add_argument("--serve", action="store_true", help="起 HTTP 接口")
    ap.add_argument("--port", type=int, default=8899)
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    atoms = fg.load_atom_dict(offline=args.offline)
    if not atoms:
        print("❌ 原子字典为空（服务端不可达且无缓存）")
        return 2
    index = AtomIndex(atoms)

    cfg, notes = load_llm_cfg(args.provider)
    if args.model:
        cfg["model"] = args.model
    if args.base:
        cfg["base"] = args.base.rstrip("/")

    if args.check:
        return cmd_check(cfg, notes, index)

    if args.serve:
        return serve(index, LlmClient(cfg), port=args.port, retries=args.retries)

    nl = " ".join(args.nl).strip()
    if not nl:
        ap.error("给一句需求，或加 --selftest / --check / --serve")

    transport = make_replay_transport(args.replay) if args.replay else None
    if not transport and not cfg["api_key"] and "127.0.0.1" not in (cfg["base"] or ""):
        print("❌ 没有 LLM API key，生成不了。先跑 `--check` 看怎么配。")
        print("   （想先看链路通不通：python scripts/nl2spec.py --selftest）")
        return 2

    client = LlmClient(cfg, transport=transport, recorder=make_recorder(args.dump_dir))

    print("=" * 64)
    print("nl2spec · 自然语言 → 流程规格")
    print("=" * 64)
    print("需求：%s" % nl)
    print("模型：%s @ %s" % (cfg["model"],
                             "回放（不联网）" if transport else (cfg["base"] or "(stub)")))
    print("-" * 64)

    few_shot = None if args.no_few_shot else _pick_few_shot(nl)
    if few_shot:
        print("  · 参考范例：%s（只借结构）" % few_shot.get("name"))

    t0 = time.time()
    try:
        spec, hist = generate(nl, index, client, retries=args.retries, few_shot=few_shot)
    except Exception as e:
        print("\n❌ 生成失败：%s" % e)
        return 3
    elapsed = time.time() - t0

    print("-" * 64)
    print("✅ 通过校验（%.1fs / %d 轮 / %d 请求）" % (elapsed, len(hist), len(client.calls)))
    print("流程名：%s" % spec.get("name"))
    print("节点 %d 个：" % len(spec.get("nodes") or []))
    for i, n in enumerate(spec.get("nodes") or [], 1):
        print("  %2d. %-28s %s" % (i, n.get("atom"), n.get("alias") or ""))

    out = args.out or os.path.join(DEFAULT_FLOW_DIR, "gen_" + slugify(spec.get("name")) + ".json")

    # 把真实路径/真实列名钉进参数默认值（模型只会编 D:\demo\x.xlsx）
    if args.set:
        changed = apply_sets(spec, args.set)
        if changed:
            print("\n参数按命令覆盖：")
            for k, old, new, kind in changed:
                print("  · %s: %s → %s（%s）" % (k, old if old else "(空)", new, kind))
        else:
            print("\n⚠️ --set 没有命中任何参数：%s" % ", ".join(args.set))

    if args.verify:
        print("\n" + "=" * 64)
        print("真跑验证闭环（生成 → 落库真跑 → 失败回灌重写）")
        print("=" * 64)
        if args.dry:
            print("(--dry 与 --verify 互斥，--verify 会落库)")
            return 2
        spec, log, used, ok = verify_loop(nl, index, client, spec, out,
                                          args.robot_name, rounds=args.verify_rounds)
        save_spec(spec, out)
        print("-" * 64)
        if ok:
            print("✅ 真跑通过（额外 %d 轮修复）· 规格：%s" % (used, os.path.relpath(out, ROOT)))
            print("\n⚠️ SUCCESS 不等于做对了 —— 必须人工核对产出文件：")
            print("   路径、内容、条数是否符合预期；再进设计器点一次「运行」确认。")
            return 0
        print("❌ 真跑仍未通过（%d 轮修复用完）· 规格：%s" % (used, os.path.relpath(out, ROOT)))
        print("\n最后一次真实报错：")
        for ln in _error_digest(log).splitlines()[:10]:
            print("  " + ln[:150])
        print("\n下一步（三选一）：")
        print("  ① 补真实路径后重试：加 --set src_file=F:\\真实\\路径.xlsx")
        print("  ② 换实现路径：这条需求的正确解法可能不是原子拼装（如需要 Python 子流程模块）")
        print("  ③ 直接改规格：%s 改完跑 python scripts/ai_flow.py --spec %s"
              % (os.path.relpath(out, ROOT), os.path.relpath(out, ROOT)))
        return 4

    if args.dry:
        print("\n(--dry 未写文件；去掉 --dry 才会落盘到 %s)" % os.path.relpath(out, ROOT))
        return 0
    save_spec(spec, out)
    print("\n规格已写入：%s" % os.path.relpath(out, ROOT))
    if args.dump_dir:
        print("录制目录：%s（可用 --replay 复现）" % os.path.relpath(args.dump_dir, ROOT))
    print("下一步：")
    print("  python scripts/spec_lint.py %s" % os.path.relpath(out, ROOT))
    print("  python scripts/ai_flow.py --spec %s --robot-name <机器人名>" % os.path.relpath(out, ROOT))

    if args.run:
        print("\n" + "-" * 64)
        print("落库 + 本地真跑（ai_flow.py）")
        print("-" * 64)
        ok, log = _run_ai_flow(out, args.robot_name)
        print(log.strip().splitlines()[-1] if log.strip() else "")
        if not ok:
            print("⚠️ 真跑没过（规格本身已通过 lint，问题多在：参数语义 / 路径存在性 / 机器人 / 元素）")
            print("   想让模型自己修：改用 --verify")
            return 4
        print("⚠️ 别把 SUCCESS 当成功 —— 去核对产出文件。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
