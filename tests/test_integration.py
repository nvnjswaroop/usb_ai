"""Happy-path integration tests — routers end-to-end via TestClient.

Covers the surfaces that previously had zero happy-path coverage:
  - /api/chat/stream SSE flow with a scripted fake LLM (tokens, artifact
    emission, done event, session persistence)
  - /api/files/upload streaming cap (11MB -> 413) and small-file success
  - /api/pdf/extract round-trip on a real generated PDF (pymupdf-gated)
  - runtime non-loopback guard (2.2) via raw ASGI scopes

Run: python tests/test_integration.py   (or via unittest discover)
"""
import sys, os, json, asyncio, tempfile
from pathlib import Path
from unittest import TestCase, main

_APP = Path(__file__).resolve().parent.parent / "app"
sys.path.insert(0, str(_APP))

try:
    from fastapi.testclient import TestClient
    _HAVE_FASTAPI = True
except ImportError:
    _HAVE_FASTAPI = False


# ── FakeLLM ───────────────────────────────────────────────────────────────────
class FakeLLM:
    """Scripted stand-in for LLMEngine — no model, no threads."""
    current_model = "fake-model"

    def __init__(self, tokens):
        self._tokens = tokens
        self.calls = []

    def is_loaded(self): return True
    def is_loading(self): return False
    def supports_vision(self): return False
    def reset_window(self): pass

    def stream_tokens(self, messages, user_message, system,
                      temperature, max_tokens, image_b64=None):
        self.calls.append({"history": messages, "user": user_message})
        yield from self._tokens

    def generate_code_files(self, text, output_dir):
        return []


def _make_client(tmp_history, tmp_output, llm):
    import main as _m
    from dependencies import get_llm, get_paths, get_session_store
    from sessions import SessionStore
    from types import SimpleNamespace
    store = SessionStore.default(Path(tmp_history))
    paths = SimpleNamespace(root=Path(tmp_history).parent,
                            output=Path(tmp_output),
                            history=Path(tmp_history),
                            models=Path(tmp_output), whisper=Path(tmp_output),
                            ui=Path(tmp_output))
    _m.app.dependency_overrides[get_llm] = lambda: llm
    _m.app.dependency_overrides[get_session_store] = lambda: store
    _m.app.dependency_overrides[get_paths] = lambda: paths
    _m.app.state.rate_limiter.reset()
    return TestClient(_m.app), _m, store


class IntegrationBase(TestCase):
    def setUp(self):
        if not _HAVE_FASTAPI:
            self.skipTest("fastapi/httpx not installed")
        os.environ.pop("USB_API_KEY", None)
        self._tmp = tempfile.TemporaryDirectory()
        self.history = Path(self._tmp.name) / "history"
        self.output = Path(self._tmp.name) / "output"
        self.history.mkdir(); self.output.mkdir()

    def tearDown(self):
        import main as _m
        from dependencies import get_llm, get_paths, get_session_store
        for k in (get_llm, get_paths, get_session_store):
            _m.app.dependency_overrides.pop(k, None)
        os.environ.pop("USB_API_KEY", None)
        self._tmp.cleanup()


class TestChatStreamFlow(IntegrationBase):
    def test_tokens_artifact_done_and_persistence(self):
        tokens = ["Here you go:\n",
                  "```python\n", "# filename: demo.py\n",
                  "print('hi')\n", "```\n", "Done."]
        llm = FakeLLM(tokens)
        client, _m, store = _make_client(self.history, self.output, llm)

        with client.stream("POST", "/api/chat/stream",
                           json={"session_id": "itest-1",
                                 "message": "make a demo script"}) as resp:
            self.assertEqual(resp.status_code, 200)
            self.assertIn("text/event-stream", resp.headers["content-type"])
            frames = []
            buf = ""
            for chunk in resp.iter_text():
                buf += chunk
                while "\n\n" in buf:
                    frame, buf = buf.split("\n\n", 1)
                    frames.append(frame)

        events = [json.loads(f[len("data: "):]) for f in frames
                  if f.startswith("data: ")]

        # user turn + streamed assistant text persisted exactly once
        saved = store.load("itest-1")
        roles = [m["role"] for m in saved["messages"]]
        self.assertEqual(roles, ["user", "assistant"])
        joined = "".join(tokens)
        self.assertEqual(saved["messages"][1]["content"], joined)
        self.assertEqual(saved["title"], "make a demo script")

        # artifact emitted exactly once for the closed ```python block.
        # ponytail: the SSE frame merges {'type':'artifact'} with
        # Artifact.model_dump(), whose own type ("code") wins — a wire-format
        # quirk the UI already depends on, so match on file_name instead.
        artifacts = [e for e in events if e.get("file_name")]
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0]["file_name"], "demo.py")
        self.assertEqual(artifacts[0]["type"], "code")

        # terminal done event present, error absent
        dones = [e for e in events if e.get("done")]
        errs = [e for e in events if "error" in e]
        self.assertEqual(len(dones), 1)
        self.assertEqual(errs, [])
        # the fake engine actually received the prior history as context
        self.assertEqual(llm.calls[0]["user"], "make a demo script")


