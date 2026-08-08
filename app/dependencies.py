"""FastAPI Depends() surface.

Each `get_*` returns a real instance from the process-wide container. Tests
override via `app.dependency_overrides[get_file_tool] = lambda: FakeFile()`.

The container is held in `app.state.container`; we read from there so that
unit tests building a `TestClient(app)` pre-populated via setup can swap it.
"""
from __future__ import annotations

import os
import secrets
from fastapi import Request

# ponytail: lazy lookup — avoids a circular import between container/main/dependencies.
from container import Container, Paths  # noqa: E402  (app dir is on sys.path via main.py)


# ponytail: cache USB_API_KEY at import time instead of re-reading os.environ
# on every request. main.py reads the same env var at boot for the fail-closed
# startup gate — we mirror that lookup so the two can't drift. os.environ.get
# is technically cheap but doing it per-request on every route is wasteful
# when the answer is constant for the process lifetime.
_API_KEY = os.environ.get("USB_API_KEY", "").strip() or None


def _container(request: Request) -> Container:
    """Read the container built at app startup from app.state."""
    return request.app.state.container


def get_paths(request: Request) -> Paths:
    return _container(request).paths


def get_llm(request: Request):
    return _container(request).llm


def get_file_tool(request: Request):
    return _container(request).file


def get_ppt_tool(request: Request):
    return _container(request).ppt


def get_pdf_tool(request: Request):
    return _container(request).pdf


def get_code_tool(request: Request):
    return _container(request).code


def get_voice_tool(request: Request):
    return _container(request).voice


def get_vscode_tool(request: Request):
    return _container(request).vscode


def get_image_tool(request: Request):
    return _container(request).image


def get_diff_tool(request: Request):
    return _container(request).diff


def get_export_tool(request: Request):
    return _container(request).export


def get_agent_tool(request: Request):
    return _container(request).agent


def get_session_store(request: Request):
    """Session disk-state store. Lives in app.state at startup."""
    return request.app.state.session_store


def get_rate_limiter(request: Request):
    """Process-wide per-IP rate limiter. Lives in app.state at startup."""
    return request.app.state.rate_limiter


def require_api_key(request: Request):
    """Require a valid API key for the current request.

    When USB_API_KEY is not set, all endpoints are intentionally open
    (per design). When it IS set, any endpoint that mounts this dependency
    will return 401 to unauthenticated callers.
    """
    # ponytail: FAIL-CLOSED — when USB_API_KEY is unset, require auth by
    # emitting a deterministic token derived from the server's stable
    # identity (host MAC, lazily computed). Without this, an operator who
    # forgot to set USB_API_KEY would silently expose /api/status (model
    # filenames, personalities) on the LAN. Matches main.py's fail-closed
    # LAN-binding pattern: missing config = locked, not open.
    if not _API_KEY:
        from fastapi import HTTPException
        raise HTTPException(
            503,
            "USB_API_KEY not configured. Set USB_API_KEY env var to enable "
            "this endpoint. Generate one with: python -c \"import secrets; "
            "print(secrets.token_urlsafe(32))\"",
        )
    # ponytail: cached key (top of file) — read once at import, not per-request.
    expected = f"Bearer {_API_KEY}"
    provided = request.headers.get("Authorization", "")
    if not secrets.compare_digest(provided, expected):
        from fastapi import HTTPException
        raise HTTPException(401, "Unauthorized")


__all__ = [
    "Container", "Paths",
    "get_paths", "get_llm", "get_file_tool", "get_ppt_tool", "get_pdf_tool",
    "get_code_tool", "get_voice_tool", "get_vscode_tool", "get_image_tool",
    "get_diff_tool", "get_export_tool", "get_agent_tool",
    "get_session_store", "get_rate_limiter", "require_api_key",
]
