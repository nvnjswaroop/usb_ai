# Contributing to USB AI

Thanks for your interest in making USB AI better. The project is intentionally
small — one process, one developer, stdlib + a handful of pinned deps. The bar
for "good contribution" matches that.

## Before opening a PR

```bash
python -m unittest discover -s tests -p "test_*.py"   # 133+ tests, ~15s
```

Must be green. CI also runs `pip-audit` and the same test suite — don't bump
pins without a reason.

## Style

The full style guide is in [AGENTS.md](AGENTS.md). The TL;DR:

- `# ponytail: <why>, <when to upgrade>` on every deliberate shortcut.
- Bare `except:` is banned. Catch `(OSError, ValueError)` or narrower.
- Pydantic mutable defaults: `Field(default_factory=...)`, not `= {}`.
- One-line-then-body (`if x: do()` is fine if it's truly one line).
- Constants live in the same module that uses them.
- Never put a trailing comment on a `.gitignore` pattern line (git treats the
  whole line as the pattern).

## Install-script changes

If you touch `setup.bat`, `setup.sh`, or `requirements.txt`, keep the
`@feature:` marker blocks intact — `scripts/install.py` parses them to build
the dependency plan. Run the installer in dry-run to sanity-check:

```bash
python scripts/install.py --dry-run --yes
```

## LLM runtime changes

The runtime is the official prebuilt `llama-server` binary, pinned by
`llama_server.lock` (URL + sha256) and fetched via `scripts/fetch_llama.py`.
To bump builds: `--list` to discover assets, update the lock, re-fetch,
commit both the lock and `bin/llama/`. **Never** introduce a step that
compiles anything — prebuilt-only is an invariant, not a preference.

## What we're NOT taking

- New top-level dependencies without a one-paragraph justification in the PR
  (the dependency ladder lives in [AGENTS.md](AGENTS.md)).
- A custom FTS index for session search. Add when profiling shows it's hot.
- Syscall-level sandboxing for `code_tool.run_python`. Job Object (Windows)
  + rlimit (POSIX) cover CPU/RAM/tree-kill; beyond that needs a real
  container story (see [SECURITY.md](SECURITY.md)).

## Reporting bugs

Open an issue with:
- What you did (the exact request / command)
- What you expected
- What happened (full error, not paraphrased)
- `python -c "import sys, platform; print(sys.version, platform.platform())"` output

## Security

The codebase has been through multiple hardening passes. Read
[SECURITY.md](SECURITY.md) before reporting a security issue — your finding
may already be documented as "known limitation, deferred to N".
