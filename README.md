# USB AI

**100% Local Â· 100% Private Â· Runs from a USB Drive**

A portable AI assistant that runs entirely on your machine. No cloud. No internet required after setup. Your data never leaves your device.

---

## Features

| Feature | Description |
|---|---|
| **Local LLMs** | GGUF models (Qwen, Llama, Gemma, Mistral, Phi, DeepSeek) via llama-cpp-python |
| **Vision** | Image analysis with Gemma 4, LLaVA, Moondream, Qwen2-VL, MiniCPM-V |
| **Sliding Window** | Intelligent context management â€” oldest messages dropped when context fills up |
| **File Browser** | Read, write, and browse any file anywhere on your system |
| **PDF Reader** | Extract text from PDFs (pymupdf), with OCR for scanned pages |
| **Code Runner** | Write and execute Python instantly, see output inline |
| **AI Debugger** | Point at a file, the AI fixes it in place with automatic `.bak` backup |
| **Diff Viewer** | Side-by-side comparison of two files |
| **Calculator** | Math solver with advanced functions (sqrt, sin, cos, log, etc.) |
| **PPT Generator** | AI generates PowerPoint presentations |
| **Agent Mode** | Autonomous multi-step task execution (read files â†’ write code â†’ run Python) |
| **Custom Personas** | Switch between AI character prompts |
| **Chat Sessions** | Full session history with full-text search |
| **Export** | Save chats as HTML or Markdown |
| **Voice Input** | Speech-to-text using local Whisper (offline) |

---

## Requirements

**Operating system:**
- Windows 10 (1809+) or Windows 11 â€” setup runs `setup.bat`
- macOS 11+ or any modern Linux â€” setup runs `setup.sh`

**CPU:**
- 64-bit x86 (Intel/AMD)
- **AVX2 instruction set required.** Most CPUs since 2013 have it:
  - Intel Haswell (4th gen, 2013) or newer â€” covers Core i3/i5/i7/i9 from 2013 onwards
  - AMD Carrizo (2015) or newer â€” covers Ryzen, Threadripper, EPYC
  - Older CPUs (pre-2013 Intel, pre-2015 AMD) are not supported.

**RAM:**
- 8 GB minimum (16 GB recommended for 7B+ models)
- Adds ~1 GB per 1B parameters at Q4 quantization

**Disk space:**
- ~500 MB for the app + Python + dependencies
- ~5 GB headroom for models (0.8Bâ€“8B GGUF range)

**Python version:**
- **Any modern Python 3.10+ works.** The LLM runtime is no longer a pip package â€” `scripts/fetch_llama.py` downloads the official prebuilt `llama-server` binary from ggml's GitHub releases (sha256-pinned in `llama_server.lock`), so there are no cp3xx wheel constraints and nothing is ever compiled.
- `setup.bat` auto-installs an embeddable Python on Windows if you don't have one.
- Legacy escape hatch: set `USB_AI_BACKEND=inline` to use the old in-process engine (requires Python 3.11 + `llama-cpp-python==0.3.19`; not installed by default).

**Internet (one-time only, for setup):**
- Required for first run to install Python packages and pull the GGUF model
- After setup: works fully offline

**For Windows users specifically:**
- If you've never installed Python, `setup.bat` will install a portable embeddable Python for you â€” no admin rights required.

**Optional:**
- `pip install -r requirements-ocr.txt` â€” OCR for scanned PDFs (requires pytesseract + Tesseract binary)
- `ffmpeg` in your PATH for audio transcription (Whisper)
- VS Code (for the "open in editor" feature)
- Microphone (for voice input via Whisper)

---

## Quick Start

### 1. Setup (One-time, requires internet)

**For Windows users:**
```bash
setup.bat
```

**For Mac/Linux users:**
```bash
chmod +x setup.sh
./setup.sh
```

Or simply double-click `setup.bat` on Windows.

> **Note:** On Windows, `setup.bat` automatically finds or installs Python 3.11. On Linux/Mac, you need Python 3.11 or newer already installed (run `python3 --version` to check).

### 2. Add a Model

Drop any `.gguf` model file into the `models/` folder.

Recommended to start with one of:
- **Qwen 2.5 7B** â€” fast, excellent quality, good code understanding
- **Llama 3.1 8B** â€” versatile, widely supported
- **Phi-3 Mini** â€” smallest footprint, surprisingly capable

