"""Tests for scripts/install.py — pure logic only (no pip, no network).

Run: python tests/test_installer.py
"""
import sys
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts"))
sys.path.insert(0, str(_REPO / "app"))

import install as inst  # noqa: E402


class TestParseRequirements(TestCase):
    def test_splits_feature_blocks(self):
        sets = inst.parse_requirements(_REPO / "requirements.txt")
        self.assertIn("fastapi", " ".join(sets["core"]))
        self.assertIn("httpx", " ".join(sets["core"]))
        # whisper must NOT be core anymore (Phase A split)
        self.assertNotIn("openai-whisper", " ".join(sets["core"]))
        # numpy moved out of core 2026-09-05 — whisper is its only consumer
        self.assertNotIn("numpy", " ".join(sets["core"]))
        self.assertEqual(len(sets.get("voice", [])), 2)
        self.assertIn("openai-whisper", sets["voice"][0])
        self.assertIn("numpy", " ".join(sets["voice"]))
        # Phase C: the inline engine is gone from requirements entirely
        self.assertNotIn("inline-llm", sets)

    def test_parser_handles_nested_free_synthetic(self):
        p = _REPO / "requirements.txt"
        text = ("core-a==1.0\n"
                "# comment\n"
                "# @feature:x:start\nx-pkg==2.0\n# @feature:x:end\n"
                "core-b>=3\n")
        tmp = Path(p.parent / "_synth_req_test.txt")
        tmp.write_text(text, encoding="utf-8")
        try:
            sets = inst.parse_requirements(tmp)
        finally:
            tmp.unlink()
        self.assertEqual(sets["core"], ["core-a==1.0", "core-b>=3"])
        self.assertEqual(sets["x"], ["x-pkg==2.0"])

    def test_inline_comments_stripped_from_packages(self):
        """Regression: 'numpy>=1.20  # why' used to leak '# why' into pip argv."""
        tmp = _REPO / "_synth_inline_test.txt"
        tmp.write_text("pkg-a>=1.0  # trailing reason\n"
                       "# full comment\n"
                       "pkg-b==2.0\t# tab comment\n", encoding="utf-8")
        try:
            sets = inst.parse_requirements(tmp)
        finally:
            tmp.unlink()
        self.assertEqual(sets["core"], ["pkg-a>=1.0", "pkg-b==2.0"])


class TestDetection(TestCase):
    def test_arch_arm64(self):
        with patch.object(inst.platform, "machine", return_value="ARM64"):
            self.assertEqual(inst.detect_arch(), "arm64")

    def test_arch_x64_default(self):
        with patch.object(inst.platform, "machine", return_value="AMD64"):
            self.assertEqual(inst.detect_arch(), "x64")

    def test_gpu_darwin_metal(self):
        with patch.object(inst.sys, "platform", "darwin"), \
             patch.object(inst.shutil, "which", return_value=None):
            variant, reason = inst.detect_gpu_hint()
            self.assertEqual(variant, "metal")
            self.assertTrue(reason)

    def test_gpu_nvidia_via_smi(self):
        with patch.object(inst.sys, "platform", "win32"), \
             patch.object(inst.shutil, "which", return_value=r"C:\tools\nvidia-smi.exe"):
            variant, _ = inst.detect_gpu_hint()
            self.assertEqual(variant, "cuda")

    def test_gpu_fallback_cpu(self):
        with patch.object(inst.sys, "platform", "win32"), \
             patch.object(inst.shutil, "which", return_value=None):
            variant, reason = inst.detect_gpu_hint()
            self.assertEqual(variant, "cpu")
            self.assertTrue(reason)

    def test_arm64_downgrades_cuda_to_vulkan(self):
        with patch.object(inst.sys, "platform", "linux"), \
             patch.object(inst.shutil, "which", return_value="/usr/bin/nvidia-smi"), \
             patch.object(inst.platform, "machine", return_value="aarch64"):
            hw = inst.detect_hardware()
            self.assertEqual(hw["gpu"], "vulkan")


class TestBuildPlan(TestCase):
    SETS = {
        "core": ["fastapi>=0.104,<1.0"],
        "inline-llm": ["llama-cpp-python==0.3.19"],
        "voice": ["openai-whisper>=20231117,<2025.0"],
    }

    def test_minimal_core_only(self):
        plan = inst.build_plan(self.SETS, {"variant": "cpu", "voice": False,
                                           "ocr": False, "llm_inline": False})
        names = [s.name for s in plan]
        # core deps + pinned llama-server fetch (the Phase C default runtime)
        self.assertEqual(len(plan), 2)
        self.assertIn("core", names[0])
        self.assertIn("llama-server", names[1])
        argv = " ".join(plan[0].argv)
        self.assertNotIn("torch", argv)
        fetch_argv = " ".join(plan[1].argv)
        self.assertIn("fetch_llama.py", fetch_argv)
        self.assertIn("--variant", fetch_argv)

    def test_voice_adds_torch_then_whisper_both_optional(self):
        plan = inst.build_plan(self.SETS, {"variant": "cpu", "voice": True,
                                           "ocr": False, "llm_inline": False})
        torch_steps = [s for s in plan if "torch" in " ".join(s.argv)]
        whisper_steps = [s for s in plan if any("whisper" in i for i in s.imports)]
        self.assertEqual(len(torch_steps), 1)
        self.assertIn(inst.TORCH_CPU_INDEX, torch_steps[0].argv)
        self.assertTrue(torch_steps[0].optional)
        self.assertEqual(len(whisper_steps), 1)
        self.assertTrue(whisper_steps[0].optional)

    def test_ocr_step_optional_with_import_check(self):
        plan = inst.build_plan(self.SETS, {"variant": "cpu", "voice": False,
                                           "ocr": True, "llm_inline": False})
        ocr_steps = [s for s in plan if any("pytesseract" in i for i in s.imports)]
        self.assertEqual(len(ocr_steps), 1)
        self.assertTrue(ocr_steps[0].optional)


class TestAssumeYesPrompts(TestCase):
    def test_ask_yes_returns_default(self):
        self.assertEqual(inst.ask("q?", ["a", "b"], 1, assume_yes=True), "b")

    def test_ask_yn_yes_returns_default(self):
        self.assertFalse(inst.ask_yn("q?", default=False, assume_yes=True))
        self.assertTrue(inst.ask_yn("q?", default=True, assume_yes=True))


if __name__ == "__main__":
    main(verbosity=2)
