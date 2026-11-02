# QA 报告：Script.module 能力（AI 自写 Python 脚本 → 自动建模块 → 注入 moduleId）

- 项目根：`F:/AI-Workbench/10-Projects/星辰开源`
- 解释器：`C:/Python314/python.exe`
- 验证方式：**纯静态 + 离线**（未碰平台、未建机器人、未调 LLM、未跑 `resolve_scripts` 真实落库路径）
- 用例脚本（全部可复跑）：`logs/qa/v1_regression.py` `v2_negative.py` `v4_strategy.py`
  `v5_resolve_dry.py` `v6_hardness.py` `v7_bonus.py`
- 测试轮次：第 1 轮完成全部 6 项；未发现「测试写错」需自修的情况

---

## 结论表

| 项 | 结果 | 问题等级 | 备注 |
|---|---|---|---|
| V1 回归基线（87 条 flows） | **PASS** | — | 0 错误；2 警告与本次改动**无关**（与工程师 before 快照逐字一致） |
| V1 里 8 条含 `Script.module` 的老流程 | **PASS** | — | `content=$p:module_id`（参数默认值=纯数字）0 错 0 警，新规则未误伤 |
| V2 负向 N1~N9 | **PASS（9/9）** | — | 每条都报对、报得能照着改；修掉后一律变绿 |
| V3 正向最小规格 | **PASS** | — | lint 0 错 0 警；`load_spec` 不报未知字段，`$script:` / `$json:` 未被破坏 |
| V4 `--strategy` 静态行为 | **PASS** | — | 三值均被 choices 接受；三种 system prompt 互不相同；atom 含「禁止使用 Script.module」 |
| V5 `resolve_scripts(dry=True)` | **PASS** | — | create/save/open_module 调用次数均为 0；content 未替换；无台账/需求池副作用；引用不存在脚本抛 SystemExit 且消息可用 |
| V6 防偷懒约束硬度 | **部分 FAIL** | **P1** | `why` **缺失**=err（守住）；`why` **写了但没写 B-x**=只 warn，而 `generate()` 不拦 warn → 偷懒有一条窄路能过 |
| 附加 V7（占位透传 / 需求池幂等） | **PASS** | — | `$script:` 落库为字面量不改写；历史写法 2 种仍能构建；需求池同名同条款累计、换条款新增 |
| 新增风险 R1：`$p:` 烘焙 → 脚本拿不到运行时参数 | **需真机确认** | **P1** | 预先存在的烘焙机制，但**新文档 §11.5 的承诺与之矛盾**；建议工程师用 1 次真机实验定性 |
| 新增风险 R2：`--stage publish` 不传 module_ids | **遗留缺口** | **P2** | `--stage expose` 传了，publish 没传 → 少了「模块是否进版本」校验 |
| R3：draft 阶段 `IS_PASS: YES` 但节点里是 `$script:` 字面量 | **体验问题** | **P2** | 不落库不致命，但绿灯含义被稀释 |
| R4：`logs/` 与 `scripts/mod_gen_*.py` 未进 .gitignore | **体验问题** | **P2** | 一次真实运行就会产生未跟踪产物 |
| R5：`docs/原子需求池.md` 已有 1 条联调记录，流程名在 `flows/` 里不存在 | **清洁度** | **P2** | 不影响功能，交付前建议清 |

**是否建议交付：建议交付（不带 P0）。** 核心链路（lint → load_spec → build → resolve_scripts dry）
行为正确且错误信息可指导修复；1 个 P1（why 条款降级为 warn）建议本轮顺手改成 err（1 行改动），
R1 请工程师用一次真机实验定性后回复。

---

## V1 回归基线

命令：

```
C:/Python314/python.exe logs/qa/v1_regression.py
```

真实输出（节选，完整见 `logs/qa/v1_regression.json`）：

```
flows 总数            : 87
JSON 解析失败         : 0
总错误 err            : 0
总警告 warn           : 2
0 错误流程数          : 87
0 错误 0 警告流程数   : 85

含 Script.module 的流程 : 8 条
  Excel多表合并小流程.json       错 0 / 警 0  节点1 个  content=$p: 参数引用（历史写法）
  Excel按列去重小流程.json       错 0 / 警 0  节点1 个  content=$p: 参数引用（历史写法）
  Excel按条件拆表小流程.json     错 0 / 警 0  节点1 个  content=$p: 参数引用（历史写法）
  PDF版面还原Word小流程.json     错 0 / 警 0  节点1 个  content=$p: 参数引用（历史写法）
  PDF转可编辑Word小流程.json     错 0 / 警 0  节点1 个  content=$p: 参数引用（历史写法）
  批量PDF版面还原小流程.json     错 0 / 警 0  节点1 个  content=$p: 参数引用（历史写法）
  文件夹按类型整理小流程.json    错 0 / 警 0  节点1 个  content=$p: 参数引用（历史写法）
  运行子流程小流程.json          错 0 / 警 0  节点1 个  content=$p: 参数引用（历史写法）

错误明细：无
警告明细：
[W] Excel查找替换小流程.json  第 2 个节点（全表查找替换）：2 个必填参数未写，将用默认值：match_range, output_type
[W] n2s_word2txt.json        第 1 个节点（打开Word）：4 个必填参数未写，将用默认值：default_application, encoding, open_pwd_flag, write_pwd_flag
```

