#!/usr/bin/env python3
"""简易 SSH 运维脚本：连接 192.168.76.134 执行命令。
用法: python3 ops_ssh.py "<命令>" [--sudo] [--no-sudo]
默认对需要特权的命令加 --sudo（sudo -S 喂密码）。
"""
import sys
import paramiko

HOST = "192.168.76.134"
USER = "xudongdong"
PW = "1234"
PORT = 22


def run(cmd: str, sudo: bool = False, timeout: int = 90):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, port=PORT, username=USER, password=PW, timeout=20)
    try:
        if sudo:
            full = f"sudo -S -p '' bash -c {shlex_quote(cmd)}"
        else:
            full = cmd
        stdin, stdout, stderr = client.exec_command(full, timeout=timeout)
        if sudo:
            stdin.write(PW + "\n")
            stdin.flush()
        out = stdout.read().decode(errors="replace")
        err = stderr.read().decode(errors="replace")
        return out, err
    finally:
        client.close()


def shlex_quote(s: str) -> str:
    import shlex
    return shlex.quote(s)


if __name__ == "__main__":
    args = sys.argv[1:]
    sudo = True
    if "--no-sudo" in args:
        sudo = False
        args.remove("--no-sudo")
    if "--sudo" in args:
        sudo = True
        args.remove("--sudo")
    cmd = " ".join(args)
    try:
        out, err = run(cmd, sudo=sudo)
    except ImportError:
        sys.stderr.write("paramiko 未安装，请先 pip install paramiko\n")
        sys.exit(2)
    except Exception as e:  # noqa
        sys.stderr.write(f"SSH 执行失败: {e}\n")
        sys.exit(1)
    sys.stdout.write(out)
    if err.strip():
        sys.stderr.write("--- STDERR ---\n" + err)
