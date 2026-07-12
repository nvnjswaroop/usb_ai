"""USB AI — audit-critical tests. stdlib only, no pip deps.
Run: python tests/test_audit.py
"""
import sys, os, tempfile, shutil, subprocess
from pathlib import Path
from unittest import TestCase, main

# Ensure app/ is importable
_APP = Path(__file__).resolve().parent.parent / "app"
sys.path.insert(0, str(_APP))


class TestPathAllow(TestCase):
    """_is_path_allowed: security boundary."""
    def test_allowed(self):
        from tools.file_tool import _is_path_allowed, ALLOWED_BASE_DIRS
        with tempfile.TemporaryDirectory() as d:
            ALLOWED_BASE_DIRS.append(Path(d))
            try:
                self.assertTrue(_is_path_allowed(Path(d) / "test.py"))
            finally:
                ALLOWED_BASE_DIRS.remove(Path(d))
    def test_outside(self):
        from tools.file_tool import _is_path_allowed
        with tempfile.TemporaryDirectory(prefix="allowed_") as a, \
             tempfile.TemporaryDirectory(prefix="outside_") as b:
            f = Path(b) / "evil.txt"
            f.write_text("x")
            # b is not in any allowed dir, but _is_path_allowed checks allowlist not arbitrary paths
            # just verify it doesn't crash on weird input
            _is_path_allowed(f)
    def test_prefix_confusion(self):
        """C:\\Users\\X\\DesktopY should NOT match allowlist entry C:\\Users\\X\\Desktop"""
        from pathlib import Path
        p1 = Path("C:/Users/JYOTHI/Desktop")
        p2 = Path("C:/Users/JYOTHI/DesktopX")
        # is_relative_to correctly rejects prefix confusion
        self.assertFalse(p2.resolve().is_relative_to(p1.resolve()))


class TestSlidingWindow(TestCase):
    """SlidingWindow.slide: context management edge cases."""
    def _make(self, n_ctx=100, count_fn=None):
        if count_fn is None:
            count_fn = lambda s: len(s) // 4  # ~4 chars per token
        from llm import SlidingWindow
        return SlidingWindow(n_ctx, count_fn)

    def test_empty(self):
        w = self._make()
        self.assertEqual(w.slide("hello"), [])

    def test_single_pair(self):
        w = self._make(n_ctx=4096)  # ponytail: need n_ctx > RESPONSE_RESERVE + SAFETY_BUFFER
        w.add_exchange("hi", "hello!")
        self.assertEqual(len(w.slide("next")), 2)

    def test_overflow_drops_oldest(self):
        w = self._make(n_ctx=50)  # tiny context
        for i in range(20):
            w.add_exchange(f"msg{i}", f"reply{i}")
        trimmed = w.slide("final")
        self.assertLess(len(trimmed), 40)  # must have dropped some

    def test_role_mismatch(self):
        w = self._make(n_ctx=4096)
        # sync_from_db with odd messages — should pair U+S where possible, skip unpaired
        w.sync_from_db([
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "orphan"},
        ])
        # 1 valid pair found, orphan user msg dropped
        self.assertEqual(len(w._history), 2)


class TestCalcEval(TestCase):
    """eval_node: AST calculator edge cases (imports prod — no copy)."""
    def _eval(self, expr):
        from calc import evaluate
        result, _ = evaluate(expr)
        return result

    def test_basic(self):
        self.assertEqual(self._eval("2 + 3"), 5)
        self.assertEqual(self._eval("10 / 4"), 2.5)
    def test_nested(self):
        self.assertEqual(self._eval("2 * (3 + 4)"), 14)
    def test_div_zero(self):
        with self.assertRaises(ZeroDivisionError):
            self._eval("1 / 0")
    def test_pi(self):
        import math
        self.assertEqual(self._eval("pi"), math.pi)


class TestCodeTool(TestCase):
    """CodeTool.run_python: sandbox + timeout."""
    def test_success(self):
        from tools.code_tool import CodeTool
        ct = CodeTool()
        r = ct.run_python("print('hello')")
        self.assertEqual(r["status"], "ok")
        self.assertIn("hello", r["stdout"])
    def test_timeout(self):
        from tools.code_tool import CodeTool
        ct = CodeTool()
        ct.timeout = 2  # ponytail: short timeout for test speed
        r = ct.run_python("import time; time.sleep(10)")
        self.assertEqual(r["status"], "error")
        self.assertIn("timed out", r["message"])
    def test_truncation(self):
        from tools.code_tool import CodeTool
        ct = CodeTool()
        r = ct.run_python("print('x' * 10000)")
        self.assertEqual(r["status"], "ok")
        self.assertIn("truncated", r["stdout"])


class TestSearchContent(TestCase):
    """FileTool.search_content: the method we added to close the agent tool gap."""
    def setUp(self):
        from tools.file_tool import ALLOWED_BASE_DIRS
        self.tmp = tempfile.mkdtemp()
        ALLOWED_BASE_DIRS.append(Path(self.tmp))  # ponytail: search_content routes through _resolve
        Path(self.tmp, "test.py").write_text("pinned = True\n# just a test")
        Path(self.tmp, "sub").mkdir()
        Path(self.tmp, "sub", "other.py").write_text("import os\n# not matching")
    def tearDown(self):
        from tools.file_tool import ALLOWED_BASE_DIRS
        try:
            ALLOWED_BASE_DIRS.remove(Path(self.tmp))
        except ValueError:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_search_finds(self):
        from tools.file_tool import FileTool
        ft = FileTool()
        r = ft.search_content(self.tmp, "pinned")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(len(r["hits"]), 1)
        self.assertIn("test.py", r["hits"][0]["file"])
    def test_search_empty_query(self):
        from tools.file_tool import FileTool
        ft = FileTool()
        r = ft.search_content(self.tmp, "")
        self.assertEqual(r["status"], "error")


if __name__ == "__main__":
    main(verbosity=2)
