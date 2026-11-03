---
name: astron-rpa-flow
description: 把一句自然语言需求变成可离线运行的固定 RPA 流程（Astron RPA / 星辰 RPA）。触发场景：用户说「帮我用 RPA 做 XX」「自然语言生成 RPA 流程」「让 AI 操作 RPA」「把这张表按部门拆开」「把这个文件夹的 PDF 都转成 Word」「RPA 流程生成」「astron-rpa」「星辰开源 RPA」，或要求安装/配置/调试这套 RPA 工具链时。也用于换电脑后的环境自检、API Key 配置、Python 子流程模块重建。
agent_created: true
---

# 自然语言 → 固定 RPA 流程

把用户的**一句话**变成一条能在 Astron RPA 设计器里独立运行的固定流程。
流程搭好后**不需要 AI**，这是本技能的交付标准。

## 第 0 步：找到项目在哪（每次会话先做）

```bash
# 技能安装时会写一个 PROJECT_ROOT.txt，里面是仓库绝对路径。
# 两个 client 的技能目录不同，哪个存在读哪个（WorkBuddy 桌面客户端优先进第一个）：
cat ~/.workbuddy/skills/astron-rpa-flow/PROJECT_ROOT.txt 2>/dev/null \
  || cat ~/.workbuddy-ai/skills/astron-rpa-flow/PROJECT_ROOT.txt
```

拿到路径后 `cd` 进去，**后面所有命令都在项目根目录执行**。
找不到这个文件时：问用户仓库在哪，或搜索 `ai_flow.py`。
**别自己猜路径** —— 换台机器装到别处就全错了。

> 刚装完发现「技能在但用不上」？跑 `python scripts/doctor.py`，最后两行就是查技能包和 MCP 接线的。

## 第 1 步：判断现在处于哪个阶段

| 用户说什么 | 你要做 |
| --- | --- |
| 「帮我装这个项目」/ 刚 clone 下来 | 跑 `python scripts/install.py`，然后要 Key |
| 「Key 是 xxx」 | 跑 `python scripts/install.py --key xxx --host h:p --robot-id id` |
| 「帮我做个 XX 流程」 | 走第 3 步 |
| 「跑不通 / 报错」 | 先 `python scripts/doctor.py`，按 `→` 提示修 |
| **「我换设备了」/ 换了台电脑** | **先读 `docs/待办清单.md` §1**，按那里的 5 步重建环境，再往下走 |

## 第 2 步：装 + 配（只有第一次）

```bash
# 装依赖 + 技能包 + MCP 登记 + 自检
python scripts/install.py

# 用户发来 Key 之后
python scripts/install.py --key <API_KEY> --host 192.168.76.130:32742 --robot-id <robotId>
```

装完必须看到自检没有 ❌。常见红灯与修法（脚本会直接打印）：

- `客户端 conf.yaml ❌` → 没装设计器，或设 `RPA_CLIENT_CONF` 指到 conf.yaml
- `登录态 ❌` → 让用户打开设计器登录一次
- `本地网关 ⚠️` → 设计器没启动（只影响本地真跑，落库仍可）
- `目标机器人 ❌` → 改 `defaults.robot_id`

> **Key 只在用户主动发来时使用，不要编造、不要写进聊天记录之外的地方。**
> 配置写进 `rpa_config.json`（已在 `.gitignore`，不会入库）。

> ⚠️ **`git pull` 不会同步技能副本。** 技能目录里的是 `skill/astron-rpa-flow/` 的**拷贝**，
> 仓库更新后必须重跑一次 `python scripts/install.py`，否则 AI 读到的是旧手册
> （2026-11-03 实际踩到：仓库里已是 282 行，客户端那份还停在 3 天前的 265 行）。

### 2.1 换设备了怎么办（**用户说「我换设备了」就走这里**）

robotId / elementId 换机器后**全部作废**，但规格、脚本、文档全部可迁移。按顺序：

