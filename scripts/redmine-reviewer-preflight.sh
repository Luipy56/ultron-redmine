#!/usr/bin/env bash
# Preflight digest for autoagents 001 (Redmine + optional GitHub + ultron.log heuristics).
set -euo pipefail

ctx="${1:-}"
if [[ -z "$ctx" ]]; then
  echo "Usage: $0 <output-context-file>" >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TASKDIR="$REPO_ROOT/autoagents/tasks"
GH_REPO="${AGENT_GH_REPO:-Luipy56/ultron-redmine}"
LAST_REVIEW_FILE="$REPO_ROOT/autoagents/001-redmine-reviewer/time-of-last-review.txt"
LOG_FILE="${ULTRON_LOG_FILE:-$REPO_ROOT/ultron.log}"

G001_REDMINE_OK=0
G001_GH_OK=0
G001_GH_AUTH_FAILED=0
G001_UNTRACKED_REDMINE=0
G001_UNTRACKED_GH=0
G001_LOG_SIGNALS=0

mkdir -p "$(dirname "$ctx")"
utc="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

{
  echo "ultron-agent-loop 001 preflight — $utc (UTC)"
  echo "repo: $REPO_ROOT  tasks: $TASKDIR"
  echo "gh_repo: $GH_REPO"
} >"$ctx"

issue_linked_in_tasks() {
  local num="$1"
  local f bn
  shopt -s nullglob
  for f in "$TASKDIR"/*.md; do
    bn=$(basename "$f")
    [[ "$bn" == "README.md" ]] && continue
    if grep -qE "#${num}([^0-9]|$)|/issues/${num}([^0-9]|$)|Redmine:.*#${num}([^0-9]|$)" "$f" 2>/dev/null; then
      shopt -u nullglob
      return 0
    fi
  done
  shopt -u nullglob
  return 1
}

{
  echo ""
  echo "=== Redmine (open issues) ==="
} >>"$ctx"

if command -v python3 >/dev/null 2>&1 && [[ -f "$REPO_ROOT/autoagents/issue_checker_redmine.py" ]]; then
  set +e
  out=$(cd "$REPO_ROOT" && python3 autoagents/issue_checker_redmine.py --dry-run 2>&1)
  rc=$?
  set -e
  printf '%s\n' "$out" >>"$ctx"
  if ((rc == 0)); then
    G001_REDMINE_OK=1
    G001_UNTRACKED_REDMINE=$(printf '%s\n' "$out" | grep -c '^  QUEUE #' || true)
  fi
else
  echo "(python3 or issue_checker_redmine.py missing)" >>"$ctx"
fi

{
  echo ""
  echo "=== GitHub (open issues, limit 20) ==="
} >>"$ctx"

gh_stderr="$(mktemp)"
trap 'rm -f "$gh_stderr"' EXIT
if command -v gh >/dev/null 2>&1; then
  if gh issue list --repo "$GH_REPO" --state open -L 20 --json number,title,url,updatedAt >>"$ctx" 2>"$gh_stderr"; then
    G001_GH_OK=1
    while IFS= read -r num; do
      [[ -z "${num:-}" ]] && continue
      if ! issue_linked_in_tasks "$num"; then
        G001_UNTRACKED_GH=$((G001_UNTRACKED_GH + 1))
        echo "UNTRACKED_GH issue #$num" >>"$ctx"
      fi
    done < <(gh issue list --repo "$GH_REPO" --state open -L 20 --json number -q '.[].number' 2>/dev/null || true)
  else
    {
      echo "(gh issue list failed)"
      cat "$gh_stderr" 2>/dev/null || true
    } >>"$ctx"
    if grep -qiE '401|not authenticated|Bad credentials' "$gh_stderr" 2>/dev/null; then
      G001_GH_AUTH_FAILED=1
    fi
  fi
else
  echo "(gh not on PATH)" >>"$ctx"
fi

{
  echo ""
  echo "=== Ultron log incident heuristics ($LOG_FILE) ==="
} >>"$ctx"

if [[ -f "$LOG_FILE" ]]; then
  raw="$(tail -n 800 "$LOG_FILE" 2>/dev/null || true)"
  hits=$(printf '%s\n' "$raw" | grep -iE \
    'traceback|Exception|ERROR|CRITICAL|Redmine rejected|401 Unauthorized|discord\.errors|LoginFailure' \
    | grep -viE 'DEBUG|heartbeat' | head -n 80 || true)
  if [[ -n "$hits" ]]; then
    G001_LOG_SIGNALS=1
    printf '%s\n' "$hits" >>"$ctx"
  else
    echo "(no heuristic matches in sampled window)" >>"$ctx"
  fi
else
  echo "(log file not found — skipped)" >>"$ctx"
fi

{
  echo ""
  echo "=== Preflight summary ==="
  echo "G001_REDMINE_OK=$G001_REDMINE_OK G001_GH_OK=$G001_GH_OK G001_GH_AUTH_FAILED=$G001_GH_AUTH_FAILED G001_UNTRACKED_REDMINE=$G001_UNTRACKED_REDMINE G001_UNTRACKED_GH=$G001_UNTRACKED_GH G001_LOG_SIGNALS=$G001_LOG_SIGNALS"
} >>"$ctx"
