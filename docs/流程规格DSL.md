# 流程规格 DSL（给 AI 看的写法规范）

> 编号 HANDOVER-ASTRON-AI-004 · 建立 2026-11-02
> **这份文档是「AI 搭 RPA 流程」这项能力的内核。** 会写 spec，就会搭流程；不会写，就只能靠猜。
> 消费者：`scripts/flow_generator.py`（spec → 节点树）、`scripts/spec_lint.py`（校验）、`scripts/ai_flow.py`（落库/真跑/发布）。
>
> **不想手写 spec？** 现在可以让程序直出：`python scripts/nl2spec.py "把 D:\in 下的 xlsx 合并成一张"`
> （LLM 按本文的规则生成 → 校验 → 失败回灌重写 → 可选真跑验证，见 `scripts/nl2spec.py` 的文档头）。

---

## 0. 一句话理解

> **spec 是一份声明式的"流程图"，用 JSON 写。** 你只声明"用哪个原子、输入从哪来、输出叫什么"，生成器负责翻译成平台要的节点树（`inputList`/`outputList`/`advanced`/`exception` 那一大坨）。
> **你永远不要手写节点树**，只写 spec。

```
人话/需求  →  spec.json（你写）  →  flow_generator  →  节点树  →  ai_flow 落库 → 本地真跑 → 发布 → openapi
```

---

## 1. 文件结构（5 个顶层字段 + 1 个可选）

```json
{
  "name": "文件批量重命名小流程",
  "nl_examples": ["把这个文件夹里的文件批量重命名", "批量改文件名"],
  "keywords": ["批量重命名", "文件改名"],
  "params": [ ... ],
  "scripts": { ... },
  "nodes": [ ... ]
}
```

| 字段 | 必填 | 作用 | 注意 |
| --- | --- | --- | --- |
| `name` | ✅ | 流程名 | 与机器人名对应；`--robot-name` 一般去掉「小流程」后缀 |
| `nl_examples` | ✅ | 用户可能的自然语言说法 | 用于 `intent_match` 关键词匹配 |
| `keywords` | ✅ | 关键词 | 同上，写 3–5 个即可 |
| `params` | 视情况 | 流程参数，落 `c_param`，可被 openapi 覆盖 | **全部参数必须有 `default`**，否则设计器点运行会拿到空串 |
| `scripts` | 可选 | **内嵌 Python 子流程源码**（AI 现场写的代码放这儿） | 只有用 `Script.module` 时才需要，见 §11 |
| `nodes` | ✅ | 节点数组，**按执行顺序**写 | 顺序即流程顺序，不要写并行 |

---

## 2. `params`：流程参数

```json
{"name": "in_dir", "type": "Str", "default": "D:\\data\\in", "describe": "输入目录"}
```

| 字段 | 说明 |
| --- | --- |
| `name` | 变量名，节点里用 `$p:name` 引用 |
| `type` | `Str` / `Int` / `Float` / `Bool` / `List` |
| `default` | **必须有值**（这是铁律，见 §7 坑 1） |
| `describe` | 中文说明，会显示在设计器参数面板 |

> **绝对禁止**：`"default": ""`。设计器打开会判"参数缺失"，点运行报 `[WinError 3] ...: ''`。

---

## 3. `nodes`：节点

```json
{
  "atom": "File.get_file_list",
  "alias": "列出文件夹里的文件",
  "out": {"file_list": "file_list"},
  "inputs": {"folder_path": "$p:in_dir", "traverse_subfolder": "no"}
}
```

| 字段 | 说明 |
| --- | --- |
| `atom` | 原子全名 `域.动作`，**必须存在于原子字典**（查法见 §6） |
| `alias` | 中文节点名，设计器里显示这个。**写清楚意图**，别写"节点1" |
| `out` | `{原子输出key: 你起的变量名}`。key 必须与原子 `outputList` 完全一致 |
| `inputs` | `{原子输入key: 值}`。key 必须与原子 `inputList` 完全一致（**拼错不报错，会静默失效**） |

---

## 4. 引用语法（最容易写错的地方）

| 写法 | 含义 | 例 |
| --- | --- | --- |
| 字面量 | 原样传 | `"Sheet1"`、`1`、`true` |
| `"$p:名字"` | 引用**流程参数** | `"$p:in_dir"` |
| `"$变量"` | 引用**上游节点输出**（`out` 里起的名字） | `"$file_list"` |
| `["片段1", "$p:x", "_", 3]` | **字符串拼接**（数组形式） | `["$p:prefix", "_", "$idx"]` → `改名_0` |
| `"$json:[...]"` | 复杂参数（`need_parse` 类），**必须是字符串化的 JSON** | 见 §5 |
| `"$item"` / `"$idx"` | 循环内的当前元素 / 索引（`Code.ForList` 的 `out` 起的名） | `"file_path": "$item"` |

**规则**：
- `$p:x` 里的 `x` **必须**在 `params` 声明过，否则生成器直接抛错（这是好事，早报错）。
- `$变量` 的变量名必须在**它之前**的某个节点 `out` 里定义过，否则抛 `变量引用 $xxx 未在任何上游节点 out 中定义`。
- 拼接数组里的**数字/布尔会被转成字面量**，不要写 `"$idx"` 之外的东西当序号。