Vision models (Gemma 4 E4B, LLaVA) also supported â€” place the matching `mmproj*` file alongside the model.

### 3. Run

**For Windows users:**
```bash
launch.bat
```

**For Mac/Linux users:**
```bash
chmod +x launch.sh
./launch.sh
```

Open `http://localhost:8080` in your browser.



---

## Supported Models

Place any GGUF-format model (`.gguf`) in the `models/` folder.

| Model Family | Supported | Notes |
|---|---|---|
| **Qwen** 2/2.5/3 | Yes | Best all-round for code + chat |
| **Llama** 3 / 3.1 | Yes | chatml format |
| **Gemma** 2 / 3 | Yes | Gemma format. E4B supports vision |
| **Mistral** / Nemo | Yes | mistral-instruct format |
| **Phi-3** | Yes | chatml format |
| **DeepSeek** | Yes | chatml format |
| **LLaVA** / BakLLaVA | Yes | Vision. Needs `mmproj*` file |
| **Qwen2-VL** | Yes | Vision. Needs `mmproj*` file |
| **MiniCPM-V** | Yes | Vision. Needs `mmproj*` file |
| **Moondream** | Yes | Vision. Needs `mmproj*` file |

---

## Usage

### Voice Input

Run `download_whisper.bat` once to download the Whisper STT model. Choose `tiny` (75MB, fast), `base` (145MB, recommended), or `small` (480MB, most accurate). After that, the microphone button works fully offline.

### Feature Chips

Before sending a message, enable chips to unlock extra capabilities:

- **Run Code** â€” Automatically execute Python code blocks in the response
- **Save Code** â€” Save code blocks to `output/` and open in VS Code
- **Agent** â€” Enable autonomous multi-step tool chaining (opens a second AI loop)

### Personas

Use the Personas dropdown to switch between:

- **Chat** â€” General helpful assistant
- **Coder** â€” Expert software engineer, adds `# filename:` hints to code
- **Teacher** â€” Patient step-by-step explanations
- **Doctor** â€” Medical information (always recommends seeing a real doctor)
- **Math** â€” Shows all working step by step
- **Vision** â€” Image analysis mode

### File Browser

Click the folder icon to browse any file on your system. Read/write any text file, browse directories, or navigate to a specific path.

### Image Analysis

Paste an image (`Ctrl+V`) in chat mode to send it to a vision-capable model. Load Gemma 4 E4B or LLaVA to enable this.

---

## Updating the LLM runtime

When you try a new model family (e.g. Gemma 4, Qwen3), you may need a newer llama.cpp build. Run:

```bash
python scripts/fetch_llama.py --list        # see the latest prebuilt assets
# bump "build" + URLs in llama_server.lock, then:
python scripts/fetch_llama.py --variant cpu --write-lock
```

No compilers involved â€” it's a sha256-verified download of ggml's own release binary.

---

## Troubleshooting

**Server won't start / port 8080 in use:**
Run `launch.bat` again â€” it will kill any existing process on port 8080 first.

**Model fails to load with KV cache error:**
This means your model was built for a different context size than requested. The app now auto-detects the model's context size from the GGUF header and caps it automatically. If the error persists, try a different model file or reinstall `llama-cpp-python` via `update_llama.bat`.

**No voice from TTS on Windows:**
Make sure a TTS voice is installed in Windows Settings â†’ Accessibility â†’ Speech â†’ Add voices.

**No voice from TTS on Linux:**
Install espeak: `sudo apt install espeak` (or `espeak-ng`)

**Whisper transcription fails:**
Make sure `ffmpeg` is installed and in your PATH. Download from https://ffmpeg.org


```
```


---

## Architecture

After the Group 3 refactor, `main.py` is thin glue (~200 lines). The server is split into routers:

