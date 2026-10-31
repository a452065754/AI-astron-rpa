# Astron RPA 版本更新对比：你部署的 latest 镜像 vs 最新代码

> 分析日期：2026-10-28
> 数据来源：GitHub Releases + main 分支 commits（仓库根目录无 CHANGELOG.md，故取自 releases/commits）
> 对应部署：`ghcr.io/iflytek/astron-rpa/*:latest`（**rpa-auth 镜像构建时间已确认：2026-10-22T07:27:51Z，落后 6 天**）

---

## 0. 版本对应关系与前提（先说清楚）

- 你部署的是 `latest` 标签镜像，**没有锁版本**。已通过 `docker inspect` **精确确认**：
  - `rpa-auth:latest` 构建时间：**2026-10-22T07:27:51Z**（北京时间 10-22 15:27）。
- 最新 Release：**v1.1.7**（release notes 涵盖到 #903 / 10-22，**#904 / 10-26 未包含** → tag 在 #903 之后、#904 之前打的）。
- main 分支最新提交：**2026-10-26**（PR #904，hash `df46ed8`）。
- **10-23 ~ 10-25 三天 main 零提交**，10-22 之后只有 #904 这一条。
- 结论：**你的 10-22 镜像基本等于 v1.1.7（最新 Release），与 main 最新仅差 1 个 PR（#904）、落后 4 天，不存在"必须立即更新"的差距。**

**你部署镜像的"年龄"已确认（5 个核心服务批量核对，全为 2026-10-22 同一轮 CI 构建）**：
```bash
for s in ai-service openapi-service resource-service robot-service rpa-auth; do
  echo -n "$s: "; docker inspect ghcr.io/iflytek/astron-rpa/$s:latest --format '{{.Created}}'
done
```
| 服务 | 镜像构建时间 (UTC) |
| --- | --- |
| ai-service | 2026-10-22T07:25:07Z |
| openapi-service | 2026-10-22T07:25:11Z |
| resource-service | 2026-10-22T07:29:21Z |
| robot-service | 2026-10-22T07:27:52Z |
| rpa-auth | 2026-10-22T07:27:51Z |

→ 5 个服务构建时间高度一致（跨度仅 4 分钟），为**同一批 `latest` 构建**，整体对应 v1.1.7（或 v1.1.7 发布前 10-22 的 main HEAD），与 GitHub main 最新（10-26 #904）统一落后 1 个 PR。

---

## 1. v1.1.7（最新 Release，≈ 你当前 latest 应包含）

### 🚀 功能新增
| 功能 | 说明 |
| --- | --- |
| 持久化 MCP 执行管理 | 幂等、取消、故障恢复；完善工作流执行生命周期、鉴权语义、执行记录用户隔离 |
| n8n 集成 | 基于 MCP 的 AstronRPA Community Node 公共框架；JSON 与数据类型工作流集成 |
| 可配置语义选择组件 | 默认 OpenAI 兼容供应商，可选 Jev 配置、独立计费、能力过滤 |
| RISC-V 后端 | 初始能力支持 |
| Windows 桌面自动化发布流程 | 自动构建/发布 Windows 客户端 |
| 客户端体验 | 浏览器插件事件监听、iframe 元素处理、Chrome 插件检测、跨容器拖拽改进 |

### 🐛 BUG 修复 / 稳定性 / 安全
| PR | 内容 |
| --- | --- |
| #845 | 修复任务重试次数判断 off-by-one 错误 |
| #874 | 修复 WebSocket 消息解析失败时静默丢消息（msg 未绑定导致 NameError） |
| #865 | 加固 openapi 工作流执行生命周期与 MCP 错误处理 |
| #862 | 公网端点启用 HTTPS（**注意：你内网 legacy-http 模式不受影响**） |
| #873 | ai-service 停止记录上游 API Key 与聊天内容（防敏感信息泄露） |
| #882 | 加固用户搜索与 SQL 排序安全 |
| #887 | 网关鉴权处理器校验 Bearer Token |
| 安全汇总 | 防止终端凭据/API Key/聊天载荷进日志；限制调度器访问；增加局域网访问保护；提高执行器启动检测超时 |

---

## 2. main 在 v1.1.7 之后（10 月）的最新提交

> 这些可能已被你的 latest 包含（若 10-22~26 构建），也可能略落后。

