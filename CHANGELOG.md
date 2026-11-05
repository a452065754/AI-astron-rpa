# 变更记录（CHANGELOG）

> 由 `python scripts/release.py` 自动生成，别手改格式。
> 唯一用途：让人在 30 秒内判断「我要不要更新」——所以只写「改了什么、影响谁」，实现细节看 git log。

## v1.0.3（2026-11-05）
> 新增 --demo 演示提醒文案

### 2026-11-05 · 2 次提交
- **新增** 1：
  - feat(version): 新增 --demo，直观看四种情况下的提醒文案（22ac977）
- **文档** 1：
  - docs: 交接清单与开发进度补 GitHub 分发链路（§28）+ 实测可达性结论（b5553ef）

## v1.0.2（2026-11-05）
> 修亮点取错区间与 CHANGELOG 重复段

### 2026-11-05 · 1 次提交
- **修复** 1：
  - fix(changelog): 亮点只取本版区间，header 不再每次 bump 重复一段（50001d3）

## v1.0.1（2026-11-05）
> GitHub 版分发链路：Contents API 主源 + 一键发版 + CI 一致性

### 2026-11-05 · 1 次提交
- **新增** 1：
  - feat(version): GitHub 版分发链路，raw 不可达改走 Contents API（e8b2eb4）

## v1.0.0（2026-11-05）
> 首个版本化基线。此前无版本号，全部提交都归到这里。
> 从这一天起：改完 bug 就 `--bump patch`，改了 spec 格式就 `--bump minor --breaking`。

### 2026-11-05 · 10 次提交
- **修复** 1：
  - fix(A20)：生效版本是「线上版本」，不是 app_version —— 撤销 3 个误报红灯（cc29b7b）
- **新增** 2：
  - feat(发布闸门)：默认只做草稿，publish/expose 必须 --confirm-publish（3ca5301）
  - feat(参数预算)：把 A20「参数太多→添加应用 999999」做成三道门禁（9110dd4）
- **文档** 7：
  - docs(platform): 缺陷清单加「上报」标识列 + 进度巡检脚本（6e96cb8）
  - docs(upstream): 验证其余 12 条平台缺陷并合并为 9 个 issue 上报（#910~#918）（6897d03）
  - docs(upstream): 盘点其余 15 条平台缺陷在 main 的现状（已修 3 / 仍在 12）（f00f3c4）
  - docs(upstream): A20 已提交上游 issue #909，并登记链接（f48e08f）
  - docs(upstream): A20 上游 Issue 素材 + 根因正则复核更正为 QUOTED_VALUE（e25e713）
  - docs(A20)：澄清「≤6 条」不是硬限制 —— 硬限是 8 条，主判据是长度（82cce9c）
  - docs(A20)：补齐「防护边界」——把管得住/管不住写死，不再只说「不会了」（7e23121）

### 2026-11-04 · 7 次提交
- **修复** 2：
  - fix(flow-generator): 参数形态约束固化 —— AI 生成节点必须与前台原子一致（3409fd2）
  - fix(browserscript): js_run 的 input_type 包成 rpa:special —— 否则设计器丢 content（c0504a0）
- **新增** 1：
  - feat(lint): 内嵌脚本「两份代码」漂移 —— 加检查 + 一条命令同步（d32b5d3）
- **文档** 3：
  - docs(安全)：记录 git 历史改写（filter-repo）—— 附「force push 不等于删除」的实证（b54500c）
  - docs: 记录「不迁」决策 —— 130 的 76 条落库成果转为历史存档（f0a4c03）
  - docs(todo): 183 正式服会话恢复 + 揭示「成果留在 130」这个事实（77d2241）
- **其他** 1：
  - review(安全+门禁)：清流程内明文凭据、修 V15 被元素流程打挂、去 lint 误报 9 条（a41d3b0）

### 2026-11-03 · 25 次提交
- **修复** 4：
  - fix(dist): 技能/MCP 装到客户端真正读取的目录 + 自检兜住 + 发布就绪体检（8f4e6dd）
  - fix(lint): B-1 表述被证据推翻（标量计数器是原子能力）+ D1 提示收窄（3356255）
  - fix(script): 默认值两条语义规则（sheet 名不许猜 + 脚本参数必须是字符串）（ece33e0）
  - fix(script): Script.module 落库结构定为平台标准形态（Py 原子）（09e463c）
