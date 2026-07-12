"""
Code Execution Tool - safely runs Python code and returns output
"""
import subprocess
import sys
import tempfile
import os
from pathlib import Path


class CodeTool:
    def __init__(self, python_path: str = None):
        self.python_path = python_path or sys.executable
        self.timeout = 30  # seconds

    def run_python(self, code: str) -> dict:
        """Execute Python code in a sandboxed subprocess, return stdout/stderr."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(code)
            tmp_path = f.name

        # ponytail: minimal sandbox — temp cwd, stripped env, resource rlimit.
        # upgrade to seccomp/firejail when running untrusted code from network.
        sandbox_env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
        }
        sandbox_cwd = tempfile.mkdtemp(prefix="usbai_sandbox_")

        def _rlimit():
            # ponytail: CPU + memory cap. 2s CPU, 512MB RAM. add when: users hit the cap on legit code.
            import resource as _r
            _r.setrlimit(_r.RLIMIT_CPU, (2, 2))
            if sys.platform != "win32":
                _r.setrlimit(_r.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))

        try:
            result = subprocess.run(
                [self.python_path, tmp_path],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=sandbox_cwd,
                env=sandbox_env,
                preexec_fn=_rlimit if sys.platform != "win32" else None,
            )
            stdout = result.stdout.strip()
            stderr = result.stderr.strip()
            # Truncate long output
            if len(stdout) > 4000:
                stdout = stdout[:4000] + "\n... [output truncated]"
            if len(stderr) > 2000:
                stderr = stderr[:2000] + "\n... [truncated]"
            return {
                "status":      "ok",
                "stdout":      stdout,
                "stderr":      stderr,
                "returncode":  result.returncode,
                "success":     result.returncode == 0,
            }
        except subprocess.TimeoutExpired:
            return {
                "status":  "error",
                "message": f"Code execution timed out after {self.timeout}s",
                "stdout":  "",
                "stderr":  "",
                "success": False,
            }
        except Exception as e:
            return {
                "status":  "error",
                "message": str(e),
                "stdout":  "",
                "stderr":  "",
                "success": False,
            }
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            try:
                import shutil; shutil.rmtree(sandbox_cwd, ignore_errors=True)
            except Exception:
                pass
