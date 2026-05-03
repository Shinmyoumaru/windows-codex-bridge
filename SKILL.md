---
name: windows-codex-bridge
description: "Delegate coding tasks to OpenAI Codex CLI on Windows via HTTP bridge with email authentication. For Hermes running in WSL/Docker while Codex is on Windows."
version: 2.0.0
metadata:
  hermes:
    tags: [codex, openai, windows, bridge, delegation, wsl, docker, email, auth]
---

# Windows Codex Bridge with Email Verification

Delegate coding tasks to the OpenAI Codex CLI installed on the Windows host through a restricted HTTP bridge.

This skill is designed for this architecture:

```
Hermes container
  -> HTTP POST
Windows Codex Bridge
  -> codex exec
```

Hermes must never execute Windows shell commands directly.

## Use this skill when

Use this skill when:
- The user asks to use Codex.
- The user asks to inspect, modify, refactor, summarize, or review code using Codex.
- Hermes is running inside WSL, Docker, or Podman, while Codex CLI is installed on Windows.

## Do not use this skill for

Do not use this skill for:
- General shell commands.
- Direct PowerShell, cmd.exe, bash.exe, git, npm, pip, or arbitrary command execution.
- Tasks that do not require Codex.

## Endpoint

Known working endpoint:

```
http://172.17.224.1:8765
```

Codex endpoint:

```
http://172.17.224.1:8765/codex
```

Verification endpoint:

```
http://172.17.224.1:8765/auth/verify
```

Optional verification-start endpoint:

```
http://172.17.224.1:8765/auth/start
```

## Allowed workdir

Default workdir:

```
F:\hermes_safe
```

The workdir must:
- Be under the bridge's allowed root.
- Be a git repository.

## Authentication model

The bridge requires a temporary token before `/codex` can be used.

If a `/codex` request has no valid token:
- The bridge sends a random verification code to the configured email address.
- The verification code must be used within 5 minutes.
- After successful verification, the bridge returns a token.
- The token is valid for 30 minutes.
- During the valid token period, Codex write access is enabled.

The token must be passed as:

```
Authorization: Bearer <TOKEN>
```

or:

```
X-Codex-Token: <TOKEN>
```

## Token cache

Store the temporary token inside the Hermes container at:

```
/tmp/hermes_codex_token.json
```

The file should contain:

```json
{
  "token": "...",
  "expires_at": 1234567890
}
```

Before calling `/codex`, check whether this file exists and whether `expires_at` is still in the future.

If the token is valid, use it.

If the token is missing or expired, call `/codex` once without token to trigger email verification, then ask the user to provide the verification code.

## Step 1: Try calling Codex with cached token

Use this template:

```bash
python3 - <<'PY'
import json
import time
import os
import urllib.request
import urllib.error

BASE_URL = "http://172.17.224.1:8765"
CODEX_URL = BASE_URL + "/codex"
TOKEN_FILE = "/tmp/hermes_codex_token.json"

def load_token():
    if not os.path.exists(TOKEN_FILE):
        return None

    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if data.get("expires_at", 0) > time.time():
            return data.get("token")

    except Exception:
        return None

    return None

token = load_token()

prompt = r"""<USER_TASK>"""

data = {
    "prompt": prompt,
    "workdir": r"F:\hermes_safe",
    "mode": "read_only"
}

headers = {
    "Content-Type": "application/json"
}

if token:
    headers["Authorization"] = f"Bearer {token}"

req = urllib.request.Request(
    CODEX_URL,
    data=json.dumps(data).encode("utf-8"),
    headers=headers,
    method="POST",
)

try:
    with urllib.request.urlopen(req, timeout=1800) as resp:
        print(resp.read().decode("utf-8", errors="replace"))

except urllib.error.HTTPError as e:
    body = e.read().decode("utf-8", errors="replace")
    print("HTTP ERROR:", e.code, e.reason)
    print(body)

except Exception as e:
    print("ERROR:", repr(e))
PY
```

If the response is `401 Unauthorized` and mentions `auth_required`, tell the user:

