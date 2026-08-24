# Architecture

> How the pieces fit together. Maintained alongside the code; if a comment and this doc disagree, fix the doc.

## Process model

USB AI is a **single-process uvicorn app** (no workers, no multiprocessing). All state lives in:
- `app.state.container` — tool instances, built once at startup
- `app.state.session_store` — session disk JSON, mutated via `SessionStore.save()`
- `app.state.load_lock` — `threading.Lock` protecting Llama model init
- `app.state.rate_limiter` — per-IP token-bucket, in-process only

The single-process constraint is why the rate limiter is stdlib-only (no Redis needed) and why `app.state` is the DI surface.

## Request lifecycle: `/api/chat/stream`

This is the most complex endpoint. Here's what happens:

```
Client                               Server
  |                                       |
  |-- POST /api/chat/stream (SSE) ------->|
  |                                       | auth middleware (optional)
  |                                       | rate limiter check
  |                                       | load session JSON from history/
  |                                       | append user message
  |                                       | save session JSON
  |                                       |
  |<- 200 OK + text/event-stream ----------|
  |                                       | LLMEngine.stream_tokens()
  |  data: {"token": "H"}                 |   → SlidingWindow.build_messages()
  |  data: {"token": "i"}                 |   → Llama create_chat_completion
  |  data: {"token": "!"}                 |   → yield tokens via queue.Queue
  |  data: {"type": "artifact", ...}      |   StreamingArtifactExtractor.push()
  |  data: {"done": true}                 |   → flush remaining artifacts
  |                                       |
  |                                       | finally: append assistant message
  |                                       | save session JSON
  |                                       |
  `-- connection closes`

```

### Sliding window

`LLMEngine` holds a `SlidingWindow`. On each chat turn:

1. `build_messages(user_msg, system)` is called
2. `slide(user_msg)` runs — removes oldest user+assistant pairs while `usage > budget`
3. The trimmed history is sent to `Llama.create_chat_completion`

Budget = `n_ctx - len(system) - len(user_msg) - RESPONSE_RESERVE - SAFETY_BUFFER`

### Streaming artifact extractor

`_StreamingArtifactExtractor` (canonical home: `app/artifacts.py`) watches the token stream as it arrives. When it sees a closing `` ``` `` it emits an `Artifact` Pydantic object. The UI renders these as code blocks with syntax highlighting. This is what makes responses like Claude Code artifacts appear.

## Tool invocation (agent mode)

`/api/agent/execute` runs a loop:

1. Build prompt with tool manifest + last 6 observations
2. Call `LLMEngine.stream_tokens()` to get a structured JSON action
3. `_parse_tool_call()` extracts the action — validates param keys against the manifest allowlist
4. `_execute_action()` dispatches to the named tool
5. On tool error, call LLM again with a "revise" prompt (max 1 retry per step)
6. Repeat up to `max_steps` times

The allowlist at `AgentTool._ALLOWED_PARAMS` (built from `TOOL_MANIFEST`) closes the "LLM invents a path-bypass parameter" hole without a code-level sandbox.

## File security: `_resolve`

Every file operation flows through `_resolve(path)` in `file_tool.py`:

1. Reject `\0` bytes
2. For absolute paths: resolve and check `is_relative_to(ALLOWED_BASE_DIR)` — `is_relative_to` is the correct Windows prefix check
3. For relative paths: try resolving against each allowed base + `~/Desktop`, `~/Documents`
4. Raise `ValueError` if outside allowed list

This is the single chokepoint. No tool may read/write outside this boundary.

## Session persistence

Sessions are stored as JSON files in `history/`. An `_index.json` tracks the list in sorted-by-updated order for fast listing. `SessionStore.list_index()` has an in-memory cache invalidated on every `save()`.

Search (`/api/sessions/search/{query}`) uses an mtime-keyed cache: if `history/sess_xxx.json`'s mtime+size match the cached entry, reuse the parsed dict; otherwise re-parse and update cache.

## Dependency injection

`app/container.py` holds the tool factories. `app/dependencies.py` exposes `Depends(get_xxx)` callables that read from `request.app.state.container`. Tests swap tools with:

```python
from fastapi.testclient import TestClient
app.dependency_overrides[get_file_tool] = lambda: FakeFileTool()
```

## Rate limiting

`app/rate_limit.py` implements a per-IP sliding-window limiter in-process using `threading.Lock` + `collections.defaultdict(deque)`. This works for single-process uvicorn. Multi-worker deployments need slowapi + shared state.

## CORS

Origins pinned to `localhost:8080/127.0.0.1:8080/3000`. No wildcards. `max_age=600` caches preflight for 10 minutes. `allow_headers` narrowed to `Content-Type + Authorization` only.

## Auth

`check_auth` middleware checks `Authorization: Bearer <key>` using `secrets.compare_digest` (constant-time). When `USB_API_KEY` is unset, all endpoints are open (localhost-default). Filesystem-mutating endpoints (`/api/files/write`, `/api/files/debug`) are always 401 when `USB_API_KEY` is not set, even if other endpoints are open.

## The startup gate

At `if __name__ == "__main__"`, if `USB_AI_HOST` is not loopback and `USB_API_KEY` is empty, the process prints a `[FATAL]` message and exits non-zero. This is fail-closed: LAN exposure without auth is rejected at boot, not at first request.