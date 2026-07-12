# SECURITY.md — What USB AI enforces, and what it doesn't

> Honest list. If a section says "documented-as-not" — that's a real hole
> someone added on purpose. The agent can call `read_file` on a path the LLM
> invents; `_resolve` mitigates, doesn't eliminate.

## Enforced

### Trust boundaries — `app/tools/file_tool.py`

- `_resolve(path)` is the single chokepoint for every file read/write/browse
  path. Rejects null bytes, rejects paths outside `ALLOWED_BASE_DIRS`, normalizes
  `..` via `Path.resolve()` before allowlist check.
- `ALLOWED_BASE_DIRS` is hard-coded: project `output/`, `models/`, `history/`,
  `whisper_models/`, plus `~/Desktop` and `~/Documents`. Add new dirs by editing
  this list, not bypassing `_resolve`.
- Read uses `Path.is_relative_to(base.resolve())` — exactly the right Windows
  prefix check (rejects `C:\Users\X\DesktopY` vs `C:\Users\X\Desktop`).
- File reads cap at 1MB (`MAX_FILE_SIZE`). Writes allowed only on
  `SAFE_TEXT_EXTENSIONS` (~50 known suffixes).

### Calc endpoint — `app/calc.py`

- AST-walked whitelist evaluator. Never `eval()`. Whitelisted: arithmetic
  operators, `sin/cos/tan/sqrt/log/log10/exp/floor/ceil/degrees/radians`, `pi`,
  `e`, `abs/round/min/max/sum/pow/int/float`.
- Inf/NaN result is rejected (`math.isinf`/`isnan`). `1/0` returns
  `{"status":"error"}` 200, not 500.

### Upload size cap

- `/api/files/upload`, `/api/pdf/upload`, `/api/image/upload` all stream 64KB
  chunks with a hard 10MB cap. Over-cap → `HTTPException(413)` and the partial
  file is `unlink()`ed. No upload reads into RAM.

### Auth — `app/main.py:check_auth`

- Opt-in via `USB_API_KEY` env var. Empty/missing → endpoints open (designed
  for localhost).
- When set, comparison uses `secrets.compare_digest` (constant-time, no timing
  leak).
- Startup prints SHA-256[:8] of the key for identification, never the key.
- CORS pinned to `http://localhost:8080/127.0.0.1:8080/3000` — not wildcard.

### Bind default

- uvicorn binds `127.0.0.1` by default. Opt into LAN with `USB_AI_HOST=0.0.0.0`.
  (Was hard-coded `0.0.0.0` before, closed inadvertently for a shared-network
  user.)

### Streaming chat

- Stream-error path does NOT persist an empty assistant message into session
  history (this was polluting it with null turns).
- `_loading` race in `/api/models/load` is guarded with a real lock — no two
  POSTs can double-init the Llama object.

## Documented-as-not (real holes, named explicitly)

### `code_tool.run_python` — sandbox is minimal

Per `code_tool.py:23-38`: temp cwd + stripped env + (3-line)`resource` rlimit
on Linux only. Not seccomp, not firejail. Tied to:

- **Linux:** 2s CPU and 512MB RAM via `resource.setrlimit`. Stops fork-bombs
  and large-alloc, not arbitrary system calls.
- **Windows:** no rlimit (Windows lacks preexec_fn). A malicious code string
  on Windows can call `subprocess`, read `HOMEPATH`, write anywhere.

Upgrade path: `pywin32` + `psutil.Process().suspend()`; or shell out to
PowerShell's `Start-Process -Job` with memory limits. Or block the endpoint
behind `USB_AI_AGENT_CODE=1` opt-in.

### `vscode_tool.fix_file_in_place` only enforces `_resolve` since the recent
harden — any prior caller bypassing `_resolve` is a backdoor. The current
implementation routes everything through it.

### PPT-JSON parse failure (rare)

When the LLM hallucinates non-JSON, `/api/ppt/generate` returns 500 with the
parse error, plus a server-side log of the raw LLM output (truncated to 500
chars). No untrusted input reaches downstream.

### Whisper transcription is fully local

`openai-whisper` is `pip install`-ed, runs on CPU, no network calls. Models
download once via `download_whisper.bat`. Voice never leaves the host.

## What's intentionally out

- HTTPS / TLS. Run on localhost; expose behind a reverse proxy for LAN.
  (`Caddy`/`nginx` with self-signed is the minimal move.)
- Rate limiting. Single-user localhost app — the larger point of this tool
  is "no SaaS, your data, your machine." Add when crossing to multi-user.
- `aiofiles` is in `requirements.txt` but unused — pending removal. Doesn't
  affect security.

## Reporting

This is an offline tool. If you find a security issue, fix it locally. The
README has the architecture diagram.
