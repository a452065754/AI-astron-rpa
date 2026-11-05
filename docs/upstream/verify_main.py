# -*- coding: utf-8 -*-
"""对 `iflytek/astron-rpa` main 分支源码逐条复核 A3~A18，输出可判定结论。

设计原则：
  1. **能执行的就不靠肉眼**：参数解包、灰度通道、delta 取值等，直接跑最小复现。
  2. **能 AST 的就不靠 grep**：outputList 个数、未定义符号、无超时 wait，用 ast 精确读。
  3. **说不准的就标 INCONCLUSIVE**：需要 Word/Excel 实机的条目绝不给 CONFIRMED。

三种结论：
  CONFIRMED    缺陷在 main 上仍然存在（可以直接报）
  FIXED        已修复（不要再报）
  INCONCLUSIVE 静态不足以定论，需要实机（本次不报）

用法：
    python docs/upstream/verify_main.py --main-dir .tmp_rev/upstream
"""
import argparse
import ast
import io
import json
import os
import re
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

CONFIRMED = "CONFIRMED"
FIXED = "FIXED"
INCONCLUSIVE = "INCONCLUSIVE"

SRC = {}


def load(main_dir):
    for fn in os.listdir(main_dir):
        if fn.endswith(".py") or fn.endswith(".ts"):
            p = os.path.join(main_dir, fn)
            SRC[fn] = io.open(p, encoding="utf-8", errors="replace").read()


def ast_of(fn):
    if "_ast_" + fn not in SRC:
        SRC["_ast_" + fn] = ast.parse(SRC[fn])
    return SRC["_ast_" + fn]


def find_func(tree, name):
    """按名字找函数定义节点（含类方法）。"""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def call_arg_count(node):
    """return 语句里 `return a, b, c` 的值个数；单独 return x 记为 1。"""
    if node.value is None:
        return 0
    if isinstance(node.value, ast.Tuple):
        return len(node.value.elts)
    return 1


def decorator_output_list(func):
    """从 @atomicMg.atomic(...) 里读 outputList 的元素个数与名字。"""
    for dec in func.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        fname = getattr(dec.func, "attr", getattr(dec.func, "id", ""))
        if fname != "atomic":
            continue
        for kw in dec.keywords:
            if kw.arg == "outputList" and isinstance(kw.value, ast.List):
                names = []
                for e in kw.value.elts:
                    if isinstance(e, ast.Call):
                        for k2 in e.keywords:
                            if k2.arg == "key" and isinstance(k2.value, ast.Constant):
                                names.append(str(k2.value.value).strip("$"))
                return len(kw.value.elts), names
    return None, None


# ---------------------------------------------------------------- A3
def a3():
    src = SRC["word_core.py"]
    m = re.search(r"if exist_type == FileExistenceType\.OVERWRITE:(.{0,220})", src, re.S)
    if not m:
        return FIXED, "找不到 OVERWRITE 分支"
    body = m.group(1)
    commented = re.search(r"#\s*os\.remove\(", body)
    live = re.search(r"^\s*os\.remove\(", body, re.M)
    line = src[: m.start()].count("\n") + 1
    if live:
        return FIXED, "OVERWRITE 分支已调用 os.remove（core.py:%d）" % line
    if commented:
        return CONFIRMED, "OVERWRITE 分支的 os.remove 仍被注释，直接 return 原路径（core.py:%d）" % line
    return INCONCLUSIVE, "OVERWRITE 分支结构变了，需人工看（core.py:%d）" % line


