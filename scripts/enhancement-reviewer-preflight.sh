#!/usr/bin/env bash
# Preflight digest for autoagents 008 (enhancement / repo health reviewer).
set -euo pipefail

ctx="${1:-}"
if [[ -z "$ctx" ]]; then
  echo "Usage: $0 <output-context-file>" >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TASKDIR="$REPO_ROOT/autoagents/tasks"
STAMP_FILE="$REPO_ROOT/autoagents/008-enhancement-reviewer/time-of-last-review.txt"
READONLY="${ENHANCEMENT_PREFLIGHT_READONLY:-1}"

G008_OK=1
G008_DAYS_SINCE_LAST_REVIEW=999
G008_WEEKLY_DUE=0
G008_DOC_DRIFT=0
G008_TASK_SIGNALS=0
G008_TEST_SIGNALS=0
G008_SIGNALS=0

mkdir -p "$(dirname "$ctx")"
utc="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

{
  echo "ultron-agent-loop 008 preflight — $utc (UTC)"
  echo "repo: $REPO_ROOT"
} >"$ctx"

if [[ -f "$STAMP_FILE" ]]; then
  last_line="$(grep -oE '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z' "$STAMP_FILE" | tail -1 || true)"
  if [[ -n "$last_line" ]]; then
    now_epoch=$(date -u +%s)
    last_epoch=$(date -u -d "$last_line" +%s 2>/dev/null || date -u -j -f "%Y-%m-%dT%H:%M:%SZ" "$last_line" +%s 2>/dev/null || echo 0)
    if ((last_epoch > 0)); then
      G008_DAYS_SINCE_LAST_REVIEW=$(( (now_epoch - last_epoch) / 86400 ))
    fi
  fi
fi

(( G008_DAYS_SINCE_LAST_REVIEW >= 7 )) && G008_WEEKLY_DUE=1

shopt -s nullglob
wip_count=0
testing_count=0
for f in "$TASKDIR"/WIP-*.md "$TASKDIR"/TESTING-*.md; do
  bn=$(basename "$f")
  [[ "$bn" == WIP-* ]] && wip_count=$((wip_count + 1))
  [[ "$bn" == TESTING-* ]] && testing_count=$((testing_count + 1))
done
shopt -u nullglob

if ((wip_count + testing_count >= 4)); then
  G008_TASK_SIGNALS=1
  echo "SIGNAL task_backlog wip=$wip_count testing=$testing_count" >>"$ctx"
fi

if [[ -f "$REPO_ROOT/README.md" ]] && [[ -f "$REPO_ROOT/pyproject.toml" ]]; then
  readme_ver=$(grep -oE 'Ultron [0-9]+\.[0-9]+\.[0-9]+' "$REPO_ROOT/README.md" | head -1 || true)
  py_ver=$(grep -E '^version = ' "$REPO_ROOT/pyproject.toml" | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || true)
  init_ver=$(grep -E '__version__' "$REPO_ROOT/ultron/__init__.py" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)
  if [[ -n "$py_ver" && -n "$init_ver" && "$py_ver" != "$init_ver" ]]; then
    G008_DOC_DRIFT=1
    echo "SIGNAL version_mismatch pyproject=$py_ver __init__=$init_ver" >>"$ctx"
  fi
fi

if [[ "$READONLY" != "1" ]] && command -v python3 >/dev/null 2>&1 && [[ -x "$REPO_ROOT/.venv/bin/pytest" ]]; then
  set +e
  test_out=$("$REPO_ROOT/.venv/bin/pytest" -q --tb=no 2>&1)
  test_rc=$?
  set -e
  if ((test_rc != 0)); then
    G008_TEST_SIGNALS=1
    echo "SIGNAL pytest_fail exit=$test_rc" >>"$ctx"
    printf '%s\n' "$test_out" | tail -n 15 >>"$ctx"
  fi
elif [[ "$READONLY" == "1" ]]; then
  echo "(pytest skipped — ENHANCEMENT_PREFLIGHT_READONLY=1)" >>"$ctx"
fi

G008_SIGNALS=$((G008_WEEKLY_DUE + G008_DOC_DRIFT + G008_TASK_SIGNALS + G008_TEST_SIGNALS))

{
  echo ""
  echo "=== Preflight summary ==="
  echo "G008_OK=$G008_OK G008_DAYS_SINCE_LAST_REVIEW=$G008_DAYS_SINCE_LAST_REVIEW G008_WEEKLY_DUE=$G008_WEEKLY_DUE G008_DOC_DRIFT=$G008_DOC_DRIFT G008_TASK_SIGNALS=$G008_TASK_SIGNALS G008_TEST_SIGNALS=$G008_TEST_SIGNALS G008_SIGNALS=$G008_SIGNALS"
} >>"$ctx"
