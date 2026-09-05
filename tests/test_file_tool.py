"""Tests for FileTool and path security."""
import sys, os, time, uuid
from pathlib import Path
from unittest import TestCase, main

_APP = Path(__file__).resolve().parent.parent / "app"
sys.path.insert(0, str(_APP))

from tools.file_tool import (
    FileTool, _resolve, SAFE_TEXT_EXTENSIONS,
    SAFE_WRITE_EXTENSIONS, ALLOWED_BASE_DIRS,
)


class TestSafeExtensions(TestCase):
    def test_read_allows_html_js(self):
        for ext in [".html", ".js", ".jsx", ".tsx", ".svelte", ".vue", ".react"]:
            self.assertIn(ext, SAFE_TEXT_EXTENSIONS, f"{ext} should be readable")

    def test_write_blocks_html_js(self):
        for ext in [".html", ".js", ".jsx", ".tsx", ".svelte", ".vue", ".react"]:
            self.assertNotIn(ext, SAFE_WRITE_EXTENSIONS, f"{ext} should NOT be writable")

    def test_write_blocks_shell_scripts(self):
        for ext in [".ps1", ".bat", ".sh", ".rb", ".php"]:
            self.assertNotIn(ext, SAFE_WRITE_EXTENSIONS, f"{ext} should NOT be writable")

    def test_write_allows_safe_types(self):
        for ext in [".py", ".ts", ".c", ".rs", ".go", ".java", ".json", ".md"]:
            self.assertIn(ext, SAFE_WRITE_EXTENSIONS, f"{ext} should be writable")


class TestResolve(TestCase):
    def test_null_byte_rejected(self):
        with self.assertRaises(ValueError):
            _resolve("/path/to/file\0.txt")

    def test_absolute_outside_allowed_denied(self):
        with self.assertRaises(ValueError):
            _resolve("C:/Windows/System32/config")

    def test_relative_dotdot_denied(self):
        with self.assertRaises(ValueError):
            _resolve("../../etc/passwd")

    def test_resolve_to_allowed_dir(self):
        out_dir = ALLOWED_BASE_DIRS[0]
        out_dir.mkdir(parents=True, exist_ok=True)
        uid = uuid.uuid4().hex[:12]
        candidate = out_dir / f"test_{uid}.py"
        result = _resolve(str(candidate))
        self.assertEqual(result, candidate.resolve())


class TestFileTool(TestCase):
    def setUp(self):
        self.tool = FileTool()
        self.tmp_dir = ALLOWED_BASE_DIRS[0]
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

    def _p(self, prefix, ext="txt"):
        return str(self.tmp_dir / f"{prefix}_{uuid.uuid4().hex[:12]}.{ext}")

    def test_write_and_read_roundtrip(self):
        path = self._p("hello")
        r = self.tool.write_file(path, "Hello, World!")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["action"], "created")
        r = self.tool.read_file(path)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["content"], "Hello, World!")

    def test_read_file_accepts_web_types(self):
        # ponytail: behavior, not membership — read_file used to validate
        # against SAFE_WRITE_EXTENSIONS while TestSafeExtensions only
        # checked the set, so broken .html reads passed CI (audit 2026-09-05).
        for ext in ("html", "js", "jsx", "tsx", "vue"):
            path = self._p("webfile", ext)
            Path(path).write_text(f"<p>content {ext}</p>")
            r = self.tool.read_file(path)
            self.assertEqual(r["status"], "ok",
                             f"read_file rejected readable type {ext}: {r.get('message', '')}")
            self.assertIn("content", r["content"])

    def test_write_blocks_html(self):
        path = self._p("evil", "html")
        r = self.tool.write_file(path, "<script>alert('xss')</script>")
        self.assertEqual(r["status"], "error")
        self.assertIn("Unsupported type", r["message"])

    def test_write_blocks_ps1(self):
        path = self._p("evil", "ps1")
        r = self.tool.write_file(path, "Remove-Item -Recurse C:\\")
        self.assertEqual(r["status"], "error")
        self.assertIn("Unsupported type", r["message"])

    def test_read_unknown_extension_blocked(self):
        path = self._p("file", "exe")
        Path(path).write_text("binary content")
        r = self.tool.read_file(path)
        self.assertEqual(r["status"], "error")
        self.assertIn("Unsupported type", r["message"])

    def test_write_new_file(self):
        path = self._p("brand_new")
        r = self.tool.write_file(path, "brand new")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["action"], "created")

    def test_write_updates_existing(self):
        path = self._p("update")
        Path(path).write_text("original")
        r = self.tool.write_file(path, "updated")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["action"], "updated")

    def test_list_directory(self):
        uid = uuid.uuid4().hex[:12]
        subdir = self.tmp_dir / f"subdir_{uid}"
        subdir.mkdir()
        (self.tmp_dir / f"a_{uid}.txt").write_text("a")
        (self.tmp_dir / f"b_{uid}.py").write_text("b")
        r = self.tool.list_directory(str(self.tmp_dir))
        self.assertEqual(r["status"], "ok")
        names = {e["name"] for e in r["entries"]}
        self.assertIn(f"a_{uid}.txt", names)
        self.assertIn(f"b_{uid}.py", names)
        self.assertIn(f"subdir_{uid}", names)

    def test_list_directory_nonexistent(self):
        uid = uuid.uuid4().hex[:12]
        r = self.tool.list_directory(str(self.tmp_dir / f"nonexistent_{uid}"))
        self.assertEqual(r["status"], "error")

    def test_drives_list(self):
        r = self.tool.list_directory("__drives__")
        self.assertEqual(r["status"], "ok")
        self.assertGreater(len(r["entries"]), 0)


if __name__ == "__main__":
    main()
