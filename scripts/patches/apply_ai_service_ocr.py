"""把「超算中心 OCR」补丁部署到 ai-service 容器（幂等，可重复执行）

做的事：
  1. 备份 config.py / utils/ocr.py（首次）
  2. 上传 utils/ocr_scnet.py
  3. config.py 增加 OCR_PROVIDER / SCNET_OCR_URL / SCNET_OCR_TYPE / SCNET_API_KEY
  4. utils/ocr.py 的 recognize_text_from_image 增加 scnet 分支
  5. docker/.env 写入 OCR_PROVIDER=scnet 与 SCNET_API_KEY

不自动重启容器 —— 刻意留一步人工确认（重启会让该服务短暂不可用）。

用法：
    C:/Python314/python scripts/patches/apply_ai_service_ocr.py --key sk-xxxx
    C:/Python314/python scripts/patches/apply_ai_service_ocr.py --key sk-xxxx --restart
"""

import argparse
import io
import os
import posixpath
import sys
import tempfile

import paramiko

HOST, USER, PWD = "192.168.76.130", "xudongdong", "1234"
APP_DIR = "/home/astron-rpa/astron-rpa/backend/ai-service/app"
ENV_FILE = "/home/astron-rpa/astron-rpa/docker/.env"

HERE = os.path.dirname(os.path.abspath(__file__))
LOCAL_ADAPTER = os.path.join(HERE, "ai_service_ocr_scnet.py")

MARK_BEGIN = "# ---- OCR_PROVIDER 补丁开始 ----"
MARK_END = "# ---- OCR_PROVIDER 补丁结束 ----"
MARK_CALL = "# ---- 部署补丁：超算中心 OCR ----"

CONFIG_BLOCK = """
{begin}
    OCR_PROVIDER: str = "xfyun"
    SCNET_OCR_URL: str = "https://api.scnet.cn/api/llm/v1/ocr/recognize"
    SCNET_OCR_TYPE: str = "GENERAL"
    SCNET_API_KEY: str = ""
{end}
""".format(begin=MARK_BEGIN, end=MARK_END)

OCR_BRANCH = '''    __MARK__（OCR_PROVIDER=scnet 时生效；不设则下方原逻辑完全不变）
    if (getattr(get_settings(), "OCR_PROVIDER", "") or "").lower() == "scnet":
        from app.utils.ocr_scnet import recognize_via_scnet

        try:
            return await recognize_via_scnet(
                image_base64,
                image_encoding,
                timeout,
                api_url=get_settings().SCNET_OCR_URL,
                api_key=get_settings().SCNET_API_KEY,
                ocr_type=get_settings().SCNET_OCR_TYPE,
            )
        except httpx.HTTPError as e:
            logger.error(f"scnet ocr http error: {e}")
            raise
        except ValueError as e:
            raise OCRError(str(e)) from e

'''.replace("__MARK__", MARK_CALL)


def log(msg):
    print(msg, flush=True)


class Remote:
    def __init__(self):
        self.c = paramiko.SSHClient()
        self.c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.c.connect(HOST, 22, USER, PWD, timeout=20)
        self.sftp = self.c.open_sftp()

    def read(self, path):
        with self.sftp.open(path, "r") as f:
            return io.TextIOWrapper(f, encoding="utf-8").read()

    def write(self, path, text):
        with self.sftp.open(path, "w") as f:
            f.write(text.encode("utf-8"))

    def put(self, local, remote):
        self.sftp.put(local, remote)

    def backup(self, path):
        bak = path + ".bak-ocrscnet"
        exists = True
        try:
            self.sftp.stat(bak)
        except IOError:
            exists = False
        if exists:
            log("  已存在备份，跳过：{}".format(bak))
            return
        self.write(bak, self.read(path))
        log("  备份 -> {}".format(bak))

    def run(self, cmd, timeout=120):
        i, o, e = self.c.exec_command(cmd, timeout=timeout)
        out = o.read().decode()
        err = e.read().decode()
        return o.channel.recv_exit_status(), out, err

    def close(self):
        self.sftp.close()
        self.c.close()


def patch_config(r):
    path = posixpath.join(APP_DIR, "config.py")
    s = r.read(path)
    if MARK_BEGIN in s:
        log("  config.py 已打过补丁，跳过")
        return
    anchor = "    XFYUN_API_KEY: str\n"
    if anchor not in s:
        raise SystemExit("config.py 里找不到锚点 `XFYUN_API_KEY`，请人工确认后再改")
    r.backup(path)
    r.write(path, s.replace(anchor, anchor + CONFIG_BLOCK, 1))
    log("  config.py 已插入 OCR_PROVIDER 字段")


def patch_ocr(r):
    path = posixpath.join(APP_DIR, "utils", "ocr.py")
    s = r.read(path)
    if MARK_CALL in s:
        log("  utils/ocr.py 已打过补丁，跳过")
        return
    anchor = "    try:\n        # Build request components\n"
    if anchor not in s:
        raise SystemExit("utils/ocr.py 里找不到锚点 `try: # Build request components`")
    r.backup(path)
    r.write(path, s.replace(anchor, OCR_BRANCH + anchor, 1))
    log("  utils/ocr.py 已插入 scnet 分支")


def patch_env(r, key):
    s = r.read(ENV_FILE)
    lines = s.splitlines()
    want = {"OCR_PROVIDER": "scnet", "SCNET_API_KEY": key}

    changed = []
    for name, value in want.items():
        hit = None
        for idx, line in enumerate(lines):
            if line.startswith(name + "="):
                hit = idx
                break
        new_line = '{}="{}"'.format(name, value)
        if hit is None:
            lines.append(new_line)
            changed.append(name + " (新增)")
        elif lines[hit] != new_line:
            lines[hit] = new_line
            changed.append(name + " (更新)")

    if not changed:
        log("  .env 已是最新，跳过")
        return
    r.backup(ENV_FILE)
    r.write(ENV_FILE, "\n".join(lines) + "\n")
    log("  .env 已更新：" + "、".join(changed))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", required=True, help="超算中心 API Key")
    ap.add_argument("--restart", action="store_true", help="部署完自动重启 ai-service")
    args = ap.parse_args()

    if not os.path.exists(LOCAL_ADAPTER):
        raise SystemExit("找不到适配器文件：{}".format(LOCAL_ADAPTER))

    r = Remote()
    try:
        log("[1/4] 上传适配器")
        r.put(LOCAL_ADAPTER, posixpath.join(APP_DIR, "utils", "ocr_scnet.py"))
        log("  -> {}/utils/ocr_scnet.py".format(APP_DIR))

        log("[2/4] 改 config.py")
        patch_config(r)

        log("[3/4] 改 utils/ocr.py")
        patch_ocr(r)

        log("[4/4] 写 .env")
        patch_env(r, args.key)

        if args.restart:
            log("[重启] ai-service")
            code, out, err = r.run(
                "docker restart rpa-opensource-ai-service 2>&1 | tail -3", timeout=180
            )
            log("  " + (out or err).strip())
            if code != 0:
                log("  !! 重启返回码 {}".format(code))
        else:
            log("\n未重启。确认无误后执行：")
            log("  docker restart rpa-opensource-ai-service")
    finally:
        r.close()

    log("\n完成。验证：")
    log("  curl -s -X POST http://127.0.0.1:13159/api/rpa-ai-service/ocr/general \\")
    log('    -H "content-type: application/json" -d \'{"encoding":"png","image":"<b64>","status":3}\'')


if __name__ == "__main__":
    sys.exit(main())
