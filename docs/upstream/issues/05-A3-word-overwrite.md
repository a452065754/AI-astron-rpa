# [BUG] Word「覆盖已存在文件」的 os.remove 被注释掉，OVERWRITE 实际不删除旧文件

- **组件**：`engine/components/astronverse-word`（`astronverse/word/core.py`，`IDocumentCore.handle_existence`）
- **影响版本**：`main`（2026-11-05）、v1.1.7
- **严重级别**：P1 —— 「覆盖」策略退化成「直接返回原路径」，同名文件重复生成时会失败

## 1. 现象

流程里设为「文件已存在时 → 覆盖」：

- 第二次执行同一个流程、输出同名 .docx 时，出现「文档保存失败 / 文件已损坏 / 无法访问」
- 手动删掉旧文件后再跑，就正常
- 若上一轮 Word 进程没释放句柄（`close_flag=False` 时很常见），失败率 100%

## 2. 位置

`astronverse/word/core.py`，`IDocumentCore.handle_existence`：

```python
# L78~84
@staticmethod
def handle_existence(file_path, exist_type):
    # 文件存在时的处理方式
    if exist_type == FileExistenceType.OVERWRITE:
        # 覆盖已存在文件，直接返回文件路径
        # os.remove(file_path)          # ← 被注释
        return file_path
```

注释还写着「覆盖已存在文件」，但真正执行删除的那一行被注释掉了。于是 OVERWRITE 和「什么都不做」等价：**只是不生成新文件名，旧文件原封不动留在原地**。

调用点：`core_win.py:208`

```python
tentative_path = os.path.join(file_path, file_name)
new_file_path = IDocumentCore.handle_existence(tentative_path, exist_handle_type)
if new_file_path:
    try:
        doc.SaveAs(FileName=new_file_path)     # 目标文件仍存在
    except Exception as e:
        raise RuntimeError(f"文档保存失败: {e}")
```

`SaveAs` 直接怼上一个已存在的文件时，是否成功取决于 Word/WPS 的 `DisplayAlerts` 与文件占用状态 —— **这是不确定行为**，取决于用户装的是 Word 还是 WPS、文件有没有被别的进程打开。

## 3. 同类问题

同一份 `handle_existence` 在 `astronverse.actionlib.utils` 里也有一份（Excel/PDF 等模块用），行为需要一并核对：

```python
from astronverse.actionlib.utils import FileExistenceType, handle_existence
```

`core_win.py:65` 用的就是这一份。如果那边同样是「注释掉 os.remove」，则影响范围覆盖 Word/Excel/PDF 三个模块的「覆盖」策略。

## 4. 修复建议

```diff
     if exist_type == FileExistenceType.OVERWRITE:
-        # 覆盖已存在文件，直接返回文件路径
-        # os.remove(file_path)
+        # 覆盖已存在文件：先删旧文件，避免 SaveAs 撞上占用/权限问题
+        try:
+            if os.path.exists(file_path):
+                os.remove(file_path)
+        except OSError as e:
+            raise BaseException(f"无法覆盖已存在文件 {file_path}: {e}", "")
         return file_path
```

若担心误删，更稳妥的做法是交给 Word 的 `SaveAs` 显式覆盖：

```python
doc.DisplayAlerts = 0          # wdAlertsNone
doc.SaveAs(FileName=new_file_path)
```

但 `DisplayAlerts = 0` 只抑制弹窗，"文件被占用"依然会失败，所以**删除 + 失败时给明确报错**更可靠。

## 5. 复现方式

1. 流程：`Word.create`（输出 `D:/tmp/a.docx`，已存在时选「覆盖」）→ 保存
2. 连续跑两次
3. 第二次报 `文档保存失败: ...`

对照实验：第二次跑之前手动 `del a.docx`，必然成功。

## 6. 建议的验收

- OVERWRITE 时旧文件被真正替换（修改时间变化、内容为新内容）
- 文件被占用时应给出「文件被其他程序占用」之类的可读报错，而不是底层 COM 异常