---

## 5. 三个特殊参数（写错必崩）

### 5.1 `need_parse` 类参数 → 必须写 `"$json:[...]"`

`Dialog.select_box.options`、`Script.process.process_param`、`Script.module.module_param`、`Excel.write_excel` 的部分参数等，字典里标了 `need_parse`。这些参数**在节点里必须是字符串**，写 list 会在编译期报 `TypeError: the JSON object must be str, bytes or bytearray, not list`。

```json
"inputs": {
  "module_param": "$json:[{\"src_excel\": \"$p:src\", \"key_col\": \"$p:col\"}]"
}
```

### 5.2 `Script.module` 的 `module_param` → 生成器自动处理，但要知道原理

- 含 **只有 `$p:`** → 生成器会把 `$p:` **烘焙成默认值字面量**，落库成**纯 JSON 字符串**（设计器能正常打开）。
- 含 **`$var:`/`$item`（循环变量）** → 保留 `{"rpa":"special",...}` 结构，**设计器打不开（报「请求参数解析失败」）**，只能本地真跑验证。
- **所以**：能用 `$p:` 解决的，别用循环变量；必须循环时，接受"设计器不能看、但能跑"。
- 模块内容怎么来、`content` 怎么填 → 见 **§11 脚本模块**。

### 5.3 空值必填参数 → 会红叉，但引擎不报

平台有些参数 `required: true`，传空串设计器会标红叉，**但引擎照跑**（本地真跑 SUCCESS 会欺骗你）。
典型：`StringProcess.replace_content_in_string` 的 `new_value`（"替换为空"天然是空的）。
**绕法**：换一个不需要空值的原子。例：要"去掉扩展名的点"，别用"替换为空"，改用
`StringProcess.extract_content_from_string` + `regex_formula: "[^.]+$"`。

---

## 6. 写之前必须查的三件事

| 查什么 | 命令 | 用来判断 |
| --- | --- | --- |
| 原子存不存在、入参出参叫什么 | `python scripts/atom_catalog.py --atom File.get_file_list`（或读 `load_atom_dict()`） | `inputs`/`out` 的 key 名、枚举 `options` |
| 这个原子**本机有没有实现** | `python scripts/atom_impl_check.py` | 39 个"字典有实现无"的原子（IE 系、`DataTable.sort_table`、`System.screen_lock` 等），写了必失败 |
| 已有流程怎么写的 | `flows/` 里找同域规格抄结构 | 别从零发明 |

**枚举参数**（`sort_method`、`exist_options`、`read_range`…）必须用字典里的 `options.value`，**不能写中文描述**。

---

## 7. 必踩的坑（都已在真实项目里踩过）

| # | 坑 | 表现 | 正确做法 |
| --- | --- | --- | --- |
| 1 | 参数 `default` 为空 | 设计器运行 `[WinError 3] ...: ''` | 每个 param 都给真实默认值 |
| 2 | `need_parse` 参数写成 list | 编译期 `TypeError: the JSON object must be str` | 写 `"$json:[...]"` 字符串 |
| 3 | 循环变量塞进 `module_param` | 设计器「请求参数解析失败」 | 用 `$p:` 烘焙，或接受设计器不可看 |
| 4 | `required` 参数传空串 | 设计器红叉、引擎照跑（假通过） | 换原子，别硬传空 |
| 5 | 元素类原子无 `elementId` | 运行时找不到元素 | 元素按 robotId 隔离，新机器人元素库为空；需先 `element_pick.py` 拾取 |
| 6 | `input` 的 key 拼错 | **不报错**，参数静默丢失、行为异常 | 逐字对照原子字典 |
| 7 | 平台参数表残留历史值 | 跑出来的路径跟你写的默认值不一样 | 落库后回读 `c_param` 核对；`sync_params` 会按名字覆盖不一致值 |
| 8 | **Bool 参数写成字符串** | `"visible_flag": "False"` → 引擎把非空字符串当真值，**窗口反而显示出来**（行为正好相反） | 写 JSON 布尔 `false`，**不加引号**。数字同理写 `3` 不写 `"3"` |
| 9 | **对象型参数传了文件路径** | 真跑 `参数 doc 的值转换成 DocumentObject 失败` | `types` 是 `DocxObj`/`ExcelObj`/`*Object` 的参数，要传**上游打开节点的输出**（`$doc`），不能传路径 |
| 10 | **拼接数组写成字符串** | 真跑 `文件路径:[$p:dir,\,...]有误`（方括号被当成路径字面量） | 必须是真 JSON 数组 `["$p:dir","\\","$p:name"]`，不能写成字符串 |
| 11 | **`$json:` 用在路径参数上** | 路径变成 `[{"varName":...}]` 文本 | 路径类直接写 `"$p:x"`；`$json:` 只给 list/dict 之类结构化参数 |

> 坑 8–11 是 2026-11-02 由 `nl2spec`（LLM 直出 spec）大规模生成才暴露的 —— 手写流程时人会本能避开，
> 机器不会。`spec_lint` 现在都能抓到（8、9 需靠真跑日志，其余静态可查）。

---

## 8. 完整示例

