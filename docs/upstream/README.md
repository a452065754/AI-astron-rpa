# 给上游的缺陷反馈素材（`iflytek/astron-rpa`）

> ✅ **已于 2026-11-05 15:45 提交**：https://github.com/iflytek/astron-rpa/issues/909（`bug` 标签因外部账号无 triage 权限未自动打上）
> 复现脚本已贴在该 Issue 的第一条评论里。

本目录**不是内部文档**，是准备直接贴到上游 GitHub Issue 的干净素材。
凡是内部主机名、账号、业务系统名、凭据，在这里一律不出现。

| 文件 | 用途 |
| --- | --- |
| `ISSUE-A20-log-sanitizer-stackoverflow.md` | Issue 正文，从「标题」往下整段复制即可 |
| `A20Repro.java` | 可附在 Issue 里的最小复现，零依赖，`javac A20Repro.java && java A20Repro` |

## 怎么发

1. 打开 <https://github.com/iflytek/astron-rpa/issues/new>
2. 标题：`[BUG] SensitiveDataSanitizer 值正则递归导致 robotExe/list 整包 999999`
3. 正文：整个 `ISSUE-A20-*.md`（去掉第一行的 `# [BUG] ...` 也可以，GitHub 会用它当标题）
4. 附件：把 `A20Repro.java` 拖进评论区（Issue 不支持 .java 附件时，贴代码块）

## 要不要拆成多个 Issue

正文 §5 列了 4 个独立缺陷。拆与不拆：

| 方案 | 适用 |
| --- | --- |
| **不拆，发 1 个**（推荐） | 4 个缺陷同源、同一段调用链，拆开发维护者要来回对照。正文里已经分表列出，修完能逐条勾 |
| 拆成 2 个 | 主 Issue 留缺陷 1+2+3（脱敏与日志切面），单独发一个「`getExecutorList` 无单条容错」——后者是独立设计问题，不依赖脱敏 |

## 发之前必查

```bash
grep -nE '192\.168\.|10\.[0-9]+\.|\.183|\.130|\.134|password|token|api[_-]?key' \
  docs/upstream/ISSUE-A20-log-sanitizer-stackoverflow.md docs/upstream/A20Repro.java
```

应无输出。当前版本已核对：只有 `<REDACTED>`、`AAAA` 这类占位值。

## 结论的有效性边界

- 正则原文来自 **v1.1.7 / main 官方源码**（`backend/robot-service/.../SensitiveDataSanitizer.java`），不是反编译还原
- 阈值：生产 JDK 1.8.0_504 实测 **1673 字符必爆**；本机 JDK 17 复核 **2000 起爆**（5 次一致）
- `QUOTED_ARRAY_VALUE` 在所有长度下都不爆 → 正文据此排除了它，这条是「反证」，比「正证」更有说服力
