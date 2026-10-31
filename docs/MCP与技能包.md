# 技能包与 MCP

> 这一篇讲怎么让 **AI 自己**完成「安装 → 配 Key → 用自然语言生成流程」。
> 手工部署见 [部署使用文档](部署使用文档.md)。

---

## 0. 三件套的分工

同一套能力，三种入口，共用一份 `flows/*.json` 规格和同一个流水线：

| 入口 | 是什么 | 谁用 | 适合场景 |
| --- | --- | --- | --- |
| **命令行**（`scripts/*.py`） | 外挂工具链本体 | 人 / 脚本 | 手工调试、CI |
| **技能包**（`skill/astron-rpa-flow/`） | 给 AI 的操作手册 | AI 助手（WorkBuddy 等） | 说一句话让 AI 全流程代办 |
| **MCP 服务**（`mcp/server.py`） | 标准协议工具端点 | 任何 MCP 客户端 | Claude Desktop / Cursor / 自研 Agent |

三者不冲突：技能包告诉 AI「按什么顺序做什么」，MCP 把「做什么」变成可调用的工具，
命令行是它们共同调用的底层。

```
用户：「把这张表按部门拆成多个文件」
   │
   ├─ 技能包：教 AI 先查有没有现成规格、再校验、再真跑、再交付
   ├─ MCP   ：rpa_list_flows → rpa_generate → rpa_run
   └─ CLI   ：ai_flow.py --nl "..." / --spec flows/x.json
                    │
                    ▼
        flows/*.json 规格 → 平台里的固定流程
```

---

## 1. 一句话安装（AI 视角）

用户只需要说：**「帮我装这个项目」**。AI 执行：

```bash
git clone https://github.com/a452065754/AI-astron-rpa.git
cd AI-astron-rpa
python scripts/install.py
```

这一步会：装依赖 → 生成配置骨架 → **装技能包** → **登记 MCP** → 环境自检。

然后用户说：**「Key 是 xxxx」**。AI 执行：

```bash
python scripts/install.py --key xxxx --host 192.168.76.130:32742 --robot-id <robotId>
```

之后用户直接说需求即可。

### install.py 参数

| 参数 | 说明 |
| --- | --- |
| `--key` | openapi API Key（平台「开放平台 / API 密钥」页生成） |
| `--host` | 服务端网关，如 `192.168.76.130:32742`；留空则从设计器 `conf.yaml` 自动探测 |
| `--robot-id` | 默认机器人 id |
| `--client-python` | 设计器自带 Python 路径（留空自动探测） |
| `--local-port` | 设计器本地网关端口（默认 13159） |
| `--no-skill` | 不装技能包 |
| `--no-register-mcp` | 不登记 MCP |
| `--skip-deps` / `--skip-doctor` | 跳过依赖安装 / 自检 |
| `--print-mcp-config` | 只打印 MCP 配置片段（给别的客户端用） |
| `--json` | 机器可读输出 |

全部步骤**幂等**，重复跑不会坏；MCP 登记是**合并**，不会覆盖已有服务。

---

## 2. 技能包

### 装在哪

```
~/.workbuddy-ai/skills/astron-rpa-flow/
├── SKILL.md            # 技能本体（AI 读这个）
└── PROJECT_ROOT.txt    # 仓库绝对路径（AI 靠它定位项目）
```

`install.py` 自动复制。也可以手动拷：`cp -r skill/astron-rpa-flow ~/.workbuddy-ai/skills/`，
但记得自己写 `PROJECT_ROOT.txt`。

### 什么时候会被触发

用户说这些话时，AI 应当加载它：

> 「帮我用 RPA 做 XX」「自然语言生成 RPA 流程」「让 AI 操作 RPA」
> 「把这张表按部门拆开」「把这个文件夹的 PDF 都转成 Word」
> 「astron-rpa」「星辰开源 RPA」「帮我装这个项目」「Key 是 xxx」

### 技能里定的规矩（关键）

1. **先查现成的再自己拼** —— 不许跳过 `--stage draft` 直接造新流程。
2. **能力降级顺序**：现有原子 → Python 子流程 → 新原子。
3. **不猜**：听不懂就列流程让用户改说法，绝不静默跑成别的流程。
4. **不跳校验**：必须 `IS_PASS: YES` + 本地真跑 SUCCESS 才能交人工评审。
5. **批量要明说**：单文件流程遇到「整个文件夹都转」，主动拦下。
6. **凭据不外泄**：Key 只写 `rpa_config.json`，不打印全文、不提交 git。

> 装完技能后，若 AI 没立刻认出来，**重启客户端**或刷新技能列表。

---

## 3. MCP 服务

### 启动方式

```bash
python mcp/server.py     # stdio，每行一条 JSON-RPC 消息
```

零依赖实现（不需要 `pip install mcp`），只支持 MCP 的必要子集：
`initialize` / `notifications/initialized` / `tools/list` / `tools/call` / `ping`。

### 客户端配置

