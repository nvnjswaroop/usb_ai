"""
File Tool - read/write/browse ANY file on the system (not just USB)
"""
import os
import sys
from pathlib import Path

SAFE_TEXT_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".json", ".md",
    ".txt", ".csv", ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".env",
    ".sh", ".bat", ".ps1", ".c", ".cpp", ".h", ".java", ".rs", ".go", ".rb",
    ".php", ".sql", ".r", ".swift", ".kt", ".dart", ".vue", ".svelte",
    ".graphql", ".log", ".conf", ".config", ".gitignore", ".dockerfile",
}

MAX_FILE_SIZE = 1 * 1024 * 1024  # 1MB

# Define allowed base directories for file operations
ALLOWED_BASE_DIRS = [
    Path(__file__).parent.parent / "output",  # OUTPUT_DIR
    Path(__file__).parent.parent / "models",  # MODELS_DIR
    Path(__file__).parent.parent / "history",  # HISTORY_DIR
    Path(__file__).parent.parent / "whisper_models",  # WHISPER_DIR
    Path.home() / "Desktop",
    Path.home() / "Documents",
]

def _is_path_allowed(path: Path) -> bool:
    """Check if a path is within allowed directories."""
    try:
        resolved_path = path.resolve()
        # Check if path is within any allowed base directory
        for base_dir in ALLOWED_BASE_DIRS:
            if base_dir.exists() and resolved_path.is_relative_to(base_dir.resolve()):
                return True
        # Also allow paths relative to current working directory ONLY if cwd
        # is itself inside an allowed dir — closes the cwd `..` hole.
        cwd_resolved = Path.cwd().resolve()
        for base_dir in ALLOWED_BASE_DIRS:
            if base_dir.exists() and cwd_resolved.is_relative_to(base_dir.resolve()):
                if resolved_path.is_relative_to(cwd_resolved):
                    return True
        return False
    except (OSError, ValueError):
        return False

def _resolve(path: str) -> Path:
    """Resolve path — restricts access to allowed directories only."""
    p = Path(path.strip())

    # Reject null bytes
    if "\0" in path:
        raise ValueError("Invalid path")

    # For absolute paths, ensure they're within allowed directories
    if p.is_absolute():
        if not _is_path_allowed(p):
            raise ValueError(f"Access denied: path outside allowed directories")
        return p

    # For relative paths, try to resolve within allowed directories.
    # Write paths may not exist yet — accept any candidate inside an allowed dir.
    base_parents = [Path(__file__).parent.parent,  # OUTPUT/MODELS/HISTORY/WHISPER parents
                    Path.home(),
                    Path.home() / "Desktop", Path.home() / "Documents"]
    for base in base_parents:
        candidate = (base / p).resolve()
        if _is_path_allowed(candidate):
            return candidate
    # ponytail: cwd-relative read was previously allowed here — closed because
    # an LLM-supplied ".." path slipped back into cwd and read arbitrary files.
    # If a user genuinely needs relative-to-launch-dir reads, add launch_dir
    # to ALLOWED_BASE_DIRS explicitly — don't silently fall back.

    # Reject relative paths not found in allowed directories
    raise ValueError(f"Access denied: path not found in allowed directories")


class FileTool:
    def read_file(self, path: str) -> dict:
        p = _resolve(path)
        if not p.exists():
            return {"status": "error", "message": f"File not found: {path}"}
        if not p.is_file():
            return {"status": "error", "message": f"Not a file: {path}"}
        if p.suffix.lower() not in SAFE_TEXT_EXTENSIONS:
            return {"status": "error", "message": f"Unsupported type: {p.suffix}. Supported: {', '.join(sorted(SAFE_TEXT_EXTENSIONS))}"}
        size = p.stat().st_size
        if size > MAX_FILE_SIZE:
            return {"status": "error", "message": f"File too large: {size/1024:.0f}KB (max 1MB)"}
        try:
            content = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                content = p.read_text(encoding="latin-1")
            except Exception as e:
                return {"status": "error", "message": f"Cannot read file: {e}"}
        return {
            "status":    "ok",
            "path":      str(p),
            "filename":  p.name,
            "extension": p.suffix,
            "lines":     content.count("\n") + 1,
            "size_kb":   round(size / 1024, 2),
            "content":   content,
        }

    def write_file(self, path: str, content: str) -> dict:
        p = _resolve(path)
        if p.suffix.lower() not in SAFE_TEXT_EXTENSIONS:
            return {"status": "error", "message": f"Unsupported type: {p.suffix}"}
        action = "updated" if p.exists() else "created"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return {
            "status":   "ok",
            "path":     str(p),
            "filename": p.name,
            "action":   action,
            "lines":    content.count("\n") + 1,
            "size_kb":  round(p.stat().st_size / 1024, 2),
        }

    def list_directory(self, path: str) -> dict:
        """List directory — supports any path on the system."""
        # Special token to list drives on Windows
        if path in ("__drives__", "", "/drives"):
            return self._list_drives()

        p = _resolve(path)
        if not p.exists():
            return {"status": "error", "message": f"Path not found: {path}"}
        if not p.is_dir():
            return {"status": "error", "message": f"Not a directory: {path}"}

        entries = []
        try:
            items = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
            for item in items:
                try:
                    entry = {
                        "name":      item.name,
                        "type":      "file" if item.is_file() else "directory",
                        "extension": item.suffix.lower() if item.is_file() else "",
                        "size_kb":   round(item.stat().st_size / 1024, 2) if item.is_file() else None,
                        "readable":  item.suffix.lower() in SAFE_TEXT_EXTENSIONS if item.is_file() else False,
                    }
                    entries.append(entry)
                except (PermissionError, OSError):
                    entries.append({"name": item.name, "type": "directory" if item.is_dir() else "file",
                                    "extension": "", "size_kb": None, "readable": False, "locked": True})
        except PermissionError:
            return {"status": "error", "message": f"Access denied: {path}"}

        return {"status": "ok", "path": str(p), "entries": entries}

    def _list_drives(self) -> dict:
        """List available drives (Windows) or root dirs (Linux)."""
        entries = []
        if sys.platform == "win32":
            import string
            for letter in string.ascii_uppercase:
                drive = Path(f"{letter}:\\")
                if drive.exists():
                    entries.append({
                        "name": f"{letter}:\\", "type": "directory",
                        "extension": "", "size_kb": None, "readable": False,
                    })
        else:
            # Linux/Mac — show root and home
            for p in [Path("/"), Path.home(), Path("/mnt"), Path("/media")]:
                if p.exists():
                    entries.append({
                        "name": str(p), "type": "directory",
                        "extension": "", "size_kb": None, "readable": False,
                    })
        return {"status": "ok", "path": "__drives__", "entries": entries}

    # ponytail: simple recursive scan; upgrade to FTS index if search becomes hot
    def search_content(self, path: str, query: str) -> dict:
        p = _resolve(path)
        if not p.is_dir():
            return {"status": "error", "message": f"Not a directory: {path}"}
        if not query:
            return {"status": "error", "message": "Empty query"}
        q = query.lower()
        hits, scanned = [], 0
        for f in p.rglob("*"):
            if not f.is_file():
                continue
            if f.suffix.lower() not in SAFE_TEXT_EXTENSIONS:
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            scanned += 1
            for i, line in enumerate(text.splitlines(), 1):
                if q in line.lower():
                    hits.append({"file": str(f), "line": i, "text": line.strip()[:200]})
                    if len(hits) >= 100:
                        return {"status": "ok", "hits": hits, "scanned": scanned,
                                "truncated": True}
        return {"status": "ok", "hits": hits, "scanned": scanned}