逐条人工判断「是不是本次改动引入的噪音」：

| 警告 | 是否本次引入 | 依据 |
|---|---|---|
| Excel.search_and_replace 缺 2 个必填参数 | **否** | 与 `logs/_tmp_lint_before.json`（工程师改动前快照）**逐字一致**：before 也是 flows=87/错 0/警 2，警告文本完全相同 |
| Docx.open_docx 缺 4 个必填参数 | **否** | 同上 |

**事实纠正（数据口径）**：任务书里写「9 条流程含 `Script.module` 且 content 是纯数字 moduleId」。
实测 **8 条**含 `Script.module` 节点、共 8 个节点；第 9 个文件
`flows/模块回声小流程.json` 只是 `nl_examples`/`keywords` 里出现了 "Script.module" 字样，
**没有该原子节点**。另外 8 条的 `content` 都不是纯数字，而是 `$p:module_id`
（数字 moduleId 存在**流程参数默认值**里：2116342817880481792 等 8 个）。
两种形态我都验证过，均 0 错 0 警 —— 新规则没误伤历史写法。

---

## V2 负向用例（9/9 通过）

命令：`C:/Python314/python.exe logs/qa/v2_negative.py`（`--only N2` 可单跑）

### N1 库不在白名单

```
lint 结果：错误 1 条 / 警告 0 条
  [E] 第 1 个节点（按部门拆分（自动脚本，改逻辑需开发））：内嵌脚本 excel_split import 了运行环境没有的库：requests2
      fix → 模块运行环境离线，没有这个库（装了也跑不了）。可用第三方库只有：PIL, astronverse, docx,
            numpy, openpyxl, pandas, pdfplumber, requests, win32com，其余限 Python 标准库
修掉后（改回 openpyxl）：错误 0 / 警告 0 → 变绿
```

**能不能照着改：能。** 点名了坏库 `requests2`、说明了根因（环境离线）、给了白名单全量。
业务人员看到也知道「不是装个包就行」。

### N2 args 与 module_param 对不上（本能力价值最高的一条）

```
lint 结果：错误 1 条 / 警告 0 条
  [E] 第 1 个节点（按部门拆分…）：脚本 excel_split 需要参数 template_file，但 module_param 没有提供（运行时会拿到 None）
      fix → 在 module_param 里补一项 {"varName": "template_file", "varValue": "$p:template_file"}（或直接给字面量）
修掉后：错误 0 / 警告 0 → 变绿
```

**能不能照着改：能，而且能直接复制粘贴。** 缺的参数名、后果（None）、补法三段齐全。
补充验证：`args["tpl_path"]` 下标写法同样能抓（探针 C，err 1 条）。

### N3 没有 `def main(`

```
  [E] 第 1 个节点（按部门拆分…）：内嵌脚本 excel_split 没有定义 def main(args)
      fix → 平台只调用 main(args)；入口必须写成 def main(args): ... 并 return 结果
修掉后：错误 0 / 警告 0 → 变绿
```

**能照着改。** 探针 D 另行验证 `def main():`（无参数）也被 err 拦住。

### N4 语法错误（要行号）

```
  [E] 第 1 个节点（按部门拆分…）：内嵌脚本 excel_split 有语法错误（第 9 行）：expected ':'
      fix → 脚本必须是完整可运行的源码；改完语法再跑
修掉后：错误 0 / 警告 0 → 变绿
```

**能照着改。** 行号 9 + CPython 原始错误 `expected ':'` 精确指向问题。

### N5 引用了不存在的脚本

```
lint 结果：错误 1 条 / 警告 1 条
  [E] 第 1 个节点（按部门拆分…）：引用了内嵌脚本 不存在的名字，但顶层 scripts 里没有它
      fix → 可用脚本名：excel_split
  [W] scripts.excel_split 定义了但没有任何节点引用它
      fix → 要执行就加一个节点 "content": "$script:excel_split"；不用就删掉
修掉后：错误 0 / 警告 0 → 变绿
```

**能照着改。** 错误 + 可用名单 + 顺带把「定义了没人用」也报出来，两条合起来正好是一个完整
的「改名字」指引。唯一小遗憾：`fix` 里没直接说「把 content 改成 `$script:excel_split`」，
但同名警告已经说了。

### N6 缺 `why`

```
  [E] 第 1 个节点（按部门拆分…）：脚本 excel_split 缺少 why（必须写明为什么原子做不到）
      fix → 加 "why": "命中判定 B-2：输出文件数取决于「部门」列的唯一值，原子无法动态生成文件名"
            （判定条款见 docs/流程规格DSL.md §11.1）
修掉后：错误 0 / 警告 0 → 变绿
```

**能照着改**，且 `§11.1` 在 `docs/流程规格DSL.md` 里**真实存在**（第 264 行），指引可点。

