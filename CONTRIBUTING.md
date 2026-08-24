# Contributing to USB AI

Thanks for your interest in making USB AI better. The project is intentionally
small — one process, one developer, stdlib + 5 pinned deps. The bar for
"good contribution" matches that.

## Before opening a PR

```bash
python -m pytest tests/           # 95+ tests, ~10 seconds
python scripts/update_lock.py     # only if you touched scripts/fetch_wheels.py
```

Both must be green. CI also runs `pip-audit` on `requirements.txt` — don't bump
pins without a reason.

## Style

The full style guide is in [AGENTS.md](AGENTS.md). The TL;DR:

- `# ponytail: <why>, <when to upgrade>` on every deliberate shortcut.
- Bare `except:` is banned. Catch `(OSError, ValueError)` or narrower.
- Pydantic mutable defaults: `Field(default_factory=...)`, not `= {}`.
- One-line-then-body (`if x: do()` is fine if it's truly one line).
- Constants live in the same module that uses them — no global settings object.

## Install-script changes

If you touch `setup.bat`, `setup.sh`, or `requirements.txt`, run all three:

1. `pytest tests/test_install_drift.py -v` — catches setup.bat / setup.sh / requirements.txt desync.
2. `python scripts/fetch_wheels.py --force` on a clean machine.
3. `python scripts/update_lock.py` to regenerate `requirements.lock`.

The `--extra-index-url` flag (vs `--index-url`) is **load-bearing** — without
the fallback to PyPI, fresh installs fail on `diskcache`. Don't change it.

## What we're NOT taking

- New top-level dependencies. The dependency ladder is at rung-5 (stdlib +
  what's already installed). Adding a dep needs a one-paragraph justification
  in the PR description.
- A custom FTS index for session search. Add when profiling shows it's hot.
- A Windows-specific sandbox for `code_tool.run_python`. The current rlimit
  sandbox is acknowledged insufficient in [SECURITY.md](SECURITY.md); the
  upgrade path requires `pywin32`, which is rung-6 (out of scope).

## Reporting bugs

Open an issue with:
- What you did (the exact request / command)
- What you expected
- What happened (full error, not paraphrased)
- `python -c "import sys, platform; print(sys.version, platform.platform())"` output

## Security

The codebase has been through multiple hardening passes (Phase A–D). Read
[SECURITY.md](SECURITY.md) before reporting a security issue — your finding
may already be documented as "known limitation, deferred to N".
