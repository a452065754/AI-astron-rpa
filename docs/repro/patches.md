# 建议补丁（逐条最小改动）

行号基于 **Astron RPA v1.1.11 / astronverse 本机实测**，路径前缀
`%APPDATA%/astron-rpa/python_core/Lib/site-packages/astronverse/`。
每条都复合了「改动前 → 改动后」，不需要整文件替换。

---

## A1 · `dataprocess/string.py:237`

```diff
         n = math.ceil((total_length_int - len(string_data)) / len(add_str))
         if fill_type == FillStringType.LEFT:
             result_str = (add_str * n)[0 : total_length_int - len(string_data)] + string_data
         elif fill_type == FillStringType.RIGHT:
-            result_str = (string_data + add_str * n)[0:total_length]
+            result_str = (string_data + add_str * n)[0:total_length_int]
         return result_str
```

> 左补齐那条本来就用的 `total_length_int`，右补齐漏改。

---

## A2 · `pdf/__init__.py` · `_handle_files_input()`

```diff
 def _handle_files_input(files_path):
     if isinstance(files_path, str):
         if files_path.startswith("[") and files_path.endswith("]"):
             files_path = ast.literal_eval(files_path)
         else:
             files_path = files_path.split(".pdf,")
             for i in range(len(files_path)):
                 if i != len(files_path) - 1:
                     files_path[i] = files_path[i].strip() + ".pdf"
     elif not isinstance(files_path, list):
         raise BaseException(FILE_PATH_ERROR_FORMAT.format(str(files_path)), "PDF文件路径有误，请输入正确的路径")
+    return files_path
```

顺带建议（本包 A2 脚本的附带发现）：`ast.literal_eval` 对前端常见的
`[a.png,b.png]`（**路径不带引号**）会抛 `SyntaxError`。建议改成

```python
# 兼容 [a,b] 与 ['a','b'] 两种形态
body = files_path.strip()[1:-1]
try:
    files_path = ast.literal_eval(files_path)
except (ValueError, SyntaxError):
    files_path = [p.strip().strip("'\"") for p in body.split(",") if p.strip()]
```

---

## A3 · `word/core.py:79-84`（同类：`excel/core.py`、`pdf/core.py` 的 handle_existence）

```diff
     @staticmethod
     def handle_existence(file_path, exist_type):
         # 文件存在时的处理方式
         if exist_type == FileExistenceType.OVERWRITE:
             # 覆盖已存在文件，直接返回文件路径
-            # os.remove(file_path)
+            if os.path.exists(file_path):
+                os.remove(file_path)
             return file_path
```

以及 `word/docx.py:188-192` 不要吞原始异常：

```diff
         except Exception as e:
             raise BaseException(
                 DOCUMENT_READ_ERROR_FORMAT.format(file_path),
                 "打开文档失败，请检查文件路径是否正确！",
-            ) from e
+            ) from e   # 建议把 str(e) 一起拼进 message，或加个 from 让前端能看到原始错误
```

> 现状是用户看到「请检查文件是否损坏」，实际根因是「文件正被当前 Word 实例占用」。

---

## A4 · `network/ftp.py:306`

```diff
             try:
-                FtpCore.ftp_rename(ftp_instance, cur_file_name, new_folder_name)
+                FtpCore.ftp_rename(ftp_instance, cur_folder_name, new_folder_name)
                 return FtpCore.get_path(ftp_instance, new_folder_name)
             except Exception as e:
                 raise BaseException(FTP_RENAME_FORMAT.format(e), "FTP文件重命名失败")
```

> 上方文件分支（第 280 行）用的就是正确的 `cur_file_name`，这里是复制过来忘改。

---

## A5 · `datatable/datatable.py` · `copy_data` / `read_data` 的 outputList

```diff
     @atomicMg.atomic(
         "DataTable",
         ...
         outputList=[
-            atomicMg.param("copied_cell", types="List"),
-            atomicMg.param("copied_row", types="List"),
-            atomicMg.param("copied_column", types="List"),
-            atomicMg.param("copied_area", types="List"),
+            # 实现只返回一个值（当前复制/读取到的那份内容），
+            # 声明 4 个会被代码生成器拆包，得到碎片
+            atomicMg.param("copied_data", types="List"),
         ],
     )
```