```bash
# ① 补两个「不在 git 里」的文件（最容易漏！git clone 拿不到）
#    rpa_config.json（网关地址/API Key/默认 robotId）
#    docs/_local_secrets.md（SSH/MySQL/Redis/Minio/Casdoor 密码留档）
#    旧机器：python scripts/handover_export.py   → 产出 handover/ 整个拷过来
#    新机器：python scripts/handover_export.py --check
python scripts/rpa_config.py        # 确认生效值

# ② 环境自检（设计器已登录、Chrome 装了 RPA 插件）
python scripts/doctor.py

# ③ 换机必做（坑 118，不做 DataTable 全系原子必崩）
mkdir -p "$APPDATA/astron-rpa/astron"

# ④ 盘点平台上有什么（2026-11-03 实测：91 个机器人 / 本项目 76 条已落库；
#    规格共 92 条，未落库 16 条见 docs/待办清单.md §3.1）
python scripts/publish_flow.py --list

# ⑤ 网页流程还要看桥接
python scripts/element_pick.py --status
```

> ⚠️ `rpa_config.json` 里的 `defaults.robot_id` 是**旧机器**的平台 ID，换机后必然失效。
> `--check` 会喊出来，但不会替你改——一律用下面的 `--robot-name`，别信这个默认值。

**关键：用 `--robot-name`，不要抄旧 robotId。**

```bash
python scripts/ai_flow.py --spec flows/Excel统计行列小流程.json --robot-name Excel统计行列小流程
```

`--robot-name` 按名字找机器人，**找不到就新建**。命名约定：规格 `XXX小流程` ↔ 机器人 `XXX`
（历史遗留，脚本两种都试）。两个例外已登记在 `publish_flow.ROBOT_ALIASES`。

**别做的事**：不要手工去设计器建机器人再抄 id（换设备必卡这里）；不要复用旧 robotId（会报找不到机器人）。

## 第 3 步：把需求变成流程（核心）

### 3.1 先看有没有现成的（**必须做，别急着自己拼**）

```bash
python scripts/ai_flow.py --nl "<用户原话>" --stage draft
```

- **命中已有规格** → 输出会写 `匹配度 x.xx`。先跑 `--stage draft` 让用户确认是不是这条，
  再跑完整链路（第 4 步）。
- **没命中**（打印「没听懂这句话」+ 流程清单）→ 进 3.2 自己拆解。

### 3.2 自己拆解成节点（没命中时）

**严格遵守能力降级顺序**，不要一上来就写 Python：

| 顺序 | 手段 | 什么时候用 |
| --- | --- | --- |
| ① | **现有原子** | 能用原子拼出来就用（`Excel.*` / `File.*` / `Docx.*` / `PDF.*` / `BrowserElement.*`） |
| ② | **Python 子流程** | 原子做不到（如「分组后输出多个文件」这种一次写多个目标） |
| ③ | **新原子** | 前两条都不行，且用户明确同意才考虑 |

#### 第 1 步：先查有哪些原子（**别凭印象写原子名**）

```bash
python scripts/atom_catalog.py                        # 有哪些域、各多少原子
python scripts/atom_catalog.py --domain Excel --detail # 该域的原子 + 必填参数 + 枚举值
python scripts/atom_catalog.py --atom Excel.read_excel # 单个原子的完整参数表
python scripts/atom_catalog.py --search 拆分           # 按关键词找
```

原子名写错、参数名写错、枚举值写错，`spec_lint` 都会拦下并给候选——但先查一次能少走两轮。

**注意「字典里有 ≠ 本机能跑」**：服务端字典 357 个，本机 `python_core` 实际只有 291 个实现。
IE 系 36 个整块缺失（`astronverse.iebrowser` 没装）、`DataTable.sort_table`、`Docx.open_document` 等
写了必挂 `AttributeError`。选原子前先看 `docs/atom_missing.json`（或跑一次
`python scripts/atom_impl_check.py` 重新核对），`spec_lint` 也会直接拦。

#### 第 2 步：写规格

写进 `flows/<流程名>.json`，字段 `{name, nl_examples, keywords, params, nodes}`。
每个节点形如：

```json
{
  "atom": "Excel.read_excel",
  "alias": "读数据",
  "out": {"read_excel_contents": "data"},
  "inputs": {"excel": "$wb", "read_range": "all"}
}
```

