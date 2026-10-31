# 平台缺陷复现包（Astron RPA v1.1.11）

给平台方的**可执行**复现包：11 条缺陷，每条一个独立脚本，跑完自动给出
`REPRODUCED / NOT_REPRODUCED / BLOCKED` 结论，不需要人读日志判断。

所有脚本都是**断言式**的——它们不打印一堆输出让人猜，而是直接对比「期望行为」和
「实际行为」，最后一行写结论。凡是需要对照的，脚本里都带了**对照组**
（同一个函数换一个参数、或把疑似有问题的调用换成正确写法），避免把「用法不对」
误报成「平台有缺陷」。

---

## 0. 环境

| 项 | 值 |
| --- | --- |
| 客户端 | Astron RPA v1.1.11（Windows） |
| 引擎 | `%APPDATA%/astron-rpa/python_core/`，`astronverse.*` |
| Python | 3.13.7（客户端自带，**必须用它跑**，系统 Python 里没有 `astronverse`） |
| 其它 | Word / WPS、Excel 各需可用（A3、A6、A11）；FTP 可选（A4） |

## 1. 怎么跑

```bat
:: 一条命令跑完全部 11 条
"%APPDATA%/astron-rpa/python_core/python.exe" run_all.py

:: 只跑某几条
"%APPDATA%/astron-rpa/python_core/python.exe" run_all.py --only A1,A5,A8

:: 让 A4 顺带真跑一次 FTP（不给参数时只做源码级判定）
"%APPDATA%/astron-rpa/python_core/python.exe" run_all.py --ftp 192.168.0.27:21 --user anonymous --password anonymous@
```

也可以单跑某一条，直接看完整结论块：

```bat
"%APPDATA%/astron-rpa/python_core/python.exe" A5_multi_output_mismatch.py
```

退出码：`0` = 结论明确（缺陷成立或已修复）；`1` = 有条目 `BLOCKED`（本机缺前置条件）。

## 2. 结论一览（本机实测）

| # | 缺陷 | 现象一句话 | 严重度 | 边界（脚本里已验证） |
| --- | --- | --- | --- | --- |
| A1 | `StringProcess.fill_string_to_length` 右补齐崩 | `total_length` 声明为 Str，右补齐却拿它当切片下标 → `TypeError` | P1 | 左补齐正常；传 int 就正常，根因确定是类型 |
| A2 | `PDF.convert_img_to_pdf` 必失败 | `_handle_files_input()` 全函数没有 `return`，永远返回 `None` | P0 | 3 种入参形态全失败；附带：无引号 `[a,b]` 会 `SyntaxError` |
| A3 | 同流程重复生成同名文档报「文件损坏」 | `handle_existence` 的 `os.remove` 被注释掉 + 错误文案误导 | P1 | **文件存在本身不失败**（组1 成功），只有文件被同实例占用才失败 |
| A4 | `ftp_rename` 文件夹分支传错变量 | 传 `cur_file_name`（空串）→ 服务端 `501 Invalid number of parameters` | P1 | 真跑 FTP 复现到 501；同函数文件分支写法是对的 |
| A5 | 多输出声明 ≠ 实现 | 声明 4 个输出，实现返回 1 个值，引擎解包即错位 | P0 | 3 列 → `ValueError`；**4 列 → 静默错位不报错**（最危险） |
| A6 | `Excel.insert_pic` 插入的是外链图 | `Pictures().Insert()` 只写外部路径，`xl/media` 为空 | P0 | 对照 `Shapes.AddPicture(False,True)` 产出 `xl/media/image1.png` |
| A7 | 字典有、实现无的死 key | 3 个 key 反射不到方法；同模块现行方法都能反射到 | P1 | 对照组证明不是反射写法的问题 |
| A8 | `_param_to_eval` 有一处返回 3 个值 | 调用点按 2 个解包 → `too many values to unpack` | P1 | 掺一个变量引用就正常（走另一条 return），触发条件精确 |
| A9 | IE 系原子整块缺失 | 字典/前端有 35+ 个，发布包不带 `astronverse.iebrowser` | P2 | 对照组 6 个域包都能导入，排除路径问题 |
| A10 | 扩展空闲断连、桥接不唤醒 | 闲置后所有 `Browser*` 原子报「插件无响应」（`code 1001`） | P0 | 从日志统计 103 次会话，20 次空档 > 30s；开新窗口立刻恢复 |
| A11 | `insert_sep`（段落）不分段 | `InsertParagraph()` 只插标记、光标不动，三段文字挤成一段 | P1 | 对照 `TypeParagraph()` 得到正确 3 段，证明修复建议有效 |

