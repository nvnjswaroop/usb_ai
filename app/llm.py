"""
USB AI - LLM Engine
Sliding Window context + Vision + Thread/Queue streaming
All internal events use logging.getLogger("usbai") — never print().
"""
import re
import queue
import struct
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional, Iterator

from logging_config import getLogger
_log = getLogger("usbai")

# ponytail: single source of truth for lang->extension lives in artifacts._CODE_LANGS;
# generate_code_files() below reads it instead of keeping a second inline copy.
from artifacts import _CODE_LANGS as _LANG_EXT  # noqa: E402

# --------------------------------------------------------------------------- #
# Model profiles - chat format + stop tokens per model family
# --------------------------------------------------------------------------- #
VISION_KEYWORDS = [
    "gemma-4","gemma4","gemma-3","gemma3",
    "llava","llava-1","bakllava",
    "moondream","qwen2-vl","qwen2vl",
    "minicpm-v","minicpmv",
]

MODEL_PROFILES = [
    {"keywords":["phi-3","phi3"],
     "chat_format":"chatml",
     "stop":["<|end|>","<|user|>","<|assistant|>","<|endoftext|>"]},
    {"keywords":["qwen2.5","qwen2","qwen3","qwen"],
     "chat_format":"chatml",
     "stop":["<|im_end|>","<|endoftext|>"]},
    {"keywords":["mistral-nemo","mistral-7b","mistral"],
     "chat_format":"mistral-instruct",
     "stop":["[INST]","[/INST]","</s>"]},
    {"keywords":["llama-3","llama3","meta-llama-3"],
     "chat_format":"chatml",
     "stop":["<|eot_id|>","<|end_of_text|>"]},
    {"keywords":["gemma-2","gemma2","gemma"],
     "chat_format":"gemma",
     "stop":["<end_of_turn>","<eos>"]},
    {"keywords":["deepseek"],
     "chat_format":"chatml",
     "stop":["<|end_of_sentence|>"]},
]

DEFAULT_PROFILE = {
    "chat_format":"chatml",
    "stop":["<|im_end|>","<|endoftext|>","</s>","<|end|>"],
}

# Sliding window constants
RESPONSE_RESERVE = 768   # tokens kept free for model reply
SAFETY_BUFFER    = 128   # formatting/BOS/EOS overhead
MAX_RESPONSE_CAP = 2048  # hard cap per response


def _detect_profile(model_name: str) -> dict:
    n = model_name.lower()
    for p in MODEL_PROFILES:
        if any(kw in n for kw in p["keywords"]):
            return p
    return DEFAULT_PROFILE


def _is_vision(model_name: str) -> bool:
    n = model_name.lower()
    return any(kw in n for kw in VISION_KEYWORDS)


