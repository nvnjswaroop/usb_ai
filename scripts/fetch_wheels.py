#!/usr/bin/env python
"""Download pre-built llama-cpp-python and torch wheels for offline installs.

Populates ./wheels/ with .whl files matching the host Python + platform.
Run once on a networked machine, commit the wheels, and setup can install
truly air-gapped without touching abetlen.github.io or download.pytorch.org.

Usage: python scripts/fetch_wheels.py
"""
import os, sys, urllib.request, ssl, platform, re, zipfile
from pathlib import Path

WHEEL_DIR = Path(__file__).resolve().parent.parent / "wheels"
WHEEL_DIR.mkdir(parents=True, exist_ok=True)

ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE

def py_tag():
    v = sys.version_info
    return f"cp{v.major}{v.minor}", f"{v.major}.{v.minor}"

def platform_tag():
    # ponytail: only win_amd64 + linux_x86_64 supported today; mac/unix wheels exist
    # on abetlen index for the same cp tag if you need them — extend as needed.
    if sys.platform.startswith("win") and platform.machine().endswith("64"):
        return "win_amd64"
    if sys.platform.startswith("linux") and platform.machine().endswith("64"):
        return "linux_x86_64"
    raise SystemExit(f"Unsupported platform: {sys.platform} / {platform.machine()}")

# --- llama-cpp-python --------------------------------------------------------
LLAMA_VERSION = "0.3.19"
llama_py, py_short = py_tag()
plat = platform_tag()

# abetlen index lists files under llama-cpp-python/
index_url = "https://abetlen.github.io/llama-cpp-python/whl/cpu/"
def find_wheel_on_abetlen(rx):
    try:
        html = urllib.request.urlopen(index_url, timeout=30, context=ctx).read().decode()
    except Exception as e:
        print(f"[skip] cannot reach {index_url}: {e}")
        return None
    for href in re.findall(r'href="(https://github\.com/abetlen/llama-cpp-python/releases/download/v[^"]+\.whl)"', html):
        if re.search(rx, href):
            return href
    return None

def fetch(url, dest):
    if dest.exists() and dest.stat().st_size > 10000:
        return False
    print(f"  fetching {url}\n     -> {dest.name}")
    urllib.request.urlretrieve(url, dest)
    return True

# main
print(f"Python: {py_short}  platform: {plat}")
fname_re = re.compile(rf"llama_cpp_python-{LLAMA_VERSION}-{llama_py}-.*?-{plat}\.whl")
url = find_wheel_on_abetlen(fname_re)
if url:
    dest = WHEEL_DIR / f"llama_cpp_python-{LLAMA_VERSION}-{llama_py}-{plat}.whl"
    fetch(url, dest)
else:
    print(f"[warn] no matching llama-cpp-python v{LLAMA_VERSION} wheel on abetlen index for {llama_py}/{plat}")
    print("       fetch manually from https://github.com/abetlen/llama-cpp-python/releases")

# Quick sanity: verify the wheel is a valid ZIP (not an error page)
import zipfile
for whl in WHEEL_DIR.glob("*.whl"):
    try:
        z = zipfile.ZipFile(whl); z.close()
        print(f"  ok: {whl.name} ({whl.stat().st_size//1024} KB)")
    except zipfile.BadZipFile:
        print(f"  bad: {whl.name} (corrupt? re-run with --force)")

print(f"\nWheels sealed in: {WHEEL_DIR}")
print("Next: setup.bat / setup.sh will use these automatically when present.")
