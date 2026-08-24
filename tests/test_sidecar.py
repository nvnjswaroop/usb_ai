"""Phase B sidecar tests — ServerEngine + SidecarManager against a FAKE
llama-server (real HTTP via stdlib http.server, no binary needed).

Also guards the engine interface contract: both backends must expose the
same public surface or routers/FakeLLM-based tests will drift apart.

Run: python tests/test_sidecar.py
"""
import sys, json, socket, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch

_APP = Path(__file__).resolve().parent.parent / "app"
sys.path.insert(0, str(_APP))

import llm_server as ls  # noqa: E402

# ── Fake llama-server ─────────────────────────────────────────────────────────
class _Handler(BaseHTTPRequestHandler):
    stream_tokens = ["Hello", " ", "world"]
    json_content = '```json\n{"title": "T"}\n```'
    seen_payloads = []

    def log_message(self, *a):  # silence
        pass

    def do_GET(self):
        if self.path == "/health":
            body = json.dumps({"status": "ok"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        _Handler.seen_payloads.append(payload)
        if self.path == "/v1/chat/completions" and payload.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for tok in _Handler.stream_tokens:
                frame = {"choices": [{"delta": {"content": tok}}]}
                self.wfile.write(f"data: {json.dumps(frame)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
        else:
            body = json.dumps(
                {"choices": [{"message": {"content": _Handler.json_content}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


def start_fake_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port


class TestSidecar(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.port = start_fake_server()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        _Handler.seen_payloads = []
        mgr = ls.SidecarManager(binary=Path("unused"), command_prefix=[])
        # ponytail: attach to the ALREADY-RUNNING fake server instead of
        # spawning — manager.start is exercised separately below.
        mgr.port = self.port
        mgr.proc = None
        mgr.external = True
        self.eng = ls.ServerEngine(Path("."), manager=mgr)
        self.eng.current_model = "fake.gguf"
        self.eng._n_ctx = 2048
        from llm import SlidingWindow
        self.eng._window = SlidingWindow(n_ctx=2048,
                                         count_fn=lambda s: max(1, len(s) // 4))

    def test_stream_tokens_yields_in_order_and_persists_window(self):
        out = list(self.eng.stream_tokens([], "hi there", "sys", 0.7, 256))
        self.assertEqual("".join(out), "Hello world")
        # exchange persisted into the sliding window
        self.assertEqual(self.eng._window.pair_count, 1)
        self.assertEqual(_Handler.seen_payloads[-1]["messages"][-1]["role"],
                         "user")

    def test_generate_json_strips_fences(self):
        r = self.eng.generate_json("give me json")
        self.assertEqual(r, '{"title": "T"}')

    def test_max_tokens_clamped_to_window_budget(self):
        list(self.eng.stream_tokens([], "x" * 4000, "s", 0.7, 99999))
        sent = _Handler.seen_payloads[-1]
        self.assertLessEqual(sent["max_tokens"], 2048)

    def test_not_loaded_raises(self):
        eng = ls.ServerEngine(Path("."),
                              manager=ls.SidecarManager(Path("x"), command_prefix=[]))
        with self.assertRaises(RuntimeError):
            list(eng.stream_tokens([], "m", "s", 0.7, 8))

    def test_manager_start_stop_with_stub_binary(self):
        stub = Path(_APP.parent / "tests" / "_stub_llama_server.py")
        # ponytail: argv becomes [python, stub, x, --host..., --port N, ...].
        # The stub parses --port and binds it; 'x' stands in for the binary
        # path (ignored by the stub).
        mgr = ls.SidecarManager(binary=Path("x"),
                                command_prefix=[sys.executable, str(stub)],
                                startup_timeout=15)
        try:
            mgr.start(model_path=Path("fake.gguf"), ctx_size=512, threads=2,
                      n_gpu_layers=-1, mmproj=None, model_name="f",
                      on_phase=lambda: None)
            self.assertIsNotNone(mgr.proc)
            self.assertGreater(mgr.port, 0)
        finally:
            mgr.stop()
        self.assertIsNone(mgr.proc)

    def test_early_exit_surfaces_log_tail(self):
        mgr = ls.SidecarManager(binary=Path(sys.executable),
                                command_prefix=[sys.executable, "-c",
                                                "import sys; sys.exit(3)"],
                                startup_timeout=10)
        with self.assertRaises(RuntimeError) as cm:
            mgr.start(model_path=Path("x"), ctx_size=1, threads=1,
                      n_gpu_layers=0, mmproj=None, model_name="m")
        self.assertIn("exited early", str(cm.exception))


class TestInterfaceParity(TestCase):
    """Both engines must expose the same public surface — routers and tests
    depend on duck-typing, so drift here breaks one backend silently.

    ponytail: checks INSTANCES, not classes — current_model/_window are set
    in __init__, so class-level hasattr would false-negative both engines.
    """

    REQUIRED_METHODS = [
        "is_loaded", "is_loading", "set_loading", "supports_vision",
        "load_model_sync", "stream_tokens", "generate_json",
        "generate_code_files", "reset_window", "get_load_progress", "_count",
    ]
    REQUIRED_ATTRS = ["current_model"]

    def _server(self):
        mgr = ls.SidecarManager(Path("x"), command_prefix=[])
        return ls.ServerEngine(Path("."), manager=mgr)

    def test_server_engine_has_full_surface(self):
        eng = self._server()
        for name in self.REQUIRED_METHODS:
            self.assertTrue(hasattr(eng, name), f"ServerEngine missing {name}")
        for name in self.REQUIRED_ATTRS:
            self.assertTrue(hasattr(eng, name), f"ServerEngine missing {name}")
        # server-only lifecycle hook (inline has no child process)
        self.assertTrue(hasattr(eng, "shutdown"))

    def test_inline_engine_has_full_surface(self):
        import llm as llm_mod
        eng = llm_mod.LLMEngine(Path("."), Path("."))
        for name in self.REQUIRED_METHODS:
            self.assertTrue(hasattr(eng, name), f"LLMEngine missing {name}")
        for name in self.REQUIRED_ATTRS:
            self.assertTrue(hasattr(eng, name), f"LLMEngine missing {name}")


class TestBackendSelection(TestCase):
    """Regression: build_default once computed the chosen backend into a
    local variable and then ignored it - inline always ran regardless of
    USB_AI_BACKEND. This pins the wiring AND the Phase C default (server)."""

    def tearDown(self):
        import os
        os.environ.pop("USB_AI_BACKEND", None)

    def _bd(self):
        from container import build_default, build_paths
        return build_default(build_paths(Path(_APP.parent)))

    def test_server_is_default(self):
        import os
        os.environ.pop("USB_AI_BACKEND", None)
        c = self._bd()
        import llm_server as ls
        self.assertIsInstance(c.llm, ls.ServerEngine)

    def test_missing_binary_fails_loud_at_load(self):
        """Lazy resolution: boot succeeds headless (CI/ubuntu), but the first
        manager access - i.e. /api/models/load - raises the actionable
        RuntimeError instead of something vague later."""
        import os
        with patch.dict(os.environ, {
                "USB_AI_BACKEND": "server",
                "USB_AI_LLAMA_DIR": str(_APP.parent / "bin" / "nope")}):
            c = self._bd()
            with self.assertRaises(RuntimeError) as cm:
                _ = c.llm.manager  # noqa: B018 - property triggers resolve
            self.assertIn("llama-server", str(cm.exception))

    def test_inline_requires_explicit_env(self):
        try:
            from llama_cpp import Llama  # noqa: F401 - legacy engine needs it
        except ImportError:
            self.skipTest("llama-cpp-python not installed")
        import os
        with patch.dict(os.environ, {"USB_AI_BACKEND": "inline"}):
            c = self._bd()
            import llm as llm_mod
            self.assertIsInstance(c.llm, llm_mod.LLMEngine)


if __name__ == "__main__":
    main(verbosity=2)
