"""探针：用真实验证码图片测打码网关（判断 VerifyCode 链路是否可用）。

如果网关能识别出 1Panel 的图形验证码，说明 VerifyCode.picture_code 可以真跑；
如果 500/空结果，说明本机没配打码平台账号，这条链路只能记「环境未满足」。
"""
import base64
import json
import sys
import urllib.request

PANEL_CAPTCHA_API = "http://192.168.76.130:10086/api/v1/auth/captcha"
GATEWAY = "http://127.0.0.1:13159/api/rpa-ai-service/jfbym/customApi"


def http_get(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def http_post_json(url, payload, timeout=25):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


print("== 1) 从 1Panel 拿一张真实验证码")
raw = http_get(PANEL_CAPTCHA_API)
obj = json.loads(raw)
data = obj.get("data") or {}
img_path = data.get("imagePath") or ""
print("   captchaID:", data.get("captchaID"))
print("   imagePath 前缀:", img_path[:40])

b64 = img_path.split(",", 1)[1] if "," in img_path else img_path
img_bytes = base64.b64decode(b64)
print("   解码后大小:", len(img_bytes), "字节")

import os
os.makedirs(os.path.join(os.path.dirname(__file__)), exist_ok=True)
with open(os.path.join(os.path.dirname(__file__), "panel_captcha.png"), "wb") as f:
    f.write(img_bytes)
print("   已存 demo/_probe/panel_captcha.png")

print("\n== 2) 提交给本地打码网关（type=10110 通用4位）")
for t in ("10110", "10103"):
    try:
        resp = http_post_json(GATEWAY, {"image": b64, "type": t})
        print(f"   type={t} -> HTTP 200，响应：{resp[:300]}")
    except Exception as e:
        print(f"   type={t} -> 失败：{type(e).__name__} {str(e)[:200]}")
