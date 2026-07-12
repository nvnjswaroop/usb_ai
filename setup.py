#!/usr/bin/env python3
"""USB AI installer. Pure stdlib + subprocess. No cmd.exe / PowerShell."""
import os, sys, subprocess, urllib.request, zipfile, shutil, json
from pathlib import Path

USB   = Path(__file__).resolve().parent
PY    = Path(r"C:\Users\JYOTHI\AppData\Local\Programs\Python\Python311\python.exe")
WIN   = USB / "python" / "win"
WHEEL = USB / "wheels" / "llama_cpp_python-0.3.19-cp311-win_amd64.whl"

def msg(c, t): print(f"\033[{c}m{t}\033[0m")
def ok(t):   msg("92", f"  [OK] {t}")
def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    return r.returncode, r.stdout, r.stderr

def step(n, t):
    print(f"\n[{n}/7] {t}")
    print("=" * (len(t) + 8))

# 1. Python
step(1, "Locating Python")
if not PY.exists():
    print(f"  [ERROR] Python not found at {PY}")
    print("  Install Python 3.11 from python.org then re-run.")
    sys.exit(1)
rc, out, _ = run([str(PY), "-c", "import sys; print(sys.version)"])
if rc: sys.exit(f"  [ERROR] Python broken: {out!r}")
ok(f"Python: {out.strip()}")
print(f"  path: {PY}")

# 2. pip
step(2, "Installing pip")
run([str(PY), "-m", "ensurepip", "--upgrade", "--quiet"])
rc, out, err = run([str(PY), "-m", "pip", "--version"])
if rc:
    print(f"  [WARNING] ensurepip failed, fetching get-pip.py...")
    urllib.request.urlretrieve("https://bootstrap.pypa.io/get-pip.py",
                               str(USB / "tmp_get-pip.py"))
    run([str(PY), str(USB / "tmp_get-pip.py"), "--quiet"])
    (USB / "tmp_get-pip.py").unlink(missing_ok=True)
run([str(PY), "-m", "pip", "install", "--upgrade", "pip", "--quiet"])
ok("pip ready")

# 3. Base packages
step(3, "Installing base packages")
pkgs = ["fastapi", "uvicorn[standard]", "pydantic", "python-multipart",
        "aiofiles", "pypdf", "pymupdf", "python-pptx", "pillow",
        "jinja2", "numpy>=1.20"]
rc, out, err = run([str(PY), "-m", "pip", "install", *pkgs, "--quiet"])
if rc:
    sys.exit(f"  [ERROR] Base packages failed:\n{err}")
ok(f"installed {len(pkgs)} packages")

# 4. llama-cpp-python
step(4, "Installing llama-cpp-python 0.3.19 (CPU)")
if WHEEL.exists():
    ok(f"using local wheel {WHEEL.name}")
    rc, out, err = run([str(PY), "-m", "pip", "install", str(WHEEL),
                        "--no-index", "--quiet"])
else:
    print(f"  no local wheel, fetching from abetlen index (with PyPI fallback)...")
    rc, out, err = run([str(PY), "-m", "pip", "install",
                        "llama-cpp-python==0.3.19",
                        "--extra-index-url",
                        "https://abetlen.github.io/llama-cpp-python/whl/cpu",
                        "--quiet"])
if rc:
    sys.exit(f"  [ERROR] llama-cpp-python install failed:\n{err}")
ok("llama-cpp-python 0.3.19 installed")

# 5. Feature packages
step(5, "Installing feature packages")
rc, _, err = run([str(PY), "-m", "pip", "install", "torch",
                  "--index-url", "https://download.pytorch.org/whl/cpu",
                  "--quiet"])
if rc:
    print(f"  [WARNING] torch CPU index failed, trying PyPI...")
    rc, _, err = run([str(PY), "-m", "pip", "install", "torch", "--quiet"])
    if rc: print(f"  [WARNING] torch unavailable: {err.splitlines()[-1] if err else 'unknown'}")
else:
    ok("torch CPU installed")

rc, _, err = run([str(PY), "-m", "pip", "install", "openai-whisper", "--quiet"])
if rc:
    print(f"  [WARNING] whisper unavailable, voice input disabled")
else:
    ok("openai-whisper installed")
    print(f"  pre-downloading whisper 'base' model...")
    rc, _, err = run([str(PY), "-c",
                      "import whisper; whisper.load_model('base', download_root='whisper_models')"])
    if rc: print(f"  [WARNING] whisper model download failed")

# 6. py_path.txt
step(6, "Saving launcher helper")
(USB / "py_path.txt").write_text(str(PY), encoding="utf-8")
ok(f"wrote {USB / 'py_path.txt'}")

# 7. folders
step(7, "Creating folders")
for d in ["models", "history", "output", "prefeeds", "whisper_models"]:
    p = USB / d
    p.mkdir(exist_ok=True)
    print(f"  ensured {d}/")
sp = USB / "prefeeds" / "system_prompt.txt"
if not sp.exists():
    sp.write_text("You are a helpful AI assistant on a USB drive. "
                  "Completely private. Be concise and honest.\n",
                  encoding="utf-8")
    print(f"  wrote system_prompt.txt")

# Verify
print(f"\nVerifying llama-cpp-python...")
verify = (
    "import llama_cpp; "
    "v = getattr(llama_cpp, '__version__', '?'); "
    "i = llama_cpp.llama_print_system_info().decode(); "
    "miss = [x for x in ('AVX2',) if x not in i]; "
    "status = 'OK' if not miss else 'MISSING: ' + ','.join(miss); "
    "print(f'  version: {v}'); "
    "print(f'  CPU    : {i.strip()}'); "
    "print(f'  status : {status}')"
)
rc, out, err = run([str(PY), "-c", verify])
print(out or err)

print("\n" + "=" * 60)
print(" Setup complete!")
print(f"  1. Python: {PY}")
print(f"  2. Drop a .gguf model into {USB / 'models'}")
print(f"  3. Run download_whisper.bat for voice input")
print(f"  4. Run launch.bat (uses py_path.txt automatically)")
print("=" * 60)