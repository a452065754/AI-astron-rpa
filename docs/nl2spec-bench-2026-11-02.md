# nl2spec 对照基准（2026-11-02）

> 模型 `xopqwen36v35b` · 自愈上限 3 轮 · 原子字典 309 可用
> 题目：14 条**已人工评审通过**的流程，把需求改写成用户话术，让模型从零生成。
> `原子重合` = 生成出的原子覆盖标准答案原子的比例（越高越接近人的做法）。

| # | 需求 | 结果 | 轮数 | 节点（生成/标准） | 原子重合 | 耗时 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 把 F:\demo\a.pdf 和 F:\demo\b.pdf 合并 | ✅ 通过 | 1 | 1 / 1 | 100% | 2.7s |
| 2 | 把 F:\demo\报表.docx 转成 PDF | ✅ 通过 | 2 | 3 / 3 | 100% | 6.5s |
| 3 | 把 F:\demo\in 文件夹里的文件批量重命名成 改名_序号 | ✅ 通过 | 2 | 5 / 6 | 67% | 7.0s |
| 4 | 把 Excel 表格按某一列的值拆成多个文件，每个值一个文件 | ❌ 失败 | 4 | - / 1 | - | 20.6s |
| 5 | 把 F:\demo\in 文件夹压缩成 zip，再解压到 F:\de | ❌ 失败 | 4 | - / 3 | - | 13.7s |
| 6 | 把 F:\demo\in 里的多个 xlsx 合并成一张表 | ✅ 通过 | 2 | 11 / 1 | 0% | 39.6s |
| 7 | 把 Excel 表格按某一列去重，保留第一次出现的行 | ❌ 失败 | 4 | - / 1 | - | 26.2s |
| 8 | 把 Word 文档里的文字全部提取出来存成 txt | ✅ 通过 | 1 | 4 / 9 | 44% | 3.6s |
| 9 | 把 PDF 的第 1 到 2 页抽出来另存成一个新 PDF | ✅ 通过 | 1 | 1 / 1 | 100% | 3.0s |
| 10 | 统计 Excel 表格有多少行多少列 | ✅ 通过 | 2 | 5 / 7 | 83% | 7.6s |
| 11 | 把 Excel 表格里的旧值查找替换成新值 | ✅ 通过 | 1 | 4 / 3 | 100% | 5.2s |
| 12 | 把多个 Word 文档里的旧公司名批量替换成新公司名 | ✅ 通过 | 2 | 6 / 8 | 62% | 9.8s |
| 13 | 把 PDF 里的表格提取出来转成 Excel | ✅ 通过 | 1 | 4 / 1 | 100% | 3.8s |
| 14 | 把文件夹里的文件按扩展名分到不同子文件夹 | ✅ 通过 | 3 | 8 / 7 | 86% | 10.4s |

## 汇总

- 通过 **11/14**
- 收敛轮数：1 轮 5 条、2 轮 5 条、3 轮 1 条
- 原子重合度：均值 **77%**（最低 0%，最高 100%）

## 逐条差异（生成缺了什么 / 多做了什么）

### 把 F:\demo\a.pdf 和 F:\demo\b.pdf 合并成一个 PDF，放到 F:\demo\out

- 标准答案（1 节点）：`PDF.merge_pdf_files`
- 生成结果（1 节点）：`PDF.merge_pdf_files`

### 把 F:\demo\报表.docx 转成 PDF

- 标准答案（3 节点）：`Docx.open_docx → Docx.convert_format → Docx.close_docx`
- 生成结果（3 节点）：`Docx.open_docx → Docx.convert_format → Docx.close_docx`

### 把 F:\demo\in 文件夹里的文件批量重命名成 改名_序号

- 标准答案（6 节点）：`File.get_file_list → Code.ForList → Code.If → File.file_rename → Code.IfEnd → Code.ForEnd`
- 生成结果（5 节点）：`File.get_file_list → Code.ForList → StringProcess.concatenate_string → File.file_rename → Code.ForEnd`
- **少了**：Code.If、Code.IfEnd
- **多了**：StringProcess.concatenate_string