### N7 缺 `maintain_hint`

```
  [E] 第 1 个节点（按部门拆分…）：脚本 excel_split 缺少 maintain_hint（业务人员改什么、什么必须找开发）
      fix → 加 "maintain_hint": "改拆哪一列 → 改参数 split_column；改输出目录 → 改参数 out_dir；改逻辑需找开发改 Python"
修掉后：错误 0 / 警告 0 → 变绿
```

**能照着改。** 给的例句本身就把「业务人员能改什么」写清楚了，是好的模板。

### N8 `out` 里没有 `program_script`（应为 warn）

```
lint 结果：错误 0 条 / 警告 1 条
  [W] 第 1 个节点（按部门拆分…）：out 里没有 program_script
      fix → 模块 main() 的返回值会无处落地（想用返回值就加 "out": {"program_script": "模块结果变量名"}）
修掉后：错误 0 / 警告 0 → 变绿
```

**能照着改。** 级别正确（warn，不阻断）。

### N9 脚本定义了没人用（应为 warn）

```
lint 结果：错误 0 条 / 警告 1 条
  [W] scripts.excel_split_unused 定义了但没有任何节点引用它
      fix → 要执行就加一个节点 "content": "$script:excel_split_unused"；不用就删掉
修掉后：错误 0 / 警告 0 → 变绿
```

**能照着改。** 级别正确。

---

## V3 正向用例

命令：`C:/Python314/python.exe logs/qa/v2_negative.py --only V3`

```
[V3] 完全正确的含 scripts 最小规格
lint 结果：错误 0 条 / 警告 0 条

  load_spec 验证：
    · load_spec 通过，无「未知字段 scripts」异常
    · nodes[0].inputs.content 加载后 = '$script:excel_split'
    · module_param 仍以 $json: 开头 = True
    · scripts 规范化后键 = ['excel_split']
    · scripts.excel_split 字段 = ['atom_suggestion', 'code', 'maintain_hint', 'name', 'why']
```

规格本身 = 「读 xlsx 按 `部门` 列拆成多个文件」，代码 20 行（openpyxl + 3 个 `$p:` 参数），
`why` / `maintain_hint` / `atom_suggestion` 齐全。
→ 干净规格是**真的能写到 0 错 0 警**的，不是「永远有噪音」；
`norm_win_path` 没破坏 `$script:` / `$json:`（代码字符串也未被改写）。

---

## V4 `--strategy` 静态行为

命令：`C:/Python314/python.exe logs/qa/v4_strategy.py`

```
V4-1 argparse 接受 auto / atom / script
   [--strategy {auto,atom,script}] [--no-few-shot] [--dry]
   三个取值都出现在 choices 里：PASS

V4-2 build_messages() 三种策略的 system prompt 差异
  auto    system prompt 长度 =   5385 字符 ；user 长度 =  22173 字符
  atom    system prompt 长度 =   5713 字符 ；user 长度 =  22173 字符
  script  system prompt 长度 =   5525 字符 ；user 长度 =  22173 字符
  auto != atom : PASS    auto != script: PASS    atom != script: PASS
  三种策略的 user prompt 完全一致（策略只作用于 system）：PASS

V4-3 atom 策略下 prompt 的「禁止脚本」强约束
  atom 含「本次强制策略：只用原子」                      PASS
  atom 含「禁止使用 Script.module」                    PASS
  atom 含 impossible 逃生口                          PASS
  atom 强制块确实追加在 SYSTEM_PROMPT 之后             PASS
  auto 不含该强制块                                   PASS

V4-4 SYSTEM_PROMPT 判定表 A/B 顺序
  A 段位置 = 1903 ；B 段位置 = 2280 ；C 段位置 = 2639
  A 在 B 之前：PASS   A 段含「禁止用脚本」：PASS   B 段 B-1~B-4：PASS
```

atom 相对 auto 多出的 328 字（人工复核过，约束是硬的）：

```
# 【本次强制策略：只用原子（--strategy atom）】
本次**禁止**使用 `Script.module`，也**不要**在顶层写 `scripts`。
- 能拼原子就用原子拼（哪怕 5~8 个节点、多两个循环或条件块）。
- 如果这条需求**确实**原子表达不了（命中 B-1~B-4 任一条），**不要硬凑 spec**，直接输出：
  {"impossible": true, "reason": "命中 B-2：…", "need_atom": "…"}
```

未发任何 LLM 请求（只调 `build_messages` 拿 messages，进程内不联网）。

---

## V5 `resolve_scripts(dry=True)`

命令：`C:/Python314/python.exe logs/qa/v5_resolve_dry.py`
（用 `unittest.mock` 把 `publish_flow.create_module / save_module / open_module` 换成
「一被调用就 AssertionError」的桩）

