#!/usr/bin/env bash
# Apply Ultron code changes: editable reinstall + systemd restart.
#
# Run after ANY source change — manual edits, git pull, or config that needs a
# process restart. Slash commands and scheduled jobs only update after this.
#
# Usage:
#   ./scripts/ultron-dump.sh
#   ULTRON_SYSTEMD_UNIT=ultron.service ./scripts/ultron-dump.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT="${ULTRON_SYSTEMD_UNIT:-ultron.service}"

cd "${ROOT}"

echo "ultron-dump: pip install -e ."
"${ROOT}/.venv/bin/pip" install -q -e .

if [[ -f "${ROOT}/package.json" ]] && command -v npm >/dev/null 2>&1; then
  echo "ultron-dump: npm install (pi-coding-agent)"
  (cd "${ROOT}" && npm install --ignore-scripts --silent)
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