`read_data` 同理（`cell_info`/`row_info`/`column_info`/`area_info` → 一个 `read_content`）。
`BrowserElement.element_operation` 的 8 个输出（`get_ele_attr`…`get_ele_style`）也是同一个问题。

> 若确实想按类型返回不同语义，就得让实现 `return (cell, row, column, area)` 四元组，
> 两条路选一条，不能「声明 4 个、返回 1 个」。

---

## A6 · `excel/core_win/worksheet.py:259`

```diff
     @staticmethod
     def insert_picture(worksheet, image_path, pic_left=0, pic_top=0, pic_height=300, pic_width=400, pic_scale=1.0):
         """
         插入图片，并设置图片大小
         """
-        picture = worksheet.Pictures().Insert(image_path)
+        # Pictures().Insert 插入的是「文件链接」，图片不随工作簿走；
+        # Shapes.AddPicture(路径, LinkToFile=False, SaveWithDocument=True) 才是嵌入
+        picture = worksheet.Shapes.AddPicture(
+            image_path, False, True, pic_left, pic_top, pic_width, pic_height
+        )
```

> 两者返回的都是 Shape，后面 `ShapeRange.Item(1)` 那段可以照旧；
> 注意 `AddPicture` 自带位置和尺寸参数，原来靠 `Left/Top/Width/Height` 设置的逻辑可以简化。

---

## A7 · 字典侧（无代码补丁）

| key | 建议 |
| --- | --- |
| `DataTable.sort_table` | 实现类里 16 个方法没有它。要么补 `sort_table`，要么从 `c_atom_meta_new` 删掉 |
| `Docx.open_document` | 已被 `open_docx` 取代，建议字典标 `deprecated` + 前端隐藏 |
| `Docx.read_document_content` | 已被 `read_docx` 取代，同上 |

> 这三个 key 在 183 条真实流程里命中 0 次 —— 人工也不会用，留着只会让 AI 生成流程时踩坑。

---

## A8 · `executor/flow/params.py:126`

```diff
         if len(pieces) == 1:
             return pieces[0], need_eval
         if need_eval:
             return "+".join(f"str({p})" for p in pieces), need_eval
         else:
-            return "".join(pieces), need_eval, need_eval
+            return "".join(pieces), need_eval
```

顺带把第 89 行的注解修一下：

```diff
-    def _param_to_eval(self, ls: list, gv: dict = None) -> (Any, bool):
+    def _param_to_eval(self, ls: list, gv: dict = None) -> tuple[Any, bool]:
```

---

## A9 · 发布打包 / 字典

二选一：

1. 发布包里带上 `astronverse/iebrowser/`（字典与前端已经在暴露这 35+ 个原子）；
2. 或者字典里给 `IeBrowser*` 打 `unsupported` 标记，前端隐藏、AI 生成时拦截。

现状是「能拖出来、编译能过、运行报没模块」，属于最差的组合。

---

## A10 · 桥接与扩展

**桥接侧**（`astronverse/browser_bridge`）：调用进来时先检查该浏览器的扩展连接是否存活，
不存活就主动唤起扩展（如向扩展注入一次轻量请求），而不是直接返回 `code 1001 send uuid empty`。

**扩展侧**（MV3）：不要指望 service worker 常驻。用 `chrome.alarms`（最小间隔 30s）定时
重建 WS，或在 `onStartup/onInstalled` 之外加断线重连退避（实测当前断开后 60 秒内不会自己连回来）。

---

## A11 · `word/core_win.py:519`

```diff
         if sep_type == InsertionType.PAGE:
             s.InsertNewPage()
         elif sep_type == InsertionType.PARAGRAPH:
-            s.InsertParagraph()
+            # InsertParagraph 只往光标处插段落标记、光标不前进，
+            # 后续 insert_docx 的文字会继续写进同一段；TypeParagraph 等价于按 Enter
+            s.TypeParagraph()
```

> 本包对照组已验证：换成 `TypeParagraph()` 后，三段文字正确落到三个段落。

---

## A12 · `executor/debug/apis/ws.py:71` / `:112`