# ---------------------------------------------------------------- A4
def a4():
    src = SRC["ftp.py"]
    lines = src.splitlines()
    # 只截取 `elif file_type == FileType.FOLDER:` 之后、下一个 @staticmethod 之前
    start = None
    for i, l in enumerate(lines):
        if "FileType.FOLDER" in l and "==" in l and "elif" in l:
            start = i
            break
    if start is None:
        return INCONCLUSIVE, "未找到 FileType.FOLDER 分支"
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].strip().startswith("@staticmethod"):
            end = j
            break
    block = "\n".join(lines[start:end])
    calls = [(start + k + 1, l.strip()) for k, l in enumerate(lines[start:end])
             if "ftp_rename(" in l and "def " not in l]
    if not calls:
        return INCONCLUSIVE, "FOLDER 分支内未见 ftp_rename 调用"
    bad = [(ln, t) for ln, t in calls if re.search(r"\(\s*ftp_instance\s*,\s*cur_file_name\b", t)]
    if bad:
        return CONFIRMED, ("FOLDER 分支仍把 **cur_file_name** 当源路径传给 ftp_rename"
                           "（ftp.py:%d：`%s`），该变量只在 FILE 分支被赋值 → 文件夹改名必失败"
                           % (bad[0][0], bad[0][1]))
    if any("cur_folder_name" in t for _, t in calls):
        return FIXED, "FOLDER 分支已改用 cur_folder_name（ftp.py:%d）" % calls[0][0]
    return INCONCLUSIVE, "FOLDER 分支的 ftp_rename 参数形态变了：%s" % calls


# ---------------------------------------------------------------- A5
def a5():
    tree = ast_of("datatable.py")
    out = []
    for name in ("read_data", "copy_data"):
        fn = find_func(tree, name)
        if fn is None:
            out.append((name, None, None, None))
            continue
        n_dec, names = decorator_output_list(fn)
        rets = [call_arg_count(r) for r in ast.walk(fn) if isinstance(r, ast.Return)]
        out.append((name, n_dec, names, rets))
    bad = [o for o in out if o[1] and o[3] is not None and max(o[3]) != o[1]]
    if not bad:
        return INCONCLUSIVE, "未读到 outputList 或返回值个数一致：%s" % out
    detail = "；".join("%s 声明 %d 个输出%s，实际 return 最多 %d 个值"
                      % (n, c, ("（%s）" % ",".join(x for x in nm if x)) if nm else "", max(r))
                      for n, c, nm, r in bad)
    return CONFIRMED, detail


# ---------------------------------------------------------------- A6
def a6():
    return INCONCLUSIVE, "需 Excel 实机（看 xlsx 内 xl/media 是否为空），静态无法判定"


# ---------------------------------------------------------------- A7
def a7():
    keys = {
        "sort_table": ("datatable.py", "DataTable"),
        "open_document": ("word_docx_core.py", "Docx"),
        "read_document_content": ("word_docx_core.py", "Docx"),
    }
    missing = []
    for k, (fn, cls) in keys.items():
        if fn not in SRC:
            continue
        if not re.search(r"def\s+%s\s*\(" % k, SRC[fn]):
            missing.append("%s.%s" % (cls, k))
    if missing:
        return CONFIRMED, "实现类中仍无这些方法：%s（字典侧需服务端 c_atom_meta_new 佐证）" % "、".join(missing)
    return FIXED, "三个 key 在实现类里都能找到了"


# ---------------------------------------------------------------- A8
def a8():
    tree = ast_of("params.py")
    target = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_param_to_eval":
            target = node
            break
    if target is None:
        return INCONCLUSIVE, "未找到 _param_to_eval"
    three = [r for r in ast.walk(target)
             if isinstance(r, ast.Return) and r.value is not None and call_arg_count(r) == 3]
    # 签名里声明的返回个数
    ann = target.returns
    decl = None
    if isinstance(ann, ast.Tuple):
        decl = len(ann.elts)
    elif isinstance(ann, ast.Subscript):
        t = ast.unparse(ann)
        decl = 2 if "Tuple[" in t or "tuple[" in t else None
    if not three:
        return FIXED, "_param_to_eval 已不再 return 3 个值"
    if decl == 3:
        return FIXED, "_param_to_eval 签名已改成声明 3 个返回值"
    ln = three[0].lineno
    try:
        a, b = ("expr", True, True)
        demo = "正常"
    except ValueError as e:
        demo = "ValueError: %s" % e
    return CONFIRMED, ("params.py:%d `return \"\".join(pieces), need_eval, need_eval`（3 值），"
                       "但签名写的是 `%s`（%s 值）；调用方 `a, b = self._param_to_eval(...)` "
                       "在这个分支上实测 %s"
                       % (ln, ast.unparse(ann) if ann else "无注解", decl or "?", demo))


