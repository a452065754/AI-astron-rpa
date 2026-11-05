# [BUG] Dialog 4 个原子无超时且无前端时静默永久挂起（message_box 有超时，其余 4 个没有）

- **组件**：`engine/components/astronverse-dialog`（`astronverse/dialog/dialog.py`）
- **影响版本**：`main`（2026-11-05）、v1.1.7
- **严重级别**：P0 —— 无人值守时**永久挂起**，日志无异常，只能强杀进程

## 1. 现象

流程里用了 `Dialog.input_box` / `select_box` / `select_time_box` / `select_file_box` 中任意一个：

- **服务端 / 定时任务 / 无头方式触发**：流程跑到这一步就停住，**永远不返回**，既不报错也不超时。进程活着、CPU 几乎为 0，看日志什么都没有
- **有客户端但用户离开**：同样永久等待，整个机器人被一个对话框钉死
- 只有 `message_box` 正常 —— 它等不到会自动走默认按钮

## 2. 位置

同文件 `dialog.py` 里 5 个原子，只有 1 个做了超时：

| 原子 | 定义行 | 等待方式 |
| --- | --- | --- |
| `message_box` | L132 | `wait_with_timeout(..., wait_time=wait_time)` ✅ |
| `input_box` | L247 | `done.wait()` ❌（L293） |
| `select_box` | L324 | `done.wait()` ❌（L359） |
| `select_time_box` | L415 | `done.wait()` ❌（L459） |
| `select_file_box` | L536 | `done.wait()` ❌（L578） |

而且 4 个原子的**参数表里没有 `wait_time`**，用户想设超时也设不了。

## 3. 根因：两个独立缺陷叠加

### 3.1 无前端时消息根本没发出去，但仍在等

```python
ws = atomicMg.cfg().get("WS", None)
if ws:
    ws.send_reply({"data": {"name": "userform", "option": payload}}, 600, callback_func)

done.wait()   # ← ws 为 None 时，上面整段被跳过，这里没有任何人 set()
```

`ws is None` 表示当前没有前端（服务端执行、定时触发、无头运行）。此时：
1. 请求**没有发出去**（`if ws:` 直接跳过）
2. `done` 事件永远不会被 set
3. `done.wait()` **无限阻塞**

这是最糟的组合：条件判断已经知道"没人会回答"，后面的等待却没有相应短路。

### 3.2 `Ws.loop` 为 None 时直接崩

`send_reply` 内部（`executor/debug/apis/ws.py:112`）：

```python
future = asyncio.run_coroutine_threadsafe(raw_send_reply(), Ws.loop)
future.result(timeout)
```

`Ws.loop` 是模块级变量，在没有事件循环的线程/进程里为 `None`：

```
AttributeError: 'NoneType' object has no attribute 'call_soon_threadsafe'
```

所以「无前端」场景实际有两条失败路径：
- `atomicMg.cfg().get("WS")` 拿不到 → 静默挂起
- 拿到了但 `Ws.loop` 未初始化 → AttributeError

两条都应该在"没有可用前端"时**立即判定失败或走默认值**，而不是挂起/崩溃。

## 4. 为什么这是 P0

- 定时任务是最常见的用法，而定时任务天然没有前端 ⇒ **只要流程里有这 4 个原子，定时执行就一定会挂**
- 挂起不报错，监控看到的是"机器人一直在运行"，不会触发任何告警
- 唯一的处置方式是杀掉进程；如果流程持有 Excel/Word/FTP 句柄，还会连带文件锁死

## 5. 修复建议

### 5.1 给 4 个原子补 `wait_time` 并用 `wait_with_timeout`（与 `message_box` 对齐）

```diff
     def input_box(
         ...
+        wait_time: int = 600,
     ):
         ...
         ws = atomicMg.cfg().get("WS", None)
-        if ws:
-            ws.send_reply({"data": {"name": "userform", "option": payload}}, 600, callback_func)
+        if not ws:
+            raise BaseException("当前没有可用的前端，Dialog 原子不能在此环境执行", "")
+        ws.send_reply({"data": {"name": "userform", "option": payload}}, 600, callback_func)

-        done.wait()
+        wait_with_timeout(lambda: done.is_set(), reset_timeout_on_activity=False, wait_time=wait_time)
+        if not done.is_set():
+            raise BaseException("等待用户输入超时（%ss）" % wait_time, "")
```

4 处同样改法。若希望"无前端时走默认值"而不是报错，把 `raise` 换成 `return default_value` 即可，但**不能什么都不做就继续等**。

### 5.2 `send_reply` 加 None 保护

```diff
-        future = asyncio.run_coroutine_threadsafe(raw_send_reply(), Ws.loop)
+        if Ws.loop is None or Ws.loop.is_closed():
+            raise BaseException("WS 事件循环未就绪，无法向客户端发送消息", "")
+        future = asyncio.run_coroutine_threadsafe(raw_send_reply(), Ws.loop)
```

## 6. 复现方式

**挂起（不需要任何业务环境）**

1. 新建流程：`Dialog.input_box`（默认参数即可，不填等待时间）
2. 用定时任务触发（或直接 `python -m astronverse.executor start`，不启动设计器前端）
3. 观察：进程存活、日志无输出、步骤一直停在 `input_box`

**崩溃**

```python
class Ws:
    loop = None
Ws.loop.call_soon_threadsafe(lambda: None)
# AttributeError: 'NoneType' object has no attribute 'call_soon_threadsafe'
```

## 7. 建议的最小验收

- 4 个原子都接受 `wait_time`，超时抛明确异常
- 无前端时**立即**失败（或立即取默认值），不进入等待
- `Ws.loop is None` 时不抛 `AttributeError`，抛带业务语义的异常