### 8.1 最小（3 节点，零文件依赖 —— `capability_bootstrap.py` 内置的就是它）

```json
{
  "name": "能力自检_最小流程",
  "nl_examples": ["把一段文本按分隔符拆开、去重、再拼回去"],
  "keywords": ["能力自检"],
  "params": [
    {"name": "text", "type": "Str", "default": "c,a,b,a", "describe": "待处理文本"},
    {"name": "sep",  "type": "Str", "default": ",", "describe": "分隔符"}
  ],
  "nodes": [
    {"atom": "StringProcess.split_string_to_list", "alias": "拆成列表",
     "out": {"split_list_from_string": "parts"},
     "inputs": {"string_data": "$p:text", "separator": "$p:sep"}},
    {"atom": "ListProcess.get_unique_list", "alias": "去重",
     "out": {"unique_list_data": "uniq"},
     "inputs": {"list_data": "$parts"}},
    {"atom": "StringProcess.merge_list_to_string", "alias": "拼回字符串",
     "out": {"merged_string_from_list": "joined"},
     "inputs": {"list_data": "$uniq", "separator": "-"}}
  ]
}
```

### 8.2 带循环（列表 → 逐项处理）

```json
{
  "atom": "Code.ForList", "alias": "逐个处理",
  "out": {"index": "idx", "item": "item"},
  "inputs": {"list": "$file_list"}
},
{
  "atom": "File.file_rename", "alias": "重命名",
  "out": {"rename_file_path": "renamed"},
  "inputs": {"file_path": "$item", "new_name": ["$p:prefix", "_", "$idx"], "exist_options": "generate"}
}
```

> 循环体 = `Code.ForList` 之后的节点，直到同一层级的结束。**`$item`/`$idx` 只在这个循环体内有效。**

### 8.3 条件（只在满足时执行）

```json
{
  "atom": "Code.If", "alias": "只要目标类型",
  "out": {},
  "inputs": {"args1": "$item", "condition": "in", "args2": "$p:pattern"}
}
```

---

## 9. 自检与落库（写完必做）

```bash
# 1. 静态校验（原子存在性、参数引用、实现缺失、元素依赖）
python scripts/spec_lint.py flows/<名>.json

# 2. 落库 + 本地真跑（会真建机器人、真执行、打印产出）
python scripts/ai_flow.py --spec flows/<名>.json --robot-name <机器人名>

# 3. 看结果：IS_PASS 是否 YES、SUCCESS 用时、产出文件是否存在
#    ⚠️ SUCCESS ≠ 做成了。必须核对产出文件的内容/大小/条数。
```

**判定成功的三把尺**：① `spec_lint` 0 错 ② 本地真跑 `SUCCESS` ③ **产出文件真的对**。
少任何一把，都不算完成。

---

## 10. 给 AI 的行动顺序（照着做，别跳）

1. 问清需求：在哪个系统/软件、要做什么、输入是什么、产出要什么形式；
2. 查原子：有没有能用的原子（`atom_catalog` + `atom_impl_check`）；
3. 查已有：`flows/` 里有没有同类，抄结构改参数；
4. 写 spec：先写 3–5 个节点跑通，再逐步加；
5. `spec_lint` → `ai_flow` 本地真跑 → **核对产出**；
6. 有问题按 §7 对照表排查（80% 的失败都在那张表里）；
7. 通过后才谈发布/登记 openapi（发布不可逆，先给用户清单确认）；
8. **原子拼不出来时**（命中 §11.1 的 B-1~B-4 任一条）才用 `Script.module`，并且必须写
   `why` / `maintain_hint`；如果这类需求反复出现（`docs/原子需求池.md` 里 ≥3 次），
   就去推动**新增原子** —— 让业务人员能直接拖一个节点用，而不是永远靠脚本。

---

## 11. 脚本模块（Script.module）

> 这一节解决的是**原子做不到的事**，但请先读 11.1：
> **脚本是兜底，不是默认。** 产品的验收标准是「**非专业业务人员能看懂、能做微调**」，
> 三个手段的价值排序**不是并列**的：

| 优先 | 手段 | 业务人员可读 | 可微调 | 定位 |
| --- | --- | --- | --- | --- |
| 1 | **原子拼装** | 高 | 高 | **默认路径，能拼就必须拼** |
| 2 | **Script.module 脚本** | 低 | 低 | **兜底**，只用于原子确实表达不了 |
| 3 | **新增原子** | 最高 | 最高 | 脚本的**升级形态**，不是并列选项 |

### 11.1 什么时候用 / 什么时候别用（硬判定，不是倾向）

**A. 默认：用原子拼（先证明原子做不到）**

下列需求**禁止**用脚本，必须用原子：

| 需求 | 用什么 |
| --- | --- |
| 单个/固定数量的文件读写、复制、移动、重命名、压缩解压 | `File.*` / `Folder.*` |
| 格式转换（Word→PDF、Excel→CSV、PDF 抽页/合并、图片转换） | `Docx.*` / `Excel.*` / `PDF.*` |
| 固定规则的查找替换、筛选、去重 | `Excel.*` / `DataTable.*` / `StringProcess.*` |
| 顺序固定的线性步骤（打开→读取→写→保存） | 直接串节点 |
| 打开网页点元素、截图、发邮件、HTTP 请求 | `Browser*` / `Mail.*` / `HTTP.*` |

