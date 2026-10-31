# -*- coding: utf-8 -*-
"""把一句自然语言里的**具体值**抽出来，填进流程规格的参数。

## 为什么需要这一层

`flow_generator.match_spec_file()` 只回答「这句话像哪条流程」。但用户说
「把 `D:\\单.pdf` 还原版面」时，光选中流程没用 —— 参数还是规格里的示例值，
跑出来是**另一份文件**。用户会觉得「它没听懂我说的是哪个文件」。

这一层的职责就一件事：**话里说了什么，就改成什么**。

## 抽取什么

| 类型 | 例子 |
| --- | --- |
| 源文件 | `把 F:\\demo\\报废单.pdf 还原版面` → `src_pdf` |
| 目录 | `把 F:\\demo\\in 里的 pdf 合成一个` → `in_dir` |
| 页码 | `只保留第 1 到 2 页` / `前 3 页` / `抽第 5 页` → `page_range` |
| 缩放 | `放大 30%` → `scale` |
| 输出名 | `另存成 报销单` / `文件名改成 最终版` → `out_name` |

## 两条推导规则（用户没明说，但显然想要的）

1. **换源文件后，输出名跟着源文件名走**：`out_name` 原来是
   `20260914报废_版面还原`（= 旧源文件名 + `_版面还原`），换了源文件就变成
   `<新源文件名>_版面还原`。否则跑完出来一个名字对不上的文件。
2. **换源文件后，输出目录跟着源目录走**：用户没单独指定 `out_dir` 时，
   产物落到源文件旁边，而不是规格里写死的旧 demo 目录。

两条推导都会在命令行里**打印原因**，不静默改。

## 不做什么

- **不做批量**。`「把这个文件夹的 PDF 都转成 Word」` 会被识别成批量意图并
  **明确拒绝**（见 `is_batch()`），而不是偷偷只转第一个文件。批量需要
  `File.file_list` + 循环节点，属于流程结构问题，不在参数层解决。
- **不猜。** 抽不到就不改，用规格默认值，并说明「没抽到，用默认」。
"""
import os
import re

# 能当「流程源文件」的扩展名
_EXTS = ("pdf", "docx", "doc", "xlsx", "xls", "csv", "txt", "png", "jpg", "jpeg", "bmp")

# 绝对路径：X:\... 或 X:/...（允许中文，不允许空格——带空格必须加引号）
_ABS = re.compile(r"[A-Za-z]:[\\/][^\s\"'“”‘’<>|，。；、：？！]*")

# 裸文件名（相对路径）：报废单.pdf / 订单.xlsx
_BARE = re.compile(
    r"(?<![\w\\/:.])"
    r"([\w\u4e00-\u9fa5][\w\u4e00-\u9fa5\-_.()（） ]{0,80}?\."
    r"(?:pdf|docx|doc|xlsx|xls|csv|txt))"
    r"(?![\w])",
    re.I)

_EXT_RE = re.compile(r"\.(" + "|".join(_EXTS) + r")", re.I)

# 页码：第1到2页 / 1-2页 / 前3页 / 第5页
_PAGE_RANGE = [
    (re.compile(r"第?\s*(\d+)\s*页?\s*[-~～至到]\s*(\d+)\s*页"), lambda a, b: f"{a}-{b}"),
    (re.compile(r"前\s*(\d+)\s*页"), lambda a, b: f"1-{a}"),
    (re.compile(r"第\s*(\d+)\s*页"), lambda a, b: f"{a}"),
    (re.compile(r"(\d+)\s*[-~～]\s*(\d+)\s*页"), lambda a, b: f"{a}-{b}"),
]

_SCALE = re.compile(r"(?:放大|缩放|比例|scale)\D{0,4}(\d{1,3})\s*%", re.I)

_OUTNAME = re.compile(
    r"(?:另存为|另存成|另存|存成|存为|命名为|文件名(?:叫|是|为|改成)|改名为|输出名(?:叫|是|为)|重命名为)"
    r"\s*[「“\"『']?([^」”\"』'\s，。；、]+)")

# 批量意图词。**故意不含「文件夹」「整个文件夹」**——「把这个文件夹里的PDF合并成一个」
# 也提到文件夹，但它是单次操作；只有「都/全部/每个/批量」才是真的要对每个文件做一遍。
_BATCH_WORDS = ("都", "全部", "所有", "批量", "每个", "逐个", "遍历", "每一个")

# 参数名 → 角色。固定值类参数（脚本路径/语言/模块 id）永远不动
_FIXED_NAMES = {"lang", "module_id", "ps1", "img_prefix", "txt_name", "img_name"}