```
app/
  main.py              FastAPI app + CORS + auth middleware + startup
  container.py         Tool factories (DI container)
  dependencies.py      FastAPI Depends() surface
  sessions.py          SessionStore (disk + index + caches)
  artifacts.py         StreamingArtifactExtractor (code-block â†’ Artifact)
  llm.py               LLMEngine + SlidingWindow
  schemas.py           Pydantic models (Artifact, AgentResult, ToolCall)
  routers/
    system.py          /, /api/status, /api/models, /api/models/load, /api/health
    sessions.py        /api/sessions/*, /api/sessions/search/*
    chat.py            /api/chat/stream
    files.py           /api/files/*, /api/preview, /api/outputs
    media.py           /api/pdf/*, /api/image/*
    voice.py           /api/voice/*
    ppt.py             /api/ppt/*
    calc.py            /api/calc
    export.py          /api/export
    code.py            /api/code/*
    agent.py           /api/agent/execute (opt-in)
  tools/
    file_tool.py       _resolve() security chokepoint + read/write/browse
    code_tool.py       subprocess Python execution, Linux rlimit only
    pdf_tool.py        pymupdf + pypdf + OCR fallback
    ppt_tool.py        python-pptx, 3 style presets
    voice_tool.py      TTS (token-bucket + 4-worker emit pool) + Whisper STT
    vscode_tool.py     save + open + fix-in-place
    image_tool.py       image â†’ base64 for vision models
    diff_tool.py        unified diff
    export_tool.py     HTML/Markdown session export
    agent_tool.py       autonomous multi-step agent with JSON actions
```

| Key | Action |
|---|---|
| `Enter` | Send message |
| `Shift + Enter` | New line in input |
| `Ctrl + V` | Paste image into chat |

---

## API Endpoints

All endpoints are under `http://localhost:8080`. Auth is required when `USB_API_KEY` is set. When unset, the server is open (design for local-only use). Mutating endpoints are marked explicitly. Rate-limited endpoints show the limit.

| Method | Path | Purpose | Auth | Rate Limit | Mutates |
|---|---|---|---|---|---|
| GET | `/` | Chat UI | â€” | â€” | â€” |
| GET | `/api/status` | Model loaded, available models, personalities | â€” | â€” | â€” |
| GET | `/api/health` | Liveness: model loaded, disk free, uptime | â€” | â€” | â€” |
| GET | `/api/models` | List `.gguf` files in `models/` | â€” | â€” | â€” |
| GET | `/api/models/progress` | Model load progress + last log line | â€” | â€” | â€” |
| POST | `/api/models/load` | Load a model into memory | **Yes** | â€” | â€” |
| POST | `/api/sessions/new` | Reset in-memory sliding window | **Yes** | â€” | â€” |
| GET | `/api/sessions` | List sessions (index, cached) | **Yes** | â€” | â€” |
| GET | `/api/sessions/{sid}` | Full session JSON | **Yes** | â€” | â€” |
| DELETE | `/api/sessions/{sid}` | Delete a session file | **Yes** | â€” | âœ“ |
| POST | `/api/sessions/{sid}/rename` | Update session title | **Yes** | â€” | âœ“ |
| POST | `/api/sessions/{sid}/summarise` | Run summarisation prompt | **Yes** | â€” | â€” |
| GET | `/api/sessions/search/{query}` | Full-text search in session history | **Yes** | 30/min | â€” |
| POST | `/api/chat/stream` | SSE streaming chat | **Yes** | 30/min | âœ“ |
| POST | `/api/calc` | Safe AST-walked arithmetic | â€” | â€” | â€” |
| POST | `/api/export` | Export session as HTML or Markdown | **Yes** | 30/min | â€” |
| POST | `/api/diff/files` | Unified diff between two files | **Yes** | â€” | â€” |
| POST | `/api/files/read` | Read a file via `_resolve` | **Yes** | â€” | â€” |
| POST | `/api/files/write` | Write a file via `_resolve` | **Always** | â€” | âœ“ |
| GET | `/api/files/browse` | List a directory | **Yes** | 30/min | â€” |
| GET | `/api/files/drives` | List drives (Windows) or root dirs | â€” | â€” | â€” |
| POST | `/api/files/upload` | Stream-upload to `output/` (10MB cap) | **Yes** | â€” | âœ“ |
| POST | `/api/files/debug` | AI fix-in-place on a file | **Always** | â€” | âœ“ |
| POST | `/api/pdf/extract` | Extract text from PDF path | **Yes** | â€” | â€” |
| POST | `/api/pdf/upload` | Upload PDF, extract, delete temp | **Yes** | â€” | â€” |
| POST | `/api/image/upload` | Upload image, return base64 for vision | **Yes** | â€” | â€” |
| POST | `/api/code/run` | Execute Python | **Yes** | 30/min | â€” |
| POST | `/api/code/save` | Save code to `output/` and open VS Code | â€” | â€” | âœ“ |
| GET | `/api/preview/{filename}` | Render `output/` file as HTML | â€” | â€” | â€” |
| GET | `/api/outputs` | List files in `output/` | â€” | â€” | â€” |
| GET | `/api/outputs/download/{filename}` | Download `output/` file | â€” | â€” | â€” |
| POST | `/api/voice/speak` | Text-to-speech | â€” | â€” | â€” |
| GET | `/api/voice/voices` | List available TTS voices | â€” | â€” | â€” |
| POST | `/api/voice/transcribe` | Whisper STT (stream to `whisper_models/`) | **Yes** | â€” | â€” |
| POST | `/api/ppt/generate` | Generate PowerPoint from topic | **Yes** | â€” | â€” |
| GET | `/api/ppt/download/{filename}` | Download generated `.pptx` | â€” | â€” | â€” |
| POST | `/api/agent/execute` | Autonomous agent loop (opt-in) | **Yes** | â€” | â€” |
| POST | `/api/v1/chat/completions` | OpenAI-compatible non-streaming chat | **Yes** | 30/min | âœ“ |
| GET | `/api/v1/models` | OpenAI-compatible model list | **Yes** | 30/min | â€” |

