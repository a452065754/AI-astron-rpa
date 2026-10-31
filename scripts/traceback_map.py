#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""深度定位：把执行日志里的 traceback 归因到「第几个流程节点 + 哪个原子实现」。

## 为什么需要它

`error_hints.py` 回答的是「**为什么**错 + 怎么修」，但它拿不到「**在哪**错」。
平台给用户的往往只有一句外壳（`执行错误` / `Word文档读取失败，请检查文件是否损坏`），
而真因藏在完整的 Python traceback 里 —— 那里面有三种帧，各有各的价值：

| 帧类型 | 长什么样 | 告诉我们 |
| --- | --- | --- |
| 编译产物 | `venvs\\<robot_id>\\astron\\main.py", line 26, in main` | **第几个流程节点**出的事 |
| 原子实现 | `site-packages\\astronverse\\word\\docx.py", line 180, in create_docx` | 平台代码里哪一行抛的 |
| COM / 第三方 | `_Document.py", line 457, in SaveAs` + `pywintypes.com_error` | 真正的**底层异常与消息** |

「节点行号 → 流程节点」的映射不用猜：引擎编译时同时写出 `main.map`
（内容就是 `14:1,15:2,...` = 行号:节点序号），并且每行调用都带
`__info__=[节点序号, 节点id]`。再拿 `package.json` 的
`process_meta = [[序号, 节点id, 别名, 原子key], ...]` 就能还原成人能读的
「第 4 个节点『新建Word文档』(`Docx.create_docx`)」。（三者源码实证：
`executor/flow/flow.py:121-131` 写 `.map`，`:389` 收集 `process_meta`。）

## 为什么优先用「当下」的编译产物

`venvs/<robot_id>/astron/` 是**按机器人共享的构建缓存**，每次运行都重新生成。
拿今天的产物去解释三天前日志里的行号，可能已经错位（实测过：日志 15:24、产物 15:06）。
所以：

- `run_flow_local.py` 跑完**立刻**调用 → 行号与产物自洽，归因可信；
- 读历史日志时返回 `caution`（提醒可能被后续编译覆盖），并给出自洽校验结果。

## 用法

```
python scripts/traceback_map.py --log latest            # 最近一次设计器/引擎日志
python scripts/traceback_map.py --log <executor.log> --robot-id 2116055287965179904
python scripts/traceback_map.py --text "Traceback ...:"  # 直接给一段
python scripts/traceback_map.py --selftest              # 用真实编译产物自测归因
```

作为库：

