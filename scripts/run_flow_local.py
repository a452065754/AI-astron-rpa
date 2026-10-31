#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""本地全流程执行器：不打开设计器界面，直接把服务端已保存的流程真跑一遍。

## 为什么需要它

`validate()` 只查结构（原子存在 / 必填 / 枚举 / 元素归属），`verify_atoms_live.py` 只查
单个原子的实现层行为。两者都**证明不了「整棵节点树能跑」**——变量连线、节点顺序、
主流程入口、参数在真实调用链里的强转，只有真跑才知道。

设计器点「运行」时做的事：客户端起一个本地网关（默认 13159），再由
`astronverse.executor` 通过该网关拉 `process_content` → 编译成 Python → 执行。
本脚本复刻这条链路：**只要设计器客户端在运行**（网关在监听），就能跑。

## 实测（2026-10-30）

```
python run_flow_local.py --robot-id 2115990391806255104
→ 主流程 4 节点：open_excel → read_excel → create_excel → save_excel
→ ExecuteStatus.SUCCESS
→ demo/导出结果.xlsx 被重写（Excel 真实打开，生成 ~$订单.xlsx 锁文件）
```

编译产物在 `%APPDATA%/astron/`（`main.py` / `processN.py` / `package.json`），
可以人工阅读，是理解「节点树 → Python」映射的最佳材料。

## 注意

- 产物目录 `%APPDATA%/astron/` 是**构建缓存**，每次运行按 project_id 重新生成；
  若同时在设计器里运行别的机器人，会互相覆盖（不影响服务端数据，只影响本地缓存）。
