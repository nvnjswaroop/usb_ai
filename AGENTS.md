# AGENTS.md — Conventions for working on USB AI

> If you're an AI reading this: the codebase self-documents via `# ponytail:`
> comments. Read those before adding to a function — they're the "why" notes,
> not noise. Do not duplicate their content in PRs; link to them.

## Style

- One-line-then-body (matching `if x: do()` on one line is fine if it's truly
  one line; longer = expand).
- ASCII-safe `print()` only — Windows cp1252 crashes on unicode. Both
  `app/main.py` and `app/llm.py` reconfigure stdout to UTF-8 at import.
- Bare `except:` is banned. Catch `(OSError, ValueError)` or narrower.
- Mutable defaults in Pydantic fields: `Field(default_factory=...)`, not `= {}`.
- Constants: `MAX_FILE_SIZE`, `RESPONSE_RESERVE`, `UPLOAD_MAX = 10 * 1024 * 1024`
  in the same module that uses them — no global settings object yet (YAGNI;
  add `app/settings.py` only when a second consumer appears).
- `# ponytail: <why>, <when to upgrade>` on every deliberate shortcut.

## Structure

- `app/main.py` — FastAPI app, routes. Single entrypoint (`if __name__ == "__main__"`).
- `app/llm_server.py` — llama-server sidecar manager + `ServerEngine`
  (default backend). Duck-type-compatible with `llm.LLMEngine` (legacy
  inline, selected via `USB_AI_BACKEND=inline`); parity is enforced by
  tests/test_sidecar.py::TestInterfaceParity. The sidecar child runs in a
  KILL_ON_JOB_CLOSE Job Object — never remove that, it's orphan protection.
- `app/calc.py` — AST evaluator for `/api/calc`. Single source of truth; tests
  import it (no copy in tests/).
- `app/schemas.py` — Pydantic models for artifacts, agent results.
- `app/llm.py` — `LLMEngine` + `SlidingWindow`. Owns the loaded model and the
  chat-format profile per model family.
- `app/tools/` — one file per tool. Stateless except for `_whisper_model`
  (cached lazily on first transcribe) and `VSCodeTool(dotdir)` instances.

## Tests

Seven files+, stdlib `unittest` (httpx ships in core requirements.txt, so
starlette's TestClient works everywhere including CI):

```
python -m unittest discover -s tests -p "test_*.py"   # runs everything
# or individually:
python tests/test_audit.py            # security boundary, sliding window,
                                      # calc, code-tool sandbox, search
python tests/test_security.py         # path-traversal regression, streaming
                                      # extractor, agent JSON parser, schema
                                      # factories, auth matrix, export escaping
tests/test_session_store.py           # SessionStore index/cache round-trips
tests/test_rate_limiter.py            # RateLimiter window semantics
tests/test_file_tool.py               # FileTool resolve/read/write/search
tests/test_concurrent.py              # parallel request behaviour
tests/test_openai_compat.py           # /api/v1/* adapter contract
```

Auth policy invariant (enforced by `TestAuthMatrix`): when `USB_API_KEY` is
set every `/api/*` route returns exactly 401 without a Bearer token; when
unset everything except `/api/files/write|debug` and the env-gated exec
routes is reachable on loopback. Both layers read `dependencies.get_api_key()`
— never cache the key in a module global.

Gotcha: `test_security.py` source-extracts some methods via `exec(...)` rather
than importing them, to avoid importing `main.py`'s transitive deps
(`python-pptx`, `fastapi`). When you move a method out of `main.py`, this
breaks — update the source-extraction sites too.

## Commits

Subject-style: `phase A — security + error-handling hardening`, body is a flat
bullet list of intent (one line per change, no semicolons piling 4 ideas into
one). Why this exists: review-replay — when a regression ships, "what was this
supposed to do" is one line, not paragraph-reading.

## Where NOT to add

- New dependency for "just a few lines" — the ladder rules that out.
- A `routes/` package split — `main.py` is 715 lines, workable. Only split
  when adding a 4th contributor or crossing ~1500 lines.
- A custom FTS index for `search_content` — add when profiling shows it's hot.
- A heavier sandbox for `code_tool.run_python` — the ctypes Job Object
  (Windows) and rlimit (POSIX) now cover CPU/RAM/tree-kill. What's still out:
  syscall filtering (seccomp/AppContainer) — needs a real container story,
  not more ctypes.

## The ladder (applied to this codebase)

1. Does this need to exist at all? — yes, you asked.
2. Already in this codebase? — `ponytail:` comments are the spec.
3. Stdlib does it? — yes, this whole stack is stdlib + 5 pinned deps.
4. Native platform feature covers it? — yes (FastAPI SSE, llama.cpp CPU).
5. Already-installed dep solves it? — yes, don't reach for new tools.
6. One line? — when possible, see examples in main.py:633 area (1-line
   streamed upload cap).
7. Only then: the minimum code that works.

## Import layout — deliberate

`app/` uses FLAT imports (`from schemas import …`, not `from app.schemas`)
with the app dir on `sys.path`. This is a decision, not an accident:

- launch.bat owns `PYTHONPATH=%USB%app`; main.py bootstraps for direct runs.
- Flat imports guarantee ONE module identity everywhere (no
  `main` vs `app.main` double-import splitting singletons like RateLimiter).
- Migrate to package-relative only when a second entrypoint or external
  importer appears — until then it's churn with regression risk.
