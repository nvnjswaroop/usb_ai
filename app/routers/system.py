"""System router — root HTML, status, models, model load + progress.

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

from dependencies   import get_paths, require_api_key, get_rate_limiter
from request_models import LoadModelRequest
from sessions       import PERSONALITIES


router = APIRouter()

# ponytail: per-process uptime anchor — set lazily on first /api/health call so it tracks process lifetime.
_health_started_at: float | None = None


def get_load_lock(request: Request) -> threading.Lock:
    return request.app.state.load_lock


def get_llm_via_request(request: Request):
    return request.app.state.container.llm


import threading  # noqa: E402  (kept below the helpers to mirror layout)


@router.get("/", response_class=HTMLResponse)
async def root(paths=Depends(get_paths)):
    hp = paths.ui / "chat.html"
    return HTMLResponse(
        hp.read_text(encoding="utf-8") if hp.exists() else "<h1>chat.html missing</h1>"
    )


@router.get("/api/status")
async def api_status(request: Request, paths=Depends(get_paths),
                     llm=Depends(get_llm_via_request),
                     _auth=Depends(require_api_key)):
    # ponytail: gated behind require_api_key per audit 2026-08-07 — previously
    # leaked current_model + *.gguf filenames + personality names with zero
    # auth. When USB_API_KEY is unset on loopback this still returns 200
    # (intentional — status needs to be readable by the local UI).
    return {
        "model_loaded":   llm.is_loaded(),
        "model_loading":  llm.is_loading(),
        "current_model":  llm.current_model,
        "vision_capable": llm.supports_vision(),
        "models_available": [f.name for f in sorted(paths.models.glob("*.gguf"))],
        "personalities":  list(PERSONALITIES.keys()),
    }


@router.get("/api/health")
async def api_health(paths=Depends(get_paths), llm=Depends(get_llm_via_request)):
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
    return {"models": [
        {"name": f.name, "size_mb": round(f.stat().st_size / (1024 * 1024), 1)}
        for f in sorted(paths.models.glob("*.gguf"))
    ]}


@router.get("/api/models/progress")
async def api_progress(request: Request, paths=Depends(get_paths),
                       llm=Depends(get_llm_via_request),
                       _auth=Depends(require_api_key),
                       limiter=Depends(get_rate_limiter)):
    ip = request.client.host if request.client else "unknown"
    if not limiter.check(ip):
        raise HTTPException(429, "Rate limit exceeded; try again shortly.")
    prog = llm.get_load_progress()
    try:
        logp = paths.root / "server.log"
        if logp.exists():
            lines = logp.read_text(errors="ignore").splitlines()
            for line in reversed(lines[-50:]):
                if any(k in line for k in
                       ["llm_load", "llama_model", "llama_new", "AVX",
                        "model size", "KV self", "llama_kv"]):
                    prog["log_line"] = line.strip()[:120]
                    break
    except (OSError, ValueError):
        pass
    return prog


@router.post("/api/models/load")
async def api_load(req: LoadModelRequest, request: Request,
                   llm=Depends(get_llm_via_request), lock=Depends(get_load_lock),
                   _auth=Depends(require_api_key),
                   limiter=Depends(get_rate_limiter)):
    """Model-load endpoint, gated by a lock stored in app.state.

    The lock closes the two-POST race from AGENTS.md that double-inits Llama.
    """
    ip = request.client.host if request.client else "unknown"
    if not limiter.check(ip):
        raise HTTPException(429, "Rate limit exceeded; try again shortly.")
    # ponytail: check-and-set under a lock — kills the two-POST race that double-inits Llama.
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
        raise HTTPException(500, str(e))
    finally:
        llm.set_loading(False)
        lock.release()
