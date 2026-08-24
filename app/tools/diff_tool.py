"""
Diff Tool - show changes between two texts or files.

## Why this module exists

The chat UI surfaces diffs next to file-debugger output so a user can see what
the AI changed in a targeted file. Without an explicit unified-diff tool, the
agent would either rewrite files blindly (no review surface) or build diffs ad
hoc per request (no test surface).

## Failure modes

- diff_files(): If a path is outside ALLOWED_BASE_DIRS, _resolve raises
  ValueError — caught here and returned as a status="error" payload.
- Non-UTF-8 file content: read_text(errors="replace") substitutes replacement
  characters rather than raising — preserves the path/comparison for the user.
"""
import difflib
from pathlib import Path
from tools.file_tool import _resolve


class DiffTool:
    def diff_texts(self, old: str, new: str,
                   old_name: str = "original", new_name: str = "modified") -> dict:
        old_lines = old.splitlines(keepends=True)
        new_lines = new.splitlines(keepends=True)
        diff = list(difflib.unified_diff(old_lines, new_lines,
                                          fromfile=old_name, tofile=new_name, lineterm=""))
        added   = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
        return {
            "status":  "ok",
            "diff":    "\n".join(diff),
            "added":   added,
            "removed": removed,
            "changed": added + removed,
        }

    def diff_files(self, path_a: str, path_b: str) -> dict:
        pa, pb = Path(path_a), Path(path_b)
        if not pa.exists():
            return {"status": "error", "message": f"File not found: {path_a}"}
        if not pb.exists():
            return {"status": "error", "message": f"File not found: {path_b}"}
        try:
            text_a = _resolve(path_a).read_text(encoding="utf-8", errors="replace")
            text_b = _resolve(path_b).read_text(encoding="utf-8", errors="replace")
            return self.diff_texts(text_a, text_b, pa.name, pb.name)
        except (OSError, ValueError, UnicodeDecodeError) as e:
            # ponytail: narrow catch — _resolve raises ValueError on
            # out-of-allowlist; .read_text raises OSError/UnicodeDecodeError.
            # Don't swallow arbitrary Exception (per AGENTS.md).
            return {"status": "error", "message": str(e)}