_ASCII_SEG = re.compile(r"^[A-Za-z0-9_.\-]+")
_CJK = re.compile(r"[\u4e00-\u9fa5]")


def _norm_slashes(p):
    """`X:/a/b` → `X:\\a\\b`，并把重复分隔符压成一个（和坑 64 同规则）。"""
    if re.match(r"^[A-Za-z]:[\\/]", p or ""):
        return re.sub(r"[\\/]{2,}", r"\\", p.replace("/", "\\"))
    return p


def _trim_path(p):
    """把「路径 + 后面粘着的动词」切开。

    `F:\\demo\\a.pdf转成word` → `F:\\demo\\a.pdf`
    `F:\\demo\\pdf_merge_in里的pdf合成一个` → `F:\\demo\\pdf_merge_in`
    `F:\\demo\\in` → `F:\\demo\\in`（纯 ASCII 目录名，原样保留）
    """
    p = p.strip().strip("\"'“”‘’")
    m = _EXT_RE.search(p)
    if m:
        return p[:m.end()]

    # 目录：最后一段如果混进了中文，说明后面粘了动词
    i = max(p.rfind("\\"), p.rfind("/"))
    seg = p[i + 1:] if i >= 0 else p
    if not _CJK.search(seg):
        return p                      # 干净的目录名，别动
    head = _ASCII_SEG.match(seg)
    if head and head.end() >= 2:
        return p[:i + 1] + head.group(0)      # `pdf_merge_in里的pdf…` → `pdf_merge_in`
    cut = p[:i] if i > 2 else p
    return cut if len(cut) > 3 else p         # 纯中文目录名，整段保留


def find_paths(nl):
    """从话里找路径，分成「文件」和「目录」两类。保持出现顺序。"""
    nl = nl or ""
    files, dirs, seen = [], [], set()

    for m in _ABS.finditer(nl):
        raw = m.group(0)
        p = _norm_slashes(_trim_path(raw))
        if len(p) < 4:
            continue
        key = p.lower().rstrip("\\/")
        if key in seen:
            continue
        seen.add(key)
        if _EXT_RE.search(os.path.basename(p)):
            files.append(p)
        else:
            dirs.append(p.rstrip("\\/"))

    # 裸文件名：把绝对路径里已经出现过的名字排除掉
    abs_low = [f.lower() for f in files]
    for m in _BARE.finditer(nl):
        name = m.group(1).strip()
        if not name or len(name) < 4:
            continue
        if any(a.endswith(name.lower()) for a in abs_low):
            continue
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        files.append(name)

    return files, dirs


def find_page_range(nl):
    for rx, fn in _PAGE_RANGE:
        m = rx.search(nl or "")
        if m:
            return fn(m.group(1), m.group(2) if m.lastindex and m.lastindex >= 2 else "")
    return ""


def find_scale(nl):
    m = _SCALE.search(nl or "")
    return int(m.group(1)) if m else None


def find_out_name(nl):
    m = _OUTNAME.search(nl or "")
    if not m:
        return ""
    name = m.group(1).strip()
    # 用户可能把扩展名也写进来，去掉（out_name 不带扩展名）
    return _EXT_RE.sub("", name) if _EXT_RE.search(name) else name


def is_batch(nl):
    """这句话是不是「批量处理整个文件夹」的意思。"""
    s = nl or ""
    return any(w in s for w in _BATCH_WORDS)


def _role(p):
    """判断一个参数是「源文件 / 目录 / 输出名 / 页码 / 固定值」。"""
    n = (p.get("name") or "").lower()
    d = str(p.get("default") or "")
    ext = os.path.splitext(d)[1].lower().lstrip(".")
    if n == "page_range":
        return "pagerange"
    if n in _FIXED_NAMES:
        return "fixed"
    if n.endswith("_dir") or n == "out_dir":
        return "dir"
    if n == "out_name":
        return "outname"
    if ext in _EXTS:
        return "file"
    return "other"


def _has_loop(spec):
    """规格里有没有循环节点（决定能不能接批量意图）。"""
    for n in spec.get("nodes") or []:
        atom = (n.get("atom") or "").lower()
        if any(k in atom for k in ("for", "loop", "while", "foreach", "iterate")):
            return True
    return False


_IDX_CACHE = {}


def _get_index(root):
    key = os.path.abspath(root).lower()
    if key not in _IDX_CACHE:
        _IDX_CACHE[key] = _index_files(root)
    return _IDX_CACHE[key]