```
--- 1) 调用结果 ---
返回值 mids = []
抛出异常    = None
create_module 调用次数 = 0
save_module   调用次数 = 0
open_module   调用次数 = 0
未发任何网络请求：PASS

--- 2) 落盘 scripts/mod_gen_*.py ---
生成文件存在：True
文件前 4 行：
   # -*- coding: utf-8 -*-
   # 由 ai_flow.resolve_scripts 从规格 scripts.qa_dry_run_split.code 自动落盘。
   # 用途：让人能直接看/改/复用这段代码。
   # 注意：改这个文件**不会**影响流程 —— 平台上的模块是独立副本；
代码体完整（含 def main / args.get）：True

--- 3) 节点 content 是否被替换 ---
dry 后 content = '$script:qa_dry_run_split'
仍是 $script: 占位（未写假 moduleId）：PASS
spec 里没有 _resolved_modules：True

--- 4) 本地副作用 ---
台账变化：False → False（大小 -1 → -1）
原子需求池大小：1800 → 1800
dry 不写台账 / 不写需求池：PASS

--- 5) 引用不存在的脚本 ---
异常类型 = SystemExit（期望 SystemExit）：PASS
   节点引用了内嵌脚本 ['这个脚本不存在']，但规格顶层 `scripts` 里没有：
     可用脚本名：qa_dry_run_split
     修法：在 spec 顶层加 "scripts": {"这个脚本不存在": {"code": "def main(args): ..."}}
消息能指导修复（含缺的名字 / 可用名字 / 修法）：PASS
```

**dry 模式不发网络请求 → 不是 P0。** 且 dry 连台账/需求池都不写（这点比要求更严格，是好事：
否则真跑会误判「模块已存在」）。测试落盘文件已自动清理，`scripts/` 无残留。

---

## V6 防偷懒约束硬度（1 项未过）

命令：`C:/Python314/python.exe logs/qa/v6_hardness.py`

```
--- 1) why 缺失（None） ---
  [E] 第 1 个节点（拆分（自动脚本））：脚本 s 缺少 why（必须写明为什么原子做不到）
  → why 缺失是 err 级：PASS

--- 2) why 写了但没有命中条款（模型偷懒话术） ---
  errs=0 warns=1
  [W] 第 1 个节点（拆分（自动脚本））：脚本 s 的 why 没写命中哪条判定（B-1~B-4）
  → 偷懒话术被 err 拦住：FAIL（只有警告）

--- 3) nl2spec.generate() 自愈闭环：warns 是否阻断 ---
  generate() 里没有 `if warns:` 这种阻断分支：PASS
  → 结论：warns 只记录不阻断

--- 4) SYSTEM_PROMPT 判定表 A/B ---
  A 段位置 = 1903 ；B 段位置 = 2280
  B 段含 B-1/B-2/B-3/B-4：True
  → A 在前 + B 四条齐全：PASS
```

### 结论：`why` 的校验「一半是 err」

- `spec_lint.py:318` → **缺 why = err**（守住了，符合要求）
- `spec_lint.py:322` → **why 存在但没写 B-1~B-4 = warn**

而 `nl2spec.generate()`（757~769 行）只拿 `errors` 决定「回灌重写 / 接受」：
`warns` 只写进 `item["warns"]`，**不参与判断**。

于是「模型偷懒选脚本」仍有一条窄路能过：
```json
"why": "原子做不到，这个需求太复杂了"      ← 有 why，没 B-x → 只 warn → 规格被接受
```
`docs/流程规格DSL.md` §11.1 写的是「写进 `scripts.<名>.why`，`spec_lint` 会校验」，
实际只到 warn，文档也略微高估了约束力。

**建议（1 行改动，P1）**：`spec_lint.py:322` 的 `warn(` 改 `err(`，错误文案保留现值即可
（它已经写明「写成『命中判定 B-x：……』，写不出来说明该用原子拼」，可指导修复）。
如果担心误杀老规格 —— 不需要担心：老规格走 `$p:module_id`，`_lint_scripts` 在
`content` 不是 `$script:` 时直接 `continue`，这条规则**只作用于新内联脚本**。

---

## V7（附加项，非要求）两条没有覆盖到的链路

命令：`C:/Python314/python.exe logs/qa/v7_bonus.py`

```
A) build_process_content 对 $script: 占位是否透传
  $script:s（未解析）      落库 content = [{'type': 'other', 'value': '$script:s'}]   透传 PASS
  纯数字 moduleId（历史）  落库 content = [{'type': 'other', 'value': '2116342817880481792'}]  PASS
  $p:module_id（历史）     落库 content = [{'type': 'p_var', 'value': 'module_id'}]        PASS

B) _atom_demand_update 幂等
  同条款连续 3 次 → [('B-2', 1), ('B-2', 2), ('B-2', 3)]，表里仍 1 行，次数=3   PASS
  换条款 B-3 → 新增一行                                                       PASS
```

用途：确认 `flow_generator` 的「透传」不会把占位当变量引用而抛
「变量引用 `$script:xxx` 未定义」，同时确认 `≥3 次该沉淀成原子` 的统计真的幂等。

---

## 其他发现（不阻断，但交付前值得处理）

### R1（P1，需真机确认）：`$p:` 被烘焙成默认值 → 脚本模块可能拿不到运行时参数

