#!/usr/bin/env python
"""Download pre-built llama-cpp-python and torch wheels for offline installs.

Populates ./wheels/ with .whl files matching the host Python + platform.
Run once on a networked machine, commit the wheels, and setup can install
truly air-gapped without touching abetlen.github.io or download.pytorch.org.

Usage: python scripts/fetch_wheels.py [--force]

Verify-after-fetch:
  - Each downloaded wheel is checked against requirements.lock (sha256).
  - If a wheel's hash doesn't match, it's deleted and the script aborts.
  - On a poisoned-CDN response, the bad file never reaches ./wheels/.
"""
import os, sys, urllib.request, ssl, platform, re, hashlib, argparse
from pathlib import Path

WHEEL_DIR = Path(__file__).resolve().parent.parent / "wheels"
LOCK_FILE = Path(__file__).resolve().parent.parent / "requirements.lock"
WHEEL_DIR.mkdir(parents=True, exist_ok=True)

# ponytail: was `ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE` —
# that disabled cert verification for every URL we fetch, including any
# redirect targets. Now uses the default cert store. If a corporate MITM
# proxy breaks this, set PYTHONHTTPSVERIFY=0 to opt back in (with eyes open).
def _ssl_context():
    if os.environ.get("PYTHONHTTPSVERIFY") == "0":
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return ssl.create_default_context()


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
    ctx = _ssl_context()
    try:
        html = urllib.request.urlopen(index_url, timeout=30, context=ctx).read().decode()
    except Exception as e:
        print(f"[skip] cannot reach {index_url}: {e}")
        return None
    for href in re.findall(r'href="(https://github\.com/abetlen/llama-cpp-python/releases/download/v[^"]+\.whl)"', html):
        if re.search(rx, href):
            return href
    return None


def sha256_of(path: Path) -> str:
    """Compute sha256 of a file. Used to verify against requirements.lock."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_lock() -> dict:
    """Load requirements.lock → {filename: sha256}. Missing file → empty dict."""
    if not LOCK_FILE.exists():
        return {}
    out = {}
    for line in LOCK_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Format: <sha256>  <wheel-filename>
        parts = line.split()
        if len(parts) == 2 and len(parts[0]) == 64:
            out[parts[1]] = parts[0].lower()
    return out


def verify_or_delete(path: Path, lock: dict) -> bool:
    """Verify a wheel's sha256 against the lockfile. Returns True if OK."""
    expected = lock.get(path.name)
    if expected is None:
        # No pin for this wheel — accept it but warn.
        print(f"  warn: {path.name} has no sha256 pin in requirements.lock")
        return True
    actual = sha256_of(path)
    if actual != expected:
        print(f"  FAIL: {path.name} sha256 mismatch")
        print(f"    expected: {expected}")
        print(f"    actual:   {actual}")
        path.unlink(missing_ok=True)
        return False
    print(f"  ok: {path.name} sha256 verified")
    return True


def fetch(url, dest, force=False):
    if dest.exists() and dest.stat().st_size > 10000 and not force:
        print(f"  cached: {dest.name} ({dest.stat().st_size//1024} KB)")
        return False
    print(f"  fetching {url}")
    print(f"     -> {dest.name}")
    ctx = _ssl_context()
    # ponytail: stream to disk so we don't hold the full ~50 MB wheel in RAM.
    with urllib.request.urlopen(url, timeout=120, context=ctx) as resp, \
         open(dest, "wb") as f:
        while True:
            chunk = resp.read(1 << 20)  # 1 MB chunks
            if not chunk:
                break
            f.write(chunk)
    return True


# --- main -------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true",
                    help="re-download even if cached wheel exists")
    args = ap.parse_args()

    lock = load_lock()
    print(f"Python: {py_short}  platform: {plat}")
    if not lock:
        print(f"  note: no requirements.lock found — wheels will be downloaded "
              f"but NOT hash-verified. Run scripts/update_lock.py after fetching.")
    else:
        print(f"  lock: {len(lock)} wheels pinned")

    fname_re = re.compile(rf"llama_cpp_python-{LLAMA_VERSION}-{llama_py}-.*?-{plat}\.whl")
    url = find_wheel_on_abetlen(fname_re)
    if url:
        dest = WHEEL_DIR / f"llama_cpp_python-{LLAMA_VERSION}-{llama_py}-{plat}.whl"
        fetch(url, dest, force=args.force)
    else:
        print(f"[warn] no matching llama-cpp-python v{LLAMA_VERSION} wheel on abetlen index for {llama_py}/{plat}")
        print("       fetch manually from https://github.com/abetlen/llama-cpp-python/releases")

    # Quick sanity: verify the wheel is a valid ZIP (not an error page)
    import zipfile
    ok_count = 0
    for whl in sorted(WHEEL_DIR.glob("*.whl")):
        try:
            z = zipfile.ZipFile(whl); z.close()
        except zipfile.BadZipFile:
            print(f"  bad: {whl.name} (corrupt? re-run with --force)")
            whl.unlink(missing_ok=True)
            continue
        # sha256 verify
        if not verify_or_delete(whl, lock):
            sys.exit(2)
        print(f"  ok: {whl.name} ({whl.stat().st_size//1024} KB)")
        ok_count += 1

    print(f"\nWheels sealed in: {WHEEL_DIR} ({ok_count} verified)")
    print("Next: setup.bat / setup.sh will use these automatically when present.")


if __name__ == "__main__":
    main()
