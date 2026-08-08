"""Files router — read/write/browse/upload/debug/preview + outputs.

The chat.html preview and outputs endpoints live here because they read the
same output directory as the file tools.
"""
from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from dependencies   import (
    get_diff_tool, get_file_tool, get_llm, get_paths, get_vscode_tool,
    get_rate_limiter, require_api_key,
)
from request_models import (
    DiffRequest, DebugFileRequest, FileReadRequest, FileWriteRequest,
)
from util import safe_filename as _safe_filename  # dedup: shared with media/ppt


router = APIRouter()


@router.post("/api/diff/files")
async def api_diff(req: DiffRequest, request: Request,
                   diff_tool=Depends(get_diff_tool),
                   _auth=Depends(require_api_key),
                   limiter=Depends(get_rate_limiter)):
    ip = request.client.host if request.client else "unknown"
    if not limiter.check(ip):
        raise HTTPException(429, "Rate limit exceeded; try again shortly.")
    return diff_tool.diff_files(req.path_a, req.path_b)


@router.post("/api/files/read")
async def api_read(req: FileReadRequest, request: Request,
                   file_tool=Depends(get_file_tool),
                   _auth=Depends(require_api_key),
                   limiter=Depends(get_rate_limiter)):
    ip = request.client.host if request.client else "unknown"
    if not limiter.check(ip):
        raise HTTPException(429, "Rate limit exceeded; try again shortly.")
    return file_tool.read_file(req.path)


@router.post("/api/files/write")
async def api_write(req: FileWriteRequest, request: Request,
                    file_tool=Depends(get_file_tool),
                    limiter=Depends(get_rate_limiter)):
    # ponytail: write-only auth lives in the middleware fail-closed gate
    # (_SENSITIVE_PATH_PREFIXES); the limiter here defends against bursty
    # local callers hitting the same endpoint.
    ip = request.client.host if request.client else "unknown"
    if not limiter.check(ip):
        raise HTTPException(429, "Rate limit exceeded; try again shortly.")
    return file_tool.write_file(req.path, req.content)


@router.get("/api/files/browse")
async def api_browse(request: Request, path: str = "__drives__",
                     file_tool=Depends(get_file_tool),
                     _auth=Depends(require_api_key),
                     limiter=Depends(get_rate_limiter)):
    ip = request.client.host if request.client else "unknown"
    if not limiter.check(ip):
        raise HTTPException(429, "Rate limit exceeded; try again shortly.")
    return file_tool.list_directory(path)


@router.get("/api/files/drives")
async def api_drives(request: Request,
                     file_tool=Depends(get_file_tool),
                     _auth=Depends(require_api_key),
                     limiter=Depends(get_rate_limiter)):
    ip = request.client.host if request.client else "unknown"
    if not limiter.check(ip):
        raise HTTPException(429, "Rate limit exceeded; try again shortly.")
    return file_tool.list_directory("__drives__")


@router.post("/api/files/upload")
async def api_upload(request: Request, paths=Depends(get_paths),
                     _auth=Depends(require_api_key),
                     limiter=Depends(get_rate_limiter),
                     file: UploadFile = File(...)):
    ip = request.client.host if request.client else "unknown"
    if not limiter.check(ip):
        raise HTTPException(429, "Rate limit exceeded; try again shortly.")
    upload_max = 10 * 1024 * 1024  # ponytail: 10MB cap, add config when needed
    safe_name = _safe_filename(file.filename)
    dest = paths.output / safe_name
    # ponytail: stream to disk, not RAM — avoids OOM on large uploads
    remaining = upload_max
    with open(dest, "wb") as f:
        while chunk := await file.read(64 * 1024):
            remaining -= len(chunk)
            if remaining < 0:
                dest.unlink(missing_ok=True)
                raise HTTPException(413, "File too large (10MB max)")
            f.write(chunk)
    return {"status": "ok", "filename": safe_name, "path": str(dest)}


@router.post("/api/files/debug")
async def api_debug(req: DebugFileRequest, request: Request,
                    llm=Depends(get_llm),
                    file_tool=Depends(get_file_tool),
                    vscode_tool=Depends(get_vscode_tool),
                    limiter=Depends(get_rate_limiter)):
    ip = request.client.host if request.client else "unknown"
    if not limiter.check(ip):
        raise HTTPException(429, "Rate limit exceeded; try again shortly.")
    if not llm.is_loaded():
        raise HTTPException(400, "No model")
    r = file_tool.read_file(req.path)
    if r.get("status") == "error":
        raise HTTPException(400, r["message"])
    original = r["content"]
    prompt = (f"File: {r['filename']}\nInstruction: {req.instruction}\n\n"
              f"Code:\n```{r['extension'][1:]}\n{original}\n```\n\n"
              f"Return ONLY the complete fixed code. No explanation. No markdown.")
    loop = asyncio.get_event_loop()
    fixed = await loop.run_in_executor(
        None, lambda: "".join(llm.stream_tokens([], prompt,
                                                "Expert debugger. Return ONLY fixed code.",
                                                0.3, 4096))
    )
    fixed = re.sub(r"^```\w*\n?", "", fixed)
    fixed = re.sub(r"\n?```$", "", fixed).strip()
    result = vscode_tool.fix_file_in_place(req.path, fixed)
    result["original_lines"] = original.count("\n") + 1
    result["fixed_lines"] = fixed.count("\n") + 1
    return result


@router.get("/api/preview/{filename}")
async def api_preview(filename: str, request: Request, paths=Depends(get_paths),
                      _auth=Depends(require_api_key),
                      limiter=Depends(get_rate_limiter)):
    ip = request.client.host if request.client else "unknown"
    if not limiter.check(ip):
        raise HTTPException(429, "Rate limit exceeded; try again shortly.")
    fp = paths.output / _safe_filename(filename)
    if not fp.exists():
        raise HTTPException(404, "Not found")
    return HTMLResponse(fp.read_text(encoding="utf-8"))


@router.get("/api/outputs")
async def api_outputs(request: Request, paths=Depends(get_paths),
                      _auth=Depends(require_api_key),
                      limiter=Depends(get_rate_limiter)):
    ip = request.client.host if request.client else "unknown"
    if not limiter.check(ip):
        raise HTTPException(429, "Rate limit exceeded; try again shortly.")
    files = []
    for f in sorted(paths.output.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.is_file():
            files.append({"name": f.name, "size_kb": round(f.stat().st_size / 1024, 1)})
    return {"files": files}


@router.get("/api/outputs/download/{filename}")
async def api_dl(filename: str, request: Request, paths=Depends(get_paths),
                 _auth=Depends(require_api_key),
                 limiter=Depends(get_rate_limiter)):
    ip = request.client.host if request.client else "unknown"
    if not limiter.check(ip):
        raise HTTPException(429, "Rate limit exceeded; try again shortly.")
    fp = paths.output / _safe_filename(filename)
    if not fp.exists():
        raise HTTPException(404, "Not found")
    return FileResponse(str(fp), filename=filename)
