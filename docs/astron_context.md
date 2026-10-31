# Astron RPA 系统上下文与交接文档

> 用途：作为 WorkBuddy（或任何 AI 助手）接手 Astron RPA 生产运维的上下文交接资料
> 整理日期：2026-10-28
> 来源：内部运维交接（用户整理）

---

## 一、环境与基础信息

| 项目 | 生产环境（正式服务器） | 测试环境（虚拟机） |
| --- | --- | --- |
| IP 地址 | 192.168.0.183 | 192.168.76.130 |
| 操作系统 | Ubuntu | Ubuntu |
| 项目路径 | /opt/astron-rpa/astron-rpa/docker | /home/astron-rpa/astron-rpa/docker |
| 网络情况 | 完全无外网（纯内网） | 可访问外网 |
| 数据库密码 | 位于 .env 的 DATABASE_PASSWORD | 同左 |
| DB 用户名/库名 | root / rpa | 同左 |

---

## 二、当前系统状态（2026-10-28）

- **部署模式**：已废弃"本地源码构建"模式，全面切换为官方镜像（`ghcr.io/iflytek/astron-rpa/*:latest`）。
- **数据状态**：业务数据（流程、用户、执行记录）完好保留，MySQL 与 MinIO 数据挂载在 `./data` 目录下。
- **客户端**：Windows 客户端（星辰 RPA / 星辰 Cowork）已能正常登录，连接地址为 `http://192.168.0.183:32742/`。
- **网关配置**：Nginx 使用 `legacy-http` 模式（纯 HTTP），配置目录为 `./volumes/nginx/`（包含 `templates` 和 `includes`）。

---

## 三、核心服务架构

由 **11 个容器**组成：

- **5 个核心业务服务**：`ai-service`、`openapi-service`、`resource-service`、`robot-service`、`rpa-auth`（均由官方镜像提供）。
- **4 个基础中间件**：`mysql`、`redis`、`minio`、`casdoor`（身份认证）。
- **2 个网关与辅助**：`openresty-nginx`（网关）、`atlas`（启动时一次性执行数据库结构迁移，执行完会退出，状态为 Exited 是正常的）。

---

## 四、⚠️ 历史踩坑与绝对红线（重点防御）

请严格遵循以下规则，否则会导致生产环境崩溃：

### 绝对不要动 Casdoor 容器
- **红线**：不要 `docker compose down` 包含 casdoor 的整个项目，不要重建/重启 casdoor 容器，不要执行 `DROP DATABASE casdoor`。
- **原因**：Casdoor 每次重新初始化都会随机生成新的 Client Secret。一旦重建，`.env` 里的旧密钥就会失效，导致客户端报 `Incorrect client secret`。
- **补救**：如果意外重启了，必须在 Casdoor 数据库查出最新密钥，更新 `.env`，然后只重启 `rpa-auth`（加 `--no-deps` 参数）。

### HTTP 环境下的 Cookie 限制
- `docker-compose-build.yml` 中 `rpa-auth` 必须包含 `SESSION_COOKIE_SECURE=false`。
- Nginx 必须使用 `DEPLOYMENT_MODE: "legacy-http"`。
- **原因**：官方默认安全策略是 HTTPS，内网 HTTP 环境下强制 Secure Cookie 会导致登录状态无法保存。

### 报 502 Bad Gateway 的解法
- 如果刚替换完镜像或重启后端，客户端报 502，是因为 Nginx 缓存了旧的后端容器 IP。
- **补救**：执行 `docker compose -f docker-compose-build.yml restart openresty-nginx` 刷新网关即可。

### 数据库结构迁移（Schema）
- 官方新版本增加了 `openai_executions` 的若干新列。如果官方镜像报 `schema is not ready`，需手动执行 `ALTER TABLE` 语句补全列（具体 SQL 见下）。

---

## 五、日常升级标准流程（SOP）

因为生产服务器无外网，禁止在生产直接 `docker compose pull`，必须走**离线打捞流程**：

**有网环境（虚拟机）：**
1. 拉取官方镜像：`docker pull ghcr.io/iflytek/astron-rpa/<服务名>:latest`
2. 打包：`docker save -o rpa-official.tar ghcr.io/...`
3. 传输：将 `.tar` 文件拷贝到生产服务器 `/opt/astron-rpa/`。

**生产环境（无网）：**
1. 停止服务：`docker compose -f docker-compose-build.yml down`（注意：如果只想更新部分服务，尽量加 `--no-deps` 或者别停 Casdoor）。
2. 加载：`docker load -i rpa-official.tar`
3. 启动：`docker compose -f docker-compose-build.yml up -d`
4. 刷新网关：`docker compose restart openresty-nginx`

---

## 六、常用排错 SQL（供 WorkBuddy 使用）

### 1. 查找 Casdoor 真实密钥
```bash
docker exec rpa-opensource-mysql mysql -uroot -p"你的密码" casdoor -e "SELECT client_id, client_secret FROM application WHERE name='example-app';"
```

### 2. 补全缺失的 OpenAPI 数据库列
```bash
docker exec rpa-opensource-mysql mysql -uroot -p"你的密码" rpa -e "
ALTER TABLE openai_executions
  ADD COLUMN protocol INT NULL,
  ADD COLUMN idempotency_key_hash VARCHAR(64) NULL,
  ADD COLUMN request_hash VARCHAR(64) NULL,
  ADD COLUMN client_id VARCHAR(36) NULL,
  ADD COLUMN run_id VARCHAR(100) NULL,
  ADD COLUMN dispatch_state VARCHAR(20) NULL,
  ADD COLUMN started_at DATETIME NULL,
  ADD COLUMN execution_timeout INT NULL,
  ADD COLUMN cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN cancel_supported BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN secret_fields TEXT NULL,
  ADD COLUMN data_contract MEDIUMTEXT CHARACTER SET utf8mb4 NULL;
"
```

---

## 七、给 WorkBuddy 的嘱咐

> 你好，WorkBuddy。当前 Astron RPA 系统处于稳定运行状态。由于是生产环境（无外网），处理任何问题请遵循：
> 1. **先备份，后操作**（数据在 `./data`，配置在 `.env`）。
> 2. **不破坏**当前的 `docker-compose-build.yml` 结构。
> 3. 遇到网络问题优先考虑**离线搬运**。
> 4. 遇到登录问题，严格按照"第四部分"的红线排查，**不要盲目重建容器**。

---

把这份文档交给 WorkBuddy，它就能瞬间掌握这几天的"战斗史"，完美接手接下来的运维工作。