- **新增** 6：
  - feat(mes): 首条真实业务流程收口 —— 补「不限下限」语义 + 回读真校验 + 探针产物不入库（9e7e185）
  - feat(切服)：首次切正式服 192.168.0.183（离线/空环境）链路实测通过（8bca421）
  - feat(ocr)：打通 OpenApi.common_ocr —— ai-service 接超算中心 OCR（含幂等部署与回滚）（53e9bdf）
  - feat(ai): ChatAI 单轮打通 + 挖出流式解析缺陷 A18（57c1891）
  - feat(email): 打通发信原子 + 根治 json_str 参数漏写崩溃（627c587）
  - feat(paths): 流程默认路径去机器化 —— PROJECT_ROOT 占位符（134 处/77 文件）（bee3234）
- **文档** 12：
  - docs(待办)：流程数 89 → 92（§0.0 项目定位是接力第一段，数字不能错）+ 更新日期（0b75b3f）
  - docs(部署)：新机器安装链路成文 —— 修 §0 过时定位、补 install.py 六步、技能副本同步规则（b33ad26）
  - docs(交接)：OCR 结案 —— 覆盖率 274/346 / 四件套 + README 同步（75b4e8a）
  - docs(交接): 接通 ai-service 的记录 + 缺陷 A18 立档 + 数字统一到 91 条/273（b07ea50）
  - docs(交接): P1#4 续推 —— 邮件域打通 / 三个域的阻塞定性收口（f27282c）
  - docs(交接): 路径去机器化结案 —— P0#6 关闭 / DSL 新增 §2.1 / 四件套全量同步（3219681）
  - docs(交接): 全量口径校准（数字重新实测，不照抄旧账）（b70604b）
  - docs(交接): 推送 origin 28 提交 + 匿名 clone 实测通过（13/13 全绿）（0d30598）
  - docs(交接): 分发层补齐 —— 装错目录的硬伤/自检假绿灯/发布体检（de37900）
  - docs(交接): 四件套收尾 —— 11b 结案 / B-1 口径修正 / §11.1.1 标准计数器写法（c1a7787）
  - docs(交接): 四件套收尾 —— Py 原子标准结构 / A17 结案 / 11a·11c 关闭（4bb38c5）
  - docs(交接): 四件套收尾 —— 脚本能力链 / 值保真边界 / 11c 唯一 P0（1c18d81）
- **测试** 1：
  - test(验收)：正式服 192.168.0.183 真跑产出（Word转PDF，3 页 / 88KB）作为换服物证（d847eb9）
- **杂项** 2：
  - chore(gitignore): OCR 运行产出目录整体忽略，让 release_check 回到全绿（b67fbd8）
  - chore(repo): 仓库瘦身 559→391 文件（评审单退出仓库 + 清可再生产物）（7c7148a）

### 2026-11-02 · 12 次提交
- **修复** 5：
  - fix(script): 分组列里的「真空」与「Excel 错误值」分开成组、分开说（QA X2）（1d7838d）
  - fix(script): 脚本健壮性五条 —— 值保真(keep_default_na) + 错误值点名 + 改名有感 + sheet 序号口径（58d38d8）
  - fix(script): R6-a 改名要告警 / R6-b sheet_name 数字按索引 + 收 v11 固定门禁（d112b6c）
  - fix(script): 脚本健壮性四条 —— 空值不静默丢行 / sheet_name 与 overwrite 参数化（9b04362）
  - fix(spec_lint): module_param 的 varValue 报错文案按类型分开（N10-e）（f1ce440）
- **新增** 3：
  - feat(script): AI 自己写 Python 模块 —— 原子做不到时自动建 Script.module 并真跑验证（0936e9c）
  - feat(L3): 自然语言直出流程 —— nl2spec + 自愈 + 真跑验证闭环（bac8046）
  - feat: 能力化改造——新增能力自举验收与规格DSL规范，口径从「流程成品」改为「搭流程能力」（d225cea）
- **文档** 2：
  - docs(qa): Round 7 最终验收 —— AA 提交卫生 / BB 清态全量 / CC 脏值角度（28e0901）
  - docs(readme): B-1~B-4 判定表表述与 SYSTEM_PROMPT 逐字对齐（0f1cf39）
- **测试** 1：
  - test(qa): 收紧 v11 的 D2 断言（必须产出 空值.xlsx 且 summary 明说「为空 1 行」）（fab8c6b）
- **杂项** 1：
  - chore(spec_lint): 数字型 varValue 的修法文案指向 int(args.get(...)) 转换（5e833e9）

### 2026-10-31 · 35 次提交
- **修复** 3：
  - fix(robot): 删除 2 个空机器人，固化 delete-robot 的 situation 必填坑（86d1c19）
  - fix(param): 清理主流程 40 个历史残留参数，固化 param/delete 正确用法（ddc42f3）
  - fix(winsimilar_pick): _do() 内误用 main() 局部变量 ap，改为 print+返回码（3539f7a）
