#!/usr/bin/env python
"""Regenerate requirements.lock from the current ./wheels/ directory.

After running `python scripts/fetch_wheels.py`, run this to write the sha256
of each wheel into requirements.lock. setup.{bat,sh} can then verify
wheel integrity at install time.

Usage: python scripts/update_lock.py
"""
import hashlib, sys
from pathlib import Path

WHEEL_DIR = Path(__file__).resolve().parent.parent / "wheels"
LOCK_FILE = Path(__file__).resolve().parent.parent / "requirements.lock"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    if not WHEEL_DIR.exists() or not any(WHEEL_DIR.glob("*.whl")):
        print(f"[ERROR] no wheels in {WHEEL_DIR}")
        print("        run `python scripts/fetch_wheels.py` first.")
        sys.exit(1)

    lines = [
        "# requirements.lock — sha256 pins for wheels in ./wheels/",
        "# Regenerate with: python scripts/update_lock.py",
        "# Verified at install time by setup.{bat,sh} when --verify-wheels is set.",
        "",
    ]
    for whl in sorted(WHEEL_DIR.glob("*.whl")):
        digest = sha256_of(whl)
        lines.append(f"{digest}  {whl.name}")

    LOCK_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {len(list(WHEEL_DIR.glob('*.whl')))} pins to {LOCK_FILE}")


if __name__ == "__main__":
    main()
