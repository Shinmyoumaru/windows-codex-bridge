# Windows Codex Bridge

A lightweight HTTP bridge that enables Linux/containerized AI Agents (like Hermes Agent) to delegate coding tasks to OpenAI Codex CLI running on Windows.

## Architecture

```
Hermes (Linux/W)  -->  HTTP POST  -->  Windows Codex Bridge  -->  codex exec
     (Agent)           :8765            (Python/FastAPI)        (OpenAI Codex)
```

Hermes must never execute Windows shell commands directly. All operations flow through this trusted bridge layer.

## Features

- HTTP API — REST interface callable from any language (curl, Python, etc.)
- Email verification — 6-digit code sent to configured email before each Codex call; code expires in 5 min, token valid for 30 min
- Path safety enforcement — workdir must be inside `F:\hermes_safe` and must be a git repository
- Sandboxed execution — Codex runs in `read-only` or `workspace-write` mode
- Timeout protection — max 30 minutes per request

## Quick Start

### 1. Start the Bridge (Windows side)

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

### 2. Configure SMTP email (optional — only needed for auth)

The bridge sends verification codes via SMTP. Set these environment variables:

| Variable | Description |
|---|---|
| `CODEX_VERIFY_EMAIL_TO` | Email address to receive verification codes |
| `CODEX_SMTP_HOST` | SMTP server hostname |
| `CODEX_SMTP_PORT` | SMTP port (default 465) |
| `CODEX_SMTP_USER` | SMTP username |
| `CODEX_SMTP_PASSWORD` | SMTP password |
| `CODEX_SMTP_FROM` | Sender address (default = SMTP_USER) |

### 3. Call from an Agent

```python
import json, urllib.request, os, time

BASE_URL = "http://172.17.224.1:8765"
CODEX_URL = BASE_URL + "/codex"
TOKEN_FILE = "/tmp/hermes_codex_token.json"

# Check cached token
if os.path.exists(TOKEN_FILE):
    with open(TOKEN_FILE) as f:
        data = json.load(f)
        if data["expires_at"] > time.time():
            token = data["token"]

# Call Codex
payload = {
    "prompt": "List all git repositories under F:\\hermes_safe",
    "workdir": r"F:\hermes_safe",
    "mode": "read_only"
}
headers = {"Content-Type": "application/json"}
if token:
    headers["Authorization"] = f"Bearer {token}"

req = urllib.request.Request(CODEX_URL, data=json.dumps(payload).encode(), headers=headers, method="POST")
with urllib.request.urlopen(req, timeout=1800) as resp:
    result = json.loads(resp.read().decode())
    print(result["stdout"])
```

### 4. Email verification flow

On first call, the bridge returns `401 auth_required` and emails a 6-digit code. Exchange it for a token via `/auth/verify`:

```python
verify_req = urllib.request.Request(
    BASE_URL + "/auth/verify",
    data=json.dumps({"code": "594594"}).encode(),
    headers={"Content-Type": "application/json"},
    method="POST"
)
with urllib.request.urlopen(verify_req, timeout=30) as resp:
    result = json.loads(resp.read())
    token = result["token"]
    # Save to TOKEN_FILE with expires_at
```

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Health check |
| `/auth/start` | POST | Trigger verification email |
| `/auth/verify` | POST | Exchange code for access token |
| `/codex` | POST | Execute Codex task (requires token) |

## Modes

| Mode | Description |
|---|---|
| `read_only` | Inspect repository without modifying any files |
| `edit` | Allow Codex to create/modify files |

## Security Rules

1. Never execute `codex`, `powershell.exe`, `cmd.exe`, or any Windows host command directly inside the container
2. Never set workdir outside `F:\hermes_safe`
3. Never hardcode or log access tokens
4. All operations must go through the HTTP bridge

## Files

- `codex_server.py` — Windows-side bridge service (FastAPI)
- `SKILL.md` — Hermes Agent skill documentation (for agent consumption)
- `README.md` — This file
- `README_CN.md` — Chinese version

## License

MIT
