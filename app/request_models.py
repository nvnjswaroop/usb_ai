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


class LoadModelRequest(BaseModel):
    model_name: str
    n_ctx: int = 4096
    n_threads: int = 8
    # -1 = auto-detect (recommended), 0 = CPU, 9999 = max GPU layers
    n_gpu_layers: int = -1


class ChatRequest(BaseModel):
    session_id:    str
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
    topic: str
    num_slides: int = 6
    style: str = "professional"
    extra_instructions: str = ""
    session_id: Optional[str] = None


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
    session_id: str
    format: str = "html"


class CalcRequest(BaseModel):
    expression: str