def _resolve_relative(name, root=None):
    """相对文件名 → 项目里的绝对路径。找不到就返回空串（不瞎猜）。"""
    root = os.path.abspath(root or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    for base in (root, os.path.join(root, "demo"), os.path.join(root, "scripts")):
        p = os.path.join(base, name)
        if os.path.exists(p):
            return p
    hits = _get_index(root).get(os.path.basename(name).lower()) or []
    return hits[0] if len(hits) == 1 else ""


def apply(nl, spec, root=None):
    """把自然语言里的值落进 `spec["params"][*]["default"]`（就地修改）。

    返回 `{"applied": {参数: {"old","new","why"}}, "warnings": [...], "skipped": [...]}`。
    `applied` 只记**真的变了**的参数，方便命令行逐条打印。
    """
    res = {"applied": {}, "warnings": [], "skipped": []}
    params = {p["name"]: p for p in (spec.get("params") or []) if p.get("name")}
    if not params:
        return res

    files, dirs = find_paths(nl)
    explicit_out = find_out_name(nl)

    def setp(name, val, why):
        p = params.get(name)
        if not p:
            return
        old = str(p.get("default") or "")
        val = _norm_slashes(str(val))
        if val == old:
            return
        p["default"] = val
        res["applied"][name] = {"old": old, "new": val, "why": why}

    src_names = [n for n, p in params.items() if _role(p) == "file" and n.startswith("src")]
    aux_names = [n for n, p in params.items() if _role(p) == "file" and not n.startswith("src")]
    dir_names = [n for n, p in params.items() if _role(p) == "dir"]
    in_dir = next((n for n in dir_names if n.startswith("in")), "")
    out_dir = next((n for n in dir_names if n == "out_dir"), "")

    # ---------- 1. 源文件 ----------
    src_param, src_new, old_src = "", "", ""
    if files and src_names:
        pick = None
        for f in files:
            e = os.path.splitext(f)[1].lower()
            for n in src_names:
                if os.path.splitext(str(params[n].get("default") or ""))[1].lower() == e:
                    pick = (n, f)
                    break
            if pick:
                break
        if not pick:
            pick = (src_names[0], files[0])
            res["warnings"].append(
                f"话里的文件扩展名和 `{src_names[0]}` 默认值的扩展名不一样，仍按位置填了，请核对")
        src_param, src_new = pick
        # 裸文件名（`订单.xlsx`）要落成绝对路径，否则子流程按当前工作目录找 → 找不到
        if not re.match(r"^[A-Za-z]:[\\/]", src_new):
            abs_p = _resolve_relative(src_new, root)
            if abs_p:
                src_new = abs_p
            else:
                res["warnings"].append(
                    f"话里的 `{src_new}` 是相对路径，项目里没找到同名文件，原样传给流程"
                    f"（子流程会按当前目录找，可能报「路径不存在」）")
        old_src = str(params[src_param].get("default") or "")
        setp(src_param, src_new, "话里给的源文件")
    elif files and not src_names:
        res["skipped"].append(f"话里有文件 `{files[0]}`，但这条流程没有「源文件」参数")

    # ---------- 2. 目录 ----------
    old_src_dir = os.path.dirname(old_src) if old_src else ""
    new_src_dir = os.path.dirname(src_new) if src_new and os.path.dirname(src_new) else ""
    if dirs:
        if len(dirs) >= 2 and in_dir:
            setp(in_dir, dirs[0], "话里给的第 1 个目录")
            if out_dir:
                setp(out_dir, dirs[1], "话里给的第 2 个目录")
        else:
            if in_dir:
                setp(in_dir, dirs[0], "话里给的目录")
            if out_dir:
                setp(out_dir, dirs[0], "话里给的目录")
    elif new_src_dir and old_src_dir and \
            os.path.normcase(new_src_dir) != os.path.normcase(old_src_dir):
        # 推导规则 2：换了源文件，输出默认落到源文件旁边
        if out_dir:
            setp(out_dir, new_src_dir, "跟着新源文件的目录（用户没单独指定输出目录）")

    # ---------- 3. 输出名 ----------
    if explicit_out:
        if "out_name" in params:
            setp("out_name", explicit_out, "话里指定的输出名")
    elif src_param and "out_name" in params:
        old_stem = os.path.splitext(os.path.basename(old_src))[0]
        new_stem = os.path.splitext(os.path.basename(src_new))[0]
        old_out = str(params["out_name"].get("default") or "")
        if new_stem and old_stem and old_out and not old_out.startswith("_") \
                and new_stem != old_stem:
            # 推导规则 1：把旧源文件名换成新源文件名，后缀保留
            if old_out.startswith(old_stem):
                suffix = old_out[len(old_stem):]
                setp("out_name", new_stem + suffix, "按新源文件名 + 原后缀推导")
            elif "_" in old_out:
                suffix = old_out.split("_", 1)[1]
                setp("out_name", new_stem + "_" + suffix, "按新源文件名 + 原后缀推导")
            else:
                setp("out_name", new_stem, "按新源文件名推导")

    # ---------- 4. 中间文件跟着输出目录走 ----------
    cur_out_dir = str(params[out_dir].get("default") or "") if out_dir else ""
    if cur_out_dir:
        for n in aux_names:
            p = params[n]
            d = str(p.get("default") or "")
            base = os.path.basename(d)
            if not base:
                continue
            old_parent = os.path.dirname(d)
            if old_parent and os.path.normcase(old_parent) != os.path.normcase(cur_out_dir):
                # 只在「它原来跟输出目录在一起」时才搬
                if old_src_dir and os.path.normcase(old_parent) == os.path.normcase(old_src_dir):
                    setp(n, os.path.join(cur_out_dir, base), "跟着输出目录一起搬")

    # ---------- 5. 页码 / 缩放 ----------
    if "page_range" in params:
        pr = find_page_range(nl)
        if pr:
            setp("page_range", pr, "话里指定的页码")
    if "scale" in params:
        sc = find_scale(nl)
        if sc:
            setp("scale", sc, "话里指定的缩放比例")

    # ---------- 6. 批量意图：明确拒绝，不偷偷只处理一个 ----------
    if is_batch(nl) and not _has_loop(spec):
        res["warnings"].append(
            "这句是**批量**意图（整个文件夹），但这条流程是**单文件**模式，只会处理一个文件。"
            "批量需要「列出文件 + 循环」节点，属于流程结构改造，当前没做。"
            "确认只处理一个文件再加 `--skip-batch-check` 继续。")

    return res


# ----------------------------- 迁移用：路径重挂 -----------------------------

_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".workbuddy-ai", "_archive", ".idea", ".vscode"}


