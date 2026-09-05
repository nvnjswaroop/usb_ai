"""
VS Code Tool
- Save code to file and open in VS Code
- Debug/fix a file: AI edits it in place
- Find VS Code on the USB or host machine
"""
import os
import re
from logging_config import getLogger
_log = getLogger("usbai")

import subprocess
import shutil
from pathlib import Path
from typing import Optional

# ponytail: import the write allowlist rather than re-declaring it —
# one source of truth next to the constants it depends on.
from tools.file_tool import SAFE_WRITE_EXTENSIONS


def _find_vscode() -> Optional[str]:
    """Find VS Code executable — check USB first, then system."""
    # Common install paths on Windows
    candidates = [
        shutil.which("code"),
        shutil.which("code-insiders"),
        r"C:\Program Files\Microsoft VS Code\bin\code.cmd",
        r"C:\Program Files (x86)\Microsoft VS Code\bin\code.cmd",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\bin\code.cmd"),
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    return None


class VSCodeTool:
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir

    def save_and_open(self, code: str, filename: str, language: str = "") -> dict:
        """
        Save code to a file and open it in VS Code.
        filename: suggested name, e.g. 'solution.py'
        """
        # Sanitise filename
        safe = re.sub(r'[<>:"/\\|?*]', '_', filename).strip() or "output.txt"
        # ponytail: same write allowlist as file_tool.write_file — without
        # this, saving "x.bat" wrote batch content and then auto-opened it
        # (os.startfile EXECUTES .bat) — a write+execute primitive reachable
        # from the agent's save_code_file (audit 2026-09-05).
        if Path(safe).suffix.lower() not in SAFE_WRITE_EXTENSIONS:
            return {"status": "error",
                    "message": f"Unsupported type: {Path(safe).suffix}. "
                               f"Supported: {', '.join(sorted(SAFE_WRITE_EXTENSIONS))}"}
        dest = self.output_dir / safe

        try:
            dest.write_text(code, encoding="utf-8")
        except Exception as e:
            return {"status": "error", "message": f"Could not write file: {e}"}

        vscode = _find_vscode()
        if vscode:
            try:
                subprocess.Popen([vscode, str(dest)], shell=False)
                return {"status": "ok", "path": str(dest), "opened_in_vscode": True}
            except (OSError, ValueError) as e:
                return {"status": "ok", "path": str(dest), "opened_in_vscode": False,
                        "note": f"File saved but VS Code failed to open: {e}"}
        else:
            # ponytail: no auto-open fallback — os.startfile EXECUTES .bat/.cmd
            # and hands .html to the browser from the app's origin. The file is
            # saved; the user opens it on purpose. (audit 2026-09-05)
            return {"status": "ok", "path": str(dest), "opened_in_vscode": False,
                    "note": "VS Code not found — file saved (open it manually)"}

    def fix_file_in_place(self, file_path: str, fixed_code: str) -> dict:
        """
        Overwrite an existing file with the fixed version.
        Used when AI debugs / fixes a file the user pointed to.
        """
        # ponytail: route through file_tool._resolve — the agent already uses
        # allowlisted dirs, but this guard makes the convention explicit so a
        # future caller can't bypass it.
        try:
            from tools.file_tool import _resolve
            p = _resolve(file_path)
        except (OSError, ValueError) as e:
            return {"status": "error", "message": f"Path not allowed: {e}"}
        if not p.exists():
            return {"status": "error", "message": f"File not found: {file_path}"}

        # Backup original
        # ponytail: OSError only — read/write failures on the backup file. We do
        # not catch arbitrary Exception per AGENTS.md; backup is best-effort but
        # logged so a user can see why the .bak didn't appear.
        backup = p.with_suffix(p.suffix + ".bak")
        try:
            backup.write_bytes(p.read_bytes())
        except OSError as e:
            _log.warning(f"VSCODE-BACKUP: backup skipped for {p}: {e}")

        try:
            p.write_text(fixed_code, encoding="utf-8")
        except (OSError, ValueError) as e:
            return {"status": "error", "message": f"Could not write fix: {e}"}

        vscode = _find_vscode()
        if vscode:
            try:
                subprocess.Popen([vscode, str(p)])
            except (OSError, ValueError) as e:
                # ponytail: narrow catches per AGENTS.md — Popen spawn can fail
                # with FileNotFoundError (subclass of OSError) or ValueError
                # (bad args). Don't mask other failures as silent.
                _log.warning(f"VSCODE-REOPEN: reopen failed {p}: {e}")

        return {
            "status":   "ok",
            "path":     str(p),
            "backup":   str(backup),
            "message":  f"File fixed in place. Backup at {backup.name}",
            "opened_in_vscode": vscode is not None,
        }
