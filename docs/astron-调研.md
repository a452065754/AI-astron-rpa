# 星辰开源 · 参考项目调研报告

> 调研对象：iFlytek（科大讯飞）开源的 **Astron Agent** 与 **Astron RPA**
> 调研日期：2026-10-28
> 目的：为「星辰开源」项目立项提供架构、技术栈、模块与生态的参考基线

---

## 1. 两个项目是什么

| 维度 | astron-agent | astron-rpa |
| --- | --- | --- |
| 一句话定位 | 企业级、商业友好的 **Agentic Workflow 开发平台** | 企业级 **RPA 桌面应用**（低代码/无代码） |
| 核心能力 | AI 工作流编排、模型管理、AI/MCP 工具集成、RPA 自动化、团队协作 | 可视化流程设计、桌面软件与网页自动化、图像识别、UI 自动化 |
| 关系 | 原生支持调用 RPA 工作流节点 | 原生支持在 RPA 中调用 Agent 工作流，**双向打通** |
| 开源协议 | **Apache 2.0**（可商用、无限制） | **Open Source License**（仓库 LICENSE 为自定义开源协议，需细读条款） |
| 维护方 | iFlytek（讯飞） | iFlytek（讯飞） |

两者共同构成「**决策（Agent）+ 执行（RPA）**」闭环：Agent 负责推理与决策，RPA 负责落地操作，互相调用。

---

## 2. 架构对比

### 2.1 astron-agent
- 平台型服务，无桌面端，偏「后端服务 + Web 前端」。
- 关键组件：
  - **认证网关**：Casdoor（默认账号 `admin / 123`）。
  - **工作流编排**：可视化编排 AI Workflow。
  - **模型管理**：支持 API 快速接入 + 企业级 MaaS 本地集群一键部署。
  - **工具生态**：内置讯飞开放平台海量 AI 能力，支持 MCP 工具接入。
  - **高可用**：提供完整高可用版本开源，支持一键部署。
- 部署：Docker Compose（推荐）/ Helm（K8s，开发中）。

### 2.2 astron-rpa
- 前后端分离 + 桌面客户端（Electron）。
- 分层：
  - **前端**：Vue 3 + TypeScript + Electron（桌面应用）。
  - **后端**：Java Spring Boot + Python FastAPI（微服务，承载业务与 AI 能力）。
  - **引擎层**：Python，集成 **300+ 原子能力**，支持图像识别与 UI 自动化。
  - **部署**：Docker（含 Casdoor 鉴权 + MySQL）。
- 客户端：Windows 10/11（主支持），需 Node ≥22 / Python 3.13.x / JDK 8+ / pnpm ≥9 / UV 0.8+ / 7-Zip / SWIG。

> 一句话：agent 是「云侧大脑」，rpa 是「端侧手脚」；agent 偏服务化，rpa 偏桌面 + 引擎。

---

## 3. 技术栈对比

| 层 | astron-agent | astron-rpa |
| --- | --- | --- |
| 前端 | Web（具体框架 README 未详列，生态含 nginx 代理） | Vue 3 + TypeScript + Electron |
| 后端 | 微服务（含 Casdoor 鉴权网关） | Java Spring Boot + Python FastAPI |
| 引擎/核心 | 工作流编排引擎 + 模型/MCP 接入 | Python 引擎（UI 自动化 + 图像识别） |
| 鉴权 | Casdoor | Casdoor + MySQL |
| 部署 | Docker Compose / Helm | Docker（客户端另发二进制） |
| 大模型 | 多模型 API + 本地 MaaS 集群 | 通过 agent 集成 AI 能力 |

---

## 4. 核心模块 / 组件对比

### astron-rpa 的组件生态（`astronverse.*`，值得借鉴的模块化思路）

