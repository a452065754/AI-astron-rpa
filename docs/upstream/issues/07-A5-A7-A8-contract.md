# [BUG] 原子声明与实现不一致三处：多输出只 return 一个值 / 字典有 key 实现无方法 / 参数求值 return 3 值

这三处是同一类问题——**原子对外声明的契约和 Python 实现不一致**——所以放在一个 issue 里。三处都在 `main` 上仍存在。

---

## 一、`DataTable.read_data` / `copy_data` 声明 4 个输出，实现只 return 1 个值

### 位置

`engine/components/astronverse-datatable/src/astronverse/datatable/datatable.py`

```python
@atomicMg.atomic(
    "DataTable",
    ...
    outputList=[
        atomicMg.param("cell_info", types="List"),
        atomicMg.param("row_info", types="List"),
        atomicMg.param("column_info", types="List"),
        atomicMg.param("area_info", types="List"),
    ],
)
def read_data(...):
    if read_type == ReadType.CELL:
        ...
        return value            # 1 个值
    if read_type == ReadType.ROW:
        ...
        return row_value        # 1 个值
    ...
```

`copy_data` 同样：声明 `copied_cell / copied_row / copied_column / copied_area` 4 个输出（字典里已确认，见附录），实现 `return _clipboard` 只有 1 个值。

### 为什么危险

引擎代码生成器按 `outputList` 的数量生成解包语句：

```python
cell_info, row_info, column_info, area_info = DataTable.read_data(...)
```

Python 侧的表现取决于"1 个值"恰好是什么：

| `read_data` 实际返回 | 解包结果 |
| --- | --- |
| `"ABCD"`（4 个字符的字符串） | **不报错**，4 个变量各拿到 1 个字符 —— 静默错位，最难查 |
| `"ABC"` | `ValueError: not enough values to unpack (expected 4, got 3)` |
| `["a","b","c","d"]` | 不报错，但 `cell_info="a"`，语意全错 |
| `123`（int） | `TypeError: cannot unpack non-iterable int object` |

也就是说：**同样的原子，选「单元格」和选「行」时，有时报错、有时静默给垃圾值**。

### 修复建议

二选一：

**A. 让实现匹配声明**（推荐，声明不变，对已有流程无破坏）

```python
def read_data(...):
    if read_type == ReadType.CELL:
        value = ...
        return value, None, None, None
    if read_type == ReadType.ROW:
        return None, row_value, None, None
    if read_type == ReadType.COLUMN:
        return None, None, col_value, None
    if read_type == ReadType.AREA:
        return None, None, None, area_value
```

**B. 让声明匹配实现**（按 `read_type` 动态隐藏输出）

`outputList` 数量是静态的，做不到按参数动态变。所以只能走 A，或把 4 个原子拆成 `read_cell` / `read_row` / `read_column` / `read_area`。

`copy_data` 同理（它内部调 `read_data`，一起改）。

---

## 二、字典里有 3 个 key，实现类里没有对应方法

### 现状

| 字典 key | 设计器显示 | 实现类实际方法 |
| --- | --- | --- |
| `DataTable.sort_table` | 「数据表格排序」 | **无**（`DataTable` 类里没有 `sort_table`） |
| `Docx.open_document` | 有（输出 `doc_obj`） | 实际叫 `open_docx` |
| `Docx.read_document_content` | 有（输出 `doc_data`） | 实际叫 `read_docx` |

字典侧证据（从生产库 `c_atom_meta_new` 导出）：

```json
{
  "DataTable.sort_table":         { "title": "数据表格排序", "outputs": [] },
  "Docx.open_document":           { "title": null, "outputs": ["doc_obj"] },
  "Docx.read_document_content":   { "title": null, "outputs": ["doc_data"] }
}
```

`main` 上核对：

```
$ grep -n "def sort_table\b" datatable.py            # 无输出
$ grep -n "def open_document\b" docx.py              # 无输出
$ grep -n "def read_document_content\b" docx.py      # 无输出
```

### 影响

用户/前端能正常拖出这三个原子、能保存流程，**执行时**才 `AttributeError: type object 'Docx' has no attribute 'open_document'`。如果字典里 `title` 还是 `null`，前端显示的名字和实现里的名字完全对不上，排障成本更高。

### 修复建议

- 字典侧删除这 3 个 key（如果确认是废弃命名），或
- 实现侧补别名方法：

```python
class Docx:
    open_docx = ...            # 现有实现

    @staticmethod
    def open_document(*args, **kwargs):
        """兼容字典里的历史 key"""
        return Docx.open_docx(*args, **kwargs)
```

---

## 三、`_param_to_eval` 在有变量的分支上 return 3 个值，调用方按 2 个解包

### 位置

`engine/servers/astronverse-executor/src/astronverse/executor/flow/params.py`

```python
# L89
def _param_to_eval(self, ls: list, gv: dict = None) -> (Any, bool):
    ...
    if len(pieces) == 1:
        return pieces[0], need_eval
    if need_eval:
        return "+".join(f"str({p})" for p in pieces), need_eval
    else:
        return "".join(pieces), need_eval, need_eval     # ← L126，3 个值
```

签名声明 `-> (Any, bool)`（2 个），三个 `return` 里**只有最后一个**多返回一个。

### 触发条件与后果

触发条件是「参数被写成 **3 段以上**的混合文本，且不含变量」（`need_eval == False`，例如 `abc` + `123` + `xyz` 三个纯文本片段拼在一起）。

调用方按 2 个解包：

```python
a, b = self._param_to_eval(params, gv)
```

实测：

```python
>>> a, b = ("expr", True, True)
ValueError: too many values to unpack (expected 2)
```

于是这个参数**写 3 段纯文本就崩**，写 1 段、2 段、或带变量都正常 —— 属于典型的"特定输入才炸"。

### 修复建议

```diff
     if need_eval:
         return "+".join(f"str({p})" for p in pieces), need_eval
     else:
-        return "".join(pieces), need_eval, need_eval
+        return "".join(pieces), need_eval
```

第三个 `need_eval` 是复制粘贴多出来的，函数体内没有任何地方需要它。

---

## 附录：字典导出证据

`c_atom_meta_new` 中与本文相关的条目（v1.1.7 部署库导出）：

```json
{
  "DataTable.copy_data": {
    "title": "复制数据表格",
    "outputs": ["copied_cell", "copied_row", "copied_column", "copied_area"]
  },
  "DataTable.read_data": {
    "title": "读取数据表格",
    "outputs": ["cell_info", "row_info", "column_info", "area_info"]
  },
  "DataTable.sort_table":         { "title": "数据表格排序", "outputs": [] },
  "Docx.open_document":           { "title": null, "outputs": ["doc_obj"] },
  "Docx.read_document_content":   { "title": null, "outputs": ["doc_data"] }
}
```

## 建议的验收

- 任一 `read_type` 下 `read_data` 都返回 4 元组，未选中的为 `None`
- 字典里出现的每个 `atom_key`，实现类里都有同名可调用对象（可加一条启动自检）
- `_param_to_eval` 所有 `return` 的分支值个数与签名一致（可加 `mypy`/`pyright` 检查）
