# QTA 故障分析报告：rpa-opensource-openapi-service 重启循环

> QTA = Quality Technical Analysis（质量技术分析）。本文针对 2026-10-29 测试服务器 `192.168.76.130` 上 `rpa-opensource-openapi-service` 容器反复重启故障，做完整的问题定位、根因分析与改进闭环。

## 文档信息

| 项目 | 内容 |
| --- | --- |
| 文档编号 | QTA-20261029-001 |
| 故障等级 | P3（单服务不可用，核心链路可降级；测试环境） |
| 发生时间 | 2026-10-29（服务器重启后持续） |
| 发现方式 | 用户反馈 + 容器日志 |
| 分析人 | 交付工程（AI 协同） |
| 涉及系统 | Astron RPA 后端（`192.168.76.130`） |
| 涉及组件 | `rpa-opensource-openapi-service`（OpenAPI，FastAPI，端口 8020）、`rpa-opensource-mysql`（库 `rpa`） |
| 当前状态 | ✅ 已修复并验证 |

---

## 1. 故障概述

测试服务器 `192.168.76.130` 的 `rpa-opensource-openapi-service` 容器处于 **`Restarting` 循环**：容器启动后因数据库 schema 自检不通过而主动 `Exit`，被 Docker `restart=always` 策略反复拉起，导致 OpenAPI 服务始终不可用，RPA 控制台中依赖 openapi 的功能（流程列表/执行/外部集成）全部受影响。

> 说明：本故障与「对服务器做过端口探测」无因果关系。诊断全程仅在 `.130` 执行了**被动端口探测（TCP 建连 + HTTP GET）**，从未 SSH 登录、未执行 docker 命令、未修改任何文件。日志证明重启由**应用自身启动自检失败**触发。

---

## 2. 影响范围

| 维度 | 影响 |
| --- | --- |
| 直接影响 | OpenAPI 服务（8020）不可用，容器无限重启 |
| 业务影响 | 控制台「流程列表 / 执行 / 外部集成（API Key、MCP）」不可用 |
| 数据影响 | 无数据丢失（迁移脚本非破坏性，未删除任何行） |
| 环境 | 测试环境（`192.168.76.130`），未波及生产 |
| 恢复时长 | 约 10 分钟（含备份 + 迁移 + 验证） |

---

## 3. 时间线

| 时间 | 事件 |
| --- | --- |
| — | 服务器重启（用户反馈「刚没有开现在开了」） |
| T0 | `rpa-opensource-openapi-service` 启动 → 执行 `check_execution_schema()` |
| T0+ | 校验发现 `openai_executions` / `openai_workflows` 缺列 → `RuntimeError` → 进程退出 |
| T0+ | Docker `restart=always` 拉起 → 再次失败 → 形成 `Restarting (n)` 循环 |
| T1 | 用户反馈「容器一直重启」，怀疑近期操作导致 |
| T2 | 定位：日志显示 `OpenAPI execution schema is not ready ... apply missing migrations 001/002` |
| T3 | 备份 `rpa` 库（8.0M） |
| T4 | 停容器 → 按序执行 `001_execution_management.sql`、`002_json_data_contract.sql`（RC=0） |
| T5 | 还原 `restart=always` → 启动 → `Application startup complete`，容器 `Up` 稳定 |
| T6 | 验证：新列已落库、8020 正常监听、不再重启 → 结案 |

---

## 4. 故障现象与证据

### 4.1 容器状态
```
rpa-opensource-openapi-service   Restarting (3) 23 seconds ago
```

### 4.2 关键错误日志
```
File "/app/app/services/schema_readiness.py", line 46, in validate_execution_schema
    raise RuntimeError("OpenAPI execution schema is not ready; " + "; ".join(details) + ". " + MIGRATION_HELP)
RuntimeError: OpenAPI execution schema is not ready; missing tables/columns:
  openai_executions.protocol, openai_executions.idempotency_key_hash,
  openai_executions.request_hash, openai_executions.client_id,
  openai_executions.run_id, openai_executions.dispatch_state,
  openai_executions.started_at, openai_executions.execution_timeout,
  openai_executions.cancel_requested, openai_executions.cancel_supported,
  openai_executions.secret_fields, openai_executions.data_contract;
columns require MEDIUMTEXT or LONGTEXT:
  openai_workflows.parameters, openai_executions.parameters, openai_executions.result.
Stop OpenAPI and follow EXECUTION_MANAGEMENT.md: apply missing migrations
001_execution_management.sql and 002_json_data_contract.sql in order before restarting.
ERROR:    Application startup failed. Exiting.
```