> 判断标准一句话：**能用原子 + 循环/条件块表达，就必须用原子**，哪怕多花 3–5 个节点。
> 理由很实在：原子在设计器里可视化、点开就能改参数；脚本业务人员看不懂也改不动。

**B. 只有命中下列任一条，才允许用脚本**（写进 `scripts.<名>.why`，`spec_lint` 会校验）

| 条款 | 场景 | 原子为什么不行 |
| --- | --- | --- |
| **B-1** 跨迭代累计状态 | 计数器、累加器、"已写到第几行"、循环里要记住上一个值 | 引擎没有「变量赋值/自增」原子 |
| **B-2** 输出个数/文件名取决于运行时数据 | 按某列唯一值拆成 N 个文件、按分组名建目录 | N 和名字只有跑起来才知道 |
| **B-3** 复杂数据变换 | 分组聚合、透视表、行列转置、多表按 key 关联 | 聚合规则本身依赖数据内容 |
| **B-4** 精确控制文件格式细节 | 单元格样式、列宽、合并单元格、页边距 | 原子只管"写值"，不管版式 |

> **写完自检一句**：用了脚本就必须能说出命中 B-几。写不出来 → 说明该用原子（这也是防
> 「模型偷懒选脚本」最有效的闸）。
> 反面教训：「Excel 按列的值拆成多个文件」「Excel 按列去重」用 11~17 个原子硬拼是注定失败的
> —— 命中 B-2 就用脚本，**但不许**用「一堆原子假装实现了」的方式糊过去。

> **反向核对提示（`spec_lint` 的 warn，不阻断）**：原子方案 **≥12 个节点且有嵌套循环**时
> 会提示「确认是否存在累计状态需求被硬拼」。为什么不设硬阈值（比如「>10 节点就必须用脚本」）：
> 15 步表格处理确实该用原子，阈值会误伤合法长流程。但「节点多 + 嵌套循环」是模型硬拼 B-1
> 的高发组合（原子没有变量赋值，只好套循环 + 一堆 If 去模拟计数器），所以值得提示复核。
> 12 这个数字来自现状：已评审通过的最长流程是 11 个节点。

### 11.2 字段结构

```json
{
  "name": "Excel按列拆表小流程",
  "params": [
    {"name": "src_file", "type": "Str", "default": "D:\\demo\\订单.xlsx", "describe": "源表"},
    {"name": "split_column", "type": "Str", "default": "部门", "describe": "按哪一列拆"}
  ],
  "scripts": {
    "excel_split": {
      "name": "Excel按列拆表",
      "why": "命中判定 B-2：输出文件数取决于「部门」列的唯一值，原子无法动态生成文件名",
      "maintain_hint": "改拆哪一列 → 改参数 split_column；改输出目录 → 改参数 out_dir；改逻辑需找开发改 Python",
      "atom_suggestion": "Excel 按列唯一值拆分成多表",
      "params": [
        {"name": "src_file", "describe": "源Excel文件路径", "default": "D:////demo////订单.xlsx"},
        {"name": "split_column", "describe": "按哪一列拆（写列名）", "default": "部门"}
      ],
      "code": "def main(args):\n    import os\n    import openpyxl\n    src = (args.get(\"src_file\") or \"\").strip()\n    ...\n    return {\"ok\": True, \"processed\": n, \"skipped\": 0, \"outputs\": names[:5]}\n"
    }
  },
  "nodes": [
    {"atom": "Script.module", "alias": "按部门拆分（自动脚本，改逻辑需开发）",
     "inputs": {
       "content": "$script:excel_split",
       "module_param": "$json:[{\"varName\": \"src_file\", \"varValue\": \"$p:src_file\"}, {\"varName\": \"split_column\", \"varValue\": \"$p:split_column\"}]"
     },
     "out": {"program_script": "split_result"}}
  ]
}
```

| 位置 | 必填 | 说明 |
| --- | --- | --- |
| `scripts.<名>.code` | ✅ | **完整可运行**的 Python 源码（不是伪代码、不是片段） |
| `scripts.<名>.why` | ✅ | 为什么原子做不到、命中 **B-几**（`spec_lint` 缺了报 err）——防偷懒的硬约束 |
| `scripts.<名>.maintain_hint` | ✅ | 业务人员能改什么（改哪个参数）、什么必须找开发（缺了报 err） |
| `scripts.<名>.atom_suggestion` | 建议 | 建议沉淀成什么原子（`ai_flow` 记进 `docs/原子需求池.md`） |
| `scripts.<名>.name` | 可选 | 平台上的模块显示名（省了就用脚本名） |
| `scripts.<名>.params` | 强烈建议 | 脚本参数的**中文说明**：`[{name, describe, default}]`。落进 `c_param.var_describe` → 业务人员在节点参数表格里看到的就是它。不写会退化：参数名能从 `module_param` 推，但说明是空的（表格里只有 `src_file` 这种英文名） |
| `nodes[].inputs.content` | ✅ | 写 `"$script:脚本名"` —— **占位符**，不是模块 id |
| `nodes[].inputs.module_param` | ✅ | `$json:` 字符串，`[{varName, varValue}, ...]`；无参数写 `"$json:[]"` |
| `nodes[].out.program_script` | 建议 | 模块 `main()` 返回值的接收变量名（缺了 lint 告警） |
| `nodes[].alias` | 建议 | 业务语言 + 标明是脚本，如「按部门拆分（自动脚本，改逻辑需开发）」 |

