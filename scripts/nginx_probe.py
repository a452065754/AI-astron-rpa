#!/usr/bin/env python3
"""在 .183 的 nginx 容器里执行一段 shell 脚本（脚本内容放文件，避免引号地狱）。

用法:
    python scripts/nginx_probe.py -f probe.sh     # 执行本地脚本
    python scripts/nginx_probe.py "tail -3 /usr/local/openresty/nginx/logs/access.log"
"""
import sys
import paramiko

HOST = "192.168.0.183"
USER = "xudongdong"
PW = "1234"
CONTAINER = "rpa-opensource-openresty-nginx"


def run_script(script: str, timeout: int = 240) -> str:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, port=22, username=USER, password=PW, timeout=10)
    try:
        sftp = client.open_sftp()
        with sftp.file("/tmp/_probe.sh", "w") as f:
            f.write(script)
        sftp.close()
        cmd = (
            f"docker cp /tmp/_probe.sh {CONTAINER}:/tmp/_probe.sh >/dev/null 2>&1 && "
            f"docker exec {CONTAINER} sh /tmp/_probe.sh 2>&1"
        )
        _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode(errors="replace")
        err = stderr.read().decode(errors="replace")
        return out + (("\n[STDERR]\n" + err) if err.strip() else "")
    finally:
        client.close()


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) >= 2 and args[0] == "-f":
        body = sys.stdin.read() if args[1] == "-" else open(args[1], encoding="utf-8").read()
    else:
        body = " ".join(args)
    print(run_script(body), end="")