### 4.3 修复前库表结构（缺列实证）
| 表 | 修复前列（节选） |
| --- | --- |
| `openai_executions` | id, project_id, status, parameters, result, error, user_id, exec_position, recording_config, version, start_time, end_time |
| `openai_workflows` | project_id, name, description, version, status, user_id, example_project_id, created_at, updated_at, english_name, parameters |

> 对照报错：`openai_executions` 缺少 `protocol`…`data_contract` 共 12 个新列；`parameters/result` 仍为 `TEXT` 未扩为 `MEDIUMTEXT`。

---

## 5. 根因分析

### 5.1 直接原因
`openapi-service` 启动时强制校验执行管理相关的表结构（`schema_readiness.py`），发现 `openai_executions`/`openai_workflows` 缺少新版本要求的列与列类型不达标，主动抛出异常退出；`restart=always` 让容器反复拉起，形成重启循环。

### 5.2 根本原因
**镜像版本升级后，数据库迁移脚本未执行**。openapi 服务代码已升级到要求「执行管理」（execution management）新 schema 的版本，但部署时只更新了镜像、未同步在 `rpa` 库执行 `backend/openapi-service/migrations/*.sql`，导致代码与库结构版本不匹配。

### 5.3 5 Whys
1. 为什么容器一直重启？→ 启动自检失败主动退出。
2. 为什么自检失败？→ 数据库缺新列/列类型不符。
3. 为什么缺列？→ 迁移脚本未执行。
4. 为什么迁移没执行？→ 升级流程只替换镜像，未把「数据库迁移」纳入发布 checklist。
5. 为什么没纳入？→ 缺少「镜像升级 ⇒ 必跑 migrations」的强制 SOP 与自动校验门禁。

### 5.4 贡献因素
- 服务设计为「schema 不达标即拒绝启动」（安全但会放大为重启风暴）；
- `restart=always` 无退避/熔断，失败即无限重试；
- 发布流程无 schema 版本对齐校验。

---

## 6. 处置过程

| 步骤 | 操作 | 说明 | 结果 |
| --- | --- | --- | --- |
| 1 | 备份 | `docker exec rpa-opensource-mysql mysqldump -uroot -p*** rpa > /home/xudongdong/rpa_backup_20261029.sql` | 8.0M，可回滚 |
| 2 | 停容器 | `docker update --restart=no` + `docker stop` | 避免 ALTER 元数据锁竞争 |
| 3 | 执行 001 | `mysql rpa < migrations/001_execution_management.sql` | RC=0，新增 12 列 + 索引，删除旧外键（保留历史） |
| 4 | 执行 002 | `mysql rpa < migrations/002_json_data_contract.sql` | RC=0，`parameters/result/data_contract` 改 `MEDIUMTEXT` |
| 5 | 启服 | `docker update --restart=always` + `docker start` | 容器 `Up` |
| 6 | 验证 | 查列 / 查状态 / 查日志 | 全部通过 |

### 迁移脚本要点
- **001_execution_management.sql**：对 `openai_executions` 新增 12 个可空列（`protocol`、`idempotency_key_hash`、`request_hash`、`client_id`、`run_id`、`dispatch_state`、`started_at`、`execution_timeout`、`cancel_requested`、`cancel_supported`、`secret_fields`、`data_contract`），加唯一键与索引，并动态删除 `openai_executions → openai_workflows` 的历史外键（避免删流程时丢失执行凭证）。
- **002_json_data_contract.sql**：`openai_workflows.parameters`、`openai_executions.parameters`、`openai_executions.result` 扩为 `MEDIUMTEXT`（支持 1 MiB JSON），并加 `data_contract` 列。
- 两脚本均注明「非破坏性，不删除任何现有行；旧行保持 legacy」。

---

## 7. 验证结果

