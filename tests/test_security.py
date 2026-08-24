"""New tests for security, streaming extractor, agent parser, schemas.

stdlib-only — runs without fastapi/pptx/etc. The auth-gate tests
live alongside the running app (out of scope here).
"""
import sys, os, types, json, tempfile
from pathlib import Path
from unittest import TestCase, main

# Ensure app/ is importable
_APP = Path(__file__).resolve().parent.parent / "app"
sys.path.insert(0, str(_APP))


class TestAgentParse(TestCase):
    """AgentTool._parse_tool_call: tolerant JSON extraction."""
    def _fn(self, allowed=None):
        src = (Path(__file__).resolve().parent.parent / "app" / "tools" / "agent_tool.py").read_text(encoding="utf-8")
        start = src.index("def _parse_tool_call")
        next_def = src.index("\n    def ", start + 1)
        method_src = src[start:next_def]
        method_src = method_src.replace("def _parse_tool_call(self, text:",
                                        "def _parse_tool_call(text:", 1)
        # Strip stray `self.` from helper calls once `self` is gone from the signature
        method_src = method_src.replace("self._validate_params(", "_validate_params(")
        from schemas import ToolCall, validate_params
        # ponytail: use the REAL validator from schemas — the old hand-mirrored
        # _Validator class could silently drift from production semantics.
        ns = {"json": __import__("json"),
              "Optional": __import__("typing").Optional,
              "ToolCall": ToolCall,
              "_ALLOWED_PARAMS": allowed or {},
              "_NO_SCHEMA_TOOLS": {"finish", "revise", "retry"},
              "_validate_params": lambda t, p, _a=(allowed or {}): validate_params(t, p, _a),
             }
        exec(method_src, ns)
        return ns["_parse_tool_call"]

    def test_valid(self):
        fn = self._fn({"finish": {"result", "thought", "params"}})
        r = fn('{"tool":"finish","params":{"result":"ok"},"thought":"done"}')
        self.assertIsNotNone(r)
        self.assertEqual(r.tool, "finish")

    def test_prose_wrapped(self):
        fn = self._fn({"run_python": {"code"}})
        r = fn('Here you go: {"tool":"run_python","params":{"code":"1+1"},"thought":"compute"} cheers')
        self.assertIsNotNone(r)
        self.assertEqual(r.tool, "run_python")

    def test_malformed(self):
        fn = self._fn()
        self.assertIsNone(fn('nope'))

    def test_no_tool_key(self):
        fn = self._fn({"read_file": {"path"}})
        self.assertIsNone(fn('{"params":{"path":"x"}}'))

    def test_reject_unknown_param(self):
        # F20 contract: LLM invents a field the manifest forbids — drop, don't dispatch.
        # (The >forwarded< python code reads files. Injecting `{"command": ...}` would be ignored anyway,
        # but rejecting at the gate stops the LLM from reasoning it's fine to add fields.)
        fn = self._fn({"read_file": {"path"}})
        r = fn('{"tool":"read_file","params":{"path":"x.py","command":"rm -rf /"},"thought":"oops"}')
        self.assertIsNone(r)

    def test_action_key_fallback(self):
        fn = self._fn({"finish": {"result"}})
        r = fn('{"action":"finish","result":"ok"}')
        self.assertIsNotNone(r)
        self.assertEqual(r.tool, "finish")


class TestPathTraversalClosed(TestCase):
    """The fix for the cwd-`..` hole. Reads after the F5 close must reject."""
    def test_relative_dotdot_denied(self):
        from tools.file_tool import _resolve
        with self.assertRaises(ValueError):
            _resolve("../../../../etc/passwd")

    def test_null_byte_denied(self):
        from tools.file_tool import _resolve
        with self.assertRaises(ValueError):
            _resolve("/home/user/file\0.txt")


class TestStreamingExtractor(TestCase):
    """StreamingArtifactExtractor emits artifacts as code blocks close."""
    def _cls(self):
        from artifacts import StreamingArtifactExtractor
        return StreamingArtifactExtractor

    def test_single_block(self):
        cl = self._cls()
        e = cl(); e.push("```python\nprint('x')\n```")
        self.assertGreaterEqual(len(e.flush()), 1)

    def test_orphan_block_flushes(self):
        cl = self._cls()
        e = cl(); e.push("```py\nopen code\n")
        self.assertEqual(len(e.flush()), 1)

    def test_multiple_blocks(self):
        cl = self._cls()
        e = cl(); e.push("```py\nA\n```\ntext\n```js\nB\n```")
        self.assertGreaterEqual(len(e.flush()), 2)


class TestSchemasDefaultFactories(TestCase):
    """Mutable-default fix: independent dicts/lists per instance."""
    def test_independent_dicts(self):
        from schemas import Artifact
        a1 = Artifact(type="x", title="t")
        a2 = Artifact(type="y", title="t")
        a1.metadata["mutate"] = True
        self.assertNotIn("mutate", a2.metadata)

    def test_independent_lists(self):
        from schemas import AgentResult
        r1 = AgentResult(status="ok", result="x")
        r2 = AgentResult(status="ok", result="y")
        r1.steps.append("polluted")
        self.assertNotEqual(r1.steps, r2.steps)


