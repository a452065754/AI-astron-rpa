# Astron Agent 部署记录

> 部署时间：2026-10-28 ｜ 部署人：WorkBuddy（依据星辰开源项目）
> 服务器：192.168.76.134（beiyongpc，Ubuntu 24.04，4C/7.7G，新服务器）

## 一、部署前提与定制要求（用户指令）
- 部署 **astron-agent**（企业级 Agentic Workflow 平台），**不要知识库（KB）、不要 RPA**（RPA 已独立部署）。
- 访问地址：`http://192.168.76.134/`
- 登录账号：`xudongdong` / `1234`
- 顺带处理：系统盘显示 120G 只认到 57.77G（LVM LV 未撑满 VG）。

## 二、已完成的操作
1. **磁盘扩容**：LVM 逻辑卷 `ubuntu-lv` 从 59G 在线扩展至 ~117G（`lvextend -l +100%FREE` + `resize2fs`，ext4，数据无损）。根分区现为 116G / 可用 ~100G。
2. **代码克隆**：`git clone` 到 `/home/xudongdong/astron-agent`，部署目录 `docker/astronAgent`。
3. **裁剪服务**：从 `docker-compose.yaml` 删除 `core-knowledge`（知识库）与 `core-rpa`（RPA 集成）两个服务，仅保留 15 个核心服务。
4. **配置文件 `.env`**：
   - `HOST_BASE_ADDRESS=http://192.168.76.134`
   - `EXPOSE_NGINX_PORT=80`（控制台）、`CASDOOR_PORT=8000`（鉴权）
   - 派生：`CONSOLE_DOMAIN=http://192.168.76.134:80`、`CONSOLE_CASDOOR_URL=http://192.168.76.134:8000`
   - 中间件已设强密码（见第三节）。
   - HTTP 明文环境下 Casdoor 自动按 origin(http) 下发非安全 Cookie，无需额外开关（比 RPA 更简单）。
5. **安全契约校验**：`scripts/verify_security_contract.py` → **PASS**（5 服务、4 独立凭据卷、21 个反向用例）。
6. **镜像拉取 + 启动**：直连 ghcr.io 拉取（该机有外网，无需离线搬运），`docker compose -f docker-compose-with-auth.yaml up -d` 全栈启动。
7. **账号创建**：Casdoor 默认 `admin/<CASDOOR_DEFAULT_PASSWORD>` 初始化后，通过 DB 直插创建 `xudongdong/<SSH_PASSWORD>`（built-in 组织、is_admin=1、password_type=plain）。

## 三、关键凭据（请妥善保管）
> 中间件密码由部署时随机生成，建议后续改为自有强口令。

| 项 | 值 |
| --- | --- |
| 控制台地址 | http://192.168.76.134/ |
| Casdoor 地址 | http://192.168.76.134:8000 |
| 登录账号 / 密码 | **xudongdong / <SSH_PASSWORD>** |
| 默认管理员（备用） | admin / 123 |
| MYSQL_ROOT_PASSWORD | <MYSQL_ROOT_PASSWORD> |
| REDIS_PASSWORD | <REDIS_PASSWORD> |
| MINIO_ROOT_PASSWORD | <MINIO_ROOT_PASSWORD> |
| POSTGRES_PASSWORD | <POSTGRES_PASSWORD> |
| Casdoor 内部 MySQL | root / root123（硬编码，仅内网） |

Casdoor 应用 clientId/secret（库内实际值，init 已重生成，与仓库模板不同）：
- app-built-in: `b4ae04dda390d5fd1451` / `<HASH>`
- astron-agent-app: `astron-agent-client` / `<HASH>`

## 四、踩坑与注意事项
1. **Casdoor 禁止 init_data 新增 built-in 用户**：直接把 `xudongdong` 写进 `init_data.json` 会导致 Casdoor 启动 panic（"adding a new user to the 'built-in' organization is currently disabled"）并无限重启。正确做法：先让 Casdoor 用默认 `admin/<CASDOOR_DEFAULT_PASSWORD>` 初始化，再通过 API/DB 创建账号。
2. **xudongdong 创建方式**：最终用 `docker exec ... mysql` 克隆 admin 行并改 name/password 落库（CASDOOR 的 `/api/login` JSON 接口在本版 2.67 对 clientId 字段绑定有 quirks，手动 curl 难复现 SPA 登录，故走 DB 直插，确定性更高）。
3. **docker 权限**：`xudongdong` 不在 docker 组，所有 docker 命令需 `sudo`（密码 <SSH_PASSWORD>）。`docker compose config` 不需 daemon 权限，但 `pull/up/ps` 需 sudo。
4. **compose 项目名不一致**：主栈容器前缀 `astron-agent-`，而 `docker-compose-auth.yml` 引入的 `casdoor-gateway` 前缀为 `astronagent-`，属正常现象（include 拆分导致）。
5. **与 RPA 的关系**：本机只部署 Agent 平台；RPA（core-rpa 服务）已剔除。若日后要打通，需在控制台配置独立 RPA 地址（用户已有 RPA 服务 192.168.0.183:32742）。

## 五、日常运维
- 启停：`sudo docker compose -f /home/xudongdong/astron-agent/docker/astronAgent/docker-compose-with-auth.yaml [up -d | down]`
- 查看状态：`sudo docker ps --filter name=astron`
- 升级：ghcr.io/iflytek/astron-agent/*:latest 直连拉取即可（有外网），无需像 RPA 那样离线搬运。
- 备份重点：`.env`（含密码）、Casdoor MySQL 卷 `astronagent_casdoor-mysql-data`、主 MySQL 卷。
