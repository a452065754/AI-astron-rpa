"""回滚 ai-service 的超算中心 OCR 补丁

从 `.bak-ocrscnet` 备份恢复 config.py / utils/ocr.py / docker/.env，
并删除 ocr_scnet.py。恢复后必须用 compose **重建**容器（不是 restart，
restart 不重读 env_file）。

用法：
    C:/Python314/python scripts/patches/rollback_ai_service_ocr.py
    C:/Python314/python scripts/patches/rollback_ai_service_ocr.py --rebuild
"""

import argparse
import io
import posixpath
import sys

import paramiko

HOST, USER, PWD = "192.168.76.130", "xudongdong", "1234"
APP_DIR = "/home/astron-rpa/astron-rpa/backend/ai-service/app"
ENV_FILE = "/home/astron-rpa/astron-rpa/docker/.env"
SUFFIX = ".bak-ocrscnet"

TARGETS = [
    posixpath.join(APP_DIR, "config.py"),
    posixpath.join(APP_DIR, "utils", "ocr.py"),
    ENV_FILE,
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true", help="回滚后自动重建 ai-service")
    args = ap.parse_args()

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, 22, USER, PWD, timeout=20)
    sftp = c.open_sftp()

    restored, missing = [], []
    for path in TARGETS:
        bak = path + SUFFIX
        try:
            sftp.stat(bak)
        except IOError:
            missing.append(path)
            continue
        with sftp.open(bak, "r") as f:
            content = io.TextIOWrapper(f, encoding="utf-8").read()
        with sftp.open(path, "w") as f:
            f.write(content.encode("utf-8"))
        restored.append(path)
        print("  恢复 -> {}".format(path))

    adapter = posixpath.join(APP_DIR, "utils", "ocr_scnet.py")
    try:
        sftp.remove(adapter)
        print("  删除 -> {}".format(adapter))
    except IOError:
        print("  适配器不存在，跳过")

    if missing:
        print("\n!! 以下文件找不到备份，需人工处理：")
        for p in missing:
            print("   " + p)

    sftp.close()

    if args.rebuild:
        print("\n重建 ai-service ...")
        i, o, e = c.exec_command(
            "cd /home/astron-rpa/astron-rpa/docker && "
            "docker compose -f docker-compose-build.yml up -d --no-deps ai-service 2>&1 | tail -5",
            timeout=240,
        )
        print("  " + (o.read().decode() or e.read().decode()).strip())
    else:
        print("\n未重建。生效需执行（restart 不够，环境变量在创建时固化）：")
        print("  cd /home/astron-rpa/astron-rpa/docker && "
              "docker compose -f docker-compose-build.yml up -d --no-deps ai-service")

    c.close()
    print("\n回滚完成：{} 个文件已恢复。".format(len(restored)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
