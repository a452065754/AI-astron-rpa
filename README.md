# 星辰开源 · AI 操作 RPA

> 用一句自然语言，生成一条**固定、可离线运行**的 RPA 流程。

本项目把「人话 → RPA 流程」做成一条可交付的流水线：**生成 → 校验 → 本地真跑 → 评审单 →（人工通过后）发布**。
流程搭好之后**不需要 AI 参与**，在 Astron RPA 设计器/调度里就能独立运行。

## 当前状态（2026-11-02）

> ⚠️ **先读这段，别误读下面的数字**：这个项目的**产品是「AI 搭 RPA 流程」这项能力**，不是 `flows/` 里那 84 条流程——它们是**能力的验收用例**（实测 86% 默认参数指向 `demo/`）。**用户明确：已生成流程不迁移，迁移的是能力。**
> 能力由四样支撑：**规格 DSL**（[`docs/流程规格DSL.md`](docs/流程规格DSL.md)）、**生成器**（`scripts/flow_generator.py`）、**能力白名单 + 雷区清单**（346 个原子哪些能用 / 15 条缺陷 + 11 个坑）、**交付链路**（发布 → openapi 登记 → `execute-async`，已实测跑通）。
> 迁移后用 `python scripts/capability_bootstrap.py` 一条命令验收（本机实测 **7/7**，加 `--live` 端到端 **8/8**）。详见 [`docs/待办清单.md`](docs/待办清单.md) §0.0–§0.1。

| 指标 | 值 |
| --- | --- |
| 流程规格 | **84 条**（`flows/*.json`）＝ **能力验证集**（非业务交付物，见上）；已落库 **75 条**，未落库 9 条（需元素/真实窗口，见 `docs/待办清单.md` §3.1） |
| 原子覆盖率 | **271 / 346 = 78.3%**（已剔除 11 个作废 key）；**26 个域满覆盖** |
| 已全覆盖的域 | **26 个**：Excel 35/35 · BrowserElement 22/22 · BrowserSoftware 20/20 · Docx 19/19 · Code 18/18 · ListProcess 15/15 · File 13/13 · Network 12/12 · StringProcess 10/10 · Folder 9/9 · PDF 8/8 · Gui 8/8 · DictProcess 6/6 · Encrypt 6/6 · TimeProcess 6/6 · MathProcess 5/5 · CV 5/5 · Window 4/4 · WinEle 6/6 · **Dialog 6/6** · DataConvertProcess 3/3 · DataProcess 1/1 · Report 1/1 · Script 3/3 · Software 3/3 |
| 机器自检 | 84/84 lint 通过 · 全部本地真跑 `SUCCESS` 并核对副作用 |
| 平台缺陷 | 见 `docs/平台缺陷清单.md`（A 缺陷 15 / B 行为差异 8 / C 环境限制 9 类 / D 我方误判 6）；**可直接发平台的复现包在 `docs/repro/`**（15 条一键复跑，实测 15/15 成立） |
| **人工评审** | **已通过 13 条**（2026-11-02） |
| **已发布** | **13 条 published v1**（评审通过项，全部登记 openapi 可执行列表；平台共 20 条可执行） |
| **脱离 AI 独立运行** | ✅ **已实测**：API Key 调 `execute-async` 触发已发布流程，删掉的产出文件按预期重新生成 |

**能力链路已闭环，业务价值待接入**：人话 → AI 生成 → 机器自检 → 人工评审 → 发布 → **外部 API 独立调用（无需 AI / 无需设计器）**。这条链已经在 13 条验证流程上端到端跑通，**现在缺的是一个真实业务场景**。下一步做什么、换设备怎么接，看
[`docs/待办清单.md`](docs/待办清单.md)（唯一真源，§0.0 是项目定位）和 [`docs/交接清单.md`](docs/交接清单.md)。

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
# ① 已有流程能覆盖 → 直接复用（关键词匹配，不花 LLM 的钱）
python scripts/ai_flow.py --nl "把这张表按部门拆成多个文件"

# ② 没现成的 → 让 LLM 现场生成一份新规格（L3，需要配 llm.api_key）
python scripts/nl2spec.py "把 F:\in 下所有 xlsx 合并成一张表" --verify --robot-name 我的新流程
```

### 自然语言直接生成流程（L3）

`nl2spec.py` 把人话变成**能跑的流程**，全程不需要 AI 会话介入：

```
人话 → LLM 直出 spec → spec_lint 校验 → 不过就把报错回灌重写（自愈）
    → 通过 → 落库真跑 → 跑挂了拿真实报错再回灌 → 产出正确才算完
```

| 命令 | 干什么 |
| --- | --- |
| `nl2spec.py "需求"` | 生成 + 静态校验，落盘到 `flows/gen_*.json` |
| `nl2spec.py "需求" --verify --robot-name X` | 再加**真跑验证闭环**（推荐）：失败会把引擎真实报错回灌给模型重写 |
| `nl2spec.py "需求" --set src_file=F:\真实\a.xlsx` | 把真实路径/列名钉进参数默认值（模型只会编 `D:\demo\x.xlsx`） |
| `nl2spec.py --selftest` | 离线自证（不需要 key、不联网） |
| `nl2spec.py --check` | 检查 LLM 配置 |
| `nl2spec.py --serve --port 8899` | 起 HTTP 接口给别的系统调（`POST /nl2spec`） |
| `nl2spec_bench.py` | 跑 14 条已评审流程的对照基准，出报告 |

配置（`rpa_config.json`，也可用环境变量覆盖）：

```json
"llm": {"provider":"xfyun-maas","base":"https://maas-api.cn-huabei-1.xf-yun.com/v2",
        "model":"xopqwen36v35b","api_key":"<你的 key>"}
