# A20 复现包：`paramJson` 过长 → 脱敏正则爆栈 → 「添加应用」整包 999999

本目录是 2026-11-05 在正式服 `192.168.0.183` 上排查该事故时留下的**回滚材料与证据**。

| 文件 | 内容 | 用途 |
| --- | --- | --- |
| `process_content_v3_backup.json` | `MES包装标签生成` 主流程 v3 **改动前**的 18 节点原文 | 需要还原节点树时用；写入时配 `where process_content=<旧原文>` 做乐观锁 |
| `c_param_v3_backup.tsv` | 该流程改动前 14 条参数的全字段（`id / var_direction / var_name / var_type / var_value / var_describe / process_id`） | 需要还原参数表时用 |

⚠️ **`c_param_v3_backup.tsv` 里的账号/密码值已脱敏成 `<REDACTED>`**：
本仓库是公开的（2026-11-04 曾因流程参数默认值带真实凭据而被迫改写 git 历史），
凭据一律不落盘。真要做逐字节回滚，从服务端 `c_param` 现取即可。

> ⚠️ **2026-11-05 15:40 复核修正**：根因正则确认为 **`QUOTED_VALUE`**（不是 `QUOTED_ARRAY_VALUE`）。
> `Executor.paramJson` 是 `String`，序列化后是 `"paramJson":"[...]"`（值带引号），
> `QUOTED_ARRAY_VALUE`（要求冒号后紧跟 `[`）匹配不上；`QUOTED_ARRAY_VALUE` 本身也早已是非递归的 `[^\]]*`。
> 复核脚本见 `docs/upstream/A20Repro.java`。下文凡把它写成 `QUOTED_ARRAY_VALUE` 的地方按此更正。

## 根因一句话

平台 `ApiLogAspect.formatResult()` 把接口返回值 `JSON.toJSONString()` 后丢给
`SensitiveDataSanitizer.sanitize(Object)`；真正命中的是**第二个**正则 `QUOTED_VALUE`
（匹配「敏感键 → 数组值」）的值部分 `\[(?:\\.|[^\]\\])*\]` 由 `java.util.regex.Pattern$Loop`
**递归**实现，递归深度 ≈ 匹配字符数。`Executor.paramJson` 是 JSON 数组字符串，
正好被它整段吞掉 → 参数一多就 `StackOverflowError` → 异常冒泡出切面 → 全局兜底 →
`GET /api/robot/triggerTask/robotExe/list` 只剩 `{"code":"999999"}`（HTTP 200、响应体 83 字节）。

## 阈值（生产 JVM 实测，判据 = `paramJson` 序列化长度 `inner`）

| inner | 日志串 | 结果 |
| --- | --- | --- |
| 995 / 1139 / 1279 / **1355** | 1268 / 1438 / 1578 / 1628 | OK |
| **1673** | 2112 | StackOverflowError（NCIC订单处理-华为 真实 8 条参数，3/3 复现） |
| 2153 | 2473 | StackOverflowError |

工程上按 **≤1050 字符 / 6 条** 用，硬线 **1450 字符 / 8 条**
（模型见 `scripts/param_budget.py`，生产数据回算逐字符一致）。

## 复现怎么跑

- 零依赖版：`javac --release 8 -encoding UTF-8 ../A20_sanitizer_regex_stackoverflow.java`
  → `java -cp . A20_sanitizer_regex_stackoverflow ../QUOTED_ARRAY_VALUE.regex`
- 贴近生产版（推荐）：把 jar 里的 `com.iflytek.rpa.utils.SensitiveDataSanitizer` 抽出来，
  反射调 `sanitize(Object)`，喂 `{"paramJson":"..."}` 形状的日志串，
  在 `.183` 容器内用生产 JVM（OpenJDK 1.8.0_504）跑。
  ⚠️ `sanitize(String)` 和 `sanitize(Object)` **不是同一条路径**，别测错入口。

## 现在由谁守着

- 生成期：`spec_lint` 规则 11（`nl2spec` 自愈闭环会自动改规格）
- 发布期：`publish_flow.guard_param_budget()`（红灯中止发布）
- 巡检期：`scripts/param_health.py`（只读扫全库，已接进 `release_check`）

详见 `docs/开发进度.md` §26.6e / §26.6f / §26.10 与 `docs/平台缺陷清单.md` A20。
