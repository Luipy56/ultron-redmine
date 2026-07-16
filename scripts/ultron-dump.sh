#!/usr/bin/env bash
# Apply Ultron code changes: editable reinstall + systemd restart.
#
# Run after ANY source change — manual edits, git pull, or config that needs a
# process restart. Slash commands and scheduled jobs only update after this.
#
# Usage:
#   ./scripts/ultron-dump.sh
#   ULTRON_SYSTEMD_UNIT=ultron.service ./scripts/ultron-dump.sh
#   ULTRON_DUMP_SKIP_RESTART=1 ./scripts/ultron-dump.sh   # pip/npm only (Discord /upgrade)
#   ULTRON_DUMP_RESTART_NO_BLOCK=1 ./scripts/ultron-dump.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT="${ULTRON_SYSTEMD_UNIT:-ultron.service}"
SKIP_RESTART="${ULTRON_DUMP_SKIP_RESTART:-0}"
NO_BLOCK="${ULTRON_DUMP_RESTART_NO_BLOCK:-0}"

cd "${ROOT}"

echo "ultron-dump: pip install -e ."
"${ROOT}/.venv/bin/pip" install -q -e .

if [[ -f "${ROOT}/package.json" ]] && command -v npm >/dev/null 2>&1; then
  echo "ultron-dump: npm install (pi-coding-agent)"
  (cd "${ROOT}" && npm install --ignore-scripts --silent)
fi

if [[ "$SKIP_RESTART" == "1" ]]; then
  echo "ultron-dump: OK — install done (restart skipped; caller will restart)"
  exit 0
fi

if [[ "$NO_BLOCK" == "1" ]]; then
  echo "ultron-dump: systemctl restart --no-block ${UNIT}"
  systemctl restart --no-block "${UNIT}"
  echo "ultron-dump: OK — restart requested (no-block); caller should exit"
  exit 0
fi

echo "ultron-dump: systemctl restart ${UNIT}"
systemctl restart "${UNIT}"

sleep 2
if systemctl is-active --quiet "${UNIT}"; then
  echo "ultron-dump: OK — ${UNIT} is active"
  systemctl status "${UNIT}" --no-pager -l | head -8
else
  echo "ultron-dump: FAILED — ${UNIT} is not active" >&2
  systemctl status "${UNIT}" --no-pager -l | tail -20 >&2
  exit 1
fi
