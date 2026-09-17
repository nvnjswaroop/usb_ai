"""Pydantic request/response models for routers.

Split out of main.py after Group 3. Routers import only what they need.
These match the originals verbatim so serialisation is identical.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


# ponytail: global body-size ceiling for JSON requests. FastAPI's default is
# effectively unlimited — a 1 GB POST to /api/files/write would OOM the worker.
# 10 MB mirrors the existing per-route upload cap (see files.py / media.py).
# Streaming uploads use their own 64 KB chunk loop and are NOT subject to this.
MAX_BODY_SIZE = 10 * 1024 * 1024  # 10 MB


# ponytail: session ids reach Path(history_dir)/f"{sid}.json" — an
# unvalidated id is a traversal primitive (absolute sid REPLACES the base;
# ../ escapes via the .json suffix; on Windows %5C survives route matching).
# This pattern admits every id the UI generates (sess_<ts>_<rand>) and the
# legacy history files, and nothing else.
SESSION_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"

# ponytail: LoadModelRequest fields are bounded to stop a single request from
# asking for 1M ctx / 256 threads / 99999 GPU layers and OOM-ing the worker.
# Audit 2026-09-16 (Terra): no upper bound on n_ctx/n_threads/n_gpu_layers
# let any auth-OK caller exhaust RAM before the handler ran.
class LoadModelRequest(BaseModel):
    # basename only — no path separators, no traversal. The sidecar then joins
    # this with models_dir; absolute paths are rejected by the Path join in
    # llm_server.py (the trailing .gguf check below is the second line of
    # defense). See audit 2026-09-16 finding #2.
    model_name: str = Field(pattern=r"^[\w\-. ]+\.gguf$", max_length=128)
    n_ctx:      int = Field(default=4096, ge=512, le=131072)
    n_threads:  int = Field(default=8,    ge=1,  le=64)
    # -1 = auto-detect (recommended), 0 = CPU, 9999 = max GPU layers
    n_gpu_layers: int = Field(default=-1,  ge=-1, le=9999)


class ChatRequest(BaseModel):
    # Field(pattern=) rejects traversal sids with 422 before the handler runs.
    session_id:    str = Field(pattern=SESSION_ID_PATTERN)
    message:       str = Field(default="", max_length=16000)
    model:         Optional[str] = None
    temperature:   float = 0.7
    max_tokens:    int = 2048
    system_prompt: Optional[str] = None
    mode:          str = "chat"
    run_code:      bool = False
    save_code:     bool = False
    image_b64:     Optional[str] = None


class FileReadRequest(BaseModel):
    path: str


class FileWriteRequest(BaseModel):
    path: str
    content: str


class DebugFileRequest(BaseModel):
    path: str
    instruction: str = "Fix all bugs and improve this code."


class PDFRequest(BaseModel):
    path: str


class CodeRequest(BaseModel):
    code: str


class SaveCodeRequest(BaseModel):
    code: str
    filename: str
    language: str = ""


class PPTRequest(BaseModel):
    topic: str = Field(max_length=200)
    # ponytail: bound slide count — without this an auth-OK caller can ask
    # for 10,000 slides and pin the LLM sidecar. Audit 2026-09-16 (Terra).
    num_slides: int = Field(default=6, ge=1, le=50)
    style: str = "professional"
    extra_instructions: str = Field(default="", max_length=2000)
    session_id: Optional[str] = Field(default=None, pattern=SESSION_ID_PATTERN)


class RenameRequest(BaseModel):
    title: str


class SpeakRequest(BaseModel):
    text: str
    rate: int = 175
    volume: float = 1.0
    voice_id: str = ""


class AgentRequest(BaseModel):
    task: str
    max_steps: int = 8


class DiffRequest(BaseModel):
    path_a: str
    path_b: str


class ExportRequest(BaseModel):
    session_id: str = Field(pattern=SESSION_ID_PATTERN)
    format: str = "html"


class CalcRequest(BaseModel):
    expression: str
