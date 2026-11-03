"""超算中心（SCNet）通用 OCR 适配器 —— ai-service 部署补丁

背景
----
开源版 ai-service 的通用 OCR 只接了讯飞开放平台（HMAC 签名 + 讯飞私有协议体），
而讯飞 OCR 要企业账号，开源用户基本拿不到 → 客户端 `OpenApi.common_ocr` 永远是
`503 ai服务器无响应或错误`，而且这个提示完全不指向「凭据」二字（缺陷清单 C 类）。

超算中心提供标准 REST 的通用 OCR（`POST /api/llm/v1/ocr/recognize`，multipart +
Bearer）。本模块把它接进来，**对外仍输出讯飞格式**，因此客户端一行代码都不用改。

部署位置
--------
服务器 `/home/astron-rpa/astron-rpa/backend/ai-service/app/utils/ocr_scnet.py`
（`docker-compose-build.yml` 里 ai-service 有 `../backend/ai-service/app:/app/app`
的目录挂载，所以改宿主机文件即改容器内代码，重建容器也不丢。）

启用方式
--------
`.env` 里设：

    OCR_PROVIDER=scnet
    SCNET_API_KEY=<你的 key>

然后 `docker restart rpa-opensource-ai-service`。不设 `OCR_PROVIDER` 时行为完全不变。

数据形状对照
------------
超算中心 GENERAL 返回 `elements.text[]`，每块带 `text` / `x` / `y` / `width` / `height`；
讯飞返回 `{"pages":[{"lines":[{"words":[{"content":...}],"coord":[{"x":..,"y":..}]}]}]}`
（客户端 `openapi/core_iflytek.py::__analyse_ocr_result__` 就按这个结构拼文本）。
本适配器做这个映射，`y` 是行首字符的纵坐标，客户端据此判断换行。
"""

import base64
import json

import httpx

from app.logger import get_logger
from app.schemas.ocr import OCRGeneralResponseBody

logger = get_logger(__name__)

# 扩展名 -> MIME。超算中心按 Content-Type 判文件类型，给错会报
# "File type cannot be supported"（实测：application/octet-stream 被拒）。
MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "bmp": "image/bmp",
    "gif": "image/gif",
}


def _to_iflytek_payload(scnet_body: dict) -> dict:
    """超算中心返回 -> 讯飞 pages/lines 结构"""
    data = scnet_body.get("data") or []
    trace_id = ""
    lines = []
    for item in data:
        if not trace_id:
            trace_id = item.get("traceId") or ""
        for res in item.get("result") or []:
            elements = res.get("elements") or {}
            for block in elements.get("text") or []:
                content = (block.get("text") or "").strip()
                if not content:
                    continue
                lines.append(
                    {
                        "words": [{"content": content}],
                        "coord": [{"x": block.get("x", 0), "y": block.get("y", 0)}],
                    }
                )
    return {"pages": [{"lines": lines}]}, trace_id


async def recognize_via_scnet(
    image_base64: str,
    image_encoding: str = "jpg",
    timeout: float = 30.0,
    *,
    api_url: str,
    api_key: str,
    ocr_type: str = "GENERAL",
) -> OCRGeneralResponseBody:
    """调用超算中心 OCR，返回讯飞格式的响应体

    Raises:
        httpx.HTTPError: 网络/HTTP 层失败（由上层转 503）
        ValueError: 上游返回业务错误（由上层转 400）
    """
    encoding = (image_encoding or "png").lstrip(".").lower()
    if encoding not in MIME:
        encoding = "png"
    image_bytes = base64.b64decode(image_base64)

    headers = {"Authorization": "Bearer {}".format(api_key)}
    files = {"file": ("image." + encoding, image_bytes, MIME[encoding])}
    form = {"ocrType": ocr_type}

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(api_url, headers=headers, files=files, data=form)

    response.raise_for_status()
    body = response.json()

    if str(body.get("code")) != "0":
        err = body.get("error") or body.get("msg") or body
        raise ValueError("scnet ocr error: {}".format(err))

    inner, trace_id = _to_iflytek_payload(body)
    text_b64 = base64.b64encode(json.dumps(inner, ensure_ascii=False).encode("utf-8")).decode("utf-8")

    logger.info("scnet ocr ok, lines=%d, trace=%s", len(inner["pages"][0]["lines"]), trace_id)
    return OCRGeneralResponseBody.model_validate(
        {
            "header": {"code": 0, "message": "success", "sid": trace_id or "scnet"},
            "payload": {
                "result": {
                    "compress": "raw",
                    "encoding": "utf8",
                    "format": "json",
                    "text": text_b64,
                }
            },
        }
    )
