#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""全局配置：所有环境相关的东西只在这里出现，其它脚本一律不准硬编码。

## 为什么单独抽一层（用户 2026-10-30 明确）

> 「当前方案预备做成可迁移方案 类似插件形式 换个全新环境要也能正确运行
>   服务器层面能不改就不改 改动多了迁移可能会出现问题」

迁移时最怕的就是「IP、账号、路径散落在 8 个文件里」。所以：

- **一处配置**：`rpa_config.json`（放在项目根目录），首次运行自动生成模板。
- **优先级**：环境变量 > `客户端 conf.yaml` > `rpa_config.json` > cookie 探测 > 内置默认。
- **服务端以客户端为准**：设计器靠 `<安装目录>/resources/conf.yaml` 的 `remote_addr`
  决定连哪个服务端；用户改这个文件来切换服务器，所以工具链跟着它走（2026-10-30 用户明确）。
  conf.yaml 位置靠「环境变量 → 常见安装位 → 运行中进程路径」三级定位，换机器不用配。
- **cookie 兜底**：找不到 conf.yaml 时，才从设计器 `.cookie.json` 里取候选，再用 TCP
  探测挑**唯一连得通**的那个；多个都通就报错让人明确指定（不猜）。
- **DB 默认关闭**：`db.enabled=false`。所有走 SQL 的功能（`ServerDb`）在关闭时
  降级/报明确错误，保证「只用官方 HTTP API」就能跑通主链路。

## 用到的环境变量（都可选）

| 变量 | 覆盖 |
| --- | --- |
| `RPA_HOST` | `gateway.host`，形如 `192.168.76.130:32742` |
| `RPA_GATEWAY_BASE` | 完整基址，形如 `http://192.168.76.130:32742` |
| `RPA_OPENAPI_BASE` | openapi 基址（默认 `gateway + /api/rpa-openapi`） |
| `RPA_API_KEY` | openapi 的 `X-API-Key` |
| `RPA_CLIENT_PYTHON` | 设计器自带 Python 路径 |
| `RPA_LOCAL_GATEWAY_PORT` | 设计器本地网关端口，默认 13159 |
| `RPA_ROBOT_ID` | 默认机器人 id |
| `RPA_DB_ENABLED` | `1`/`true` 才启用 SQL 通道 |
| `RPA_DB_SSH_HOST` / `RPA_DB_SSH_USER` / `RPA_DB_SSH_PASSWORD` / `RPA_DB_PASSWORD` | SQL 通道凭据 |

## 自测