class TestAuthConstantTime(TestCase):
    """check_auth compares keys via secrets.compare_digest (never plaintext ==).

    ponytail: asserts on whole-file source, not a fixed char-window — the old
    800-char window rotted when middleware comments grew and went red.
    """
    def test_source_uses_compare_digest(self):
        src = (Path(__file__).resolve().parent.parent / "app" / "main.py").read_text(encoding="utf-8")
        self.assertIn("compare_digest", src)
        # No naive equality against the header value / expected key anywhere.
        self.assertNotRegex(src, r'(provided|expected)\s*==\s*["\']')
        self.assertNotRegex(src, r'==\s*(provided|expected)\b')


class TestAuthMiddleware(TestCase):
    """Test auth middleware with TestClient — missing/wrong/correct key.

    ponytail: patch os.environ, not module globals — dependencies.get_api_key()
    and main's middleware both read the env var per-call (single source of truth).
    """
    def test_missing_key_allowed_when_none_set(self):
        from fastapi.testclient import TestClient
        import main as _m
        old = os.environ.pop("USB_API_KEY", None)
        try:
            client = TestClient(_m.app)
            resp = client.get("/api/status")
            self.assertEqual(resp.status_code, 200)
        finally:
            if old is not None:
                os.environ["USB_API_KEY"] = old

    def test_wrong_key_returns_401(self):
        from fastapi.testclient import TestClient
        import main as _m
        old = os.environ.pop("USB_API_KEY", None)
        os.environ["USB_API_KEY"] = "secret-key"
        try:
            client = TestClient(_m.app)
            resp = client.get("/api/status", headers={"Authorization": "Bearer wrong-key"})
            self.assertEqual(resp.status_code, 401)
        finally:
            if old is not None:
                os.environ["USB_API_KEY"] = old

    def test_correct_key_succeeds(self):
        from fastapi.testclient import TestClient
        import main as _m
        old = os.environ.pop("USB_API_KEY", None)
        os.environ["USB_API_KEY"] = "secret-key"
        try:
            client = TestClient(_m.app)
            resp = client.get("/api/status", headers={"Authorization": "Bearer secret-key"})
            self.assertEqual(resp.status_code, 200)
        finally:
            if old is not None:
                os.environ["USB_API_KEY"] = old


class TestFilesWriteTraversal(TestCase):
    """Path traversal against /api/files/write — expect 403."""
    def test_dotdot_in_write_path_denied(self):
        from fastapi.testclient import TestClient
        import main as _m
        client = TestClient(_m.app)
        resp = client.post("/api/files/write",
                            json={"path": "../escape.txt", "content": "malicious"})
        self.assertIn(resp.status_code, (401, 403, 400))


class TestOutputRoutesRequireAuth(TestCase):
    """Regression: /api/files/upload, /api/preview, /api/outputs and friends
    must require auth when USB_API_KEY is set.

    Phase D closed five unprotected output routes that previously leaked
    session-generated artifacts to anyone on the host.
    """
    def test_routes_reject_without_key(self):
        from fastapi.testclient import TestClient
        import main as _m
        old = os.environ.pop("USB_API_KEY", None)
        os.environ["USB_API_KEY"] = "secret-key"
        try:
            _m.app.state.rate_limiter.reset()
            client = TestClient(_m.app)
            # No Bearer token → 401.
            for method, path in [("get",  "/api/outputs"),
                                  ("get",  "/api/outputs/download/anything.txt"),
                                  ("get",  "/api/preview/anything.html"),
                                  ("post", "/api/files/upload"),
                                  ("post", "/api/calc"),
                                  ("post", "/api/chat/stream"),
                                  ("post", "/api/files/read")]:
                resp = client.request(method, path,
                                       json={"path": "x", "content": "x",
                                             "expression": "1+1",
                                             "session_id": "x", "message": "x"})
                self.assertEqual(resp.status_code, 401,
                    f"{method.upper()} {path} should 401 without key, got {resp.status_code}")
        finally:
            if old is not None:
                os.environ["USB_API_KEY"] = old


class TestExportEscapes(TestCase):
    """export_html escapes BOTH message content AND the session title.

    The title is user-controllable (first chat message / RenameRequest) and is
    interpolated into <title> + <h1> — an unescaped <script> there was a real
    stored-XSS sink in the exported file.
    """
    def test_title_and_content_escaped(self):
        from tools.export_tool import ExportTool
        evil = "<script>alert('xss')</script>"
        with tempfile.TemporaryDirectory() as d:
            tool = ExportTool(Path(d))
            r = tool.export_html({"id": "x", "title": evil, "created": 0,
                                  "messages": [{"role": "user", "content": evil}]})
            self.assertEqual(r["status"], "ok")
            out = Path(r["path"]).read_text(encoding="utf-8")
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)

    def test_markdown_export_writes_file(self):
        from tools.export_tool import ExportTool
        with tempfile.TemporaryDirectory() as d:
            tool = ExportTool(Path(d))
            r = tool.export_markdown({"id": "y", "title": "T & Co", "created": 0,
                                      "messages": [{"role": "assistant",
                                                    "content": "hi <b>there</b>"}]})
            self.assertEqual(r["status"], "ok")
            self.assertTrue(Path(r["path"]).exists())


