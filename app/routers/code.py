"""Code execution router — /api/code/run and /api/code/save.

The run route is gated by USB_AI_AGENT_CODE=1 at registration time (not at
request time) — see app/main.py. When the env var is unset, /api/code/run
returns 404 rather than 401 because a 401 leaks existence to LAN scanners.
"""
from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, Depends, HTTPException, Request

from dependencies   import get_code_tool, get_rate_limiter, get_vscode_tool, require_api_key
from request_models import CodeRequest, SaveCodeRequest


# A separate router used ONLY when the env gate is open. Computed at import
# time: include_router() in main.py wires this in conditionally.
router = APIRouter()

# When the env gate is closed, mount an empty router under the same prefix so
# the 404 stays consistent (and prevent accidental re-mounting of /api/code/run).
empty_router = APIRouter()


@router.post("/api/code/run")
async def api_run(req: CodeRequest, request: Request,
                  code_tool=Depends(get_code_tool),
                  limiter=Depends(get_rate_limiter),
                  _auth=Depends(require_api_key)):
    # ponytail: per-IP rate cap on code-exec — sandbox or not, runaway cost is bad.
    # ponytail: require_api_key is defense-in-depth — when USB_API_KEY is set this
    # route enforces Bearer auth like every other endpoint, instead of relying
    # solely on the USB_AI_AGENT_CODE env gate at mount time.
    ip = request.client.host if request.client else "unknown"
    if not limiter.check(ip):
        raise HTTPException(429, "Rate limit exceeded; try again shortly.")
    return await asyncio.get_event_loop().run_in_executor(
        None, code_tool.run_python, req.code)


@router.post("/api/code/save")
async def api_save_code(req: SaveCodeRequest, request: Request,
                        vscode_tool=Depends(get_vscode_tool),
                        limiter=Depends(get_rate_limiter),
                        _auth=Depends(require_api_key)):
    ip = request.client.host if request.client else "unknown"
    if not limiter.check(ip):
        raise HTTPException(429, "Rate limit exceeded; try again shortly.")
    return vscode_tool.save_and_open(req.code, req.filename, req.language)


def is_code_run_enabled() -> bool:
    """USB_AI_AGENT_CODE=1 gate. False → /api/code/run returns 404."""
    return os.environ.get("USB_AI_AGENT_CODE", "").lower() in ("1", "true", "yes", "on")
