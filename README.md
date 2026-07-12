# USB AI

**100% Local · 100% Private · Runs from a USB Drive**

A portable AI assistant that runs entirely on your machine. No cloud. No internet required after setup. Your data never leaves your device.

---

## Features

| Feature | Description |
|---|---|
| **Local LLMs** | GGUF models (Qwen, Llama, Gemma, Mistral, Phi, DeepSeek) via llama-cpp-python |
| **Vision** | Image analysis with Gemma 4, LLaVA, Moondream, Qwen2-VL, MiniCPM-V |
| **Sliding Window** | Intelligent context management — oldest messages dropped when context fills up |
| **File Browser** | Read, write, and browse any file anywhere on your system |
| **PDF Reader** | Extract text from PDFs (pymupdf), with OCR for scanned pages |
| **Code Runner** | Write and execute Python instantly, see output inline |
| **AI Debugger** | Point at a file, the AI fixes it in place with automatic `.bak` backup |
| **Diff Viewer** | Side-by-side comparison of two files |
| **Calculator** | Math solver with advanced functions (sqrt, sin, cos, log, etc.) |
| **PPT Generator** | AI generates PowerPoint presentations |
| **Agent Mode** | Autonomous multi-step task execution (read files → write code → run Python) |
| **Custom Personas** | Switch between AI character prompts |
| **Chat Sessions** | Full session history with full-text search |
| **Export** | Save chats as HTML or Markdown |
| **Voice Input** | Speech-to-text using local Whisper (offline) |

---

## Requirements

**Operating system:**
- Windows 10 (1809+) or Windows 11 — setup runs `setup.bat`
- macOS 11+ or any modern Linux — setup runs `setup.sh`

**CPU:**
- 64-bit x86 (Intel/AMD)
- **AVX2 instruction set required.** Most CPUs since 2013 have it:
  - Intel Haswell (4th gen, 2013) or newer — covers Core i3/i5/i7/i9 from 2013 onwards
  - AMD Carrizo (2015) or newer — covers Ryzen, Threadripper, EPYC
  - Older CPUs (pre-2013 Intel, pre-2015 AMD) are not supported.

**RAM:**
- 8 GB minimum (16 GB recommended for 7B+ models)
- Adds ~1 GB per 1B parameters at Q4 quantization

**Disk space:**
- ~500 MB for the app + Python + dependencies
- ~5 GB headroom for models (0.8B–8B GGUF range)

**Internet (one-time only, for setup):**
- Required for first run to install Python packages and pull the GGUF model
- After setup: works fully offline

**For Windows users specifically:**
- If you've never installed Python 3.11, `setup.bat` will install it for you via:
  - Your system Python (if 3.11 is already installed), or
  - winget (built into Windows 10 1809+ and Windows 11), or
  - A portable embeddable Python (last resort, slower)
- No admin rights required.

**Optional:**
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
- **Qwen 2.5 7B** — fast, excellent quality, good code understanding
- **Llama 3.1 8B** — versatile, widely supported
- **Phi-3 Mini** — smallest footprint, surprisingly capable

Vision models (Gemma 4 E4B, LLaVA) also supported — place the matching `mmproj*` file alongside the model.

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

- **Run Code** — Automatically execute Python code blocks in the response
- **Save Code** — Save code blocks to `output/` and open in VS Code
- **Agent** — Enable autonomous multi-step tool chaining (opens a second AI loop)

### Personas

Use the Personas dropdown to switch between:

- **Chat** — General helpful assistant
- **Coder** — Expert software engineer, adds `# filename:` hints to code
- **Teacher** — Patient step-by-step explanations
- **Doctor** — Medical information (always recommends seeing a real doctor)
- **Math** — Shows all working step by step
- **Vision** — Image analysis mode

### File Browser

Click the folder icon to browse any file on your system. Read/write any text file, browse directories, or navigate to a specific path.

### Image Analysis

Paste an image (`Ctrl+V`) in chat mode to send it to a vision-capable model. Load Gemma 4 E4B or LLaVA to enable this.

---

## Updating llama-cpp-python

When you try a new model family (e.g. Gemma 4, Qwen3), you may need a newer `llama-cpp-python`. Run `update_llama.bat` to upgrade.

---

## Troubleshooting

**Server won't start / port 8080 in use:**
Run `launch.bat` again — it will kill any existing process on port 8080 first.

**Model fails to load with KV cache error:**
This means your model was built for a different context size than requested. The app now auto-detects the model's context size from the GGUF header and caps it automatically. If the error persists, try a different model file or reinstall `llama-cpp-python` via `update_llama.bat`.

**No voice from TTS on Windows:**
Make sure a TTS voice is installed in Windows Settings → Accessibility → Speech → Add voices.

**No voice from TTS on Linux:**
Install espeak: `sudo apt install espeak` (or `espeak-ng`)

**Whisper transcription fails:**
Make sure `ffmpeg` is installed and in your PATH. Download from https://ffmpeg.org

---

## Architecture

```
usb_ai/
  app/
    main.py              FastAPI server (30+ endpoints, SSE streaming)
    llm.py              LLM engine
      SlidingWindow       Context management (oldest pairs dropped first)
      LLMEngine           Model loading, GGUF parsing, streaming, vision
    schemas.py           Pydantic models (Artifact, AgentResult, ToolCall)
    tools/
      file_tool.py        Read/write/browse any file on the system
      code_tool.py        Python subprocess execution, 30s timeout
      pdf_tool.py         pymupdf + pypdf + OCR fallback
      ppt_tool.py         python-pptx, 3 style presets
      voice_tool.py        Speech-to-text with local Whisper (offline)
      vscode_tool.py       Save code, open in VS Code, fix in place
      image_tool.py        Image → base64 for vision models
      diff_tool.py         Unified diff between two files
      export_tool.py       Export session to HTML or Markdown
      agent_tool.py        Autonomous multi-step agent with JSON actions
---

| Key | Action |
|---|---|
| `Enter` | Send message |
| `Shift + Enter` | New line in input |
| `Ctrl + V` | Paste image into chat |

---

## Settings Reference

| Setting | What it does |
|---|---|
| System Prompt | Customises the AI's personality (persona) |
| Temperature | 0 = precise and factual, 2 = creative and varied |
| Max Tokens | Hard limit on response length per turn |
