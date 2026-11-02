# nl2spec 对照基准（2026-11-02）

> 模型 `xopqwen36v35b` · 自愈上限 3 轮 · 原子字典 309 可用
> 题目：14 条**已人工评审通过**的流程，把需求改写成用户话术，让模型从零生成。
> `原子重合` = 生成出的原子覆盖标准答案原子的比例（越高越接近人的做法）。

| # | 需求 | 结果 | 轮数 | 节点（生成/标准） | 原子重合 | 耗时 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 把 Word 文档里的文字全部提取出来存成 txt | ✅ 通过 | 1 | 3 / 9 | 22% | 3.2s |

## 汇总

- 通过 **1/1**
- 收敛轮数：1 轮 1 条
- 原子重合度：均值 **22%**（最低 22%，最高 22%）

## 逐条差异（生成缺了什么 / 多做了什么）

### 把 Word 文档里的文字全部提取出来存成 txt

- 标准答案（9 节点）：`File.get_file_list → Code.ForList → Code.If → Docx.open_docx → Docx.read_docx → File.file_write → Docx.close_docx → Code.IfEnd → Code.ForEnd`
- 生成结果（3 节点）：`Docx.open_docx → Docx.convert_format → Docx.close_docx`
- **少了**：Code.ForEnd、Code.ForList、Code.If、Code.IfEnd、Docx.read_docx、File.file_write、File.get_file_list
- **多了**：Docx.convert_format

## 怎么读这份表

- **重合度不是分数**：人会做最优选择（例如「按列拆表」用 Python 模块一步搞定），
  模型用纯原子拼也能达成同样效果——所以低重合不等于错。要判对错必须真跑 + 核对产出。
- 真正要盯的是**失败项**和**轮数**：轮数多说明提示词没讲清楚，失败说明能力边界到了。