*Auth*: **Yes** = requires `USB_API_KEY` when set. **Always** = always requires `USB_API_KEY` even in local-only mode. **opt-in** = route not registered unless env var is set (returns 404).

### OpenAI-compatible bridge

USB AI exposes two endpoints under `/api/v1/*` that mirror the OpenAI Chat Completions API shape. These exist specifically so OpenAI-shaped clients (e.g. [CyberMatrix](.github/workflows) configured with `provider: usb_ai`) can call the local model without server-side code changes.

| Endpoint | Behaviour |
|---|---|
| `POST /api/v1/chat/completions` | Standard OpenAI request/response. Non-streaming only â€” `stream: true` returns 400. Reuses `LLMEngine.stream_tokens` and the sliding-window token counter (the same code path as `/api/chat/stream`). Token counts use the internal `_count` already used for context budgeting â€” no extra tokenizer dependency. |
| `GET /api/v1/models` | Returns `{object: "list", data: [...]}` derived from `paths.models.glob("*.gguf")` â€” same source as `/api/models`, reshaped to OpenAI's list-of-models format. |

Auth: inherits `/api/chat/stream` policy exactly â€” `USB_API_KEY` opt-in (open on loopback when unset, fail-closed on LAN). CyberMatrix clients should send whatever Bearer token they already have configured in `config.yaml`; the comparison uses `secrets.compare_digest` (constant-time). Streaming on this endpoint is deferred â€” `/api/chat/stream` is the SSE path and lives in our own shape; ~30 lines to bridge to OpenAI chunks when a client needs it.

---

## Settings Reference

| Setting | What it does |
|---|---|
| System Prompt | Customises the AI's personality (persona) |
| Temperature | 0 = precise and factual, 2 = creative and varied |
| Max Tokens | Hard limit on response length per turn |

## License Notes

| Dependency | License | Obligation |
|---|---|---|
| Most deps (fastapi, uvicorn, pydantic, llama-cpp-python, python-pptx, pillow, whisperâ€¦) | MIT / Apache-2.0 / BSD | None for normal use |
| **PyMuPDF** (`pymupdf`) | **AGPL-3.0** | If you distribute this app to others as a product (or serve it commercially without releasing source), PyMuPDF requires your source be AGPL too â€” or a commercial pymupdf license from Artifex. Personal/local use is unaffected. Alternative: swap `tools/pdf_tool.py` to `pypdf` (BSD) if you need permissive-only distribution. |

## Code Style: the # ponytail: Convention

`# ponytail: <why>, <when to upgrade>` comments appear throughout the codebase. They document **deliberate shortcuts** â€” decisions made for good reasons that the author knows are non-ideal. They are the living spec of the codebase.

Example:
```python
# ponytail: in-process limiter, single-instance only â€” multi-worker uvicorn
# would need slowapi or shared state.
rate_limiter = RateLimiter(max_per_minute=30)
```

**Do not duplicate a ponytail comment in a PR description.** Link to it instead (e.g. "see ponytail at file_tool.py:42").
