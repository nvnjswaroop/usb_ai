"""System router â€” root HTML, status, models, model load + progress.

Model load is a singleton mutation (one engine, one lock). The route itself
lives here but pulls the load_lock + engine straight from app.state via a
Request-bound dependency.
"""
from __future__ import annotations

import asyncio
import time
import traceback
from pathlib import Path
from shutil import disk_usage

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses          import HTMLResponse

from dependencies   import get_paths, require_api_key, get_rate_limiter, get_llm
from request_models import LoadModelRequest
from sessions       import PERSONALITIES
from util           import run_sync
from logging_config import getLogger
_log = getLogger("usbai")


router = APIRouter()

# ponytail: per-process uptime anchor â€” set lazily on first /api/health call so it tracks process lifetime.
_health_started_at: float | None = None


def get_load_lock(request: Request) -> threading.Lock:
    return request.app.state.load_lock


import threading  # noqa: E402  (kept below the helpers to mirror layout)


@router.get("/", response_class=HTMLResponse)
async def root(paths=Depends(get_paths)):
    hp = paths.ui / "chat.html"
    return HTMLResponse(
        hp.read_text(encoding="utf-8") if hp.exists() else "<h1>chat.html missing</h1>"
    )


@router.get("/api/status")
async def api_status(request: Request, paths=Depends(get_paths),
                     llm=Depends(get_llm),
                     _auth=Depends(require_api_key)):
    # ponytail: gated behind require_api_key per audit 2026-08-07 â€” previously
    # leaked current_model + *.gguf filenames + personality names with zero
    # auth. When USB_API_KEY is unset on loopback this still returns 200
    # (intentional â€” status needs to be readable by the local UI).
    return {
        "model_loaded":   llm.is_loaded(),
        "model_loading":  llm.is_loading(),
        "current_model":  llm.current_model,
        "vision_capable": llm.supports_vision(),
        "models_available": [f.name for f in sorted(paths.models.glob("*.gguf"))],
        "personalities":  list(PERSONALITIES.keys()),
    }


@router.get("/api/health")
async def api_health(paths=Depends(get_paths), llm=Depends(get_llm)):
    """Ops health endpoint. Must NOT trigger any model load.

    Designed for monitoring right after process startup, before the user has
    picked a model. Returns liveness + disk telemetry only.
    """
    global _health_started_at
    if _health_started_at is None:
        _health_started_at = time.time()
    out: Path = paths.output
    try:
        free_bytes = disk_usage(out.anchor if out.anchor else out).free
    except (OSError, ValueError):
        free_bytes = -1
    return {
        "model_loaded":   llm.is_loaded(),
        "current_model":  llm.current_model,
        "disk_free_mb":   round(free_bytes / (1024 * 1024), 1) if free_bytes >= 0 else None,
        "uptime_sec":     round(time.time() - _health_started_at, 1),
    }


@router.get("/api/models")
async def api_models(request: Request, paths=Depends(get_paths),
                     _auth=Depends(require_api_key),
                     limiter=Depends(get_rate_limiter)):
    ip = request.client.host if request.client else "unknown"
    if not limiter.check(ip):
        raise HTTPException(429, "Rate limit exceeded; try again shortly.")

    def _scan():
        return [{"name": f.name, "size_mb": round(f.stat().st_size / (1024 * 1024), 1)}
                for f in sorted(paths.models.glob("*.gguf"))]

    # ponytail: stat() per model file off the event loop (multi-GB .gguf dirs).
    return {"models": await run_sync(_scan)}


@router.get("/api/models/progress")
async def api_progress(request: Request, paths=Depends(get_paths),
                       llm=Depends(get_llm),
                       _auth=Depends(require_api_key),
                       limiter=Depends(get_rate_limiter)):
    ip = request.client.host if request.client else "unknown"
    if not limiter.check(ip):
        raise HTTPException(429, "Rate limit exceeded; try again shortly.")
    prog = llm.get_load_progress()

    def _tail_log():
        # ponytail: reading server.log ran on the event loop; a slow disk
        # stalled every request during model load. Returns last relevant
        # line or "" — merged into prog by the caller.
        try:
            logp = paths.root / "server.log"
            if logp.exists():
                lines = logp.read_text(errors="ignore").splitlines()
                for line in reversed(lines[-50:]):
                    if any(k in line for k in
                           ["llm_load", "llama_model", "llama_new", "AVX",
                            "model size", "KV self", "llama_kv"]):
                        return line.strip()[:120]
        except (OSError, ValueError):
            pass
        return ""

    log_line = await run_sync(_tail_log)
    if log_line:
        prog["log_line"] = log_line
    return prog


@router.post("/api/models/load")
async def api_load(req: LoadModelRequest, request: Request,
                   llm=Depends(get_llm), lock=Depends(get_load_lock),
                   _auth=Depends(require_api_key),
                   limiter=Depends(get_rate_limiter)):
    """Model-load endpoint, gated by a lock stored in app.state.

    The lock closes the two-POST race from AGENTS.md that double-inits Llama.
    """
    ip = request.client.host if request.client else "unknown"
    if not limiter.check(ip):
        raise HTTPException(429, "Rate limit exceeded; try again shortly.")
    # ponytail: check-and-set under a lock â€” kills the two-POST race that double-inits Llama.
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "Already loading.")
    llm.set_loading(True)
    try:
        await asyncio.get_event_loop().run_in_executor(
            None, llm.load_model_sync,
            req.model_name, req.n_ctx, req.n_threads, req.n_gpu_layers)
        return {"status": "ok", "model": req.model_name,
                "vision": llm.supports_vision()}
    except Exception as e:
        # ponytail: str(e) used to reach the client — llama.cpp errors leak
        # filesystem paths and library internals. Full detail goes to the log;
        # the caller gets a generic 500.
        _log.error(f"model load failed: {e}\n{traceback.format_exc()}")
        raise HTTPException(500, "Model load failed — see server logs")
    finally:
        llm.set_loading(False)
        lock.release()
