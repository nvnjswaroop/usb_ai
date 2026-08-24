import sys
import os

# Force UTF-8 output - prevents Windows cp1252 crash on any unicode chars
if hasattr(sys.stdout,"reconfigure"):
    try: sys.stdout.reconfigure(encoding="utf-8")
    except (OSError, ValueError): pass
if hasattr(sys.stderr,"reconfigure"):
    try: sys.stderr.reconfigure(encoding="utf-8")
    except (OSError, ValueError): pass

from pathlib import Path as _P
_APP_DIR = str(_P(__file__).parent)
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

import asyncio
import ipaddress
import json
import re
import secrets
import threading
import time
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel
from schemas import Artifact, AgentResult
import base64
from rate_limit import RateLimiter
from container import build_default as _build_default
from dependencies import get_api_key
from logging_config import setup_logging, getLogger

setup_logging()
_log = getLogger("usbai")

# ── Paths (legacy module-level aliases — kept so tests using
# `import main; main.HISTORY_DIR` keep working until Group 7 fixes them.) ──
USB_ROOT    = Path(__file__).parent.parent
MODELS_DIR  = USB_ROOT / "models"
HISTORY_DIR = USB_ROOT / "history"
OUTPUT_DIR  = USB_ROOT / "output"
WHISPER_DIR = USB_ROOT / "whisper_models"
UI_DIR      = Path(__file__).parent / "ui"