| 日期 | PR | 内容 | 类型 |
| --- | --- | --- | --- |
| 10-26 | #904 | n8n 新增 Office 文件、桌面 UI、AI 动态风险工作流；修复流程兼容性回退 | feat + fix |
| 10-22 | #903 | 自动化 Windows 桌面发布流程；增强 `RPA_REMOTE_ADDR` 校验、SWIG 下载校验、主窗口加载重试 | chore + fix |
| 10-20~19 | 多 | Secure Preferences 处理、文件路径处理、ai config.yaml、SemanticAI.choose 输入输出说明、pdf 文件输入、依赖修复 | fix |
| 10-16 | — | 改进 Chrome 插件检测逻辑 + 单测 | fix |
| 10-14 | #880 | 可配置语义选择组件 | feat |
| 10-14 | #887 | 网关鉴权校验 Bearer Token | fix(security) |
| 10-10 | #876 | n8n 支持 JSON 与数据类型工作流集成 | feat |
| 10-09 | #897 | 新增贡献指南与安全策略文档 | docs |
| 10-08 | #881 | FAQ 增加 SaaS-to-Cowork 提示、客户端登录 **502/timeout** 条目 | docs(faq) |
| 09-30 | #882 | 加固用户搜索与 SQL 排序 | fix(security) |
| 09-30 | #875 | n8n MCP-based Community Node 公共框架 | feat |
| 09-27 | #874/#873/#872 | WebSocket 解析修复 / ai-service 停止记录 API Key / 月度积分发放金额配置生效 | fix |
| 09-16 | #871 | 持久化 MCP 执行管理（幂等/取消/恢复） | feat |
| 09-11 | #845/#867/#866/#865/#862 | 任务重试 off-by-one / 保留远程运行参数文件与类型 / 保护终端凭据 / 工作流生命周期加固 / 公网 HTTPS | fix + feat |

---

## 3. 与你生产环境特别相关的更新

- **502 问题**：官方 #881 已把"客户端登录 502/timeout"写入 FAQ——与你 `astron_context.md` 第四条"502 就 `restart openresty-nginx`"的方法完全呼应，说明这是官方认可的通用解法。
- **安全修复**（建议更新）：SQL 排序安全(#882)、Bearer Token 校验(#887)、API Key/凭据不进日志(#873/#866)、公网 HTTPS(#862)。这些在 legacy-http 内网模式下也建议跟进；但 #862 的 HTTPS 仅针对公网端点，你内网 `legacy-http` 不受影响，**更新后仍需保持 `SESSION_COOKIE_SECURE=false`**。
- **客户端发布流程改进(#903)**：增强了 `conf.yaml` 的 `remote_addr` 校验，与你 Windows 客户端配置直接相关；若后续更新客户端版本需注意。
- **#904（你唯一落后的 1 个 PR，10-26）**：`feat(n8n)` 新增 Office 文件 / 桌面 UI / AI 动态风险工作流支持，并修复"进程兼容性回退（compatibility regression of processes）"。**若你不使用 n8n，本条对你基本无影响**；若你用 n8n 或遇到进程兼容性回归问题，则值得跟进。

---

## 4. 是否需要更新 / 怎么更新

- **判断（已精确）**：`docker inspect` 确认你的 latest 构建于 **10-22 07:27Z**，基本等于 v1.1.7（release notes 含到 #903）。与 main 最新（10-26 #904）仅差 1 个 PR；**10-23~25 三天 main 零提交，故不存在"悄悄落后很多"的情况**。除非你用到 n8n 或命中进程兼容性回归，否则**无需立即更新**。
- **更新仍走离线 SOP**（见 `astron_context.md` 第五节），**严守红线**：
  1. 先备份 `./data` 和 `.env`；
  2. 不动 Casdoor（不 `down` 含 casdoor 的整个项目、不重建/重启）；
  3. `rpa-auth` 保持 `SESSION_COOKIE_SECURE=false`，Nginx 保持 `DEPLOYMENT_MODE: legacy-http`；
  4. `docker load` 后 `docker compose up -d`，再 `restart openresty-nginx` 刷新网关。
- **数据库 schema 迁移风险**：v1.1.7 新增 `openai_executions` 若干列。若你从很早的旧版直接升到 v1.1.7，启动后报 `schema is not ready` 需执行 `ALTER TABLE`（见 `astron_context.md` 第六条）。

---

## 5. 一句话结论

你部署的 latest 镜像（构建于 **10-22 07:27Z**）基本等于最新版 **v1.1.7**，与 GitHub main 仅差 **1 个 PR（#904 / 10-26，n8n Office 文件 + 桌面 UI + AI 风险工作流 + 进程兼容性回退修复）**，落后 4 天、无功能/BUG 层面的"必更项"。**值得关注的新东西（你已含）**：MCP 执行管理（幂等/取消/恢复）、n8n 集成、多条安全加固（防凭据/Key 泄露、SQL/Bearer 安全）；**稳定相关修复（你已含）**：任务重试 off-by-one、WebSocket 静默丢消息。是否更新取决于是否用 n8n / 是否命中进程兼容性回归——否则按离线 SOP 保持现状即可。