实测（离线可证）：

```
qs: "scripts/flow_generator.py"  build_process_content(spec) 后，module_param 的值是：
[{"varName": "src_excel", "varValue": "F:\\…\\demo\\_dedup.xlsx"},
 {"varName": "key_col",   "varValue": "订单号"}, …]
```

`_bake_refs()` 把 `$p:src_excel` 直接换成了 **param 默认值字面量**（这是 2026-11-02 修
「设计器请求参数解析失败」的既有方案，**不是本次改动引入**，`git diff` 里没有它）。
后果：落库后的 `module_param` 是纯字符串，**不含任何引用** → 用户在平台/ openapi
改 `src_excel`，脚本模块仍然按**烘焙进去的默认值**跑。

而本次**新增的文档 §11.5** 写的是：
> `"$p:参数名"`｜引用**流程参数**（推荐，**可被 openapi 覆盖**；生成器会烘焙成默认值…）

这两句互相矛盾。更要紧的是：`nl2spec` 的 SYSTEM_PROMPT §三-4 要求
「**所有可调项提升为流程参数**」—— 也就是新能力产出的脚本，可调项**全部**走 `$p:`
→ 全部被烘焙 → 「业务人员能做微调」在**运行时**不成立（改了参数不生效，属于静默错误）。

我没在离线环境复现引擎运行时（按要求不碰平台），所以定性为 **P1 待确认**。
**请工程师用 1 次真机实验定性（10 分钟）**：
1. 用含 1 个 `$p:` 参数（如 `out_dir`）的脚本流程落库；
2. 在设计器里把该流程参数改成**另一个目录**再点运行；
3. 看脚本实际写到哪个目录。
   - 写到新目录 → 说明引擎会二次求值，R1 不成立，只需改文档措辞；
   - 仍写默认目录 → **R1 成立**，是「静默跑错」，需要改方案（例如：把可调项继续留
     `rpa:special` 换设计器可打开，或脚本改从流程参数以外的入口取值），并升级为 P0。

### R2（P2）：`--stage publish` 没有把 module_ids 传给发布

`scripts/ai_flow.py:1007`：

```python
r = pf.publish(ck, robot_id, spec["name"], ver)          # ← 没有 module_ids
```

而 `--stage expose` 走 `do_expose()` → `module_ids_of(spec)` → `publish_auto(..., module_ids=mids)`，
会校验「模块是否被带进版本」。`publish` 路径少了这道校验（平台通常会自动复制，所以不是
必然出错，只是**没有验证**）。建议：`pf.publish(..., module_ids=module_ids_of(spec))`。

### R3（P2）：draft 阶段 `IS_PASS: YES` 但节点里是 `$script:` 字面量

实测 `fg.validate()` 对该节点树返回 `is_pass=True, errors=[], warnings=[]`
（因为 `$script:` 只是普通字符串）。draft 不落库，所以不致命；但 `docs/generated_flow.json`
里是一个「假绿」的流程。建议 draft 阶段在 `resolve_scripts` 为 dry 且存在 `$script:` 引用时，
打印一行显式提醒（「本产物含未解析的脚本占位，不能直接落库」）。

### R4（P2）：`.gitignore` 缺两条

- `logs/`：`git status` 显示 `?? logs/`，**整个目录未跟踪**（含工程师的 `_tmp_nl2spec_dump/`
  —— 里面是完整 prompt 与模型输出）；一次 `git add .` 会把调试物一起提交。
- `scripts/mod_gen_*.py`：一次真实 `resolve_scripts` 就会产出新文件，同样会被误提交。

建议加：
```
logs/
scripts/mod_gen_*.py
```

### R5（P2）：`docs/原子需求池.md` 有一条联调残留

```
| 2026-11-02 | Excel按部门拆分成多文件 | excel_split_by_column | B-2 | Excel 按列拆分成多表 | 1 |
```
「Excel按部门拆分成多文件」在 `flows/` 里**不存在**（只在 `logs/_tmp_nl2spec_dump/` 里），
是联调时真实上传留下的。不影响功能，但会污染「≥3 次该做原子」的统计口径，交付前建议清掉
或标注 `（联调验证）`。

### R6（P2，细节）：两个小文案

- `scripts.x` **缺 `code` 字段**时报的是「没有定义 def main(args)」（探针 E）。真因是 code 没了。
  建议在 `_script_table` 里对 `code is None` 报「缺少 code 字段」（`load_spec` 已有这句，
  只是 lint 直接吃模型原文时走不到）。
- `scripts.x` 是列表时，除了「必须是对象或代码字符串」，还会追加一条「引用了内嵌脚本 x，
  但顶层 scripts 里没有它」（探针 F，2 条错误）。第二条是噪音，容易让人以为名字写错了。

---

## 复跑方式

