# 其余平台缺陷 · 上游状态盘点（2026-11-05 15:50）

核对方式：直接拉 `iflytek/astron-rpa` **main 分支源码**（GitHub API），逐条看缺陷位置是否还在。
不是看 release notes，也不是凭记忆。

## 一、结论速览

| 状态 | 条数 | 明细 |
| --- | --- | --- |
| ✅ 上游已修（不用报） | 3 | A1、A2、A10 |
| ❌ 仍存在（可报） | 12 | A3、A4、A6※、A8、A9、A11、A12、A13、A14、A15、A16、A18 |
| ⚠️ 未核对 | 2 | A5、A7（需跑 `atom_impl_check.py` 比对字典与实现） |
| ⛔ 不报 | 1 | A17（已结案：是我方构造错，不是平台缺陷） |
| 已报 | 1 | A20 → [#909](https://github.com/iflytek/astron-rpa/issues/909) |

※ A6 的底层在 Excel `Worksheet.insert_picture`，本次未展开到那一层，需实机跑一次 `unzip -l 看 xl/media` 才能定。

## 二、逐条核对结果

### ✅ 已经修好的（不要再报，报了会被打回）

| 编号 | 缺陷 | main 现状 | 证据 |
| --- | --- | --- | --- |
| A1 | `fill_string_to_length` 右补齐必崩 | **已修** | `string.py:237` 已改成 `[0:total_length_int]`；旧代码是 `[0:total_length]`（字符串切片） |
| A2 | `PDF.convert_img_to_pdf` 永远返回 None | **已修** | `pdf/__init__.py:27` 现在有 `return files_path`，且支持列表 / 逗号 / `.pdf,` 三种输入 |
| A10 | 浏览器扩展 WS 空闲即断 | **已修** | `browser-plugin/src/background.ts:47` 已加 `chrome.alarms.create('weakAlarm', { periodInMinutes: 1 })` + `onAlarm` 保活 |

> A10 的修法正是我们当初建议的方向，只是周期用了 1 分钟（我们建议 ≤20 秒）。如果现场仍遇到「Chrome 闲置后第一次调用失败」，可以就"周期偏长"单独开一条。

### ❌ 仍然存在（下面这些才是可以报的）

| 编号 | 缺陷 | 位置 | 现状 |
| --- | --- | --- | --- |
| A3 | Word「覆盖已存在文件」不生效 | `word/core.py:83` | `# os.remove(file_path)` **仍被注释**，OVERWRITE 分支直接 `return file_path` |
| A4 | FTP 文件夹重命名必失败 | `network/ftp.py:308` | 文件夹分支仍传 `cur_file_name`：`FtpCore.ftp_rename(ftp_instance, cur_file_name, new_folder_name)` |
| A8 | 参数解析返回 3 个值、调用方按 2 个解包 | `executor/flow/params.py:126` | `return "".join(pieces), need_eval, need_eval` 未改，签名仍声明 `-> (Any, bool)` |
| A9 | IE 系 36 个原子全部不可用 | 整个 `astronverse.iebrowser` | 仓库内**搜不到该模块**，但字典/前端仍在 → 用户能拖出来，一跑就 `ModuleNotFoundError` |
| A11 | `Docx.insert_sep` 只加段落标记不换行 | `word/core_win.py:532` | 仍是 `s.InsertParagraph()`，光标不前进 |
| A12 | 无前端接入时 `Dialog.*` 全部崩溃 | `executor/debug/apis/ws.py:112` | `asyncio.run_coroutine_threadsafe(..., Ws.loop)` 无 `if Ws.loop is None` 保护 |
| A13 | 4 个 Dialog 原子无超时、无限阻塞 | `dialog/dialog.py:293/359/459/578` | 仍是裸 `done.wait()`；只有 `message_box` 用了 `wait_with_timeout` |
| A14 | `screen_lock` / `screen_unlock` 是空实现 | `system/system.py:153/190` | 仍 `raise NotImplementedError()`，但字典里能拖出来 |
| A15 | CV 模板匹配灰度通道用错 | `vision/cv_match.py:146/152` | 屏幕侧 `COLOR_BGR2GRAY`（L135），模板侧仍 `COLOR_RGB2GRAY` → 彩色目标匹配分被压低 |
| A16 | `convert_to_txt` 抛 `NameError` | `word/core_win.py:830` | `pydoc = Document(oldfilepath)`，而 `Document` **既没 import 也没定义**（该文件只导入了 `DocumentObject`） |
| A18 | 流式大模型不看 `delta` 有没有 `content` | `ai/api/llm.py:50` | 仍 `response_json["choices"][0]["delta"]["content"]` 硬取 |
| A6 | Excel 插入图片变成"链接到文件" | Excel `Worksheet.insert_picture` | 未展开核对，需实机验证 `xl/media` 是否为空 |

> A16 特别值得注意：上次记的是 `docx.py` 抛 `NameError`，现在**调用点挪到了 `core_win.py:830`，但 `Document` 依然没导入** —— 换了个位置继续崩，属于"修了调用链没修根"。

## 三、建议怎么报（如果要报）

不建议一条一个 issue（12 个太碎，维护者会烦）。按**模块**合并成 7 个：

| # | 建议标题 | 合并 | 优先级 |
| --- | --- | --- | --- |
| 1 | `[BUG] Dialog 系列两个致命问题：无前端接入直接崩 + 4 个原子无限阻塞` | A12 + A13 | **P0**（无人值守场景必炸） |
| 2 | `[BUG] System.screen_lock / screen_unlock 是 raise NotImplementedError 的空实现` | A14 | **P0**（拖出来点一下就炸） |
| 3 | `[BUG] Docx.convert_format(txt) 抛 NameError: Document is not defined` | A16 | **P1**（功能完全走不通） |
| 4 | `[BUG] Word 两个问题：覆盖已存在文件不生效 + insert_sep 只加标记不换行` | A3 + A11 | P1 |
| 5 | `[BUG] FTP 文件夹重命名传错变量（cur_file_name → cur_folder_name）` | A4 | P1（真·一行补丁） |
| 6 | `[BUG] vision 模板匹配灰度通道用错，彩色目标匹配分被系统性压低` | A15 | **P1**（影响面最大：CV 元素"明明在却找不到"） |
| 7 | `[BUG] 参数解析返回 3 值 / 多输出声明与实现不一致 / 字典有实现无` | A8 + A5 + A7 | P1（同一类契约问题） |
| 8 | `[BUG] 流式大模型解析硬取 delta.content，接标准 OpenAI 兼容服务必崩` | A18 | P2 |
| 9 | `[BUG] IE 系 36 个原子在发布包中缺失，但字典仍暴露` | A9 | P2（看是否还要支持 IE） |

## 四、每条上报前要做的事

1. **实机复跑一次**，把输出贴进 issue（我们 `docs/repro/A*.py` 里大多有一键脚本）
2. 版本对齐：说明是在 **v1.1.7（10-22 镜像）** 上复现的
3. 附上 main 的当前行号（本文件已给），维护者能直接跳转
4. 不要一次全发 —— 先发 P0 三条，看维护者响应节奏再决定后续

## 五、维护说明

本文件是**对外材料**，只写代码位置与复现，不写内网 IP / 账号 / 业务系统名。
内部处置优先级与影响面见 `docs/平台缺陷清单.md`。
