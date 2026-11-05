# [BUG] System.screen_lock / screen_unlock 是 raise NotImplementedError 的空实现，但字典里可正常拖出

- **组件**：`engine/components/astronverse-system`（`astronverse/system/system.py`）
- **影响版本**：`main`（2026-11-05）、v1.1.7
- **严重级别**：P1 —— 用户能拖出、能编译、能保存，**一运行就抛异常**

## 1. 现象

在设计器里从「系统操作」分类拖出「锁屏」或「解锁屏幕」：

- 参数面板正常显示（`screen_unlock` 甚至有用户名、密码类型、密码三个输入框）
- 流程能保存、能发布
- 执行到该步骤直接报错，步骤为红色

报错内容类似：

```
NotImplementedError
```

没有任何业务语义，用户看不出是「平台没实现这个功能」。

## 2. 位置

`system.py`，两个原子：

```python
# L148~156
@staticmethod
@atomicMg.atomic(
    "System",
    outputList=[atomicMg.param("screen_lock_result", types="Bool")],
)
def screen_lock():
    raise NotImplementedError()


# L158~190
@staticmethod
@atomicMg.atomic(
    "System",
    inputList=[
        atomicMg.param("user_name", required=False),
        atomicMg.param("pwd_type", required=False),
        atomicMg.param("password_text", dynamics=[...], required=True),
        atomicMg.param("password_rsa", dynamics=[...], required=True),
    ],
    outputList=[atomicMg.param("screen_unlock_result", types="Bool")],
)
def screen_unlock(
    user_name: str = "",
    pwd_type: PwdType = PwdType.PASSWORD,
    password_text: str = "",
    password_rsa: str = "",
):
    raise NotImplementedError()
```

`screan_unlock` 尤其误导：它声明了 4 个参数、还建了 RSA 加密分支的 dynamics，让用户以为"填好账号密码就能解锁"，实际**参数一个都没用上**。

## 3. 影响

- 字典/前端把它们当可用原子暴露，流程里出现 `System.screen_lock` 是**合法**的
- 一旦执行就是硬失败，且发生在流程中途 —— 如果前面已经改了文件、发了邮件，等于半个流程执行完崩在中间
- 这类"声明完整、实现为空"的原子比"缺失的原子"更危险：缺失会在编译期暴露，空实现只在运行时暴露

## 4. 修复建议

二选一，都行，但**不要保持现状**：

### 方案 A：补实现（Windows 直接可用）

```python
import ctypes

@staticmethod
@atomicMg.atomic("System", outputList=[atomicMg.param("screen_lock_result", types="Bool")])
def screen_lock():
    ok = ctypes.windll.user32.LockWorkStation()
    return bool(ok)
```

`screen_unlock` 涉及凭据校验，建议走 `LogonUser` + `CreateProcessWithLogonW` 或直接与平台的账号体系对接；若短期不做，请走方案 B。

### 方案 B：从字典中移除 / 标记为「未实现」

- 删除 `@atomicMg.atomic` 装饰器（原子不再出现在字典里）
- 或保留但抛**带语义的**异常：

```python
raise BaseException("「解锁屏幕」当前平台版本尚未实现，请勿在生产流程中使用", "")
```

同时前端参数面板不应再展示 `user_name` / `password_*` 这些永远不会生效的输入。

## 5. 复现方式

1. 新建流程，拖入「系统操作 → 锁屏」
2. 直接运行 → 抛 `NotImplementedError`
3. 换「解锁屏幕」，随便填账号密码 → 同样抛 `NotImplementedError`

## 6. 建议的验收

- 这两个原子执行后要么成功改变系统锁屏状态，要么抛出**能看懂**的错误
- 若短期不实现，字典里不应再出现（或前端显示「暂不支持」）