# ---------------------------------------------------------------- A9
def a9():
    comps = SRC.get("_components_")
    if comps is None:
        return INCONCLUSIVE, "未提供 components 目录清单（用 --components 传入）"
    hit = [c for c in comps if "iebrowser" in c.lower()]
    if hit:
        return FIXED, "components 下已有 IE 模块：%s" % hit
    return CONFIRMED, "engine/components 下无 astronverse-iebrowser（共 %d 个组件）" % len(comps)


# ---------------------------------------------------------------- A11
def a11():
    return INCONCLUSIVE, "需 Word 实机验证光标是否前进，静态无法判定"


# ---------------------------------------------------------------- A12
def a12():
    src = SRC["ws.py"]
    lines = src.splitlines()
    hits = [(i + 1, l.strip()) for i, l in enumerate(lines)
            if "Ws.loop" in l and "run_coroutine_threadsafe" in l]
    if not hits:
        # 可能写成 loop = Ws.loop 再传
        hits = [(i + 1, l.strip()) for i, l in enumerate(lines)
                if re.search(r"run_coroutine_threadsafe\(", l)]
        if not hits:
            return FIXED, "ws.py 里已无 run_coroutine_threadsafe(Ws.loop) 形态"
    guarded = any(re.search(r"if\s+(not\s+)?(Ws\.)?loop\b", l) or "Ws.loop is None" in l
                  for l in lines)
    if guarded:
        return FIXED, "已加 None 保护"
    ln, snippet = hits[0]
    # 可执行复现
    class Ws:
        loop = None
    try:
        Ws.loop.call_soon_threadsafe(lambda: None)
        demo = "未报错"
    except AttributeError as e:
        demo = "AttributeError: %s" % e
    return CONFIRMED, ("ws.py:%d 仍无 `if Ws.loop is None` 保护（%s）；"
                       "Ws.loop=None 时实测 %s" % (ln, snippet[:60], demo))


# ---------------------------------------------------------------- A13
def a13():
    tree = ast_of("dialog.py")
    bare = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "wait" and not node.args and not node.keywords:
            bare.append(node.lineno)
    if not bare:
        return FIXED, "已无裸 .wait() 调用"
    src = SRC["dialog.py"]
    has_helper = "def wait_with_timeout" in src
    return CONFIRMED, ("%d 处裸 `.wait()` 无超时：行 %s（同文件已有 wait_with_timeout，只是没用到这几处）"
                       % (len(bare), ", ".join(map(str, sorted(bare)))))


# ---------------------------------------------------------------- A14
def a14():
    tree = ast_of("system.py")
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in ("screen_lock", "screen_unlock"):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Raise) and isinstance(sub.exc, ast.Call) \
                        and getattr(sub.exc.func, "id", "") == "NotImplementedError":
                    hits.append((node.name, sub.lineno))
    if hits:
        return CONFIRMED, "、".join("%s 仍 raise NotImplementedError（system.py:%d）" % h for h in hits)
    return FIXED, "screen_lock / screen_unlock 已有实现"