### 11.3 平台侧发生了什么（不需要人工上传 py）

```
spec(nodes.content = $script:名字 + scripts.名字.code)
   ↓ ai_flow.py 的 [2.5/6] 步骤（robot_id 已确定、建节点树之前）
落盘 scripts/mod_gen_<名字>.py          ← 给人看/改/复用的本地副本
   ↓ pf.create_module(cookie, robot_id, code, 模块名)   ← 官方接口（creator_id=当前用户）
moduleId（雪花号）写回 nodes[].inputs.content
   ↓ 之后才构建节点树 → 落库 → 真跑 → 发布时模块随版本一起复制
   ↓ 同时记一行到 docs/原子需求池.md（幂等；≥3 次就该做成真原子）
```

要点：
- **模块必须用官方接口建**（`publish_flow.create_module`）。手工 SQL 插的行 `creator_id`
  是旧格式，发布时平台按 creator_id 复制 → 报「该模块不存在」。
- 重复跑同一条命令不会重复建模块（`logs/module_ledger.json` 台账 + 内容 hash）：
  内容没变就复用，内容变了就 `save_module` 覆盖同一条。
- **台账只是「省一次 API 调用」的优化，不是事实来源**：每次命中缓存前都会
  `pf.open_module()` **探活**，读不到就当成 miss 重新建（模块可能在平台上被删了）。
  所以台账 id 被改坏、或换环境后 id 全是废号，都不会让流程跑到一半报「模块不存在」。
  台账文件已进 `.gitignore`（moduleId 是本环境专属的，不该入库）。
- **重建时撞名会自动加后缀**：平台要求「同一机器人下模块名唯一」（撞名报
  `500003 名称重复`）。而台账丢了就必然要重建、旧模块名又被占着 —— 所以
  `建模块` 会按 `名字` → `名字_2` → `名字_3` 重试。名字只是显示名（执行走 moduleId），
  带后缀完全不影响运行。
- **发布前会再刷一遍模块内容**（`refresh_modules`）：平台发布会按 `creator_id` 复制模块行，
  复制的必须是当前代码。刷失败**直接中止发布**，不会「告警后继续发一个和代码不一致的版本」。
- `--stage draft`（别名 `--prep-only`）只落盘不建模块（不上传、不落库）；
  `review`/`publish`/`expose` 才真建。⚠️ 注意 draft **仍然会**解析/创建机器人
  （给了 `--robot-name` 时），平台上可能多出一条机器人记录。

### 11.4 模块契约

```python
def main(args):
    in_dir = (args.get("in_dir") or "").strip()
    ...
    return {"ok": True, "processed": n, "skipped": k, "outputs": names[:5]}
```

| 项 | 规则 |
| --- | --- |
| 入口 | 函数名**必须**是 `main`，签名 `main(args)`；`args` 是 `dict` |
| 参数取值 | **`args` 里的值全是字符串**（`module_param` 里的 `true`、`3` 传到模块都是 `"true"`、`"3"`） |
| 返回值 | 落到 `out.program_script` 那个变量；**必须返回人类可读摘要**（`ok` / 处理数量 / 跳过数量 / 输出列表前 5 个），业务人员看返回值就知道做了什么 |
| 失败 | `raise RuntimeError("中文可读原因")`，**不要**抛英文堆栈、不要静默 `return None` |
| 日志 | `from astronverse.workflowlib import logger`（拿不到时退回 `logging`） |
| 路径 | **不许写死绝对路径**，一律从 `args.get("xxx")` 取 |

**库白名单**（运行环境**离线**，白名单外装了也跑不了）：

| 类别 | 可用 |
| --- | --- |
| 第三方 | `openpyxl` `pandas` `pdfplumber` `docx`(python-docx) `PIL` `numpy` `astronverse` `win32com` `requests` |
| 标准库 | 全部（`os` `re` `json` `csv` `shutil` `datetime` `pathlib` …） |

`spec_lint` 会在生成期就检查 import：写了白名单外的库直接报「模块运行环境离线，没有这个库」。

### 11.5 `module_param` 的三种写法

`varValue` 决定「模块拿到的这个参数是什么」：

| 写法 | 含义 | 例 |
| --- | --- | --- |
| 字面量 | 直接给固定值 | `{"varName": "copy_header", "varValue": "true"}` |
| `"$p:参数名"` | 引用**流程参数**（推荐） | `{"varName": "src_file", "varValue": "$p:src_file"}` |
| `"$var:变量名"` | 引用**循环变量**（只能用在循环体内；落库保留 `rpa:special` 引用，设计器里显示为变量名、引擎运行时求值） | `{"varName": "src_pdf", "varValue": "$var:item"}` |

`varName` 是**模块里 `args.get()` 用的名字**，两边必须一一对上（`spec_lint` 会逐个比对）。
参数名用**业务语义英文**（`split_column` / `out_dir`），中文解释写进流程参数的 `describe`。