def _index_files(root, max_depth=4):
    """在项目目录里建「文件名 → 绝对路径」索引（迁移时用）。"""
    idx = {}
    root = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        depth = dirpath[len(root):].count(os.sep)
        if depth >= max_depth:
            dirnames[:] = []
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            idx.setdefault(fn.lower(), []).append(os.path.join(dirpath, fn))
    return idx


def rebase_paths(spec, root=None):
    """迁移用：规格里的绝对路径在这台机器上不存在时，按文件名在项目里找回来。

    **只处理「文件名唯一命中」**——命中多个说明有歧义，原样留着并报出来；
    一个都没命中也不动。这样换机器后，`win_ocr.ps1`、`demo/xxx.pdf` 这类
    项目内文件能自动重挂，不需要人一个个改规格。

    返回 `{"rebased": {参数: {old,new}}, "ambiguous": [...], "missing": [...]}`。
    """
    res = {"rebased": {}, "ambiguous": [], "missing": []}
    root = os.path.abspath(root or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    if not os.path.isdir(root):
        return res
    idx = _get_index(root)

    def fix(val):
        if not isinstance(val, str) or not re.match(r"^[A-Za-z]:[\\/]", val):
            return val
        if os.path.exists(val):
            return val
        base = os.path.basename(val)
        if not base:
            return val
        hits = idx.get(base.lower()) or []
        if len(hits) == 1:
            res["rebased"][val] = hits[0]
            return hits[0]
        if len(hits) > 1:
            res["ambiguous"].append((val, hits))
        else:
            res["missing"].append(val)
        return val

    for p in spec.get("params") or []:
        p["default"] = fix(p.get("default"))
    for n in spec.get("nodes") or []:
        for k, v in list((n.get("inputs") or {}).items()):
            if isinstance(v, str):
                n["inputs"][k] = fix(v)
            elif isinstance(v, list):
                n["inputs"][k] = [fix(x) if isinstance(x, str) else x for x in v]
    return res


def format_rebase(res):
    """把 rebase 结果排成人能读的几行。"""
    lines = []
    for old, new in (res.get("rebased") or {}).items():
        lines.append(f"      重挂路径：{old} → {new}")
    for old, hits in (res.get("ambiguous") or [])[:3]:
        lines.append(f"      ⚠️ 路径有歧义（同名文件多个）：{old} → 候选 {len(hits)} 个，未改动")
    return lines