class TestAuthMatrix(TestCase):
    """Every always-mounted /api/* route enforces the SAME policy — the
    middleware and require_api_key cannot drift again:

      - key unset -> route reachable past auth (200 business-ok or 422
        validation; never 401/503). Intentional exception: /api/files/write
        and /api/files/debug fail-closed via _SENSITIVE_PATH_PREFIXES.
      - key set   -> no bearer header = exactly 401 on every route.
    """

    # Routes chosen to be side-effect-free on empty bodies: {} triggers FastAPI
    # 422 validation before any handler logic runs.
    SAFE_ROUTES = [
        ("get",    "/api/status"),
        ("get",    "/api/models"),
        ("get",    "/api/models/progress"),
        ("get",    "/api/outputs"),
        ("get",    "/api/files/browse"),
        ("get",    "/api/sessions/nonexistent-sid"),
        ("delete", "/api/sessions/nonexistent-sid"),
        ("post",   "/api/sessions/new"),
        ("post",   "/api/chat/stream"),
        ("post",   "/api/files/read"),
        ("post",   "/api/diff/files"),
        ("post",   "/api/pdf/extract"),
        ("post",   "/api/ppt/generate"),
        ("post",   "/api/calc"),
        ("post",   "/api/export"),
        ("post",   "/api/code/save"),
        ("post",   "/api/v1/chat/completions"),
        ("get",    "/api/v1/models"),
    ]

    def _client(self):
        from fastapi.testclient import TestClient
        import main as _m
        _m.app.state.rate_limiter.reset()
        return TestClient(_m.app)

    def test_unset_key_reachable_everywhere(self):
        from fastapi.testclient import TestClient
        import main as _m
        old = os.environ.pop("USB_API_KEY", None)
        try:
            c = self._client()
            for method, path in self.SAFE_ROUTES:
                r = c.request(method, path, json={})
                self.assertNotEqual(r.status_code, 401, f"{method.upper()} {path}")
                self.assertNotEqual(r.status_code, 503,
                                    f"{method.upper()} {path} -> {r.status_code} "
                                    f"(dual-auth regression: dep must not 503 when key unset)")
                self.assertNotEqual(r.status_code, 429, f"{method.upper()} {path}")
            # Documented exception: filesystem-mutating prefixes stay fail-closed.
            for path in ("/api/files/write", "/api/files/debug"):
                r = c.post(path, json={})
                self.assertEqual(r.status_code, 401, f"POST {path} should stay fail-closed")
        finally:
            if old is not None:
                os.environ["USB_API_KEY"] = old

    def test_set_key_requires_bearer_on_every_route(self):
        from fastapi.testclient import TestClient
        import main as _m
        old = os.environ.pop("USB_API_KEY", None)
        os.environ["USB_API_KEY"] = "matrix-key"
        try:
            c = self._client()
            for method, path in self.SAFE_ROUTES + [
                    ("post", "/api/files/write"), ("post", "/api/files/debug")]:
                r = c.request(method, path, json={})
                self.assertEqual(r.status_code, 401,
                                 f"{method.upper()} {path} -> {r.status_code} "
                                 f"(expected 401 without bearer)")
        finally:
            if old is not None:
                os.environ["USB_API_KEY"] = old

    def test_gated_routes_mount_policy_matches_env(self):
        import main as _m
        from routers.code import is_code_run_enabled
        from routers.agent import is_agent_enabled
        mounted = {getattr(r, "path", "") for r in _m.app.routes}
        self.assertEqual("/api/code/run" in mounted, is_code_run_enabled())
        self.assertEqual("/api/agent/execute" in mounted, is_agent_enabled())


class TestSessionIndex(TestCase):
    """SessionStore.save() writes the index; list_index() returns sorted index records."""
    def test_index_round_trip(self):
        from sessions import SessionStore  # noqa: F401
        with tempfile.TemporaryDirectory() as h:
            store = SessionStore.default(Path(h))
            store.save({"id": "a", "title": "A", "messages": [{"role":"user","content":"hi"}]})
            store.save({"id": "b", "title": "B", "messages": []})
            idx_path = store.index_path
            self.assertTrue(idx_path.exists())
            items = json.loads(idx_path.read_text(encoding="utf-8"))
            self.assertEqual({i["id"] for i in items}, {"a", "b"})
            # most-recently-updated first by the sort key
            self.assertEqual(items[0]["title"], "B")


if __name__ == "__main__":
    # ponytail: runner was missing — `python tests/test_security.py` used to
    # define all classes, run zero tests, and exit 0. AGENTS.md documents this
    # command as the way to run the suite.
    main(verbosity=2)