引用规则：`$p:参数名` = 流程参数、`$变量名` = 上游节点产出、`$json:[...]` = 复杂参数（可内含 `$var:`/`$p:`）。

**路径默认值必须写 `${PROJECT_ROOT}`**（2026-11-03 起，`spec_lint` 规则 1.8 会拦）：

```json
{"name": "src_file", "type": "Str", "default": "${PROJECT_ROOT}/demo/订单.xlsx", "describe": "源表"}
```

发布时 `load_spec()` 把它换成本机仓库根，所以 clone 到任何目录都成立。
**不要**改成相对路径（`demo/订单.xlsx`）—— 引擎执行前会 `os.chdir(%APPDATA%\astron\<project_id>)`
这个编译缓存目录，相对路径必然找不到文件。仓库外固定位置写 UNC，或在规格顶层加
`"allow_abs_path": true`。

#### 网页流程：元素不用人点，你自己去拾（**别让用户手动点**）

用户问「拾取元素这个要我来做吗」时，答案是**不用**。`scripts/element_pick.py` 会打开浏览器、
调设计器自带的插件现场取元素、写进元素库、再回查一次定位。你只需要知道**页面地址 + 一个 cssSelector**。

```bash
python scripts/element_pick.py --status                       # 桥接是否通 + 元素库有什么
python scripts/element_pick.py --probe "<css>" --url <url>    # 只探测，先确认命中且可见
python scripts/element_pick.py --css "<css>" --name <名字> --url <url> --robot-id <id>
python scripts/element_pick.py --check <elementId>            # 只读校验
```

**更好的做法**：别手敲命令，直接在规格里声明，让流水线自己补：

```json
"element_requirements": [
  {"name": "百度_搜索输入框", "kind": "web",
   "pick": {"url": "https://www.baidu.com", "css": "#chat-textarea"}}
]
```

节点槽位按名字引用：`"elements": {"element_data": {"kind": "web", "name": "百度_搜索输入框"}}`。
`ai_flow.py` 在 `[2/6]` 会把缺的元素拾好再往下走（幂等，同名已存在就复用）。

四条硬规矩（都是实测踩出来的）：

1. **元素必须挂在该流程自己的机器人下**。本项目一条流程一个同名机器人，
   借别人的会报 `Failed to get element data ... empty response`。
2. **选择器要挑可见节点**。现代站点会把旧版 DOM 留成隐藏节点（百度首页的 `#kw`/`#su` 就是），
   `querySelector` 第一个命中的常常正是它。脚本已用 `right-left>0` 卡死并拒绝落库，
   所以「命中但存不进去」时**换选择器，不要加 `--allow-hidden` 绕过**。
3. **点击类节点写 `"simulate_flag": true`**。默认的 JS 合成点击在百度这类站点上
   「原子返回 SUCCESS 但页面不动」，系统级点击才生效。
4. **别自己编 cssSelector 就落库**。先 `--probe` 看 `可见=是`、命中数=1，再写。

#### 第 3 步：必须过 lint（**不要跳过**）

```bash
python scripts/spec_lint.py flows/<流程名>.json
```

它按文本逐项查，报错**带第几个节点和怎么改**：原子不存在（给相近候选）、缺必填参数、枚举非法、
`$p:` 未声明、变量未产出、`ForList/If` 没闭合、`module_id` 没参数化。

改到 `✅ 无阻塞问题` 再往下。**这一步能省掉 80% 的试跑失败。**

写规格时注意（都是踩过的坑）：

1. **循环块结束标记一律是 `Code.ForEnd`**，不是 `Code.ForListEnd` / `Code.ForStepEnd` / `Code.ForDictEnd`
   （这三个编译成 `None(...)` 塞进循环体，一跑就 `TypeError: 'NoneType' object is not callable`）。
   已实测：`Code.ForList`、`Code.ForStep`、`Excel.loop_excel_content` 都用 `Code.ForEnd` 收尾；
   判断块用 `Code.IfEnd`；`File.file_exist` / `Folder.folder_exist` 本身就是开块，也配 `Code.IfEnd`。
   `spec_lint` 已内置死原子黑名单，写错会直接拦。