# --------------------------------------------------------------------------- #
# Sliding Window
# --------------------------------------------------------------------------- #
class SlidingWindow:
    """
    Fixed-capacity token window.
    System prompt is permanently anchored.
    History pairs slide through the remaining budget.

    Turn 1:  [SYS][U1][A1]
    Turn 2:  [SYS][U1][A1][U2][A2]
    Turn 4:  [SYS]         [U2][A2][U3][A3][U4][A4]  <- slid
    """

    def __init__(self, n_ctx: int, count_fn):
        self._n_ctx   = n_ctx
        self._count   = count_fn
        self._history : List[dict] = []
        self._system  : str        = ""

    def _tok(self, msg: dict) -> int:
        return self._count(msg.get("content","")) + 6

    def _budget(self, user_msg: str) -> int:
        user_tok = self._count(user_msg) + 6
        sys_tok  = self._count(self._system) + 6
        return max(0, self._n_ctx - sys_tok - user_tok - RESPONSE_RESERVE - SAFETY_BUFFER)

    def slide(self, user_msg: str) -> List[dict]:
        budget  = self._budget(user_msg)
        working = list(self._history)
        usage   = sum(self._tok(m) for m in working)
        dropped = 0
        while usage > budget and working:
            if (len(working) >= 2
                    and working[0]["role"] == "user"
                    and working[1]["role"] == "assistant"):
                usage  -= self._tok(working[0]) + self._tok(working[1])
                working = working[2:]
                dropped += 1
            else:
                usage  -= self._tok(working[0])
                working = working[1:]
        if dropped:
            _log.debug(f"SlidingWindow: dropped {dropped} pair(s) | history={len(working)} msgs | ctx={self._n_ctx}")
        return working

    def build_messages(self, user_msg: str, system: str):
        self._system = system
        trimmed  = self.slide(user_msg)
        msgs     = ([{"role":"system","content":system}]
                    + trimmed
                    + [{"role":"user","content":user_msg}])
        used     = sum(self._tok(m) for m in msgs)
        safe_max = max(RESPONSE_RESERVE, min(self._n_ctx - used - SAFETY_BUFFER, MAX_RESPONSE_CAP))
        return msgs, safe_max

    def add_exchange(self, user_msg: str, reply: str):
        self._history.append({"role":"user",      "content":user_msg})
        self._history.append({"role":"assistant",  "content":reply})
        self._history = self.slide("")

    def sync_from_db(self, db_msgs: List[dict]):
        self._history = []
        i = 0
        while i + 1 < len(db_msgs):
            u, a = db_msgs[i], db_msgs[i+1]
            if u.get("role")=="user" and a.get("role")=="assistant":
                self._history.append({"role":"user",      "content":u.get("content","")})
                self._history.append({"role":"assistant",  "content":a.get("content","")})
                i += 2
            else:
                i += 1
        self._history = self.slide("")
        _log.debug(f"SlidingWindow: synced from DB, {self.pair_count} pairs")

    def reset(self):
        self._history = []
        _log.debug("SlidingWindow: reset — new conversation")

    @property
    def pair_count(self) -> int:
        return len(self._history) // 2