### 把 F:\demo\in 里的多个 xlsx 合并成一张表

- 标准答案（1 节点）：`Script.module`
- 生成结果（11 节点）：`File.file_search → Excel.create_excel → Code.ForList → Excel.open_excel → Excel.get_excel_worksheet_names → Code.ForList → Excel.copy_excel_worksheet → Code.ForEnd → Excel.close_excel → Code.ForEnd → Excel.save_excel`
- **少了**：Script.module
- **多了**：Code.ForEnd、Code.ForList、Excel.close_excel、Excel.copy_excel_worksheet、Excel.create_excel、Excel.get_excel_worksheet_names、Excel.open_excel、Excel.save_excel、File.file_search

### 把 Word 文档里的文字全部提取出来存成 txt

- 标准答案（9 节点）：`File.get_file_list → Code.ForList → Code.If → Docx.open_docx → Docx.read_docx → File.file_write → Docx.close_docx → Code.IfEnd → Code.ForEnd`
- 生成结果（4 节点）：`Docx.open_docx → Docx.read_docx → File.file_write → Docx.close_docx`
- **少了**：Code.ForEnd、Code.ForList、Code.If、Code.IfEnd、File.get_file_list

### 把 PDF 的第 1 到 2 页抽出来另存成一个新 PDF

- 标准答案（1 节点）：`PDF.extract_pdf_file`
- 生成结果（1 节点）：`PDF.extract_pdf_file`

### 统计 Excel 表格有多少行多少列

- 标准答案（7 节点）：`Excel.open_excel → Excel.get_excel_worksheet_names → Excel.get_excel_row_num → Excel.get_excel_col_num → Report.print → Report.print → Excel.close_excel`
- 生成结果（5 节点）：`Excel.open_excel → Excel.get_excel_row_num → Excel.get_excel_col_num → Excel.close_excel → Report.print`
- **少了**：Excel.get_excel_worksheet_names

### 把 Excel 表格里的旧值查找替换成新值

- 标准答案（3 节点）：`Excel.open_excel → Excel.search_and_replace_excel_content → Excel.save_excel`
- 生成结果（4 节点）：`Excel.open_excel → Excel.search_and_replace_excel_content → Excel.save_excel → Excel.close_excel`
- **多了**：Excel.close_excel

### 把多个 Word 文档里的旧公司名批量替换成新公司名

- 标准答案（8 节点）：`File.get_file_list → Code.ForList → Code.If → Docx.open_docx → Docx.replace → Docx.save_docx → Code.IfEnd → Code.ForEnd`
- 生成结果（6 节点）：`File.file_search → Code.ForList → Docx.open_docx → Docx.replace → Docx.save_docx → Code.ForEnd`
- **少了**：Code.If、Code.IfEnd、File.get_file_list
- **多了**：File.file_search

### 把 PDF 里的表格提取出来转成 Excel

- 标准答案（1 节点）：`PDF.extract_forms_from_pdf`
- 生成结果（4 节点）：`PDF.extract_forms_from_pdf → Excel.open_excel → Excel.save_excel → Excel.close_excel`
- **多了**：Excel.close_excel、Excel.open_excel、Excel.save_excel

### 把文件夹里的文件按扩展名分到不同子文件夹

- 标准答案（7 节点）：`File.get_file_list → Code.ForList → File.file_info → StringProcess.extract_content_from_string → Folder.folder_create → File.file_move → Code.ForEnd`
- 生成结果（8 节点）：`File.get_file_list → Code.ForList → File.file_info → Code.If → Folder.folder_create → File.file_move → Code.IfEnd → Code.ForEnd`
- **少了**：StringProcess.extract_content_from_string
- **多了**：Code.If、Code.IfEnd

## 怎么读这份表

- **重合度不是分数**：人会做最优选择（例如「按列拆表」用 Python 模块一步搞定），
  模型用纯原子拼也能达成同样效果——所以低重合不等于错。要判对错必须真跑 + 核对产出。
- 真正要盯的是**失败项**和**轮数**：轮数多说明提示词没讲清楚，失败说明能力边界到了。
