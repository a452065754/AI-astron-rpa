# -*- coding: utf-8 -*-
"""文件夹整理子流程：把一堆散文件按**类型 / 月份**归到子文件夹里。

## 为什么用 Python 子流程而不是纯原子

`File.get_file_list` + `Code.ForList` + `File.file_move` 能做「遍历 + 移动」，
但「这个扩展名属于哪一类」用 `Code.If` 表达不了：`condition="in"` 一次只能判
一个字符串，而「图片」有 10 个后缀、「文档」有 9 个。硬写就是每类一串嵌套 If，
几十个节点，**改一次规则要改一片节点**。

所以分类规则收在一个字典里（`DEFAULT_RULES`），改规则只改这一处。循环和移动
也在这个模块内完成——对使用者来说仍然是一条固定流程，脱离 AI 一样跑。

## 安全底线（这段代码只做加法，不做减法）

1. **绝不删除任何文件**，只移动（或按 `copy_mode` 复制）。
2. **绝不覆盖**：目标重名自动变 `名字(1).pdf`。
3. **不碰子文件夹**：只整理第一层，已有目录原样不动。
4. **跳过临时文件**：`.` 开头、`~$` 开头、`.tmp` 结尾（Office 锁文件尤其不能动）。
5. **`dry_run=true` 只出报告不动手**，建议第一次都先这么跑。
6. 被占用/没权限的文件**跳过并报出来**，不中断整批。

## 分类

| `by` | 结果目录 |
| --- | --- |
| `ext`（默认） | `图片/` `文档/` `表格/` `演示/` `压缩包/` `音视频/` `程序/` `其他/` |
| `date` | `2026-10/` `2026-09/`（按修改时间） |
| `ext_date` | `图片/2026-10/` |

`rules` 参数可以覆盖默认分类，格式：
`{"图纸": ["dwg", "dxf"], "发票": ["ofd"]}` —— 传了就**整表替换**，没匹配到的进「其他」。
"""
from typing import Any

import json
import os
import shutil
import time

try:
    from astronverse.workflowlib import logger
except Exception:
    # 非 RPA 运行时（如普通 Python 下做静态检查 / 单测）退化为标准 logging。
    # 不影响 RPA 运行时：那里 astronverse 必然存在，仍走 RPA logger（日志进执行器）。
    import logging
    logger = logging.getLogger("mod_organize")

# 默认分类表。要加类型就往这里加，改完用 make_module.py 更新模块即可。
DEFAULT_RULES = {
    "图片": ["jpg", "jpeg", "png", "gif", "bmp", "webp", "svg", "ico", "tif", "tiff", "heic"],
    "文档": ["doc", "docx", "pdf", "txt", "md", "rtf", "odt", "wps", "wpt", "ofd"],
    "表格": ["xls", "xlsx", "csv", "tsv", "et", "ett"],
    "演示": ["ppt", "pptx", "dps", "dpt"],
    "压缩包": ["zip", "rar", "7z", "tar", "gz", "bz2", "xz"],
    "音视频": ["mp3", "wav", "flac", "aac", "mp4", "avi", "mkv", "mov", "flv", "wmv"],
    "程序": ["exe", "msi", "bat", "cmd", "sh", "py", "js", "jar"],
    "其他": [],
}

# 这些前缀/后缀的文件一律不动（Office/WPS 锁文件、系统隐藏文件）
_SKIP_PREFIX = (".", "~$", "~")
_SKIP_SUFFIX = (".tmp", ".crdownload", ".part")


def _truthy(v, default=False):
    if v is None or v == "":
        return default
    return str(v).strip().lower() in ("true", "1", "yes", "y", "是")


def _unique_path(path: str) -> str:
    """目标已存在就加序号，**绝不覆盖**：`a.pdf` → `a(1).pdf` → `a(2).pdf`。"""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 1
    while os.path.exists("{}({}){}".format(base, i, ext)):
        i += 1
        if i > 9999:
            raise RuntimeError("同名文件太多，无法生成新名字：{}".format(path))
    return "{}({}){}".format(base, i, ext)


def _category(ext: str, rules: dict) -> str:
    ext = (ext or "").lower().lstrip(".")
    for name, exts in rules.items():
        if name == "其他":
            continue
        if ext in [e.lower().lstrip(".") for e in exts]:
            return name
    return "其他"


def _month(ts: float) -> str:
    return time.strftime("%Y-%m", time.localtime(ts))


def _should_skip(name: str) -> bool:
    low = name.lower()
    if low.startswith(_SKIP_PREFIX):
        return True
    return any(low.endswith(s) for s in _SKIP_SUFFIX)


def _load_rules(raw) -> dict:
    """`rules` 参数（JSON 字符串）覆盖默认分类；解析失败就用默认，不中断。"""
    if not raw:
        return dict(DEFAULT_RULES)
    try:
        custom = json.loads(raw) if isinstance(raw, str) else dict(raw)
        if not isinstance(custom, dict) or not custom:
            return dict(DEFAULT_RULES)
        merged = {k: list(v) for k, v in custom.items()}
        merged.setdefault("其他", [])
        return merged
    except Exception as e:
        logger.warning("rules 参数解析失败（{}），改用默认分类".format(e))
        return dict(DEFAULT_RULES)