# --------------------------------------------------------------------------- #
# LLM Engine
# --------------------------------------------------------------------------- #
class LLMEngine:
    def __init__(self, models_dir: Path, prefeeds_path: Path):
        self.models_dir    = models_dir
        self.prefeeds_path = prefeeds_path
        self.llm           = None
        self.current_model : Optional[str] = None
        self._loading      : bool          = False
        self._is_vision    : bool          = False
        self._stop_tokens                  = DEFAULT_PROFILE["stop"]
        self._chat_format  : str           = DEFAULT_PROFILE["chat_format"]
        self._n_ctx        : int           = 4096

        self._window       : Optional[SlidingWindow] = None
        self._last_hist_len: int           = 0
        # ponytail: single-Llama-instance inference lock — llama.cpp's KV cache
        # and eval state are not safe under concurrent `create_chat_completion`
        # callers. Symptom without this lock: server crash mid-scan when
        # CyberMatrix fires auth+vuln+exploit in parallel. Throughput
        # ceiling = 1 inference at a time on this process; upgrade path is a
        # proper serving layer (vLLM / llama-server HTTP) which has per-request
        # KV contexts — that's a multi-week refactor, defer until serial
        # inference measurably bottlenecks real scans.
        self._infer_lock   : threading.Lock = threading.Lock()

        self._load_start   : float         = 0.0
        self._model_size_mb: float         = 0.0
        # ponytail: coarse structured load phase — emitted by the engine itself
        # so /api/models/progress doesn't depend on grepping llama.cpp's log
        # format (which breaks on any upstream log change). Upgrade path:
        # finer per-stage callbacks if the UI ever needs a real progress bar.
        self._phase        : str           = ""

    # -- State ----------------------------------------------------------------
    def is_loaded(self)  -> bool: return self.llm is not None
    def is_loading(self) -> bool: return self._loading
    def set_loading(self, val: bool): self._loading = val
    def supports_vision(self) -> bool: return self._is_vision and self.llm is not None

    def get_load_progress(self) -> dict:
        if not self._loading:
            return {"pct":100 if self.llm is not None else 0, "loading":False, "status":"",
                    "phase":""}
        elapsed  = time.time() - self._load_start
        expected = max(10, self._model_size_mb / 1000 * 30)
        pct      = min(93, int(elapsed / expected * 100))
        status   = f"Loading... {int(elapsed)}s elapsed"
        if self._phase:
            status = f"{status} — {self._phase}"
        return {"pct":pct, "loading":True, "elapsed":int(elapsed),
                "status":status, "phase":self._phase}

    # -- Tokenizer ------------------------------------------------------------
    def _count(self, text: str) -> int:
        if not text: return 0
        if self.llm is None: return max(1, len(text)//4)
        try: return len(self.llm.tokenize(text.encode("utf-8"), add_bos=False))
        except (ValueError, TypeError, RuntimeError):  # ponytail: Llama.tokenize raises ValueError on UTF-8 boundary issues, TypeError on unsupported token ids, and RuntimeError on llama.cpp internal errors (e.g. model not in expected state)
            return max(1, len(text)//4)

    # -- GGUF check & metadata ------------------------------------------------
    def _check_gguf(self, path: Path):
        with open(path,"rb") as f:
            magic   = f.read(4)
            version = struct.unpack("<I", f.read(4))[0]
        if magic != b"GGUF":
            raise ValueError(f"Not a valid GGUF file: {path.name}")
        _log.info(f"GGUF v{version} OK")

    def _get_model_n_ctx(self, path: Path) -> int:
        """No-op stub kept for callers; returns max so user-supplied n_ctx wins.

        ponytail: the previous hand-rolled GGUF v3 parser silently returned 2048
        for any .gguf that didn't pack the `n_ctx\\x00\\x05` key into its last 64KB
        (Qwen3.5-0.8B's metadata is in the header region, not the tail). That
        capped every load at 2048 regardless of caller request, surfacing as
        `RuntimeError: Requested tokens exceed context window of 2048` whenever
        CyberMatrix's vuln module sent a >2k-tok prompt. llama.cpp itself logs
        `n_ctx_train` on init and clamps to available RAM, so the pre-cap was
        redundant and broken. Now we trust the caller's n_ctx and let llama.cpp
        enforce the actual ceiling.
        """
        return 32768  # effectively unlimited from the API's perspective

    # -- Load -----------------------------------------------------------------
    def load_model_sync(self, model_name: str, n_ctx: int = 4096, n_threads: int = 8,
                        n_gpu_layers: int = -1):
        from llama_cpp import Llama

        model_path = self.models_dir / model_name
        if not model_path.exists():
            raise FileNotFoundError(f"Model not found: {model_path}")

        self._check_gguf(model_path)

        if self.llm is not None:
            del self.llm; self.llm = None

        self._load_start    = time.time()
        self._model_size_mb = model_path.stat().st_size / (1024*1024)
        self._phase         = "validating GGUF"

        # Cap n_ctx at model's n_ctx_train to prevent KV cache assertion failures
        model_n_ctx = self._get_model_n_ctx(model_path)
        n_ctx = min(n_ctx, model_n_ctx)
        _log.info(f"ctx={n_ctx} (cap was a no-op stub; llama.cpp clamps if RAM-bound)")

        profile           = _detect_profile(model_name)
        self._is_vision   = _is_vision(model_name)
        self._stop_tokens = profile["stop"]
        self._chat_format = profile["chat_format"]
        self._n_ctx       = n_ctx

        _log.info(f"Loading : {model_name}")
        _log.info(f"Format  : {self._chat_format}")
        _log.info(f"Vision  : {self._is_vision}")
        _log.info(f"Size    : {self._model_size_mb:.0f} MB")
        _log.info(f"ctx={n_ctx} threads={n_threads}")

        # LLaVA-style mmproj
        mmproj_path = None
        for c in model_path.parent.glob("mmproj*"):
            mmproj_path = str(c)
            _log.info(f"mmproj  : {c.name}")
            break

        # GPU: auto-detect unless user override is provided (n_gpu_layers=-1 means auto)
        gpu_layers_override = n_gpu_layers
        if gpu_layers_override == -1:
            n_gpu_layers = 0
            try:
                import llama_cpp
                if llama_cpp.llama_cuda_available():
                    n_gpu_layers = 9999   # offload all layers to CUDA GPU
                    _log.info("GPU     : CUDA detected — using GPU acceleration")
                elif sys.platform == "darwin" and llama_cpp.llama_metal_available():
                    n_gpu_layers = 9999   # offload all layers to Apple Metal
                    _log.info("GPU     : Metal detected — using GPU acceleration")
                else:
                    _log.info("GPU     : No GPU detected — using CPU only")
            except Exception as e:
                _log.info("GPU     : Detection failed ({e}) — using CPU only")
        else:
            # User override: 0=CPU, 9999=max GPU layers
            n_gpu_layers = gpu_layers_override
            if n_gpu_layers == 0:
                _log.info("GPU     : User override — using CPU only")
            else:
                _log.info("GPU     : User override — using {n_gpu_layers} GPU layers")

        self._phase = "allocating weights"
        kwargs = dict(
            model_path  =str(model_path),
            n_ctx       =n_ctx,
            n_threads   =n_threads,
            n_gpu_layers=n_gpu_layers,
            verbose     =True,
            use_mmap    =True,
            use_mlock   =False,
        )

        if mmproj_path and self._is_vision:
            try:
                from llama_cpp.llama_chat_format import Llava16ChatHandler
                kwargs["chat_handler"] = Llava16ChatHandler(
                    clip_model_path=mmproj_path, verbose=False)
                _log.info("LLaVA handler loaded")
            except Exception as e:
                _log.warning(f"mmproj skipped: {e}")

        self.llm = Llama(**kwargs)
        self.current_model = model_name
        self._phase = "building sliding window"

        self._window = SlidingWindow(n_ctx=self._n_ctx, count_fn=self._count)
        self._last_hist_len = 0

        elapsed = time.time() - self._load_start
        self._phase = ""
        _log.info(f"Ready -- sliding window {self._n_ctx} tok ({elapsed:.1f}s)")

    # -- Window helpers -------------------------------------------------------
    def reset_window(self):
        if self._window: self._window.reset()
        self._last_hist_len = 0

    def _maybe_sync(self, history: List[dict]):
        if self._window is None: return
        if len(history) < self._last_hist_len:
            _log.debug("History shrank -- syncing from DB")
            self._window.sync_from_db(history)
        self._last_hist_len = len(history)

    # -- Streaming ------------------------------------------------------------
    def stream_tokens(
        self,
        messages    : List[dict],
        user_message: str,
        system      : str,
        temperature : float,
        max_tokens  : int,
        image_b64   : Optional[str] = None,
    ) -> Iterator[str]:
        if not self.is_loaded():  raise RuntimeError("No model loaded.")
        if self._window is None:  raise RuntimeError("Load a model first.")

        _DONE = object()
        q: queue.Queue = queue.Queue(maxsize=256)

        # Vision path
        if image_b64 and self._is_vision:
            hist = messages[-20:]
            msgs = [{"role":"system","content":system}]
            msgs.extend(hist)
            msgs.append({"role":"user","content":[
                {"type":"image_url","image_url":{"url":image_b64}},
                {"type":"text","text":user_message or "Describe this image in detail."},
            ]})

            def _vision():
                try:
                    with self._infer_lock:
                        for chunk in self.llm.create_chat_completion(
                                messages=msgs, temperature=temperature,
                                max_tokens=max_tokens, stream=True):
                            t = chunk["choices"][0].get("delta",{}).get("content","")
                            if t: q.put(t)
                # ponytail: narrow to RuntimeError/ValueError/OSError per AGENTS.md —
                # bare `except Exception` would swallow KeyboardInterrupt and turn real
                # bugs (TypeError, AttributeError) into misleading RuntimeError messages.
                except (RuntimeError, ValueError, OSError) as e: q.put(RuntimeError(str(e)))
                finally: q.put(_DONE)

            th = threading.Thread(target=_vision, daemon=True)
            th.start()
            while True:
                item = q.get()
                if item is _DONE: break
                if isinstance(item, Exception): raise item
                yield item
            th.join(timeout=5)
            if th.is_alive():
                _log.warning("stream thread did not terminate within 5s timeout — continuing anyway")
            return

        # Text path with sliding window
        self._maybe_sync(messages)
        windowed, safe_max = self._window.build_messages(user_message, system)
        actual_max = min(max_tokens, safe_max)
        _log.debug(f"SlidingWindow: {len(windowed)} msgs -> model | max_tokens={actual_max} | pairs={self._window.pair_count}")

        def _text():
            try:
                with self._infer_lock:
                    for chunk in self.llm.create_chat_completion(
                            messages=windowed, temperature=temperature,
                            max_tokens=actual_max, repeat_penalty=1.18,
                            top_p=0.9, top_k=40,
                            stream=True, stop=self._stop_tokens):
                        t = chunk["choices"][0].get("delta",{}).get("content","")
                        if t: q.put(t)
            # ponytail: narrow to RuntimeError/ValueError/OSError per AGENTS.md.
            except (RuntimeError, ValueError, OSError) as e: q.put(RuntimeError(str(e)))
            finally: q.put(_DONE)

        th = threading.Thread(target=_text, daemon=True)
        th.start()
        full = ""
        while True:
            item = q.get()
            if item is _DONE: break
            if isinstance(item, Exception): raise item
            full += item
            yield item
        th.join(timeout=5)
        if th.is_alive():
            _log.warning("stream thread did not terminate within 5s timeout — continuing anyway")

        if full.strip():
            self._window.add_exchange(user_message, full)
            self._last_hist_len += 2

    # -- JSON generation (PPT) ------------------------------------------------
    def generate_json(self, prompt: str) -> str:
        if not self.is_loaded(): raise RuntimeError("No model loaded.")
        est = len(prompt)//4 + 10
        avail = max(512, min(self._n_ctx - est - SAFETY_BUFFER, 4096))
        msgs = [{"role": "system", "content": "Return ONLY valid JSON, no explanation, no markdown fences."},
                {"role": "user", "content": prompt + "\n\nReturn the JSON now."}]
        with self._infer_lock:
            r = self.llm.create_chat_completion(
                messages=msgs, temperature=0.3, max_tokens=avail,
                repeat_penalty=1.1, top_p=0.9, top_k=40,
                stream=False, stop=["Human:", "System:"],
            )

        raw = r["choices"][0]["message"]["content"].strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        return raw.strip()

    # -- Auto-save code blocks from generated text ---------------------------
    # ponytail: this set names which languages trigger auto-save during chat.
    # It is intentionally a SET, distinct from the dict `_CODE_LANGS` in
    # `app/artifacts.py` which maps language labels to file extensions.
    # The two are kept separate because they answer different questions:
    #   "should I save this block as a file?" (set membership)
    #   "what extension should that file use?"    (dict lookup)
    CODE_LANGS_SET = {"python", "py", "javascript", "js", "typescript", "ts",
                      "html", "css", "java", "cpp", "c", "rust", "go",
                      "bash", "sh", "sql", "json", "yaml", "yml", "xml"}

    def generate_code_files(self, text: str, output_dir: Path) -> list[dict]:
        """Extract code blocks and save to output_dir. Shared implementation
        lives in artifacts.generate_code_files — both backends delegate so the
        two engines cannot drift."""
        from artifacts import generate_code_files as _gen
        return _gen(text, output_dir)