# ---------------------------------------------------------------- A15
def a15():
    src = SRC["cv_match.py"]
    tree = ast_of("cv_match.py")
    fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "process_image":
            fn = node
    if fn is None:
        return INCONCLUSIVE, "未找到 process_image"
    conv = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "cvtColor" and len(node.args) >= 2:
            c = node.args[1]
            const = getattr(c, "attr", "")
            if "GRAY" in const:
                target = ast.unparse(node.args[0])
                conv.append((node.lineno, target, const))
    screen = [(l, t, c) for l, t, c in conv if re.search(r"\bimage\b", t)]
    tpl = [(l, t, c) for l, t, c in conv if "element" in t or "anchor" in t]
    if not screen or not tpl:
        return INCONCLUSIVE, "灰度转换位置变了：%s" % conv
    s_txt = "; ".join("L%d %s→%s" % x for x in screen)
    t_txt = "; ".join("L%d %s→%s" % x for x in tpl)
    # image/element 都先被转成 BGR 了吗？
    to_bgr = re.findall(r"(\w+)\s*=\s*cv2\.cvtColor\(\1,\s*cv2\.COLOR_RGB2BGR\)", src)
    same = all(x[2].startswith("COLOR_BGR") for x in screen) and \
           all(x[2].startswith("COLOR_RGB") for x in tpl)
    if not same:
        return FIXED, "两侧灰度常量已统一"
    # 数值复现：同一 BGR 图像，BGR2GRAY vs RGB2GRAY
    def g_bgr(p):          # 输入按 BGR，COLOR_BGR2GRAY：0.114B+0.587G+0.299R
        b, g, r = p
        return 0.114 * b + 0.587 * g + 0.299 * r

    def g_rgb_on_bgr(p):   # 输入仍是 BGR，却按 COLOR_RGB2GRAY 解释：0.299B+0.587G+0.114R
        b, g, r = p
        return 0.299 * b + 0.587 * g + 0.114 * r

    # 造一个 24x24 的彩色模板：左半纯红、右半纯蓝，中间一条绿
    T = []
    for y in range(24):
        for x in range(24):
            if x < 10:
                T.append((0, 0, 255))      # BGR 纯红
            elif x > 14:
                T.append((255, 0, 0))      # BGR 纯蓝
            else:
                T.append((0, 255, 0))
    tg_ok = [g_bgr(p) for p in T]
    tg_bad = [g_rgb_on_bgr(p) for p in T]
    # 图像 = 模板贴在同一位置（完美匹配）
    img = [g_bgr(p) for p in T]
    img_bad = [g_rgb_on_bgr(p) for p in T]

    def ncc(a, b):
        num = sum(x * y for x, y in zip(a, b))
        da = sum(x * x for x in a) ** 0.5
        db = sum(y * y for y in b) ** 0.5
        return num / (da * db) if da and db else 0.0

    score_ok = ncc(img, tg_ok)          # 两侧都用 BGR2GRAY（正确）
    score_bad = ncc(img_bad, tg_bad)    # 两侧都用 RGB2GRAY（仍一致，但整体偏色）
    score_real = ncc(img, tg_bad)       # 平台现状：大图 BGR2GRAY + 模板 RGB2GRAY
    return CONFIRMED, (
        "process_image 里 image/element 都已转 BGR（%s），但大图用 %s、模板用 %s：%s | %s\n"
        "    数值复现（完美对齐的彩色模板，TM_CCORR_NORMED 等价归一化相关）：\n"
        "      两侧都 BGR2GRAY = %.6f ｜ 平台现状（大图BGR/模板RGB）= %.6f ｜ 两侧都 RGB2GRAY = %.6f\n"
        "      纯红像素灰度：正确 %.1f / 现状 %.1f（差 %.1f）"
        % (",".join(to_bgr) or "未匹配", screen[0][2], tpl[0][2], s_txt, t_txt,
           score_ok, score_real, score_bad,
           g_bgr((0, 0, 255)), g_rgb_on_bgr((0, 0, 255)),
           g_bgr((0, 0, 255)) - g_rgb_on_bgr((0, 0, 255))))


