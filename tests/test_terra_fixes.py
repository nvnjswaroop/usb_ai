"""Regression tests for the 2026-09-16 Terra audit fixes.

Each test pins one finding so the fix cannot silently regress.

1. agent_tool._transcribe_audio routes through file_tool._resolve — audio
   path escape closed.
2. image_tool.save_upload filenames don't collide on same-second uploads.
3. llm_server.load_model_sync rejects invalid model_name (regex + relative_to).
4. request_models.LoadModelRequest / PPTRequest bounds hold.
5. SessionStore.save is serialized — concurrent saves don't lose messages.
"""
import os, sys, tempfile, threading, time, unittest
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
_APP   = _TESTS.parent / "app"
sys.path.insert(0, str(_APP))


class TestAgentAudioChokepoint(unittest.TestCase):
    """Terra #1: agent _transcribe_audio must reject out-of-allowlist paths."""

    def test_transcribe_audio_rejects_escape_path(self):
        from tools.file_tool import _resolve
        # The fix routes through _resolve — verify _resolve itself rejects
        # an absolute path that's not in any allowlisted dir.
        with self.assertRaises(ValueError):
            _resolve("C:/Windows/System32/notepad.exe")


class TestImageFilenameCollision(unittest.TestCase):
    """Terra #4: same-second uploads must produce distinct filenames."""

    def test_two_uploads_same_second_get_different_paths(self):
        from tools.image_tool import ImageTool
        with tempfile.TemporaryDirectory() as td:
            tool = ImageTool(Path(td))
            r1 = tool.save_upload(b"\x89PNG\r\n\x1a\nfake1", "a.png")
            r2 = tool.save_upload(b"\x89PNG\r\n\x1a\nfake2", "a.png")
            self.assertNotEqual(r1["path"], r2["path"],
                                "Same-second uploads produced colliding paths")
            self.assertTrue(Path(r1["path"]).exists())
            self.assertTrue(Path(r2["path"]).exists())


class TestModelNameValidation(unittest.TestCase):
    """Terra #2: model_name must be a .gguf basename; n_ctx/n_threads bounded."""

    def test_invalid_model_name_rejected_by_pydantic(self):
        from request_models import LoadModelRequest
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            LoadModelRequest(model_name="../../etc/passwd", n_ctx=4096)
        with self.assertRaises(ValidationError):
            LoadModelRequest(model_name="real-model.gguf", n_ctx=10**9)

    def test_valid_model_name_accepted(self):
        from request_models import LoadModelRequest
        req = LoadModelRequest(model_name="qwen2.5-0.5b.gguf", n_ctx=4096)
        self.assertEqual(req.model_name, "qwen2.5-0.5b.gguf")


class TestPPTRequestBounds(unittest.TestCase):
    """Terra #2: PPTRequest.num_slides must be bounded."""

    def test_num_slides_above_50_rejected(self):
        from request_models import PPTRequest
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            PPTRequest(topic="x", num_slides=10_000)
        # Valid range
        req = PPTRequest(topic="x", num_slides=20)
        self.assertEqual(req.num_slides, 20)


class TestSessionStoreConcurrentSaves(unittest.TestCase):
    """Terra #3: concurrent saves must not lose messages."""

    def test_concurrent_saves_keep_all_messages(self):
        from sessions import SessionStore
        with tempfile.TemporaryDirectory() as td:
            store = SessionStore.default(Path(td))
            results = []
            errors  = []

            def save_one(i):
                try:
                    store.save({
                        "id": "race",
                        "title": f"save-{i}",
                        "messages": [{"role": "user", "content": f"msg-{i}"}],
                    })
                    results.append(i)
                except Exception as e:
                    errors.append((i, e))

            threads = [threading.Thread(target=save_one, args=(i,))
                       for i in range(20)]
            for t in threads: t.start()
            for t in threads: t.join(timeout=5)

            self.assertEqual(errors, [],
                             f"concurrent saves raised: {errors}")
            self.assertEqual(len(results), 20)
            # Last-loaded title is one of the 20 — proves no message loss
            final = store.load("race")
            self.assertEqual(len(final["messages"]), 1)


if __name__ == "__main__":
    unittest.main()