`install.py` 会自动写进 `~/.workbuddy-ai/mcp.json`（合并，不覆盖其他服务）：

```json
{
  "mcpServers": {
    "astron-rpa": {
      "command": "C:\\Python314\\python.exe",
      "args": ["F:\\AI-Workbench\\10-Projects\\星辰开源\\mcp\\server.py"],
      "env": { "RPA_PYTHON": "C:\\Python314\\python.exe" }
    }
  }
}
```

给别的客户端（如 Claude Desktop 的 `claude_desktop_config.json`）用，直接：

```bash
python scripts/install.py --print-mcp-config
```

把输出整段贴进去即可。

> **注意**：`command` 必须指向一个**装了 `requests`** 的 Python。
> 服务端也会自己再挑一次（`RPA_PYTHON` > 当前解释器 > 常见路径），但显式指定最稳。

### 10 个工具

| 工具 | 作用 | 有副作用吗 |
| --- | --- | --- |
| `rpa_setup` | 写配置 + 自检（用户发 Key 后调这个） | 写 `rpa_config.json` |
| `rpa_doctor` | 环境自检，逐项给修法 | 无（只读） |
| `rpa_list_flows` | 列出现有流程规格（名称/示例/参数） | 无（只读） |
| `rpa_atom_catalog` | 查可用原子（357 个，按域分组，带必填参数与枚举） | 无（只读） |
| `rpa_lint_spec` | 校验规格，报错带「第几个节点 + 怎么改」 | 无（`spec_json` 走临时文件） |
| `rpa_generate` | 自然语言 → 匹配或校验规格（draft） | 只在给了 `spec_json` 时写 `flows/` |
| `rpa_run` | **主入口**：校验→预检→落库→真跑→评审单 | 写平台主流程、执行 |
| `rpa_publish` | 发布机器人版本 | 发布版本 |
| `rpa_invoke` | 用 Key 触发已发布版本 | 执行 |
| `rpa_make_module` | 本地 `.py` → 平台子流程模块 | 建机器人/模块、改规格 |

### AI 自动拆解的闭环

「自然语言 → 固定流程」真正跑通，靠的是这四步循环：

```
rpa_list_flows       有没有现成的？有就复用
      ↓ 没有
rpa_atom_catalog     查有哪些原子（--domain / --search / --atom）
      ↓
rpa_lint_spec        写完规格先校验，报错带定位与修法 → 改到无错误
      ↓
rpa_run              落库 + 真跑 + 评审单
```

对应的命令行：

```bash
python scripts/atom_catalog.py --domain Excel --detail
python scripts/spec_lint.py flows/新流程.json
python scripts/ai_flow.py --spec flows/新流程.json
```

### 典型调用序列

```
1. rpa_doctor            → 确认环境没问题
2. rpa_list_flows        → 看有没有现成的
3. rpa_atom_catalog      → 没现成的，查该用哪些原子
4. rpa_lint_spec         → 写完规格先校验（反复改到无错误）
5. rpa_generate          → 构建 + 结构校验（不落库）
6. rpa_run               → 落库 + 真跑 + 出评审单
7. （人工评审通过后）
8. rpa_publish(version)  → 发布
9. rpa_invoke(params)    → 用 Key 调用
```

---

## 4. 端到端示例（用户视角）

> **用户**：帮我装这个项目
> **AI**：`python scripts/install.py` → 「装好了，还需要一个 API Key」
>
> **用户**：Key 是 tDr8...，服务器 192.168.76.130:32742
> **AI**：`python scripts/install.py --key ... --host ...` → 「自检全绿」
>
> **用户**：把 demo/订单.xlsx 按客户拆成多个文件
> **AI**：`rpa_generate` 看命中 → 没完全命中，按「原子→Python」拆解出规格 →
> `rpa_run` → 落库 + 真跑成功 → 给你评审单路径 + 去设计器哪一步点运行

全程用户只说三句话，剩下的是 AI 按技能包的规矩做的。

---

## 5. 故障排查

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| AI 不加载技能 | 没装 / 没刷新 | 看 `~/.workbuddy-ai/skills/astron-rpa-flow/SKILL.md` 在不在；重启客户端 |
| MCP 客户端里看不到 `astron-rpa` | 配置没生效 / 需重启 | 检查 `mcp.json`；重启客户端；确认 `command` 的 Python 存在 |
| MCP 工具调用报 `requests` 缺失 | 启动用的 Python 没装依赖 | 改 `command` 或设 `RPA_PYTHON` 指向装好依赖的解释器 |
| MCP 客户端解析失败 | stdout 被日志污染 | 本项目日志只写 stderr；若二次开发，务必遵守 |
| `rpa_setup` 后自检仍 ❌ | 设计器没装/没登录 | 见 [部署使用文档 §6](部署使用文档.md) |

---

## 6. 安全

- `rpa_config.json`、`docs/_local_secrets.md` 含真实凭据，**已在 `.gitignore`**。
- MCP 服务的日志只写 stderr，不回显 Key。
- `install.py` 不打印 Key 全文，只报「已写入」。