- **新增** 22：
  - feat(coverage): 打通 Script.component 运行组件，覆盖率 76.3%→76.6%（265/346）（cbe05f6）
  - feat(coverage): 打通 WinEle.similar（相似元素），WinEle 域 6/6 满覆盖（13b6ff8）
  - feat(repro): 复现包扩到 15 条（15/15 REPRODUCED），新增 4 条平台缺陷自证脚本（2e5a028）
  - feat(coverage): +4 原子（打印/上传/下载/元素属性），68.2% -> 69.4%；修两处工具未接线（d575c49）
  - feat(repro): 平台缺陷复现包（11 条自证脚本，实测 11/11 成立，可一键打包发平台）（c9abc7c）
  - feat(coverage): +20 原子，覆盖率 62.1% -> 67.9%，抓出 6 个平台级坑（cbb268b）
  - feat(coverage): Docx 域 19/19 全覆盖，总覆盖率 60.1% -> 62.1%（cb22fa3）
  - feat(coverage): Network 域 FTP 12/12 全覆盖，总覆盖率 57.2% -> 60.1%（774cd50）
  - feat: System/File/BrowserElement 收尾，覆盖率 54.3% -> 57.2%，File 域 13/13 全覆盖（4b2f67d）
  - feat: Code 域 18/18 全覆盖（While/Try/Catch/Finally/Continue/Return/ForDict），覆盖率 54.3%（7112142）
  - feat: P1 扩覆盖 +44 原子（浏览器导航/Cookie、Excel批注与复制粘贴、数据表行列公式与循环、网页元素读取与操作），58 条流程 49.0%（9185bb8）
  - feat: 换设备打包脚本 handover_export.py（dd5bb36）
  - feat: 元素自动拾取打通（网页流程免人工点选），51 条流程 131 原子 36.7%（c90a4ec）
  - feat: 再补 2 条流程（Excel文本数字互转/加解密与时间差），50 条流程 130 原子覆盖率 36.4%（1c704f6）
  - feat: 再补 3 条流程（浏览器打开取标题/剪贴板与进程/数据表读写替换），覆盖率 34.5%（a2a011a）
  - feat: 再补 4 条流程（字符串清洗/列表高级操作/文件夹重命名移动/Excel空白模板），覆盖率 31.4%（17d9bbf）
  - feat: 再补 6 条流程（Word表格生成/读取、HTTP下载、Excel合并单元格、字典数学、计数循环），覆盖率 24.9%；拦掉 Code.ForStepEnd 等死原子（fb04220）
  - feat: 再补 12 条流程（Excel统计/公式格式/工作表/逐行读/转JSON、文件归档/复制、文件夹复制/压缩解压、列表文本、编码、时间戳），覆盖率 20.7%；新增原子实现核对，修 lint 循环块与预检目录误判（865f9f8）
  - feat: 批量补齐 7 条流程（Excel查找替换/批量重命名/HTTP/多表合并/按列去重/批量Word转PDF/网页填写），修正可迁移含义（0de74a1）
  - feat: 原子覆盖报告 + 两条纯原子 Word 流程（回答「300 原子为何只有 14 条规格」）（2b6e42d）
  - feat: 原子目录 + 规格校验器，打通「AI 自动拆解」闭环（0061a01）
  - feat: 技能包 + MCP 服务 + 一键安装（一句话装项目 → 发 KEY → 自然语言生成流程）（fe92d7e）
- **文档** 9：
  - docs: 精确对账落库状态（84=62机器人+8子流程+14未落库）（22abfb5）
  - docs: 四件套同步覆盖率 271/346（78.3%），Dialog 域 6/6 满覆盖（484e0cd）
  - docs: 平台缺陷清单二次复核（11/11 复现）+ 进度对账到 75 条/69.4%（30fcb32）
  - docs: 平台缺陷清单成文并复核（推翻 6 条旧结论；浏览器扩展空闲断连真因）（d1228b8）
  - docs: 换设备交接包（新增待办清单，四件套同步更新）（dbedf9f）
  - docs: 记录部署文档与性质澄清（进度日志）（45eefa2）
  - docs: 新增《部署使用文档》—— 换机部署四步、doctor 自检、迁移清单、FAQ；补充 requirements.txt（0006e2a）
  - docs: 记录 GitHub 推送与凭据脱敏过程（坑 106：.gitignore 对已跟踪文件不生效）（406ae05）
  - docs: README 更新为实际能力说明（流水线、快速开始、流程清单、安全说明）（fd8ccf3）
- **其他** 1：
  - init: AI 操作 RPA 流程生成器（自然语言→固定 RPA 流程）（30d3784）