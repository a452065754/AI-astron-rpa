# 其余平台缺陷 · 验证与上报结果（2026-11-05 16:00）

两阶段做完：
1. **静态复核**：直接拉 `iflytek/astron-rpa` **main 分支源码**，逐条 AST 解析定位（不是看 release notes，也不是凭记忆）
2. **可执行验证**：能跑的最小复现一律跑一遍（参数解包、灰度通道、delta 取值、未定义符号），拿不确定的标 INCONCLUSIVE

复跑命令：

```bash
python docs/upstream/verify_main.py \
       --main-dir .tmp_rev/upstream \
       --components "$(cat .tmp_rev/components.txt)"
```

## 一、结论速览

| 状态 | 条数 | 明细 |
| --- | --- | --- |
| ✅ 上游已修（不报） | 3 | A1、A2、A10 |
| 🔴 **确认存在 → 已上报** | 12 | A3、A4、A5、A7、A8、A9、A12、A13、A14、A15、A16、A18 |
| ⚠️ 静态不足 → 本次未报 | 2 | A6、A11（需 Office 实机） |
| ⛔ 不报 | 1 | A17（已结案：我方构造错，不是平台缺陷） |

加起来 14 + 1 = 15 条历史记录（不含 A15 之外的 A19/A20，A20 已单独报 #909）。

## 二、已上报（12 条 → 合并为 9 个 issue）

