"""
USB AI - LLM Engine
Sliding Window context + Vision + Thread/Queue streaming
All print() calls use ASCII-safe chars (no arrows/em-dashes) for Windows cp1252 compatibility
"""
import re
import queue
import struct
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional, Iterator

# Force UTF-8 stdout so Windows cp1252 does not crash on any unicode
if hasattr(sys.stdout, "reconfigure"):
    try: sys.stdout.reconfigure(encoding="utf-8")
    except: pass
if hasattr(sys.stderr, "reconfigure"):
    try: sys.stderr.reconfigure(encoding="utf-8")
    except: pass

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
            print(f"[WINDOW] Dropped {dropped} pair(s) | history={len(working)} msgs | ctx={self._n_ctx}", flush=True)
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
        print(f"[WINDOW] Synced from DB: {self.pair_count} pair(s)", flush=True)

    def reset(self):
        self._history = []
        print("[WINDOW] Reset - new conversation", flush=True)

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
        # Progress tracking
        self._load_start   : float         = 0.0
        self._model_size_mb: float         = 0.0

    # -- State ----------------------------------------------------------------
    def is_loaded(self)  -> bool: return self.llm is not None
    def is_loading(self) -> bool: return self._loading
    def set_loading(self, val: bool): self._loading = val
    def supports_vision(self) -> bool: return self._is_vision and self.llm is not None

    def get_load_progress(self) -> dict:
        if not self._loading:
            return {"pct":100 if self.llm is not None else 0, "loading":False, "status":""}
        elapsed  = time.time() - self._load_start
        expected = max(10, self._model_size_mb / 1000 * 30)
        pct      = min(93, int(elapsed / expected * 100))
        return {"pct":pct, "loading":True, "elapsed":int(elapsed),
                "status":f"Loading... {int(elapsed)}s elapsed"}

    # -- Tokenizer ------------------------------------------------------------
    def _count(self, text: str) -> int:
        if not text: return 0
        if self.llm is None: return max(1, len(text)//4)
        try: return len(self.llm.tokenize(text.encode("utf-8"), add_bos=False))
        except: return max(1, len(text)//4)

    # -- GGUF check & metadata ------------------------------------------------
    def _check_gguf(self, path: Path):
        with open(path,"rb") as f:
            magic   = f.read(4)
            version = struct.unpack("<I", f.read(4))[0]
        if magic != b"GGUF":
            raise ValueError(f"Not a valid GGUF file: {path.name}")
        print(f"[LLM] GGUF v{version} OK", flush=True)

    def _get_model_n_ctx(self, path: Path) -> int:
        """Read n_ctx_train from GGUF metadata to avoid KV cache mismatch.

        Falls back to 2048 (a safe minimum for most models). If the model's
        n_ctx_train is larger, we cap at that to avoid OOM.
        """
        try:
            import struct as struct_rt
            with open(path, "rb") as f:
                f.read(4)  # magic
                version = struct_rt.unpack("<I", f.read(4))[0]
                if version < 3:
                    return 2048
                # v3+: kv data before tensor data, preceded by.tensors offset table
                # Walk forward: count + alignment, then we need to find kv section
                # The simplest reliable read: scan last 64KB for the n_ctx key
                f.seek(0, 2)
                eof = f.tell()
                scan = f.read(max(0, eof - 65536))
            # Scan backwards for "n_ctx\x00type\x05" pattern
            key = b"n_ctx\x00\x05"
            idx = scan.rfind(key)
            if idx == -1:
                return 2048
            val = struct_rt.unpack("<q", scan[idx + len(key):idx + len(key) + 8])[0]
            val = max(128, min(int(val), 32768))
            print(f"[LLM] n_ctx_train = {val}", flush=True)
            return val
        except Exception as e:
            print(f"[LLM] n_ctx_train lookup failed ({e}) — using 2048", flush=True)
            return 2048

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

        # Cap n_ctx at model's n_ctx_train to prevent KV cache assertion failures
        model_n_ctx = self._get_model_n_ctx(model_path)
        n_ctx = min(n_ctx, model_n_ctx)
        print(f"[LLM] ctx capped at model n_ctx_train = {n_ctx}", flush=True)

        profile           = _detect_profile(model_name)
        self._is_vision   = _is_vision(model_name)
        self._stop_tokens = profile["stop"]
        self._chat_format = profile["chat_format"]
        self._n_ctx       = n_ctx

        print(f"[LLM] Loading : {model_name}", flush=True)
        print(f"[LLM] Format  : {self._chat_format}", flush=True)
        print(f"[LLM] Vision  : {self._is_vision}", flush=True)
        print(f"[LLM] Size    : {self._model_size_mb:.0f} MB", flush=True)
        print(f"[LLM] ctx={n_ctx} threads={n_threads}", flush=True)

        # LLaVA-style mmproj
        mmproj_path = None
        for c in model_path.parent.glob("mmproj*"):
            mmproj_path = str(c)
            print(f"[LLM] mmproj  : {c.name}", flush=True)
            break

        # GPU: auto-detect unless user override is provided (n_gpu_layers=-1 means auto)
        gpu_layers_override = n_gpu_layers
        if gpu_layers_override == -1:
            n_gpu_layers = 0
            try:
                import llama_cpp
                if llama_cpp.llama_cuda_available():
                    n_gpu_layers = 9999   # offload all layers to CUDA GPU
                    print("[LLM] GPU     : CUDA detected — using GPU acceleration", flush=True)
                elif sys.platform == "darwin" and llama_cpp.llama_metal_available():
                    n_gpu_layers = 9999   # offload all layers to Apple Metal
                    print("[LLM] GPU     : Metal detected — using GPU acceleration", flush=True)
                else:
                    print("[LLM] GPU     : No GPU detected — using CPU only", flush=True)
            except Exception as e:
                print(f"[LLM] GPU     : Detection failed ({e}) — using CPU only", flush=True)
        else:
            # User override: 0=CPU, 9999=max GPU layers
            n_gpu_layers = gpu_layers_override
            if n_gpu_layers == 0:
                print("[LLM] GPU     : User override — using CPU only", flush=True)
            else:
                print(f"[LLM] GPU     : User override — using {n_gpu_layers} GPU layers", flush=True)

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
                print("[LLM] LLaVA handler loaded", flush=True)
            except Exception as e:
                print(f"[LLM] mmproj skipped: {e}", flush=True)

        self.llm = Llama(**kwargs)
        self.current_model = model_name

        self._window = SlidingWindow(n_ctx=self._n_ctx, count_fn=self._count)
        self._last_hist_len = 0

        elapsed = time.time() - self._load_start
        print(f"[LLM] Ready -- sliding window {self._n_ctx} tok ({elapsed:.1f}s)", flush=True)

    # -- Window helpers -------------------------------------------------------
    def reset_window(self):
        if self._window: self._window.reset()
        self._last_hist_len = 0

    def _maybe_sync(self, history: List[dict]):
        if self._window is None: return
        if len(history) < self._last_hist_len:
            print("[WINDOW] History shrank -- syncing from DB", flush=True)
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
                    for chunk in self.llm.create_chat_completion(
                            messages=msgs, temperature=temperature,
                            max_tokens=max_tokens, stream=True):
                        t = chunk["choices"][0].get("delta",{}).get("content","")
                        if t: q.put(t)
                except Exception as e: q.put(RuntimeError(str(e)))
                finally: q.put(_DONE)

            th = threading.Thread(target=_vision, daemon=True)
            th.start()
            while True:
                item = q.get()
                if item is _DONE: break
                if isinstance(item, Exception): raise item
                yield item
            th.join(timeout=5)
            return

        # Text path with sliding window
        self._maybe_sync(messages)
        windowed, safe_max = self._window.build_messages(user_message, system)
        actual_max = min(max_tokens, safe_max)
        print(f"[WINDOW] {len(windowed)} msgs -> model | max_tokens={actual_max} | pairs={self._window.pair_count}", flush=True)

        def _text():
            try:
                for chunk in self.llm.create_chat_completion(
                        messages=windowed, temperature=temperature,
                        max_tokens=actual_max, repeat_penalty=1.18,
                        top_p=0.9, top_k=40,
                        stream=True, stop=self._stop_tokens):
                    t = chunk["choices"][0].get("delta",{}).get("content","")
                    if t: q.put(t)
            except Exception as e: q.put(RuntimeError(str(e)))
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
    CODE_LANGS = {"python", "py", "javascript", "js", "typescript", "ts",
                  "html", "css", "java", "cpp", "c", "rust", "go",
                  "bash", "sh", "sql", "json", "yaml", "yml", "xml"}

    def generate_code_files(self, text: str, output_dir: Path) -> list:
        """
                Extract code blocks from generated text and save them to output_dir.
                Returns list of dicts: [{"filename": str, "path": str, "status": str}, ...]
                """
        from app.tools.vscode_tool import VSCodeTool
        vscode = VSCodeTool(output_dir)
        saved = []
        for m in re.finditer(r"```(\w+)?\n(.*?)```", text, re.DOTALL):
            lang = (m.group(1) or "txt").lower()
            if lang not in self.CODE_LANGS:
                continue
            code = m.group(2).strip()
            if not code:
                continue
            # Extract filename from # filename: comment
            fn_match = re.search(r"(?:#|//)\s*filename:\s*(\S+)", code)
            if fn_match:
                filename = fn_match.group(1)
            else:
                ext = {"python": "py", "py": "py", "javascript": "js",
                       "js": "js", "typescript": "ts", "html": "html",
                       "css": "css", "java": "java", "cpp": "cpp", "c": "c",
                       "rust": "rs", "go": "go", "bash": "sh", "sh": "sh",
                       "sql": "sql", "json": "json", "yaml": "yaml",
                       "yml": "yaml", "xml": "xml"}.get(lang, "txt")
                filename = f"output_{int(time.time())}.{ext}"
            result = vscode.save_and_open(code, filename, lang)
            saved.append({"filename": filename, "lang": lang,
                          "path": result.get("path", ""),
                          "status": result.get("status", ""),
                          "opened_in_vscode": result.get("opened_in_vscode", False)})
        return saved
