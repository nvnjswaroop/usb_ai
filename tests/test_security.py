"""New tests for security, streaming extractor, agent parser, schemas.

stdlib-only — runs without fastapi/pptx/etc. The auth-gate tests
live alongside the running app (out of scope here).
"""
import sys, os, types, json
from pathlib import Path
from unittest import TestCase, main

# Ensure app/ is importable
_APP = Path(__file__).resolve().parent.parent / "app"
sys.path.insert(0, str(_APP))


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
    """_StreamingArtifactExtractor emits artifacts as code blocks close."""
    def _cls(self):
        main_path = Path(__file__).resolve().parent.parent / "app" / "main.py"
        src = main_path.read_text(encoding="utf-8")
        start = src.index("class _StreamingArtifactExtractor")
        end   = src.index("def _code_blocks", start)
        cls_src = src[start:end]
        from schemas import Artifact
        ns = {"__name__": "_anon", "re": __import__("re"),
              "Artifact": Artifact, "time": __import__("time"),
              "_CODE_LANGS": {"python": "py", "py": "py", "js": "js", "txt": "txt"}}
        exec(cls_src, ns)
        return ns["_StreamingArtifactExtractor"]

    def test_single_block(self):
        cl = self._cls()
        e = cl(); e.push("```python\nprint('x')\n```")
        self.assertGreaterEqual(len(e.flush()), 1)

    def test_orphan_block_flushes(self):
        cl = self._cls()
        e = cl(); e.push("```py\nopen code\n")
        # Orphan block is finalized on flush()
        self.assertEqual(len(e.flush()), 1)

    def test_multiple_blocks(self):
        cl = self._cls()
        e = cl(); e.push("```py\nA\n```\ntext\n```js\nB\n```")
        self.assertGreaterEqual(len(e.flush()), 2)


class TestAgentParse(TestCase):
    """AgentTool._parse_tool_call: tolerant JSON extraction."""
    def _fn(self):
        # Avoid importing AgentTool (pulls in main.py deps). Source-extract the method body.
        src = (Path(__file__).resolve().parent.parent / "app" / "tools" / "agent_tool.py").read_text(encoding="utf-8")
        start = src.index("def _parse_tool_call")
        next_def = src.index("\n    def ", start + 1)
        method_src = src[start:next_def]
        # Strip the bound `self` so exec produces a plain function in ns{}.
        method_src = method_src.replace("def _parse_tool_call(self, text:", "def _parse_tool_call(text:", 1)
        from schemas import ToolCall
        ns = {"json": __import__("json"), "Optional": __import__("typing").Optional,
              "ToolCall": ToolCall}
        exec(method_src, ns)
        return ns["_parse_tool_call"]

    def test_valid(self):
        fn = self._fn()
        r = fn('{"tool":"finish","params":{"result":"ok"},"thought":"done"}')
        self.assertIsNotNone(r)
        self.assertEqual(r.tool, "finish")

    def test_prose_wrapped(self):
        fn = self._fn()
        r = fn('Here you go: {"tool":"run_python","params":{"code":"1+1"},"thought":"compute"} cheers')
        self.assertIsNotNone(r)
        self.assertEqual(r.tool, "run_python")

    def test_malformed(self):
        fn = self._fn()
        self.assertIsNone(fn('nope'))

    def test_no_tool_key(self):
        fn = self._fn()
        self.assertIsNone(fn('{"params":{"x":1}}'))

    def test_action_key_fallback(self):
        fn = self._fn()
        r = fn('{"action":"finish","result":"ok"}')
        self.assertIsNotNone(r)
        self.assertEqual(r.tool, "finish")


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
    """Verify check_auth uses secrets.compare_digest (no plaintext ==)."""
    def test_source_uses_compare_digest(self):
        auth_gate_src = (Path(__file__).resolve().parent.parent /
                         "app" / "main.py").read_text(encoding="utf-8")
        auth_block = auth_gate_src[auth_gate_src.index("@app.middleware"):
                                   auth_gate_src.index("@app.middleware") + 800]
        self.assertIn("compare_digest", auth_block)
        self.assertNotIn('"Authorization\") != \"Bearer', auth_block)


if __name__ == "__main__":
    main(verbosity=2)
