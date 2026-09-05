"""Tests for SessionStore."""
import sys, os, json, tempfile, time
from pathlib import Path
from unittest import TestCase, main

_APP = Path(__file__).resolve().parent.parent / "app"
sys.path.insert(0, str(_APP))

from sessions import SessionStore


class TestSessionStore(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SessionStore.default(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_and_load(self):
        data = {"id": "s1", "title": "Test Chat", "messages": [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]}
        self.store.save(data)
        loaded = self.store.load("s1")
        self.assertEqual(loaded["title"], "Test Chat")
        self.assertEqual(len(loaded["messages"]), 2)

    def test_load_missing_returns_default(self):
        result = self.store.load("nonexistent")
        self.assertEqual(result["id"], "nonexistent")
        self.assertEqual(result["title"], "New Chat")
        self.assertEqual(result["messages"], [])

    def test_delete(self):
        self.store.save({"id": "del_test", "title": "X", "messages": []})
        self.assertTrue(Path(self.store._path("del_test")).exists())
        self.assertTrue(self.store.delete("del_test"))
        self.assertFalse(Path(self.store._path("del_test")).exists())

    def test_delete_nonexistent(self):
        self.assertFalse(self.store.delete("not_there"))

    def test_list_index_empty(self):
        result = self.store.list_index()
        self.assertEqual(result, [])

    def test_list_index_sorted_by_updated(self):
        self.store.save({"id": "a", "title": "A", "messages": []})
        time.sleep(0.01)
        self.store.save({"id": "b", "title": "B", "messages": []})
        result = self.store.list_index()
        self.assertEqual(len(result), 2)
        # Most recently updated first
        self.assertEqual(result[0]["id"], "b")
        self.assertEqual(result[1]["id"], "a")

    def test_list_index_respects_limit(self):
        for i in range(10):
            self.store.save({"id": f"s{i}", "title": f"Session {i}", "messages": []})
        result = self.store.list_index(limit=3)
        self.assertEqual(len(result), 3)

    def test_search_text_finds_match(self):
        self.store.save({"id": "search1", "title": "Search Test", "messages": [
            {"role": "user", "content": "hello world"},
        ]})
        results = self.store.search_text("hello")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "search1")

    def test_search_text_case_insensitive(self):
        self.store.save({"id": "case1", "title": "X", "messages": [
            {"role": "user", "content": "HeLLo World"},
        ]})
        results = self.store.search_text("hello")
        self.assertEqual(len(results), 1)

    def test_search_text_no_match(self):
        results = self.store.search_text("xyzabc123")
        self.assertEqual(results, [])

    def test_search_text_returns_excerpt(self):
        self.store.save({"id": "excerpt1", "title": "X", "messages": [
            {"role": "user", "content": "a" * 200},
        ]})
        results = self.store.search_text("a" * 5)
        self.assertEqual(len(results), 1)
        # Excerpt should be max 120 chars
        self.assertLessEqual(len(results[0]["matches"][0]["excerpt"]), 120)

    def test_index_cache_bust_on_save(self):
        self.store.save({"id": "c1", "title": "C1", "messages": []})
        _ = self.store.list_index()  # populate cache
        self.store.save({"id": "c2", "title": "C2", "messages": []})
        result = self.store.list_index()
        self.assertEqual(len(result), 2)  # cache was invalidated

    def test_search_cache_by_mtime(self):
        """Cached parsed session reused when file not modified."""
        self.store.save({"id": "cache1", "title": "X", "messages": [
            {"role": "user", "content": "hello"}
        ]})
        # First search — populates cache
        r1 = self.store.search_text("hello")
        # Modify file externally (touch mtime)
        time.sleep(0.1)
        p = self.store._path("cache1")
        p.write_text(json.dumps({
            "id": "cache1", "title": "X", "updated": time.time(),
            "messages": [{"role": "user", "content": "hello modified"}]
        }, ensure_ascii=False))
        # Second search should re-parse (mtime changed)
        r2 = self.store.search_text("hello")
        # Should find the modified content
        self.assertEqual(len(r2), 1)


class TestSessionIdTraversal(TestCase):
    """Regression (audit 2026-09-05): sid reached Path() unvalidated.

    An absolute sid REPLACES the history base; ../ escapes via the .json
    suffix; on Windows %5C survives Starlette path matching. _path() must
    refuse all three BEFORE building the path.
    """
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SessionStore.default(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_path_rejects_absolute_sid(self):
        with self.assertRaises(ValueError):
            self.store._path("C:/Users/x/anything")

    def test_path_rejects_dotdot_sid(self):
        with self.assertRaises(ValueError):
            self.store._path("../escape")

    def test_path_rejects_backslash_sid(self):
        with self.assertRaises(ValueError):
            self.store._path("..\\..\\escape")

    def test_load_cannot_read_outside_history(self):
        secret = Path(self.tmp.name).parent / (self.tmp.name + "_secret.json")
        self.addCleanup(lambda: secret.unlink(missing_ok=True))
        secret.write_text('{"secret": "pwned"}')
        for sid in (".." + self.tmp.name.split("/")[-1] + "_secret",):
            with self.assertRaises(ValueError):
                self.store.load(sid)

    def test_delete_cannot_unlink_outside_history(self):
        secret = Path(self.tmp.name).parent / (self.tmp.name + "_victim.json")
        self.addCleanup(lambda: secret.unlink(missing_ok=True))
        secret.write_text('{"secret": "pwned"}')
        sid = ".." + self.tmp.name.split("/")[-1] + "_victim"
        with self.assertRaises(ValueError):
            self.store.delete(sid)
        self.assertTrue(secret.exists(), "victim must survive a traversal delete attempt")

    def test_valid_ui_ids_pass(self):
        for sid in ("sess_1784203255562_mclo", "itest-1", "audit-phaseB", "solo1"):
            self.store._path(sid)  # must not raise


if __name__ == "__main__":
    main()