"""Shared utilities — single source of truth for helpers used across routers.

Keep this module stdlib-only so it can be imported anywhere without dragging
in transitive deps (python-pptx, whisper, etc.).
"""
from __future__ import annotations

import asyncio
import re
from functools import partial


async def run_sync(fn, *args, **kwargs):
    """Offload a blocking call to the default executor.

    ponytail: the stdlib answer to sync filesystem I/O inside async handlers
    (aiofiles was deliberately removed from deps — see SECURITY.md). Await
    this around store.load/save, read_text and directory scans so the event
    loop keeps serving while disk I/O runs on a worker thread.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(fn, *args, **kwargs))


def safe_filename(name: str) -> str:
    """Sanitize a filename to prevent path traversal at the route layer.

    Strips anything that is not alphanumeric, hyphen, underscore, dot, or space.
    Caps at 200 chars. Falls back to 'file' on empty input.

    NOTE: this is a presentation-layer guard (upload destination naming), not a
    security boundary. The real filesystem boundary is file_tool._resolve which
    enforces ALLOWED_BASE_DIRS. An escaped path passed here would still hit
    _resolve on read/write and be rejected there.

    Public alias for the previously-private _safe_filename copy in routers/files.py
    (Group 7 dedup target — see audit). Kept the original name as the public API
    so future imports read cleanly.
    """
    name = re.sub(r'[^\w\-_. ]', '_', name)
    name = name[:200]
    return name or "file"