#### 11.5.1 落库结构：平台认的「Py 原子」形态（2026-11-03 用户目视确认后定稿）

**业务人员改参数的地方是「脚本节点的参数表格」，不是流程参数面板。** 这句话决定了落库结构 ——
设计器打开 `Script.module` 节点时会按 `moduleId` 去 `c_param` 取参数定义，取不到就显示「暂无数据」、
顶栏报「请求参数解析失败」。所以落库必须是三层齐备：

```
c_param（模块级参数定义）
    module_id = <moduleId>   process_id = NULL   var_direction = 0
    var_type  = Str          var_value  = '[{"type":"other","value":"默认值"}]'
                    ↓ 每行的 id 就是下面 varId

节点 inputList
    content      = "<moduleId>"                          ← 纯字符串，不是数组
    module_param = [ {"varId": "<c_param.id>", "varName": "src_file",
                      "varValue": {"rpa":"special","value":[{"type":"other","value":"…"}]}} ]
                                                         ← 真数组，不是 JSON 字符串
```

这三条是**从用户手工搭的 `导入MES` 流程里取的标准答案**，逐字段对照过。少任何一块的现象：

| 缺哪块 | 设计器里的表现 |
| --- | --- |
| `c_param` 没有 `module_id` 级行 | 参数表格「暂无数据」，参数改不了 |
| `module_param` 是 JSON 字符串 | 顶栏「请求参数解析失败」 |
| `content` 包成 `[{type,value}]` | 取不到模块，节点空 |
| 参数项缺 `varId` | 那一行参数认不到（其它行可能正常） |

**规格侧不用关心这些**：你只写 `content: "$script:名字"` 和 `module_param` 的语义
（`$p:` / `$var:` / 字面量），`ai_flow.resolve_scripts()` 会建模块 + 建 `c_param` + 把 `varId`
填进节点。`$p:` 会被**烘焙成参数默认值字面量** —— 因为改参数的正道是节点表格，
留 `p_var` 引用反而让表格里显示变量名，对非专业用户更难懂。

> 历史遗留结构与批量修复：2026-11-02 之前的流程是旧结构（`content` 数组 + `module_param`
> 字符串）。跑 `python scripts/relink_modules.py --dry-run` 先盘点，去掉 `--dry-run` 就地修，
> 幂等可重跑。加 `--run` 会顺手本地真跑验证。

**`need_parse` 与 `bound` 模式已作废**：那套是「把 `module_param` 当整块 JSON 字符串 + 让引擎
求值」的方案，在结构定稿后没有存在意义（数组形态天然不需要解析，参数也不靠流程参数面板改）。
`--param-binding` 参数、`rpa_config.json` 的 `param_binding` 键都已删除。

**仍然禁止 JSON 布尔/空值**（`spec_lint` 规则 1.5，**err** 级）：`varValue` 落库后是
`{"rpa":"special","value":[{"type":"other","value":X}]}`，引擎走 `ComplexParamParser` 求值，
`true` / `false` / `null` 在 Python 里未定义 → 跑到那一步才 `NameError: name 'true' is not defined`。
布尔一律写字符串 `"true"` / `"false"`（模块侧本来就该 `str(args.get("x")).lower() in ("true","1","yes")`）。

### 11.6 可维护性补偿（脚本外面必须可维护）

脚本业务人员看不懂，所以「外面」必须补齐。写脚本时**五条都要做到**（外加下面「脚本健壮性五条」，一共十条）：
1. **所有可调项提升为脚本参数**，不许写死在代码里：路径、列名、sheet 名、阈值、是否覆盖 ——
   声明在 `scripts.<名>.params`（推荐，能带中文说明）或节点的 `module_param` 里；
2. 参数名用业务语义英文；中文说明写进 `scripts.<名>.params[].describe`
   （它会落到 `c_param.var_describe`，业务人员在节点参数表格里看到的就是它）；
3. 节点 `alias` 必须是业务语言 + 标明是脚本，如「按部门拆分（自动脚本，改逻辑需开发）」；
4. `main()` 返回人类可读摘要（`ok` / 处理数量 / 跳过数量 / 输出列表前 5 个）；
5. 失败 `raise RuntimeError("中文原因")`。

**另外五条是「数据异常 / 重复运行 / 值保真」的防御（2026-11-02 QA T4/T5/T6 + 脏值实测补上，缺一条就会静默出错）**：

6. **脚本必须对「数据缺失/异常行」给出显式处理策略（跳过并计数 / 单独成组），不许静默丢数据。**
   典型：`pandas.groupby` 默认 `dropna=True`，**空值那行不会出现在任何输出文件里**，而流程报 SUCCESS。
   → 拆/分组前统一 `df[col] = df[col].fillna("<空>")` 或 `groupby(col, dropna=False)`，
   并在 `summary` 里写「含空值 N 行」，N>0 时 `logger.warning`。
   验收标准：**输入 3 行（含 1 行空值）→ 输出必须能对上 3 行**，或明确报「跳过 N 行」。
   「没值」有两种，别混着说：真空/空串（`str.strip()==''`）与 Excel 错误值（`isna()`，
   配 `keep_default_na=False` 时剩下的 NaN 只可能是错误值）→ 各自单独成组、单独出文件
   （`空值.xlsx` / `错误值.xlsx`），summary 分两句说。
