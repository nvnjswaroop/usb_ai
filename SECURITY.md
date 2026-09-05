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
  `SAFE_WRITE_EXTENSIONS` (writes) / `SAFE_TEXT_EXTENSIONS` (reads —
  includes .html/.js for analysis; neither admits .env, .bat, .ps1, .sh).

### Session ids — `app/sessions.py`

- `SessionStore._path` rejects any sid not matching `^[A-Za-z0-9_-]{1,64}$`:
  an absolute sid would REPLACE the history base; `../` escapes via the
  `.json` suffix; `%5C`-encoded backslashes survive Starlette path matching
  on Windows (all three proven live, audit 2026-09-05). Request models
  (`ChatRequest`, `ExportRequest`) and `{sid}` path params enforce the same
  pattern at the HTTP layer (422).

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

### Auth — `app/main.py:check_auth` + `app/dependencies.py`

- Opt-in via `USB_API_KEY` env var. Empty/missing → `/api/*` open on loopback
  by design (`launch.bat` never sets a key; README Quick Start expects the UI
  to work immediately). Both the middleware and `require_api_key` read the key
  via `dependencies.get_api_key()` per-call — one source of truth, no cached
  module globals that can drift apart.
- Filesystem-mutating routes (`/api/files/write`, `/api/files/debug`) mount
  `require_key_always` — they fail-closed 401 even when no key is configured.
  New mutating routes must add this dependency (enforced by
  `TestAuthMatrix`).
- When set, comparison uses `secrets.compare_digest` (constant-time, no timing
  leak). Every `/api/*` route returns exactly 401 without a Bearer token.
- Startup prints SHA-256[:8] of the key for identification, never the key.
- CORS pinned to `http://localhost:8080/127.0.0.1:8080/3000` — not wildcard.
- Rate limiting: process-wide per-IP sliding window, 30 req/min, wired into
  every data endpoint; idle buckets evicted every 5 min.

### LLM sidecar (`app/llm_server.py`)

- The default backend spawns the official prebuilt `llama-server` binary
  (sha256-pinned in `llama_server.lock`; nothing is ever compiled).
- Child binds **127.0.0.1 on an ephemeral port** — never exposed beyond
  loopback regardless of `USB_AI_HOST`.
- Orphan protection: on Windows the child joins a KILL_ON_JOB_CLOSE Job
  Object, so even a hard parent crash/taskkill reaps it (no zombie holding
  RAM + port). POSIX relies on lifespan/atexit stop paths.
- Binary placement is explicit: `bin/llama/`, `USB_AI_LLAMA_DIR`, or
  `USB_AI_LLAMA_SERVER` — an override that misses is an error, not a silent
  fallback to another location.

### Bind default + runtime LAN guard

- uvicorn binds `127.0.0.1` by default. Opt into LAN with `USB_AI_HOST=0.0.0.0`.
  (Was hard-coded `0.0.0.0` before, closed inadvertently for a shared-network
  user.)
- The `__main__` bind gate is convenience, not the boundary — launching via
  `uvicorn main:app --host 0.0.0.0` skips it. The AUTHORITATIVE guard is in
  the auth middleware: any non-loopback client IP without a valid
  `USB_API_KEY` gets 403 on every `/api/*` route (health excepted). This
  cannot be bypassed by launcher choice — the client's address is always
  visible at request time.

### Streaming chat

- Stream-error path does NOT persist an empty assistant message into session
  history (this was polluting it with null turns).
- `_loading` race in `/api/models/load` is guarded with a real lock — no two
  POSTs can double-init the Llama object.

## Documented-as-not (real holes, named explicitly)

### `code_tool.run_python` — layered sandbox

Per `code_tool.py`: temp cwd + stripped env, plus platform memory/CPU caps:

- **Linux/macOS:** `resource.setrlimit` — 2s CPU and 512MB RAM.
- **Windows:** ctypes **Job Object** (stdlib, no pywin32) — 512MB process
  memory ceiling + KILL_ON_JOB_CLOSE, so timeout kills the whole process
  tree. Assigned per-run; job-creation failure is logged and the RAM
  watchdog thread + 30s timeout remain as backstops.

Still out: syscall filtering (seccomp/AppContainer) — a malicious code
string can still touch whatever the user account can touch. Upgrade path is
a real container story, not more ctypes. Endpoint stays behind the
`USB_AI_AGENT_CODE=1` opt-in gate + rate limiter + (when configured)
Bearer auth.

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
- `aiofiles` was removed from `requirements.txt` (Group 2 / Week 2 of the 5/5
  roadmap). Was always unused — FastAPI's `UploadFile` / `file.read(...)` covers
  the only async upload paths. The vendored Python under `python/win/` still
  has the wheel on disk but nothing imports it.

## Reporting

This is an offline tool. If you find a security issue, fix it locally. The
README has the architecture diagram.

## OpenAI-compatible bridge (`/api/v1/*`)

Two endpoints (`POST /api/v1/chat/completions`, `GET /api/v1/models`) mirror
the OpenAI Chat Completions API shape so external clients (CyberMatrix and
any other OpenAI-shaped consumer) can drive the local model without code
changes.

**Inherited from `/api/chat/stream`**:

- `USB_API_KEY` opt-in: open on loopback when unset, fail-closed on LAN via
  the same `secrets.compare_digest` check the middleware uses for the rest
  of `/api/*`.
- Per-IP rate-limit: same 30/min budget as `/api/chat/stream`, same
  process-wide `RateLimiter` instance.
- No new attack surface: token counts use the internal `_count` the
  sliding-window already uses; model discovery reuses `paths.models.glob`
  the existing `/api/models` route uses. No new dependencies.

**Not yet implemented**: `stream: true` returns 400. The SSE path lives on
`/api/chat/stream` in USB AI's own format; bridging to OpenAI chunk format
is a deferred add when a client needs streaming.

This is an **adapter**, not a separate LLM gateway — there is one
`LLMEngine` instance, one sliding window, one auth context, regardless of
which endpoint the request arrives on.
