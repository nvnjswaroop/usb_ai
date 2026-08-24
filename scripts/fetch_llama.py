#!/usr/bin/env python
"""Fetch the pinned llama-server binary for USB AI (Phase B).

Downloads ONE prebuilt zip from official ggml-org/llama.cpp releases,
verifies sha256 against llama_server.lock, extracts to bin/llama/, unblocks
(Windows), and smoke-runs `--version`.

NEVER compiles anything — if no prebuilt asset matches your platform the
answer is "unsupported", not a 40-minute doomed MSVC build.

Usage:
  python scripts/fetch_llama.py --variant cpu            # use committed lock
  python scripts/fetch_llama.py --list                   # discover assets
  python scripts/fetch_llama.py --variant cpu --write-lock   # pin new build

llama_server.lock format (JSON):
{
  "build": "b6100",
  "assets": {
    "cpu":    {"url": ".../llama-b6100-bin-win-cpu-x64.zip",    "sha256": "..."},
    "cpu-avx": {...}, "cpu-noavx": {...},
    "cuda":   {...},  "vulkan": {...}
  }
}
# ponytail: asset NAMES drift between llama.cpp releases; that's why the
# lock stores full URLs and `--list` exists — bumping pins is: --list, edit
# URLs, download once, record sha256, commit. Never guess.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "llama_server.lock"
DEST = ROOT / "bin" / "llama"
API_LATEST = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"

# AVX compatibility ladder: try in order when the binary won't execute.
CPU_LADDER = ["cpu", "cpu-avx", "cpu-noavx"]


def http_get(url: str, timeout: float = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "usb-ai-installer"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def load_lock() -> dict:
    if not LOCK.exists():
        sys.exit(f"[ERROR] {LOCK.name} missing. Create it from `--list` output:\n"
                 f"       python {Path(__file__).name} --list")
    return json.loads(LOCK.read_text(encoding="utf-8"))


API_RELEASES = "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=10"


def list_assets() -> None:
    # ponytail: /releases/latest points at non-binary tag-only releases
    # (e.g. v0.2.0) — scan the recent list and surface builds that actually
    # ship a llama-server binary instead.
    releases = json.loads(http_get(API_RELEASES).decode("utf-8"))
    shown = 0
    for rel in releases:
        assets = [a for a in rel.get("assets", [])
                  if "llama-server" not in a["name"] or a["name"].endswith(".zip")]
        bin_assets = [a for a in rel.get("assets", []) if a["name"].endswith(".zip")]
        if not bin_assets:
            continue
        print(f"release {rel['tag_name']}:")
        for a in bin_assets:
            if sys.platform == "win32" and "win" not in a["name"]:
                continue
            if "llama-server" not in a["name"] and "-bin-" not in a["name"]:
                continue
            print(f"  {a['name']:60s} {a['size']/(1024*1024):8.1f} MB")
        shown += 1
        if shown >= 3:
            break
    print("\nPin your pick into llama_server.lock "
          "(full browser-download URL + sha256 of the downloaded file).")


def download_and_verify(url: str, expect_sha: str | None) -> tuple[Path, str]:
    print(f"downloading: {url}")
    data = http_get(url, timeout=600)
    digest = hashlib.sha256(data).hexdigest()
    print(f"sha256     : {digest}")
    if expect_sha and digest != expect_sha:
        sys.exit("[ERROR] sha256 mismatch vs llama_server.lock — ABORTING "
                 "(possible tampered/stale mirror). No file written.")
    tmp = ROOT / "_llama_server_download.zip"
    tmp.write_bytes(data)
    return tmp, digest


def extract(zip_path: Path) -> None:
    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as z:
        for member in z.namelist():
            name = Path(member).name
            if not name:
                continue
            # ponytail: flatten — every release layout so far keeps binaries
            # at top level or one folder deep; we only want exe+dll siblings.
            target = DEST / name
            target.write_bytes(z.read(member))
    zip_path.unlink(missing_ok=True)


def unblock_windows() -> None:
    if sys.platform != "win32":
        return
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    f"Get-ChildItem -Recurse '{DEST}' | Unblock-File"],
                   capture_output=True)


def smoke_version(binary: Path) -> bool:
    r = subprocess.run([str(binary), "--version"],
                       capture_output=True, timeout=30)
    ok = r.returncode == 0
    tail = (r.stdout + r.stderr).decode("utf-8", errors="replace").strip()[:120]
    print(f"  --version -> rc={r.returncode} {tail}")
    return ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--variant", default="cpu",
                    help="key inside llama_server.lock assets (cpu/cuda/vulkan)")
    ap.add_argument("--list", action="store_true", help="show latest assets")
    ap.add_argument("--write-lock", action="store_true",
                    help="record fetched url+sha256 into llama_server.lock")
    args = ap.parse_args(argv)

    if args.list:
        list_assets()
        return 0

    lock = load_lock()
    ladder = CPU_LADDER if args.variant == "cpu" else [args.variant]
    last_err = "no attempt made"

    for key in ladder:
        entry = lock.get("assets", {}).get(key)
        if not entry:
            last_err = f"asset '{key}' not in {LOCK.name}"
            continue
        try:
            zip_path, digest = download_and_verify(entry["url"],
                                                   entry.get("sha256"))
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001 — network layer, report & ladder
            last_err = f"download failed: {e}"
            print(f"[WARN] {last_err}; trying next fallback…")
            continue
        extract(zip_path)
        unblock_windows()
        exe = DEST / ("llama-server.exe" if sys.platform == "win32"
                      else "llama-server")
        if not exe.exists():
            last_err = f"{exe.name} not found after extraction"
            continue
        if smoke_version(exe):
            print(f"\nOK: llama-server ({key}) ready in {DEST}")
            if args.write_lock:
                entry["sha256"] = digest
                LOCK.write_text(json.dumps(lock, indent=2), encoding="utf-8")
                print(f"locked sha256 for '{key}' -> {LOCK.name}")
            return 0
        last_err = "--version failed (possibly incompatible instruction set)"
        print(f"[WARN] {last_err}; trying next fallback…")

    sys.exit(f"[ERROR] could not install llama-server: {last_err}\n"
             f"Manual path: download the zip yourself, place it at {DEST}, "
             f"re-run with --write-lock.")


if __name__ == "__main__":
    sys.exit(main())
