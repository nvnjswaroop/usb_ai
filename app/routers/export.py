"""Export router — markdown / HTML export of a session.

Note: the export tool's API takes the loaded session dict, not just the id.
We load via SessionStore (DI) and pass the dict in. HTML escaping contract:
see tests/test_security.py::TestExportEscapes for the regression.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from dependencies   import get_export_tool, get_session_store, get_rate_limiter, require_api_key
from request_models import ExportRequest
from sessions       import SessionStore


router = APIRouter()


@router.post("/api/export")
async def api_export(req: ExportRequest,
                     request: Request,
                     export_tool=Depends(get_export_tool),
                     store: SessionStore = Depends(get_session_store),
                     limiter=Depends(get_rate_limiter),
                     _auth=Depends(require_api_key)):
    ip = request.client.host if request.client else "unknown"
    if not limiter.check(ip):
        raise HTTPException(429, "Rate limit exceeded; try again shortly.")
    session = store.load(req.session_id)
    if req.format == "markdown":
        return export_tool.export_markdown(session)
    return export_tool.export_html(session)
