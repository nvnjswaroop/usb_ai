"""
Voice Tool
TTS: Windows System.Speech via temp PowerShell script file
     Supports: streaming token-by-token with token bucket algorithm,
               selectable voice (male/female), speed control
STT: OpenAI Whisper (local, offline)
"""
import os
import queue
from logging_config import getLogger
_log = getLogger("usbai")

import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path


def _strip_for_speech(text: str) -> str:
    """Remove code blocks, markdown, keep only speakable prose."""
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"`[^`]+`", " ", text)
    text = re.sub(r"[#*_~>|]", "", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"!?\[.*?\]\(.*?\)", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ── Token Bucket TTS ──────────────────────────────────────────────────────────
class _StreamTTS:
    """
    Streaming TTS engine using a token bucket.
    - Tokens are speakable units (words/punctuation)
    - Bucket capacity: number of tokens that can be spoken immediately
    - Refill rate: tokens added per second
    - When bucket is empty, tokens are queued and spoken as the bucket refills
    """

    def __init__(self, rate: int = 175, voice_id: str = "",
                 bucket_capacity: int = 5, refill_rate: float = 3.0):
        self.rate           = rate
        self.voice_id       = voice_id
        self.bucket_cap     = bucket_capacity
        self.refill_rate    = refill_rate
        self.bucket         = float(bucket_capacity)
        self._queue         = queue.Queue()
        self._speaking      = False
        self._stop          = threading.Event()
        self._refill_lock   = threading.Lock()
        self._tts_lock      = threading.Lock()
        self._current_proc  = None
        self._refill_thread = None
        # ponytail: fixed 4-worker emission pool — prevents unbounded thread spawn
        # under TTS burst. Tokens are queued here instead of spawning new threads.
        self._emit_queue   = queue.Queue()
        self._workers       = []   # started lazily on first enqueue
        self._workers_lock  = threading.Lock()
        self._started      = False

    def _refiller(self):
        """Background thread: continuously refills the bucket over time."""
        interval = 1.0 / max(self.refill_rate, 0.1)
        while not self._stop.is_set():
            time.sleep(interval)
            if self._speaking:
                with self._refill_lock:
                    self.bucket = min(self.bucket_cap, self.bucket + 1.0)
                    # Drain the queue if bucket has capacity
                    self._drain_queue()

    def _drain_queue(self):
        """Speak queued tokens while bucket has capacity — via emit pool."""
        while self.bucket > 0:
            try:
                token = self._queue.get_nowait()
            except queue.Empty:
                break
            self.bucket -= 1
            self._emit_queue.put(token.strip())

    def _speak_now(self, token: str):
        """Speak a single token immediately (blocking)."""
        if not token:
            return
        with self._tts_lock:
            if self._stop.is_set():
                return
            try:
                self._current_proc = _speak_raw(
                    token, self.rate, self.voice_id)
                if self._current_proc:
                    self._current_proc.wait()
                    self._current_proc = None
            except (OSError, subprocess.SubprocessError) as e:
                # ponytail: per-token speech is best-effort; a dead TTS engine
                # must not kill the emit worker — but it gets logged now
                # instead of vanishing.
                _log.warning(f"TTS-TOKEN: speak failed: {e}")

    def start(self):
        """Start the TTS engine and the 4-worker emit pool."""
        self._speaking = True
        self._stop.clear()
        self._refill_thread = threading.Thread(target=self._refiller, daemon=True)
        self._refill_thread.start()
        # ponytail: 4-worker emit pool — lazily started once.
        self._ensure_workers()

    def _ensure_workers(self):
        """Spawn 4 daemon emit workers. Idempotent — only runs once."""
        with self._workers_lock:
            if self._started:
                return
            self._started = True
        for _ in range(4):
            t = threading.Thread(target=self._emit_worker, daemon=True)
            t.start()
            self._workers.append(t)

    def _emit_worker(self):
        """Pull tokens from the emit queue and speak them. Runs until _stop."""
        while not self._stop.is_set():
            try:
                token = self._emit_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if self._stop.is_set():
                break
            self._speak_now(token)
            self._emit_queue.task_done()

    def enqueue(self, token: str) -> bool:
        """
        Add a token to be spoken.
        If bucket has capacity: speak immediately via emit pool (bucket -= 1)
        Otherwise: queue it for when bucket refills.
        Returns True if queued for emission, False if queued in refill bucket.
        """
        token = token.strip()
        if not token:
            return True
        with self._refill_lock:
            self._ensure_workers()
            if self.bucket > 0:
                self.bucket -= 1
                self._emit_queue.put(token)
                return True
            else:
                self._queue.put(token)
                return False

    def enqueue_text(self, text: str) -> list:
        """
        Split text into speakable units and enqueue them.
        Returns list of booleans (True=spoken immediately, False=queued).
        """
        units = _split_into_units(text)
        results = []
        for unit in units:
            results.append(self.enqueue(unit))
        return results

    def stop(self):
        """Stop all TTS and clear the queue."""
        self._speaking = False
        self._stop.set()
        with self._tts_lock:
            if self._current_proc and self._current_proc.poll() is None:
                try:
                    self._current_proc.terminate()
                except (OSError, subprocess.SubprocessError) as e:
                    _log.warning(f"TTS-STOP: terminate failed: {e}")
        # Clear queue without spinning
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass
        # Also clear the emit queue
        try:
            while True:
                self._emit_queue.get_nowait()
        except queue.Empty:
            pass
        self.bucket = float(self.bucket_cap)

    def wait(self):
        """Block until the queue is fully drained."""
        while not self._queue.empty() or self._speaking:
            time.sleep(0.05)


def _split_into_units(text: str) -> list:
    """
    Split text into speakable units: primarily by spaces,
    but keep sentences and clauses together where natural.
    """
    if not text:
        return []
    # Split on sentence-ending punctuation first
    units = []
    # Split on sentence boundaries: . ! ? followed by space
    parts = re.split(r"(?<=[.!?])\s+", text)
    for part in parts:
        if not part.strip():
            continue
        # Within each sentence, split on commas if the chunk is too long
        if len(part) > 200:
            subparts = re.split(r"(?<=,)\s+", part)
            for sp in subparts:
                if sp.strip():
                    units.append(sp)
        else:
            units.append(part)
    return units


def _speak_raw(text: str, rate: int, voice_id: str) -> subprocess.Popen | None:
    """Speak a short piece of text using the OS TTS engine."""
    if not text.strip():
        return None
    if sys.platform == "win32":
        return _speak_windows_raw(text, rate, voice_id)
    elif sys.platform == "darwin":
        return _speak_mac_raw(text, rate)
    else:
        return _speak_linux_raw(text, rate)


def _speak_windows_raw(text: str, rate: int, voice_id: str) -> subprocess.Popen | None:
    """Speak a short snippet using Windows System.Speech."""
    sapi_rate = max(-10, min(10, int((rate - 175) / 15)))
    # Escape for PowerShell single-quoted string
    safe = (text.replace("'", "''")
              .replace("`", "``")
              .replace("$", "`$")
              .replace('"', "`'")
            )[:400]

    lines = [
        "Add-Type -AssemblyName System.Speech",
        "$tts = New-Object System.Speech.Synthesis.SpeechSynthesizer",
        f"$tts.Rate = {sapi_rate}",
    ]
    if voice_id:
        # Select specific voice (e.g. "Microsoft David" or "Microsoft Zira")
        safe_voice = voice_id.replace("'", "''")
        lines.append(f"$tts.SelectVoice('{safe_voice}')")
    lines.append(f"$tts.Speak('{safe}')")
    lines.append("$tts.Dispose()")

    script = "\r\n".join(lines)
    tmp = None
    try:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".ps1", delete=False,
            encoding="utf-8-sig"
        )
        tmp.write(script)
        tmp.close()
        proc = subprocess.Popen(
            ["powershell", "-NonInteractive", "-WindowStyle", "Hidden",
             "-ExecutionPolicy", "Bypass", "-File", tmp.name],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return proc
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        _log.warning(f"TTS-WIN: spawn failed: {e}")
        return None
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass


def _speak_mac_raw(text: str, rate: int) -> subprocess.Popen | None:
    try:
        wpm = max(100, min(500, rate))
        return subprocess.Popen(
            ["say", "-r", str(wpm), text[:3000]],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        _log.warning(f"TTS-MAC: spawn failed: {e}")
        return None


def _speak_linux_raw(text: str, rate: int) -> subprocess.Popen | None:
    if not shutil.which("espeak"):
        return None
    try:
        wpm = max(80, min(450, rate))
        return subprocess.Popen(
            ["espeak", "-s", str(wpm), "--", text[:3000]],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        _log.warning(f"TTS-LINUX: spawn failed: {e}")
        return None


# ── VoiceTool ─────────────────────────────────────────────────────────────────
class VoiceTool:
    def __init__(self):
        self._tts_lock  = threading.Lock()
        self._tts_proc  = None   # current speaking subprocess (full-response TTS)
        self._stream    = None    # streaming TTS engine (_StreamTTS)

    def stop(self):
        """Stop any ongoing TTS."""
        if self._stream:
            self._stream.stop()
        if self._tts_proc and self._tts_proc.poll() is None:
            try:
                self._tts_proc.terminate()
            except Exception:
                pass

    # ── Streaming TTS (token bucket) ────────────────────────────────────────

    def start_stream_tts(self, rate: int = 175, voice_id: str = "",
                         bucket_capacity: int = 5,
                         refill_rate: float = 3.0) -> dict:
        """Start a streaming TTS session with token bucket."""
        if self._stream:
            self._stream.stop()
        self._stream = _StreamTTS(
            rate=rate,
            voice_id=voice_id,
            bucket_capacity=bucket_capacity,
            refill_rate=refill_rate,
        )
        self._stream.start()
        return {"status": "ok", "bucket_capacity": bucket_capacity,
                "refill_rate": refill_rate, "rate": rate, "voice_id": voice_id}

    def stream_token(self, token: str) -> dict:
        """Add a token to the streaming TTS queue."""
        if not self._stream:
            return {"status": "error", "message": "Stream TTS not started."}
        spoken = self._stream.enqueue(token)
        return {"status": "ok", "spoken": spoken, "bucket": self._stream.bucket}

    def stream_text(self, text: str) -> dict:
        """Add multiple tokens from a text string."""
        if not self._stream:
            return {"status": "error", "message": "Stream TTS not started."}
        results = self._stream.enqueue_text(text)
        return {"status": "ok", "units": len(results),
                "spoken_immediately": sum(1 for r in results if r),
                "queued": sum(1 for r in results if not r)}

    def stop_stream_tts(self) -> dict:
        """Stop the streaming TTS session."""
        if self._stream:
            self._stream.stop()
            self._stream = None
        return {"status": "ok"}

    def get_stream_status(self) -> dict:
        """Return current bucket state."""
        if not self._stream:
            return {"status": "ok", "active": False}
        return {
            "status": "ok",
            "active": True,
            "bucket": self._stream.bucket,
            "bucket_capacity": self._stream.bucket_cap,
            "refill_rate": self._stream.refill_rate,
            "rate": self._stream.rate,
            "voice_id": self._stream.voice_id,
            "queue_size": self._stream._queue.qsize(),
        }

    # ── Full-response TTS ─────────────────────────────────────────────────────

    def speak(self, text: str, rate: int = 175, volume: float = 1.0,
              voice_id: str = "") -> dict:
        """Speak a full text response (non-streaming)."""
        clean = _strip_for_speech(text)
        if not clean:
            return {"status": "ok", "message": "Nothing to speak."}
        with self._tts_lock:
            if sys.platform == "win32":
                return self._speak_windows(clean, rate, volume, voice_id)
            elif sys.platform == "darwin":
                return self._speak_mac(clean, rate, voice_id)
            else:
                return self._speak_linux(clean, rate, voice_id)

    def _speak_windows(self, text: str, rate: int, volume: float,
                        voice_id: str) -> dict:
        sapi_rate = max(-10, min(10, int((rate - 175) / 15)))
        sapi_vol  = max(0,   min(100, int(volume * 100)))

        sentences = re.split(r"(?<=[.!?])\s+", text)
        chunks = []
        for s in sentences:
            s = s.strip()
            if not s:
                continue
            if len(s) > 300:
                parts = re.split(r"(?=[,;])\s+", s)
                chunks.extend(p.strip() for p in parts if p.strip())
            else:
                chunks.append(s)

        ps_lines = [
            "Add-Type -AssemblyName System.Speech",
            "$tts = New-Object System.Speech.Synthesis.SpeechSynthesizer",
            f"$tts.Rate = {sapi_rate}",
            f"$tts.Volume = {sapi_vol}",
        ]
        if voice_id:
            safe_voice = voice_id.replace("'", "''")
            ps_lines.append(f"$tts.SelectVoice('{safe_voice}')")
        for chunk in chunks:
            safe = (chunk.replace("'", "''")
                      .replace("`", "``")
                      .replace("$", "`$")
                      .replace('"', "`'")
                    )[:400]
            ps_lines.append(f"$tts.Speak('{safe}')")
        ps_lines.append("$tts.Dispose()")

        script_content = "\r\n".join(ps_lines)

        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".ps1", delete=False,
            encoding="utf-8-sig"
        )
        tmp.write(script_content)
        tmp.close()

        try:
            self._tts_proc = subprocess.Popen(
                ["powershell", "-NonInteractive", "-WindowStyle", "Hidden",
                 "-ExecutionPolicy", "Bypass", "-File", tmp.name],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            # ponytail: 60s cap — a single utterance should never need 5 min; shorter cap = faster recovery from hung powershell/say/espeak.
            self._tts_proc.wait(timeout=60)
            return {"status": "ok"}
        except subprocess.TimeoutExpired:
            self.stop()
            return {"status": "error", "message": "TTS timed out."}
        except (OSError, ValueError, subprocess.SubprocessError) as e:
            _log.warning(f"TTS-WIN: speak failed: {e}")
            return {"status": "error", "message": str(e)}
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    def _speak_mac(self, text: str, rate: int, voice_id: str) -> dict:
        try:
            wpm = max(100, min(500, rate))
            voice_arg = ["-v", voice_id] if voice_id else []
            self._tts_proc = subprocess.Popen(
                ["say", "-r", str(wpm)] + voice_arg + [text[:3000]],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._tts_proc.wait(timeout=60)
            return {"status": "ok"}
        except (OSError, ValueError, subprocess.SubprocessError) as e:
            _log.warning(f"TTS-MAC: speak failed: {e}")
            return {"status": "error", "message": str(e)}

    def _speak_linux(self, text: str, rate: int, voice_id: str) -> dict:
        if not shutil.which("espeak"):
            return {"status": "error",
                    "message": "No TTS. Install: sudo apt install espeak"}
        try:
            wpm = max(80, min(450, rate))
            voice_arg = ["-v", voice_id] if voice_id else []
            self._tts_proc = subprocess.Popen(
                ["espeak", "-s", str(wpm)] + voice_arg + ["--", text[:3000]],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._tts_proc.wait(timeout=60)
            return {"status": "ok"}
        except (OSError, ValueError, subprocess.SubprocessError) as e:
            _log.warning(f"TTS-LINUX: speak failed: {e}")
            return {"status": "error", "message": str(e)}

    # ── Voice list ─────────────────────────────────────────────────────────────

    def get_voices(self) -> dict:
        if sys.platform == "win32":
            try:
                script = (
                    "Add-Type -AssemblyName System.Speech; "
                    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                    "$s.GetInstalledVoices() | "
                    "ForEach-Object { $v=$_.VoiceInfo; "
                    "$g=$v.Gender; $n=$v.Name; "
                    "$desc=if($v.Description){$v.Description}else{$n}; "
                    "$type=if($g -eq 1){'Female'}elseif($g -eq 2){'Male'}else{'Unknown'}; "
                    "Write-Output \"$n|$type|$desc\" }"
                )
                tmp = tempfile.NamedTemporaryFile(
                    mode="w", suffix=".ps1", delete=False,
                    encoding="utf-8-sig"
                )
                tmp.write(script)
                tmp.close()
                try:
                    r = subprocess.run(
                        ["powershell", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", tmp.name],
                        capture_output=True, text=True, timeout=15)
                    voices = []
                    for line in r.stdout.strip().splitlines():
                        if "|" in line:
                            parts = line.split("|", 2)
                            voices.append({
                                "id":   parts[0].strip(),
                                "type": parts[1].strip(),
                                "name": parts[2].strip() if len(parts) > 2 else parts[0].strip(),
                            })
                    return {"status": "ok", "voices": voices}
                finally:
                    try: os.unlink(tmp.name)
                    except OSError: pass
            except (OSError, ValueError, subprocess.SubprocessError) as e:
                _log.warning(f"TTS-VOICES: probe failed: {e}")
                return {"status": "error", "message": str(e)}
        elif sys.platform == "darwin":
            try:
                r = subprocess.run(
                    ["say", "-v", "?"],
                    capture_output=True, text=True, timeout=10)
                voices = []
                for line in r.stdout.splitlines():
                    parts = line.strip().split(" ", 1)
                    if parts:
                        gender = "Unknown"
                        if "female" in parts[0].lower() or "zira" in parts[0].lower():
                            gender = "Female"
                        elif "male" in parts[0].lower() or "david" in parts[0].lower():
                            gender = "Male"
                        voices.append({"id": parts[0], "type": gender, "name": line.strip()})
                return {"status": "ok", "voices": voices}
            except (OSError, ValueError, subprocess.SubprocessError) as e:
                _log.warning(f"TTS-VOICES: say -v failed: {e}")
                return {"status": "error", "message": str(e)}
        return {"status": "ok", "voices": [{"id": "default", "type": "Unknown", "name": "System Default"}]}

    # ── STT (Whisper) ─────────────────────────────────────────────────────────

    def transcribe(self, audio_path: str, model_size: str = "base",
                   whisper_dir: str = None) -> dict:
        try:
            import whisper
        except ImportError:
            return {"status": "error",
                    "message": "openai-whisper not installed. Run setup again."}

        p = Path(audio_path)
        if not p.exists():
            return {"status": "error", "message": f"Audio not found: {audio_path}"}

        # Load whisper model once (cached on first call).
        # ponytail: whisper.load_model is ~5s cold-start per process; the
        # model stays hot in VRAM/RAM for the life of the app. Caching here
        # (rather than at VoiceTool.__init__) means users without whisper
        # installed never pay the import cost.
        if not hasattr(self, '_whisper_model'):
            self._whisper_model = whisper.load_model(
                model_size, download_root=whisper_dir or str(Path.home() / "whisper_models"))

        work_path = str(p)
        tmp_wav   = None

        if p.suffix.lower() in (".webm", ".ogg", ".opus", ".m4a"):
            converted = self._to_wav(p)
            if converted:
                tmp_wav   = converted
                work_path = converted

        try:
            result = self._whisper_model.transcribe(work_path, fp16=False)
            text   = result.get("text", "").strip()
            lang   = result.get("language", "unknown")
            _log.info(f"STT ({lang}): {text[:80]}")
            return {"status": "ok", "text": text, "language": lang}
        except Exception as e:
            # ponytail: whisper raises torch-internal exception types we can't
            # enumerate (audio decode, CUDA/CPU backend errors). Broad is
            # deliberate here — but it's logged, not swallowed.
            _log.warning(f"STT: transcribe failed: {type(e).__name__}: {e}")
            return {"status": "error", "message": str(e)}
        finally:
            if tmp_wav:
                try: os.unlink(tmp_wav)
                except OSError: pass

    def _to_wav(self, p: Path):
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        tmp_path = tmp.name
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            usb_ff = Path(__file__).parent.parent.parent / "ffmpeg.exe"
            if usb_ff.exists():
                ffmpeg = str(usb_ff)
        if ffmpeg:
            try:
                r = subprocess.run(
                    [ffmpeg, "-y", "-i", str(p),
                     "-ar", "16000", "-ac", "1", tmp_path],
                    capture_output=True, timeout=30)
                if r.returncode == 0 and Path(tmp_path).exists():
                    return tmp_path
            except (OSError, subprocess.SubprocessError) as e:
                _log.warning(f"STT: ffmpeg failed: {e}")
        Path(tmp_path).unlink(missing_ok=True)
        return None

    # ── Warmup ────────────────────────────────────────────────────────────────
    def warmup(self, whisper_dir: str = None, model_size: str = "base") -> dict:
        """Preload the Whisper model so the first /api/voice/transcribe skips
        the ~5s cold load. Called from main.py when USB_AI_WARMUP_WHISPER=1."""
        if hasattr(self, '_whisper_model'):
            return {"status": "ok", "message": "already loaded"}
        try:
            import whisper
        except ImportError:
            return {"status": "error",
                    "message": "openai-whisper not installed. Run setup again."}
        try:
            self._whisper_model = whisper.load_model(
                model_size, download_root=whisper_dir or str(Path.home() / "whisper_models"))
            _log.info(f"WARMUP: Whisper '{model_size}' loaded")
            return {"status": "ok"}
        except (OSError, ValueError, RuntimeError) as e:
            _log.warning(f"WARMUP: whisper load failed: {e}")
            return {"status": "error", "message": str(e)}