def _write_report(out_dir: str, res: dict) -> str:
    """写一份人可读的整理清单 —— 移完文件必须能追溯「哪个文件去了哪」。

    放在 `<out_dir>/_整理报告/` 下：那是个目录，下次整理时不会被当成文件搬走。
    """
    try:
        rdir = os.path.join(out_dir, "_整理报告")
        os.makedirs(rdir, exist_ok=True)
        path = os.path.join(rdir, "整理_{}.txt".format(time.strftime("%Y%m%d_%H%M%S")))
        lines = [
            "文件夹整理报告",
            "生成时间：{}".format(time.strftime("%Y-%m-%d %H:%M:%S")),
            "整理范围：{}".format(res.get("src_dir", "")),
            "整理到：  {}".format(out_dir),
            "分类方式：{}".format(res.get("by")),
            "模式：{}{}".format(
                "只报告未动文件（dry_run）" if res.get("dry_run") else "已实际移动",
                " / 复制" if res.get("copy_mode") else ""),
            "扫描 {} 个，处理 {} 个，跳过 {} 个".format(
                res.get("scanned", 0), res.get("moved", 0), res.get("skipped", 0)),
            "",
            "== 分类统计 ==",
        ]
        for cat, n in sorted((res.get("categories") or {}).items(), key=lambda x: -x[1]):
            lines.append("  {}：{} 个".format(cat, n))
        lines += ["", "== 明细 =="]
        for d in res.get("detail") or []:
            lines.append("  [{}] {} → {}".format(d.get("cat"), d.get("file"), d.get("to")))
        if res.get("skipped_detail"):
            lines += ["", "== 跳过 =="]
            for s in res["skipped_detail"]:
                lines.append("  {}：{}".format(s.get("file"), s.get("why")))
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        logger.info("整理报告：{}".format(path))
        return path
    except Exception as e:
        logger.warning("整理报告写入失败（不影响整理结果）：{}".format(e))
        return ""


def main(args) -> Any:
    src_dir = (args.get("src_dir") or "").strip()
    out_dir = (args.get("out_dir") or "").strip() or src_dir
    by = (args.get("by") or "ext").strip().lower()
    dry_run = _truthy(args.get("dry_run"), False)
    copy_mode = _truthy(args.get("copy_mode"), False)
    rules = _load_rules(args.get("rules"))

    if not src_dir or not os.path.isdir(src_dir):
        raise RuntimeError("要整理的文件夹不存在：{}".format(src_dir))
    if by not in ("ext", "date", "ext_date"):
        logger.warning("by={} 不认识，按 ext 处理".format(by))
        by = "ext"
    os.makedirs(out_dir, exist_ok=True)

    # 只取第一层文件；目录原样不动
    files = []
    for name in sorted(os.listdir(src_dir)):
        p = os.path.join(src_dir, name)
        if not os.path.isfile(p):
            continue
        if _should_skip(name):
            logger.info("跳过临时/隐藏文件：{}".format(name))
            continue
        files.append(p)

    logger.info("扫描 {}：{} 个文件（by={}，dry_run={}，copy_mode={}）".format(
        src_dir, len(files), by, dry_run, copy_mode))

    moved, skipped = [], []
    by_cat, by_target = {}, {}

    for p in files:
        name = os.path.basename(p)
        ext = os.path.splitext(name)[1]
        try:
            mtime = os.path.getmtime(p)
        except OSError:
            mtime = time.time()

        # 目标子目录
        parts = []
        cat = _category(ext, rules)
        if by in ("ext", "ext_date"):
            parts.append(cat)
        if by in ("date", "ext_date"):
            parts.append(_month(mtime))
        target_dir = os.path.join(out_dir, *parts) if parts else out_dir

        dst = os.path.join(target_dir, name)
        # 已经在目标位置 → 不用动（避免原地整理时反复搬自己）
        if os.path.normcase(os.path.abspath(os.path.dirname(p))) == \
                os.path.normcase(os.path.abspath(target_dir)):
            by_cat[cat] = by_cat.get(cat, 0) + 1
            by_target[target_dir] = by_target.get(target_dir, 0) + 1
            skipped.append({"file": name, "why": "已在目标目录"})
            continue

        if dry_run:
            moved.append({"file": name, "from": src_dir, "to": target_dir, "cat": cat})
            by_cat[cat] = by_cat.get(cat, 0) + 1
            by_target[target_dir] = by_target.get(target_dir, 0) + 1
            continue

        try:
            os.makedirs(target_dir, exist_ok=True)
            dst = _unique_path(dst)
            if copy_mode:
                shutil.copy2(p, dst)
            else:
                shutil.move(p, dst)
            moved.append({"file": name, "from": src_dir, "to": target_dir, "cat": cat})
            by_cat[cat] = by_cat.get(cat, 0) + 1
            by_target[target_dir] = by_target.get(target_dir, 0) + 1
            logger.info("{} → {}".format(name, os.path.relpath(dst, out_dir)))
        except PermissionError:
            skipped.append({"file": name, "why": "被占用或无权限（关掉它再跑）"})
            logger.warning("跳过（被占用）：{}".format(name))
        except Exception as e:
            skipped.append({"file": name, "why": str(e)[:120]})
            logger.warning("跳过（{}）：{}".format(e, name))

    logger.info("完成：扫描 {} / {} {} / 跳过 {}".format(
        len(files), "将移动" if dry_run else "已移动", len(moved), len(skipped)))
    for cat, n in sorted(by_cat.items(), key=lambda x: -x[1]):
        logger.info("  {}：{} 个".format(cat, n))

    result = {
        "ok": True,
        "src_dir": src_dir,
        "scanned": len(files),
        "moved": len(moved),
        "skipped": len(skipped),
        "dry_run": dry_run,
        "copy_mode": copy_mode,
        "by": by,
        "out_dir": out_dir,
        "categories": by_cat,
        "targets": by_target,
        "detail": moved,
        "skipped_detail": skipped,
    }
    result["report"] = _write_report(out_dir, result)
    return result
