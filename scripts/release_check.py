#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""发布就绪体检：**这个仓库现在能不能交给别的 AI / 别的机器直接用**。

## 为什么需要（2026-11-03 起因）

能力侧早就能跑（`capability_bootstrap.py` 7/7），但「发布出去」是另一回事。实测踩到的三个坑：
  ① 本地领先 origin/main 26 个提交 —— 别人 clone 到的是一个月前的版本；
  ② `install.py` 把技能装到 `~/.workbuddy-ai/`，客户端读的却是 `~/.workbuddy/` → 装完不生效；
  ③ `doctor.py` 当时不查这两项 → 新环境「全绿」，但 AI 那边什么都没有（假绿灯）。

`doctor.py` 查的是「这台机器能不能跑」，本脚本查的是「这个仓库能不能发」。两者都要绿。

## 检查项

| 组 | 检查 | 失败后果 |
| --- | --- | --- |
| 代码卫生 | 有未提交改动 | 发出去的和验证过的不是同一份 |
| | 领先 origin（未推送） | 别人 clone 到旧版 |
| 敏感 | 凭据/备份文件进了 git | 泄漏（红线） |
| | 配置样例里有真 Key | 同上 |
| 文档 | 四件套齐全 | 接力的 AI 没有唯一入口 |
| | 代码比四件套新 | 文档落后，接手就会被误导 |
| 规格 | flows 全量 lint | 生成器/引擎会出错 |
| 门禁 | V1 回归 + V13 | 已知缺陷悄悄回归 |
| 分发 | 技能包 frontmatter 合法 | AI 不会按你的话触发 |
| | install.py 覆盖客户端目录 | 装完不生效（坑 ②） |
| | 目录里没有 PROJECT_ROOT.txt | 会把错误的项目路径发给别人 |

## 用法

    python scripts/release_check.py            # 全量体检
    python scripts/release_check.py --quick    # 跳过门禁（只查静态项，3 秒）
    python scripts/release_check.py --json     # 机器可读

