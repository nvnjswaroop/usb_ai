"""llama-server sidecar — managed subprocess + drop-in engine backend.

Phase B of the de-fragility plan: instead of the in-process
`llama-cpp-python` package (cp311-only wheels, abetlen index, source-build
traps), the app talks HTTP to the OFFICIAL `llama-server.exe` binary that
ggml publishes prebuilt on every llama.cpp release.

Layout:
    SidecarManager  — spawn / health-poll / stop the subprocess
    ServerEngine    — duck-type-compatible with llm.LLMEngine

Interface contract (must match llm.LLMEngine public surface; enforced by
tests/test_sidecar.py::TestInterfaceParity):
    is_loaded, is_loading, set_loading, supports_vision, current_model,
    load_model_sync, stream_tokens, generate_json, generate_code_files,
    reset_window, get_load_progress, _count, shutdown

Security notes:
    - The server binds 127.0.0.1 on an ephemeral port picked at spawn; it is
      never exposed beyond loopback regardless of USB_AI_HOST.
    - The binary comes from bin/llama/ (placed by scripts/fetch_llama.py,
      sha256-pinned) or USB_AI_LLAMA_SERVER override. Nothing here ever
      compiles anything.

# ponytail: token counting uses the chars//4 heuristic (llama-server owns the
# real tokenizer). SlidingWindow stays as a prompt-budget guard so context
# overflow surfaces as our 400, not the server's 500.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterator, List, Optional

from logging_config import getLogger
_log = getLogger("usbai")

ROOT = Path(__file__).resolve().parent.parent
HEALTH_POLL_SECONDS = 0.5


def find_server_binary() -> Optional[Path]:
    """Locate llama-server binary: env override > bin/llama/."""
    env_exe = os.environ.get("USB_AI_LLAMA_SERVER")
    if env_exe:
        p = Path(env_exe)
        return p if p.exists() else None
    exe = "llama-server.exe" if sys.platform == "win32" else "llama-server"
    env_dir = os.environ.get("USB_AI_LLAMA_DIR")
    if env_dir:
        # ponytail: an explicit override means EXACTLY that directory — if
        # the binary isn't there we fail rather than silently falling back
        # to bin/llama (operator intent must not be second-guessed).
        cand = Path(env_dir) / exe
        return cand if cand.exists() else None
    cand = ROOT / "bin" / "llama" / exe
    return cand if cand.exists() else None


def pick_free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class SidecarManager:
    """Owns one llama-server subprocess. Start is exclusive: starting a new
    model stops the previous instance (single-GPU/single-model app)."""

    def __init__(self, binary: Path, log_path: Optional[Path] = None,
                 command_prefix: Optional[list] = None,
                 startup_timeout: float = 600.0):
        self.binary = binary
        self.log_path = log_path or (ROOT / "output" / "llama-server.log")
        # ponytail: command_prefix lets tests substitute a stub server
        # (e.g. [python, fake_server.py]) without any llama binary present.
        self.command_prefix = command_prefix or []
        self.startup_timeout = startup_timeout
        self.proc: Optional[subprocess.Popen] = None
        self.port: Optional[int] = None
        self.model_name: Optional[str] = None
        # ponytail: external=True marks "tests attached me to an already-
        # running HTTP server" — is_loaded() then skips process-liveness
        # checks (there is no child process to inspect).
        self.external = False

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, model_path: Path, ctx_size: int, threads: int,
              n_gpu_layers: int, mmproj: Optional[Path],
              model_name: str, on_phase=None) -> None:
        self.stop()
        port = pick_free_port()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        argv = (self.command_prefix +
                [str(self.binary),
                 "--host", "127.0.0.1",
                 "--port", str(port),
                 "-m", str(model_path),
                 "--ctx-size", str(ctx_size),
                 "--threads", str(threads),
                 "--no-webui"])
        if n_gpu_layers >= 0:
            argv += ["-ngl", str(n_gpu_layers)]
        if mmproj:
            argv += ["--mmproj", str(mmproj)]
        logf = open(self.log_path, "w", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen(argv, stdout=logf, stderr=subprocess.STDOUT)
        self.port = port
        self.model_name = model_name
        self._assign_kill_on_close()
        _log.info(f"SIDECAR: spawning pid={self.proc.pid} port={port} "
                  f"model={model_name}")
        self._wait_healthy(on_phase)

    def _assign_kill_on_close(self) -> None:
        """Orphan protection (Windows): put the child in a KILL_ON_JOB_CLOSE
        Job Object so a hard parent crash / taskkill can't leave
        llama-server.exe holding ~500MB RAM + its port forever. Best-effort:
        POSIX relies on the lifespan/atexit stop paths.
        """
        if sys.platform != "win32":
            return
        try:
            from tools.code_tool import _WinJob
            self._job = _WinJob(None)  # kill-on-close only, no memory cap
            if not self._job.assign(self.proc):
                _log.warning("SIDECAR: job assign failed; relying on clean "
                             "shutdown paths")
        except OSError as e:
            _log.warning(f"SIDECAR: job object unavailable ({e}); relying on "
                         f"clean shutdown paths")

    def _wait_healthy(self, on_phase=None) -> None:
        deadline = time.monotonic() + self.startup_timeout
        url = f"{self.base_url}/health"
        while time.monotonic() < deadline:
            if self.proc is not None and self.proc.poll() is not None:
                raise RuntimeError(
                    f"llama-server exited early (code {self.proc.returncode}). "
                    f"Log tail: {self._log_tail()}")
            try:
                with urllib.request.urlopen(url, timeout=2) as r:
                    if r.status == 200:
                        return
            except urllib.error.HTTPError as e:
                # 503 while the model loads inside the server — still warming.
                if e.code != 503:
                    raise RuntimeError(f"/health HTTP {e.code}")
            except (urllib.error.URLError, OSError, TimeoutError):
                pass  # not accepting connections yet
            if on_phase:
                on_phase()
            time.sleep(HEALTH_POLL_SECONDS)
        self.stop()
        raise RuntimeError(
            f"llama-server not healthy after {int(self.startup_timeout)}s. "
            f"Log tail: {self._log_tail()}")

    def _log_tail(self, n: int = 400) -> str:
        try:
            return self.log_path.read_text(encoding="utf-8",
                                           errors="replace")[-n:].strip()
        except OSError:
            return "(log unavailable)"

    def stop(self) -> None:
        if self.proc is None:
            return
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        job = getattr(self, "_job", None)
        if job is not None:
            job.close()
            self._job = None
        _log.info("SIDECAR: stopped")
        self.proc = None
        self.port = None


class ServerEngine:
    """Drop-in replacement for llm.LLMEngine backed by the sidecar."""

    def __init__(self, models_dir: Path, prefeeds_path: Path = None,
                 manager: Optional[SidecarManager] = None):
        self.models_dir = models_dir
        self.prefeeds_path = prefeeds_path
        self.current_model: Optional[str] = None
        self._loading = False
        self._phase = ""
        self._is_vision = False
        self._n_ctx = 4096
        # ponytail: manager resolution is LAZY (first .manager access, which
        # happens at load_model_sync). Resolving eagerly in __init__ made the
        # app un-importable on machines without a platform-matching binary
        # (CI/ubuntu vs the committed win-x64 exe) — 30 tests died at import.
        # Missing binary still fails LOUD, just at /api/models/load where an
        # actionable message belongs.
        self._manager = manager
        self._window = None  # lazy SlidingWindow (imported lazily: no hard dep)
        self._load_start = 0.0
        self._model_size_mb = 0.0

    @property
    def manager(self) -> SidecarManager:
        if self._manager is None:
            self._manager = self._default_manager()
        return self._manager

    @staticmethod
    def _default_manager() -> SidecarManager:
        binary = find_server_binary()
        if binary is None:
            raise RuntimeError(
                "llama-server binary not found for this platform. Run "
                "scripts/fetch_llama.py (or setup.bat) to place it in "
                "bin/llama/, or set USB_AI_LLAMA_SERVER.")
        return SidecarManager(binary)

    # ── state ──────────────────────────────────────────────────────────────
    def is_loaded(self) -> bool:
        if self.current_model is None or self.manager.port is None:
            return False
        if getattr(self.manager, "external", False):
            return True
        return self.manager.proc is not None \
            and self.manager.proc.poll() is None

    def is_loading(self) -> bool:
        return self._loading

    def set_loading(self, val: bool):
        self._loading = val

    def supports_vision(self) -> bool:
        return self._is_vision and self.is_loaded()

    def _count(self, text: str) -> int:
        # ponytail: heuristic estimate — see module docstring.
        if not text:
            return 0
        return max(1, len(text) // 4)

    def get_load_progress(self) -> dict:
        if not self._loading:
            return {"pct": 100 if self.is_loaded() else 0, "loading": False,
                    "status": "", "phase": ""}
        elapsed = time.time() - self._load_start
        expected = max(10, self._model_size_mb / 1000 * 30)
        pct = min(93, int(elapsed / expected * 100))
        status = f"Loading... {int(elapsed)}s elapsed"
        if self._phase:
            status = f"{status} — {self._phase}"
        return {"pct": pct, "loading": True, "elapsed": int(elapsed),
                "status": status, "phase": self._phase}

    # ── load ───────────────────────────────────────────────────────────────
    def _find_mmproj(self) -> Optional[Path]:
        for c in self.models_dir.glob("mmproj*"):
            return c
        return None

    def load_model_sync(self, model_name: str, n_ctx: int = 4096,
                        n_threads: int = 8, n_gpu_layers: int = -1):
        from llm import VISION_KEYWORDS, SlidingWindow
        model_path = self.models_dir / model_name
        if not model_path.exists():
            raise FileNotFoundError(f"Model not found: {model_path}")

        self.set_loading(True)
        self._load_start = time.time()
        self._model_size_mb = model_path.stat().st_size / (1024 * 1024)
        self._phase = "starting llama-server"
        try:
            low = model_name.lower()
            self._is_vision = any(kw in low for kw in VISION_KEYWORDS)
            mmproj = self._find_mmproj() if self._is_vision else None
            ngl = 999 if n_gpu_layers == -1 else n_gpu_layers

            def tick():
                self._phase = "loading model weights"

            self.manager.start(model_path=model_path, ctx_size=n_ctx,
                               threads=max(1, n_threads), n_gpu_layers=ngl,
                               mmproj=mmproj, model_name=model_name,
                               on_phase=tick)
            self.current_model = model_name
            self._n_ctx = n_ctx
            self._window = SlidingWindow(n_ctx=n_ctx, count_fn=self._count)
            self._phase = ""
            _log.info(f"SIDECAR: ready — {model_name} ctx={n_ctx}")
        except Exception:
            self.current_model = None
            self._is_vision = False
            raise
        finally:
            self.set_loading(False)

    def shutdown(self) -> None:
        self.manager.stop()
        self.current_model = None

    # ── chat ───────────────────────────────────────────────────────────────
    def _build_payload_messages(self, messages, user_message, system, image_b64):
        """Returns (messages, max_tokens_budget).

        ponytail: mirrors inline semantics — a non-vision model receiving an
        image silently drops it here; the ROUTER owns the user-facing warning
        (chat.py checks supports_vision() before streaming). Vision budget is
        fixed at 2048 (matches inline vision path's server-side default).
        """
        if image_b64 and self._is_vision:
            hist = messages[-20:]
            msgs = [{"role": "system", "content": system}]
            msgs.extend(hist)
            msgs.append({"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": image_b64}},
                {"type": "text", "text": user_message or "Describe this image."},
            ]})
            return msgs, 2048
        self._maybe_sync(messages)
        windowed, safe_max = self._window.build_messages(user_message, system)
        return windowed, safe_max

    def stream_tokens(self, messages: List[dict], user_message: str,
                      system: str, temperature: float, max_tokens: int,
                      image_b64: Optional[str] = None) -> Iterator[str]:
        if not self.is_loaded():
            raise RuntimeError("No model loaded.")

        if self._window is None:
            from llm import SlidingWindow
            self._window = SlidingWindow(n_ctx=self._n_ctx, count_fn=self._count)

        msgs, budget = self._build_payload_messages(
            messages, user_message, system, image_b64)
        payload = {
            "messages": msgs,
            "temperature": temperature,
            "max_tokens": max(16, min(int(max_tokens), int(budget))),
            "stream": True,
            # ponytail: thinking models (Qwen3.x, DeepSeek-R1 distills…) burn
            # max_tokens on hidden `reasoning_content` BEFORE any visible
            # content — tiny budgets yielded literally-empty replies. This app
            # is a chat assistant: thinking OFF by default; the template kwarg
            # is ignored harmlessly by non-thinking models.
            "chat_template_kwargs": {"enable_thinking": False},
        }
        vision_turn = bool(image_b64 and self._is_vision)
        full = ""
        for delta in self._stream_completion(payload):
            full += delta
            yield delta
        if full.strip() and not vision_turn:
            self._window.add_exchange(user_message, full)

    def generate_json(self, prompt: str) -> str:
        if not self.is_loaded():
            raise RuntimeError("No model loaded.")
        est = len(prompt) // 4 + 10
        avail = max(512, min(self._n_ctx - est - 128, 4096))
        payload = {
            "messages": [
                {"role": "system",
                 "content": "Return ONLY valid JSON, no explanation, no markdown fences."},
                {"role": "user", "content": prompt + "\n\nReturn the JSON now."},
            ],
            "temperature": 0.3,
            "max_tokens": avail,
            "stream": False,
        }
        raw = self._complete(payload)
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        raw = re.sub(r"\s*```$", "", raw)
        return raw.strip()

    # ── HTTP plumbing ──────────────────────────────────────────────────────
    def _post(self, payload: dict, timeout: float = 300.0):
        req = urllib.request.Request(
            f"{self.manager.base_url}/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        return urllib.request.urlopen(req, timeout=timeout)

    def _complete(self, payload: dict) -> str:
        payload = dict(payload, stream=False)
        try:
            with self._post(payload) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")[:300]
            except OSError:
                pass
            msg = f"llama-server HTTP {e.code}: {body}"
            low = (body + str(e)).lower()
            if "context" in low or "n_ctx" in low:
                raise ValueError(msg)
            raise RuntimeError(msg)
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"malformed completion response: {e}: "
                               f"{json.dumps(data)[:300]}")

    def _stream_completion(self, payload: dict) -> Iterator[str]:
        payload = dict(payload, stream=True)
        try:
            resp = self._post(payload)
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")[:300]
            except OSError:
                pass
            raise RuntimeError(f"llama-server HTTP {e.code}: {body}")
        got_content = False
        finish = None
        with resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                chunk = line[len("data:"):].strip()
                if chunk == "[DONE]":
                    break
                try:
                    obj = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
                choice = obj["choices"][0] if obj.get("choices") else {}
                delta = choice.get("delta") or {}
                finish = choice.get("finish_reason") or finish
                # ponytail: reasoning_content is logged, never yielded — the
                # chat UI speaks in visible answers only.
                if delta.get("reasoning_content"):
                    _log.debug(f"SIDECAR think: "
                               f"{delta['reasoning_content'][:80]}")
                content = delta.get("content")
                if content:
                    got_content = True
                    yield content
        if not got_content and finish == "length":
            raise RuntimeError(
                "model spent its entire token budget on hidden reasoning — "
                "raise max_tokens or disable thinking for this model")

    # ── shared helpers kept interface-compatible ───────────────────────────
    def reset_window(self):
        if self._window:
            self._window.reset()

    def _maybe_sync(self, history: List[dict]):
        if self._window is None:
            return
        # ponytail: inline engine resyncs only on shrinkage (tracks
        # _last_hist_len); the server rebuilds its prompt per request anyway,
        # so a full resync every turn is always correct and cheap relative
        # to inference.
        self._window.sync_from_db(history)

    def generate_code_files(self, text: str, output_dir: Path) -> list[dict]:
        from artifacts import generate_code_files as _gen
        return _gen(text, output_dir)
