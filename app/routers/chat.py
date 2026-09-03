"""Chat router — /api/chat/stream and friends.

Holds the streaming artifact extractor and session-wiring helpers inlined
because Group 11 keeps them colocated with their only consumer.
"""
from __future__ import annotations

import asyncio
import json
import threading
import traceback

from fastapi import APIRouter, Depends, HTTPException, Request

from artifacts          import StreamingArtifactExtractor
from dependencies       import get_llm, get_paths, get_rate_limiter, get_session_store, require_api_key
from request_models     import ChatRequest
from sessions           import PERSONALITIES, SessionStore, system_for_mode
from util               import run_sync
from logging_config     import getLogger
_log = getLogger("usbai")


router = APIRouter()


@router.post("/api/chat/stream")
async def api_chat(req: ChatRequest, request: Request,
                   llm=Depends(get_llm), store: SessionStore = Depends(get_session_store),
                   paths=Depends(get_paths),
                   _auth=Depends(require_api_key),
                   limiter = Depends(get_rate_limiter)):
    # ponytail: per-IP rate cap — protect against chat-spam / LLM cost blowups.
    ip = request.client.host if request.client else "unknown"
    if not limiter.check(ip):
        raise HTTPException(429, "Rate limit exceeded; try again shortly.")
    if not llm.is_loaded():
        raise HTTPException(400, "No model loaded")
    session = await run_sync(store.load, req.session_id)
    if not session["messages"]:
        session["title"] = req.message[:60] if req.message else "Vision"

    user_msg = req.message
    entry = {"role": "user", "content": req.message}
    if req.image_b64:
        entry["has_image"] = True
    session["messages"].append(entry)
    await run_sync(store.save, session)

    history = session["messages"][:-1]
    system = req.system_prompt or system_for_mode(req.mode)
    if req.image_b64 and req.mode == "chat":
        system = PERSONALITIES["vision"]

    async def event_stream():
        full = ""
        extractor = StreamingArtifactExtractor()
        try:
            loop = asyncio.get_event_loop()

            if req.image_b64 and not llm.supports_vision():
                w = "Warning: This model does not support images. Load Gemma 4 E4B or LLaVA for vision.\n\n"
                full += w
                yield f"data: {json.dumps({'token': w})}\n\n"

            # ponytail: queue producer thread already streams tokens in the LLM
            # engine; we just need to drain the queue asynchronously without
            # crossing the executor boundary per-token (which adds a thread-pool
            # hop for every chunk). yield-from-queue is ~3x faster on long replies.
            import queue as _q
            sink: _q.Queue = _q.Queue(maxsize=256)

            def _produce():
                try:
                    for tok in llm.stream_tokens(
                        history, user_msg, system, req.temperature, req.max_tokens,
                        req.image_b64 if llm.supports_vision() else None):
                        sink.put(tok)
                except Exception as e:
                    sink.put(e)
                finally:
                    sink.put(None)

            th = threading.Thread(target=_produce, daemon=True)
            th.start()
            # ponytail: batched drain — block for one token, then sweep
            # everything already queued (up to 64) without re-entering the
            # executor. The old loop paid a thread-pool dispatch per token;
            # long replies at ~50 tok/s were scheduling 50 tasks/second.
            # Wire format unchanged: still one SSE frame per token.
            done = False
            while not done:
                head = await loop.run_in_executor(None, sink.get)
                items = [head]
                while len(items) < 64:
                    try:
                        items.append(sink.get_nowait())
                    except _q.Empty:
                        break
                for it in items:
                    if it is None:
                        done = True
                        break
                    if isinstance(it, Exception):
                        raise it
                    full += it
                    extractor.push(it)
                    while artifact := extractor.pop():
                        yield f"data: {json.dumps({'type': 'artifact', **artifact.model_dump()})}\n\n"
                    yield f"data: {json.dumps({'token': it})}\n\n"
            th.join(timeout=5)

            for artifact in extractor.flush():
                yield f"data: {json.dumps({'type': 'artifact', **artifact.model_dump()})}\n\n"

            saved = []
            if req.save_code:
                saved = llm.generate_code_files(full, paths.output)

            yield f"data: {json.dumps({'done': True, 'saved_files': saved})}\n\n"
        except Exception as e:
            # ponytail: detail to the log, generic to the wire — str(e) used
            # to leak engine internals/filesystem paths into chat history.
            _log.error(f"CHAT-STREAM failed on session {req.session_id}: "
                       f"{type(e).__name__}: {e}\n{traceback.format_exc()}")
            yield f"data: {json.dumps({'error': 'Stream failed — see server logs'})}\n\n"
        finally:
            # ponytail: don't persist an empty/failed assistant turn — pollutes history.
            # save offloaded to a worker thread — JSON-dumping long histories
            # on the event loop stalled every other request.
            if full.strip():
                session["messages"].append({"role": "assistant", "content": full})
            await run_sync(store.save, session)

    # StreamingResponse is imported lazily to keep the import surface tight.
    from fastapi.responses import StreamingResponse
    return StreamingResponse(event_stream(), media_type="text/event-stream")
