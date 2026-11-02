# 流程规格 DSL（给 AI 看的写法规范）

> 编号 HANDOVER-ASTRON-AI-004 · 建立 2026-11-02
> **这份文档是「AI 搭 RPA 流程」这项能力的内核。** 会写 spec，就会搭流程；不会写，就只能靠猜。
> 消费者：`scripts/flow_generator.py`（spec → 节点树）、`scripts/spec_lint.py`（校验）、`scripts/ai_flow.py`（落库/真跑/发布）。

---

## 0. 一句话理解

> **spec 是一份声明式的"流程图"，用 JSON 写。** 你只声明"用哪个原子、输入从哪来、输出叫什么"，生成器负责翻译成平台要的节点树（`inputList`/`outputList`/`advanced`/`exception` 那一大坨）。
> **你永远不要手写节点树**，只写 spec。

```
人话/需求  →  spec.json（你写）  →  flow_generator  →  节点树  →  ai_flow 落库 → 本地真跑 → 发布 → openapi
```

---

## 1. 文件结构（5 个顶层字段）

```json
{
  "name": "文件批量重命名小流程",
  "nl_examples": ["把这个文件夹里的文件批量重命名", "批量改文件名"],
  "keywords": ["批量重命名", "文件改名"],
  "params": [ ... ],
  "nodes": [ ... ]
}
```

| 字段 | 必填 | 作用 | 注意 |
| --- | --- | --- | --- |
| `name` | ✅ | 流程名 | 与机器人名对应；`--robot-name` 一般去掉「小流程」后缀 |
| `nl_examples` | ✅ | 用户可能的自然语言说法 | 用于 `intent_match` 关键词匹配 |
| `keywords` | ✅ | 关键词 | 同上，写 3–5 个即可 |
| `params` | 视情况 | 流程参数，落 `c_param`，可被 openapi 覆盖 | **全部参数必须有 `default`**，否则设计器点运行会拿到空串 |
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

## 7. 七个必踩的坑（都已在真实项目里踩过）

| # | 坑 | 表现 | 正确做法 |
| --- | --- | --- | --- |
| 1 | 参数 `default` 为空 | 设计器运行 `[WinError 3] ...: ''` | 每个 param 都给真实默认值 |
| 2 | `need_parse` 参数写成 list | 编译期 `TypeError: the JSON object must be str` | 写 `"$json:[...]"` 字符串 |
| 3 | 循环变量塞进 `module_param` | 设计器「请求参数解析失败」 | 用 `$p:` 烘焙，或接受设计器不可看 |
| 4 | `required` 参数传空串 | 设计器红叉、引擎照跑（假通过） | 换原子，别硬传空 |
| 5 | 元素类原子无 `elementId` | 运行时找不到元素 | 元素按 robotId 隔离，新机器人元素库为空；需先 `element_pick.py` 拾取 |
| 6 | `input` 的 key 拼错 | **不报错**，参数静默丢失、行为异常 | 逐字对照原子字典 |
| 7 | 平台参数表残留历史值 | 跑出来的路径跟你写的默认值不一样 | 落库后回读 `c_param` 核对；`sync_params` 会按名字覆盖不一致值 |

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
7. 通过后才谈发布/登记 openapi（发布不可逆，先给用户清单确认）。