```

> 任何 **OpenAI 兼容** `/chat/completions` 都能用（DeepSeek / OpenAI / vLLM / Ollama / one-api）。
> 实测：4B 级小模型吃不下 9k token 的规格提示词（会超时），要 30B 以上才有稳定产出。

**实测水平（2026-11-02，14 条已评审流程对照）**：结构合法 **11–12/14**（1 轮 5 条、2 轮 5 条、3 轮 1 条），
原子选择与人工答案重合 **71–77%**；端到端真跑并核对产出 **3/3 成功**（txt 合并 / Word 提取文字 / Word 转 PDF）。
报告见 `docs/nl2spec-bench-2026-11-02.md`。

> 早先那条边界「正确解法是 `Script.module` 时模型会硬用原子拼装然后失败」**已经修掉**：
> 现在生成前先做一次「原子可实现性评估」，命中 B-1~B-4 就直接走脚本，见下一节。

### 原子做不到时：AI 自己写 Python 模块（脚本兜底）

一句话：**AI 判断「纯原子拼不出来」时，会自己写一个 Python 模块**——代码作为 `scripts.code`
内嵌在规格 JSON 里，`ai_flow.py` 自动在平台上新建/更新子流程模块并把 moduleId 写进
`Script.module` 节点。**不需要人打开设计器上传 py，也不需要提前准备模块。**

手段优先级（prompt、`ATOM_HINTS`、`spec_lint` 三处口径一致）：

| 顺序 | 手段 | 什么时候用 |
| --- | --- | --- |
| ① | **原子拼装** | 默认选项。能用原子就用原子——业务人员看得懂、能自己改 |
| ② | **脚本 `Script.module`** | 必须命中判定表 B-1~B-4 之一，并在 `why` 里写清命中哪条（判定表见下） |
| ③ | **新增原子** | 同一段脚本被 **≥3 条不同流程**需要 → 说明是高频需求，该沉淀成真原子（`docs/原子需求池.md` 自动计数） |

「原子表达不了」的四条判定（与 `scripts/nl2spec.py` 的 `SYSTEM_PROMPT` 逐字一致，别自己改写）：

| 编号 | 定义 | 典型形态 |
| --- | --- | --- |
| **B-1** | **跨迭代累计状态** | 计数器、累加器、「已经写到第几行」、循环里要记住上一个值 —— 原子没有变量赋值 |
| **B-2** | **输出个数/文件名取决于运行时数据** | 按某列的唯一值拆成 N 个文件、按分组名建目录 —— N 和名字只有跑起来才知道 |
| **B-3** | **复杂数据变换** | 分组聚合、透视表、行列转置、多表按 key 关联 |
| **B-4** | **精确控制文件格式细节** | 单元格样式、列宽、合并单元格、页边距 |

> 除了这四条，一律用原子。**写完自检一句**：用了脚本就必须能说出命中了哪一条，
> 并把这句话原样写进 `scripts.<名>.why`（形如 `"命中判定 B-2：……"`）；写不出来 → 说明该用原子。
> 例：按部门拆表 = **B-2**（输出文件数取决于「部门」列的唯一值，原子无法动态生成文件名和数量）。

```bash
# 一条命令：人话 → 规格 → 建模块 → 落库 → 真跑 → 核对产出
python scripts/nl2spec.py "把 F:\in\销售明细.xlsx 按部门拆成多个 xlsx" --verify --robot-name 按部门拆分Excel
```

真机实测（2026-11-02）：**2 轮自愈收敛（13s）**，自动建 1 个模块，本地真跑 **SUCCESS 2.8s**，
产出 4 个部门文件 × 11 行 = **44 行**（40 数据 + 4 表头），单号与源表逐行一致、无重复；
业务人员看参数表是中文说明（`src_file 源Excel文件路径` …）。

**一句诚实话（别被上面的漂亮数字盖住）**：脚本节点默认用 `baked` 模式落库——`$p:` 会被
**烘焙成生成时的默认值**，所以**业务人员在参数面板改路径/列名，脚本这边不生效**（换来的是
「设计器一定能打开」）。这是我们 2026-10-30 修「设计器请求参数解析失败」时做的保守取舍。
要让参数真的可改：

```bash
# ① 只这一次：命令行
python scripts/ai_flow.py --spec flows/gen_按部门拆分Excel.json --param-binding bound

# ② 以后都这样：配置一次（不用改代码）
#    rpa_config.json 加 {"param_binding": "bound"}，或环境变量 RPA_PARAM_BINDING=bound
```

`bound` 会保留 `rpa:special` 引用 + `need_parse`，引擎侧已实测参数真的生效（改输出目录 → 产出
落到新目录；改源文件 → 读到新数据）。唯一**没确认**的是设计器前端能不能正常显示这种写法
（平台是本地 Electron 客户端，无法自动验证，只能目视），记录在 `docs/平台缺陷清单.md` **A17**。
所以默认保守、开关做到一行配置 —— 你确认设计器没问题，自己切就行。

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
├── flows/                    # 82 条流程规格（契约文件）
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

> 原子覆盖率：346 个有效原子中已用 **271 个（78.3%）**，26 个域满覆盖（Excel 35/35 · BrowserElement 22/22 · BrowserSoftware 20/20 · Docx 19/19 · Code 18/18 · ListProcess 15/15 · File 13/13 · Network 12/12 · StringProcess 10/10 · Folder 9/9 · PDF 8/8 · Gui 8/8 · DictProcess 6/6 · Encrypt 6/6 · TimeProcess 6/6 · MathProcess 5/5 · CV 5/5 · Window 4/4 · WinEle 6/6 · **Dialog 6/6** …）。`python scripts/coverage_report.py` 随时看缺口；
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