```python
import traceback_map as tm
d = tm.deep_from_file(res["log_file"], robot_id)   # 本地真跑失败后
print(tm.format_deep(d))
```
"""
import argparse
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

APPDATA = os.environ.get("APPDATA", "")
# 设计器/引擎的全局日志目录（设计器点「运行」时写这里）
CLIENT_LOG_DIR = os.path.join(APPDATA, "astron-rpa", "logs")
# 编译产物：按机器人隔离的构建缓存
VENVS_DIR = os.path.join(APPDATA, "astron-rpa", "venvs")
# 兼容旧路径（部分场景引擎落在 %APPDATA%\astron）
LEGACY_GEN_DIR = os.path.join(APPDATA, "astron")

TB_HEAD = "Traceback (most recent call last):"
FRAME_RE = re.compile(r'^\s+File "(?P<file>[^"]+)", line (?P<line>\d+)(?:, in (?P<func>.+?))?\s*$')
EXC_RE = re.compile(r"^(?P<etype>[\w][\w\.]{0,80}):\s?(?P<msg>.*)$")
INFO_RE = re.compile(r"__info__\s*=\s*\[\s*(\d+)\s*,\s*['\"](\d+)['\"]")
TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
ARTIFACT_FILE_RE = re.compile(r"^(main|process\d+|module\d+|smart\d+)\.py$", re.I)
# .../venvs/<robot_id>/astron/main.py  →  抓 robot_id 与产物目录
ART_DIR_RE = re.compile(r"^(?P<dir>.*[\\/]astron)[\\/](?P<file>(?P<stem>main|process\d+|module\d+|smart\d+)\.py)$", re.I)
ROBOT_IN_PATH_RE = re.compile(r"[\\/]venvs[\\/](\d+)[\\/]astron", re.I)
CHAIN_MARKERS = ("During handling of the above exception",
                 "The above exception was the direct cause")


# ------------------------------------------------------------------ 帧分类
def classify_frame(path):
    """把一帧归到五类之一。顺序有讲究：executor 也在 astronverse 包里，要先排掉。"""
    p = path.replace("\\", "/").lower()
    if "/astron/" in p and ARTIFACT_FILE_RE.match(os.path.basename(p)):
        return "artifact"          # 引擎把节点树编译出来的 py
    if "/astron_extension/" in p:
        return "component"
    if "/astronverse/executor/" in p or p.endswith("/lib/bdb.py"):
        return "engine"            # 执行框架自身（不是业务，也不是原子）
    if "gen_py" in p or "win32com" in p or "pythoncom" in p:
        return "com"               # Office COM 边界（真异常常在这里）
    if "/astronverse/" in p:
        return "atomic"            # 原子实现（word/docx.py 之类）
    if "site-packages/" in p:
        return "lib"
    return "other"


def _short_path(path):
    """把长长的安装路径收成可读形态：
    `venvs/<robot>/astron/main.py` → `astron/main.py`；
    `.../site-packages/astronverse/word/docx.py` → `astronverse/word/docx.py`。
    """
    p = path.replace("\\", "/")
    i = p.find("site-packages/")
    if i >= 0:
        return p[i + len("site-packages/"):]
    j = p.find("/astron/")
    if j >= 0:
        return p[j + 1:]
    return os.path.basename(p)


# ------------------------------------------------------------------ traceback 解析
def parse_blocks(text):
    """日志文本 → traceback 块列表（按时间戳分组）。

    一份日志里有几十次运行，每次失败可能是**链式异常**（原始异常 → 被包装重抛）。
    所以给每块带上：
      - `ts`：它所属的日志记录时间（用于「哪条链最新」）
      - `chain`：与前一块的关系（`During handling...` / `direct cause`），
        空串表示**新的一次抛出**
    每块：`{ts, chain, exception, msg, frames:[{file,line,func,code,kind}]}`
    """
    lines = (text or "").splitlines()
    blocks, cur = [], None
    pending_after, ts = "", ""
    for ln in lines:
        stripped = ln.strip()
        m_ts = TS_RE.match(stripped)
        if m_ts:
            ts = m_ts.group(1)
        if TB_HEAD in stripped:
            if cur:
                blocks.append(cur)
            cur = {"frames": [], "exception": "", "msg": "",
                   "chain": pending_after, "ts": ts}
            pending_after = ""
            continue
        if cur is None:
            continue
        for m in CHAIN_MARKERS:
            if m in stripped:
                pending_after = m
        # loguru 一条记录 = 一次抛出；遇到新记录说明上一个 traceback 结束了
        if m_ts:
            blocks.append(cur)
            cur = None
            continue
        fm = FRAME_RE.match(ln)
        if fm:
            f = {"file": fm.group("file"), "line": int(fm.group("line")),
                 "func": (fm.group("func") or "").strip(), "code": ""}
            f["kind"] = classify_frame(f["file"])
            cur["frames"].append(f)
            continue
        if cur["frames"] and cur["frames"][-1]["code"] == "" and ln.startswith("    "):
            cur["frames"][-1]["code"] = stripped
            continue
        em = EXC_RE.match(stripped)
        if em:
            cur["exception"] = em.group("etype")
            cur["msg"] = em.group("msg")
    if cur:
        blocks.append(cur)
    for b in blocks:
        b["frames"] = [f for f in b["frames"] if f["file"]]
    return [b for b in blocks if b["frames"] or b["exception"]]


def group_chains(blocks):
    """把 traceback 块按「异常链」分组，组按时间升序。

    规则：`chain` 非空 → 属于前一块所在的链；否则起一条新链。
    返回 `[{ts, blocks:[...]}]`，最后一组 = **最近一次失败**。
    """
    groups = []
    for b in blocks:
        if groups and b.get("chain"):
            groups[-1]["blocks"].append(b)
        else:
            groups.append({"ts": b.get("ts") or "", "blocks": [b]})
    return groups


# ------------------------------------------------------------------ 节点归因
def artifact_dir_of(frame_path):
    m = ART_DIR_RE.match(frame_path.replace("\\", "/"))
    if not m:
        return "", "", ""
    return m.group("dir"), m.group("file"), m.group("stem")


def robot_id_of(frame_path):
    m = ROBOT_IN_PATH_RE.search(frame_path.replace("\\", "/"))
    return m.group(1) if m else ""


def load_line_map(artifact_dir, stem):
    """读 `<stem>.map` → [(行号, 节点序号)]（引擎编译时同步写出）。"""
    p = os.path.join(artifact_dir, stem + ".map")
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            txt = f.read()
    except Exception:
        return {}
    pairs = {}
    for a, b in re.findall(r"(\d+)\s*:\s*(\d+)", txt):
        pairs[int(a)] = int(b)
    return pairs


def load_process_meta(artifact_dir):
    """package.json → {文件名: [(序号, 节点id, 别名, 原子key), ...]}。"""
    p = os.path.join(artifact_dir, "package.json")
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            j = json.load(f)
    except Exception:
        return {}
    out = {}
    for _pid, info in (j.get("process_info") or {}).items():
        fn = info.get("process_file_name") or ""
        meta = info.get("process_meta") or []
        if fn and meta:
            out[fn] = [(m[0], m[1], m[2] if len(m) > 2 else "", m[3] if len(m) > 3 else "")
                       for m in meta if isinstance(m, list) and m]
        # module / component 也带 meta，一并收
    return out


def _node_from_info(text):
    """从语句文本里抠 `__info__=[N, id]` —— 编译产物里每行调用都自带节点序号。"""
    m = INFO_RE.search(text or "")
    if not m:
        return None, ""
    return int(m.group(1)), m.group(2)


def locate_node(frame, meta_by_file):
    """一帧（编译产物里的行）→ 流程节点。

    三条证据按可靠性取用：
      1. 该行（或其语句）里的 `__info__=[N,...]`   → `source="info"`（最硬）
      2. `.map` 里精确命中该行                     → `source="map"`
      3. 落在两个 map 行之间（多行语句的续行）      → `source="range"`（推断，会标注）
    """
    adir, afile, stem = artifact_dir_of(frame["file"])
    if not adir or not afile:
        return None
    line = int(frame.get("line") or 0)
    lmap = load_line_map(adir, stem)
    meta = meta_by_file.get(afile) or []
    starts = sorted(lmap) if lmap else []

    # 读产物源码：报错行可能是多行调用的**续行**，所以从「最近的节点起始行」
    # 往下拼回整条语句，再抠 `__info__=[N,...]`（编译时每行调用都自带节点序号）。
    info_no, info_id = None, ""
    src = []
    py = os.path.join(adir, afile)
    if os.path.exists(py):
        try:
            with open(py, encoding="utf-8", errors="replace") as f:
                src = f.read().splitlines()
        except Exception:
            src = []
    if src:
        prev = [s for s in starts if s <= line]
        lo = prev[-1] if prev else max(line - 1, 1)
        nxt = [s for s in starts if s > line]
        hi = (nxt[0] - 1) if nxt else min(line + 8, len(src))
        chunk = "\n".join(src[lo - 1:max(hi, lo)])
        info_no, info_id = _node_from_info(chunk)

    if info_no is not None:
        node_no, source = info_no, "info"
    elif lmap.get(line):
        node_no, source = lmap[line], "map"
    elif starts:
        prev = [s for s in starts if s <= line]
        if not prev:
            return {"file": afile, "line": line, "node_no": None, "alias": "",
                    "atom_key": "", "source": "before-first-node", "artifact_dir": adir,
                    "robot_id": robot_id_of(frame["file"])}
        node_no, source = lmap[prev[-1]], "range"   # 多行语句续行：按区间推断
    else:
        return {"file": afile, "line": line, "node_no": None, "alias": "",
                "atom_key": "", "source": "no-map", "artifact_dir": adir,
                "robot_id": robot_id_of(frame["file"])}

    rec = next((m for m in meta if m[0] == node_no), None)
    return {
        "file": afile, "line": line, "node_no": node_no,
        "node_id": (rec[1] if rec else info_id),
        "alias": (rec[2] if rec else ""),
        "atom_key": (rec[3] if rec else ""),
        "source": source, "artifact_dir": adir,
        "robot_id": robot_id_of(frame["file"]),
    }


# ------------------------------------------------------------------ 深度定位主流程
def _frames_of(blocks):
    art, atom, com = [], [], []
    for b in blocks:
        for f in b["frames"]:
            if f["kind"] == "artifact":
                art.append(f)
            elif f["kind"] == "atomic":
                atom.append(f)
            elif f["kind"] in ("com", "lib"):
                com.append(f)
    return art, atom, com


def _exc_of(b):
    return f"{b['exception']}: {b['msg']}" if b.get("exception") else ""


def deep_from_blocks(blocks, robot_id=""):
    """traceback 块 → 「在哪错 + 底层真因」的结构化结论。

    只归因**最近一条异常链**（一份日志跨几十次运行，拿最旧的链给结论是错的）。
    """
    empty = {"ok": False, "reason": "日志里没有 traceback 块", "headline": "",
             "chains": [], "node": None, "atomic": None, "trustworthy": False}
    if not blocks:
        return empty

    groups = group_chains(blocks)
    # 指定了 robot 时，取**属于该 robot** 的最近一条链（一份日志跨几十次运行、
    # 跨多个机器人，拿最旧/别人的链给结论是错的）。
    picked = None
    if robot_id:
        for g in reversed(groups):
            rids = {robot_id_of(f["file"]) for f in _frames_of(g["blocks"])[0]}
            if str(robot_id) in {r for r in rids if r}:
                picked = g
                break
    if picked is None:
        picked = groups[-1]
    lb = picked["blocks"]
    art, atom, com = _frames_of(lb)

    out = {"ok": False, "chains": [], "node": None, "atomic": None, "com": None,
           "headline": "", "caution": "", "robot_id": robot_id,
           "chain_ts": picked.get("ts") or "", "chain_count": len(groups),
           "other_chains": len(groups) - 1, "trustworthy": False}

    # 节点归因：编译产物帧里函数名为 main 的那一帧才是「流程语句」（`in <module>` 是入口调用）
    for f in art:
        if (f.get("func") or "").lower() not in ("main", ""):
            continue
        loc = locate_node(f, load_process_meta(artifact_dir_of(f["file"])[0]))
        if loc and loc.get("node_no"):
            out["node"] = loc
            if robot_id and loc.get("robot_id") and str(loc["robot_id"]) != str(robot_id):
                out["caution"] = (f"这段 traceback 来自另一个机器人 {loc['robot_id']}"
                                  f"（本次问的是 {robot_id}），归因不属于当前流程。")
            break
    if atom:
        f = atom[-1]
        out["atomic"] = {"file": _short_path(f["file"]), "line": f["line"],
                         "func": f["func"], "code": f.get("code") or ""}
    if com:
        f = com[-1]
        out["com"] = {"file": _short_path(f["file"]), "line": f["line"],
                      "func": f["func"], "code": f.get("code") or ""}

    # 链式异常：日志里**先打印的是底层原始异常**（真因），后打印的是平台包装后的外壳
    excs = [_exc_of(b) for b in lb if b.get("exception")]
    out["root_exception"] = excs[0] if excs else ""
    out["final_exception"] = excs[-1] if excs else ""
    out["chain_exceptions"] = [{"exception": _exc_of(b), "chain": b.get("chain") or ""}
                               for b in lb]

    # 详细帧（只列**选中的那条链**，整份日志的几十条链全打出来会刷屏）
    cb = []
    for b in lb:
        a2, t2, c2 = _frames_of([b])
        cb.append({
            "exception": _exc_of(b), "chain": b.get("chain") or "",
            "artifact_frames": [f"{_short_path(f['file'])}:{f['line']} in {f['func']}" for f in a2],
            "atomic_frames": [f"{_short_path(f['file'])}:{f['line']} in {f['func']}" for f in t2],
            "com_frames": [f"{_short_path(f['file'])}:{f['line']} in {f['func']}" for f in c2],
        })
    out["chain_blocks"] = cb

    n = out["node"]
    if n and n.get("node_no"):
        # 可信度：产物里那一行确实带着 `__info__=[同一个 N]`（info 源）才算硬证据
        out["trustworthy"] = (n.get("source") == "info")
        parts = [f"失败发生在**第 {n['node_no']} 个节点**"]
        if n.get("alias"):
            parts.append(f"「{n['alias']}」")
        if n.get("atom_key"):
            parts.append(f"（`{n['atom_key']}`）")
        parts.append(f"｜编译产物 {n['file']}:{n['line']}")
        if out.get("atomic"):
            parts.append(f" → 平台实现 {out['atomic']['file']}:{out['atomic']['line']}"
                         f" ({out['atomic']['func']})")
        if out.get("com"):
            parts.append(f" → COM/三方 {out['com']['file']}:{out['com']['line']}"
                         f" ({out['com']['func']})")
        exc = out["final_exception"]
        if exc:
            parts.append(f"；异常 {exc[:200]}")
        if out.get("root_exception") and out["root_exception"] != exc:
            parts.append(f"\n｜底层原始异常（真因）：{out['root_exception'][:220]}")
        out["headline"] = "".join(parts)
        out["ok"] = True
    else:
        out["headline"] = (f"没能定位到具体节点（{out.get('node', {}).get('source') if out.get('node') else '无编译产物帧'}）；"
                           f"最近一次异常 {out['final_exception'] or '(无)'}")
    return out


def deep_from_text(text, robot_id=""):
    return deep_from_blocks(parse_blocks(text), robot_id=robot_id)


def deep_from_file(log_path, robot_id=""):
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            txt = f.read()
    except Exception as e:
        return {"ok": False, "reason": f"读日志失败：{e}", "headline": "", "chains": [],
                "trustworthy": False}
    d = deep_from_text(txt, robot_id=robot_id)
    d["log_file"] = log_path
    # 行号归因依赖编译产物，而产物是**会被后续运行覆盖的缓存**。日志时间晚于产物
    # 时间才自洽；产物更新说明中间又跑过别的版本，行号可能已漂移。
    n = d.get("node") or {}
    if n.get("artifact_dir") and d.get("chain_ts"):
        py = os.path.join(n["artifact_dir"], n.get("file") or "")
        if os.path.exists(py) and d["chain_ts"]:
            try:
                import datetime as _dt
                log_t = _dt.datetime.strptime(d["chain_ts"], "%Y-%m-%d %H:%M:%S").timestamp()
                py_t = os.path.getmtime(py)
                d["artifact_mtime"] = py_t
                if py_t > log_t + 5:
                    d["caution"] = ((d.get("caution") + " ") if d.get("caution") else "") + \
                        (f"编译产物比该次失败更晚（产物 {time_str(py_t)} vs 失败 {d['chain_ts']}）"
                         "—— 期间该机器人又被重新编译过，行号→节点可能有漂移。"
                         "**结论仅在该流程未改动时可信。**")
                    d["trustworthy"] = False
            except Exception:
                pass
    return d


def time_str(ts):
    import time as _t
    return _t.strftime("%m-%d %H:%M:%S", _t.localtime(ts))


def latest_client_logs(n=1):
    files = sorted(glob.glob(os.path.join(CLIENT_LOG_DIR, "executor-*.log")), key=os.path.getmtime)
    return files[-n:] if files else []


def format_deep(d, indent="      "):
    """给人看的定位块。"""
    if not d:
        return ""
    L = []
    if d.get("headline"):
        L.append(f"{indent}🔍 {d['headline']}")
    n = d.get("node") or {}
    if n.get("source") == "range":
        L.append(f"{indent}   （节点序号由行号区间推断：该行是多行调用的续行）")
    for c in d.get("chain_exceptions", [])[1:]:
        if c.get("exception"):
            L.append(f"{indent}   异常链：{c['exception'][:160]}")
    if d.get("caution"):
        L.append(f"{indent}   ⚠️ {d['caution']}")
    if d.get("reason"):
        L.append(f"{indent}（未取到 traceback：{d['reason']}）")
    return "\n".join(L)


# ------------------------------------------------------------------ 自测
def selftest():
    """用**真实编译产物**造 traceback，验证行号→节点归因正确。

    不连服务端、不需要设计器在跑，换环境也能自证。
    """
    if not os.path.isdir(VENVS_DIR):
        print(f"❌ 找不到编译产物目录：{VENVS_DIR}")
        return 2
    cases = []
    for rid in sorted(os.listdir(VENVS_DIR)):
        adir = os.path.join(VENVS_DIR, rid, "astron")
        meta = load_process_meta(adir)
        if not meta:
            continue
        for fname, rows in meta.items():
            py = os.path.join(adir, fname)
            if not os.path.exists(py):
                continue
            stem = os.path.splitext(fname)[0]
            lmap = load_line_map(adir, stem)
            # 取该文件里所有「节点起始行」，逐行造帧并核对别名
            for no, node_id, alias, key in rows:
                ln = next((l for l, n in sorted(lmap.items()) if n == no), None)
                if ln is None:
                    continue
                cases.append((rid, adir, fname, ln, no, alias, key))
    if not cases:
        print("❌ 没有可用的编译产物（先跑一次流程，让引擎生成 venvs/<robot>/astron/）")
        return 2
    print(f"自测：{len(cases)} 条「产物行 → 节点」断言（真实 .map + package.json）")
    bad = 0
    for rid, adir, fname, ln, no, alias, key in cases:
        frame = {"file": os.path.join(adir, fname), "line": ln, "func": "main", "kind": "artifact"}
        loc = locate_node(frame, load_process_meta(adir))
        got = (loc or {}).get("node_no")
        okk = got == no and (loc or {}).get("alias") == alias
        if not okk:
            bad += 1
            print(f"  ❌ robot {rid} {fname}:{ln} 期望 节点{no}「{alias}」 实得 {loc}")
    print(f"  ✅ 通过 {len(cases) - bad} / {len(cases)}")

    # 端到端：造一段带链式异常的日志文本，看能否归因到节点 + 原子实现
    rid, adir, fname, ln, no, alias, key = cases[-1]
    fake = (f"2026-01-01 00:00:00.000 | ERROR | x - error 执行错误 traceback {TB_HEAD}\n"
            f'  File "{os.path.join(adir, fname)}", line {ln}, in main\n'
            f"    result = astronverse.x.Y().{key.split('.')[-1]}(a=1, __info__=[{no}, '123'])\n"
            f"RuntimeError: 自测异常\n")
    d = deep_from_text(fake, robot_id=rid)
    print(f"  端到端：ok={d['ok']} 命中节点={no}「{(d.get('node') or {}).get('alias')}」 "
          f"异常={d.get('final_exception')}")
    if not d["ok"]:
        bad += 1
    print("\n结果：", "✅ 自测全绿" if bad == 0 else f"❌ 有 {bad} 条不对")
    return 0 if bad == 0 else 1


# ------------------------------------------------------------------ CLI
def main():
    ap = argparse.ArgumentParser(description="把执行日志里的 traceback 归因到流程节点")
    ap.add_argument("--log", default="", help="日志文件路径；latest = 客户端最近的 executor 日志")
    ap.add_argument("--text", default="", help="直接给一段 traceback 文本")
    ap.add_argument("--robot-id", default="", help="机器人 id（用于挑该流程的最近一次失败并兜底定位产物目录）")
    ap.add_argument("--selftest", action="store_true", help="用真实编译产物自测归因")
    ap.add_argument("--all-chains", action="store_true", help="列出日志里每一条异常链（默认只列选中的那条）")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if args.text:
        d = deep_from_text(args.text, args.robot_id)
        raw = args.text
    elif args.log:
        path = args.log
        if args.log == "latest":
            f = latest_client_logs(1)
            if not f:
                print(f"找不到 {CLIENT_LOG_DIR}/executor-*.log", file=sys.stderr)
                return 2
            path = f[0]
            print(f"[日志] {path}")
        d = deep_from_file(path, args.robot_id)
        try:
            raw = open(path, encoding="utf-8", errors="replace").read()
        except Exception:
            raw = ""
    else:
        ap.error("必须给 --log / --text / --selftest")

    print("=" * 68)
    print(format_deep(d, indent="  "))
    if d.get("node"):
        print(f"  归因依据：{d['node'].get('source')}｜机器人 {d['node'].get('robot_id') or '—'}"
              f"｜可信 {'是' if d.get('trustworthy') else '否'}"
              f"｜本日志共 {d.get('chain_count', 0)} 条异常链（取最近一条）")
    print("-" * 68)
    for i, c in enumerate(d.get("chain_blocks", []), 1):
        print(f"  第 {i} 段异常: {c.get('exception') or '(未识别)'}")
        for k in ("artifact_frames", "atomic_frames", "com_frames"):
            if c.get(k):
                print(f"    {k:16}: {'; '.join(c[k][:4])}")
    if args.all_chains and raw:
        print("-" * 68)
        print("全部异常链：")
        for g in group_chains(parse_blocks(raw)):
            print(f"  · {g.get('ts') or '(无时间)'}  "
                  f"{_exc_of(g['blocks'][0])[:80] or '(无异常名)'} / "
                  f"{_frames_of(g['blocks'])[0][:1] and _short_path(_frames_of(g['blocks'])[0][0]['file']) or '无产物帧'}")
    print("=" * 68)
    return 0 if d.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