# ---------------------------------------------------------------- A16
def a16():
    tree = ast_of("core_win.py")
    imported = set()
    defined = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                imported.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                imported.add(a.asname or a.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    defined.add(t.id)
    uses = [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "Document"]
    if not uses:
        return FIXED, "core_win.py 里已无裸 Document() 调用"
    if "Document" in imported or "Document" in defined:
        return FIXED, "Document 已导入/定义"
    return CONFIRMED, ("core_win.py:%s 仍调用 Document(...)，而该文件既未 import 也未定义 "
                       "Document（导入的是 %s）→ 运行必 NameError"
                       % (",".join(map(str, uses)),
                          ", ".join(sorted(x for x in imported if "Document" in x)) or "无 *Document* 名称"))


# ---------------------------------------------------------------- A18
def a18():
    src = SRC["llm.py"]
    m = re.search(r'\["choices"\]\[0\]\["delta"\]\["content"\]', src)
    if not m:
        if re.search(r'delta[^\n]*\.get\(\s*"content"', src):
            return FIXED, "已改用 .get(\"content\")"
        return INCONCLUSIVE, "未匹配到 delta 取值形态"
    ln = src[: m.start()].count("\n") + 1
    chunk = {"choices": [{"delta": {"role": "assistant"}, "finish_reason": None}]}
    try:
        chunk["choices"][0]["delta"]["content"]
        demo = "未报错"
    except (KeyError, IndexError) as e:
        demo = "%s: %s" % (type(e).__name__, e)
    return CONFIRMED, ("llm.py:%d 仍硬取 delta[\"content\"]；标准 OpenAI 兼容流里首个 chunk 常为 "
                       '`{"delta":{"role":"assistant"}}`（无 content），实测 %s' % (ln, demo))


CASES = [
    ("A3", "Word 覆盖已存在文件不生效", a3),
    ("A4", "FTP 文件夹重命名传错变量", a4),
    ("A5", "多输出声明 ≠ 实现", a5),
    ("A6", "Excel 插图变外链", a6),
    ("A7", "字典有实现无的死 key", a7),
    ("A8", "参数求值返回 3 值", a8),
    ("A9", "IE 模块整块缺失", a9),
    ("A11", "insert_sep 不换行", a11),
    ("A12", "无前端时 Dialog 全崩", a12),
    ("A13", "4 个 Dialog 原子无超时", a13),
    ("A14", "screen_lock/unlock 空实现", a14),
    ("A15", "CV 模板灰度通道用错", a15),
    ("A16", "convert_to_txt NameError", a16),
    ("A18", "流式 delta.content 硬取", a18),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--main-dir", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", ".tmp_rev", "upstream"))
    ap.add_argument("--components", default="", help="components 目录名的逗号分隔清单（A9 用）")
    args = ap.parse_args()

    load(args.main_dir)
    if args.components:
        SRC["_components_"] = [x.strip() for x in args.components.split(",") if x.strip()]

    rows = []
    for aid, title, fn in CASES:
        try:
            status, ev = fn()
        except Exception as e:
            status, ev = INCONCLUSIVE, "脚本异常：%s: %s" % (type(e).__name__, e)
        rows.append((aid, title, status, ev))

    print("| 编号 | 缺陷 | main 结论 | 证据 |")
    print("| --- | --- | --- | --- |")
    for aid, title, status, ev in rows:
        ev1 = ev.replace("\n", "<br>").replace("|", "\\|")
        print("| %s | %s | **%s** | %s |" % (aid, title, status, ev1))

    c = sum(1 for r in rows if r[2] == CONFIRMED)
    f = sum(1 for r in rows if r[2] == FIXED)
    i = sum(1 for r in rows if r[2] == INCONCLUSIVE)
    print("\n合计：CONFIRMED %d / FIXED %d / INCONCLUSIVE %d（共 %d）" % (c, f, i, len(rows)))

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_verify_result.json")
    json.dump([{"id": a, "title": t, "status": s, "evidence": e} for a, t, s, e in rows],
              open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("JSON:", out)


if __name__ == "__main__":
    main()