```diff
  class Ws:
      loop = None

      def __init__(self, svc):
          self.svc = svc

-         atomicMg.cfg()["WS"] = self
+         # 只有当 WS 服务真的起来（loop 已就绪）时，才对外暴露，
+         # 否则 dialog.py 会判 `if ws:` 为真 → 进入 send_reply → run_coroutine_threadsafe(coro, None) → AttributeError
+         if Ws.loop is not None:
+             atomicMg.cfg()["WS"] = self
```

同时给 `send_reply` 加一道保护，避免任何路径再漏出 `NoneType` 这种没法看的错：

```diff
  @staticmethod
  def send_reply(msg, timeout, callback_func=None):
      """发送消息并等待回复"""
+     if Ws.loop is None:
+         raise BaseException("当前环境未连接设计器，无法显示弹窗", "WS 未就绪")
      ...
```

> 二选一即可（改 `__init__` 或加保护）。若同时做，效果更稳。
> 另一条更彻底的路线：把 `Ws.loop = loop`（`ws.py:226`）从「起 WS 服务」里挪到
> `Ws.__init__`，起一个后台 loop，保证 `cfg["WS"]` 有值时 loop 一定可用。

---

## A13 · `dialog/dialog.py:267` / `:329` / `:420` / `:539`

四处 `done.wait()` 都没有超时参数，而同文件的 `message_box`（`:174`）和
`custom_box`（`:621`）用的是 `wait_with_timeout`。统一改成后者：

```diff
-        done.wait()
+        wait_with_timeout(
+            lambda: done.is_set(),
+            reset_timeout_on_activity=auto_check,
+            wait_time=wait_time,
+        )
```

同时给这四个原子补一个 `wait_time` 参数（默认 60），并在字典 `inputList` 里声明，
否则调用方没法控制等待时长。

> 本包对照组已验证：`message_box(wait_time=3)` 3.1 秒正常返回；
> 而这四个原子在 6 秒后线程仍然存活。

---

## A14 · `system/system.py:152` / `:190`

短期（二选一）：

```diff
-    def screen_lock():
-        raise NotImplementedError()
+    def screen_lock():
+        raise BaseException("当前环境暂不支持锁屏（需 Windows 桌面会话 + 管理员权限）", "暂不支持")
```

或者干脆在生成原子字典时过滤掉这类「函数体只有 `raise NotImplementedError`」的原子，
让设计器直接灰掉并提示「暂不支持」——**别让用户拖进流程才炸**。

中期实现（锁屏）：

```python
import ctypes
ctypes.windll.user32.LockWorkStation()
```

解锁需要校验域账号口令，签名里已经预留了 `pwd_type`（`PwdType.PASSWORD` / `PwdType.RSA`）
两套参数，照着补实现即可。

> 本包已全引擎扫描：`raise NotImplementedError` 占位原子就 `screen_lock` / `screen_unlock` 这 2 个。

---

## A15 · `vision/cv_match.py:146` / `:152` / `:33`

模板和锚点在 `:110` / `:113` 已经从 RGB 转成了 BGR，后面再按 **RGB** 权重转灰度，
等于把 R 和 B 的权重反了：

```diff
-        small_gray = cv2.cvtColor(element, cv2.COLOR_RGB2GRAY)
+        small_gray = cv2.cvtColor(element, cv2.COLOR_BGR2GRAY)
```

`:152` 的 `anchor_gray` 同理；`:33` 的 `check_if_multiple_elements`（判定元素是否唯一）
也拿的是 BGR 语义的 `element`，一并改。

顺带把 `process_image` 的 `ratio` 默认值从 `None` 改成 `"1,1"`：

```diff
-        ratio=None,
+        ratio="1,1",
```

> 不传 `ratio` 时 `ratio != ""` 成立 → `None.split(",")` → `AttributeError`。
> 两个内部调用方（`vision/core.py:58`、`vision_picker/core/core.py:160`）都显式传了
> `ratio='w,h'`，所以线上没暴露，但默认值本身是坏的。
>
> 本包对照实验已验证：同一块彩色像素，引擎写法匹配分 **0.9395**（低于默认阈值 0.95
> → 判「元素不存在」），正确写法 **1.0000**；同样的目标改成灰色后引擎能正常匹配。