7. **读哪张表必须参数化**：`sheet_name` 是流程参数，只接受 `''`/`'0'`（第一张）或**真实 sheet 名**；
   要按序号读第 N 张，必须先 `pd.ExcelFile(src).sheet_names[N]` 拿到真名（直接把 `"1"` 当 sheet 名传会报
   `Worksheet named '1' not found`）。`summary` 里要报出实际读到的 sheet 名。
8. **输出默认可重跑**：`out_dir` 自动建；目标文件已存在时默认**不覆盖**（`overwrite` 参数默认 `"false"`，
   命中则唯一化改名 `xxx(1).xlsx`）。**改名必须 `logger.warning` + 写进 summary** —— 只 `logger.info`
   等于没说（界面上看不到，业务人员会以为新数据没生效）。同一流程连跑两次不许互相冲掉。
9. **值保真：不许让 pandas 把源表里的值悄悄改掉。**
   只做搬运/拆分/合并（不参与计算）的流程，读取**一律加 `keep_default_na=False`** ——
   否则 pandas 默认把 `NA`/`N/A`/`NULL`/`None`/`#N/A` 当缺失值抹成空白，而**行数还对得上**，
   第 6 条的行数对账根本抓不到。加了它之后空单元格是 `''` 不是 `NaN`，
   所以空判定必须写成 `isna() | (astype(str).str.strip() == '')`（两种都认）。
10. **真 Excel 错误值（`#N/A`/`#VALUE!`/`#REF!`）参数救不回来** → 必须点名告警：
    用 openpyxl 扫目标 sheet 的 `cell.data_type == 'e'`，有就在 summary 写
    「源表有 N 个错误值（如 #N/A），输出已变空白，请核对」+ `logger.warning`。

**怎么让脚本不再回来找你**：每次用脚本都会记进 `docs/原子需求池.md`（脚本名 + 命中条款 +
建议原子 + 出现次数）。同一模式被 **≥3 条不同流程**需要 = 这类需求高频 = 应做成**真原子**
（业务人员直接拖一个节点、填参数就能用）。这样脚本不是技术债，而是原子需求的来源。

> 「出现次数」按**不同流程名**去重，不是运行次数：同一条流程跑 100 次也只算 1 次
> （那只是同一个人反复跑）；3 条不同流程都要它，才值得做成原子。
> 表里另有「首次出现 / 最后出现」两列，看得出这个需求是刚冒出来还是攒了半年。

### 11.7 必踩的坑

| # | 坑 | 表现 | 正确做法 |
| --- | --- | --- | --- |
| S1 | **以为 `args` 里是 bool/int** | `if args.get("copy_header"):` 永远是 True —— `"false"` 也是非空字符串 | 自己转：`str(args.get("copy_header")).lower() in ("true","1","yes")`；数字 `int(args.get("n") or 0)` |
| S2 | **以为返回 dict 就能在流程里取字段** | 写 `"msg": "$结果[processed_count]"` → 引擎原样打印这串文本（业务人员以为流程坏了）；极端情况变量名被当 Python 名 eval → `NameError` | **没有 `$结果[字段]` 这个语法**。要摘要就让脚本 `logger.info(...)` 自己打日志（流程里就别放 `Report.print`），或让脚本 `return {"summary": "拆好了：4 个部门，40 行"}` 再 `"msg": "$summary"`。详见 §11.8 |
| S3 | **`module_param` 少给参数不报错** | 模块里 `args.get("x")` 拿到 `None` → 静默产出空文件（最难查的一类失败） | 让 `spec_lint` 拦：它会把 `args.get("x")` 的名字和 `varName` 集合对比，缺一个就报错 |
| S4 | **在模块里写死绝对路径** | 换机器/换环境就报「文件不存在」 | 路径、文件名、列名全部从 `args.get()` 取 |
| S5 | **改了本地 `mod_gen_*.py` 以为生效** | 流程行为完全没变（平台模块是独立副本） | 改**规格里的 `scripts.code`**，然后重跑 `ai_flow.py`；本地 py 只是给你看/复用的副本 |
| S6 | **preflight 拦住"脚本自己建输出目录"的流程** | `preflight` 只看规格里的路径字面量，看不到脚本里的 `os.makedirs` → 报「目录不存在，流程不会自动建这一级」 | `ai_flow` 的 `self_heal` 会自动建目录后重检；**不要**为此加 `Folder.folder_create` 节点（脚本里已有 `os.makedirs` 就够了，见 S7）；实在不想管就 `--skip-preflight` |
| S7 | **`Folder.folder_create` 语义搞反** | `target_path` 填了完整目标目录 `...\out\split`、`folder_name` 又填 `split` → 实际建成 `...\out\split\split`，多一层空目录 | `target_path` = **父目录**、`folder_name` = 新子目录名。脚本里已 `os.makedirs(out_dir, exist_ok=True)` 时**直接不要这个节点**（多一个节点只会让业务人员问「为什么建两次」） |
| S8 | **字符串里内嵌变量** | `"共生成 $n 个文件"` 落库后整串是**字面量**，运行时原样打印 `$n`；流程还报 SUCCESS（静默错） | 要拼接写**数组** `["共生成 ", "$n", " 个文件"]`；要么整串只写 `"$n"`。详见 §11.8 |
| S9 | **输出里 `N/A` / `#N/A` 变空白** | pandas 默认 `keep_default_na=True` 把 18 种文本当缺失值抹成 `NaN`；**行数还对得上**，行数对账抓不到（静默改值） | 读时 `keep_default_na=False`；真错误值再用 openpyxl 扫 `cell.data_type == 'e'` 点名告警（§11.6 第 9、10 条） |
| S10 | **`sheet_name` 传 `"1"` 报错** | 数字字符串被当成 sheet **名**：`Worksheet named '1' not found`，业务人员看不懂 | 先 `pd.ExcelFile(src).sheet_names[N]` 拿真名再读；参数只接受 `''`/`'0'` 或真名 |
| S11 | **重跑后打开还是旧数据** | 输出改成 `xxx(1).xlsx` 却只记 `logger.info` —— 界面不显示，用户以为流程没生效 | 改名必须 `logger.warning` + summary 点名「N 个同名文件已改名写入」（§11.6 第 8 条） |