```bash
cd "F:/AI-Workbench/10-Projects/星辰开源"
C:/Python314/python.exe logs/qa/v1_regression.py     # V1
C:/Python314/python.exe logs/qa/v2_negative.py       # V2 + V3（--only N2 / --only V3 单跑）
C:/Python314/python.exe logs/qa/v4_strategy.py       # V4
C:/Python314/python.exe logs/qa/v5_resolve_dry.py    # V5
C:/Python314/python.exe logs/qa/v6_hardness.py       # V6
C:/Python314/python.exe logs/qa/v7_bonus.py          # 附加
```

全部只读：不落库、不建机器人、不调 LLM、不改 `flows/`；唯一写盘是 V5 的临时
`scripts/mod_gen_qa_dry_run_split.py`（跑完自动删除）。

---

# 第二轮（Round-2 回归）：工程师改完 spec_lint / nl2spec 后复跑

复跑时间点：`spec_lint.py` 15:22:52、`nl2spec.py` 15:18:55 改动之后。
新增用例：`logs/qa/v8_newrules.py`（抽查 Round-3 新加的三条「防模型踩坑」err）。

| 项 | Round-1 | Round-2（当前代码） | 说明 |
|---|---|---|---|
| V1 87 条 flows 回归 | 0 错 / 2 警 | **0 错 / 2 警** | 无回归，警告仍与 before 快照一致 |
| V2 负向 N1~N9 | 9/9 | **9/9** | 全绿 |
| V3 正向 | PASS | **PASS** | 0 错 0 警；`load_spec` 未破坏占位 |
| V4 `--strategy` | 4/4 | **4/4** | prompt 长度仍为 5385 / 5713 / 5525 |
| V5 `resolve_scripts(dry)` | 5/5 | **5/5** | create/save/open_module 调用仍全 0 |
| V6 防偷懒硬度 | 3/4（V6-2 FAIL） | **3/4（V6-2 仍 FAIL）** | `spec_lint.py:322` 仍是 `warn(`，P1 **未修** |
| V7 附加（透传 / 幂等） | 5/5 | **5/5** | — |
| V8 新规则抽查 | — | **3/3 PASS** | 见下 |

## V8：Round-3 新增的三条规则实测（`logs/qa/v8_newrules.py`）

```
[R-a] file_path 写 "$clean_dept.xlsx"（想表达「变量 + 扩展名」）
  [E] 第 2 个节点（写文件）：$clean_dept.xlsx 看起来是「变量 + 扩展名」的拼接，不是变量名
      fix → 拼接请写成数组：["$clean_dept", ".xlsx"]（clean_dept 要由上游节点产出）
[R-b] msg 写 "$key_pos == -1"（把条件判断写进引用）
  [E] 第 2 个节点（写文件）：$key_pos == -1 看起来是一个表达式，不是变量名
      fix → 条件判断要用 Code.If 节点（inputs: args1 / condition / args2），不要在 $ 引用里写表达式
[R-c] Code.ForList 的 out 写成 {}，循环体里引用 $item
  [E] 第 3 个节点（写一个）：循环变量 $item 没有产出
      fix → Code.ForList 的 out 要显式声明，写成 "out": {"index":"idx","item":"item"}；引擎不会自动提供（你当前这块的 out 是空的）

V8 判定汇总：R-a PASS ｜ R-b PASS ｜ R-c PASS
```

判读：三条都报 err（阻断）、都把「下一步该改成什么」写进了 fix，符合「错误信息能指导修复」的标准。
`_split_var_ext()` 只认白名单后缀（`.xlsx`/`.txt` 等），不会把 `"a.b"` 这类变量名误判。

## Round-2 结论

- **没有引入新回归**：87 条 flows 仍 0 错误，负向 9 条、正向、dry 模式、策略层全部保持 PASS。
- **V6-2 仍未修**：`scripts/spec_lint.py:322` 的 `warn(` 未改成 `err(`，
  「why 有值但无 B-x」仍只报 warn，`generate()` 不拦 warn → 偷懒路径仍通。**建议本轮改掉（1 行）。**
- R1（`$p:` 烘焙）仍未定性，需要工程师真机实验；R2~R6 五个 P2 状态未变。

---

# 第三轮（Round-3 复验）：工程师改完 `why`=err + 清 P2 之后

新增用例：`logs/qa/v9_bool_in_module_param.py`（对应 N10，工程师主动提供的新约束）。

## 全部用例复跑结果

| 项 | Round-1 | Round-2 | Round-3（当前代码） |
|---|---|---|---|
| V1 flows 回归 | 87 条 / 0 错 2 警 | 87 / 0 错 2 警 | **88 条 / 0 错 3 警**（新增 E2E 生成的 `gen_按部门拆分Excel.json`，0 错 0 警） |
| V1 含 `Script.module` | 8 条 | 8 条 | **9 条** = 8 历史（`$p:`）+ 1 新（`$script:`），全绿 |
| V2 负向 N1~N9 | 9/9 | 9/9 | **9/9** |
| V3 正向 | PASS | PASS | **PASS** |
| V4 `--strategy` | 4/4 | 4/4 | **4/4**（prompt 变长：A 段位置 1903 → 2358，A 仍在 B 前） |
| V5 dry | 5/5 | 5/5 | **5/5** |
| V6 防偷懒 | 3/4（V6-2 FAIL） | 3/4 | **4/4 PASS**（`why` 无 B-x 已改 err） |
| V7 附加 | 5/5 | 5/5 | **7/7 PASS**（见下，需求池改成「按不同流程名去重」，我按新语义重写用例） |
| V8 新规则 | — | 3/3 | **3/3 PASS** |
| V9 / N10 布尔与 null | — | — | **FAIL（新 P1）** |

