# [BUG] FTP 文件夹重命名把 cur_file_name 当源路径传入，文件夹模式下必然失败

- **组件**：`engine/components/astronverse-network`（`astronverse/network/ftp.py`，`Network.ftp_rename`）
- **影响版本**：`main`（2026-11-05）、v1.1.7
- **严重级别**：P1 —— 一行笔误，功能 100% 不可用

## 1. 现象

用「FTP → 重命名」，把「重命名类型」选成**文件夹**：

- 填了「原文件夹名」「新文件夹名」，执行报错
- FTP 服务端返回 `501` / `550`（取决于服务端），平台包装成「FTP文件夹重命名失败」
- 改成重命名**文件**则一切正常

## 2. 位置

`ftp.py`，`ftp_rename` 的 FOLDER 分支，L310：

```python
        elif file_type == FileType.FOLDER:
            if cur_folder_name not in exist_file:
                raise BaseException(FOLDER_EXIST_FORMAT.format(cur_folder_name), "待重命名文件夹不存在")

            if not FtpCore.is_dir(ftp_instance, cur_folder_name):
                raise BaseException(FILE_NAME_FORMAT.format(cur_folder_name), "待重命名内容非文件夹，请检查输入信息")

            if new_folder_name in exist_file:
                if exist_type == FileExistenceType.CANCEL:
                    return FtpCore.get_path(ftp_instance, cur_folder_name)
                elif exist_type == FileExistenceType.OVERWRITE:
                    FtpCore.ftp_delete_dir(ftp_instance, new_folder_name)
                elif exist_type == FileExistenceType.RENAME:
                    new_folder_name = FtpCore.generate_name(ftp_instance, new_folder_name)
                else:
                    raise NotImplementedError()
            try:
                FtpCore.ftp_rename(ftp_instance, cur_file_name, new_folder_name)   # ← L310 应为 cur_folder_name
                return FtpCore.get_path(ftp_instance, new_folder_name)
            except Exception as e:
                raise BaseException(FTP_RENAME_FORMAT.format(e), "FTP文件夹重命名失败")
```

上面 6 行判断用的都是 `cur_folder_name`，**唯独真正执行重命名时传的是 `cur_file_name`**。

## 3. 为什么必错

函数签名里两个参数是并列的：

```python
def ftp_rename(
    ftp_instance: ftplib.FTP,
    file_type: FileType = FileType.FILE,
    cur_file_name: str = "",        # 文件模式用
    new_file_name: str = "",
    cur_folder_name: str = "",      # 文件夹模式用
    new_folder_name: str = "",
    exist_type: FileExistenceType = FileExistenceType.RENAME,
):
```

选「文件夹」时，前端只会填 `cur_folder_name` / `new_folder_name`，`cur_file_name` 保持默认空串。于是实际发出的命令是：

```
RNFR ""          ← 空路径
RNTO "新名字"
```

任何标准 FTP 服务端都会拒绝空路径（vsftpd 返回 `501 RNFR requires a path`，FileZilla Server 返回 `550`）。

更麻烦的是「待重命名文件夹不存在」的校验（`cur_folder_name not in exist_file`）能过，说明用户填的名字是对的 —— 报错信息和真实原因完全对不上，用户会以为是自己路径写错了。

## 4. 修复

一行：

```diff
-                FtpCore.ftp_rename(ftp_instance, cur_file_name, new_folder_name)
+                FtpCore.ftp_rename(ftp_instance, cur_folder_name, new_folder_name)
```

## 5. 复现方式

无需真实业务数据：

1. 本地起 `pyftpdlib`：
   ```python
   from pyftpdlib.authorizers import DummyAuthorizer
   from pyftpdlib.handlers import FTPHandler
   from pyftpdlib.servers import FTPServer
   a = DummyAuthorizer(); a.add_user("u", "p", "D:/ftproot", perm="elradfmw")
   h = FTPHandler; h.authorizer = a
   FTPServer(("127.0.0.1", 2121), h).serve_forever()
   ```
2. 在 `D:/ftproot` 下建目录 `old_folder`
3. 流程：`FTP.create` → `FTP.rename(file_type=文件夹, cur_folder_name=old_folder, new_folder_name=new_folder)`
4. 观察：报「FTP文件夹重命名失败」，服务端日志里能看到 `RNFR ""`

## 6. 建议的验收

- 文件夹重命名成功，且 `old_folder` 消失、`new_folder` 出现
- 顺带核对 `FtpCore.ftp_rename` 的实现不要对「文件/文件夹」做扩展名处理（当前 `file_ext = os.path.splitext(cur_file_name)[1]` 只在 FILE 分支，没问题）
