import sys
import os

# Force UTF-8 output - prevents Windows cp1252 crash on any unicode chars
if hasattr(sys.stdout,"reconfigure"):
    try: sys.stdout.reconfigure(encoding="utf-8")
    except (OSError, ValueError): pass
if hasattr(sys.stderr,"reconfigure"):
    try: sys.stderr.reconfigure(encoding="utf-8")
    except (OSError, ValueError): pass

from pathlib import Path as _P
_APP_DIR = str(_P(__file__).parent)
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

import asyncio
import json
import re
import secrets
import threading
import time
import traceback
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel
from schemas import Artifact, AgentResult
import base64

# ── Paths ──────────────────────────────────────────────────────────────────────
USB_ROOT    = Path(__file__).parent.parent
MODELS_DIR  = USB_ROOT / "models"
HISTORY_DIR = USB_ROOT / "history"
OUTPUT_DIR  = USB_ROOT / "output"
WHISPER_DIR = USB_ROOT / "whisper_models"
UI_DIR      = Path(__file__).parent / "ui"

for _d in (MODELS_DIR, HISTORY_DIR, OUTPUT_DIR, WHISPER_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ── Tools ──────────────────────────────────────────────────────────────────────
from llm                import LLMEngine
from tools.file_tool    import FileTool
from tools.ppt_tool     import PPTTool
from tools.pdf_tool     import PDFTool
from tools.voice_tool   import VoiceTool
from tools.vscode_tool  import VSCodeTool
from tools.image_tool   import ImageTool
from tools.diff_tool    import DiffTool
from tools.export_tool  import ExportTool
from tools.agent_tool   import AgentTool
from tools.code_tool   import CodeTool

llm_engine   = LLMEngine(MODELS_DIR, USB_ROOT/"prefeeds")
agent_tool   = AgentTool(OUTPUT_DIR)
file_tool    = FileTool()
ppt_tool     = PPTTool(OUTPUT_DIR)
pdf_tool     = PDFTool()
code_tool    = CodeTool(python_path=sys.executable)
voice_tool   = VoiceTool()
vscode_tool  = VSCodeTool(OUTPUT_DIR)
image_tool   = ImageTool(OUTPUT_DIR)
diff_tool    = DiffTool()
export_tool  = ExportTool(OUTPUT_DIR)

app = FastAPI(title="USB AI")

# CORS — restrict credentials + allowed headers
app.add_middleware(CORSMiddleware,
    allow_origins=["http://localhost:8080", "http://127.0.0.1:8080", "http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization", "X-Requested-With", "Accept"])

# ── Auth middleware ─────────────────────────────────────────────────────────────
_RAW = os.environ.get("USB_API_KEY", "").strip()
_API_KEY = _RAW or None

@app.middleware("http")
async def check_auth(request: Request, call_next):
    if request.url.path.startswith("/api/") and not _API_KEY:
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        # ponytail: constant-time compare defeats timing leaks on localhost; swap for HMAC if shared.
        provided = request.headers.get("Authorization", "")
        expected = f"Bearer {_API_KEY}" if _API_KEY else ""
        if _API_KEY and not secrets.compare_digest(provided, expected):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    return await call_next(request)

# ── Pydantic ───────────────────────────────────────────────────────────────────
class LoadModelRequest(BaseModel):
    model_name: str; n_ctx: int = 4096; n_threads: int = 8
    n_gpu_layers: int = -1  # -1 = auto-detect (recommended), 0 = CPU, 9999 = max GPU layers

class ChatRequest(BaseModel):
    session_id:    str
    message:       str
    model:         Optional[str] = None
    temperature:   float         = 0.7
    max_tokens:    int           = 2048
    system_prompt: Optional[str] = None
    mode:          str           = "chat"
    run_code:      bool          = False
    save_code:     bool          = False
    image_b64:     Optional[str] = None

class FileReadRequest(BaseModel):   path: str
class FileWriteRequest(BaseModel):  path: str; content: str
class DebugFileRequest(BaseModel):  path: str; instruction: str = "Fix all bugs and improve this code."
class PDFRequest(BaseModel):        path: str
class CodeRequest(BaseModel):       code: str
class SaveCodeRequest(BaseModel):   code: str; filename: str; language: str = ""
class PPTRequest(BaseModel):
    topic: str; num_slides: int = 6; style: str = "professional"
    extra_instructions: str = ""; session_id: Optional[str] = None
class RenameRequest(BaseModel):     title: str
class SpeakRequest(BaseModel):
    text: str; rate: int = 175; volume: float = 1.0; voice_id: str = ""
class AgentRequest(BaseModel):
    task: str; max_steps: int = 8
class SearchRequest(BaseModel):     query: str; max_results: int = 5
class DiffRequest(BaseModel):       path_a: str; path_b: str
class ExportRequest(BaseModel):     session_id: str; format: str = "html"
class CalcRequest(BaseModel):       expression: str

# ── Personalities ──────────────────────────────────────────────────────────────
PERSONALITIES = {
    "chat":    "You are a helpful AI assistant. Completely private and local. Be concise, accurate, honest.",
    "coder":   "You are an expert software engineer. Write clean efficient code. Always add # filename: name.ext at the top of each code block.",
    "teacher": "You are a patient clear teacher. Explain step by step with real examples.",
    "doctor":  "You are a medical information assistant. Give accurate information and always recommend seeing a real doctor.",
    "math":    "You are a mathematics expert. Show all working step by step. Check arithmetic carefully.",
    "vision":  "You are a vision AI assistant. Analyse images in detail. Describe objects, text, colours, and context accurately.",
}

# ── Session helpers ────────────────────────────────────────────────────────────
def _sp(sid):   return HISTORY_DIR / f"{sid}.json"
_SESSION_INDEX = HISTORY_DIR / "_index.json"

def _write_index(items):
    _SESSION_INDEX.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")

def _load(sid):
    p = _sp(sid)
    if not p.exists():
        return {"id":sid,"title":"New Chat","messages":[],
                "created":time.time(),"updated":time.time()}
    return json.loads(p.read_text(encoding="utf-8"))

def _save(data):
    data["updated"] = time.time()
    p = _sp(data["id"])
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    # ponytail: write index eagerly; replaces the per-call glob+json.dumps disk
    # storm in api_sessions. Cost: one extra write per save, gain: O(1) reads.
    try:
        idx = json.loads(_SESSION_INDEX.read_text(encoding="utf-8")) if _SESSION_INDEX.exists() else []
    except (OSError, ValueError):
        idx = []
    rec = next((i for i in idx if i["id"] == data["id"]),
               {"id": data["id"]})
    rec.update({"id": data["id"], "title": data.get("title", "Chat"),
                "updated": data["updated"],
                "message_count": len(data.get("messages", []))})
    idx = [i for i in idx if i["id"] != data["id"]]
    idx.append(rec)
    idx.sort(key=lambda x: x.get("updated", 0), reverse=True)
    try:
        _write_index(idx)
    except OSError:
        pass  # ponytail: index is advisory; api_sessions falls back to glob.

def _sys(mode="chat"): return PERSONALITIES.get(mode, PERSONALITIES["chat"])

# ── Streaming Artifact Extractor ───────────────────────────────────────────────
# Real-time code block detection — emits artifacts as blocks close, not at end.
# This is what powers the Claude-style artifact panel in the UI.
# ponytail: single dict — _CODE_EXT is just .get() on the same map.
_CODE_LANGS = {
    "python": "py", "py": "py",
    "javascript": "js", "js": "js",
    "typescript": "ts", "ts": "ts", "tsx": "tsx",
    "html": "html", "htm": "html", "css": "css", "scss": "scss", "sass": "sass",
    "java": "java",
    "cpp": "cpp", "c": "c", "h": "h", "hpp": "hpp",
    "rust": "rs",
    "go": "go",
    "bash": "sh", "sh": "sh", "zsh": "zsh", "shell": "sh",
    "sql": "sql",
    "json": "json", "yaml": "yaml", "yml": "yaml",
    "xml": "xml",
    "ruby": "rb", "rb": "rb",
    "php": "php",
    "swift": "swift",
    "kotlin": "kt", "kt": "kt",
    "dart": "dart",
    "scala": "scala",
    "r": "r",
    "lua": "lua",
    "perl": "pl",
    "csharp": "cs", "cs": "cs", "c#": "cs",
    "markdown": "md", "md": "md",
    "dockerfile": "dockerfile",
    "makefile": "makefile", "make": "makefile",
    "toml": "toml", "ini": "ini", "cfg": "cfg",
    "graphql": "graphql", "gql": "graphql",
    "prisma": "prisma",
    "svelte": "svelte",
    "vue": "vue",
    "jsx": "jsx", "react": "jsx",
    "txt": "txt",
}
# _CODE_EXT removed — was a duplicate of _CODE_LANGS values. callers use _CODE_LANGS.get(lang, "txt") directly.


class _StreamingArtifactExtractor:
    """
    Accumulates streaming text, detects complete code blocks as they close,
    and yields Artifact objects in real-time.

    Usage:
        extractor = _StreamingArtifactExtractor()
        for tok in tokens:
            extractor.push(tok)
            while artifact := extractor.pop():
                yield artifact
        for a in extractor.flush():
            yield a
    """

    def __init__(self):
        self._buf = ""           # raw accumulated text
        self._open_lang = None  # language of currently open block
        self._open_start = -1   # position where current block's ``` appears
        self._complete: list[Artifact] = []  # completed artifacts ready to pop
        # ponytail: cursor to avoid O(n²) re-scans. only search past this point.
        self._scan_pos = 0

    def push(self, chunk: str):
        """Feed a token chunk. May complete a block."""
        self._buf += chunk
        self._scan()

    def pop(self) -> Artifact | None:
        """Return the next completed artifact, or None."""
        if self._complete:
            return self._complete.pop(0)
        return None

    def flush(self) -> list[Artifact]:
        """Return all remaining artifacts (call after stream ends)."""
        self._scan()
        # Orphan open block — treat as complete
        if self._open_lang is not None:
            code = self._buf[self._open_start:]
            self._complete.append(self._make_artifact(self._open_lang, code))
            self._open_lang = None
            self._open_start = -1
        out = self._complete
        self._complete = []
        return out

    def _scan(self):
        """Scan _buf for newly-closed code blocks. Uses _scan_pos cursor."""
        while True:
            if self._open_lang is None:
                # Look for opening ``` — only from cursor forward
                idx = self._buf.find("```", self._scan_pos)
                if idx == -1:
                    self._scan_pos = len(self._buf)
                    break
                # Language is everything after ``` on the same line
                rest = self._buf[idx + 3:]
                eol = rest.find("\n")
                lang_raw = rest[:eol] if eol != -1 else rest
                lang_raw = lang_raw.strip().lower()
                self._open_lang = lang_raw if lang_raw else "txt"
                self._open_start = idx
                self._buf = rest[eol + 1:] if eol != -1 else ""
                self._scan_pos = 0  # buffer was sliced, reset cursor
            else:
                # Look for closing ``` — only from cursor forward
                close = self._buf.find("```", self._scan_pos)
                if close == -1:
                    self._scan_pos = len(self._buf)
                    # Block still open — leave for next scan
                    break
                code = self._buf[:close]
                self._complete.append(self._make_artifact(self._open_lang, code))
                self._buf = self._buf[close + 3:]
                self._open_lang = None
                self._open_start = -1
                self._scan_pos = 0  # buffer was sliced, reset cursor

    def _make_artifact(self, lang_raw: str, code: str) -> Artifact:
        code = code.strip()
        lang = _CODE_LANGS.get(lang_raw, lang_raw)

        # Extract filename from comment inside code
        filename = None
        for pat in [r"(?:#|//)\s*filename:\s*(\S+)", r"<!--\s*filename:\s*(\S+)\s*-->"]:
            m = re.search(pat, code)
            if m:
                filename = m.group(1)
                break

        if not filename:
            ext = _CODE_LANGS.get(lang, "txt")
            filename = f"code_{int(time.time())}.{ext}"

        return Artifact(
            type="code",
            title=filename,
            description=f"{lang.title()} code",
            content=code,
            file_name=filename,
            mime_type=f"text/x-{lang}" if lang not in ("txt",) else "text/plain",
            preview=(code[:200] + "...") if len(code) > 200 else code,
        )


def _code_blocks(text: str) -> list[dict]:
    return [{"lang": m.group(1) or "txt", "code": m.group(2).strip()}
            for m in re.finditer(r"```(\w+)?\n(.*?)```", text, re.DOTALL)]

# ── Routes ─────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def root():
    hp = UI_DIR / "chat.html"
    return HTMLResponse(hp.read_text(encoding="utf-8") if hp.exists()
                        else "<h1>chat.html missing</h1>")

@app.get("/api/status")
async def api_status():
    return {"model_loaded":llm_engine.is_loaded(),
            "model_loading":llm_engine.is_loading(),
            "current_model":llm_engine.current_model,
            "vision_capable":llm_engine.supports_vision(),
            "models_available":[f.name for f in sorted(MODELS_DIR.glob("*.gguf"))],
            "personalities":list(PERSONALITIES.keys())}

@app.get("/api/models/progress")
async def api_progress():
    prog = llm_engine.get_load_progress()
    try:
        log = USB_ROOT / "server.log"
        if log.exists():
            lines = log.read_text(errors="ignore").splitlines()
            for line in reversed(lines[-50:]):
                if any(k in line for k in
                       ["llm_load","llama_model","llama_new","AVX",
                        "model size","KV self","llama_kv"]):
                    prog["log_line"] = line.strip()[:120]
                    break
    except (OSError, ValueError): pass
    return prog

@app.get("/api/models")
async def api_models():
    return {"models":[{"name":f.name,
                        "size_mb":round(f.stat().st_size/(1024*1024),1)}
                       for f in sorted(MODELS_DIR.glob("*.gguf"))]}

_load_lock = threading.Lock()

@app.post("/api/models/load")
async def api_load(req: LoadModelRequest):
    # ponytail: check-and-set under a lock — kills the two-POST race that double-inits Llama.
    if not _load_lock.acquire(blocking=False):
        raise HTTPException(409, "Already loading.")
    llm_engine.set_loading(True)
    try:
        await asyncio.get_event_loop().run_in_executor(
            None, llm_engine.load_model_sync,
            req.model_name, req.n_ctx, req.n_threads)
        return {"status":"ok","model":req.model_name,
                "vision":llm_engine.supports_vision()}
    except Exception as e:
        print(f"[LOAD ERROR]\n{traceback.format_exc()}", flush=True)
        raise HTTPException(500, str(e))
    finally:
        llm_engine.set_loading(False)
        _load_lock.release()

@app.post("/api/sessions/new")
async def api_new_session():
    llm_engine.reset_window()
    return {"status":"ok"}

@app.get("/api/sessions")
async def api_sessions(limit: int = 100):
    # ponytail: read the index first (one disk read), fall back to glob on corruption.
    # Cap at 100 default, avoid O(n) disk reads scaling forever.
    out = []
    try:
        if _SESSION_INDEX.exists():
            out = json.loads(_SESSION_INDEX.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        out = []
    if not out:
        # Fallback: rebuild index from disk (first run / after corruption).
        for p in HISTORY_DIR.glob("*.json"):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                out.append({"id": d["id"], "title": d.get("title", "Chat"),
                            "updated": d.get("updated", 0),
                            "message_count": len(d.get("messages", []))})
            except (OSError, ValueError) as e:
                print(f"[warn] session load failed {p.name}: {e}", flush=True)
        out.sort(key=lambda x: x.get("updated", 0), reverse=True)
    return {"sessions": out[:limit]}

@app.get("/api/sessions/{sid}")
async def api_get_session(sid: str): return _load(sid)

@app.delete("/api/sessions/{sid}")
async def api_del_session(sid: str):
    p = _sp(sid)
    if p.exists(): p.unlink()
    return {"status":"ok"}

@app.post("/api/sessions/{sid}/rename")
async def api_rename(sid: str, req: RenameRequest):
    d = _load(sid); d["title"]=req.title; _save(d)
    return {"status":"ok"}

@app.get("/api/sessions/search/{query}")
async def api_search_sessions(query: str):
    results=[]; q=query.lower()
    for p in HISTORY_DIR.glob("*.json"):
        try:
            d=json.loads(p.read_text(encoding="utf-8"))
            matches=[{"index":i,"role":m.get("role",""),
                      "excerpt":m.get("content","")[:120]}
                     for i,m in enumerate(d.get("messages",[]))
                     if q in m.get("content","").lower()]
            if matches:
                results.append({"id":d["id"],"title":d.get("title","Chat"),
                                "updated":d.get("updated",0),"matches":matches})
        except (OSError, ValueError) as e: print(f"[warn] session search failed {p.name}: {e}", flush=True)
    results.sort(key=lambda x: x["updated"], reverse=True)
    return {"query":query,"results":results}


@app.post("/api/chat/stream")
async def api_chat(req: ChatRequest):
    if not llm_engine.is_loaded(): raise HTTPException(400,"No model loaded")
    session = _load(req.session_id)
    if not session["messages"]:
        session["title"] = req.message[:60] if req.message else "Vision"

    user_msg = req.message
    entry = {"role":"user","content":req.message}
    if req.image_b64: entry["has_image"] = True
    session["messages"].append(entry)
    _save(session)

    history = session["messages"][:-1]
    system  = req.system_prompt or _sys(req.mode)
    if req.image_b64 and req.mode=="chat": system = PERSONALITIES["vision"]

    async def event_stream():
        full = ""
        extractor = _StreamingArtifactExtractor()
        # Code execution output is injected inline after each code block in full_text
        # so the session history stays complete. We track injected outputs separately
        # to avoid re-extracting them as artifacts.
        try:
            loop = asyncio.get_event_loop()

            if req.image_b64 and not llm_engine.supports_vision():
                w = "Warning: This model does not support images. Load Gemma 4 E4B or LLaVA for vision.\n\n"
                full += w
                yield f"data: {json.dumps({'token': w})}\n\n"

            token_iter = await loop.run_in_executor(
                None, lambda: llm_engine.stream_tokens(
                    history, user_msg, system, req.temperature, req.max_tokens,
                    req.image_b64 if llm_engine.supports_vision() else None))

            def _next(it):
                try: return next(it)
                except StopIteration: return None

            while True:
                tok = await loop.run_in_executor(None, _next, token_iter)
                if tok is None: break
                full += tok

                # Feed to artifact extractor — emits artifacts as code blocks close
                extractor.push(tok)
                while artifact := extractor.pop():
                    yield f"data: {json.dumps({'type': 'artifact', **artifact.model_dump()})}\n\n"

                yield f"data: {json.dumps({'token': tok})}\n\n"

            # Flush any remaining artifacts (e.g. final unclosed block)
            for artifact in extractor.flush():
                yield f"data: {json.dumps({'type': 'artifact', **artifact.model_dump()})}\n\n"

            # Stop streaming TTS
            # TTS functionality removed for fully local version

            saved = []
            if req.save_code:
                saved = llm_engine.generate_code_files(full, OUTPUT_DIR)

            yield f"data: {json.dumps({'done': True, 'saved_files': saved})}\n\n"
        except Exception as e:
            print(f"[STREAM ERROR]\n{traceback.format_exc()}", flush=True)
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        finally:
            # ponytail: don't persist an empty/failed assistant turn — pollutes history with junk.
            if full.strip():
                session["messages"].append({"role": "assistant", "content": full})
            _save(session)

    return StreamingResponse(event_stream(), media_type="text/event-stream")

@app.post("/api/sessions/{sid}/summarise")
async def api_summarise(sid: str):
    if not llm_engine.is_loaded(): raise HTTPException(400,"No model")
    session=_load(sid); msgs=session.get("messages",[])
    if len(msgs)<4: return {"status":"error","message":"Not enough messages."}
    transcript="\n".join(
        f"{'User' if m['role']=='user' else 'AI'}: {m['content'][:300]}"
        for m in msgs[-30:])
    prompt=f"Summarise this conversation in 3-5 bullet points:\n\n{transcript}"
    loop=asyncio.get_event_loop()
    summary=await loop.run_in_executor(None, lambda:
        "".join(list(llm_engine.stream_tokens([],prompt,"Summarise briefly.",0.3,512))))
    return {"status":"ok","summary":summary.strip()}

@app.post("/api/export")
async def api_export(req: ExportRequest):
    session=_load(req.session_id)
    return export_tool.export_markdown(session) if req.format=="markdown" \
           else export_tool.export_html(session)

# @app.post("/api/youtube")  # YouTube endpoint disabled

from calc import evaluate as _calc_evaluate

@app.post("/api/calc")
async def api_calc(req: CalcRequest):
    try:
        result, result_str = _calc_evaluate(req.expression)
        return {"status": "ok", "expression": req.expression,
                "result": result, "result_str": result_str}
    except ValueError as e:
        # ponytail: surface math errors (div-by-zero, inf/nan, unsupported op) as 200-error, not 500.
        return {"status": "error", "message": f"Invalid expression: {e}"}
    except Exception as e:
        return {"status": "error", "message": f"Invalid expression: {e}"}

@app.post("/api/diff/files")
async def api_diff(req: DiffRequest): return diff_tool.diff_files(req.path_a,req.path_b)

@app.post("/api/files/read")
async def api_read(req: FileReadRequest): return file_tool.read_file(req.path)

@app.post("/api/files/write")
async def api_write(req: FileWriteRequest): return file_tool.write_file(req.path,req.content)

@app.get("/api/files/browse")
async def api_browse(path: str="__drives__"): return file_tool.list_directory(path)

@app.get("/api/files/drives")
async def api_drives(): return file_tool.list_directory("__drives__")

def _safe_filename(name: str) -> str:
    """Sanitize a filename to prevent path traversal."""
    import re
    name = re.sub(r'[^\w\-_. ]', '_', name)
    name = name[:200]
    return name or "file"

@app.post("/api/files/upload")
async def api_upload(file: UploadFile=File(...)):
    upload_max = 10 * 1024 * 1024  # ponytail: 10MB cap, add config when needed
    safe_name = _safe_filename(file.filename)
    dest = OUTPUT_DIR / safe_name
    # ponytail: stream to disk, not RAM — avoids OOM on large uploads
    remaining = upload_max
    with open(dest, "wb") as f:
        while chunk := await file.read(64 * 1024):
            remaining -= len(chunk)
            if remaining < 0:
                dest.unlink(missing_ok=True)
                raise HTTPException(413, "File too large (10MB max)")
            f.write(chunk)
    return {"status":"ok","filename":safe_name,"path":str(dest)}

@app.post("/api/files/debug")
async def api_debug(req: DebugFileRequest):
    if not llm_engine.is_loaded(): raise HTTPException(400,"No model")
    r=file_tool.read_file(req.path)
    if r.get("status")=="error": raise HTTPException(400,r["message"])
    original=r["content"]
    prompt=(f"File: {r['filename']}\nInstruction: {req.instruction}\n\n"
            f"Code:\n```{r['extension'][1:]}\n{original}\n```\n\n"
            f"Return ONLY the complete fixed code. No explanation. No markdown.")
    loop=asyncio.get_event_loop()
    fixed=await loop.run_in_executor(None, lambda:
        "".join(llm_engine.stream_tokens([],prompt,"Expert debugger. Return ONLY fixed code.",0.3,4096)))
    fixed=re.sub(r"^```\w*\n?","",fixed); fixed=re.sub(r"\n?```$","",fixed).strip()
    result=vscode_tool.fix_file_in_place(req.path,fixed)
    result["original_lines"]=original.count("\n")+1
    result["fixed_lines"]=fixed.count("\n")+1
    return result

@app.post("/api/pdf/extract")
async def api_pdf(req: PDFRequest): return pdf_tool.extract_text(req.path)

@app.post("/api/pdf/upload")
async def api_pdf_upload(file: UploadFile=File(...)):
    # ponytail: stream-to-disk with the same 10MB cap as /api/files/upload — kills RAM-OOM on large PDFs.
    safe_name = _safe_filename(file.filename)
    dest = OUTPUT_DIR / safe_name
    remaining = 10 * 1024 * 1024
    with open(dest, "wb") as f:
        while chunk := await file.read(64 * 1024):
            remaining -= len(chunk)
            if remaining < 0:
                dest.unlink(missing_ok=True)
                raise HTTPException(413, "File too large (10MB max)")
            f.write(chunk)
    try:
        return pdf_tool.extract_text(str(dest))
    finally:
        # ponytail: temp upload, clean up after extraction
        try: dest.unlink(missing_ok=True)
        except OSError: pass

@app.post("/api/image/upload")
async def api_image(file: UploadFile=File(...)):
    # ponytail: 10MB streamed cap — image_tool also enforces post-save, but this kills RAM-OOM at the route.
    remaining = 10 * 1024 * 1024
    chunks = []
    while chunk := await file.read(64 * 1024):
        remaining -= len(chunk)
        if remaining < 0:
            raise HTTPException(413, "Image too large (10MB max)")
        chunks.append(chunk)
    data = b"".join(chunks)
    result = image_tool.save_upload(data, file.filename)
    if result["status"] == "ok":
        b64 = image_tool.read_for_llm(result["path"])
        result["base64_url"] = b64.get("base64_url", "")
    return result

@app.post("/api/code/run")
async def api_run(req: CodeRequest):
    return await asyncio.get_event_loop().run_in_executor(None,code_tool.run_python,req.code)

@app.post("/api/code/save")
async def api_save_code(req: SaveCodeRequest):
    return vscode_tool.save_and_open(req.code,req.filename,req.language)

@app.get("/api/preview/{filename}")
async def api_preview(filename: str):
    fp=OUTPUT_DIR/_safe_filename(filename)
    if not fp.exists(): raise HTTPException(404,"Not found")
    return HTMLResponse(fp.read_text(encoding="utf-8"))

@app.post("/api/voice/speak")
async def api_speak(req: SpeakRequest):
    return await asyncio.get_event_loop().run_in_executor(
        None,voice_tool.speak,req.text,req.rate,req.volume,req.voice_id)

@app.get("/api/voice/voices")
async def api_voices(): return voice_tool.get_voices()

@app.post("/api/voice/transcribe")
async def api_transcribe(file: UploadFile=File(...)):
    suffix=Path(file.filename).suffix or ".webm"
    tmp=OUTPUT_DIR/f"audio_{int(time.time())}{suffix}"
    tmp.write_bytes(await file.read())
    result=await asyncio.get_event_loop().run_in_executor(
        None,voice_tool.transcribe,str(tmp),"base",str(WHISPER_DIR))
    try: tmp.unlink()
    except OSError: pass
    return result

# Web search endpoint removed for fully local version

@app.post("/api/ppt/generate")
async def api_ppt(req: PPTRequest):
    if not llm_engine.is_loaded(): raise HTTPException(400,"No model")
    prompt=(f"Create a PowerPoint about: {req.topic}\nSlides: {req.num_slides}\n"
            +(f"Extra: {req.extra_instructions}\n" if req.extra_instructions else "")
            +'Return ONLY JSON: {"title":"Title","slides":[{"slide_number":1,'
            +'"title":"T","bullet_points":["A","B","C"],"speaker_notes":"N"}]}\n'
            +f"Exactly {req.num_slides} slides, 3-5 bullets each.")
    try:
        _raw_json = llm_engine.generate_json(prompt)
        slide_data = json.loads(_raw_json)
    except json.JSONDecodeError as e:
        # ponytail: log the raw LLM output so a bad-JSON failure is debuggable, not a silent 500.
        print(f"[PPT] Bad JSON from LLM ({e}): {_raw_json[:500]!r}", flush=True)
        raise HTTPException(500, f"Bad JSON: {e}")
    try: filename,_=ppt_tool.create_ppt(slide_data,req.style)
    except Exception as e: raise HTTPException(500,f"PPT failed: {e}")
    if req.session_id:
        try:
            s=_load(req.session_id)
            s["messages"].append({"role":"assistant",
                "content":f"[PPT] {slide_data.get('title','')}","ppt_file":filename})
            _save(s)
        except (OSError, ValueError) as e: print(f"[warn] ppt session save failed: {e}", flush=True)
    return {"status":"ok","filename":filename,"title":slide_data.get("title","")}

@app.get("/api/ppt/download/{filename}")
async def api_ppt_dl(filename: str):
    fp=OUTPUT_DIR/_safe_filename(filename)
    if not fp.exists(): raise HTTPException(404,"Not found")
    return FileResponse(str(fp),filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")

@app.get("/api/outputs")
async def api_outputs():
    files=[]
    for f in sorted(OUTPUT_DIR.iterdir(),key=lambda x:x.stat().st_mtime,reverse=True):
        if f.is_file(): files.append({"name":f.name,"size_kb":round(f.stat().st_size/1024,1)})
    return {"files":files}

@app.get("/api/outputs/download/{filename}")
async def api_dl(filename: str):
    fp=OUTPUT_DIR/_safe_filename(filename)
    if not fp.exists(): raise HTTPException(404,"Not found")
    return FileResponse(str(fp),filename=filename)

# Web Search and Integrations endpoints removed for fully local version

# ── Voice Tool Endpoints ───────────────────────────────────────────────────────────────

# ── Agentic Execution ────────────────────────────────────────────────────────────
@app.post("/api/agent/execute")
async def api_agent_execute(req: AgentRequest):
    if not llm_engine.is_loaded():
        raise HTTPException(400,"No model loaded")
    loop = asyncio.get_event_loop()
    result: AgentResult = await loop.run_in_executor(
        None, lambda: agent_tool.execute_task(req.task, llm_engine, req.max_steps))
    return result.model_dump()

if __name__ == "__main__":
    import uvicorn
    # ponytail: localhost bind by default; USB_AI_HOST=0.0.0.0 opts into LAN exposure.
    _host = os.environ.get("USB_AI_HOST", "127.0.0.1").strip() or "127.0.0.1"
    if _API_KEY:
        import hashlib
        print(f"[AUTH] API key set (sha256: {hashlib.sha256(_API_KEY.encode()).hexdigest()[:8]})")
    else:
        print(f"[AUTH] No USB_API_KEY set — endpoints are open (bound to {_host})")
    uvicorn.run("main:app", host=_host, port=8080, reload=False)