class TestUploadCap(IntegrationBase):
    def test_oversize_upload_413_no_partial_file(self):
        llm = FakeLLM([])
        client, _m, store = _make_client(self.history, self.output, llm)
        big = b"x" * (10 * 1024 * 1024 + 1)
        r = client.post("/api/files/upload",
                        files={"file": ("big.bin", big)})
        self.assertEqual(r.status_code, 413)
        leftovers = list(self.output.glob("*big.bin*"))
        self.assertEqual(leftovers, [], f"partial file left behind: {leftovers}")

    def test_small_upload_succeeds_to_output_dir(self):
        llm = FakeLLM([])
        client, _m, store = _make_client(self.history, self.output, llm)
        r = client.post("/api/files/upload",
                        files={"file": ("hello.txt", b"hi there")})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")
        dest = Path(r.json()["path"])
        self.assertTrue(dest.exists())
        self.assertEqual(dest.read_bytes(), b"hi there")


class TestPdfExtract(IntegrationBase):
    def setUp(self):
        super().setUp()
        # extract_text now routes through file_tool._resolve — the temp dir
        # must be allowlisted for the happy-path test (the denial test below
        # deliberately uses a path OUTSIDE any allowed base).
        from tools.file_tool import ALLOWED_BASE_DIRS
        ALLOWED_BASE_DIRS.append(Path(self._tmp.name))

    def tearDown(self):
        from tools.file_tool import ALLOWED_BASE_DIRS
        try:
            ALLOWED_BASE_DIRS.remove(Path(self._tmp.name))
        except ValueError:
            pass
        super().tearDown()

    def test_pdf_roundtrip(self):
        try:
            import fitz  # pymupdf
        except ImportError:
            self.skipTest("pymupdf not installed")
        pdf_path = Path(self._tmp.name) / "sample.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "USB_AI_INTEGRATION_MARKER_12345")
        doc.save(str(pdf_path))
        doc.close()

        llm = FakeLLM([])
        client, _m, store = _make_client(self.history, self.output, llm)
        r = client.post("/api/pdf/extract", json={"path": str(pdf_path)})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "ok")
        self.assertIn("USB_AI_INTEGRATION_MARKER_12345", body["content"])

    def test_pdf_outside_allowlist_denied(self):
        """Regression (audit 2026-09-03): extract_text used to bypass
        _resolve and read ANY absolute .pdf path on disk."""
        llm = FakeLLM([])
        client, _m, store = _make_client(self.history, self.output, llm)
        outside = Path(self._tmp.name).parent / "usbai_outside_allowlist.pdf"
        outside.write_bytes(b"%PDF-1.4 fake")  # content irrelevant — path is the attack
        try:
            r = client.post("/api/pdf/extract", json={"path": str(outside)})
        finally:
            outside.unlink(missing_ok=True)
        self.assertEqual(r.status_code, 200)  # app returns 200 + status payload
        self.assertEqual(r.json()["status"], "error")