```
python rpa_config.py            # 打印当前生效配置 + 每项的来源
python rpa_config.py --write    # 把探测结果写成 rpa_config.json
```
"""
import argparse
import json
import os
import re
import socket
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONFIG_PATH = os.path.join(ROOT, "rpa_config.json")

APPDATA = os.environ.get("APPDATA", "")
COOKIE_FILE = os.path.join(APPDATA, "astron-rpa", ".cookie.json")

DEFAULTS = {
    "gateway": {"host": "", "scheme": "http"},
    "openapi": {"base": "", "api_key": ""},
    "client": {"python": "", "local_gateway_port": 13159, "home": "", "conf_yaml": ""},
    "db": {
        "enabled": False,
        "ssh_host": "", "ssh_user": "", "ssh_password": "",
        "container": "rpa-opensource-mysql",
        "user": "root", "password": "", "database": "rpa",
    },
    "defaults": {"robot_id": ""},
    # `Script.module` 的 module_param 落库方式：`baked`（默认，设计器一定打得开，但改参数不生效）
    # 或 `bound`（保留 rpa:special 引用 + need_parse，设计器能打开时参数才可改）。
    # 留空 = 用代码里的默认值 `baked`；写错值也不会炸（flow_generator 会退回 baked 并提示）。
    "param_binding": "",
}

_ENV_MAP = {
    ("gateway", "host"): "RPA_HOST",
    ("openapi", "api_key"): "RPA_API_KEY",
    ("client", "python"): "RPA_CLIENT_PYTHON",
    ("client", "local_gateway_port"): "RPA_LOCAL_GATEWAY_PORT",
    ("client", "conf_yaml"): "RPA_CLIENT_CONF",
    ("defaults", "robot_id"): "RPA_ROBOT_ID",
    ("db", "ssh_host"): "RPA_DB_SSH_HOST",
    ("db", "ssh_user"): "RPA_DB_SSH_USER",
    ("db", "ssh_password"): "RPA_DB_SSH_PASSWORD",
    ("db", "password"): "RPA_DB_PASSWORD",
    ("db", "database"): "RPA_DB_DATABASE",
}

_cache = None
_sources = {}
_ambiguous = []


# ---------------------------------------------------------------- 基础工具
def _deep_merge(base, extra):
    out = dict(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        elif v not in (None, ""):
            out[k] = v
    return out


def _read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def cookie_hosts():
    """从设计器 cookie 文件里取「它登录过的服务端」候选（`host:port`）。"""
    ck = _read_json(COOKIE_FILE, {})
    hosts = [k for k in ck if not k.endswith(".SESSION")]
    # 本机/回环排最后：优先真实服务端
    local = [h for h in hosts if h.startswith(("127.0.0.1", "localhost", "0.0.0.0"))]
    return [h for h in hosts if h not in local] + local


# ---------------------------- 客户端 conf.yaml ----------------------------
# 设计器靠 <安装目录>/resources/conf.yaml 里的 `remote_addr` 决定连哪个服务端。
# 用户明确要求（2026-10-30）：「服务器依据客户端 conf.yaml 来，我会修改这个来确定连哪个服务器」
# → 所以它的优先级高于 cookie 猜测，也高于 rpa_config.json 里的历史值。
_DESIGNER_SUBS = (
    "astron-rpa",
    os.path.join("Program Files", "astron-rpa"),
    os.path.join("Program Files (x86)", "astron-rpa"),
    os.path.join("Programs", "astron-rpa"),
    os.path.join("Apps", "astron-rpa"),
)
_REMOTE_RE = re.compile(r"^\s*remote_addr\s*:\s*(\S+)", re.M)


def _running_designer_home():
    """从正在运行的设计器进程取安装目录（最准，但慢，只在前面的候选都失败时才用）。"""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Process astron-rpa -ErrorAction SilentlyContinue | "
             "Select-Object -First 1).Path"],
            capture_output=True, text=True, timeout=8)
        for ln in (out.stdout or "").splitlines():
            p = ln.strip()
            if p and os.path.exists(p):
                return os.path.dirname(p)
    except Exception:
        pass
    return ""


def designer_home():
    """设计器安装根目录（能读到 conf.yaml 的那个）。"""
    for home in _designer_candidates():
        if os.path.exists(os.path.join(home, "resources", "conf.yaml")):
            return home
    return ""


def _designer_candidates():
    cands = []
    for env in ("ProgramFiles", "ProgramFiles(x86)"):
        v = os.environ.get(env)
        if v:
            cands.append(os.path.join(v, "astron-rpa"))
    la = os.environ.get("LOCALAPPDATA")
    if la:
        cands.append(os.path.join(la, "Programs", "astron-rpa"))
    for d in "CDEFGH":  # 本机就装在 D:\Program Files\astron-rpa
        for sub in _DESIGNER_SUBS:
            cands.append(os.path.join(f"{d}:\\", sub))
    return cands


def conf_yaml_path(explicit=""):
    """定位 `resources/conf.yaml`：显式配置 → 常见安装位 → 运行中进程。"""
    if explicit and os.path.exists(explicit):
        return explicit
    for home in _designer_candidates():
        p = os.path.join(home, "resources", "conf.yaml")
        if os.path.exists(p):
            return p
    home = _running_designer_home()
    if home:
        p = os.path.join(home, "resources", "conf.yaml")
        if os.path.exists(p):
            return p
    return ""


def remote_addr(path=""):
    """读 conf.yaml 的 `remote_addr` → (host:port, scheme, conf路径)。"""
    p = path or conf_yaml_path()
    if not p or not os.path.exists(p):
        return "", "http", ""
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            txt = f.read()
    except Exception:
        return "", "http", p
    m = _REMOTE_RE.search(txt)
    if not m:
        return "", "http", p
    url = m.group(1).strip().strip("\"'")
    scheme = "https" if url.lower().startswith("https://") else "http"
    host = re.sub(r"^https?://", "", url).rstrip("/")
    return host, scheme, p


def probe_host(hostport, timeout=1.5):
    """TCP 探测 `host:port` 是否连得通（不依赖 HTTP 客户端库）。"""
    try:
        host, _, port = hostport.rpartition(":")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return s.connect_ex((host, int(port))) == 0
    except Exception:
        return False


def detect_host():
    """自动挑服务端。返回 (host, 候选, 连得通的候选)。

    **只在「恰好一个连得通」时才自动采用**。多个都通时返回空串 —— 宁可报错让人
    明确指定，也不能猜（猜错就是在别的服务端上建流程，很难发现）。
    """
    cands = cookie_hosts()
    alive = [h for h in cands if probe_host(h)]
    if len(alive) == 1:
        return alive[0], cands, alive
    return "", cands, alive


def detect_client_python():
    p = os.path.join(APPDATA, "astron-rpa", "python_core", "python.exe")
    return p if os.path.exists(p) else ""


# ---------------------------------------------------------------- 加载
def load(force=False):
    """加载并合并配置。结果缓存；`force=True` 重新读。"""
    global _cache, _sources
    if _cache is not None and not force:
        return _cache

    _sources = {}
    cfg = json.loads(json.dumps(DEFAULTS))

    # 1) 文件
    file_cfg = _read_json(CONFIG_PATH, {})
    if file_cfg:
        cfg = _deep_merge(cfg, file_cfg)
        for sec, vals in file_cfg.items():
            if isinstance(vals, dict):
                for k, v in vals.items():
                    if v not in (None, ""):
                        _sources[f"{sec}.{k}"] = "rpa_config.json"

    # 2) 环境变量
    for (sec, key), env in _ENV_MAP.items():
        v = os.environ.get(env)
        if v not in (None, ""):
            cfg.setdefault(sec, {})[key] = v
            _sources[f"{sec}.{key}"] = f"env {env}"
    if os.environ.get("RPA_GATEWAY_BASE"):
        cfg["openapi"].setdefault("base", "")
        _sources["gateway.base"] = "env RPA_GATEWAY_BASE"
    if os.environ.get("RPA_OPENAPI_BASE"):
        cfg["openapi"]["base"] = os.environ["RPA_OPENAPI_BASE"]
        _sources["openapi.base"] = "env RPA_OPENAPI_BASE"
    db_flag = os.environ.get("RPA_DB_ENABLED")
    if db_flag is not None:
        cfg["db"]["enabled"] = str(db_flag).lower() in ("1", "true", "yes", "on")
        _sources["db.enabled"] = "env RPA_DB_ENABLED"

    # 3) 服务端地址：环境变量 > 客户端 conf.yaml > rpa_config.json > cookie 探测
    #    用户明确：改 conf.yaml 的 remote_addr 来决定连哪个服务端，所以它压过历史配置。
    global _ambiguous
    explicit_conf = (file_cfg.get("client", {}) or {}).get("conf_yaml") \
        or os.environ.get("RPA_CLIENT_CONF", "")
    conf_path = conf_yaml_path(explicit_conf)
    if conf_path:
        cfg["client"]["conf_yaml"] = conf_path
        _sources["client.conf_yaml"] = ("rpa_config.json" if explicit_conf == conf_path
                                        else "自动定位（安装目录/resources/conf.yaml）")
    conf_host, conf_scheme, _ = remote_addr(conf_path)
    env_host = os.environ.get("RPA_HOST", "").strip()

    if env_host:
        pass  # 环境变量最优先，已在上面写入
    elif conf_host:
        prev = cfg["gateway"].get("host")
        cfg["gateway"]["host"] = conf_host
        cfg["gateway"]["scheme"] = conf_scheme
        if prev and prev != conf_host:
            _sources["gateway.host"] = f"客户端 conf.yaml（覆盖了 rpa_config.json 的 {prev}）"
        else:
            _sources["gateway.host"] = f"客户端 conf.yaml → {os.path.basename(os.path.dirname(os.path.dirname(conf_path)))}"
    elif not cfg["gateway"].get("host"):
        h, cands, alive = detect_host()
        if h:
            cfg["gateway"]["host"] = h
            _sources["gateway.host"] = f"cookie 探测（候选 {len(cands)} 个，唯一连得通）"
        elif len(alive) > 1:
            _ambiguous = alive
            _sources["gateway.host"] = f"⚠️ 探测到 {len(alive)} 个可用服务端，无法确定用哪个"
    if not cfg["client"].get("python"):
        p = detect_client_python()
        if p:
            cfg["client"]["python"] = p
            _sources["client.python"] = "自动探测（%APPDATA%/astron-rpa/python_core）"

    # 4) 派生：地址一旦来自 conf.yaml / 环境变量，openapi 与 ssh 必须跟着走，
    #    否则「换了服务器还在拿旧地址调」——这类错很难看出来（连得上但连错地方）。
    scheme = cfg["gateway"].get("scheme") or "http"
    gwhost = cfg["gateway"].get("host") or ""
    if gwhost:
        want = f"{scheme}://{gwhost}/api/rpa-openapi"
        cur = cfg["openapi"].get("base") or ""
        if not cur:
            cfg["openapi"]["base"] = want
            _sources["openapi.base"] = "由 gateway.host 推导"
        elif not cur.startswith(f"{scheme}://{gwhost}") and not os.environ.get("RPA_OPENAPI_BASE"):
            cfg["openapi"]["base"] = want
            _sources["openapi.base"] = f"跟随 gateway.host 重算（原配置指向 {cur}）"

        cur_ssh = cfg["db"].get("ssh_host") or ""
        if not cur_ssh:
            cfg["db"]["ssh_host"] = gwhost.rpartition(":")[0]
        elif cur_ssh != gwhost.rpartition(":")[0] and not os.environ.get("RPA_DB_SSH_HOST"):
            cfg["db"]["ssh_host"] = gwhost.rpartition(":")[0]
            _sources["db.ssh_host"] = f"跟随 gateway.host 重算（原配置指向 {cur_ssh}）"

    _cache = cfg
    return cfg


# ---------------------------------------------------------------- 取用
def get(dotted, default=None):
    """按 `a.b` 取值。"""
    node = load()
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node if node not in (None, "") else default


def host_port():
    return get("gateway.host", "")


def gateway_base():
    h = host_port()
    if not h:
        load()
        extra = ""
        if _ambiguous:
            extra = ("\n  探测到多个连得通的服务端，**必须显式指定**（不能猜，猜错就在别的服务端上建流程）：\n"
                     + "\n".join(f"    · {x}" for x in _ambiguous))
        raise SystemExit(
            "没配置服务端地址，且自动探测无法确定。\n"
            "  办法一（推荐）：改设计器 `resources/conf.yaml` 的 `remote_addr` —— 设计器连哪个，工具链就跟哪个\n"
            "  办法二：设置环境变量 RPA_HOST=192.168.x.x:32742\n"
            "  办法三：在 rpa_config.json 写 {\"gateway\":{\"host\":\"192.168.x.x:32742\"}}\n"
            f"  （cookie 候选来源：{COOKIE_FILE}）" + extra
        )
    scheme = get("gateway.scheme", "http")
    return f"{scheme}://{h}"


def openapi_base():
    return get("openapi.base") or (gateway_base() + "/api/rpa-openapi")


def api_key():
    return get("openapi.api_key", "")


def client_python():
    return get("client.python", "")


def local_gateway_port():
    return int(get("client.local_gateway_port", 13159) or 13159)


def default_robot_id():
    return str(get("defaults.robot_id", "") or "")


def param_binding():
    """`module_param` 落库方式：`"baked"` / `"bound"`；未配置返回空串（= 用代码默认值）。

    来源优先级：环境变量 `RPA_PARAM_BINDING` > `rpa_config.json` 的顶层 `param_binding`。
    这个开关放在配置里的原因：用户可以自己切，不必等改代码/发版（A17 的临时解）。
    """
    v = os.environ.get("RPA_PARAM_BINDING", "") or get("param_binding", "")
    return str(v or "").strip().lower()


def db_conf():
    """SQL 通道配置；未启用返回 None（调用方必须能接受 None）。"""
    if not get("db.enabled", False):
        return None
    return {
        "ssh_host": get("db.ssh_host", ""), "ssh_user": get("db.ssh_user", ""),
        "ssh_password": get("db.ssh_password", ""), "container": get("db.container", "rpa-opensource-mysql"),
        "user": get("db.user", "root"), "password": get("db.password", ""),
        "database": get("db.database", "rpa"),
    }


def cookie_file():
    return COOKIE_FILE


# ---------------------------------------------------------------- 落盘 / 展示
def write_config(force=False):
    """把当前生效值写成 `rpa_config.json`（不含敏感项时也安全）。"""
    if os.path.exists(CONFIG_PATH) and not force:
        return CONFIG_PATH, False
    cfg = load()
    out = {
        "gateway": {"host": cfg["gateway"].get("host", ""), "scheme": cfg["gateway"].get("scheme", "http")},
        "openapi": {"base": cfg["openapi"].get("base", ""), "api_key": cfg["openapi"].get("api_key", "")},
        "client": {"python": cfg["client"].get("python", ""),
                   "local_gateway_port": cfg["client"].get("local_gateway_port", 13159)},
        "db": dict(cfg["db"]),
        "defaults": {"robot_id": cfg["defaults"].get("robot_id", "")},
        "param_binding": cfg.get("param_binding") or "baked",
    }
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return CONFIG_PATH, True


def describe():
    """[(项, 值, 来源)] —— doctor 用。"""
    cfg = load()
    rows = []

    def add(k, v, secret=False):
        show = "—" if not v else (("****" + str(v)[-4:]) if secret else str(v))
        rows.append((k, show, _sources.get(k, "内置默认")))

    add("gateway.host", cfg["gateway"].get("host"))
    add("openapi.base", cfg["openapi"].get("base"))
    add("openapi.api_key", cfg["openapi"].get("api_key"), secret=True)
    add("client.python", cfg["client"].get("python"))
    add("client.conf_yaml", cfg["client"].get("conf_yaml"))
    add("client.local_gateway_port", cfg["client"].get("local_gateway_port"))
    add("db.enabled", cfg["db"].get("enabled"))
    add("db.ssh_host", cfg["db"].get("ssh_host"))
    add("defaults.robot_id", cfg["defaults"].get("robot_id"))
    add("param_binding", cfg.get("param_binding") or "baked（默认）")
    return rows


def main():
    ap = argparse.ArgumentParser(description="查看/生成 RPA 工具链配置")
    ap.add_argument("--write", action="store_true", help="把当前生效值写成 rpa_config.json")
    ap.add_argument("--force", action="store_true", help="配合 --write，覆盖已有文件")
    args = ap.parse_args()

    if args.write:
        p, created = write_config(force=args.force)
        print(("已生成 " if created else "已存在，未覆盖 ") + p)
        if not created and args.force:
            print("（--force 也没覆盖？检查权限）")
        return 0

    print("=" * 68)
    print(f"配置文件：{CONFIG_PATH}（{'存在' if os.path.exists(CONFIG_PATH) else '不存在，用探测值'}）")
    print("=" * 68)
    for k, v, src in describe():
        print(f"  {k:26} {v:38} ← {src}")
    print("-" * 68)
    cands = cookie_hosts()
    print(f"  cookie 里的服务端候选：{cands or '无'}")
    if cands:
        for h in cands:
            print(f"    {'✅' if probe_host(h) else '❌'} {h}")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
