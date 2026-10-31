#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""运行前预检（preflight）：在落库/真跑**之前**把「注定失败」的条件先挡掉。

## 为什么需要

用户实测报障「第 4 步报 Word 文件读取失败，请检查文件是否损坏」——真实原因是
目标 docx 被 WPS 打开着。这类问题**在生成阶段就能查出来**，却让用户等了 7 秒、
看了一条误导文案、还要回来问我。预检就是把这段时间省掉。

## 查什么（不查就不写）

| 检查 | 级别 | 说明 |
| --- | --- | --- |
| 输入路径存在 | error | 流程不会自动创建输入文件/目录 |
| 输出父目录存在 | error | 流程不会自动建目录（除非显式加了 create_folder 节点） |
| 输出父目录可写 | error | 只读目录、Program Files 会失败 |
| 目标文件被独占 | error | 用 `r+b` 探一次；Office 打开着的文件必失败 |
| 同目录 `~$xxx` 锁文件 | warn | Office 异常退出留下的残锁，常伴随后续失败 |
| 设计器网关 | error | 仅本地真跑需要（`need_gateway=True`） |
| 参数里未解析的占位符 | warn | `$p:x` 指向未声明的参数，运行时必然 NameError |

## 用法

```python
import preflight as pfc
res = pfc.check_spec(spec, need_gateway=True)
print(pfc.format_report(res))
if not res["is_pass"]:
    ...
```

命令行自测（不落库、不执行）：

