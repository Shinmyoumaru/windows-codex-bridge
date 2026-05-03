from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
import subprocess
import os
import traceback
import secrets
import time
import smtplib
import ssl
from email.message import EmailMessage
from typing import Optional


app = FastAPI()


# =========================
# 基础配置
# =========================

ALLOWED_ROOT = r"F:\hermes_safe"

CODEX_BIN = r"C:\Users\Maruko\AppData\Roaming\npm\codex.cmd"

DEFAULT_MODEL = "gpt-5.5"

TOKEN_TTL_SECONDS = 30 * 60       # token 有效期：30 分钟
VERIFY_CODE_TTL_SECONDS = 5 * 60  # 验证码有效期：5 分钟
EMAIL_COOLDOWN_SECONDS = 60       # 防止频繁刷邮件：60 秒内不重复发送


# =========================
# 邮件配置
# 推荐通过环境变量配置
# =========================

VERIFY_EMAIL_TO = os.environ.get("CODEX_VERIFY_EMAIL_TO", "your_email@example.com")

SMTP_HOST = os.environ.get("CODEX_SMTP_HOST", "smtp.qq.com")
SMTP_PORT = int(os.environ.get("CODEX_SMTP_PORT", "465"))

SMTP_USER = os.environ.get("CODEX_SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("CODEX_SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("CODEX_SMTP_FROM", SMTP_USER)


# =========================
# 内存状态
# 注意：服务重启后 token 和验证码都会失效
# =========================

pending_code: Optional[str] = None
pending_code_expires_at: float = 0
last_email_sent_at: float = 0

valid_tokens = {}  # token -> expires_at


class CodexReq(BaseModel):
    prompt: str
    workdir: str = ALLOWED_ROOT
    mode: str = "read_only"  # read_only | edit


class VerifyReq(BaseModel):
    code: str


def now() -> float:
    return time.time()


# =========================
# 路径安全检查
# =========================

def safe_workdir(path: str) -> str:
    full = os.path.abspath(path)
    root = os.path.abspath(ALLOWED_ROOT)

    full_norm = os.path.normcase(full)
    root_norm = os.path.normcase(root)

    try:
        common = os.path.commonpath([full_norm, root_norm])
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail=f"workdir not allowed: {full}"
        )

    if common != root_norm:
        raise HTTPException(
            status_code=403,
            detail=f"workdir not allowed: {full}"
        )

    if not os.path.isdir(full):
        raise HTTPException(
            status_code=400,
            detail=f"workdir does not exist: {full}"
        )

    if not os.path.isdir(os.path.join(full, ".git")):
        raise HTTPException(
            status_code=400,
            detail="workdir must be a git repo"
        )

    return full


# =========================
# 邮件发送
# =========================

def send_verify_email(code: str) -> None:
    if not SMTP_USER or not SMTP_PASSWORD:
        raise HTTPException(
            status_code=500,
            detail=(
                "SMTP is not configured. Please set "
                "CODEX_SMTP_USER and CODEX_SMTP_PASSWORD."
            )
        )

    msg = EmailMessage()
    msg["Subject"] = "Hermes Codex Bridge Verification Code"
    msg["From"] = SMTP_FROM
    msg["To"] = VERIFY_EMAIL_TO

    msg.set_content(
        f"""Your Hermes Codex verification code is:

{code}

This code will expire in 5 minutes.

If you did not request this, you can ignore this email.
"""
    )

    context = ssl.create_default_context()

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context) as server:
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)


def start_verification() -> None:
    global pending_code, pending_code_expires_at, last_email_sent_at

    current = now()

    if current - last_email_sent_at < EMAIL_COOLDOWN_SECONDS:
        return

    code = f"{secrets.randbelow(1_000_000):06d}"

    pending_code = code
    pending_code_expires_at = current + VERIFY_CODE_TTL_SECONDS
    last_email_sent_at = current

    send_verify_email(code)


# =========================
# token 逻辑
# =========================

def cleanup_tokens() -> None:
    current = now()
    expired = [t for t, exp in valid_tokens.items() if exp <= current]
    for t in expired:
        valid_tokens.pop(t, None)


def extract_token(request: Request) -> Optional[str]:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()

    token = request.headers.get("x-codex-token") or request.headers.get("X-Codex-Token")
    if token:
        return token.strip()

    return None


