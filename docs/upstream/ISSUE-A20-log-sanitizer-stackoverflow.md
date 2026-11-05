# [BUG] `SensitiveDataSanitizer` 值正则递归导致 `GET /api/robot/triggerTask/robotExe/list` 整包返回 999999

- **仓库**：`iflytek/astron-rpa`
- **组件**：`backend/robot-service`（Java 后端）
- **影响版本**：`v1.1.7`（10-22 镜像）及更早；`v1.1.7` 之后的 main 仍未完全修复（见下方「修复状态」）
- **严重级别**：P0（一个用户的工作流参数偏长，就会让该用户的「定时任务 → 添加应用」整张列表不可用）

---

## 1. 现象

工作流参数较多时，点「定时任务 → 添加应用」：

- 弹窗提示「系统异常，请稍后重试」+「暂无数据」
- 网络面板：`GET /api/robot/triggerTask/robotExe/list` **HTTP 200**，响应体仅 **83 字节**：`{"code":"999999"}`
- **整个账号的定时任务都选不了应用**（不是单个流程不可用）
- 删除那个参数多的流程后**立即恢复**
- 换个账号登录同一台服务器 → **完全正常**（取机器人的 SQL 带 `re.creator_id = #{userId}`，别人列表里根本没这条数据）

因为 HTTP 是 200、服务端业务日志无异常堆栈，前端和运维都只能看到「系统异常」，定位成本极高。

## 2. 复现步骤

1. 新建工作流，主流程放 8~10 条流程参数（`c_param`），使 `Executor.paramJson` 序列化后长度 > 1700 字符
2. 发布该流程（**必须发布**：只有发布过才会生成 `robot_execute` 记录并进入「添加应用」列表）
3. 用**发布者本人账号**打开「定时任务 → 添加应用」
4. 期望：列表正常；实际：`{"code":"999999"}`

> 不需要真实业务数据。任意 10 条参数、值长度各 200 字符即可触发。

## 3. 最小复现（不依赖平台，30 秒）

见附件 `A20Repro.java`。核心结论（JDK 17，默认 1MB 线程栈）：

| `paramJson` 值长度 | `QUOTED_VALUE` | `QUOTED_ARRAY_VALUE` |
| --- | --- | --- |
| 1200 | OK | OK |
| 1500 | OK | OK |
| 1600 | OK | OK |
| 1800 | OK | OK |
| **2000** | **StackOverflowError** | OK |
| 3000 | StackOverflowError | OK |
| 6000 | StackOverflowError | OK |

生产环境 JDK 1.8.0_504 上更早爆：**1673 字符必爆（3/3 复现）**，1355 字符 OK。
（JDK 8 的 `Pattern$Loop` 单帧更大，阈值比 17 低一档。）

## 4. 根因

```
TriggerTaskController.getRobotExeList
└─ TriggerTaskServiceImpl.getExecutorList          ← 无 per-robot try/catch
   ├─ paramService.getAllParams(...)               ← 取每个流程的全部参数
   ├─ new ObjectMapper().writeValueAsString(paramList)   // → String，JSON 数组文本
   └─ executor.setParamJson(paramJson)             // paramJson 是 String 字段
        ↓
   ApiLogAspect.around() 记「API 成功」日志
   └─ formatResult()
      ├─ JSON.toJSONString(result)                 // → {"data":[{"paramJson":"[{\"k\":\"v\"}]"}]}
      ├─ SensitiveDataSanitizer.sanitize(jsonStr)
      │    ├─ QUOTED_ARRAY_VALUE  → ([...][:=]\s*\[)[^\]]*(\])   ← [^\]]* 非递归，但冒号后是 " 不是 [ → 不命中
      │    ├─ QUOTED_VALUE        → [...]( "(?:\\.|[^"\\])*" )   ← ★ 命中，且是递归实现
      │    │      递归深度 ≈ 值字符数 → 1MB 栈溢出
      │    └─ 其余 5 个正则没机会跑
      ├─ if (len > 2000) substring(0, 2000)        // 截断在 sanitize **之后**，救不了
      └─ catch (Exception e)                       // ★ StackOverflowError 是 Error，catch 不到
           ↓ 冒泡出切面
        全局异常处理器 → {"code":"999999"}
```

**为什么不是 `QUOTED_ARRAY_VALUE`**：`Executor.paramJson` 是 `String` 类型，fastjson 序列化出来是
`"paramJson":"[{\"k\":\"v\"}]"`（**值带引号**），不是裸数组 `"paramJson":[{...}]`。
所以 `QUOTED_ARRAY_VALUE`（要求冒号后紧跟 `[`）根本匹配不上，真正命中并爆栈的是 `QUOTED_VALUE`。

## 5. 四个独立缺陷（任何一个都足以单独修）

| # | 缺陷 | 位置 | 影响 |
| --- | --- | --- | --- |
| 1 | 值正则用递归写法 `"(?:\\.\|[^"\\])*"` | `SensitiveDataSanitizer.QUOTED_VALUE` | **StackOverflowError 的根因** |
| 2 | `catch (Exception e)` 抓不到 `StackOverflowError` | `ApiLogAspect.formatResult` / `formatParameters` | 即使正则修好，任何 `Error` 仍会穿透日志切面改写接口返回 |
| 3 | `substring(0, maxLen)` 在 `sanitize()` **之后** | 同上 | 先全量脱敏再截断，截断完全无效，且大对象下 7 次 `replaceAll` = 7 次全量拷贝 |
| 4 | `getExecutorList` 无 per-robot 容错 | `TriggerTaskServiceImpl:535` | 单条数据异常 → **整张列表**退化成一条 999999，用户不知道是哪个流程 |