```
python preflight.py --spec flows/PDF转可编辑Word小流程.json
```
"""
import argparse
import os
import re
import socket
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

_WIN_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_UNC_PATH = re.compile(r"^\\\\[^\\]+\\")
_FILE_EXT = re.compile(r"\.(docx?|xlsx?|xlsm|csv|pdf|txt|json|png|jpe?g|bmp|zip|pptx?)$", re.I)

# 原子 + 输入名 → 角色。in=必须已存在 / out=可不存在 / outdir=目录 / target=可能是输出也可能是输入
ROLE_BY_INPUT = {
    ("Docx.create_docx", "file_path"): "out",
    ("Docx.save_docx", "file_path"): "out",
    ("Docx.insert_docx", "file_path"): "target",
    ("Docx.open_docx", "file_path"): "in",
    ("Excel.create_excel", "file_path"): "out",
    ("Excel.save_excel", "file_path"): "out",
    ("Excel.open_excel", "file_path"): "in",
    ("Excel.read_excel", "file_path"): "in",
    ("File.file_write", "file_path"): "out",
    ("File.file_read", "file_path"): "in",
    ("File.file_delete", "file_path"): "target",
    ("File.create_folder", "folder_path"): "outdir",
    ("PDF.get_pdf_text", "file_path"): "in",
    ("PDF.convert_pdf_to_img", "file_path"): "in",
    ("PDF.convert_pdf_to_img", "output_folder"): "outdir",
}

_OUT_WORDS = ("out", "save", "dest", "target", "export", "to_path", "dst")
_OUT_WORDS_CN = ("输出", "保存", "另存", "目标")
_IN_WORDS = ("file", "path", "src", "source", "input", "read")


def _is_path(s):
    return isinstance(s, str) and bool(_WIN_PATH.match(s) or _UNC_PATH.match(s))


def _is_file_like(p):
    """有扩展名就当文件（不能只认固定后缀表——`.ps1`/`.log` 之类会被漏掉）。"""
    return bool(os.path.splitext(p)[1])


def resolve_value(v, params):
    """把规格里的「值 / 片段数组」解析成具体字符串。

    - 字面量 → 原样
    - `["$p:out_dir", "\\\\", "$p:out_name", ".docx"]` → 拼起来，`$p:` 用参数默认值替换
    - `{"type":"p_var","value":"x"}` → 参数默认值
    - 出现 `$json:` 或无法解析的片段 → 返回 None（表示「运行时才知道」，跳过检查）
    """
    if isinstance(v, str):
        if v.startswith("$json:"):
            return None
        if v.startswith("$p:"):
            p = params.get(v[3:]) or {}
            return str(p.get("default") if p.get("default") is not None else "")
        return v
    if isinstance(v, dict):
        t = v.get("type")
        if t == "p_var":
            p = params.get(v.get("value")) or {}
            return str(p.get("default") if p.get("default") is not None else "")
        if t in ("other", "python"):
            val = v.get("value")
            return val if isinstance(val, str) else None
        return None
    if isinstance(v, (list, tuple)):
        parts = []
        for it in v:
            s = resolve_value(it, params)
            if s is None:
                return None
            parts.append(s)
        return "".join(parts)
    return None


def classify(atom, key, value, params):
    """判断一个路径参数是输入、输出还是目录。"""
    role = ROLE_BY_INPUT.get((atom, key))
    if role:
        return role
    k = (key or "").lower()
    if any(w in k for w in _OUT_WORDS):
        return "out"
    if "dir" in k or "folder" in k or "path_dir" in k:
        return "outdir" if ("out" in k or "save" in k) else "in"
    if any(w in k for w in _IN_WORDS):
        return "in"
    # 参数描述里写了「输出/保存」也算输出
    desc = ""
    for p in (params or {}).values():
        if p.get("name") == key:
            desc = str(p.get("describe") or "")
    if any(w in desc for w in _OUT_WORDS_CN):
        return "out"
    return "unknown"


def writable_dir(d):
    try:
        with tempfile.NamedTemporaryFile(dir=d, prefix=".pf_probe_", delete=True):
            return True
    except Exception:
        return False


def probe_locked(path):
    """探测文件是否被其它进程独占。返回 (locked, 说明)。目录一律不算占用。"""
    if not os.path.exists(path) or os.path.isdir(path):
        return False, ""
    d, b = os.path.split(path)
    lock = os.path.join(d, "~$" + b)
    if os.path.exists(lock):
        return True, f"同目录存在 Office 锁文件 {os.path.basename(lock)}"
    try:
        with open(path, "r+b"):
            pass
    except PermissionError:
        return True, "以读写方式打开被拒（被其它进程独占，通常是 Word/Excel/WPS 正打开着）"
    except Exception:
        pass
    return False, ""


def _gateway_alive(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _iter_path_inputs(spec):
    """遍历规格里所有「能解析成具体路径」的输入项。

    特别处理「目录 + 文件名」分开传的原子（`Docx.create_docx` / `Excel.save_excel` 等）：
    真正会被覆盖/被占用的是**拼出来的完整文件路径**，只查目录会漏掉「目标文件被 Word
    打开着」这种最要命的情况。
    """
    params = {p.get("name"): p for p in (spec.get("params") or [])}
    seen = set()
    # 1) 参数默认值
    for name, p in params.items():
        val = resolve_value(p.get("default"), params)
        if _is_path(val) and val not in seen:
            seen.add(val)
            yield {"src": f"参数 {name}", "atom": "", "key": name, "value": val, "params": params}
    # 2) 节点输入
    for i, n in enumerate(spec.get("nodes") or [], 1):
        atom = n.get("atom") or ""
        ins = n.get("inputs") or {}
        combo = _combo_target(atom, ins, params)
        for key, raw in ins.items():
            if combo and key in ("file_path", "file_name"):
                continue  # 用下面拼出来的完整路径代替，避免只查目录
            val = resolve_value(raw, params)
            if not _is_path(val) or val in seen:
                continue
            seen.add(val)
            yield {"src": f"节点 {i} {atom}.{key}", "atom": atom, "key": key,
                   "value": val, "params": params}
        if combo and combo["path"] not in seen:
            seen.add(combo["path"])
            yield {"src": f"节点 {i} {atom} 的目标文件（{os.path.basename(os.path.dirname(combo['path']))}+文件名拼出）",
                   "atom": atom, "key": "file_name", "value": combo["path"],
                   "params": params, "role": "out"}


_COMBO_EXT = {
    "Docx.create_docx": ".docx", "Docx.save_docx": ".docx",
    "Excel.create_excel": ".xlsx", "Excel.save_excel": ".xlsx",
}


def _combo_target(atom, ins, params):
    """把「目录 + 文件名」拼成真正会被覆盖的文件路径。

    `Docx.create_docx(file_path=目录, file_name=名字)` 这种拆开传的原子，只查目录
    会漏掉「目标 docx 正被 Word 打开」——而这恰好是「文件损坏」报错的真因。
    文件名里已带扩展名就不补；文件名为空表示「沿用 doc 对象里的路径」，跳过。
    """
    ext = _COMBO_EXT.get(atom)
    if not ext:
        return None
    d = resolve_value(ins.get("file_path"), params)
    nm = resolve_value(ins.get("file_name"), params)
    if not _is_path(d) or not nm:
        return None
    if os.path.splitext(nm)[1]:
        ext = ""
    return {"dir_key": "file_path", "path": os.path.join(d, nm + ext)}


def _created_dirs(spec):
    """规格里会被流程**自己创建**的目录（避免误报「目录不存在」）。

    覆盖三种写法（坑 117）：
      1. 原子名里带 create_folder（如 Network.create_folder）
      2. `Folder.folder_create`：target_path + folder_name
      3. 任何 `state_type=create` 的节点（File.file_copy / File.file_move /
         Folder.folder_copy …）——它们的 target 目录会被自动建出来
    """
    params = {p.get("name"): p for p in (spec.get("params") or [])}
    out = []

    def add_dir(v):
        if isinstance(v, str) and _is_path(v):
            out.append(os.path.normcase(os.path.normpath(v)))

    for n in spec.get("nodes") or []:
        atom = n.get("atom") or ""
        ins = n.get("inputs") or {}
        if "create_folder" in atom or atom == "Folder.folder_create":
            for k, raw in ins.items():
                if any(w in k.lower() for w in ("folder", "path", "dir")):
                    v = resolve_value(raw, params)
                    add_dir(v)
                    # folder_create 还会在 target_path 下建 folder_name 这一级
                    if atom == "Folder.folder_create":
                        name = resolve_value(ins.get("folder_name"), params)
                        if isinstance(v, str) and isinstance(name, str) and name:
                            add_dir(os.path.join(v, name))
            continue
        if ins.get("state_type") == "create" or ins.get("status_type") == "create":
            for k, raw in ins.items():
                if any(w in k.lower() for w in ("target", "folder", "dir")):
                    add_dir(resolve_value(raw, params))
    return out


def check_spec(spec, atom_dict=None, need_gateway=False, gateway_port=13159):
    """跑全部预检。返回 `{is_pass, errors, warnings, items}`。"""
    items = []
    auto_dirs = _created_dirs(spec)

    def will_create(p):
        np = os.path.normcase(os.path.normpath(p))
        return any(np == d or np.startswith(d + os.sep) for d in auto_dirs)

    def add(level, code, msg, fix=""):
        items.append({"level": level, "code": code, "msg": msg, "fix": fix})

    # ---------- 1. 路径检查 ----------
    for it in _iter_path_inputs(spec):
        p = it["value"].rstrip("\\/") or it["value"]
        role = it.get("role") or classify(it["atom"], it["key"], p, it["params"])

        # 流程自己会建的目录（state_type/status_type=create、Folder.folder_create）
        # 不管它被分类成 in / out 都不该报错（坑 117 / 120）
        if not os.path.exists(p) and will_create(p):
            add("ok", "outdir_auto",
                f"{it['src']} 目录暂不存在，但流程里会自己建：{p}")
            continue
        # 目录判定优先看「磁盘上真的是不是目录」，其次看有没有扩展名
        is_dir = os.path.isdir(p) or role == "outdir" or (not _is_file_like(p) and role != "in")

        if is_dir:
            if os.path.isdir(p):
                if not writable_dir(p):
                    add("error", "outdir_ro", f"{it['src']} 输出目录不可写：{p}",
                        "换到用户目录下（不要写 Program Files / 系统盘根目录）")
                else:
                    add("ok", "outdir", f"{it['src']} 输出目录存在且可写：{p}")
            else:
                if will_create(p):
                    add("ok", "outdir_auto",
                        f"{it['src']} 目录暂不存在，但流程里有 create_folder 会自己建：{p}")
                    continue
                parent = os.path.dirname(p.rstrip("\\/")) or p
                if os.path.isdir(parent):
                    add("error", "outdir_missing",
                        f"{it['src']} 目录不存在：{p}（父目录存在，流程不会自动建这一级）",
                        "先手工创建该目录，或在流程最前面加一个 File.create_folder 节点")
                else:
                    add("error", "outdir_missing",
                        f"{it['src']} 目录不存在，连父目录也没有：{p}",
                        "先手工创建；或加 File.create_folder 节点（它会逐级创建）")
            continue

        if role == "in":
            if not os.path.exists(p):
                add("error", "in_missing",
                    f"{it['src']} 输入路径不存在：{p}",
                    "流程不会自动创建输入文件，请先确认文件/目录确实在那儿")
                continue
            if os.path.isfile(p):
                locked, why = probe_locked(p)
                if locked:
                    add("warn", "in_locked", f"{it['src']} 输入文件疑似被占用：{p} —— {why}",
                        "读取通常没问题，但如果同一条流程后面还要覆盖它就会失败")
                else:
                    add("ok", "in_ok", f"{it['src']} 输入就绪：{p}")
            continue

        # out / target / unknown 的文件
        parent = os.path.dirname(p)
        if not parent or not os.path.isdir(parent):
            add("error", "out_parent_missing",
                f"{it['src']} 输出目录不存在：{parent or p}",
                "流程不会自动建目录，先手工创建或在流程前加 File.create_folder")
            continue
        if not writable_dir(parent):
            add("error", "out_ro", f"{it['src']} 输出目录不可写：{parent}",
                "换到用户目录下的路径")
            continue
        locked, why = probe_locked(p)
        if locked:
            add("error", "target_locked",
                f"{it['src']} 目标文件被占用，现在跑一定失败：{p} —— {why}",
                "先关闭这个文件（含后台 WINWORD/WPS/EXCEL 进程），或把输出名换成新名字；"
                "也可以加 File.file_delete(on_error=skip) 前置节点")
        elif os.path.exists(p):
            add("ok", "target_ok", f"{it['src']} 目标文件已存在且可写（会被覆盖）：{p}")
        else:
            add("ok", "target_new", f"{it['src']} 目标文件不存在，将新建：{p}")

    # ---------- 2. 未声明的参数引用 ----------
    declared = {p.get("name") for p in (spec.get("params") or [])}
    refs = set()
    for n in spec.get("nodes") or []:
        for key, raw in (n.get("inputs") or {}).items():
            vals = raw if isinstance(raw, list) else [raw]
            for v in vals:
                if isinstance(v, str) and v.startswith("$p:"):
                    refs.add(v[3:])
                elif isinstance(v, dict) and v.get("type") == "p_var":
                    refs.add(str(v.get("value")))
    missing = sorted(r for r in refs if r and r not in declared)
    if missing:
        add("warn", "undeclared_param",
            f"节点引用了未在 params 里声明的参数：{missing}",
            "编译出来不会有对应赋值语句 → 运行时 NameError。请在规格 params 里补声明")
    elif refs:
        add("ok", "params", f"节点引用的 {len(refs)} 个参数都已在 params 声明")

    # ---------- 3. 设计器网关 ----------
    if need_gateway:
        if _gateway_alive(gateway_port):
            add("ok", "gateway", f"设计器本地网关 127.0.0.1:{gateway_port} 在线")
        else:
            add("error", "gateway_down",
                f"设计器本地网关 127.0.0.1:{gateway_port} 未监听",
                "本地真跑要靠客户端网关取流程和元素：先启动 astron-rpa 设计器客户端")

    errors = [i for i in items if i["level"] == "error"]
    warnings = [i for i in items if i["level"] == "warn"]
    return {"is_pass": not errors, "errors": errors, "warnings": warnings,
            "items": items, "checked": len(items)}


_ICON = {"error": "❌", "warn": "⚠️", "ok": "✅"}


def format_report(res, indent="      ", show_ok=False):
    """预检结果 → 给人看的文本。默认只显示问题（error/warn）。"""
    if not res:
        return ""
    lines = []
    for it in res["items"]:
        if it["level"] == "ok" and not show_ok:
            continue
        lines.append(f"{indent}{_ICON.get(it['level'], '·')} {it['msg']}")
        if it["fix"]:
            lines.append(f"{indent}   怎么办：{it['fix']}")
    if not lines:
        lines.append(f"{indent}✅ 预检通过（{res['checked']} 项，无阻塞问题）")
    return "\n".join(lines)


def summary(res):
    return (f"预检 {'通过 ✅' if res['is_pass'] else '未通过 ❌'}"
            f"（error {len(res['errors'])} / warn {len(res['warnings'])} / 共查 {res['checked']} 项）")


def main():
    ap = argparse.ArgumentParser(description="流程运行前预检（只读，不落库、不执行）")
    ap.add_argument("--spec", required=True, help="流程规格 JSON")
    ap.add_argument("--gateway", action="store_true", help="同时检查设计器本地网关")
    ap.add_argument("--show-ok", action="store_true", help="连通过的检查也打印")
    args = ap.parse_args()

    sys.path.insert(0, HERE)
    import flow_generator as fg
    spec = fg.load_spec(args.spec)
    res = check_spec(spec, need_gateway=args.gateway)
    print("=" * 64)
    print(f"预检：{spec.get('name')}（{len(spec.get('nodes') or [])} 节点）")
    print("=" * 64)
    print(format_report(res, indent="  ", show_ok=args.show_ok))
    print("=" * 64)
    print(summary(res))
    return 0 if res["is_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