def verify_token(request: Request) -> bool:
    cleanup_tokens()

    token = extract_token(request)
    if not token:
        return False

    exp = valid_tokens.get(token)
    if not exp:
        return False

    if exp <= now():
        valid_tokens.pop(token, None)
        return False

    return True


def require_token_or_send_email(request: Request) -> None:
    if verify_token(request):
        return

    start_verification()

    raise HTTPException(
        status_code=401,
        detail={
            "error": "auth_required",
            "message": (
                "No valid token was provided. "
                "A verification code has been sent to the configured email address. "
                "Verify within 5 minutes using /auth/verify."
            ),
            "verify_endpoint": "/auth/verify",
            "code_ttl_seconds": VERIFY_CODE_TTL_SECONDS,
            "token_ttl_seconds": TOKEN_TTL_SECONDS,
        },
    )


def safe_decode(data: bytes) -> str:
    if not data:
        return ""

    for enc in ("utf-8", "gbk", "cp936", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue

    return data.decode("utf-8", errors="replace")


# =========================
# API
# =========================

@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "windows-codex-bridge",
        "allowed_root": ALLOWED_ROOT,
        "default_model": DEFAULT_MODEL,
    }


@app.post("/auth/start")
def auth_start():
    start_verification()
    return {
        "ok": True,
        "message": "Verification code sent.",
        "code_ttl_seconds": VERIFY_CODE_TTL_SECONDS,
    }


@app.post("/auth/verify")
def auth_verify(req: VerifyReq):
    global pending_code, pending_code_expires_at

    if not pending_code:
        raise HTTPException(
            status_code=400,
            detail="No verification code is pending. Please request /auth/start first."
        )

    if now() > pending_code_expires_at:
        pending_code = None
        pending_code_expires_at = 0
        raise HTTPException(
            status_code=400,
            detail="Verification code expired. Please request a new code."
        )

    if req.code.strip() != pending_code:
        raise HTTPException(
            status_code=403,
            detail="Invalid verification code."
        )

    token = secrets.token_urlsafe(32)
    valid_tokens[token] = now() + TOKEN_TTL_SECONDS

    pending_code = None
    pending_code_expires_at = 0

    return {
        "ok": True,
        "token": token,
        "expires_in_seconds": TOKEN_TTL_SECONDS,
        "message": "Verification succeeded. Codex write access is enabled for 30 minutes.",
    }


@app.post("/codex")
def run_codex(req: CodexReq, request: Request):
    # 所有 /codex 请求都需要 token。
    # 没有 token 时，会自动发送验证码邮件。
    require_token_or_send_email(request)

    workdir = safe_workdir(req.workdir)

    if not os.path.exists(CODEX_BIN):
        raise HTTPException(
            status_code=500,
            detail=f"codex executable not found: {CODEX_BIN}"
        )

    if req.mode == "read_only":
        prompt = (
            f"{req.prompt.strip()} "
            "This is a read-only task: inspect only, do not modify files, and report the result."
        )

        cmd = [
            CODEX_BIN,
            "exec",
            "--model", DEFAULT_MODEL,
            "--sandbox", "read-only",
            prompt,
        ]

    elif req.mode == "edit":
        prompt = (
            f"{req.prompt.strip()} "
            "Execute the requested file changes directly in the current repository. "
            "Do not merely acknowledge. Keep changes minimal and summarize changed files."
        )

        cmd = [
            CODEX_BIN,
            "exec",
            "--model", DEFAULT_MODEL,
            "--sandbox", "workspace-write",
            prompt,
        ]

    else:
        raise HTTPException(
            status_code=400,
            detail="mode must be read_only or edit"
        )

    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"

        result = subprocess.run(
            cmd,
            cwd=workdir,
            text=False,
            capture_output=True,
            timeout=1800,
            shell=False,
            env=env,
        )

        return {
            # for debug
            # "cmd": cmd,
            # "cwd": workdir,
            # "received_prompt": req.prompt,
            # "effective_prompt": prompt,
            "returncode": result.returncode,
            "stdout": safe_decode(result.stdout),
            "stderr": safe_decode(result.stderr),
        }

    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=504,
            detail="Codex execution timed out."
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={
                "error": repr(e),
                "traceback": traceback.format_exc(),
            },
        )