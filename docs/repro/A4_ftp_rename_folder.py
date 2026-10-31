# -*- coding: utf-8 -*-
"""A4 · Network.ftp_rename —— 文件夹分支传错变量，服务端回 501

根因（`astronverse/network/ftp.py:306`）：
    elif file_type == FileType.FOLDER:
        ...
        try:
            FtpCore.ftp_rename(ftp_instance, cur_file_name, new_folder_name)
            #                                  ^^^^^^^^^^^^^ 应为 cur_folder_name
`cur_file_name` 在文件夹场景下是空串（函数签名默认 ""），于是发到服务端的
RNFR 命令没有参数 → 服务端响应 `501 Invalid number of parameters`。

同一个函数上方 280 行的「文件分支」用的是正确的 `cur_file_name`，可直接对照。

两种运行模式：
  1) 默认（不需要任何服务器）—— 静态检查源码里 FOLDER 分支的实参名
        "%APPDATA%/astron-rpa/python_core/python.exe" docs/repro/A4_ftp_rename_folder.py
  2) 真跑（有可写 FTP 时）—— 会自己建目录、改名、再删掉
        ... A4_ftp_rename_folder.py --ftp 192.168.0.27:21 --user admin --password admin123
"""
import argparse
import ast
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import (  # noqa: E402
    BLOCKED,
    NOT_REPRODUCED,
    REPRODUCED,
    fail_detail,
    main_guard,
    report,
)

GOOD_ARG = "cur_folder_name"
BAD_ARG = "cur_file_name"


def static_check():
    """在 ftp.py 的 AST 里找 FOLDER 分支调用 ftp_rename 时传的第 2 个位置实参。"""
    from astronverse.network import ftp as ftp_mod

    src = inspect.getsource(ftp_mod)
    tree = ast.parse(src)
    fn = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "ftp_rename"),
        None,
    )
    if fn is None:
        raise RuntimeError("ftp.py 里找不到 ftp_rename 定义")

    found = []

    def arg_name(node):
        return getattr(node, "id", None) or getattr(node, "attr", None) or ast.dump(node)[:40]

    for node in ast.walk(fn):
        if not isinstance(node, ast.If):
            continue
        # 只关心 `file_type == FileType.FOLDER` 这类判断
        cond = ast.dump(node.test)
        if "FOLDER" not in cond:
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                fname = arg_name(sub.func)
                if fname == "ftp_rename" and len(sub.args) >= 2:
                    found.append((sub.lineno, arg_name(sub.args[1])))

    return found


def live_check(addr, user, password):
    """真连 FTP：建目录 → 改名 → 清理。返回服务端给的错误文本。"""
    import ftplib

    host, _, port = addr.partition(":")
    ftp = ftplib.FTP()
    ftp.connect(host, int(port or 21), timeout=20)
    ftp.login(user, password)

    from astronverse.network.ftp import FileType

    src_dir, dst_dir = "A4_repro_old", "A4_repro_new"  # 用 ASCII：IIS FTP 对中文目录名回 451
    for d in (src_dir, dst_dir):
        try:
            ftp.rmd(d)
        except Exception:  # noqa: BLE001
            pass
    ftp.mkd(src_dir)
    err = None
    try:
        from astronverse.network.ftp import FileExistenceType
        from astronverse.network import ftp as ftp_mod

        # 直接调原子本体，走 FOLDER 分支
        fn = ftp_mod.FTP.ftp_rename
        fn(
            ftp_instance=ftp,
            file_type=FileType.FOLDER,
            cur_folder_name=src_dir,
            new_folder_name=dst_dir,
            exist_type=FileExistenceType.RENAME,
        )
    except Exception as e:  # noqa: BLE001
        err = fail_detail(e)
    finally:
        for d in (src_dir, dst_dir):
            try:
                ftp.rmd(d)
            except Exception:  # noqa: BLE001
                pass
        try:
            ftp.quit()
        except Exception:  # noqa: BLE001
            ftp.close()
    return err


def run():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ftp", default="", help="host:port，给了就真跑")
    ap.add_argument("--user", default="")
    ap.add_argument("--password", default="")
    args = ap.parse_args()

    calls = static_check()
    print("源码扫描结果（ftp.py 中 FOLDER 分支调用 FtpCore.ftp_rename 的位置）：")
    if not calls:
        print("  未找到 FOLDER 分支下的调用，版本结构可能已变，请人工核对。")
    for lineno, name in calls:
        flag = "✔ 正确" if name == GOOD_ARG else f"✘ 传的是 {name}，应为 {GOOD_ARG}"
        print(f"  ftp.py:{lineno}  第 2 个位置实参 = {name}   {flag}")

    wrong = [(ln, n) for ln, n in calls if n != GOOD_ARG]

    # ---------- 静态判定 ----------
    if not wrong:
        return report(
            "A4",
            "ftp_rename 文件夹分支传错变量",
            f"FOLDER 分支调用 FtpCore.ftp_rename 时第二个实参为 {GOOD_ARG}",
            f"源码里 FOLDER 分支的实参全部正确：{calls}",
            NOT_REPRODUCED,
            "本版本已修好，可关闭该条。",
        )

    detail = "；".join(f"ftp.py:{ln} 传的是 `{n}`（空串）" for ln, n in wrong)

    # ---------- 有 FTP 就真跑，把服务端错误一起带上 ----------
    if args.ftp:
        try:
            err = live_check(args.ftp, args.user, args.password)
        except Exception as e:  # noqa: BLE001
            err = f"连接/准备阶段失败：{fail_detail(e)}"
        print(f"\n真跑（{args.ftp}）结果：{err or '没有异常（可能已修，请复核）'}")
        if err:
            return report(
                "A4",
                "ftp_rename 文件夹分支传错变量 → 服务端 501",
                "把服务端已存在的目录 A 改名为 B，返回新目录路径，且目录确实改名成功",
                f"{detail}；真跑失败：{err}",
                REPRODUCED,
                "修复建议：把 `ftp.py:306` 的 `cur_file_name` 改成 `cur_folder_name`，"
                "与上方文件分支（第 280 行）保持一致。",
            )

    return report(
        "A4",
        "ftp_rename 文件夹分支传错变量（静态确认，需 FTP 才能看到 501）",
        "FOLDER 分支调用 FtpCore.ftp_rename 时第二个实参为 cur_folder_name（当前文件夹名）",
        f"{detail}；函数签名里 cur_file_name 默认值为空串，因此发出的 RNFR 命令无参数",
        REPRODUCED,
        "修复建议：把 `ftp.py:306` 的 `cur_file_name` 改成 `cur_folder_name`。"
        "想看到实际 501 响应，加参数真跑：--ftp <host:port> --user <u> --password <p>。",
    )


main_guard(run)
