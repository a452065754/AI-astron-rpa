# 星辰开源 · AI 操作 RPA

> 用一句自然语言，生成一条**固定、可离线运行**的 RPA 流程。

本项目把「人话 → RPA 流程」做成一条可交付的流水线：**生成 → 校验 → 本地真跑 → 评审单 →（人工通过后）发布**。
流程搭好之后**不需要 AI 参与**，在 Astron RPA 设计器/调度里就能独立运行。

## 当前状态（2026-10-31）

| 指标 | 值 |
| --- | --- |
| 流程规格 | **73 条**（`flows/*.json`） |
| 原子覆盖率 | **236 / 346 = 68.2%**（已剔除 11 个作废 key） |
| 已全覆盖的域 | **12 个**：Excel 35/35 · Docx 19/19 · Code 18/18 · File 13/13 · Network 12/12 · ListProcess 15/15 · Folder 9/9 · PDF 8/8 · TimeProcess 6/6 · Encrypt 6/6 · Software 3/3 · BrowserScript 1/1 |
| 机器自检 | 73/73 lint 通过 · 全部本地真跑 `SUCCESS` 并核对副作用 |
| 平台缺陷 | 见 `docs/平台缺陷清单.md`（A 缺陷 11 / B 行为差异 8 / C 环境限制 9 类 / D 我方误判 6）；**可直接发平台的复现包在 `docs/repro/`**（11 条一键复跑，实测 11/11 成立） |
| 人工评审 | **0 条**（评审单在 `docs/reviews/`，待看） |
| 已发布 | 1 条（早期验证） |

**核心能力已通，当前卡在「人工评审」这一环。** 下一步做什么、换设备怎么接，看
[`docs/待办清单.md`](docs/待办清单.md)（唯一真源）和 [`docs/交接清单.md`](docs/交接清单.md)。

> **换电脑/换 AI 接力必读**
>
> 1. `rpa_config.json` 与 `docs/_local_secrets.md` **不在 git 里**，`git clone` 拿不到。别手拷——
>    旧机器跑 `python scripts/handover_export.py`，把产出的 `handover/` 整个带走；
>    新机器放回原位后 `python scripts/handover_export.py --check` 体检。
> 2. robotId / elementId 换机后**全部作废**，用 `--robot-name` 按名字找/建，别抄旧 id
>    （`rpa_config.json` 里的 `defaults.robot_id` 就是旧机器的，别信）。
> 3. 跑一次 `mkdir -p "$APPDATA/astron-rpa/astron"`（坑 118，不做 DataTable 全系原子必崩）。

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

## 元素自动拾取（网页流程不再需要人工点选）

网页元素的定位**完全交给设计器自带的浏览器插件**（`127.0.0.1:9082/browser/transition`），
所以「元素能不能被找到」只取决于 `path` 对象，跟「谁生成它」无关。插件里那个
`formatElementInfo()` 正是人工拾取时产出的结构，它对外暴露成动作 `elementFromSelect`
——**给一个 cssSelector，还你一份完整元素定义**。于是「人工点选」被降维成「给个选择器」。

```bash
# 看一眼桥接与元素库现状
python scripts/element_pick.py --status

# 只探测，不写库（安全，建议先跑）
python scripts/element_pick.py --probe "#chat-textarea" --url https://www.baidu.com

# 拾取并写入元素库（会自动开浏览器、等页面渲染、卡可见性、写完立刻回查定位）
python scripts/element_pick.py --css "#chat-textarea" --name 百度_搜索输入框 \
    --url https://www.baidu.com --robot-id <该流程的机器人id>

# 校验已有元素当前还能不能命中（只读）
python scripts/element_pick.py --check <elementId>
```

更好的用法是**在规格里声明元素要求**，让流水线自己补齐（幂等，已有就复用）：

```json
"element_requirements": [
  {"name": "百度_搜索输入框", "kind": "web",
   "pick": {"url": "https://www.baidu.com", "css": "#chat-textarea"}}
]
```

然后照常 `python scripts/ai_flow.py --spec flows/xxx.json --robot-id <id>`，
`[2/6]` 步骤会先把缺的元素拾好再往下走。

**三个必知坑**（详见 `docs/开发进度.md` 坑 125~129）：

1. **别用隐藏节点**。现代站点普遍把旧版 DOM 留成隐藏节点（百度首页的 `#kw`/`#su` 就是），
   它们的 rect 是 `width:8,height:8` 但 `right:0,bottom:0`。脚本已按 `right-left>0` 卡死，
   命中不可见元素直接拒绝落库。