| Issue | 标题 | 覆盖 | 级别 |
| --- | --- | --- | --- |
| [#910](https://github.com/iflytek/astron-rpa/issues/910) | vision 模板匹配两侧灰度转换常量不一致 | A15 | P1 |
| [#911](https://github.com/iflytek/astron-rpa/issues/911) | Dialog 4 个原子无超时且无前端时静默挂起 | A12 + A13 | **P0** |
| [#912](https://github.com/iflytek/astron-rpa/issues/912) | screen_lock / screen_unlock 是 NotImplementedError 空实现 | A14 | P1 |
| [#913](https://github.com/iflytek/astron-rpa/issues/913) | convert_to_txt 必抛 NameError: Document | A16 | P1 |
| [#914](https://github.com/iflytek/astron-rpa/issues/914) | Word 覆盖已存在文件 os.remove 被注释 | A3 | P1 |
| [#915](https://github.com/iflytek/astron-rpa/issues/915) | FTP 文件夹重命名传错变量 | A4 | P1 |
| [#916](https://github.com/iflytek/astron-rpa/issues/916) | 原子声明与实现不一致三处 | A5 + A7 + A8 | P1 |
| [#917](https://github.com/iflytek/astron-rpa/issues/917) | chat_streamable 硬取 delta["content"] | A18 | P2 |
| [#918](https://github.com/iflytek/astron-rpa/issues/918) | 36 个 IE 系原子缺 astronverse.iebrowser | A9 | P2 |

> label 一律没打上 —— 外部账号没有仓库 triage 权限，需仓库维护者手动加 `bug`。
> 正文与标题的源文件在 `docs/upstream/issues/*.md`，改完可重发（脚本会跳过已提交的）。

## 三、逐条验证结论（main，2026-11-05）

| 编号 | 缺陷 | 结论 | 证据 |
| --- | --- | --- | --- |
| A3 | Word 覆盖已存在文件不生效 | **CONFIRMED** | `word/core.py:81` OVERWRITE 分支 `# os.remove(file_path)` 仍注释，直接 `return file_path` |
| A4 | FTP 文件夹重命名必失败 | **CONFIRMED** | `ftp.py:310` FOLDER 分支仍传 `cur_file_name`（该参数在文件夹模式下恒为空串） |
| A5 | 多输出声明 ≠ 实现 | **CONFIRMED** | AST：`read_data` / `copy_data` 各声明 4 个输出，实现 return 最多 1 个值 |
| A7 | 字典有实现无的死 key | **CONFIRMED** | `DataTable.sort_table` / `Docx.open_document` / `Docx.read_document_content` 在 main 实现类中均无同名方法 |
| A8 | 参数求值返回 3 值 | **CONFIRMED** | `params.py:126` `return "".join(pieces), need_eval, need_eval`；签名 `-> (Any, bool)`；实测 `ValueError: too many values to unpack (expected 2)` |
| A9 | IE 模块整块缺失 | **CONFIRMED** | main 的 `engine/components` 共 26 个组件，无 `astronverse-iebrowser`；字典侧 36 个 `IeBrowser*` key |
| A12 | 无前端时 Dialog 崩 | **CONFIRMED** | `ws.py:112` `run_coroutine_threadsafe(..., Ws.loop)` 无 None 保护；`Ws.loop=None` 实测 `AttributeError` |
| A13 | 4 个 Dialog 原子无超时 | **CONFIRMED** | `dialog.py` 行 293/359/459/578 四处裸 `done.wait()`；`message_box` 用了 `wait_with_timeout`，其余 4 个没有 |
| A14 | screen_lock / unlock 空实现 | **CONFIRMED** | `system.py:153/190` 仍 `raise NotImplementedError()`，且 `screen_unlock` 声明了 4 个永不生效的参数 |
| A15 | CV 模板灰度通道用错 | **CONFIRMED** | `cv_match.py:135` 大图 `BGR2GRAY`、`:146/:152` 模板/锚点 `RGB2GRAY`；真实 cv2 实测 **0.884786 vs 1.000000** |
| A16 | convert_to_txt NameError | **CONFIRMED** | `core_win.py:830` `Document(oldfilepath)`，该文件只导入了 `IDocumentCore`，无 `Document` |
| A18 | 流式 delta.content 硬取 | **CONFIRMED** | `llm.py:50`；标准首包 `{"delta":{"role":"assistant"}}` 实测 `KeyError: 'content'` |
| A6 | Excel 插图变外链 | **INCONCLUSIVE** | 需 Excel 实机解包看 `xl/media`；本次无 Office 环境，未报 |
| A11 | insert_sep 不分段 | **INCONCLUSIVE** | 需 Word 实机看 `Selection.Start` 是否前进；本次未报 |

### 验证过程中被纠正的两处（留痕，避免下次重犯）

1. **A4 一开始被脚本误判为 FIXED** —— 检测逻辑只在整个文件里搜 `cur_folder_name` 是否存在，而该变量在 `if cur_folder_name not in exist_file` 里本来就出现过。改成**只看 FOLDER 分支块内的 `ftp_rename(` 调用参数**，才暴露出 L310 仍传 `cur_file_name`。
2. **A8 一开始被误判为 FIXED** —— 脚本挑到了 `parse_param`（签名 `-> InputParam`，正常返回 1 个值），而真正的问题函数是 `_param_to_eval`。改成按函数名精确定位后确认仍有 3 值返回。

这两处说明：**静态检查脚本本身也会错**，所以每条必须落到"具体的行 + 具体的调用点"，不能只做关键字存在性判断。

## 四、已修好的三条（不要再报）

| 编号 | 缺陷 | main 现状 |
| --- | --- | --- |
| A1 | `fill_string_to_length` 右补齐必崩 | `string.py:237` 已改成 `[0:total_length_int]` |
| A2 | `PDF.convert_img_to_pdf` 永远返回 None | `pdf/__init__.py:27` 已有 `return files_path`，还支持列表/逗号/`.pdf,` |
| A10 | 浏览器扩展空闲断连 | `browser-plugin/src/background.ts:47` 已加 `chrome.alarms` 每分钟保活 |

A10 的周期用的 1 分钟（我们当初建议 ≤20 秒）。若现场仍遇到「Chrome 闲置后第一次调用失败」，可单独开一条讨论周期。

## 五、本次没报的两条，怎么补

### A6 · Excel 插图变外链

要验证的点：`Worksheet.insert_pic` 底层走的是 `Pictures().Insert(path)` 还是 `Shapes.AddPicture(path, False, True)`。

```python
# 跑完后解包产物
unzip -l result.xlsx | grep xl/media      # 空 → 是链接
```

本机无 Excel，跑不了。有 Office 的机器上 5 分钟能出结论。

### A11 · insert_sep 不分段

看 `Selection.InsertParagraph()` 之后 `Selection.Start` 有没有前进：

```python
doc.Paragraphs.Count     # 1 → 2 → 3 递增
selection.Start          # 停在原地 → 就是本缺陷
```

同样需要 Word 实机。

> 这两条历史上都在 v1.1.7 上实机复现过（`平台缺陷清单.md` A6/A11 有实测记录），只是本次对 main 的**静态**核对无法判定。要报的话，建议补一次 main 实机复跑，否则容易被打回「无法复现」。

## 六、维护说明

- 本文件是**对外口径**：只写代码位置与复现，不含内网 IP / 账号 / 业务系统名
- 内部处置优先级与影响面见 `docs/平台缺陷清单.md`
- 上游若升级，先重跑 `verify_main.py` 再决定是否需要更新 issue