```
The Codex bridge sent a verification code to the configured email. Please provide the 6-digit code within 5 minutes.
```

## Step 2: Verify email code

After the user provides the code, call:

```bash
python3 - <<'PY'
import json
import time
import urllib.request
import urllib.error

BASE_URL = "http://172.17.224.1:8765"
VERIFY_URL = BASE_URL + "/auth/verify"
TOKEN_FILE = "/tmp/hermes_codex_token.json"

code = "<USER_PROVIDED_CODE>"

data = {
    "code": code
}

req = urllib.request.Request(
    VERIFY_URL,
    data=json.dumps(data).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)

try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        print(raw)

        result = json.loads(raw)
        token = result["token"]
        expires_in = int(result.get("expires_in_seconds", 1800))

        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "token": token,
                    "expires_at": time.time() + expires_in - 10
                },
                f
            )

        print("TOKEN_SAVED")

except urllib.error.HTTPError as e:
    print("HTTP ERROR:", e.code, e.reason)
    print(e.read().decode("utf-8", errors="replace"))

except Exception as e:
    print("ERROR:", repr(e))
PY
```

If verification succeeds, retry the original `/codex` request with the saved token.

## Modes

For read-only repository inspection:

```json
{
  "mode": "read_only"
}
```

For code modification:

```json
{
  "mode": "edit"
}
```

Use `edit` only when the user asks Codex to modify files.

## Prompt construction

When sending a task to Codex, include:
- The user's exact request.
- The target repository or directory.
- Whether the task is read-only or edit.
- The expected output.
- Any constraints.

For read-only analysis, include:

```
Do not modify files. Only inspect the repository and return a concise result.
```

For code modification, include:

```
Modify the repository as needed. Keep changes minimal and explain what files were changed.
```

## Security rules

Strictly follow these rules:

1. Never call `codex` directly inside the Hermes container.
2. Never call `powershell.exe`, `cmd.exe`, `bash.exe`, `wsl.exe`, `git`, `npm`, `pip`, or arbitrary host commands to reach Codex.
3. Only call the HTTP bridge endpoints:
   - `/codex`
   - `/auth/verify`
   - `/auth/start`
   - `/health`
4. Never call arbitrary URLs.
5. Never use a workdir outside the bridge's allowed root.
6. Never expose or print the token unless debugging is explicitly requested by the user.
7. Store the token only in `/tmp/hermes_codex_token.json`.
8. If `/codex` returns 401, ask the user for the email verification code.
9. If `/auth/verify` says the code expired, trigger a new verification request and ask the user for the new code.
10. If the bridge returns 403 for workdir, report that the workdir is outside the allowed root.
11. If the bridge returns "workdir must be a git repo", tell the user to run `git init` in that directory.
12. Treat the bridge response as the Codex result.

## Error handling

### 401 Unauthorized

Meaning:

```
No valid token was provided.
The bridge has sent an email verification code.
```

Action:

```
Ask the user to provide the 6-digit email verification code within 5 minutes.
```

### 400 verification expired

Meaning:

```
The verification code was not used within 5 minutes.
```

Action:

```
Trigger a new verification request by calling /auth/start or /codex again without token.
```

### 403 Forbidden

Meaning:

```
Invalid verification code, invalid token, or workdir outside allowed root.
```

Action:

```
Read the response body and explain the specific reason.
```

### Connection refused

Meaning:

```
The Windows bridge is not running or the port is blocked.
```

Action:

Tell the user to start the bridge on Windows:

```powershell
uvicorn codex_server:app --host 0.0.0.0 --port 8765
```

### SMTP is not configured

Meaning:

```
The bridge cannot send verification email.
```

Action:

Tell the user to configure:

```powershell
$env:CODEX_VERIFY_EMAIL_TO="..."
$env:CODEX_SMTP_HOST="..."
$env:CODEX_SMTP_PORT="465"
$env:CODEX_SMTP_USER="..."
$env:CODEX_SMTP_PASSWORD="..."
$env:CODEX_SMTP_FROM="..."
```
