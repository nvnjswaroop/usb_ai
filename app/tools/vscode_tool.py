"""
VS Code Tool
- Save code to file and open in VS Code
- Debug/fix a file: AI edits it in place
- Find VS Code on the USB or host machine
"""
import os
import re
import subprocess
import shutil
from pathlib import Path
from typing import Optional


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
            except Exception as e:
                return {"status": "ok", "path": str(dest), "opened_in_vscode": False,
                        "note": f"File saved but VS Code failed to open: {e}"}
        else:
            # Open with default app as fallback
            try:
                os.startfile(str(dest))
            except Exception:
                pass
            return {"status": "ok", "path": str(dest), "opened_in_vscode": False,
                    "note": "VS Code not found — file saved and opened with default app"}

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
        backup = p.with_suffix(p.suffix + ".bak")
        try:
            backup.write_bytes(p.read_bytes())
        except Exception:
            pass  # backup is best-effort

        try:
            p.write_text(fixed_code, encoding="utf-8")
        except Exception as e:
            return {"status": "error", "message": f"Could not write fix: {e}"}

        vscode = _find_vscode()
        if vscode:
            try:
                subprocess.Popen([vscode, str(p)])
            except Exception:
                pass

        return {
            "status":   "ok",
            "path":     str(p),
            "backup":   str(backup),
            "message":  f"File fixed in place. Backup at {backup.name}",
            "opened_in_vscode": vscode is not None,
        }
