"""OpenAI-compatible chat-completions adapter.

Bridges CyberMatrix (provider: usb_ai) and any OpenAI-shaped client to the
existing /api/chat/stream code path. Reuses LLMEngine.stream_tokens and
SlidingWindow exactly — no model-calling logic is duplicated.

Auth + rate-limit policy is inherited from /api/chat/stream:
  - USB_API_KEY env-var opt-in (open on loopback, fail-closed on LAN).
  - Per-IP rate-limit via the same process-wide RateLimiter.

Streaming: not implemented on this endpoint. stream=true returns 400.
The SSE path lives on /api/chat/stream already; bridging it to OpenAI
chunk format is a one-evening add when CyberMatrix needs it.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from dependencies import get_llm, get_paths, get_rate_limiter, require_api_key
from util import run_sync


router = APIRouter()


class OAIMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = ""
    messages: list[OAIMessage]
    max_tokens: int = Field(default=2048, ge=1, le=32768)
    temperature: float = Field(default=1.0, ge=0.0, le=2.0)
    stream: bool = False


@router.post("/api/v1/chat/completions")
async def oai_chat_completions(
    req: ChatCompletionRequest,
    request: Request,
    llm=Depends(get_llm),
    paths=Depends(get_paths),
    _auth=Depends(require_api_key),
    limiter=Depends(get_rate_limiter),
):
    # Auth + rate-limit policy mirrors /api/chat/stream verbatim.
    ip = request.client.host if request.client else "unknown"
    if not limiter.check(ip):
        raise HTTPException(429, "Rate limit exceeded; try again shortly.")
    if req.stream:
        # ponytail: deferred — /api/chat/stream already does SSE in our own
        # shape. OpenAI chunk format add is ~30 lines when CyberMatrix needs it.
        raise HTTPException(400, "streaming not yet supported on this endpoint; use /api/chat/stream")
    # ponytail: validate the cheapest input first so callers see a clear
    # "messages must be non-empty" rather than "no model loaded" when both
    # conditions hold.
    if not req.messages:
        raise HTTPException(400, "messages must be non-empty")
    if not llm.is_loaded():
        raise HTTPException(400, "No model loaded")

    # Translate OpenAI messages -> our (history, user_msg, system) split.
    # The last message is the user turn; everything before it is history.
    system_prompt = ""
    history: list[dict] = []
    for m in req.messages[:-1]:
        if m.role == "system":
            system_prompt = (system_prompt + "\n" + m.content).strip() if system_prompt else m.content
        else:
            history.append({"role": m.role, "content": m.content})
    user_msg = req.messages[-1].content

    # Reuse the same sliding-window + token-counting code path as /api/chat/stream.
    # llm._count is the internal counter used by SlidingWindow — pulling it here
    # means prompt_tokens/completion_tokens reflect the exact same math the
    # engine used to budget the response.
    prompt_tokens = llm._count(system_prompt) + sum(llm._count(m["content"]) + 6 for m in history) + llm._count(user_msg) + 6

    loop = asyncio.get_event_loop()
    try:
        completion = await loop.run_in_executor(
            None,
            lambda: "".join(llm.stream_tokens(history, user_msg, system_prompt,
                                              req.temperature, req.max_tokens)),
        )
    except RuntimeError as e:
        # ponytail: surface context-window overflow as 400 (caller's fault
        # — they sent too long a prompt for the loaded model's window) rather
        # than a 500. OpenAI's API does the same.
        msg = str(e).lower()
        if "context window" in msg or "n_ctx" in msg:
            raise HTTPException(400, f"prompt exceeds loaded model context window: {e}")
        raise
    completion_tokens = llm._count(completion)
    model_name = req.model or llm.current_model or "usb-ai"

    return {
        "id":      f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object":  "chat.completion",
        "created": int(time.time()),
        "model":   model_name,
        "choices": [{
            "index":         0,
            "message":       {"role": "assistant", "content": completion},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens":     prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens":      prompt_tokens + completion_tokens,
        },
    }


@router.get("/api/v1/models")
async def oai_models(
    request: Request,
    paths=Depends(get_paths),
    _auth=Depends(require_api_key),
    limiter=Depends(get_rate_limiter),
):
    ip = request.client.host if request.client else "unknown"
    if not limiter.check(ip):
        raise HTTPException(429, "Rate limit exceeded; try again shortly.")

    def _scan():
        # Reshape paths.models.glob("*.gguf") -> OpenAI list-of-models shape.
        return [{"id": f.name, "object": "model",
                 "owned_by": "usb-ai",
                 "size_mb": round(f.stat().st_size / (1024 * 1024), 1)}
                for f in sorted(paths.models.glob("*.gguf"))]

    # ponytail: stat() per model file off the event loop — mirrors /api/models.
    return {"object": "list", "data": await run_sync(_scan)}