## V6 复核（P1 已闭环）

```
--- 1) why 缺失（None） ---
  [E] …脚本 s 缺少 why（必须写明为什么原子做不到）                → PASS
--- 2) why 写了但没有命中条款（"原子做不到，这个需求太复杂了"） ---
  errs=1 warns=0
  [E] …脚本 s 的 why 没写命中哪条判定（B-1~B-4）                → PASS（已是 err 级）
--- 4) A 段位置 2358 ；B 段位置 2735 → A 在 B 之前 PASS；B-1~B-4 齐全 PASS
```

## V7 复核（工程师把需求池语义改成「不同流程名计次」，我按新语义重写用例）

```
同一流程连续 3 次 → 返回 [('B-2',1),('B-2',1),('B-2',1)]
  | 2026-11-02 | 2026-11-02 | QA流程 | excel_split | B-2 | Excel 按列拆表 | 1 |      PASS（跑 3 次只算 1）
再加 2 条不同流程 → 返回 ('B-2',3)
  | 2026-11-02 | 2026-11-02 | QA流程、QA流程B、QA流程C | excel_split | B-2 | … | 3 |   PASS（不同流程才累计）
换条款 B-3 → 新增一行                                                              PASS
老 6 列格式行 + 新流程 → 就地升级为 7 列、次数按不同流程重算=3                        PASS
```

## V9 / N10（新 P1）：`module_param` 里的 JSON 布尔 / null 没人拦

`C:/Python314/python.exe logs/qa/v9_bool_in_module_param.py`

```
[N10-a] module_param 里写 JSON 布尔 true
  module_param = [{"varName":"flag","varValue":true},{"varName":"src","varValue":"$var:item"}]
  构建后 inputList 项 = need_parse='json_str'
  值(前80) = '[{"varName": "flag", "varValue": true}, {"varName": "src", "varValue": {"rpa": "'
  → 走 need_parse 路径（值会被当 Python 源码）：是
  lint：错误 0 / 警告 1（只有一条无关的「必填参数用默认值」）
  → 生成期拦住了 JSON 布尔/null：FAIL（漏检）

[N10-b] JSON null 同理 → FAIL（漏检）
[N10-c] 正确写法（布尔写成字符串 "true"）→ PASS（不误报）
```

静态证据链：`module_param` 含 `$var:` → `flow_generator` 走 `need_parse="json_str"` 分支
（第 891~893 行）→ 值原样（含 `true`/`null`）被当 Python 源码交给 `complex_param_parser`
→ `true` 在 Python 里未定义 → `NameError`。这是**生成期能拦、真跑才炸**的一类，正好是
`spec_lint` 该管的。

风险面：
- 现在**只有** `$var:`（循环变量）会走这条路径，`$p:`-only 走烘焙（不 need_parse），所以命中的是
  「循环里跑脚本」这类流程；
- 但一旦按 R1 的结论把 `$p:` 也改成 `need_parse`（B 组方案），**所有** `module_param` 都会走
  这条路径 → 模型按全局规则 6「Bool 参数写 JSON 布尔 true/false」写就全线 `NameError`。
  **建议在改生成器之前先把这条 lint 规则加上。**

建议规则（`spec_lint.py` 规则 1 的 module_param 结构检查里加）：
遍历 payload 每个 `varValue`，若 `isinstance(v, bool)` 或 `v is None`
→ `err`「module_param 的 varValue 只能是字符串或 `$p:`/`$var:` 引用；JSON 布尔/null 会被当
Python 源码插入 → `NameError`。布尔请写 `"true"` / `"false"`（与 `def main(args)` 里 args 全为
字符串的契约一致）」。

## R1 现状（工程师已做 E1 实验，我方离线复核一致）

工程师 A/B 实验结论：`varValue={"rpa":"special","value":[{"type":"p_var",...}]}` + `need_parse="json_str"`
→ 引擎真的求值（改参数生效）；烘焙字面量 → 不生效。我方离线复核：**产物确实是 A 态**：

```
flows/gen_按部门拆分Excel.json（E2E 真实生成的那条）构建后：
content      | need_parse=None | {"type":"other","value":"$script:excel_split_by_dept"}
module_param | need_parse=None | "[{\"varName\":\"src_file\",\"varValue\":\"F:////demo////data////销售明细.xlsx/"},
                                  {\"varName\":\"split_column\",\"varValue\":\"部门\"},
                                  {\"varName\":\"out_dir\",\"varValue\":\"F:////demo////out////split/"}]"
```

即：**这条已进 `flows/` 的真实流程，现在就是「改参数不生效」状态**。生成器修好后，
这个文件要重新生成并重落库，否则它会一直是 A 态。

