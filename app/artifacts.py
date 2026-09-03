"""Streaming artifact extractor — emits code-block artifacts as they close.

Moved out of main.py after Group 3 router split. Behaviour preserved verbatim
so the test_security.py test continues to pass. # ponytail: block-language table
moved up from main.py unchanged — single source of truth for code-fence MIMEs.
"""
from __future__ import annotations

import re
import time

from schemas import Artifact


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


class StreamingArtifactExtractor:
    """Accumulates streaming text and emits Artifact objects as code blocks close.

    Real-time code-block detection — emits artifacts as blocks close, not at end.
    Used by the chat stream to populate the artifact panel.
    """

    def __init__(self):
        self._buf = ""           # raw accumulated text
        self._open_lang = None   # language of currently open block
        self._open_start = -1
        self._complete: list[Artifact] = []
        # ponytail: cursor to avoid O(n^2) re-scans. only search past this point.
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
                idx = self._buf.find("```", self._scan_pos)
                if idx == -1:
                    self._scan_pos = len(self._buf)
                    break
                rest = self._buf[idx + 3:]
                eol = rest.find("\n")
                lang_raw = rest[:eol] if eol != -1 else rest
                lang_raw = lang_raw.strip().lower()
                self._open_lang = lang_raw if lang_raw else "txt"
                self._open_start = idx
                self._buf = rest[eol + 1:] if eol != -1 else ""
                self._scan_pos = 0
            else:
                close = self._buf.find("```", self._scan_pos)
                if close == -1:
                    self._scan_pos = len(self._buf)
                    break
                code = self._buf[:close]
                self._complete.append(self._make_artifact(self._open_lang, code))
                self._buf = self._buf[close + 3:]
                self._open_lang = None
                self._open_start = -1
                self._scan_pos = 0

    def _make_artifact(self, lang_raw: str, code: str) -> Artifact:
        code = code.strip()
        lang = _CODE_LANGS.get(lang_raw, lang_raw)
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


def code_blocks(text: str) -> list[dict]:
    return [{"lang": m.group(1) or "txt", "code": m.group(2).strip()}
            for m in re.finditer(r"```(\w+)?\n(.*?)```", text, re.DOTALL)]


# ponytail: extracted from llm.LLMEngine so BOTH backends (inline engine and
# the llama-server sidecar) share one implementation — the two engines must
# stay behaviorally identical, and copy-pasting this was how drift starts.
# NOTE the two language tables answer DIFFERENT questions (see llm.py):
#   CODE_SAVE_LANGS  -> "should this block be auto-saved?"   (gate)
#   _CODE_LANGS      -> "what file extension does it use?"    (mapping)
CODE_SAVE_LANGS = {"python", "py", "javascript", "js", "typescript", "ts",
                   "html", "css", "java", "cpp", "c", "rust", "go",
                   "bash", "sh", "sql", "json", "yaml", "yml", "xml"}


def generate_code_files(text: str, output_dir: Path) -> list[dict]:
    """Extract code blocks from generated text and save them to output_dir.

    Returns list of dicts: [{"filename", "path", "status",
                             "opened_in_vscode"}, ...]
    """
    from tools.vscode_tool import VSCodeTool
    vscode = VSCodeTool(output_dir)
    saved = []
    for m in re.finditer(r"```(\w+)?\n(.*?)```", text, re.DOTALL):
        lang = (m.group(1) or "txt").lower()
        if lang not in CODE_SAVE_LANGS:
            continue
        code = m.group(2).strip()
        if not code:
            continue
        fn_match = re.search(r"(?:#|//)\s*filename:\s*(\S+)", code)
        filename = fn_match.group(1) if fn_match else \
            f"output_{int(time.time())}.{_CODE_LANGS.get(lang, 'txt')}"
        result = vscode.save_and_open(code, filename, lang)
        saved.append({"filename": filename, "lang": lang,
                      "path": result.get("path", ""),
                      "status": result.get("status", ""),
                      "opened_in_vscode": result.get("opened_in_vscode", False)})
    return saved


# Backward-compat aliases (test_security.py exec-extracts _StreamingArtifactExtractor
# from main.py's source; leaving a stub here lets the legacy source still parse).
_StreamingArtifactExtractor = StreamingArtifactExtractor
_code_blocks = code_blocks
