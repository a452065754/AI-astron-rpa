# -*- coding: utf-8 -*-
"""
星辰开源 · 修复 c_atom_meta_new 中损坏的 atom_content
=====================================================
问题（2026-10-30 实测）：13 个原子的 `atom_content` 被「双重转义」——
本应是单个反斜杠的 JSON 转义符全部变成了两个（如 " -> \\" , \\u5c0f -> \\\\u5c0f），
导致 JSON 解析器把多余反斜杠当字面量、提前关掉字符串，报 "Expecting ',' delimiter"。

修复：把每处「两个反斜杠」还原成「一个」（均匀双倍，全局替换安全）。

⚠️ 重要实测结论（2026-10-30）：**直接改 DB 不持久**——上游 robot-service（Java 写
   `c_atom_meta_new`）会把双重转义的坏值回写覆盖（实测写入后数秒被还原）。因此本工具
   仅作留档/待上游修复后使用；真正落地的是 `flow_generator.py` 的**读时自动修复**
   （`_parse_atom`），不依赖 DB 被改。

落库方式（实测踩坑后确定）：
  - 用 `docker exec -i mysql` 经 **stdin** 发送 SQL（避免 `-e` 长参数截断）；
  - 单条 UPDATE + `CONVERT(UNHEX('<hex>') USING utf8mb4)`（hex 纯 ASCII，彻底规避引号/反斜杠/中文转义）；
  - 不要把 SET NAMES / SET sql_mode / SELECT 与 UPDATE 拼在同一批（会静默失效）。
  - 原值从备份文件读取（诊断期间可能已改动个别行），保证修复源是「真·原始损坏值」。

用法：
  python atom_repair.py                 # 仅验证（基于备份原始值）
  python atom_repair.py --apply         # 从备份修复并写回 + 回读校验
  python atom_repair.py --apply --backup <path>
"""
import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from element_relocator import ServerDb

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP_DIR = os.path.join(HERE, "docs", "backup")

BROKEN_KEYS = [
    "atomCommon", "BrowserSoftware.browser_open", "BrowserSoftware.upload_web_file",
    "File.file_read", "File.file_write", "DataTable.insert_formula",
    "ComputerUse.run", "ComputerUse.custom_action_screen", "ComputerUse.extract_data",
    "ComputerUse.fill_form", "ComputerUse.process_captcha",
    "IeBrowserSoftware.browser_open", "IeBrowserSoftware.upload_web_file",
]


def fetch_true(db, key):
    """单列原始文本抓取，避免 tab 截断 atom_content。"""
    t = db.sql("SELECT atom_content FROM c_atom_meta_new WHERE atom_key='%s'" % key)
    return t.rstrip("\n").rstrip("\r")


def repair(raw):
    """双重反斜杠还原为单个。"""
    return raw.replace("\\\\", "\\")


def exec_stdin(db, sql):
    """经 docker exec -i 用 stdin 发送 SQL（分块写 + flush，规避长参数/通道问题）。"""
    c = db._connect()
    conf = db.conf  # 凭据从 rpa_config.json 读，不硬编码
    stdin, stdout, stderr = c.exec_command(
        "docker exec -i {ct} mysql -u{u} -p{pw} {dbn}".format(
            ct=conf["container"], u=conf["user"], pw=conf["password"], dbn=conf["database"]),
        timeout=300)
    data = sql.encode("utf-8")
    CH = 65536
    for i in range(0, len(data), CH):
        stdin.write(data[i:i + CH])
        stdin.flush()
    stdin.channel.shutdown_write()
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    if "ERROR" in err:
        raise RuntimeError(err.strip()[:200])
    return out, err


def find_backup(preferred=None):
    if preferred:
        return preferred
    files = sorted(glob.glob(os.path.join(BACKUP_DIR, "atom_content_backup_*.json")))
    # 取最早的一个（诊断前备份，含真实原始损坏值），且覆盖全部 13 个 key
    for p in files:
        try:
            d = json.load(open(p, encoding="utf-8"))
            if all(k in d for k in BROKEN_KEYS):
                return p
        except Exception:
            continue
    return None


def verify_from(orig):
    ok, bad = [], []
    for k in BROKEN_KEYS:
        raw = orig.get(k) or ""
        try:
            obj = json.loads(repair(raw))
            ok.append((k, len(obj.get("inputList") or [])))
        except Exception as e:
            bad.append((k, str(e)[:60]))
    return ok, bad


def apply_from(orig):
    """逐原子用「独立 SSH 连接」写回并校验，避免大内容污染共享通道。"""
    results = []
    for k in BROKEN_KEYS:
        fixed = repair(orig.get(k) or "")
        hexstr = fixed.encode("utf-8").hex()
        sql = ("UPDATE c_atom_meta_new SET atom_content=CONVERT(UNHEX('%s') USING utf8mb4) "
               "WHERE atom_key='%s';\n") % (hexstr, k)
        db = ServerDb()
        try:
            exec_stdin(db, sql)
            chk = fetch_true(db, k)
            obj = json.loads(chk)  # 回读校验
            results.append((k, "OK", len(obj.get("inputList") or []), len(chk)))
        except Exception as e:
            try:
                ln = len(fetch_true(db, k))
            except Exception:
                ln = -1
            results.append((k, "FAIL", str(e)[:70], ln))
        finally:
            db.close()
    return results


def main():
    ap = argparse.ArgumentParser(description="修复 c_atom_meta_new 中 13 个损坏的 atom_content")
    ap.add_argument("--apply", action="store_true", help="真正写回（默认只验证）")
    ap.add_argument("--backup", help="指定备份文件（含 13 个原始损坏值）")
    args = ap.parse_args()

    bp = find_backup(args.backup)
    if not bp:
        print("未找到含 13 个 key 的备份文件，请先创建备份（或 --backup 指定）。")
        return 1
    orig = json.load(open(bp, encoding="utf-8"))
    print("修复源备份：%s" % bp)

    ok, bad = verify_from(orig)
    print("验证（修复后应可解析）：%d / %d" % (len(ok), len(BROKEN_KEYS)))
    for k, n in ok:
        print("  OK   %-38s inputList=%d" % (k, n))
    for k, e in bad:
        print("  FAIL %-38s %s" % (k, e))
    if bad:
        print("存在无法修复项，已停止（未写库）。")
        return 1

    if not args.apply:
        print("\n（仅验证，未写库；加 --apply 从备份修复并写回）")
        return 0

    res = apply_from(orig)
    print("写回 + 回读校验：")
    allok = True
    for k, s, n, ln in res:
        print("  %-4s %-38s inputList=%s len=%d" % (s, k, n, ln))
        if s != "OK":
            allok = False
    print("\n结果：%s" % ("全部 13 个修复成功 ✅" if allok else "存在失败项 ❌"))
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