- 引擎按 `project_id` 从服务端拉元素，所以元素必须属于该 robot（见交接清单「元素按 robot 隔离」）。
- 子流程默认不执行，只有被主流程调用才跑；要单独跑某条子流程用 `--process-id`。
"""
import argparse
import glob
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time

import rpa_config as cfg

APPDATA = os.environ.get("APPDATA", "")
GEN_DIR = os.path.join(APPDATA, "astron")  # 引擎编译产物（构建缓存）


def client_python():
    """设计器自带的 Python（引擎就在它里面）。路径来自 rpa_config，换机器自动探测。"""
    return cfg.client_python() or os.path.join(APPDATA, "astron-rpa", "python_core", "python.exe")



def free_port(start=13171):
    for p in range(start, start + 60):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    return start


def gateway_alive(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def run(robot_id, process_id="", gateway_port=None, timeout=300, verbose=False,
        log_ws="n", wait_web_ws="n"):
    gateway_port = int(gateway_port or cfg.local_gateway_port())
    py = client_python()
    if not os.path.exists(py):
        return {"ok": False, "error": f"找不到设计器自带 Python：{py}（设计器未安装？）"}
    if not gateway_alive(gateway_port):
        return {
            "ok": False,
            "error": (
                f"本地网关 127.0.0.1:{gateway_port} 未监听 —— 请先启动 astron-rpa 设计器客户端。\n"
                "执行引擎不直连服务端，它通过客户端本地网关取流程/取元素。"
            ),
        }

    port = free_port()
    workdir = tempfile.mkdtemp(prefix="rpa_run_")
    cmd = [
        py, "-m", "astronverse.executor",
        "--project_id", str(robot_id),
        "--mode", "EDIT_PAGE",
        "--gateway_port", str(gateway_port),
        "--port", str(port),
        "--log_ws", str(log_ws),
        "--wait_web_ws", str(wait_web_ws),
    ]
    if process_id:
        cmd += ["--process_id", str(process_id)]

    if verbose:
        print("$", " ".join(cmd))
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)
    elapsed = round(time.time() - t0, 1)

    # 引擎把日志写文件（loguru），从文件里取状态
    logs = sorted(glob.glob(os.path.join(workdir, "logs", "executor-*.log")))
    log_text = ""
    if logs:
        with open(logs[-1], encoding="utf-8", errors="replace") as f:
            log_text = f.read()

    status = None
    m = re.findall(r"ExecuteStatus\.(\w+)", log_text)
    if m:
        status = m[-1]

    errors = [ln.strip() for ln in log_text.splitlines()
              if "| ERROR" in ln or "Traceback" in ln or "error " in ln.lower() and "| ERROR" in ln]
    # 引擎把「流程内异常」也记成 SUCCESS + 节点级日志，故额外抓取节点报错
    node_err = [ln.strip() for ln in log_text.splitlines()
                if re.search(r"(异常|失败|不存在|找不到|cannot|No such|FileNotFound)", ln)
                and "| DEBUG" not in ln]

    gen = {}
    if os.path.isdir(GEN_DIR):
        for name in ("main.py", "package.json"):
            p = os.path.join(GEN_DIR, name)
            if os.path.exists(p):
                gen[name] = os.path.getsize(p)
        gen["process_files"] = sorted(os.path.basename(p) for p in glob.glob(os.path.join(GEN_DIR, "process*.py")))

    ok = (status == "SUCCESS")  # 退出码不可信：引擎结束时 kill_proc_tree 自杀，code 是随机的
    res = {
        "ok": ok,
        "robot_id": str(robot_id),
        "process_id": str(process_id) or "(主流程)",
        "status": status,
        "exit_code": proc.returncode,
        "elapsed_sec": elapsed,
        "generated": gen,
        "errors": errors[-8:],
        "node_errors": node_err[-8:],
        "log_file": logs[-1] if logs else "",
        "stdout_tail": (proc.stdout or "").strip().splitlines()[-6:],
        "stderr_tail": (proc.stderr or "").strip().splitlines()[-6:],
    }
    # 失败时顺手翻译真因（error_hints 是纯函数，缺依赖也不影响执行结果）
    if not ok:
        try:
            import error_hints as eh
            res["hints"] = eh.translate("\n".join(
                [str(res.get("error") or "")]
                + res["errors"] + res["node_errors"] + res["stderr_tail"]))
        except Exception:
            res["hints"] = []
        # 深度定位：把 traceback 归因到「第几个节点 + 哪个原子实现」
        # 本地真跑刚生成产物，行号→节点是自洽的（比读历史日志可信）
        if res.get("log_file"):
            try:
                import traceback_map as tm
                d = tm.deep_from_file(res["log_file"], robot_id=robot_id)
                if d.get("ok"):
                    res["deep"] = d
            except Exception:
                res["deep"] = None
    return res


def main():
    ap = argparse.ArgumentParser(description="用客户端引擎本地执行已保存的 RPA 流程")
    ap.add_argument("--robot-id", required=True, help="机器人（project）id")
    ap.add_argument("--process-id", default="", help="只跑某条子流程；不给则跑主流程")
    ap.add_argument("--gateway-port", type=int, default=cfg.local_gateway_port(),
                    help="设计器本地网关端口（默认取 rpa_config.json 的 client.local_gateway_port）")
    ap.add_argument("--timeout", type=int, default=300, help="超时秒数")
    ap.add_argument("--show-log", action="store_true", help="打印日志尾部")
    ap.add_argument(
        "--log-ws", default="n", choices=["y", "n"],
        help="是否把执行日志推到前端 websocket（默认 n：无人看界面）",
    )
    ap.add_argument(
        "--wait-web-ws", default="n", choices=["y", "n"],
        help="是否等待前端 websocket 接入（Dialog.* 弹窗要靠它渲染，默认 n）",
    )
    args = ap.parse_args()

    r = run(args.robot_id, args.process_id, args.gateway_port, args.timeout, verbose=True,
            log_ws=args.log_ws, wait_web_ws=args.wait_web_ws)
    print("=" * 60)
    for k in ("ok", "status", "exit_code", "elapsed_sec", "process_id"):
        if k in r:
            print(f"{k:12}: {r[k]}")
    if "generated" in r:
        print(f"{'generated':12}: {r['generated']}")
    for k in ("error",):
        if k in r:
            print(f"{k:12}: {r[k]}")
    for k in ("errors", "node_errors", "stderr_tail"):
        if r.get(k):
            print(f"-- {k} --")
            for ln in r[k]:
                print("  ", ln)
    if r.get("hints"):
        print("-- 真因翻译 --")
        try:
            import error_hints as eh
            print(eh.format_report(r["hints"], indent="   "))
        except Exception:
            pass
    if r.get("deep"):
        print("-- 深度定位（traceback → 节点） --")
        try:
            import traceback_map as tm
            print(tm.format_deep(r["deep"], indent="   "))
        except Exception:
            pass
    if args.show_log and r.get("log_file"):
        print(f"-- log: {r['log_file']} --")
        with open(r["log_file"], encoding="utf-8", errors="replace") as f:
            print("".join(f.readlines()[-30:]))
    print("=" * 60)
    print("结果：", "✅ 执行成功" if r.get("ok") else "❌ 执行失败/未完成")
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
