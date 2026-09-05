#!/usr/bin/env python
"""USB AI installer — hardware-driven dependency chooser.

Stdlib only. Called by setup.{bat,sh} after the Python bootstrap, or run
directly:  python scripts/install.py [flags]

Stages:
  1. gate      — Python version check (>=3.10)
  2. detect    — best-effort hardware probe (arch, GPU hint)
  3. confirm   — show detected choices; interactive override unless --yes
  4. install   — pip install selected feature sets (core always)
  5. verify    — import-smoke every installed feature set
  6. summary   — what you chose + next steps

Flags:
  --yes                non-interactive: accept detected/default answers
  --dry-run            print the plan, run nothing
  --cpu | --cuda | --vulkan | --metal     force LLM runtime variant
                                       (Phase B consumes this; recorded now)
  --voice / --no-voice install / skip Whisper STT (torch ~900MB)
  --ocr  / --no-ocr    install / skip scanned-PDF OCR

# ponytail: pure logic (parse/detect/plan) is separated from execution so
tests/test_installer.py can exercise decisions without touching pip.
"""
from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQ_FILE = ROOT / "requirements.txt"

MIN_PY = (3, 10)

VARIANTS = ("cpu", "cuda", "vulkan", "metal")
TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"


# ── requirements.txt parsing ─────────────────────────────────────────────────
def parse_requirements(path: Path = REQ_FILE) -> dict[str, list[str]]:
    """Split requirements into feature sets via @feature:<name>:start/end
    comment markers. Lines outside any block -> 'core'. The file stays
    plain-pip-installable because markers are comment lines.
    """
    sets: dict[str, list[str]] = {"core": []}
    current = "core"
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("# @feature:") and line.endswith(":start"):
            current = line.removeprefix("# @feature:").removesuffix(":start").strip()
            sets.setdefault(current, [])
            continue
        if line.startswith("# @feature:") and line.endswith(":end"):
            current = "core"
            continue
        if not line or line.startswith("#"):
            continue
        # ponytail: strip INLINE comments too ("pkg>=1  # why") — pip ignores
        # anything from ' #' onward, and if we don't, the comment text leaks
        # into the pip argv as bogus package names (caught by --dry-run).
        cut = line.find(" #")
        if cut == -1:
            cut = line.find("\t#")
        if cut != -1:
            line = line[:cut].strip()
            if not line:
                continue
        sets.setdefault(current, []).append(line)
    return sets


# ── hardware detection (best-effort; user confirms/overrides) ────────────────
def detect_arch() -> str:
    m = platform.machine().lower()
    return "arm64" if m in ("arm64", "aarch64") else "x64"


def detect_gpu_hint() -> tuple[str, str]:
    """Returns (variant_guess, human_reason). Never raises."""
    if sys.platform == "darwin":
        return "metal", "macOS — Metal is built into llama.cpp"
    if shutil.which("nvidia-smi"):
        return "cuda", "nvidia-smi found on PATH"
    return "cpu", "no GPU tooling detected"


def detect_hardware() -> dict:
    variant, reason = detect_gpu_hint()
    # ponytail: cuda asset assumes x64 linux/win builds published by ggml;
    # arm64 + cuda exists upstream but keep the guess conservative.
    if variant == "cuda" and detect_arch() == "arm64":
        variant, reason = "vulkan", "arm64 host — CUDA asset unlikely; pick manually"
    return {"arch": detect_arch(), "gpu": variant, "gpu_reason": reason}


# ── plan building (pure) ──────────────────────────────────────────────────────
@dataclass
class Step:
    name: str
    argv: list              # exact command to run
    optional: bool = False  # failure -> warn + continue
    imports: list = field(default_factory=list)  # verify-stage import names


def build_plan(sets: dict, ans: dict) -> list[Step]:
    """Pure: feature answers -> ordered Step list. No side effects."""
    py = sys.executable
    steps: list[Step] = []

    core_pkgs = list(sets.get("core", []))
    steps.append(Step("core dependencies",
                      [py, "-m", "pip", "install", "--no-warn-script-location"] + core_pkgs,
                      optional=False,
                      imports=["fastapi", "pydantic", "pymupdf", "pptx", "PIL",
                               "httpx"]))

    if ans["llm_inline"]:
        llm_pkgs = list(sets.get("inline-llm", []))
        if not llm_pkgs:
            print("  [NOTE] --legacy-inline requested but requirements.txt "
                  "no longer carries the inline engine (removed Phase C).")
        else:
            steps.append(Step("inline LLM engine (LEGACY, py3.11 only)",
                              [py, "-m", "pip", "install",
                               "--no-warn-script-location"] + llm_pkgs,
                              optional=False,
                              imports=["llama_cpp"]))
    else:
        # Default: download the pinned official llama-server binary.
        steps.append(Step(
            f"llama-server runtime ({ans['variant']}, sha256-pinned)",
            [py, str(ROOT / "scripts" / "fetch_llama.py"),
             "--variant", ans["variant"]],
            optional=False))

    if ans["voice"]:
        # torch first, CPU index (repo rule: never the CUDA wheel by accident),
        # then whisper from PyPI.
        steps.append(Step("voice: torch (CPU build, ~900MB)",
                          [py, "-m", "pip", "install", "--no-warn-script-location",
                           "torch", "--index-url", TORCH_CPU_INDEX],
                          optional=True))
        voice_pkgs = list(sets.get("voice", []))
        if voice_pkgs:
            steps.append(Step("voice: Whisper STT",
                              [py, "-m", "pip", "install",
                               "--no-warn-script-location"] + voice_pkgs,
                              optional=True,
                              imports=["whisper"]))

    if ans["ocr"]:
        steps.append(Step("ocr: pytesseract (system Tesseract binary still needed)",
                          [py, "-m", "pip", "install", "--no-warn-script-location",
                           "pytesseract>=0.3.10"],
                          optional=True,
                          imports=["pytesseract"]))

    return steps


