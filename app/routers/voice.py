"""Voice router — text-to-speech, voice listing, audio transcription."""
from __future__ import annotations

import asyncio
import secrets
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from dependencies   import get_paths, get_voice_tool, get_rate_limiter, require_api_key
from request_models import SpeakRequest


router = APIRouter()


@router.post("/api/voice/speak")
async def api_speak(req: SpeakRequest, request: Request,
                    voice_tool=Depends(get_voice_tool),
                    limiter=Depends(get_rate_limiter),
                    _auth=Depends(require_api_key)):
    ip = request.client.host if request.client else "unknown"
    if not limiter.check(ip):
        raise HTTPException(429, "Rate limit exceeded; try again shortly.")
    return await asyncio.get_event_loop().run_in_executor(
        None, voice_tool.speak, req.text, req.rate, req.volume, req.voice_id)


@router.get("/api/voice/voices")
async def api_voices(request: Request,
                    voice_tool=Depends(get_voice_tool),
                    _auth=Depends(require_api_key),
                    limiter=Depends(get_rate_limiter)):
    ip = request.client.host if request.client else "unknown"
    if not limiter.check(ip):
        raise HTTPException(429, "Rate limit exceeded; try again shortly.")
    return voice_tool.get_voices()


@router.post("/api/voice/transcribe")
async def api_transcribe(request: Request,
                         paths=Depends(get_paths), voice_tool=Depends(get_voice_tool),
                         limiter=Depends(get_rate_limiter),
                         _auth=Depends(require_api_key),
                         file: UploadFile = File(...)):
    ip = request.client.host if request.client else "unknown"
    if not limiter.check(ip):
        raise HTTPException(429, "Rate limit exceeded; try again shortly.")
    suffix = Path(file.filename).suffix or ".webm"
    # ponytail: token suffix — same-second uploads must not overwrite each other.
    tmp = paths.output / f"audio_{int(time.time())}_{secrets.token_hex(4)}{suffix}"
    # ponytail: stream-to-disk with the shared 10MB cap (matches files/media
    # routers). `await file.read()` here was the last upload buffering the whole
    # body in RAM — chunked encoding also dodges the Content-Length middleware.
    remaining = 10 * 1024 * 1024
    try:
        with open(tmp, "wb") as f:
            while chunk := await file.read(64 * 1024):
                remaining -= len(chunk)
                if remaining < 0:
                    raise HTTPException(413, "Audio too large (10MB max)")
                f.write(chunk)
        return await asyncio.get_event_loop().run_in_executor(
            None, voice_tool.transcribe, str(tmp), "base", str(paths.whisper)
        )
    finally:
        # ponytail: unlink in finally — a transcription error used to orphan
        # the temp audio file (unlink previously ran only on the success path).
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