---

# 第四轮（Round-4 复验）：N10 已修 + `--param-binding` 离线对照

新增用例：`logs/qa/v10_param_binding.py`（baked / bound 两种模式的离线对照，**不连平台**）。

## 结果总表

| 项 | R3 | **R4（当前）** |
|---|---|---|
| V1 88 条 flows | 0 错 3 警 | **0 错 3 警**（第 3 条 D1 文案已去掉 `⚠️` 前缀） |
| V2 负向 N1~N9 | 9/9 | **9/9** |
| V3 / V4 / V5 / V8 | PASS | **PASS** |
| V6 防偷懒 | 4/4 | **4/4** |
| V7 附加 | 7/7 | **7/7** |
| V9 N10 | 2 FAIL（漏检） | **4/5 PASS**（N10-a/b/c/d 通过；N10-e 文案待修） |
| V10 参数绑定 | — | **5/5 PASS** |

## V9（N10 已修）

```
[N10-a] varValue: true  → [E] module_param 里 flag 的 varValue 是 true —— 不能是 JSON 布尔/空值
                              fix → 引擎会把它们当 Python 源码求值，报 NameError…改成字符串 "true" / "false"   PASS
[N10-b] varValue: null  → [E] 同上（显示 null）                                                     PASS
[N10-c] varValue: "true" → 0 错                                                                     PASS
[N10-d] varValue: 3（数字）→ [W] …是数字 3（模块拿到的 args 约定全是字符串）
                              fix → 改成字符串 "3"（脚本里再 int(...) 转），否则 args.get() 拿到 int，
                                    .strip()/.lower() 会 AttributeError                              PASS
[N10-e] null 分支的 fix 文案                                                                        FAIL
```

**N10-e（P2，文案）**：`null` 的 fix 是从布尔分支照抄的 ——
`报 NameError: name 'true' is not defined。改成字符串 "true" / "false"`。
对 `null` 来说错误名不对（应是 `name 'null' is not defined`），改法也不对（应写 `""` 空串）。
建议按分支给文案，或写成一句通用话术：
「布尔写 `"true"`/`"false"`；空值写 `""`（`need_parse="json_str"` 会把值当 Python 源码，
`true`/`null` 在 Python 里未定义）」。不影响阻断效果，只影响「照不照得着改」。

**关于数字留 warn 的判断：同意工程师的做法，不建议改 err。**
理由：数字在 Python 里是合法字面量，**不会 NameError**，只是违反「args 全是字符串」的契约；
真写成 err 会把「本来能跑」的流程判死。当前 warn 的 fix 已经把后果写清（`.strip()` 会 AttributeError），
这足够。唯一小瑕疵：fix 里写「脚本里再 int(...) 转」，对 `3.5` 这种应是 `float(...)`。

## V10 `--param-binding {baked,bound}` 离线对照（不连平台）

```
V10-1 默认 PARAM_BINDING_MODE='baked'；非法值报错并提示 baked/bound                  PASS
V10-2 同一份规格（gen_按部门拆分Excel.json）：
       [baked] need_parse=None       value=[{"varName":"src_file","varValue":"F://demo//data//销售明细.xlsx"}, …]
       [bound] need_parse='json_str' value=[{"varName":"src_file","varValue":{"rpa":"special","value":[
                                             {"type":"p_var","value":"src_file"}]}}, …]
       → baked 是死值、bound 保留 p_var 引用                                        PASS
V10-3 88 条 flows 两种模式各构建一遍：
       [baked] 构建失败 13 条 / lint 有错 0 条
       [bound] 构建失败 13 条 / lint 有错 0 条
       两种模式构建失败集合一致：PASS（13 条全是「元素库里没有该元素」的离线限制，
                                      与参数绑定模式无关；需平台元素库才能构建）
       两种模式 lint 失败集合一致：PASS
V10-4 baked 模式提醒：受影响节点 1 个 / src_file、split_column、out_dir
       调用点有 if fg.PARAM_BINDING_MODE == "baked" 把关（bound 下不打扰）        PASS
```

结论：**bound 不是「只能跑新流程」的旁路** —— 88 条全量在 bound 下与 baked 表现完全一致
（同样的 13 条元素类离线失败、同样的 0 条 lint 错），切换模式不会引入结构性回归。

注：工程师另有一个会把临时机器人建到平台上的 `logs/_tmp_binding_roundtrip.py`，我**没有跑**
（我的约束是不碰平台）；真机侧的一手数据以他的报告为准，我这侧只做离线对照。

## Round-4 结论

- N10 漏检已修，**生成期能拦住 JSON 布尔/null**；剩一条 null 分支的文案待修（P2）。
- 数字留 warn 的裁剪判断正确，建议保留。
- `--param-binding` 两种模式的落法差异、全量兼容性、提醒机制都验过了，无回归。
- 全量：**88 条 flows 0 错 / 3 警**；V2 9/9、V3/V4/V5/V6/V7/V8/V10 全绿。