# ── interaction ───────────────────────────────────────────────────────────────
def ask(question: str, options: list[str], default_idx: int, assume_yes: bool) -> str:
    if assume_yes:
        return options[default_idx]
    print(f"\n{question}")
    for i, opt in enumerate(options, 1):
        mark = " (default)" if i - 1 == default_idx else ""
        print(f"  {i}) {opt}{mark}")
    while True:
        raw = input(f"Choose 1-{len(options)} [Enter=default]: ").strip()
        if raw == "":
            return options[default_idx]
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        print("  invalid choice")


def ask_yn(question: str, default: bool, assume_yes: bool) -> bool:
    if assume_yes:
        return default
    suffix = " [Y/n]: " if default else " [y/N]: "
    raw = input(question + suffix).strip().lower()
    if raw == "":
        return default
    return raw in ("y", "yes")


# ── execution ─────────────────────────────────────────────────────────────────
def run_step(step: Step, dry_run: bool) -> bool:
    if dry_run:
        print(f"  [dry-run] {step.name}\n            $ {' '.join(step.argv)}")
        return True
    print(f"  -> {step.name}")
    r = subprocess.run(step.argv)
    if r.returncode != 0:
        if step.optional:
            print(f"  [WARNING] {step.name} failed — continuing without it.")
            return False
        print(f"[ERROR] {step.name} failed. See output above.")
        sys.exit(2)
    return True


def verify_imports(plan: list[Step], dry_run: bool) -> list[str]:
    problems = []
    for step in plan:
        for mod in step.imports:
            try:
                __import__(mod)
            except ImportError:
                problems.append(f"{mod}  (needed by: {step.name})")
    return problems


def ensure_folders() -> None:
    for d in ("models", "history", "output", "prefeeds", "whisper_models", "bin"):
        (ROOT / d).mkdir(exist_ok=True)
    sp = ROOT / "prefeeds" / "system_prompt.txt"
    if not sp.exists():
        sp.write_text(
            "You are a helpful AI assistant on a USB drive. "
            "Completely private. Be concise and honest.", encoding="utf-8")


# ── main ──────────────────────────────────────────────────────────────────────
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="USB AI dependency installer")
    ap.add_argument("--yes", action="store_true", help="non-interactive defaults")
    ap.add_argument("--dry-run", action="store_true")
    g = ap.add_mutually_exclusive_group()
    for v in VARIANTS:
        g.add_argument(f"--{v}", dest="variant", action="store_const", const=v)
    ap.add_argument("--voice", dest="voice", action="store_true", default=None)
    ap.add_argument("--no-voice", dest="voice", action="store_false")
    ap.add_argument("--ocr", dest="ocr", action="store_true", default=None)
    ap.add_argument("--no-ocr", dest="ocr", action="store_false")
    ap.add_argument("--skip-llm", action="store_true",
                    help="skip the llama-server binary download entirely")
    ap.add_argument("--legacy-inline", action="store_true",
                    help="install the REMOVED llama-cpp-python engine "
                         "(only works with a requirements.txt that still "
                         "carries the @feature:inline-llm block, py3.11)")
    args = ap.parse_args(argv)

    print("=" * 60)
    print(" USB AI - Dependency Installer")
    print("=" * 60)

    # 1 ── gate
    if sys.version_info < MIN_PY:
        print(f"[ERROR] Python {MIN_PY[0]}.{MIN_PY[1]}+ required, "
              f"got {sys.version_info.major}.{sys.version_info.minor}.")
        return 1
    print(f"[1/5] Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}: OK")

    # 2 ── detect
    hw = detect_hardware()
    print(f"[2/5] Hardware: arch={hw['arch']}  gpu-hint={hw['gpu']} ({hw['gpu_reason']})")

    # 3 ── confirm / override
    variant = args.variant or hw["gpu"]
    if args.variant is None and not args.yes:
        variant = ask(
            "LLM runtime variant? (Phase B downloads the matching llama.cpp build)",
            [v for v in VARIANTS],
            VARIANTS.index(variant) if variant in VARIANTS else 0,
            args.yes)
    voice = args.voice if args.voice is not None else \
        ask_yn("Install voice input (Whisper STT, downloads ~900MB torch)?",
               default=False, assume_yes=args.yes)
    ocr = args.ocr if args.ocr is not None else \
        ask_yn("Install OCR for scanned PDFs?", default=False, assume_yes=args.yes)
    llm_inline = bool(args.legacy_inline)

    ans = {"variant": variant, "voice": voice, "ocr": ocr, "llm_inline": llm_inline}
    print(f"[3/5] Choices: variant={ans['variant']}  voice={voice}  ocr={ocr}"
          f"  inline-llm={llm_inline}" + ("  (non-interactive)" if args.yes else ""))

    # 4 ── plan + execute
    sets = parse_requirements()
    plan = build_plan(sets, ans)
    print(f"[4/5] Installing {len(plan)} step(s):")
    for s in plan:
        run_step(s, args.dry_run)

    ensure_folders()

    # 5 ── verify + summary
    print("[5/5] Verifying imports...")
    problems = verify_imports(plan, args.dry_run)
    if problems:
        print("  [WARNING] missing after install:")
        for p in problems:
            print(f"    - {p}")
        print("  Core chat works; flagged features may be degraded.")
    else:
        print("  all selected features import cleanly.")

    print()
    print("=" * 60)
    print(" Installer done.")
    print(f"   LLM runtime : llama-server ({ans['variant']}) in bin{os.sep}llama")
    print("   Next: drop a .gguf into models\\ , then run launch.bat")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
