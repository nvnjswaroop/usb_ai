"""PPT (PowerPoint) router — JSON-driven generation + download."""
from __future__ import annotations

import json

from logging_config import getLogger
_log = getLogger("usbai")

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from dependencies   import get_llm, get_paths, get_ppt_tool, get_rate_limiter, require_api_key
from request_models import PPTRequest


router = APIRouter()


@router.post("/api/ppt/generate")
async def api_ppt(req: PPTRequest, request: Request,
                  llm=Depends(get_llm), paths=Depends(get_paths),
                  ppt_tool=Depends(get_ppt_tool),
                  limiter=Depends(get_rate_limiter),
                  _auth=Depends(require_api_key)):
    ip = request.client.host if request.client else "unknown"
    if not limiter.check(ip):
        raise HTTPException(429, "Rate limit exceeded; try again shortly.")
    if not llm.is_loaded():
        raise HTTPException(400, "No model")
    prompt = (
        f"Create a PowerPoint about: {req.topic}\nSlides: {req.num_slides}\n"
        + (f"Extra: {req.extra_instructions}\n" if req.extra_instructions else "")
        + 'Return ONLY JSON: {"title":"Title","slides":[{"slide_number":1,'
        + '"title":"T","bullet_points":["A","B","C"],"speaker_notes":"N"}]}\n'
        + f"Exactly {req.num_slides} slides, 3-5 bullets each."
    )
    try:
        raw_json = llm.generate_json(prompt)
        slide_data = json.loads(raw_json)
    except json.JSONDecodeError as e:
        # ponytail: log the raw LLM output so a bad-JSON failure is debuggable, not a silent 500.
        _log.warning(f"PPT: Bad JSON from LLM ({e}): {raw_json[:500]!r}")
        raise HTTPException(500, f"Bad JSON: {e}")
    try:
        filename, _ = ppt_tool.create_ppt(slide_data, req.style)
    except Exception as e:
        raise HTTPException(500, f"PPT failed: {e}")
    return {"status": "ok", "filename": filename, "title": slide_data.get("title", "")}


@router.get("/api/ppt/download/{filename}")
async def api_ppt_dl(filename: str, request: Request, paths=Depends(get_paths),
                     _auth=Depends(require_api_key),
                     limiter=Depends(get_rate_limiter)):
    ip = request.client.host if request.client else "unknown"
    if not limiter.check(ip):
        raise HTTPException(429, "Rate limit exceeded; try again shortly.")
    from util import safe_filename as _safe_filename  # dedup: shared with files/media
    fp = paths.output / _safe_filename(filename)
    if not fp.exists():
        raise HTTPException(404, "Not found")
    return FileResponse(
        str(fp),
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )
