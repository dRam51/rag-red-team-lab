#!/usr/bin/env bash
# Deploy the markdown_exfil probe + detector into a target garak install.
#
# Usage:
#   ./deploy.sh                       # auto-detect garak from `python -c "import garak; ..."`
#   ./deploy.sh /path/to/venv/python  # use the given interpreter's garak
#
# Idempotent: safe to re-run whenever you change the source files.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${1:-python3}"

# Resolve garak's package directory from the target interpreter.
GARAK_PKG="$("$PY" -c 'import garak, os; print(os.path.dirname(garak.__file__))')"

if [[ -z "${GARAK_PKG}" || ! -d "${GARAK_PKG}/probes" ]]; then
  echo "error: could not find garak package via '$PY'." >&2
  echo "hint: pass the venv python explicitly, e.g. ./deploy.sh ../garak_work/.venv/bin/python" >&2
  exit 1
fi

install -m 644 "${HERE}/probes/markdown_exfil.py"    "${GARAK_PKG}/probes/markdown_exfil.py"
install -m 644 "${HERE}/detectors/markdown_exfil.py" "${GARAK_PKG}/detectors/markdown_exfil.py"

echo "deployed to ${GARAK_PKG}"
echo "verify with: $PY -m garak --list_probes | grep markdown_exfil"