class TestLanGuardRawAsgi(IntegrationBase):
    """2.2 — the runtime non-loopback guard, exercised through raw ASGI
    scopes (TestClient can't spoof a remote client IP)."""
    def _call(self, method, path, client_addr):
        import main as _m
        async def run():
            scope = {"type": "http", "asgi": {"version": "3.0"},
                     "http_version": "1.1", "method": method, "scheme": "http",
                     "path": path, "raw_path": path.encode(),
                     "query_string": b"", "root_path": "",
                     "server": ("srv", 80), "client": client_addr,
                     "headers": [(b"host", b"t")]}
            msgs = []
            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}
            async def send(m): msgs.append(m)
            await _m.app(scope, receive, send)
            start = next(m for m in msgs if m["type"] == "http.response.start")
            return start["status"]
        return asyncio.run(run())

    def test_remote_client_blocked_without_key(self):
        import main as _m
        _m.app.state.rate_limiter.reset()
        self.assertEqual(self._call("GET", "/api/status", ("192.168.1.77", 5000)), 403)
        self.assertEqual(self._call("GET", "/api/status", ("10.0.0.5", 5000)), 403)

    def test_loopback_and_health_unaffected(self):
        import main as _m
        _m.app.state.rate_limiter.reset()
        self.assertEqual(self._call("GET", "/api/status", ("127.0.0.1", 5000)), 200)
        self.assertEqual(self._call("GET", "/api/health", ("192.168.1.77", 5000)), 200)

    def test_ipv4_mapped_ipv6_treated_as_loopback(self):
        import main as _m
        _m.app.state.rate_limiter.reset()
        self.assertEqual(self._call("GET", "/api/status", ("::ffff:127.0.0.1", 5000)), 200)


class TestSessionIdValidation(IntegrationBase):
    """Regression (audit 2026-09-05): traversal sids must die at the door.

    /api/chat/stream takes session_id from a raw JSON body — the primary
    read+write vector on every platform.
    """
    def test_chat_stream_traversal_sid_422(self):
        llm = FakeLLM([])
        client, _m, store = _make_client(self.history, self.output, llm)
        for sid in ("../../evil", "C:/Users/x/evil", "..\\..\\evil",
                    "ok_then/../escape"):
            r = client.post("/api/chat/stream",
                            json={"session_id": sid, "message": "hi"})
            self.assertEqual(r.status_code, 422, f"sid {sid!r} must 422, got {r.status_code}")
            self.assertEqual(list(self.history.glob("*evil*")), [],
                             f"no file may be touched for sid {sid!r}")

    def test_export_traversal_sid_422(self):
        llm = FakeLLM([])
        client, _m, store = _make_client(self.history, self.output, llm)
        r = client.post("/api/export",
                        json={"session_id": "../../evil", "format": "html"})
        self.assertEqual(r.status_code, 422)

    def test_path_param_traversal_422(self):
        llm = FakeLLM([])
        client, _m, store = _make_client(self.history, self.output, llm)
        # %2F stays encoded through route matching (no match -> 404);
        # %5C DECODES to backslash after match on Windows-shaped paths —
        # this is the exact vector proven in the audit.
        r = client.get("/api/sessions/..%5C..%5Cevil")
        self.assertIn(r.status_code, (404, 422))
        r = client.delete("/api/sessions/..%5C..%5Cevil")
        self.assertIn(r.status_code, (404, 422))


class TestPdfUploadNameCollision(IntegrationBase):
    """Regression (audit 2026-09-05): uploading "report.pdf" used to delete
    any pre-existing output/report.pdf (finally-unlink on a shared name)."""
    def test_upload_does_not_destroy_existing_file(self):
        llm = FakeLLM([])
        client, _m, store = _make_client(self.history, self.output, llm)
        existing = self.output / "report.pdf"
        existing.write_bytes(b"PRE-EXISTING USER FILE")
        r = client.post("/api/pdf/upload",
                        files={"file": ("report.pdf", b"%PDF-1.4 fake bytes")})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(existing.read_bytes(), b"PRE-EXISTING USER FILE",
                         "upload must not touch same-named files in output/")
        leftovers = [p for p in self.output.iterdir()
                     if p.name.startswith("pdf_")]
        self.assertEqual(leftovers, [], f"temp pdf left behind: {leftovers}")

    def test_upload_rejects_non_pdf(self):
        llm = FakeLLM([])
        client, _m, store = _make_client(self.history, self.output, llm)
        r = client.post("/api/pdf/upload",
                        files={"file": ("payload.exe", b"MZ fake")})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    main(verbosity=2)
