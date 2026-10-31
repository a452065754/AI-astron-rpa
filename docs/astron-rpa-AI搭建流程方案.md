# Astron RPA —— AI 搭建流程（路线 B）技术方案

> 背景：用户已明确 A（AI 调度已有流程）由 OpenClaw 插件完成（只 `get_workflows` + `execute_workflow_sync`）。
> 本文件聚焦 **B：AI 直接搭建/生成 RPA 流程定义**，而非仅调度。

## 1. 关键澄清：A 与 B 的边界

| 能力 | 现状 | 接口 |
| --- | --- | --- |
| A · AI 调度已有流程 | ✅ 已支持（OpenClaw 插件 `doctorbruce/AstronRPA-OpenClaw-Plugin`） | `GET /workflows/get`、`POST /workflows/execute` |
| B · AI 搭建（生成）流程 | ❌ 公开 API 不支持，需走内部接口 | `robot-service` 的 `/robot-design` 创建接口 |

OpenClaw 插件动作（已核实 `plugin.ts` / README）：
- `get_workflows` → `GET .../workflows/get`
- `execute_workflow_sync` → `POST .../workflows/execute`

对外 `openapi-service` 的 `POST /workflows/upsert` **只收 metadata**（`project_id/name/description/parameters`），**不含节点逻辑**——所以公开 API 只能"登记+触发"，不能"生成节点"。

## 2. 工作流的数据模型（AI 要生成的真实格式）

来源：`frontend/packages/web-app/src/ast/ASTNode.ts`

```ts
export interface ProcessNode {
  id: string       // 节点唯一 id
  type: string     // 原子 key，见第 3 节词汇表
  [key: string]: any  // 该原子的参数（由后端 /atom 下发表单 schema 决定）
}
```

- 工作流是 **树（parent/children）**，不是自由图。`ASTNode` 用 `id`/`type`/`children` 表达层级。
- 控制流用**成对节点**表达：`If`/`IfEnd`、`ForList`/`ForListEnd`、`Try`/`TryEnd`、`While`/`WhileEnd`、`Group`/`GroupEnd` 等。`LOOP_END_MAP` 定义开始→结束的配对。
- `else`/`elseif`/`catch`/`finally` 作为开始节点的**兄弟子节点**挂在同级（特殊层级规则见 `ASTNode.calculateLevel`）。

## 3. 原子词汇表（AI 可生成的节点类型）

来源：`frontend/packages/web-app/src/views/Arrange/config/atomKeyMap.ts`

### 控制流（Code.* 命名空间）
- `Code.If` / `Code.IfEnd`
- `Code.ElseIf` / `Code.ElseIfEnd`、`Code.Else` / `Code.ElseEnd`
- `Code.Try` / `Code.TryEnd`、`Code.Catch` / `Code.CatchEnd`、`Code.Finally` / `Code.FinallyEnd`
- `Code.ForStep` / `Code.ForEnd`（步进循环）
- `Code.ForDict` / `Code.ForEnd`（字典循环）
- `Code.ForList` / `Code.ForListEnd`（列表循环）
- `Code.While` / `Code.WhileEnd`（条件循环）
- `Code.Break`、`Code.Continue`
- `Code.Group` / `Code.GroupEnd`（分组）
- `Code.Process`（运行子流程）、`Code.Module`（运行子模块）

### 领域原子（其他命名空间，完整目录由后端 `/atom` 元数下发）
- `CV.is_image_exist`（图像存在判断，配 `CV.is_image_exist_end`）
- `File.file_exist`、`Folder.folder_exist`
- `Window.exist`（窗口存在判断）
- `Excel.loop_excel_content`（Excel 内容循环）
- `BrowserElement.loop_similar`（浏览器相似元素循环）
- `DataTable.loop_data_table`（数据表循环）
- `Script.process`、`Script.module`

> 浏览器/Excel/窗口/鼠标/键盘/应用等**具体动作原子**（open-app、click、input、get-text…）的完整清单与参数 schema，由 `robot-service` 的 `/atom` 基础元数据接口在运行时下发，前端据此动态渲染表单。AI 生成前需先拉取该目录。

## 4. 创建流程的真实入口（B 落点的关键）

来源：`backend/robot-service/README.zh.md`

- 服务：`robot`（Spring Boot），端口 `8040`，上下文 `/api/robot`。
- **`/robot-design`**：创建、重命名、列表、详情、复制、删除、分享 —— **这是接收"含节点"完整流程定义的创建接口**。
- 配套：`/robot-version`（发布/启用）、`/robot-execute`（执行）、`/atom`（原子目录）、`/element`（UI 元素库）、`/param`（参数）。

鉴权：`robot-service` 集成 Casdoor SSO（JWT 过滤器）。前端会话或等效 token 才能调用。对外 `openapi-service` 走 API Key，但不暴露节点级创建。

## 5. B 的真正难点（必须面对）

| 难点 | 说明 | 应对 |
| --- | --- | --- |
| **UI 元素定位** | click/input 等原子需要目标元素（xpath/坐标/CV 图像）。元素由设计器"录制"捕获，存于 `/element` 库 | 让 AI 仅引用**已存在的元素库条目**（按名称解析），或先录制元素再生成流程 |
| **原子参数 schema** | 每个原子参数形状不同 | 先拉 `/atom` 目录，作为 LLM 的"工具/函数"规范 |
| **内部接口鉴权** | `/robot-design` 需 Casdoor 会话 | 复用 openapi 的 API Key 通道（若 openapi 有等价创建），或在服务端用会话 token 调用 |
| **流程树合法性** | if/else/try/finally 的配对与层级有强约束（`orderMap`/`LOOP_END_MAP`） | 生成后用校验规则自检，再提交 |

## 6. 推荐落地架构（B1：程序化生成）

```
自然语言流程描述
      │
      ▼
[LLM / DeepSeek]  ← 注入：原子词汇表 + 元素库 + 参数 schema
      │  产出
      ▼
ProcessNode 树 (JSON)   ← 符合 ASTNode 模型，经合法性自检
      │
      ▼
[流程创建适配层]  ← 调 robot-service /robot-design（或 openapi 等价接口）
      │
      ▼
RPA 流程已生成 → OpenClaw/execute 触发执行（A 闭环）
```

- LLM 不直接拼 HTTP，只产出**结构化的 ProcessNode 树**（受 atom 词汇 + 元素库约束），降低出错率。
- 元素依赖：AI 流程中的 click/input 目标限定为"元素库里已有的条目"（按业务名引用，适配层解析成真实 elementId）；新增元素需先录制。

## 7. 落地前还差的一块实证

要真正把"生成 → 创建"跑通，需从用户已部署的 `.130`（RPA 服务端）取两样样本：
1. **一个真实流程的节点树 JSON**（经 openapi `get_workflows` 或 `robot-design` 详情），证实 `{id,type,...params}` 的实际落库形态与包裹结构（root 节点、位置坐标等）。
2. **`/atom` 原子目录**与 **`/element` 元素库**（AI 生成时的"字典"）。

拿到样本后即可写出确定性的"AI 流程生成器"并对接 `/robot-design`。

## 8. 备选 B2（不碰内部接口）

若不想逆向 `/robot-design`：用 DeepSeek Harness / computer-use 直接**驱动 RPA 设计器 GUI**（桌面客户端 `D:\Program Files\astron-rpa`）可视化拖拽搭建。优点：零内部接口依赖；缺点：GUI 自动化脆弱、慢、难批量化。仅在 B1 受鉴权阻塞时作为 fallback。
