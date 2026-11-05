#!/usr/bin/env python3
"""在 .183 上执行 shell 脚本：既可在宿主机跑，也可在指定容器里跑（脚本走文件，规避引号地狱）。

用法:
    python scripts/run183.py -f probe.sh                 # 宿主机执行
    python scripts/run183.py -c robot-service -f p.sh    # 容器内执行
    python scripts/run183.py -f - <<'EOF' ... EOF         # 从 stdin 读脚本
"""
import sys
import paramiko

HOST = "192.168.0.183"
USER = "xudongdong"
PW = "1234"


def run(script: str, container: str | None = None, timeout: int = 300) -> str:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, port=22, username=USER, password=PW, timeout=10)
    try:
        with client.open_sftp().file("/tmp/_probe.sh", "w") as f:
            f.write(script)
        if container:
            cmd = (
                f"docker cp /tmp/_probe.sh {container}:/tmp/_probe.sh >/dev/null 2>&1 && "
                f"docker exec {container} sh /tmp/_probe.sh 2>&1"
            )
        else:
            cmd = "sh /tmp/_probe.sh 2>&1"
        _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode(errors="replace")
        err = stderr.read().decode(errors="replace")
        return out + (("\n[STDERR]\n" + err) if err.strip() else "")
    finally:
        client.close()


if __name__ == "__main__":
    args = sys.argv[1:]
    container = None
    if "-c" in args:
        i = args.index("-c")
        container = args[i + 1]
        del args[i:i + 2]
    if "-f" in args:
        i = args.index("-f")
        src = args[i + 1]
        body = sys.stdin.read() if src == "-" else open(src, encoding="utf-8").read()
    else:
        body = " ".join(args)
    print(run(body, container), end="")