退出码：0 全部通过 / 1 有警告（可发但要知情）/ 2 有阻塞（不许发）。
"""
import argparse
import io
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

OK, WARN, BAD, INFO = "ok", "warn", "bad", "info"
_ICON = {OK: "✅", WARN: "⚠️ ", BAD: "❌", INFO: "ℹ️ "}
_rows = []

DOCS = ["交接清单.md", "开发进度.md", "功能蓝图.md", "待办清单.md"]
SECRETS = ["rpa_config.json", "rpa_config.local.json", "docs/_local_secrets.md"]


def add(level, name, detail="", fix=""):
    _rows.append({"level": level, "name": name, "detail": detail, "fix": fix})


def git(*args, timeout=30):
    """跑 git，返回 (returncode, stdout)。git 不在就用 (127, '')。"""
    if not shutil.which("git"):
        return 127, ""
    try:
        r = subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=True,
                           text=True, timeout=timeout)
        return r.returncode, (r.stdout or "").strip()
    except Exception:
        return 1, ""


# ---------------------------------------------------------------- 1. 代码卫生
def check_git():
    code, _ = git("rev-parse", "--is-inside-work-tree")
    if code != 0:
        add(WARN, "git 仓库", "不是 git 仓库，跳过版本项", "发布建议先 git init + 推到远端")
        return
    code, out = git("status", "--porcelain")
    if code == 0:
        dirty = [x for x in out.splitlines() if x.strip()]
        # demo/ 是运行噪音，不算脏
        real = [x for x in dirty if "/demo/" not in x.replace("\\", "/")
                and not x.replace("\\", "/").startswith("demo/")]
        if real:
            add(WARN, "未提交改动", "%d 个文件未提交" % len(real),
                "先 commit 再发布，否则别人拿到的不是你验证过的版本")
        else:
            add(OK, "未提交改动", "无" + ("（demo/ 运行噪音 %d 条已忽略）" % len(dirty) if dirty else ""))

    code, remote = git("remote", "get-url", "origin")
    if code != 0 or not remote:
        add(WARN, "远端", "没有 origin", "git remote add origin <url>")
        return
    code, branch = git("rev-parse", "--abbrev-ref", "HEAD")
    branch = branch or "main"
    code, ahead = git("rev-list", "--count", "origin/%s..HEAD" % branch)
    if code == 0 and ahead.isdigit():
        n = int(ahead)
        if n == 0:
            add(OK, "推送状态", "%s 已与 origin 同步" % branch)
        else:
            add(BAD if n >= 5 else WARN, "推送状态",
                "%s 领先 origin/%s %d 个提交（别人 clone 到的是旧版）" % (branch, branch, n),
                "git push origin %s" % branch)
    else:
        add(WARN, "推送状态", "算不出领先数（可能没有 origin/%s 分支）" % branch,
            "git push -u origin %s" % branch)


# ---------------------------------------------------------------- 2. 敏感文件
def check_secrets():
    code, tracked = git("ls-files")
    if code != 0:
        add(INFO, "敏感文件", "读不到 git 索引，跳过")
        return
    files = set(x.replace("\\", "/") for x in tracked.splitlines())
    leaked = [p for p in SECRETS if p in files]
    leaked += [p for p in files if p.startswith("handover/") or p.endswith(".secret.json")]
    if leaked:
        add(BAD, "敏感文件", "已进仓库：%s" % ", ".join(leaked[:5]),
            "git rm --cached 这些文件，并确认 .gitignore 生效；已推送的要换凭据")
    else:
        add(OK, "敏感文件", "凭据/交接包均未入库")

    ex = os.path.join(ROOT, "rpa_config.example.json")
    if not os.path.exists(ex):
        add(WARN, "配置样例", "缺 rpa_config.example.json", "新环境没法照抄字段")
        return
    try:
        with io.open(ex, encoding="utf-8") as f:
            data = json.load(f) or {}
        # 只看「像真凭据」的值：`<...>` 占位符、空串、"示例" 之类都算合规。
        # 这检查是有用的 —— 样例进 git，一旦被谁改成真 Key 就是公开泄漏。
        suspects = []
        for sec, keys in (("openapi", ("api_key",)), ("db", ("password", "ssh_password")),
                          ("llm", ("api_key",))):
            for k in keys:
                v = str((data.get(sec) or {}).get(k) or "").strip()
                if len(v) >= 12 and not (v.startswith("<") or v.endswith(">")
                                         or "示例" in v or "请填" in v):
                    suspects.append("%s.%s" % (sec, k))
        if suspects:
            add(BAD, "配置样例", "疑似真凭据：%s" % ", ".join(suspects),
                "样例里只留 <...> 占位符；真值写 rpa_config.json（已 gitignore）")
        else:
            add(OK, "配置样例", "存在，凭据全是占位符")
    except Exception as e:
        add(WARN, "配置样例", "解析失败：%s" % e)


# ---------------------------------------------------------------- 3. 文档四件套
def check_docs():
    missing = [d for d in DOCS if not os.path.isfile(os.path.join(ROOT, "docs", d))]
    if missing:
        add(BAD, "四件套", "缺：%s" % ", ".join(missing), "补齐后再发布（接力的 AI 靠它上手）")
    else:
        add(OK, "四件套", "4/4 齐全（%s）" % ", ".join(d.replace(".md", "") for d in DOCS))

    # 代码比文档新 → 文档大概率落后
    _, code_ct = git("log", "-1", "--format=%ct", "--", "scripts")
    _, doc_ct = git("log", "-1", "--format=%ct", "--", "docs")
    if code_ct.isdigit() and doc_ct.isdigit():
        if int(code_ct) > int(doc_ct):
            add(WARN, "文档新鲜度", "scripts 的最后提交比 docs 新",
                "这轮改完记得更新四件套（用户红线）")
        else:
            add(OK, "文档新鲜度", "docs 不落后于 scripts")


# ---------------------------------------------------------------- 4. 规格全量 lint
def check_flows():
    try:
        import flow_generator as fg
        import spec_lint as sl
    except Exception as e:
        add(BAD, "规格 lint", "导入失败：%s" % e)
        return
    try:
        atoms = fg.load_atom_dict(offline=True)
    except Exception as e:
        add(WARN, "规格 lint", "原子字典加载失败：%s" % e, "跑一次 atom_catalog.py 重新缓存")
        return
    flows_dir = os.path.join(ROOT, "flows")
    total = err_flows = warn_n = 0
    bad_names = []
    for name in sorted(os.listdir(flows_dir)):
        if not name.endswith(".json"):
            continue
        total += 1
        try:
            # 注意：这里必须 lint **原始文件**，不能先过 `fg.load_spec()`。
            # load_spec 会把 `${PROJECT_ROOT}` 解析成本机绝对路径，而「参数默认值
            # 不许写死盘符路径」（spec_lint 规则 1.8）检查的是**仓库里的文件**，
            # 拿解析后的运行时视图去查，等于把 120 处合规写法全判成错误。
            with io.open(os.path.join(flows_dir, name), encoding="utf-8") as f:
                spec = json.load(f)
            errs, warns = sl.lint(spec, atoms)
        except Exception as e:
            err_flows += 1
            bad_names.append("%s（%s）" % (name, e))
            continue
        if errs:
            err_flows += 1
            bad_names.append(name)
        warn_n += len(warns)
    if err_flows:
        add(BAD, "规格 lint", "%d/%d 条有错：%s" % (err_flows, total, ", ".join(bad_names[:3])),
            "python scripts/spec_lint.py flows/<名>.json 看定位")
    else:
        add(OK, "规格 lint", "%d 条 0 错 · %d 警" % (total, warn_n))


# ---------------------------------------------------------------- 5. 门禁
def check_gates():
    gates = [("V1 全量回归", "logs/qa/v1_regression.py"),
             ("V13 D1 收窄", "logs/qa/v13_b1_counter.py"),
             ("V14 路径去机器化", "logs/qa/v14_port_paths.py")]
    for label, rel in gates:
        path = os.path.join(ROOT, rel)
        if not os.path.isfile(path):
            add(WARN, "门禁 %s" % label, "脚本不存在：%s" % rel)
            continue
        try:
            r = subprocess.run([sys.executable, path], cwd=ROOT,
                               capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            add(WARN, "门禁 %s" % label, "超时（>300s）")
            continue
        if r.returncode == 0:
            tail = [x.strip() for x in (r.stdout or "").splitlines() if "总警告" in x or "合计" in x]
            add(OK, "门禁 %s" % label, tail[-1] if tail else "通过")
        else:
            add(BAD, "门禁 %s" % label, "退出码 %d" % r.returncode,
                "先看完整输出：python %s" % rel)


# ---------------------------------------------------------------- 6. 分发能力
def check_distribution():
    skill_md = os.path.join(ROOT, "skill", "astron-rpa-flow", "SKILL.md")
    if not os.path.isfile(skill_md):
        add(BAD, "技能包", "缺 skill/astron-rpa-flow/SKILL.md")
    else:
        with io.open(skill_md, encoding="utf-8") as f:
            head = f.read(2000)
        has_name = "\nname:" in "\n" + head
        has_desc = "\ndescription:" in "\n" + head
        if has_name and has_desc:
            add(OK, "技能包", "SKILL.md frontmatter 合法（name + description）")
        else:
            add(BAD, "技能包", "frontmatter 缺 name 或 description",
                "没有 description，AI 不知道什么时候该用这个技能")

    # 仓库里不能带 PROJECT_ROOT.txt（那是安装时按当前机器写的，带出去就是错的路径）
    stray = []
    for base, _, names in os.walk(os.path.join(ROOT, "skill")):
        if "PROJECT_ROOT.txt" in names:
            stray.append(os.path.relpath(os.path.join(base, "PROJECT_ROOT.txt"), ROOT))
    if stray:
        add(BAD, "PROJECT_ROOT.txt", "仓库里带了：%s" % ", ".join(stray),
            "删掉，让 install.py 在目标机器上按实际路径写")
    else:
        add(OK, "PROJECT_ROOT.txt", "仓库内无残留（由 install.py 现场生成）")

    # install.py 必须覆盖客户端真正读取的目录 —— 防回退到「只装 ~/.workbuddy-ai」
    inst = os.path.join(HERE, "install.py")
    if not os.path.isfile(inst):
        add(WARN, "安装落点", "找不到 scripts/install.py")
        return
    with io.open(inst, encoding="utf-8") as f:
        src = f.read()
    has_wb_skills = '.workbuddy", "skills"' in src
    has_wb_mcp = '.workbuddy", "mcp.json"' in src
    if has_wb_skills and has_wb_mcp:
        add(OK, "安装落点", "install.py 覆盖 ~/.workbuddy/skills + ~/.workbuddy/mcp.json")
    else:
        add(BAD, "安装落点", "install.py 没写客户端目录（只写 ~/.workbuddy-ai 的话装完不生效）",
            "补 SKILL_CANDIDATES / MCP_CANDIDATES 里的 ~/.workbuddy 路径")

    # doctor 必须能查出「AI 侧没接线」
    doc = os.path.join(HERE, "doctor.py")
    if os.path.isfile(doc):
        with io.open(doc, encoding="utf-8") as f:
            dsrc = f.read()
        if "技能包" in dsrc and "MCP 登记" in dsrc:
            add(OK, "自检覆盖", "doctor.py 会检查技能包 + MCP 接线")
        else:
            add(WARN, "自检覆盖", "doctor.py 不查技能/MCP → 会出现「全绿但 AI 用不上」",
                "把这两项加进 doctor")
    else:
        add(WARN, "自检覆盖", "找不到 scripts/doctor.py")


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="发布就绪体检（只读）")
    ap.add_argument("--quick", action="store_true", help="跳过门禁与 lint（只查静态项）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if not a.json:
        print("=" * 74)
        print("发布就绪体检 · %s" % ROOT)
        print("=" * 74)

    check_git()
    check_secrets()
    check_docs()
    if not a.quick:
        check_flows()
        check_gates()
    check_distribution()

    bad = [r for r in _rows if r["level"] == BAD]
    warn = [r for r in _rows if r["level"] == WARN]

    if a.json:
        print(json.dumps({"rows": _rows, "bad": len(bad), "warn": len(warn),
                          "ready": not bad}, ensure_ascii=False))
        return 0 if not bad else 2

    print()
    for r in _rows:
        print(" %s %-14s %s" % (_ICON[r["level"]], r["name"], r["detail"]))
        if r["fix"] and r["level"] != OK:
            print("    %-14s → %s" % ("", r["fix"]))

    print("=" * 74)
    if bad:
        print("结论：❌ %d 项阻塞，现在发出去别人接不住：" % len(bad))
        for r in bad:
            print("   · %s：%s" % (r["name"], r["fix"] or r["detail"]))
        return 2
    if warn:
        print("结论：⚠️ 可以发，但有 %d 项要知情（不阻塞别人的上手链路）。" % len(warn))
        return 1
    print("结论：✅ 可以发布。另一个 AI / 另一台机器按 docs/交接清单.md 就能接手。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
