"""Media router — PDF + image upload/extract endpoints.

All three routes stream to disk with a 10MB cap before invoking the relevant
tool — matches the same RAM-OOM protection /api/files/upload uses.
"""
from __future__ import annotations

import secrets
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from dependencies   import get_image_tool, get_paths, get_pdf_tool, get_rate_limiter, require_api_key
from request_models import PDFRequest
from util           import safe_filename as _safe_filename  # dedup: shared with files/ppt


router = APIRouter()


@router.post("/api/pdf/extract")
async def api_pdf(req: PDFRequest, request: Request,
                  pdf_tool=Depends(get_pdf_tool),
                  _auth=Depends(require_api_key),
                  limiter=Depends(get_rate_limiter)):
    ip = request.client.host if request.client else "unknown"
    if not limiter.check(ip):
        raise HTTPException(429, "Rate limit exceeded; try again shortly.")
    return pdf_tool.extract_text(req.path)


@router.post("/api/pdf/upload")
async def api_pdf_upload(request: Request,
                         paths=Depends(get_paths), pdf_tool=Depends(get_pdf_tool),
                         _auth=Depends(require_api_key),
                         limiter=Depends(get_rate_limiter),
                         file: UploadFile = File(...)):
    ip = request.client.host if request.client else "unknown"
    if not limiter.check(ip):
        raise HTTPException(429, "Rate limit exceeded; try again shortly.")
    # ponytail: unique temp name — the old dest was output/<safe_name>.pdf,
    # and the finally-unlink DELETED any pre-existing file the user had with
    # that name (upload "report.pdf" destroyed output/report.pdf).
    # ponytail: stream-to-disk with the same 10MB cap as /api/files/upload — kills RAM-OOM on large PDFs.
    safe_name = _safe_filename(file.filename)
    if Path(safe_name).suffix.lower() != ".pdf":
        raise HTTPException(400, "Expected a .pdf file")
    dest = paths.output / f"pdf_{int(time.time())}_{secrets.token_hex(4)}.pdf"
    remaining = 10 * 1024 * 1024
    completed = False
    try:
        with open(dest, "wb") as f:
            while chunk := await file.read(64 * 1024):
                remaining -= len(chunk)
                if remaining < 0:
                    raise HTTPException(413, "File too large (10MB max)")
                f.write(chunk)
        completed = True
    finally:
        # ponytail: symmetric partial-file cleanup — matches /api/files/upload;
        # the old code unlinked only on the size-cap path, so a mid-write
        # OSError (disk full) orphaned the partial file.
        if not completed:
            try:
                dest.unlink(missing_ok=True)
            except OSError:
                pass
    try:
        return pdf_tool.extract_text(str(dest))
    finally:
        # ponytail: temp upload, clean up after extraction
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass


@router.post("/api/image/upload")
async def api_image(request: Request,
                    paths=Depends(get_paths), image_tool=Depends(get_image_tool),
                    _auth=Depends(require_api_key),
                    limiter=Depends(get_rate_limiter),
                    file: UploadFile = File(...)):
    ip = request.client.host if request.client else "unknown"
    if not limiter.check(ip):
        raise HTTPException(429, "Rate limit exceeded; try again shortly.")
    # ponytail: 10MB streamed cap — image_tool also enforces post-save, but this kills RAM-OOM at the route.
    # Single growing bytearray instead of chunk-list + b"".join — halves the
    # peak allocation (the join used to momentarily hold 2x the cap).
    remaining = 10 * 1024 * 1024
    buf = bytearray()
    while chunk := await file.read(64 * 1024):
        remaining -= len(chunk)
        if remaining < 0:
            raise HTTPException(413, "Image too large (10MB max)")
        buf.extend(chunk)
    result = image_tool.save_upload(buf, file.filename)
    if result["status"] == "ok":
        b64 = image_tool.read_for_llm(result["path"])
        result["base64_url"] = b64.get("base64_url", "")
    return result
