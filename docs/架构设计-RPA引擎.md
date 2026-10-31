# 星辰开源 · RPA 引擎架构设计（v0.1 草案）

> 状态：草案，待技术栈拍板
> 立项决策（2026-10-28）：**定位 = RPA 引擎优先**；**首步 = 架构设计文档**；**技术栈 = 架构阶段再定**（本文给出候选，第 5 节）
> 参考：[iFlytek astron-rpa](https://github.com/iflytek/astron-rpa)、[astron-agent](https://github.com/iflytek/astron-agent)（见 `docs/astron-调研.md`）

---

## 0. 决策记录（ADR）

| 项 | 决策 | 备注 |
| --- | --- | --- |
| 首期形态 | 只做 RPA「执行侧」引擎 | Agent「决策侧」留作远期 |
| 首步交付 | 本架构文档 | 技术栈在本文定候选，待拍板 |
| 远景 | 决策 + 执行闭环（Agent + RPA 双向） | 与 astron 一致的闭环思路 |
| 协议 | 建议 Apache 2.0 | astron-rpa 为自定义协议，谨慎借鉴 |

---

## 1. 目标与范围

### 1.1 首期范围（MVP）
- **执行引擎**：基于 Python 的自动化流程执行引擎。
- **原子能力**：UI 自动化、浏览器、文件/系统、图像识别等核心能力包。
- **可视化编排**：节点拖拽式流程设计器（Web 或桌面形态待定）。
- **调度触发**：本地手动执行 + 定时（cron）+ API 触发。
- **实时通信**：设计器 ↔ 引擎 双向指令与日志流。

### 1.2 明确不在首期
- Agent/LLM 推理节点、大模型工作流编排。
- 云端多租户、高可用集群部署。
- 团队协作市场 / 卓越中心 / 权限矩阵。

---

## 2. 总体架构（分层）

```
┌─────────────────────────────────────────────┐
│  L1  可视化设计器  (前端：拖拽编排 / 元素拾取)   │
├─────────────────────────────────────────────┤
│  L2  接口服务    (后端 API：流程 CRUD / 触发)   │
├─────────────────────────────────────────────┤
│  L3  执行引擎    (executor：解析 / 调度 / 状态)  │
├─────────────────────────────────────────────┤
│  L4  原子能力库  (actionlib：ui/browser/excel…) │
└─────────────────────────────────────────────┘
   横切：scheduler/trigger · websocket · 持久化 · 鉴权(远期)
```

---

## 3. 核心模块划分

### 3.1 原子能力层（参考 `astronverse.*`，聚焦引擎侧）
| 包（建议名） | 职责 | 首期优先级 |
| --- | --- | --- |
| `core.ui` | 鼠标/键盘/窗口/控件（UIA）操作 | **P0** |
| `core.browser` | 浏览器自动化、网页元素操作 | **P0** |
| `core.fs` | 文件、目录、进程、截图、系统 | **P0** |
| `core.vision` | 图像识别、模板匹配、OCR | P1 |
| `core.excel` | 表格读写、数据处理 | P1 |
| `core.net` | HTTP 请求、API 调用 | P1 |
| `core.docx` / `core.pdf` | Word / PDF 处理 | P2 |
| `core.email` | 邮件收发 | P2 |
| `core.crypto` | 加解密 | P2 |

### 3.2 执行引擎 `executor`
- 工作流解析（DAG / 顺序 / 分支 / 循环 / 子流程）。
- 节点调度与并发控制。
- 变量与上下文（作用域、类型）。
- 异常处理策略：停止 / 跳过 / 重试（含退避）。
- 执行日志、断点续跑、回放。

### 3.3 元素定位 `locator` / 拾取 `picker`
- 定位策略：UIA 树、坐标、OCR 文本、图像模板。
- 拾取器：设计器内实时抓取元素并生成定位表达式。

### 3.4 调度与触发 `scheduler` / `trigger`
- 手动执行、定时（cron）、API 触发；事件触发留远期。

### 3.5 通信 `transport`
- WebSocket：引擎状态上报、设计器下发、实时日志流。

### 3.6 持久化 `store`
- 工作流定义、执行记录、凭证（**加密存储**）。

---

## 4. 工作流数据模型（Schema 草案）

```json
{
  "version": "1.0",
  "name": "demo-flow",
  "variables": { "url": "https://example.com" },
  "nodes": [
    { "id": "n1", "type": "core.browser.open", "params": { "url": "${url}" }, "next": "n2" },
    { "id": "n2", "type": "core.ui.click",     "params": { "selector": "..." }, "next": null }
  ],
  "edges": [ { "from": "n1", "to": "n2" } ]
}
```
**节点类型枚举**（首期）：`core.browser.*`、`core.ui.*`、`core.fs.*`、`core.net.*`、`control.branch`、`control.loop`、`control.subflow`。

---

## 5. 技术选型候选（⚠️ 待拍板）

| 层 | 候选 A（推荐） | 候选 B | 候选 C | 待定说明 |
| --- | --- | --- | --- | --- |
| 后端框架 | **FastAPI**（异步、OpenAPI） | Flask | 纯 asyncio | 推荐 FastAPI，生态与文档好 |
| 前端框架 | **Vue 3**（贴近参考） | React | — | 参考项目用 Vue3 |
| 桌面封装 | Web（纯浏览器） | Electron | Tauri | 影响打包体积与复杂度 |
| 引擎运行时 | **Python 3.11+**（生态稳） | Python 3.13（astron 用） | — | 3.13 部分 C 扩展兼容性待验 |
| 通信 | **WebSocket** | gRPC | — | 实时日志流场景 WebSocket 更轻 |
| 持久化 | **SQLite**（MVP） | PostgreSQL | — | MVP 单文件，远期可换 |
| 图像识别 | **OpenCV + 模板匹配** | 引入 OCR 引擎 | — | 先模板匹配，OCR 后续 |
| 鉴权 | 简单 JWT（MVP） | Casdoor（远期） | — | 复用 astron 的 Casdoor 思路 |

> 上述「推荐」仅为默认建议，**最终选型需你拍板**（见第 9 节）。

---

## 6. 执行模型
- 有向图执行；支持顺序、并行分支、条件分支、循环、子流程嵌套。
- 失败策略：停止 / 跳过 / 重试（指数退避）。
- 变量作用域：全局 + 节点局部；支持 `${var}` 插值。

## 7. 安全与合规
- 凭证加密存储（不落明文）。
- 全量操作审计日志。
- 星辰开源自身协议建议 **Apache 2.0**（商用友好）。

## 8. 里程碑路线图
| 里程碑 | 内容 |
| --- | --- |
| M0 | 架构设计（本阶段） |
| M1 | 引擎骨架 + 3 个 P0 原子能力（ui / fs / browser 之一） |
| M2 | 设计器 MVP（拖拽 + 参数配置） |
| M3 | 调度（cron/API）+ 持久化 + 执行日志 |
| M4 | 扩展原子能力 + 图像识别（vision） |

## 9. 待你拍板清单
1. 技术栈具体选型（第 5 节逐项确认）。
2. 设计器形态：纯 Web 还是桌面（Electron）。
3. Python 版本（3.11 vs 3.13）。
4. 首期是否需要鉴权。
5. 开源协议最终确认（建议 Apache 2.0）。

## 10. 参考
- `docs/astron-调研.md` —— astron-agent / astron-rpa 调研
- astron-rpa 组件生态 `astronverse.*`（system/browser/gui/excel/vision/ai/network/email/docx/pdf/encrypt + actionlib/executor/picker/scheduler/trigger + baseline/locator/websocket）
