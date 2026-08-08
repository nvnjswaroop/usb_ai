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
import json
import re
import secrets
import threading
import time
import traceback
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
from logging_config import setup_logging

setup_logging()

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
app = FastAPI(title="USB AI")
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
_RAW = os.environ.get("USB_API_KEY", "").strip()
_API_KEY = _RAW or None

# ponytail: filesystem-mutating routes must NEVER be reachable without a key,
# even if other /api/* routes are open. The middleware short-circuits on missing
# keys for these prefixes before the generic "no key = open" branch, so LAN
# exposure without USB_API_KEY cannot reach them. /api/health and
# /api/models/progress are explicit allow-lists below — health probes and
# progress polling must work for monitoring and for the UI's loading spinner,
# even when USB_API_KEY is configured.
_SENSITIVE_PATH_PREFIXES = ("/api/files/write", "/api/files/debug")
_AUTH_BYPASS_PATHS = ("/api/health",)

@app.middleware("http")
async def check_auth(request: Request, call_next):
    if request.url.path.startswith("/api/") and not _API_KEY:
        # ponytail: fail-closed on sensitive paths even when other endpoints are open.
        if any(request.url.path.startswith(p) for p in _SENSITIVE_PATH_PREFIXES):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        # ponytail: constant-time compare defeats timing leaks on localhost; swap for HMAC if shared.
        # ponytail: health probes bypass auth — orchestrators + the UI's loading spinner
        # both need /api/health to respond 200 regardless of key config.
        if any(request.url.path == p for p in _AUTH_BYPASS_PATHS):
            return await call_next(request)
        provided = request.headers.get("Authorization", "")
        expected = f"Bearer {_API_KEY}" if _API_KEY else ""
        if _API_KEY and not secrets.compare_digest(provided, expected):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    return await call_next(request)

# ── Pydantic moved to app/request_models.py (Group 3) ───────────────────────
# Re-export main-line names so tests like `from main import RenameRequest`
# keep working. Group 7 will move test imports properly.
from request_models import (  # noqa: F401
    LoadModelRequest, ChatRequest, FileReadRequest, FileWriteRequest,
    DebugFileRequest, PDFRequest, CodeRequest, SaveCodeRequest, PPTRequest,
    RenameRequest, SpeakRequest, AgentRequest, SearchRequest, DiffRequest,
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

# ── Personalities live in app/sessions.py (Group 3) ──────────────────────────
from sessions import PERSONALITIES  # noqa: F401

# ── Legacy session helpers (Group 3) ─────────────────────────────────────
# Back-compat shims for test_security.py which still mutates main.HISTORY_DIR
# and calls main._save directly. Group 7 will move test imports properly;
# until then these aliases let Group 3 land without editing tests/.
def _session_index_path():
    """Index path follows HISTORY_DIR — recomputed each call so tests that
    swap main.HISTORY_DIR mid-test see the right filesystem location.
    """
    return HISTORY_DIR / "_index.json"

def _sp(sid): return HISTORY_DIR / f"{sid}.json"
def _load(sid):
    p = _sp(sid)
    if not p.exists():
        return {"id":sid,"title":"New Chat","messages":[],
                "created":time.time(),"updated":time.time()}
    return json.loads(p.read_text(encoding="utf-8"))

def _save(data):
    """Legacy save shim — delegates to SessionStore owned by app.state.

    Tests overwrite HISTORY_DIR before calling _save; we mirror that into
    the SessionStore so both legacy and modern paths see the same on-disk data.
    """
    if HISTORY_DIR != app.state.session_store.history_dir:
        app.state.session_store = SessionStore.default(HISTORY_DIR)
    app.state.session_store.save(data)

def _sys(mode="chat"): return PERSONALITIES.get(mode, PERSONALITIES["chat"])

# Back-compat attribute removed — TestSessionIndex now uses SessionStore directly.

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
    if _API_KEY:
        import hashlib
        print(f"[AUTH] API key set (sha256: {hashlib.sha256(_API_KEY.encode()).hexdigest()[:8]})")
    else:
        print(f"[AUTH] No USB_API_KEY set — endpoints are open (bound to {_host})")
    # ponytail: fail-closed startup. LAN exposure (any non-local host) without USB_API_KEY
    # would leave every /api/* route open to the network. Refuse to boot and tell the
    # operator exactly how to fix it: set USB_API_KEY (recommended for any LAN exposure)
    # OR bind to a loopback host by exporting USB_AI_HOST=127.0.0.1.
    if not _API_KEY and not _IS_LOCAL_HOST:
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
