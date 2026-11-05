#!/usr/bin/env python3
"""在 192.168.0.183 的 rpa 库上执行 SQL（走 ssh + docker exec stdin，避免引号地狱）。
用法: python scripts/db183.py "select 1"     或  python scripts/db183.py -f q.sql
"""
import sys
import paramiko

HOST = "192.168.0.183"
USER = "xudongdong"
PW = "1234"


def run_sql(sql: str) -> str:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, port=22, username=USER, password=PW, timeout=10)
    try:
        cmd = (
            "docker exec -i -e MYSQL_PWD=rpa123456 rpa-opensource-mysql "
            "mysql -uroot -N rpa 2>&1"
        )
        stdin, stdout, stderr = client.exec_command(cmd, timeout=180)
        stdin.write(sql if sql.rstrip().endswith(";") else sql + ";")
        stdin.write("\n")
        stdin.flush()
        stdin.channel.shutdown_write()
        return stdout.read().decode(errors="replace")
    finally:
        client.close()


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "-f":
        sql = open(args[1], encoding="utf-8").read()
    else:
        sql = " ".join(args)
    print(run_sql(sql), end="")