| 验证项 | 方法 | 结果 |
| --- | --- | --- |
| 容器状态 | `docker ps` | `Up`（无 Restarting） |
| 启动日志 | `docker logs --tail` | `Application startup complete` / `Uvicorn running on 0.0.0.0:8020` |
| 表结构 | `information_schema.COLUMNS` | `openai_executions` 已含全部 12 新列 |
| 端口暴露 | `docker port` | 8020 仅内网，经 nginx `32742→80` 的 `/api/rpa-openapi/` 反代 |
| 持久性 | 重启策略 | `restart=always` 已还原，下次重启不再报缺列 |

---

## 8. 改进与预防措施

### 8.1 短期（立即）
- [x] 已完成本次迁移，备份留存 `/home/xudongdong/rpa_backup_20261029.sql`。
- [ ] 将「`rpa` 库迁移执行记录」写入运维台账（记录已执行 001/002）。

### 8.2 中期（流程）
- [ ] **发布 SOP 新增强制门禁**：凡升级 `openapi-service` 镜像，必须在 `rpa` 库按序执行 `migrations/*.sql`（幂等性先确认）。发布 checklist 增加「schema 版本对齐」项。
- [ ] 建立迁移脚本版本表（如 `schema_migrations`），应用启动前对比，缺迁移给出明确提示而非直接退出。

### 8.3 长期（机制）
- [ ] `restart=always` 改为 `on-failure` + 退避，或加健康检查门禁，避免失败时无限重启放大故障。
- [ ] CI/CD 中增加「镜像版本 ↔ DB schema 版本」一致性校验步骤。
- [ ] 迁移脚本纳入自动化发布（如 docker entrypoint 内做一次性迁移，或独立的 migrate 服务）。

### 8.4 预防 Checklist（升级 openapi 时必勾）
```
[ ] 已备份 rpa 库
[ ] 已确认当前镜像版本的 migrations 目录
[ ] 已按序执行 001…00N（记录 RC）
[ ] 已停 openapi → 迁移 → 启 openapi
[ ] 验证：docker ps = Up；日志含 startup complete；无 schema 报错
[ ] 验证：/api/rpa-openapi 经 nginx 可达
```

---

## 9. 经验教训

1. **升级 = 镜像 + 库结构**，二者版本必须同步；只看镜像 tag 会踩坑。
2. 应用「schema 不达标即退出」是正确设计，但需配套**可执行的迁移指引**（本次日志已直接给出脚本名，帮了大忙）。
3. `restart=always` 在配置错误场景会放大为重启风暴；建议区分「崩溃重启」与「配置错误不重启」。
4. 排障第一步看**容器日志**，能直接定位到应用层根因，避免误判为「外部操作破坏」。

---

## 10. 附录

### 10.1 关键路径
- 迁移脚本：`/home/astron-rpa/astron-rpa/backend/openapi-service/migrations/`
- 说明文档：`/home/astron-rpa/astron-rpa/backend/openapi-service/EXECUTION_MANAGEMENT.md`
- 数据库备份：`/home/xudongdong/rpa_backup_20261029.sql`
- nginx 路由：`/home/astron-rpa/astron-rpa/docker/volumes/nginx/includes/gateway-routes.conf`

### 10.2 复现与修复命令（脱敏）
```bash
# 复现：容器重启循环
docker ps --filter name=openapi          # Restarting (n)
docker logs --tail 50 rpa-opensource-openapi-service

# 备份
docker exec -i rpa-opensource-mysql mysqldump -uroot -p*** rpa > ~/rpa_backup_$(date +%Y%m%d).sql

# 停 → 迁移 → 启
docker update --restart=no rpa-opensource-openapi-service
docker stop rpa-opensource-openapi-service
docker exec -i rpa-opensource-mysql mysql -uroot -p*** rpa < .../migrations/001_execution_management.sql
docker exec -i rpa-opensource-mysql mysql -uroot -p*** rpa < .../migrations/002_json_data_contract.sql
docker update --restart=always rpa-opensource-openapi-service
docker start rpa-opensource-openapi-service

# 验证
docker ps --filter name=openapi
docker logs --tail 30 rpa-opensource-openapi-service | grep -E 'startup complete|Uvicorn'
```

### 10.3 回滚方案
如需回滚：`docker stop rpa-opensource-openapi-service` → 用备份恢复（`mysql rpa < ~/rpa_backup_20261029.sql`，注意会覆盖后续数据）→ 使用旧镜像启动。**注意**：迁移为非破坏性，一般无需回滚。

---

*报告结束 · QTA-20261029-001*
