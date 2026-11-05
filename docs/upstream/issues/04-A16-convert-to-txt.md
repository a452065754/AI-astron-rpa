# [BUG] Docx.convert_to_txt 必抛 NameError: name 'Document' is not defined

- **组件**：`engine/components/astronverse-word`（`astronverse/word/core_win.py`）
- **影响版本**：`main`（2026-11-05）、v1.1.7
- **严重级别**：P1 —— 该原子 100% 不可用

## 1. 现象

流程里用「Word 文档 → 转换为 TXT」：

- 直接报 `NameError: name 'Document' is not defined`
- 不会生成任何 .txt 文件
- 100% 复现，与文档内容无关

## 2. 位置

`core_win.py`，`WordDocumentCore.convert_to_txt`（L803 定义，L830 出错）：

```python
    @classmethod
    def convert_to_txt(
        cls,
        doc: object = None,
        output_path: str = "",
        output_name: str = "",
        save_type: SaveFileType = SaveFileType.WARN,
    ):
        filename = f"{output_name}.txt"
        if WordDocumentCore.check_file_in_path(output_path, filename):
            if save_type == SaveFileType.WARN:
                raise BaseException(FILENAME_ALREADY_EXISTS_ERROR.format(filename), "")
            if save_type == SaveFileType.GENERATE:
                counter = 1
                oldfilename, _ = os.path.splitext(filename)
                while WordDocumentCore.check_file_in_path(output_path, filename):
                    filename = f"{oldfilename}_{counter}.txt"
                    counter += 1
            elif save_type == SaveFileType.OVERWRITE:
                fullpath = os.path.join(output_path, filename)
                os.remove(fullpath)

        oldfilepath = doc.FullName
        pydoc = Document(oldfilepath)          # ← L830，NameError
        new_path = os.path.join(output_path, filename)
        with open(new_path, "w", encoding="utf-8") as txt_file:
            txt_file.writelines(para.text + "\n" for para in pydoc.paragraphs)
```

## 3. 为什么必错：`Document` 在这个文件里既没导入也没定义

`core_win.py` 的 import 区（L1~L40）：

```python
import psutil
import win32clipboard
import win32com.client as wc
from astronverse.actionlib.logger import logger
from astronverse.actionlib.types import PATH
from astronverse.actionlib.utils import FileExistenceType, handle_existence
from astronverse.word import (ApplicationType, CloseRangeType, ...)   # 一长串枚举
from astronverse.word.core import IDocumentCore                        # 只有这个
from astronverse.word.error import *
from win32api import RGB
```

全文搜索 `Document` 只有 3 处有效引用，没有任何一处能提供 `Document` 这个名字：

| 行 | 内容 |
| --- | --- |
| L38 | `from astronverse.word.core import IDocumentCore`（名字是 `IDocumentCore`，不是 `Document`） |
| L43 | `class WordDocumentCore(IDocumentCore)` |
| L830 | `pydoc = Document(oldfilepath)` ← 这里 |

`Document` 大概率是从 `docx` 库（python-docx）的用法残留下来的 —— `from docx import Document`，但 `astronverse-word` 走的是 **win32com**（`wc.DispatchEx("Word.Application")`），并不依赖 python-docx。

> 补充说明：这个问题此前出现在 `astronverse/word/docx.py`，现在调用点被移到了 `core_win.py`，但 `Document` 依然没有被导入 —— 属于「修了调用链，没修根」。

## 4. 影响

- 「Word 转 TXT」这个原子在 Windows 上完全不可用
- 用户的绕过方式是「另存为 .txt」再读文件，但这会丢失段落结构，且多一步人工
- 报错信息 `NameError` 属于 Python 内部错误，用户完全无法自助解决

## 5. 修复建议

`doc` 本身就是 Word COM 的 `Document` 对象，**不需要重新打开文件**，直接遍历它的段落即可：

```diff
         oldfilepath = doc.FullName
-        pydoc = Document(oldfilepath)
         new_path = os.path.join(output_path, filename)
         with open(new_path, "w", encoding="utf-8") as txt_file:
-            txt_file.writelines(para.text + "\n" for para in pydoc.paragraphs)
+            for para in doc.Paragraphs:
+                txt_file.write(para.Range.Text.rstrip("\r\a") + "\n")
```

说明：
- `doc.Paragraphs` 是 COM 集合，`para.Range.Text` 末尾带 `\r\a`（段落标记 + 单元格标记），要清掉
- 若坚持用 python-docx，则必须补 `from docx import Document` 并在 `pyproject.toml` 里声明 `python-docx` 依赖；但既然 `doc` 已经打开，再开一份属于多余开销，且文档被占用时会失败

## 6. 复现方式

1. 准备任意 .docx
2. 流程：`Word.open_docx` → `Word.convert_to_txt`
3. 运行 → `NameError: name 'Document' is not defined`

## 7. 建议的验收

- 转换成功且段落数与源文档一致
- 中文不乱码（`encoding="utf-8"` 已具备）
- 目标文件已存在时，`WARN / GENERATE / OVERWRITE` 三种策略都按预期行为