| 类别 | 包名 | 职责 |
| --- | --- | --- |
| 核心组件 | `system` | 系统操作、进程管理、截图 |
| | `browser` | 浏览器自动化、网页操作 |
| | `gui` | GUI 自动化、鼠标键盘操作 |
| | `excel` | Excel 表格操作、数据处理 |
| | `vision` | 计算机视觉、图像识别 |
| | `ai` | AI 智能服务集成 |
| | `network` | 网络请求、API 调用 |
| | `email` | 邮件收发 |
| | `docx` / `pdf` | Word / PDF 文档处理 |
| | `encrypt` | 加解密 |
| 执行框架 | `actionlib` | 原子操作定义与执行 |
| | `executor` | 工作流执行引擎 |
| | `picker` | 元素拾取引擎 |
| | `scheduler` | 引擎调度器 |
| | `trigger` | 引擎触发器 |
| 共享库 | `baseline` | RPA 框架核心 |
| | `websocketserver` / `websocketclient` | WebSocket 通信 |
| | `locator` | 元素定位技术 |

### astron-agent 的工具/能力生态
- **SkillHub**（兄弟项目）：自托管 agent 技能注册中心，发布/版本化/治理技能包，从 Astron Agent 加载运行。
- **MCP 工具**：原生接入 MCP，工具可插拔。
- **讯飞开放平台能力**：海量已验证 AI 能力，开箱即用。

---

## 5. 部署与运维对比

| 项 | astron-agent | astron-rpa |
| --- | --- | --- |
| 快速启动 | `docker compose -f docker-compose-with-auth.yaml up -d` | `docker compose up -d mysql casdoor` → 同步凭证 → `up -d` |
| 鉴权初始化 | 需先完成 Casdoor 密码迁移 | 需初始化独立 Casdoor 凭证（脚本 `sync-casdoor-credentials.py`） |
| 客户端 | 纯 Web | 需单独构建/下载桌面客户端（`./build.bat`） |
| HTTPS | 反向代理 + 自签/CA 证书 | 文档 `HTTPS_DEPLOYMENT.md` 要求双域名证书 |
| 高可用 | 明确支持高可用版本 | 强调可观测性、可扩展性 |

---

## 6. 开源协议与生态观察

- **astron-agent**：Apache 2.0，商业友好，可直接商用、修改、分发。
- **astron-rpa**：标记为 "Open Source License"，**非标准协议**，立项前必须细读其 LICENSE 文件的具体限制（如是否允许闭源衍生、是否要求署名、是否限制商用场景）。
- 两者都内建 **Casdoor** 做统一鉴权，这是可复用的成熟组件。
- 两者都强调 **MCP** 作为工具/服务接入标准，符合当前 Agent 生态趋势。

---

## 7. 对「星辰开源」的启示（初步）

1. **决策 + 执行分离**：沿用「Agent（大脑）+ RPA（手脚）」的双仓/双模块架构，二者通过标准协议（MCP / HTTP-SSE）双向调用。
2. **组件化原子能力**：rpa 的 `astronverse.*` 模块化拆法值得借鉴——把自动化能力拆成可插拔原子包，配可视化编排。
3. **统一鉴权**：直接复用 Casdoor，避免自研鉴权。
4. **MCP 优先**：工具/服务接入统一走 MCP，降低集成成本。
5. **协议合规**：若计划商用或二次分发，**优先选择 Apache 2.0 类协议**，rpa 的自定义协议需谨慎对待。
6. **部署标准化**：Docker Compose 一键起，降低试用门槛。

---

## 8. 待确认问题（决定星辰开源走向）

1. **定位**：是完全复刻 astron 技术栈？还是在其思想上自研一套？或是只取 RPA 引擎/某模块？
2. **技术栈取舍**：是否沿用 Vue3+Electron / Spring Boot+FastAPI / Python 引擎？还是换成更轻量或团队更熟悉的栈？
3. **范围**：首阶段只做 Agent？只做 RPA？还是二者都做？
4. **协议**：星辰开源自身采用哪种开源协议（建议 Apache 2.0）？
5. **首交付物**：是先出架构设计文档，还是先搭最小可运行骨架（MVP）？

详见项目根 README 与立项讨论。
