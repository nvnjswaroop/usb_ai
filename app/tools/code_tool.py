"""
Code Execution Tool - safely runs Python code and returns output
"""
import subprocess
import sys
import tempfile
import os
import threading
import time
from pathlib import Path

from logging_config import getLogger
_log = getLogger("usbai")


# ponytail: best-effort RAM watchdog. psutil is the cleanest path but isn't a
# dep — try to import it, fall back to a polling subprocess tasklist probe on
# Windows. If neither works, the timeout itself is the only safety net.
try:
    import psutil  # type: ignore
    _HAVE_PSUTIL = True
except ImportError:
    _HAVE_PSUTIL = False


# ── Windows Job Object (stdlib ctypes) ────────────────────────────────────────
# ponytail: AGENTS.md said this upgrade needed pywin32 "not in stdlib" —
# wrong, ctypes IS stdlib. Gives the Windows sandbox what POSIX gets from
# setrlimit: a hard memory ceiling plus kill-the-whole-tree semantics (child
# processes die too). Creation/assignment failure is non-fatal: logged, and
# the watchdog thread + timeout remain as backstops.
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JobObjectExtendedLimitInformation = 9

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        # ponytail: layout VERIFIED EMPIRICALLY on x64 (behavioral probe:
        # over-limit child killed iff LimitFlags written @16 and memory cap
        # @112). The easy-to-miss field is the SECOND timestamp —
        # PerJobUserTime — without it every later offset shifts by -8 and
        # SetInformationJobObject rejects the struct (ERROR_BAD_LENGTH 24).
        _fields_ = [
            ("PerProcessUserTime", ctypes.c_longlong),   # off   0
            ("PerJobUserTime", ctypes.c_longlong),       # off   8
            ("LimitFlags", wintypes.DWORD),              # off  16
            ("MinimumWorkingSetSize", ctypes.c_size_t),  # off  24
            ("MaximumWorkingSetSize", ctypes.c_size_t),  # off  32
            ("ActiveProcessLimit", ctypes.c_size_t),     # off  40
            ("Affinity", ctypes.c_size_t),               # off  48
            ("PriorityClass", wintypes.DWORD),           # off  56
            ("SchedulingClass", wintypes.DWORD),         # off  60
        ]                                               # = 64 bytes

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        # BasicLimitInformation (64) then IoInfo (6x8=48) then four SIZE_T.
        # ProcessMemoryLimit lands at offset 112; total 144 on x64.
        _fields_ = [("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
                    ("IoInfo", _IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    class _WinJob:
        """Kernel Job Object wrapper.

        memory_bytes=None  -> KILL_ON_JOB_CLOSE only (orphan protection for
                              child processes like llama-server)
        memory_bytes=int   -> + hard process-memory ceiling (code sandbox)
        """

        def __init__(self, memory_bytes: int | None):
            self._ok = False
            self._k32 = ctypes.windll.kernel32
            self._handle = self._k32.CreateJobObjectW(None, None)
            if not self._handle:
                raise OSError("CreateJobObjectW failed")
            limit = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            flags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if memory_bytes is not None:
                flags |= _JOB_OBJECT_LIMIT_PROCESS_MEMORY
                limit.ProcessMemoryLimit = memory_bytes
            limit.BasicLimitInformation.LimitFlags = flags
            if not self._k32.SetInformationJobObject(
                    self._handle, _JobObjectExtendedLimitInformation,
                    ctypes.byref(limit), ctypes.sizeof(limit)):
                raise OSError("SetInformationJobObject failed")
            self._ok = True

        def assign(self, proc: subprocess.Popen) -> bool:
            return bool(self._k32.AssignProcessToJobObject(
                self._handle, int(proc._handle)))

        def terminate(self) -> None:
            if self._ok:
                self._k32.TerminateJobObject(self._handle, 1)

        def close(self) -> None:
            if self._ok:
                self._k32.CloseHandle(self._handle)
                self._ok = False
else:
    _WinJob = None  # type: ignore[assignment,misc]


class CodeTool:
    def __init__(self, python_path: str = None):
        self.python_path = python_path or sys.executable
        self.timeout = 30  # seconds
        # ponytail: RAM cap for subprocess sandbox. 512 MB matches the Linux
        # rlimit in _rlimit() — keeps behaviour consistent across platforms.
        self.max_memory_bytes = 512 * 1024 * 1024
        # Polling interval for the memory watchdog thread.
        self._watchdog_poll_seconds = 0.5

    def _make_watchdog(self, proc: subprocess.Popen) -> threading.Thread:
        """Spawn a daemon thread that kills `proc` if it exceeds the RAM cap.

        Returns the thread so callers can join it on cleanup. Uses psutil
        when available (accurate + cross-platform); falls back to tasklist
        on Windows; no-op on other platforms without psutil.
        """
        stop = threading.Event()
        peak = {"rss": 0}

        def _watch():
            try:
                if _HAVE_PSUTIL:
                    p = psutil.Process(proc.pid)
                    while not stop.is_set():
                        try:
                            rss = p.memory_info().rss
                            if rss > peak["rss"]:
                                peak["rss"] = rss
                            if rss > self.max_memory_bytes:
                                _log.warning(
                                    f"CODE-WATCHDOG: killing subprocess "
                                    f"pid={proc.pid} rss={rss} > cap={self.max_memory_bytes}"
                                )
                                proc.kill()
                                return
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            return
                        if stop.wait(self._watchdog_poll_seconds):
                            return
                elif sys.platform == "win32":
                    # Fallback: tasklist /FI "PID eq <pid>" — ~50ms per probe.
                    # Less accurate (only refreshes every 0.5s) but ships stdlib.
                    while not stop.is_set():
                        try:
                            rss = self._rss_via_tasklist(proc.pid)
                            if rss and rss > peak["rss"]:
                                peak["rss"] = rss
                            if rss and rss > self.max_memory_bytes:
                                _log.warning(
                                    f"CODE-WATCHDOG: killing subprocess "
                                    f"pid={proc.pid} rss={rss} > cap={self.max_memory_bytes}"
                                )
                                proc.kill()
                                return
                        except OSError:
                            return
                        if stop.wait(self._watchdog_poll_seconds):
                            return
            except Exception as e:
                _log.warning(f"CODE-WATCHDOG: thread died: {e}")

        th = threading.Thread(target=_watch, daemon=True, name="code-watchdog")
        th._stop_event = stop  # type: ignore[attr-defined]
        th.start()
        return th

    @staticmethod
    def _rss_via_tasklist(pid: int) -> int:
        """Windows fallback — probe a single PID's working set via tasklist.

        Returns RSS in bytes, or 0 on parse failure / process gone.
        """
        try:
            r = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            return 0
        # CSV line: "python.exe","12345","Console","1,234,567 K"
        for line in r.stdout.splitlines():
            if not line.strip():
                continue
            parts = [p.strip().strip('"') for p in line.split(",")]
            # mem field is the last one; format "X,XXX K"
            if len(parts) >= 5:
                mem = parts[-1].replace("K", "").replace(",", "").strip()
                try:
                    return int(mem) * 1024
                except ValueError:
                    continue
        return 0

    def run_python(self, code: str) -> dict:
        """Execute Python code in a sandboxed subprocess, return stdout/stderr."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(code)
            tmp_path = f.name

        # ponytail: minimal sandbox — temp cwd, stripped env, resource rlimit
        # on POSIX. Windows has no rlimit, so the memory watchdog thread above
        # is the equivalent safety net there.
        sandbox_env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
        }
        sandbox_cwd = tempfile.mkdtemp(prefix="usbai_sandbox_")

        def _rlimit():
            # ponytail: CPU + memory cap on POSIX. 2s CPU, 512MB RAM.
            import resource as _r
            _r.setrlimit(_r.RLIMIT_CPU, (2, 2))
            if sys.platform != "win32":
                _r.setrlimit(_r.RLIMIT_AS, (self.max_memory_bytes, self.max_memory_bytes))

        watchdog = None
        win_job = None
        try:
            # ponytail: hard memory ceiling on Windows via Job Object — the
            # platform equivalent of the POSIX RLIMIT_AS below. Best-effort:
            # if the ctypes dance fails we log and fall back to watchdog-only.
            if _WinJob is not None:
                try:
                    win_job = _WinJob(self.max_memory_bytes)
                except OSError as e:
                    _log.warning(f"CODE-SANDBOX: job object unavailable: {e}")
                    win_job = None
            proc = subprocess.Popen(
                [self.python_path, tmp_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=sandbox_cwd,
                env=sandbox_env,
                preexec_fn=_rlimit if sys.platform != "win32" else None,
            )
            if win_job is not None and not win_job.assign(proc):
                _log.warning("CODE-SANDBOX: AssignProcessToJobObject failed "
                             "(pid may have already exited); watchdog remains")
            # ponytail: Windows watchdog kicks in once the Popen handle exists.
            # No-op on POSIX where rlimit covers memory.
            if sys.platform == "win32" or _HAVE_PSUTIL:
                watchdog = self._make_watchdog(proc)
            try:
                stdout, stderr = proc.communicate(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                # ponytail: prefer job-tree termination on Windows — kills
                # grandchildren too, not just the direct child.
                if win_job is not None:
                    win_job.terminate()
                else:
                    proc.kill()
                stdout, stderr = proc.communicate()
                return {
                    "status":  "error",
                    "message": f"Code execution timed out after {self.timeout}s",
                    "stdout":  "",
                    "stderr":  "",
                    "success": False,
                }
            # Truncate long output
            if len(stdout) > 4000:
                stdout = stdout[:4000] + "\n... [output truncated]"
            if len(stderr) > 2000:
                stderr = stderr[:2000] + "\n... [truncated]"
            return {
                "status":      "ok",
                "stdout":      (stdout or "").strip(),
                "stderr":      (stderr or "").strip(),
                "returncode":  proc.returncode,
                "success":     proc.returncode == 0,
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
            if watchdog is not None:
                watchdog._stop_event.set()  # type: ignore[attr-defined]
                watchdog.join(timeout=1.0)
            if win_job is not None:
                # ponytail: KILL_ON_JOB_CLOSE makes this a no-op for exited
                # children, and a safety net if the timeout path was skipped.
                win_job.terminate()
                win_job.close()
            try:
                os.unlink(tmp_path)
            except OSError as e:
                _log.warning(f"CODE-CLEANUP: unlink {tmp_path}: {e}")
            import shutil; shutil.rmtree(sandbox_cwd, ignore_errors=True)
