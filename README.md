# 星辰开源 · AI 操作 RPA

> 用一句自然语言，生成一条**固定、可离线运行**的 RPA 流程。

本项目把「人话 → RPA 流程」做成一条可交付的流水线：**生成 → 校验 → 本地真跑 → 评审单 →（人工通过后）发布**。
流程搭好之后**不需要 AI 参与**，在 Astron RPA 设计器/调度里就能独立运行。

## 核心设计

| 原则 | 说明 |
| --- | --- |
| **规格即契约** | 每条流程一份 `flows/<流程名>.json`（名称、示例话术、关键词、参数、节点）。改流程 = 改这个文件再重跑，落库幂等，不会产生重复流程 |
| **能力降级顺序** | ① 现有原子能拼出来就用原子 → ② 拼不出来用 Python 子流程（`Script.module`）→ ③ 实在不行才开发新原子 |
| **可迁移** | 服务端地址、API Key、客户端路径全部收敛在 `rpa_config.json`；换环境只改这一个文件。模块 id 做成流程参数，迁移时重建模块即可 |
| **不猜** | 听不懂就明说并列出可用流程，不静默跑成别的流程 |

## 快速开始

### 一句话安装（推荐）

```bash
git clone https://github.com/a452065754/AI-astron-rpa.git
cd AI-astron-rpa
python scripts/install.py                        # 装依赖 + 技能包 + MCP + 自检
python scripts/install.py --key <API_KEY> --host <host:port>   # 拿到 Key 后补配置
```

装完就能直接说人话了：

```bash
python scripts/ai_flow.py --nl "把这张表按部门拆成多个文件"
```

### 手工方式

```bash
cp rpa_config.example.json rpa_config.json   # 填 gateway / api_key / 客户端 Python 路径
python scripts/ai_flow.py --spec flows/Excel按条件拆表小流程.json
python scripts/ai_flow.py --spec flows/<流程>.json --stage publish --version 1   # 评审通过后发布
```

产出：`docs/generated_flow.json`（编译结果）+ `docs/reviews/<时间>_<流程>.md`（评审单）。

## 三种用法

| 入口 | 适合 | 说明 |
| --- | --- | --- |
| 命令行 `scripts/*.py` | 人 / CI | 工具链本体 |
| 技能包 `skill/astron-rpa-flow/` | WorkBuddy 等 AI 助手 | 说一句话让 AI 全流程代办 |
| MCP 服务 `mcp/server.py` | 任何 MCP 客户端 | 8 个工具：setup / doctor / list / generate / run / publish / invoke / make_module |

详见 [技能包与 MCP](docs/MCP与技能包.md)。

## 目录结构

```
星辰开源/
├── scripts/
│   ├── install.py            # 一键安装（依赖 / 配置 / 技能包 / MCP / 自检）
│   ├── atom_catalog.py       # 原子目录（357 个，按域分组，带必填参数与枚举）
│   ├── spec_lint.py          # 规格校验（报错带「第几个节点 + 怎么改」）
│   ├── coverage_report.py    # 原子覆盖报告（357 个原子用了多少、还差哪些）
│   ├── ai_flow.py            # 交付流水线：生成→校验→预检→落库→真跑→评审单→发布
│   ├── flow_generator.py     # 规格 → process_content（真实原子 schema 校验）
│   ├── nl_params.py          # 从自然语言抽具体参数（路径/页码/输出名/批量识别）
│   ├── self_heal.py          # 失败自愈（只做"改了不会更糟"的事，最多 2 轮）
│   ├── preflight.py          # 运行前预检（路径/占用/网关）
│   ├── publish_flow.py       # 落库、发布、登记 openapi
│   ├── make_module.py        # 本地 .py → 平台 Python 子流程模块
│   └── mod_*.py              # Python 子流程实现（PDF 版面还原 / OCR / 文件夹整理 / Excel 拆表）
├── mcp/server.py             # MCP 服务（零依赖，stdio JSON-RPC）
├── skill/astron-rpa-flow/    # 技能包（AI 操作手册）
├── flows/                    # 14 条流程规格（契约文件）
├── docs/                     # 交接清单、开发进度、部署与 MCP 文档、评审单
└── demo/                     # 演示数据与实测产物
```

## 已交付流程

| 流程 | 说明 |
| --- | --- |
| Word 转 PDF / **批量 Word 转 PDF** | 单个 / 文件夹批量导出 |
| PDF 合并 / PDF 抽取指定页 | 基础文档操作 |
| **Word 批量替换** | 文件夹内所有 Word 替换指定文字，另存到输出目录（不动原文件） |
| **Word 提取文本** | 文件夹内所有 Word 正文汇总成一个 txt |
| PDF 表格转 Excel | 提取表格结构 |
| PDF 转 Docx / 转可编辑 Word | 文字搬运 |
| PDF 版面还原 Word | 保留版式：真表格、字号分级、页眉页脚、页边距 |
| 批量 PDF 版面还原 | 文件夹内批量处理（循环） |
| 文件夹按类型整理 | 按扩展名/月份归类，自带可追溯报告 |
| **Excel 按条件拆表** | 按某列的值拆成多个文件 |
| **Excel 多表合并** | 文件夹内多个 xlsx 合成一张（表头不一致自动跳过并记录） |
| **Excel 按列去重** | 按指定列删重复行，可保留首条/末条 |
| **Excel 查找替换** | 全表查找替换，另存不动源文件 |
| **文件批量重命名** | 按序号统一改名 |
| **HTTP 请求** | 调用接口并把响应存成文件 |
| **网页填写输入框** | 打开网页填写指定输入框（需先拾取元素） |
| Excel 订单表导出 / 文本文件转存 / 网页元素点击 | 基础原子流程 |

## 文档

- [**部署使用文档**](docs/部署使用文档.md) —— **换到另一台电脑怎么装、怎么配、怎么跑（先看这篇）**
- [**技能包与 MCP**](docs/MCP与技能包.md) —— 一句话安装、AI 自动生成流程、8 个 MCP 工具
- [交接清单](docs/交接清单.md) —— 制品地图、环境信息、已知现象、红线
- [开发进度](docs/开发进度.md) —— 逐条踩坑记录与验证结论
- [架构设计](docs/架构设计-RPA引擎.md) —— RPA 引擎结构
- [调研报告](docs/astron-调研.md) —— 上游项目调研

## 这个项目是什么

**外挂程序（旁路工具链）**，不是技能包、也不是 MCP：

- 一套独立 Python 命令行工具，通过 HTTP 接口操作**已有的** Astron RPA 平台，**不改服务端**。
- 不是 MCP server（未实现该协议）；不是 skill（但可另行封装成 skill 或 MCP 工具）。
- 必须与 Astron RPA 设计器装在同一台 Windows 机器上（写流程要登录态、真跑要本地执行引擎）。

详见 [部署使用文档 §0](docs/部署使用文档.md)。

## 安全

- `rpa_config.json`（含 API Key、服务器密码）与本地凭据留档**不入库**，已在 `.gitignore` 中。
- 仓库内文档中的凭据均已脱敏为占位符，请以本机 `rpa_config.json` 为准。

## 参考

基于 [astron-rpa](https://github.com/iflytek/astron-rpa)（执行端）与 [astron-agent](https://github.com/iflytek/astron-agent)（决策端）的生态。