2. **循环变量传进子流程要写 `"$var:item"`**，纯字符串永远是字面量。
3. **`Script.module` 的 `module_id` 做成流程参数**（不要写死在节点里），换环境才不用改流程。
4. **`keywords` 决定自然语言能否命中**：放用户真会说的词，不要放「批量/文件夹」这类会误伤的歧义词。
5. **`out` 的 key 必须是该原子的真实输出项**（用 `atom_catalog --atom` 查），写错就拿不到值。
6. `--stage draft` 还会再跑一遍「构建 + 校验」，两道都过才落库。

### 3.3 需要新的 Python 子流程时

```bash
# 1) 写 .py 到 scripts/mod_xxx.py，函数签名固定为 main(args) -> dict
# 2) 建机器人 + 上传模块 + 把 module_id 写回规格（一条命令）
python scripts/make_module.py --py scripts/mod_xxx.py --new-robot "<流程名>" \
  --name "<模块名>" --spec flows/<流程名>.json
```

模块**按机器人隔离**：跨机器人引用别人的 `module_id` 会得到 `None`，报「无法导入模块 .None」。
换环境 / 换机器人**必须重建**。

## 第 4 步：跑完整链路并交付

```bash
python scripts/ai_flow.py --spec flows/<流程>.json --robot-id <robotId>
```

它一次做完：结构校验 → 运行前预检 → 写入机器人**主流程** → 本地真跑 → 生成评审单。

看到 `✅ 已就绪，等你人工评审` 才算交付。然后把评审单路径给用户，并说清：

1. 去设计器哪个机器人、点「编辑」、点「运行」；
2. 要确认哪几件事（能跑通 / 输入对 / 输出对 / 没残留窗口和 `~$` 锁文件）。

## 第 5 步：评审通过后发布 / 调用

```bash
# 发布版本（必须人工评审通过后才做）
python scripts/ai_flow.py --spec flows/<流程>.json --stage publish --version N

# 用 API Key 直接触发（不经过界面）
python scripts/ai_flow.py --spec flows/<流程>.json --stage invoke --params '{"src_excel":"D:/x.xlsx"}'
```

## 硬性规则（违反就等于没交付）

- **不猜**：听不懂就列出现有流程让用户改说法，**绝不静默跑成别的流程**。
- **不跳校验**：任何流程都必须 `IS_PASS: YES` + 本地真跑 SUCCESS 才能交给用户评审。
- **批量要明说**：用户说「整个文件夹都转」而流程是单文件的，**主动拦下并说明**，不要只处理一个文件装没事。
- **核心目的不偏移**：用户要的是「自然语言 → 固定 RPA 流程」，不是把某个示例流程调通。
- **不动服务端**：这套工具是外挂，不装插件、不改数据库、不重编镜像。
- **凭据不外泄**：Key 只写 `rpa_config.json`；不要打印全文，不要提交到 git。

## 常见报错对照

| 报错 | 真因 | 处理 |
| --- | --- | --- |
| `无法导入模块 .None` | 模块按机器人隔离，引用了别人的 module_id | `make_module.py --robot <目标id>` 重建 |
| `本地网关 127.0.0.1:13159 未监听` | 设计器没启动 | 启动客户端，或 `--no-run` |
| `找不到设计器 cookie 文件` | 没登录过 | 打开设计器登录一次 |
| 首次点开子流程弹一次错 | 客户端首次编译 venvs 的时序 | 正常现象，刷新即可（不是缺陷） |
| `MemoryError`（批量） | 图片/数组没释放 | 循环体里 `im.close()` + `del` + `gc.collect()` |
| 说「都转成 Word」被拦 | 批量意图 vs 单文件流程 | 用 `批量PDF版面还原小流程`，或加 `--skip-batch-check` |

## 也可以走 MCP（可选）

如果客户端支持 MCP，装了本项目后可直接调这些工具，不必手敲命令：

`rpa_setup` / `rpa_doctor` / `rpa_list_flows` / `rpa_generate` / `rpa_run` / `rpa_publish` / `rpa_invoke` / `rpa_make_module` / `rpa_atom_catalog` / `rpa_lint_spec`

配置见 `docs/MCP与技能包.md`。