补充第 5 点（体验）：错误只返回 `{"code":"999999"}`、HTTP 200，前端无法区分「系统故障」与「某个流程数据异常」。

## 6. 修复建议

### 6.1 值正则改非递归（最小改动，1 行）

同一个文件里 `QUOTED_ARRAY_VALUE` 已经用了非递归的 `[^\]]*`，说明作者知道这个坑——
`QUOTED_VALUE` 应该是漏改。改成同样的思路即可：

```java
private static final Pattern QUOTED_VALUE = Pattern.compile(
    "([\"']" + SENSITIVE_KEY + "[\"']\\s*[:=]\\s*)"
  + "(\"[^\"\\\\]*(?:\\\\.[^\"\\\\]*)*\"|'[^'\\\\]*(?:\\\\.[^'\\\\]*)*')",
    Pattern.CASE_INSENSITIVE);
```

（`X*(?:\.X*)*` 与 `(?:\.|X)*` 语义等价，但前者是线性匹配、不递归。）

### 6.2 先截断再脱敏 + 抓 `Throwable`

```java
private String formatResult(Object result, ApiLog apiLog) {
    if (result == null) return "null";
    try {
        String jsonStr = JSON.toJSONString(result);
        int maxLength = apiLog != null ? apiLog.maxResultLength() : 2000;
        boolean truncated = jsonStr.length() > maxLength;
        if (truncated) jsonStr = jsonStr.substring(0, maxLength);   // ← 先截断
        jsonStr = SensitiveDataSanitizer.sanitize(jsonStr);          // ← 再脱敏
        return truncated ? jsonStr + "...[已截取]" : jsonStr;
    } catch (Throwable e) {          // ← StackOverflowError / OOM 是 Error
        return "<result omitted: " + e.getClass().getSimpleName() + ">";
    }
}
```

> 注意：日志切面的任何失败都**不应该改变业务返回值**。现在 `around()` 里
> `logMethodSuccess(...)` 抛出的 `Error` 会直接取代 `joinPoint.proceed()` 的正常结果。

### 6.3 `paramJson` 不进日志（推荐，治本）

`paramJson` 是「全部流程参数」的整包文本，既长又必然含业务敏感值，本来就不该进访问日志。
二选一：

- 给 `getRobotExeList` 加 `@NoApiLog`（该接口返回体体积大，日志价值本来就低）；
- 或在 `Executor` 的 `paramJson` 上标 `@JSONField(serialize = false)` 只在日志 DTO 层面排除。

### 6.4 `getExecutorList` 加单条容错

```java
for (RobotExecute robotExecute : robotExecuteList) {
    String robotId = robotExecute.getRobotId();
    try {
        // ...原有装配逻辑...
    } catch (Throwable e) {                     // 单条失败不影响整张列表
        log.warn("装配执行参数失败, robotId={}", robotId, e);
        Executor degraded = new Executor();
        degraded.setRobotId(robotId);
        degraded.setRobotName(robotExecute.getName());
        degraded.setHaveParam(false);
        result.add(degraded);                   // 降级保留，前端可见、可点
    }
}
```

### 6.5 回归测试

`SensitiveDataSanitizerTest` 加一条即可锁死这个坑：

```java
@Test
void longQuotedValueShouldNotOverflow() {
    StringBuilder sb = new StringBuilder("{\"paramJson\":\"[{\"v\":\"");
    for (int i = 0; i < 5000; i++) sb.append('A');
    sb.append("\"}]\"}");
    String out = SensitiveDataSanitizer.sanitize(sb.toString());
    assertTrue(out.contains(SensitiveDataSanitizer.REDACTED));
    assertFalse(out.contains("AAAA"));
}
```

## 7. 修复状态（重要）

截至 `main`（2026-11-05）：

- ✅ `QUOTED_ARRAY_VALUE` 已是非递归 `[^\]]*`（**这条没问题**）
- ❌ `QUOTED_VALUE` 仍是递归的 `"(?:\\.|[^"\\])*"` → **根因仍在**
- ❌ `formatResult` 仍是 `catch (Exception e)` → 抓不到 `StackOverflowError`
- ❌ 仍是先 `sanitize` 后 `substring`
- ❌ `getExecutorList` 仍无 per-robot try/catch

即：**这个 P0 在最新代码上依然可复现**，不是历史遗留。

## 8. 为什么值得优先修

- 触发门槛很低：一个正常业务流程有 10 个可配置参数（数据库、目录、批次大小、重试次数…）很容易超过 1700 字符
- 故障表现与原因**完全无关**：用户以为是「定时任务坏了」，实际是「日志脱敏」
- **一个流程打挂整个列表**，且只有创建者能复现 → 极易被误判为「客户端缓存」「环境问题」，实际排障要反编译 jar
- 业务方唯一的自救方式是**删掉自己的流程**，数据损失不可控
