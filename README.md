# Windows Codex Bridge

[English](README_EN.md) | **中文**

---

当 AI 智能体运行在 Docker/Podman 等容器中（基于 WSL 环境），容器内部无法直接调用 Windows 宿主机上的 OpenAI Codex CLI。本项目正是为了解决这一场景下的跨环境代码任务执行问题。

同时，出于安全考量，Bridge 采用**指定邮箱验证码**机制进行身份认证，确保只有授权用户才能触发 Codex 执行。

## 架构

```
Host Windows                Container (WSL/Docker)
┌─────────────────┐         ┌──────────────────────────────┐
│  Codex CLI      │         │  Hermes Agent               │
│  codex.cmd      │         │  (或其他 AI Agent)          │
│                 │         │                              │
│  Bridge Service │◄────────┤  HTTP POST                   │
│  (FastAPI)      │  :8765  │  /codex                     │
│                 │         │                              │
│  SMTP Email     │         │                              │
│  Verification   │         │                              │
└─────────────────┘         └──────────────────────────────┘
```

容器内的 Agent 永远不直接执行 Windows 命令，所有操作通过受信任的 HTTP 桥接层完成。

## 解决的问题

- 容器内无法直接调用 Windows 宿主机上的 `codex` 命令
- Docker/WSL 网络隔离导致的跨环境通信问题
- Codex 访问的**安全问题** — 通过邮件验证码确保只有授权用户可以使用

## 功能特性

- HTTP API — REST 接口，容器内的 Agent 可通过 curl/Python 调用
- 邮件验证码认证 — 每次调用前向指定邮箱发送 6 位验证码，5 分钟有效，Token 有效期 30 分钟
- 路径安全限制 — 只允许访问 `F:\hermes_safe` 及其子目录，且必须是 git 仓库
- 沙盒执行 — Codex 以 `read-only` 或 `workspace-write` 模式运行
- 超时保护 — 单次请求最长 30 分钟

## 快速开始

### 1. 启动 Bridge（Windows 端）

```powershell
cd windows-codex-bridge
pip install fastapi uvicorn pydantic

$env:CODEX_VERIFY_EMAIL_TO="your@email.com"
$env:CODEX_SMTP_HOST="smtp.qq.com"
$env:CODEX_SMTP_PORT="465"
$env:CODEX_SMTP_USER="your_smtp_user"
$env:CODEX_SMTP_PASSWORD="your_smtp_password"

uvicorn codex_server:app --host 0.0.0.0 --port 8765
```

### 2. 配置说明

| 环境变量 | 说明 |
|---|---|
| CODEX_VERIFY_EMAIL_TO | **必填**。接收验证码的邮箱，也是身份认证的唯一凭证 |
| CODEX_SMTP_HOST | SMTP 服务器地址 |
| CODEX_SMTP_PORT | SMTP 端口（默认 465） |
| CODEX_SMTP_USER | SMTP 用户名 |
| CODEX_SMTP_PASSWORD | SMTP 密码 |
| CODEX_SMTP_FROM | 发件人（默认同 SMTP_USER） |

### 3. 从 Agent 调用

```python
import json, urllib.request, os, time

BASE_URL = "http://172.17.224.1:8765"
CODEX_URL = BASE_URL + "/codex"
TOKEN_FILE = "/tmp/hermes_codex_token.json"

# 检查缓存 token
if os.path.exists(TOKEN_FILE):
    with open(TOKEN_FILE) as f:
        data = json.load(f)
        if data["expires_at"] > time.time():
            token = data["token"]

payload = {"prompt": "请列出 F:\\hermes_safe 下的所有 git 仓库", "workdir": r"F:\hermes_safe", "mode": "read_only"}
headers = {"Content-Type": "application/json"}
if token:
    headers["Authorization"] = f"Bearer {token}"

req = urllib.request.Request(CODEX_URL, data=json.dumps(payload).encode(), headers=headers, method="POST")
with urllib.request.urlopen(req, timeout=1800) as resp:
    print(json.loads(resp.read())["stdout"])
```

### 4. 验证码流程

首次调用返回 401 auth_required，Bridge 向 `CODEX_VERIFY_EMAIL_TO` 发送 6 位验证码。调用 `/auth/verify` 换取 Token：

```python
verify_req = urllib.request.Request(BASE_URL + "/auth/verify", data=json.dumps({"code": "594594"}).encode(), headers={"Content-Type": "application/json"}, method="POST")
with urllib.request.urlopen(verify_req, timeout=30) as resp:
    token = json.loads(resp.read())["token"]
    # 保存到 TOKEN_FILE（包含 expires_at）
```

## API 端点

| 端点 | 方法 | 说明 |
|---|---|---|
| /health | GET | 健康检查 |
| /auth/start | POST | 手动触发验证码邮件 |
| /auth/verify | POST | 验证验证码，返回访问 Token |
| /codex | POST | 执行 Codex 任务（需 Token） |

## 模式

| 模式 | 说明 |
|---|---|
| read_only | 只读扫描，不修改任何文件 |
| edit | 允许 Codex 创建/修改文件 |

## 安全规则

1. 永远不要在容器内直接执行 `codex`、`powershell.exe`、`cmd.exe` 等 Windows 命令
2. 永远不要将 workdir 设置在 `F:\hermes_safe` 之外
3. 永远不要将 Token 硬编码或打印到日志
4. 所有操作必须通过 HTTP 桥接层进行
5. 验证码是唯一身份凭证，请确保 `CODEX_VERIFY_EMAIL_TO` 邮箱的安全

## 文件

- `codex_server.py` — Windows 端 Bridge 服务（FastAPI）
- `SKILL.md` — Hermes Agent 技能文档

## 许可证

MIT
