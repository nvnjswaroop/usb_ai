#!/usr/bin/env bash
set -euo pipefail

# Run pip-audit against requirements.txt and fail on high-severity findings.
# Note: pip-audit 2.10.1 does not support the --fail-level flag, so we use a
# belt-and-braces heuristic: pin exit code + scan output for the "Found N known
# vulnerabilities" message. The "Found 0 known vulnerabilities" line is parsed
# out as the no-op success case.

OUT=$(mktemp)
trap 'rm -f "$OUT"' EXIT

# pip-audit non-zero on findings — capture and inspect.
set +e
pip-audit -r requirements.txt >"$OUT" 2>&1
AUDIT_RC=$?
set -e

VULNS=0

# 1. Non-zero exit code from pip-audit.
if [ "$AUDIT_RC" -ne 0 ]; then
    VULNS=1
fi

# 2. Output mentions "Found N known vulnerabilities" but N != 0.
if grep -E "Found [1-9][0-9]* known vulnerabilit" "$OUT" > /dev/null; then
    VULNS=1
fi

# 3. If output says "Found 0 known vulnerabilities", override to clean.
if grep -q "Found 0 known vulnerabilities" "$OUT"; then
    VULNS=0
fi

if [ "$VULNS" -eq 1 ]; then
    echo "============================================================"
    echo " pip-audit found vulnerabilities in requirements.txt"
    echo " Bump vulnerable pins, then re-run this script."
    echo "============================================================"
    cat "$OUT"
    exit 1
fi

exit 0
