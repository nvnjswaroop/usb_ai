"""FastAPI Depends() surface.

Each `get_*` returns a real instance from the process-wide container. Tests
override via `app.dependency_overrides[get_file_tool] = lambda: FakeFile()`.

The container is held in `app.state.container`; we read from there so that
unit tests building a `TestClient(app)` pre-populated via setup can swap it.
"""
from __future__ import annotations

import os
import secrets
from fastapi import HTTPException, Request

# ponytail: lazy lookup — avoids a circular import between container/main/dependencies.
from container import Container, Paths  # noqa: E402  (app dir is on sys.path via main.py)


def get_api_key() -> str | None:
    """Canonical USB_API_KEY lookup — the single source of truth for auth.

    Read by BOTH main.py's middleware and require_api_key() below, so the two
    layers cannot drift.

    ponytail: read per-call instead of caching at import. os.environ.get is a
    dict lookup; the old double-cache (main._API_KEY AND dependencies._API_KEY,
    each read once at boot) meant tests patching one global silently left the
    other stale — that drift produced two contradictory auth policies
    (middleware open-when-unset vs dependency 503-when-unset).
    """
    return os.environ.get("USB_API_KEY", "").strip() or None


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
    """Verify the Bearer key when USB_API_KEY is configured.

    Policy — single source of truth, identical to main.py's middleware:
      - Key unset -> allow. Loopback-open by design (README Quick Start never
        sets a key; launch.bat health-checks /api/status without one).
        Residual hardening lives elsewhere: the middleware fail-closes
        filesystem-mutating prefixes (/api/files/write|debug), and startup
        refuses non-loopback binds without a key.
      - Key set   -> constant-time `Authorization: Bearer <key>` check.
    """
    api_key = get_api_key()
    if not api_key:
        return
    expected = f"Bearer {api_key}"
    provided = request.headers.get("Authorization", "")
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(401, "Unauthorized")


def require_key_always(request: Request):
    """Fail-closed guard for filesystem-mutating routes.

    Unlike require_api_key (loopback-open when USB_API_KEY is unset), this
    raises 401 whenever the Bearer key is missing or invalid — including
    when NO key is configured.

    ponytail: mounted explicitly on /api/files/write and /api/files/debug,
    replacing the old string-prefix tuple in the middleware. The tuple was
    drift-prone — a newly added mutating route silently missed it; here the
    guard sits visibly in the handler signature and is enforced by
    TestAuthMatrix. Add this dependency to any new filesystem-mutating route.
    """
    api_key = get_api_key()
    provided = request.headers.get("Authorization", "")
    if not api_key:
        raise HTTPException(401, "Unauthorized")
    expected = f"Bearer {api_key}"
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(401, "Unauthorized")


__all__ = [
    "Container", "Paths",
    "get_paths", "get_llm", "get_file_tool", "get_ppt_tool", "get_pdf_tool",
    "get_code_tool", "get_voice_tool", "get_vscode_tool", "get_image_tool",
    "get_diff_tool", "get_export_tool", "get_agent_tool",
    "get_session_store", "get_rate_limiter", "require_api_key",
    "require_key_always", "get_api_key",
]