## 3. 每条建议怎么改

详见 [`patches.md`](patches.md)（逐条给出最小改动）。一句话版本：

| # | 改动 |
| --- | --- |
| A1 | `dataprocess/string.py:237` 的 `[0:total_length]` → `[0:total_length_int]` |
| A2 | `pdf/__init__.py` 的 `_handle_files_input` 末尾补 `return files_path` |
| A3 | `word/core.py:83` 放开 `os.remove(file_path)`；`docx.py:188` 别吞原始异常 |
| A4 | `network/ftp.py:306` 的 `cur_file_name` → `cur_folder_name` |
| A5 | `copy_data`/`read_data` 的 `outputList` 收敛成 1 个（语义上本就是同一个值） |
| A6 | `excel/core_win/worksheet.py:259` 换 `Shapes.AddPicture(path, False, True, ...)` |
| A7 | `DataTable.sort_table` 补实现或删 key；`Docx.open_document`/`read_document_content` 标 deprecated |
| A8 | `executor/flow/params.py:126` 删掉多余的第三个返回值 |
| A9 | 发布包带上 `iebrowser`，或字典标 unsupported + 前端隐藏 |
| A10 | 桥接侧调用前检查连接存活并主动唤醒；扩展改用 `alarms` 心跳而非依赖 SW 常驻 |
| A11 | `word/core_win.py:519` 的 `InsertParagraph()` → `TypeParagraph()` |

## 4. 文件清单

```
repro/
├── README.md                      本文件
├── run_all.py                     一键跑全部 + 汇总表
├── patches.md                     逐条建议补丁
├── _harness.py                    公共夹具（三种结论 + 统一输出格式）
├── _dict_evidence.json            字典侧证据（c_atom_meta_new 导出，供 A5/A7 对照）
├── A1_fill_string_to_length.py
├── A2_convert_img_to_pdf.py
├── A3_overwrite_existing.py
├── A4_ftp_rename_folder.py
├── A5_multi_output_mismatch.py
├── A6_insert_pic_linked.py
├── A7_dead_keys.py
├── A8_param_to_eval_unpack.py
├── A9_iebrowser_missing.py
├── A10_bridge_idle_disconnect.py
└── A11_insert_sep_no_break.py
```

脚本自己产生的中间文件都落在 `_tmp/<编号>/<pid>/`，可随时整个删掉。

## 5. 我们主动排除了的（不算缺陷，请勿计入）

| 项 | 真实情况 |
| --- | --- |
| `Docx.insert_hyperlink` | 生成的是 Word **HYPERLINK 域**（`fldChar` 三件套），Word 里可点、样式正确。只是没有 `w:hyperlink` 元素和 `.rels`，部分解析库读不到 |
| `BrowserSoftware.browser_open` | 用 UIA 找窗口，找不到就再开一个窗口。Chrome 在跑也可能多开 —— 属于行为约定，不是失败 |
| `DataTable.export_data_table_to_file` 导 csv | 非 JSON 类型底层走 openpyxl，`.csv` 得到的是**改了后缀的 xlsx**。属于文档缺失 |
| `System.screen_lock` | 实现就是 `raise NotImplementedError()`，属于未实现，不算 bug |
| `System.terminate_process`（按名） | 恒返回 True，返回值不可作为证据 |
| FTP 中文目录名 | IIS FTP 回 `451 No mapping for the Unicode character...`，服务器侧编码问题，不是原子缺陷 |

## 6. 复现纪律（为什么这份清单可信）

1. **每条都有对照组**：只验证「出错了」不够，还要证明「换个正确写法就好了」，才能把锅归到平台。
2. **每条的边界都写清楚**：比如 A3 如果笼统说「覆盖已存在文件必失败」就是错的，实测文件存在但无人占用时是成功的。
3. **不靠返回值自证**：能解包文件结构就解包（A6 看 `xl/media`、A11 看 `<w:p>`），能查服务端就查（A4 看 501）。
4. **结论可重跑**：`run_all.py` 的汇总表就是最终结论，任何一条翻转都会体现出来。