### 11.8 引用写法：字符串内嵌变量**不会被解析**（2026-11-02 实测）

`flow_generator.resolve_input()` 的判定只有一条：**整串是否以 `$` 开头**。所以：

| 规格写法 | 落库结果 | 运行时表现 |
| --- | --- | --- |
| `"$r"` | `[{"type":"var","value":"r"}]` | ✅ 正常取变量 |
| `["共 ", "$r2", " 个文件"]` | 3 段（other / var / other） | ✅ 正常拼接 |
| `"共 $r2 个文件"` | `[{"type":"other","value":"共 $r2 个文件"}]` | ❌ **原样打印 `共 $r2 个文件`** |
| `"$r[processed_count]"` | `[{"type":"other","value":"$r[processed_count]"}]` | ❌ 原样打印（没有索引语法） |

补充一条引擎侧事实：`executor/flow/params.py::_param_to_eval` 会把变量名当**裸 Python 名**
塞进待 eval 的表达式 —— 也就是 `$r[x]` 里的 `x` 会被当变量名，找不到就 `NameError`。

`spec_lint` 对这两种情况都是 **err**（不是 warn）：
它们能让流程「跑成 SUCCESS」却打印出带 `$` 的原始文本，属于**静默错**，
业务人员只会以为流程坏了。误伤收窄：只有当 `$` 后面那个名字**确实是已知变量/流程参数**
时才报错，`"价格 $100"`、正则里的 `$^`、路径里偶然出现的 `$` 都不会命中。

**所以「脚本 + Report.print」这条链路要这么写**（二选一）：

```jsonc
// 写法 A（推荐）：脚本自己打日志，流程里不放 Report.print —— 少一个节点、少一处误导
{"atom": "Script.module", "alias": "按部门拆分（自动脚本，改逻辑需开发）",
 "inputs": {"content": "$script:excel_split", "module_param": "$json:[...]"},
 "out": {"program_script": "split_result"}}
// 脚本里：logger.info("拆分完成：%d 个部门，共 %d 行 → %s" % (n, rows, out_dir))

// 写法 B：脚本额外 return 一个中文摘要字符串，流程里整串引用它
// 脚本 return {"ok": True, "processed": 4, "summary": "拆分完成：4 个部门，共 40 行"}
{"atom": "Report.print", "alias": "打印拆分结果", "inputs": {"msg": "$split_summary"}}
```

补充：`content` 写成 `$script:名字` 后，**不要**再往 `params` 里塞 `module_id` 参数 ——
这个模式下的模块 id 由系统自动解析，塞进去只污染 openapi 参数 schema（老流程那种
`"content": "$p:module_id"` 的写法仍然支持，见 §5.2）。

### 11.9 想知道「这条需求原子到底够不够用」：`--strategy` 与生成前评估

`scripts/nl2spec.py` 在生成**之前**先做一次「可实现性评估」（只问一句：原子能不能表达），
再决定用原子还是用脚本。三种策略：

| `--strategy` | 评估怎么做 | 结果 |
| --- | --- | --- |
| `auto`（默认/产品行为） | 评估 + 把结论钉进 prompt | 判「原子表达不了（命中 B-1~B-4）」→ **直接**用脚本兜底（省一轮硬拼试错）；判「能」→ 优先原子 |
| `atom` | **评估就是结论** | 判 false → 打印「⚠️ 原子无法表达这条需求 / 原因 / 建议」并**以退出码 3 结束**，不进入生成循环（不会产出假流程） |
| `script` | 不评估 | 脚本优先（仅调试，别拿它交付） |

判 false 时输出里带一句边界说明：**「本结论由模型基于判定表给出，不是形式化证明；
要完全确认需人工核 `atom_catalog.py`」** —— 别把模型「认输」当成平台能力的定论。

退出码：`0` 成功 / `2` 参数或环境问题 / **`3` 原子表达不了（有效结论，不是故障）** /
`4` 真跑没通过 / `6` 生成失败（自愈轮用完）。
