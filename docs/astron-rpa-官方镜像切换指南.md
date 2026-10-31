# Astron RPA 切换官方镜像操作指南

> 来源：内部运维手册（Astron RPA 生产环境镜像切换）
> 整理日期：2026-10-28

**适用场景**：官方已发布包含最新修复的镜像（如修复了 MCP 兼容性、安全漏洞等），需从"本地源码构建"模式平滑切换为"官方镜像"模式。

**核心原则**：只换镜像和配置，**绝对不碰 Casdoor 密钥和业务数据**。

---

## 一、前期准备（在虚拟机/有网环境）

由于正式服务器无外网，需先在虚拟机上拉取并打包官方镜像。

### 1. 拉取官方最新镜像

```bash
# 在虚拟机执行
docker pull ghcr.io/iflytek/astron-rpa/ai-service:latest
docker pull ghcr.io/iflytek/astron-rpa/openapi-service:latest
docker pull ghcr.io/iflytek/astron-rpa/resource-service:latest
docker pull ghcr.io/iflytek/astron-rpa/robot-service:latest
docker pull ghcr.io/iflytek/astron-rpa/rpa-auth:latest
```

### 2. 打包镜像

```bash
docker save -o astron-rpa-official-$(date +%Y%m%d).tar \
  ghcr.io/iflytek/astron-rpa/ai-service:latest \
  ghcr.io/iflytek/astron-rpa/openapi-service:latest \
  ghcr.io/iflytek/astron-rpa/resource-service:latest \
  ghcr.io/iflytek/astron-rpa/robot-service:latest \
  ghcr.io/iflytek/astron-rpa/rpa-auth:latest
```

### 3. 修改并打包配置文件

编辑 `docker-compose-build.yml`，将 5 个核心服务从 `build:` 改为 `image: ghcr.io/...`，并务必补全以下关键环境变量（避免官方镜像的默认安全策略导致内网无法登录）：

```yaml
  casdoor:
    # ... 其他配置 ...
    environment:
      # 官方新加，防止重启重置管理员密码
      - initDataNewOnly=true

  rpa-auth:
    # ... 其他配置 ...
    environment:
      # 极其关键：内网 HTTP 环境下必须设为 false，否则 Cookie 无法写入
      - SESSION_COOKIE_SECURE=false
```

打包配置文件：

```bash
tar -czf astron-rpa-config-official-$(date +%Y%m%d).tar.gz \
  docker/docker-compose-build.yml \
  docker/volumes/nginx/
```

---

## 二、正式服务器切换（无网环境）

将上述两个 `.tar` 文件通过 U 盘或 scp 传输到正式服务器的 `/opt/astron-rpa/` 目录。

### 1. 强制备份（护身符）

```bash
cd /opt/astron-rpa/astron-rpa/docker
# 必须备份 data 和 .env
sudo tar -czf /opt/astron-rpa/backups/data-before-official-$(date +%Y%m%d).tar.gz data/ .env
```

### 2. 停止旧服务并解压配置

```bash
# 停止旧容器
docker compose -f docker-compose-build.yml down

# 解压新配置
cd /opt/astron-rpa/astron-rpa
sudo tar -xzf /opt/astron-rpa/astron-rpa-config-official-*.tar.gz
```

### 3. 加载镜像并启动

```bash
cd /opt/astron-rpa/astron-rpa/docker

# 加载离线镜像包
docker load -i /opt/astron-rpa/astron-rpa-official-*.tar

# 确认镜像已在列表中
docker images | grep "ghcr.io"

# 启动所有服务
docker compose -f docker-compose-build.yml up -d
sleep 30
docker compose -f docker-compose-build.yml ps
```

### 4. 验证服务

```bash
curl -s http://localhost:32742/api/rpa-auth/user/login-check
# 预期返回 {"code":"900001",...} 即成功
```

---

## 三、客户端配置验证

在 Windows 客户端找到 `resources/conf.yaml`：

```yaml
remote_addr: http://正式服务器IP:32742/
app_auth_type: casdoor
app_edition: saas
```

完全退出客户端后重新启动，输入账号密码登录。

---

## 四、避坑指南（血泪教训）

| 异常现象 | 根本原因 | 解决动作 |
| --- | --- | --- |
| 登录报 `Incorrect client secret` | 重建了 Casdoor 容器，导致数据库里的密钥变了，与 `.env` 不匹配。 | 绝对不要重建/重启 Casdoor。如果意外重启了，查最新密钥写入 `.env`，仅重启 `rpa-auth`（`--no-deps`）。 |
| 报 `502 Bad Gateway` | 替换镜像后，Nginx 缓存了旧的后端容器 IP，连不上新容器。 | 执行 `docker compose -f docker-compose-build.yml restart openresty-nginx` 刷新网关连接池。 |
| 客户端登录后白屏 / 无法记录状态 | 官方镜像默认开启 HTTPS Secure Cookie，内网 HTTP 环境下失效。 | 确保 `docker-compose-build.yml` 的 `rpa-auth` 中有 `SESSION_COOKIE_SECURE=false`。 |
| `openapi-service` 无限重启 | 官方镜像带了新的数据库表结构，旧数据缺少列。 | 执行 `ALTER TABLE` 补全 `openai_executions` 等表的列（详见历史迁移记录）。 |

---

## 五、切换后清理工作

确认业务运行正常，稳定 1~2 天后，可清理旧镜像和旧包：

```bash
# 1. 删除旧的本地构建镜像（释放空间）
docker rmi docker-ai-service:latest docker-openapi-service:latest \
           docker-resource-service:latest docker-robot-service:latest \
           docker-rpa-auth:latest

# 2. 删除旧的镜像包/配置包
rm -f /opt/astron-rpa/astron-rpa-update-*.tar
rm -f /opt/astron-rpa/astron-rpa-config-*.tar.gz

# 3. 清理悬空镜像
docker image prune -f
```

---

## 核心总结

未来的更新如果只是拉取官方镜像，**千万不要去执行 `git pull` 同步源码，也不要在正式服务器上进行 `docker compose build`**。只需：

> **打包镜像 → 替换 YAML 配置 → 启动 → 登录**

这才是生产环境最优雅的平滑升级方式。
