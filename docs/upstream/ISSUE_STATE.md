# 上游 issue 跟踪快照

> 自动生成，勿手改。生成时间：**2026-11-05 16:09**（UTC+8）
> 重新生成：`python docs/upstream/track_issues.py`

| Issue | 状态 | 评论 | label | 最后更新 | 标题 |
| --- | --- | --- | --- | --- | --- |
| [#909](https://github.com/iflytek/astron-rpa/issues/909) | open | 1 | — | 2026-11-05 | [BUG] 日志脱敏正则递归导致「添加应用」接口整包 999999（paramJson 超长） |
| [#910](https://github.com/iflytek/astron-rpa/issues/910) | open | 0 | — | 2026-11-05 | [BUG] vision 模板匹配两侧灰度转换常量不一致，彩色目标匹配分被系统性压低 |
| [#911](https://github.com/iflytek/astron-rpa/issues/911) | open | 0 | — | 2026-11-05 | [BUG] Dialog 4 个原子无超时且无前端时静默永久挂起（message_box 有超时，其余 4 个没有） |
| [#912](https://github.com/iflytek/astron-rpa/issues/912) | open | 0 | — | 2026-11-05 | [BUG] System.screen_lock / screen_unlock 是 raise NotImplem |
| [#913](https://github.com/iflytek/astron-rpa/issues/913) | open | 0 | — | 2026-11-05 | [BUG] Docx.convert_to_txt 必抛 NameError: name 'Document' is |
| [#914](https://github.com/iflytek/astron-rpa/issues/914) | open | 0 | — | 2026-11-05 | [BUG] Word「覆盖已存在文件」的 os.remove 被注释掉，OVERWRITE 实际不删除旧文件 |
| [#915](https://github.com/iflytek/astron-rpa/issues/915) | open | 0 | — | 2026-11-05 | [BUG] FTP 文件夹重命名把 cur_file_name 当源路径传入，文件夹模式下必然失败 |
| [#916](https://github.com/iflytek/astron-rpa/issues/916) | open | 0 | — | 2026-11-05 | [BUG] 原子声明与实现不一致三处：多输出只 return 一个值 / 字典有 key 实现无方法 / 参数求值  |
| [#917](https://github.com/iflytek/astron-rpa/issues/917) | open | 0 | — | 2026-11-05 | [BUG] chat_streamable 硬取 delta["content"]，接标准 OpenAI 兼容流式接 |
| [#918](https://github.com/iflytek/astron-rpa/issues/918) | open | 0 | — | 2026-11-05 | [BUG] 字典里有 36 个 IE 系原子，发布包里没有 astronverse.iebrowser 模块 |

**合计**：10 个 · open **10** · closed **0**

## 本次变化

（无变化）

> ⚠️ **issue 被关闭不等于已修复**。必须跑 `python docs/upstream/verify_main.py`，
> 结论从 `CONFIRMED` 翻成 `FIXED` 才算真的修好；然后把 `平台缺陷清单.md` §0.5
> 与 §1 对应行的 📤 改成 ✅。
