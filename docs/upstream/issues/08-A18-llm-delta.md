# [BUG] chat_streamable 硬取 delta["content"]，接标准 OpenAI 兼容流式接口时 KeyError

- **组件**：`engine/components/astronverse-ai`（`astronverse/ai/api/llm.py`）
- **影响版本**：`main`（2026-11-05）、v1.1.7
- **严重级别**：P2 —— 换成非自带模型/网关上「流式对话」必崩

## 1. 现象

「AI → 对话（流式）」原子在以下情况下直接抛 `KeyError: 'content'`：

- 把网关指到标准的 OpenAI 兼容服务（vLLM、Ollama、One-API、各类云厂商）
- 使用带思维链的模型（DeepSeek-R1 类），首个 chunk 只有 `reasoning_content`
- 流末尾的 chunk 只带 `finish_reason`，没有 `delta.content`

自带的 `xopdeepseekv32` 网关恰好每个 chunk 都带 `content`，所以默认配置下看不出来。

## 2. 位置

`llm.py`，`chat_streamable`，L42~L52：

```python
    chat_json = {"messages": messages, "model": model, "stream": True}

    response = requests.post(API_URL, json=chat_json, timeout=(5, 35), stream=True)
    try:
        if response.status_code == 200:
            client = sseclient.SSEClient(response)
            for event in client.events():
                if event.data and event.data != "[DONE]":
                    response_json = json.loads(event.data)
                    if response_json.get("choices"):
                        yield response_json["choices"][0]["delta"]["content"]   # ← L50
        else:
            raise BaseException(LLM_NO_RESPONSE_ERROR.format(response), "")
    finally:
        response.close()
```

外层只判了 `if response_json.get("choices")`，里面 `delta` 和 `content` 都是**方括号硬取**。

## 3. 标准流式响应的真实形态

按 OpenAI Chat Completions 规范，一个流的 chunk 序列至少包含三种形态：

```jsonc
// ① 首包：只有 role，没有 content
{"choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}

// ② 中间包：有 content
{"choices":[{"index":0,"delta":{"content":"你"},"finish_reason":null}]}

// ③ 尾包：只有 finish_reason
{"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}
```

①③ 都没有 `content` 键，`delta["content"]` 直接 `KeyError`。

实测：

```python
>>> chunk = {"choices": [{"delta": {"role": "assistant"}, "finish_reason": None}]}
>>> chunk["choices"][0]["delta"]["content"]
KeyError: 'content'
```

DeepSeek 系还会额外给：

```jsonc
{"choices":[{"delta":{"reasoning_content":"用户在问…"}}]}
```

这里连 `content` 都没有 —— 当前代码在这种情况下也会崩，而不是"跳过思考内容"。

## 4. 影响

- 「流式对话」原子对模型供应商强绑定，换模型就崩
- `KeyError` 不会带任何上下文（不知道是哪个 chunk），用户看到的只是一个 Python 内部错误
- 因为 `yield` 在生成器里，**部分内容已经输出到下游**才崩，流程会停在中途

## 5. 修复建议

```diff
                     if response_json.get("choices"):
-                        yield response_json["choices"][0]["delta"]["content"]
+                        delta = response_json["choices"][0].get("delta") or {}
+                        piece = delta.get("content")
+                        if piece:
+                            yield piece
+                        # 需要时同时暴露思维链：
+                        # reasoning = delta.get("reasoning_content")
+                        # if reasoning: yield from ...
```

如果调用方依赖"每个 chunk 都必须有内容"，改成跳过空 chunk 即可；**不要用 `delta.get("content", "")` 后 yield 空串** —— 下游按空串判断会写出多余的换行。

## 6. 附带建议

同文件的 `chat_normal` 等非流式接口也建议统一：

- `choices[0].get("message", {}).get("content", "")`
- 判断 `finish_reason == "length"` 时给出「输出被截断」的提示，而不是当作正常结果返回

## 7. 复现方式

不需要真实模型：

1. 起一个假网关，按 SSE 依次返回上面 ①②③ 三段
2. 把 `GATEWAY_PORT` 指过去
3. 流程里跑「AI → 对话（流式）」
4. 首个 chunk 就抛 `KeyError: 'content'`

## 8. 建议的验收

- ①②③ 三种 chunk 都能正常处理，最终得到 `"你"`
- 带 `reasoning_content` 的模型不崩，且行为可配置（透传/丢弃）
- 空 delta（`{}`）不产生多余输出
