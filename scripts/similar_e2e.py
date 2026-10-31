#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""相似元素（WinEle.similar）端到端：起靶场 → 发布 → 真跑 → 交叉核对。

与 gui_e2e / cv_e2e 同一套路，差别只有一个：**靶场窗口必须活到流程跑完**。
`WinEle.similar` 运行时要在真实 UIA 树里找「窗口→容器→相似子元素」，
所以宿主进程由本脚本拉起（子进程），跑完 finally 收掉。

用法：
    "%APPDATA%/astron-rpa/python_core/python.exe" scripts/similar_e2e.py
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable
OUT_DIR = os.path.join(ROOT, "demo", "_similar_out")
DEFAULT_ROBOT_ID = "2115990391806255104"


def log(msg):
    print(msg, flush=True)


# --------------------------------------------------------------------------- #
# 1. 靶场
# --------------------------------------------------------------------------- #
def spawn_host(items=4, wait=10.0):
    host = os.path.join(HERE, "similar_host.py")
    proc = subprocess.Popen([PY, host, "--items", str(items)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log(f"· 已拉起靶场 pid={proc.pid}（{items} 个列表项）")

    import uiautomation as auto

    deadline = time.time() + wait
    while time.time() < deadline:
        for c in auto.GetRootControl().GetChildren():
            if "靶场" in (c.Name or ""):
                log(f"  靶场窗口就绪：{c.Name!r} pid={c.ProcessId}")
                return proc
        if proc.poll() is not None:
            raise SystemExit(f"✗ 靶场进程已退出 rc={proc.returncode}")
        time.sleep(0.4)
    raise SystemExit(f"✗ 等 {wait}s 靶场窗口没出现")


# --------------------------------------------------------------------------- #
# 2. 发布 + 真跑
# --------------------------------------------------------------------------- #
def publish(spec, robot_id, process_name):
    gen = [PY, os.path.join(HERE, "flow_generator.py"), "--spec", spec, "--robot-id", str(robot_id)]
    log("· 编译规格 → 节点树…")
    r = subprocess.run(gen, cwd=ROOT, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise SystemExit("✗ 生成失败：\n{}\n{}".format(r.stdout, r.stderr))
    for line in (r.stdout or "").strip().splitlines()[-8:]:
        log("  " + line)

    generated = os.path.join(ROOT, "docs", "generated_flow.json")
    cmd = [PY, os.path.join(HERE, "publish_flow.py"), "--flow", generated,
           "--robot-id", str(robot_id), "--process-name", process_name]
    log("· 发布为子流程（不覆盖主流程）…")
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        raise SystemExit("✗ 发布失败：\n{}\n{}".format(r.stdout, r.stderr))
    for line in (r.stdout or "").strip().splitlines()[-6:]:
        log("  " + line)
    m = re.search(r"processId=(\d+)", r.stdout or "")
    if not m:
        raise SystemExit("✗ 发布成功但没拿到 processId")
    return m.group(1)


def run_flow(robot_id, process_id, timeout):
    cmd = [PY, os.path.join(HERE, "run_flow_local.py"),
           "--robot-id", str(robot_id), "--process-id", str(process_id),
           "--timeout", str(timeout), "--show-log"]
    log("· 本地真跑流程…")
    t0 = time.time()
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout + 180)
    return r, time.time() - t0


# --------------------------------------------------------------------------- #
# 3. 取证
# --------------------------------------------------------------------------- #
def read(name):
    p = os.path.join(OUT_DIR, name)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return f.read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", default=os.path.join(ROOT, "flows", "相似元素小流程.json"))
    ap.add_argument("--robot-id", default=DEFAULT_ROBOT_ID)
    ap.add_argument("--process-name", default="相似元素小流程")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--items", type=int, default=4)
    ap.add_argument("--expect-count", type=int, default=4)
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    for f in os.listdir(OUT_DIR):
        try:
            os.remove(os.path.join(OUT_DIR, f))
        except OSError:
            pass

    host = spawn_host(args.items)
    try:
        pid = publish(args.spec, args.robot_id, args.process_name)
        r, elapsed = run_flow(args.robot_id, pid, args.timeout)

        log("")
        log("=" * 78)
        log(f"执行耗时 {elapsed:.1f}s")
        tail = (r.stdout or "").strip().splitlines()[-12:]
        for line in tail:
            log("  " + line)

        checks = []

        def check(name, ok, detail=""):
            checks.append((name, ok, detail))
            log(f"  {'✓' if ok else '✗'} {name}{('：' + detail) if detail else ''}")

        mark = read("相似元素_0_开始.txt")
        check("流程真的跑起来了（开始标记文件存在）", mark is not None)

        cnt_txt = read("相似元素_1_数量.txt") or ""
        m = re.search(r"个数\s*=\s*(\d+)", cnt_txt)
        got_count = int(m.group(1)) if m else -1
        check("WinEle.similar 返回了相似元素列表", got_count > 0, f"个数={got_count}")
        check(f"相似元素个数 == {args.expect_count}", got_count == args.expect_count,
              f"实际 {got_count}")

        ev = read("相似元素取证.txt") or ""
        m2 = re.search(r"第 0 个相似元素的控件名\s*=\s*(.+)", ev)
        got_name = (m2.group(1).strip() if m2 else "")
        check("列表第 0 项能读出控件名（说明装的是真控件）", bool(got_name), f"名字={got_name!r}")
        check("控件名是靶场里的列表项", got_name.startswith("项目"), f"名字={got_name!r}")

        ok = all(c[1] for c in checks)
        log("=" * 78)
        log(f"结论：{sum(1 for c in checks if c[1])}/{len(checks)} 项通过"
            f" → {'WinEle.similar 已真跑通过' if ok else '未全部通过'}")
        if ev:
            log("")
            log("---- 取证文件 ----")
            log(ev)
        return 0 if ok else 1
    finally:
        if host.poll() is None:
            host.terminate()
            try:
                host.wait(timeout=5)
            except Exception:  # noqa: BLE001
                host.kill()
            log("靶场已收尾")


if __name__ == "__main__":
    sys.exit(main())
