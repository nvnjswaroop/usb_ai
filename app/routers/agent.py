"""Agent router — /api/agent/execute (autonomous tool orchestration).

Gated by USB_AI_AGENT=1 at registration time. When unset, /api/agent/execute
returns 404 — a 401 leaks existence to LAN scanners.
"""
from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, Depends, HTTPException, Request

from dependencies   import get_agent_tool, get_llm, get_rate_limiter, require_api_key
from request_models import AgentRequest
from schemas        import AgentResult


router = APIRouter()


@router.post("/api/agent/execute")
async def api_agent_execute(req: AgentRequest, request: Request,
                            llm=Depends(get_llm),
                            agent_tool=Depends(get_agent_tool),
                            limiter=Depends(get_rate_limiter),
                            _auth=Depends(require_api_key)):
    """Run an autonomous agent loop.

    Rate-limited per IP (Group 5): same 30/min budget as /api/code/run.
    ponytail: require_api_key is defense-in-depth — when USB_API_KEY is set
    this route enforces Bearer auth like every other endpoint, instead of
    relying solely on the USB_AI_AGENT env gate at mount time.
    """
    ip = request.client.host if request.client else "unknown"
    if not limiter.check(ip):
        raise HTTPException(429, "Rate limit exceeded; try again shortly.")
    if not llm.is_loaded():
        raise HTTPException(400, "No model loaded")
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, lambda: agent_tool.execute_task(req.task, llm, req.max_steps))
    return result.model_dump()


def is_agent_enabled() -> bool:
    """USB_AI_AGENT=1 gate. False → /api/agent/execute returns 404."""
    return os.environ.get("USB_AI_AGENT", "").lower() in ("1", "true", "yes", "on")