2. **元素按机器人隔离**。本项目一条流程一个同名机器人，元素必须挂在该流程自己的机器人下，
   否则运行时报 `Failed to get element data ... empty response`。
3. **网页点击用 `simulate_flag: true`**。默认的 JS 合成点击在百度这类站点上「返回成功但页面不动」，
   系统级点击才真的生效。

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
│   ├── element_pick.py       # 元素自动拾取（调浏览器插件现场取，写入元素库）
│   ├── publish_flow.py       # 落库、发布、登记 openapi
│   ├── make_module.py        # 本地 .py → 平台 Python 子流程模块
│   └── mod_*.py              # Python 子流程实现（PDF 版面还原 / OCR / 文件夹整理 / Excel 拆表）
├── mcp/server.py             # MCP 服务（零依赖，stdio JSON-RPC）
├── skill/astron-rpa-flow/    # 技能包（AI 操作手册）
├── flows/                    # 51 条流程规格（契约文件）
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
| **网页填写输入框** | 打开网页填写指定输入框（元素自动拾取，无需人工点） |
| **网页元素点击** | 打开网页点击指定元素（元素自动拾取，系统级点击） |
| **网页填表并截图存证** | 填表 → 提交 → 截图留证 → 关窗（全链路可核验） |
| **Excel 统计行列** | 读工作表名、已用行数、已用列数并打印 |
| **Excel 写公式加格式** | 公式向下填充（自动调整相对引用）+ 表头加粗居中 + 列宽自适应 |
| **Excel 工作表管理** | 新增页签、改名、写标题后另存 |
| **Excel 逐行读取** | 循环遍历表格每一行（`loop_excel_content`） |
| **Excel 转 JSON** | 表格数据导出成 JSON（走数据表导入/导出） |
| **文件按扩展名归档** | 按后缀建子目录并归类（纯原子，无 Python 子流程） |
| **文件批量复制** / **文件夹批量复制** | 批量拷贝到目标目录（目录不存在自动建） |
| **列表与文本处理** | 分割 → 去重 → 排序 → 计数 → 合并 → 大小写 |
| **文本编码解码** | Base64 编/解、MD5、SHA 摘要 |
| **时间戳转换** | 当前时间 ⇄ 时间戳 ⇄ 指定格式文本 |
| **Word 生成表格报告** | 新建 Word + 插入数据表格 + 超链接（链接只落文字，见坑 121） |
| **Word 读取表格** | 读文档里第 N 个表格的内容 |
| **HTTP 下载文件** | 按 URL 下载并另存到指定目录 |
| **Excel 合并单元格与插入行** | 末尾插行 + 合并单元格 + 写合计 |
| **字典与数学计算** | 字典建/写/取值 + 乘算 + 四舍五入 + 随机数 |
| **计数循环与提前退出** | `ForStep` + `Break` 控制流样例 |
| **字符串清洗** | 去空格 / 截取 / 提手机号 / 拼接 |
| **列表高级操作** | 建/插/改/删/反转/打乱/交集/合并/剔除/清空（12 个列表原子） |
| **文件夹重命名与移动** | IF 存在 → 改名 → 移动到目标目录 |
| **Excel 生成空白模板** | 复制工作表 + 清空数据 + 只留表头 |
| **浏览器打开取标题** | 打开网址取标题/URL 后刷新关闭（不需要拾取元素） |
| **剪贴板与进程信息** | 剪贴板读写清空 + 查进程 PID |
| **数据表读写与查找替换** | 数据表追加行 / 查找替换 / 读回 / 导出 JSON |
| **Excel 文本数字互转** | 区域数字⇄文本 + 查下一个可用行 |
| **加解密与时间差** | 对称加解密往返 + 时间加减与时间差 |
| Excel 订单表导出 / 文本文件转存 / 网页元素点击 | 基础原子流程 |

> 原子覆盖率：346 个有效原子中已用 **236 个（68.2%）**。`python scripts/coverage_report.py` 随时看缺口；
> `python scripts/atom_impl_check.py` 核对「字典里有但本机没实现」的原子（当前 39 个，IE 系整块缺失）。
> 没覆盖的为什么没覆盖、其中哪些是平台缺陷哪些是环境限制，看 **`docs/平台缺陷清单.md`**。

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