for _d in (MODELS_DIR, HISTORY_DIR, OUTPUT_DIR, WHISPER_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ── App + container ──────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Startup/shutdown hooks — run under ANY launcher (python main.py,
    uvicorn main:app, launch.bat), unlike the __main__ block below.

    ponytail: the bind-address fail-closed gate can't live here (uvicorn's
    CLI --host never reaches the app), so the authoritative LAN protection is
    the runtime client-IP guard in check_auth below. This lifespan adds the
    operator-visible banner + a clean index flush on shutdown.
    """
    key = get_api_key()
    if key:
        import hashlib
        _log.info(f"[AUTH] API key active (sha256: {hashlib.sha256(key.encode()).hexdigest()[:8]})")
    else:
        _log.warning("[AUTH] USB_API_KEY unset — /api/* open on loopback only; "
                     "non-loopback clients are rejected at runtime")
    yield
    store = getattr(_app.state, "session_store", None)
    if store is not None:
        try:
            store.flush_index()
        except Exception as e:  # noqa: BLE001 — shutdown must never raise
            _log.warning(f"SHUTDOWN: index flush failed: {e}")
    llm = getattr(_app.state.container, "llm", None)
    if llm is not None and hasattr(llm, "shutdown"):
        # ponytail: server backend owns a child process — always stop it.
        try:
            llm.shutdown()
        except Exception as e:  # noqa: BLE001
            _log.warning(f"SHUTDOWN: llama-server stop failed: {e}")


app = FastAPI(title="USB AI", lifespan=lifespan)
_container = _build_default()
app.state.container     = _container
app.state.session_store = None  # set in lifespan below, OR by tests
app.state.rate_limiter  = RateLimiter(max_per_minute=30)
app.state.load_lock     = threading.Lock()

# ponytail: a single SessionStore wraps the disk+index writes. Built early so
# routers sharing the import-side Have get_session_store() use the same instance.
from sessions import SessionStore  # noqa: E402
_DEFAULT_STORE = SessionStore.default(HISTORY_DIR)
app.state.session_store = _DEFAULT_STORE

# ponytail: optional Whisper preload — USB_AI_WARMUP_WHISPER=1 loads the model
# in a background thread at boot so the first /api/voice/transcribe skips the
# ~5s cold start. Off by default (not everyone uses voice).
if os.environ.get("USB_AI_WARMUP_WHISPER", "").lower() in ("1", "true", "yes", "on"):
    threading.Thread(
        target=_container.voice.warmup,
        kwargs={"whisper_dir": str(_container.paths.whisper)},
        daemon=True, name="whisper-warmup").start()

# CORS — restrict credentials + allowed headers
# ponytail: narrow to Content-Type + Authorization — X-Requested-With isn't
# needed and adds attack surface.
app.add_middleware(CORSMiddleware,
    allow_origins=["http://localhost:8080", "http://127.0.0.1:8080", "http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    # ponytail: DELETE added for /api/sessions/{sid} — without this, browsers
    # block the session-delete flow at the CORS preflight (works via curl, fails in UI).
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
    # ponytail: cache CORS preflight 10 min — avoids re-handshaking on every nav.
    max_age=600)

# ── Auth middleware ─────────────────────────────────────────────────────────────
# ponytail: this middleware is the SINGLE generic bearer gate. Route-specific
# hardening lives in declarative dependencies — require_key_always() on
# filesystem-mutating routes (/api/files/write|debug) fail-closes them even
# when USB_API_KEY is unset. /api/health is the explicit allow-list below —
# health probes must work for monitoring regardless of key config.
_AUTH_BYPASS_PATHS = ("/api/health",)


def _client_is_loopback(host: Optional[str]) -> bool:
    """True when the client IP is loopback (or unparseable — non-IP
    transports like unix sockets / test clients carry no routable address).

    ponytail: authoritative LAN guard. The __main__ bind gate can be bypassed
    by `uvicorn main:app --host 0.0.0.0` because the CLI host never reaches
    the app — but a remote CLIENT's IP is always visible at request time, so
    this check cannot be bypassed by any launcher.
    """
    if not host:
        return True
    h = host.lower()
    if h.startswith("::ffff:"):  # IPv4-mapped IPv6
        h = h[7:]
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return True


@app.middleware("http")
async def check_auth(request: Request, call_next):
    api_key = get_api_key()
    if request.url.path.startswith("/api/"):
        # ponytail: constant-time compare defeats timing leaks on localhost; swap for HMAC if shared.
        # ponytail: health probes bypass auth AND the LAN guard — remote
        # orchestrators must be able to probe liveness without a key.
        if any(request.url.path == p for p in _AUTH_BYPASS_PATHS):
            return await call_next(request)
        # ponytail: runtime fail-closed LAN gate (see _client_is_loopback).
        client_host = request.client.host if request.client else None
        if not api_key and not _client_is_loopback(client_host):
            return JSONResponse(
                {"detail": "LAN access requires USB_API_KEY — set it or bind to 127.0.0.1"},
                status_code=403)
        if api_key:
            provided = request.headers.get("Authorization", "")
            expected = f"Bearer {api_key}"
            if not secrets.compare_digest(provided, expected):
                return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    return await call_next(request)

# ── Pydantic moved to app/request_models.py (Group 3) ───────────────────────
# Re-export main-line names so tests like `from main import RenameRequest`
# keep working. Group 7 will move test imports properly.
from request_models import (  # noqa: F401
    LoadModelRequest, ChatRequest, FileReadRequest, FileWriteRequest,
    DebugFileRequest, PDFRequest, CodeRequest, SaveCodeRequest, PPTRequest,
    RenameRequest, SpeakRequest, AgentRequest, DiffRequest,
    ExportRequest, CalcRequest,
)
from request_models import MAX_BODY_SIZE  # noqa: E402

# ── Body-size middleware ───────────────────────────────────────────────────
# ponytail: rejects oversize JSON / form bodies before Pydantic parses them.
# Streaming uploads (/api/files/upload, /api/pdf/upload, /api/image/upload) use
# their own 64 KB chunk loop and don't carry a Content-Length, so they're not
# gated here. Anything with a Content-Length over MAX_BODY_SIZE is 413'd.
@app.middleware("http")
async def check_body_size(request: Request, call_next):
    cl = request.headers.get("content-length")
    if cl:
        try:
            if int(cl) > MAX_BODY_SIZE:
                return JSONResponse(
                    {"detail": f"Request body too large (max {MAX_BODY_SIZE} bytes)"},
                    status_code=413,
                )
        except ValueError:
            return JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)
    return await call_next(request)

# ── Global exception handler ───────────────────────────────────────────────
# ponytail: unhandled exceptions previously surfaced Starlette's plain 500
# (or raw str(e) where handlers re-raised). Uniform envelope + server-side
# traceback; the client never sees internals.
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    _log.error(f"UNHANDLED {type(exc).__name__} on {request.url.path}: "
               f"{exc}\n{traceback.format_exc()}")
    return JSONResponse({"detail": "Internal server error"}, status_code=500)

# ── Router registration ──────────────────────────────────────────────────────
from routers import system, sessions as sessions_router, chat, files, media, \
                     voice, ppt, calc, export as export_router, code as code_router, \
                     openai_compat
from routers.agent import is_agent_enabled

app.include_router(system.router)
app.include_router(sessions_router.router, tags=["sessions"])
app.include_router(chat.router,        tags=["chat"])
app.include_router(files.router,       tags=["files"])
app.include_router(media.router,       tags=["media"])
app.include_router(voice.router,       tags=["voice"])
app.include_router(ppt.router,         tags=["ppt"])
app.include_router(calc.router,        tags=["calc"])
app.include_router(export_router.router, tags=["export"])
# ponytail: OpenAI-compatible adapter — same auth/rate-limit as /api/chat/stream.
app.include_router(openai_compat.router, tags=["openai-compat"])

# ponytail: /api/code/run gated by USB_AI_AGENT_CODE=1 — 404 by absence.
app.include_router(code_router.router if code_router.is_code_run_enabled() else code_router.empty_router,
                   tags=["code"])

# ponytail: /api/agent/execute gated by USB_AI_AGENT=1 — 404 by absence.
if is_agent_enabled():
    from routers.agent import router as agent_router
    app.include_router(agent_router, tags=["agent"])

if __name__ == "__main__":
    import uvicorn
    # ponytail: localhost bind by default; USB_AI_HOST=0.0.0.0 opts into LAN exposure.
    _host = os.environ.get("USB_AI_HOST", "127.0.0.1").strip() or "127.0.0.1"
    _IS_LOCAL_HOST = _host.lower() in ("127.0.0.1", "::1", "localhost")
    _key = get_api_key()
    if _key:
        import hashlib
        print(f"[AUTH] API key set (sha256: {hashlib.sha256(_key.encode()).hexdigest()[:8]})")
    else:
        print(f"[AUTH] No USB_API_KEY set — endpoints are open (bound to {_host})")
        # ponytail: env-gated exec routes are their own auth when no key exists.
        # On loopback that's an explicit operator opt-in; warn so it's visible.
        from routers.agent import is_agent_enabled
        if is_code_run_enabled() or is_agent_enabled():
            print("[WARN] USB_AI_AGENT_CODE / USB_AI_AGENT is enabled WITHOUT "
                  "USB_API_KEY — code execution and agent endpoints are open "
                  "to anything that can reach this host.", flush=True)
    # ponytail: fail-closed startup. LAN exposure (any non-local host) without USB_API_KEY
    # would leave every /api/* route open to the network. Refuse to boot and tell the
    # operator exactly how to fix it: set USB_API_KEY (recommended for any LAN exposure)
    # OR bind to a loopback host by exporting USB_AI_HOST=127.0.0.1.
    if not _key and not _IS_LOCAL_HOST:
        print(
            "[FATAL] USB_API_KEY is not set but USB_AI_HOST={!r} is not a loopback address.\n"
            "        LAN exposure requires an API key — refusing to start with the API open.\n"
            "        Fix one of the following:\n"
            "          1. Set USB_API_KEY to a strong secret before launching.\n"
            "          2. Or set USB_AI_HOST=127.0.0.1 (loopback only, no LAN access).".format(_host),
            flush=True,
        )
        sys.exit(2)
    uvicorn.run("main:app", host=_host,
                port=int(os.environ.get("USB_AI_PORT", "8080")),
                reload=False)
