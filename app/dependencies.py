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
    raw_key = os.environ.get("USB_API_KEY", "").strip()
    if not raw_key:
        return  # auth disabled — endpoint is open
    provided = request.headers.get("Authorization", "")
    expected = f"Bearer {raw_key}"
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
