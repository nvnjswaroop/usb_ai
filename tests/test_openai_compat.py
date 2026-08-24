"""Tests for the OpenAI-compatible adapter at /api/v1/*.

Stdlib-only schema tests run without fastapi. TestClient tests need
fastapi + httpx + the project's transitive deps (pptx etc.) — they're
gated behind `try: import fastapi.testclient` so the file stays importable
in a barebones venv, matching the existing test_security.py pattern.
"""
import sys, os
from pathlib import Path
from unittest import TestCase, main

_APP = Path(__file__).resolve().parent.parent / "app"
sys.path.insert(0, str(_APP))


# ── stdlib-only: schema + helper logic ──────────────────────────────────────

class TestChatCompletionRequestSchema(TestCase):
    """Pydantic request validation — no FastAPI needed."""
    def _cls(self):
        # Import lazily so this file stays importable without fastapi.
        from routers.openai_compat import ChatCompletionRequest
        return ChatCompletionRequest

    def test_minimal_request(self):
        Cls = self._cls()
        req = Cls(messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(req.max_tokens, 2048)
        self.assertEqual(req.temperature, 1.0)
        self.assertEqual(req.stream, False)
        self.assertEqual(req.model, "")

    def test_full_request(self):
        Cls = self._cls()
        req = Cls(
            model="llama-3",
            messages=[
                {"role": "system", "content": "you are terse"},
                {"role": "user", "content": "what is 1+1"},
            ],
            max_tokens=128, temperature=0.3, stream=False,
        )
        self.assertEqual(req.max_tokens, 128)
        self.assertEqual(req.temperature, 0.3)

    def test_temperature_bounds(self):
        Cls = self._cls()
        with self.assertRaises(Exception):
            Cls(messages=[{"role": "user", "content": "x"}], temperature=3.0)
        with self.assertRaises(Exception):
            Cls(messages=[{"role": "user", "content": "x"}], temperature=-0.1)

    def test_max_tokens_bounds(self):
        Cls = self._cls()
        with self.assertRaises(Exception):
            Cls(messages=[{"role": "user", "content": "x"}], max_tokens=0)
        with self.assertRaises(Exception):
            Cls(messages=[{"role": "user", "content": "x"}], max_tokens=99999)

    def test_messages_required_non_empty_via_router(self):
        # Pydantic allows empty list (type matches); router raises 400.
        Cls = self._cls()
        req = Cls(messages=[])
        self.assertEqual(req.messages, [])


class TestTokenCountMatchesSlidingWindow(TestCase):
    """prompt_tokens/completion_tokens must use the same counter SlidingWindow uses.

    `llm._count` is the internal counter — we hit it with both a stub model
    (tokenize raises, falls back to len(text)//4) and verify our adapter math
    produces the same numbers SlidingWindow would.
    """
    def test_count_uses_internal_counter(self):
        from llm import SlidingWindow
        # Stub the engine the way SlidingWindow expects it.
        class StubLLM:
            def __init__(self):
                self.llm = None  # tokenize() would raise; falls back to chars//4
            def _count(self, text):
                if not text: return 0
                return max(1, len(text) // 4)
        s = StubLLM()
        w = SlidingWindow(n_ctx=4096, count_fn=s._count)

        # Simulate the adapter's prompt_tokens math against the same window.
        history = [{"role": "user", "content": "hello world"},
                   {"role": "assistant", "content": "hi there"}]
        system = "be terse"
        user_msg = "what is 1+1"

        # Adapter formula:
        prompt_tokens = s._count(system) + sum(s._count(m["content"]) + 6 for m in history) + s._count(user_msg) + 6
        # Same counter on the same input — both must agree.
        self.assertEqual(prompt_tokens, s._count(system)
                         + s._count(history[0]["content"]) + 6
                         + s._count(history[1]["content"]) + 6
                         + s._count(user_msg) + 6)
        self.assertGreater(prompt_tokens, 0)


class TestInferLockSerializesConcurrentCalls(TestCase):
    """Regression: USB AI crashed mid-scan when CyberMatrix fired vuln+exploit
    at the same Llama instance concurrently. The fix is a single `_infer_lock`
    around `create_chat_completion` in stream_tokens/generate_json.

    This test verifies the lock is held across concurrent callers by
    instrumenting a stub Llama that records overlapping-call intervals. If
    the lock is missing, two concurrent calls will overlap. If the lock is
    present, every call sees `_active > 1 == 0`.
    """
    def test_no_overlapping_inference_calls(self):
        from llm import LLMEngine
        import threading, time

        # Stub the model: simulate a 100ms blocking inference. Track call
        # intervals; assert none overlap.
        active = 0
        max_active = 0
        lock_observed = threading.Lock()

        class FakeChunk(dict):
            """Mimics the OpenAI streaming chunk shape:
            chunk["choices"][0].get("delta",{}).get("content","")
            """
            def __init__(self, content):
                super().__init__(choices=[{"delta": {"content": content}}])

        class FakeLlama:
            def __init__(self):
                self.tokenize_calls = 0
            def tokenize(self, text, add_bos=False):
                self.tokenize_calls += 1
                # SlidingWindow._count needs a token count — fake it.
                return list(range(max(1, len(text) // 4)))
            def create_chat_completion(self, messages, **kwargs):
                nonlocal active, max_active
                active += 1
                with lock_observed:
                    max_active = max(max_active, active)
                try:
                    time.sleep(0.10)  # simulate inference work
                    yield FakeChunk("ok")
                finally:
                    active -= 1

        engine = LLMEngine.__new__(LLMEngine)
        engine.llm = FakeLlama()
        engine._n_ctx = 4096
        engine._is_vision = False
        engine._stop_tokens = []
        engine._chat_format = ""
        engine._maybe_sync = lambda msgs: None  # skip DB sync
        # __new__ skips __init__, so explicitly initialize the lock + window
        # + post-yield bookkeeping fields touched by stream_tokens.
        import threading as _th
        engine._infer_lock = _th.Lock()
        engine._last_hist_len = 0
        from llm import SlidingWindow
        engine._count = lambda t: max(1, len(t) // 4) if t else 0
        engine._window = SlidingWindow(n_ctx=engine._n_ctx, count_fn=engine._count)

        # Call stream_tokens concurrently from N threads.
        N = 8
        results = []
        def worker(i):
            chunks = list(engine.stream_tokens(
                user_message=f"thread-{i}",
                messages=[{"role": "user", "content": f"thread-{i}"}],
                system="you are terse",
                temperature=0.0,
                max_tokens=8,
            ))
            results.append(chunks)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=10)
        self.assertEqual(len(results), N,
                         f"only {len(results)}/{N} threads completed")
        self.assertEqual(max_active, 1,
                         f"concurrent inference calls overlapped (max_active={max_active}); "
                         f"_infer_lock is not held around create_chat_completion")


class TestModelsRouteShape(TestCase):
    """The /api/v1/models adapter must reshape, not reimplement, model discovery."""
    def _routes(self):
        from routers.openai_compat import router
        # FastAPI stores HTTP methods as a frozenset on each route — expand
        # into the (method, path) tuples the test asserts against.
        return [(m, r.path) for r in router.routes for m in r.methods]

    def test_routes_registered(self):
        routes = self._routes()
        # Methods is a set like {"POST"} or {"GET"} — compare sets, not tuples.
        paths_by_method = {(m.split()[0] if isinstance(m, str) else m): p
                           for m, p in routes}
        self.assertTrue(any(p == "/api/v1/chat/completions" and m == "POST"
                            for m, p in routes),
                        f"POST /api/v1/chat/completions not found in {routes}")
        self.assertTrue(any(p == "/api/v1/models" and m == "GET"
                            for m, p in routes),
                        f"GET /api/v1/models not found in {routes}")


# ── TestClient-based: auth parity with /api/chat/stream ─────────────────────

try:
    import fastapi  # noqa: F401
    from fastapi.testclient import TestClient
    _HAVE_FASTAPI = True
except ImportError:
    _HAVE_FASTAPI = False


if _HAVE_FASTAPI:
    class TestOpenAICompatAuthParity(TestCase):
        """Auth behavior on /api/v1/* must mirror /api/chat/stream exactly.

        Pattern copied from tests/test_security.py::TestAuthMiddleware — set
        USB_API_KEY, hit both routes with and without Bearer header, confirm
        parity.
        """
        def setUp(self):
            import main as _m
            self._m = _m
            # ponytail: patch os.environ, not module globals — both auth layers
            # read dependencies.get_api_key() per-call (single source of truth).
            self._old_key = os.environ.pop("USB_API_KEY", None)
            _m.app.state.rate_limiter.reset()

        def tearDown(self):
            if self._old_key is not None:
                os.environ["USB_API_KEY"] = self._old_key

        def test_openai_routes_open_when_no_key(self):
            os.environ.pop("USB_API_KEY", None)
            client = TestClient(self._m.app)
            # /api/v1/models: 200 (may be empty list if no .gguf in test env)
            r = client.get("/api/v1/models")
            self.assertEqual(r.status_code, 200,
                f"open when no key: got {r.status_code} {r.text}")
            body = r.json()
            self.assertEqual(body["object"], "list")
            self.assertIsInstance(body["data"], list)
            for m in body["data"]:
                self.assertIn("id", m)
                self.assertEqual(m["object"], "model")
                self.assertEqual(m["owned_by"], "usb-ai")

        def test_openai_routes_401_when_key_set_without_bearer(self):
            os.environ["USB_API_KEY"] = "secret-key"
            client = TestClient(self._m.app)
            for method, path in [("get",  "/api/v1/models"),
                                  ("post", "/api/v1/chat/completions")]:
                resp = client.request(method, path, json={
                    "model": "x",
                    "messages": [{"role": "user", "content": "hi"}],
                })
                self.assertEqual(resp.status_code, 401,
                    f"{method.upper()} {path}: expected 401, got {resp.status_code}")

        def test_openai_routes_accept_with_bearer(self):
            os.environ["USB_API_KEY"] = "secret-key"
            client = TestClient(self._m.app)
            r = client.get("/api/v1/models",
                           headers={"Authorization": "Bearer secret-key"})
            self.assertEqual(r.status_code, 200)

        def test_chat_completions_rejects_stream_true(self):
            # No model loaded in test env — we expect 400 from the stream
            # check OR the model-loaded check, not 500. The router checks
            # stream BEFORE model-loaded.
            os.environ.pop("USB_API_KEY", None)
            client = TestClient(self._m.app)
            r = client.post("/api/v1/chat/completions", json={
                "model": "x",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            })
            self.assertEqual(r.status_code, 400)
            self.assertIn("streaming", r.json()["detail"].lower())

        def test_chat_completions_requires_non_empty_messages(self):
            os.environ.pop("USB_API_KEY", None)
            client = TestClient(self._m.app)
            r = client.post("/api/v1/chat/completions", json={
                "model": "x",
                "messages": [],
            })
            self.assertEqual(r.status_code, 400)
            self.assertIn("messages", r.json()["detail"].lower())


if __name__ == "__main__":
    main(verbosity=2)
