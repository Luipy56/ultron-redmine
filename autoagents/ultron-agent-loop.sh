#!/usr/bin/env bash
# Ultron autoagents loop orchestrator. Run from repo root:
#   ./autoagents/ultron-agent-loop.sh [COMMAND]
# or:
#   cd autoagents && ./ultron-agent-loop.sh [COMMAND]
#
# Requires: cursor-agent on PATH for steps that invoke it.
# Task dir: autoagents/tasks/ (sibling of this script).

set -euo pipefail

SCRIPTDIR="$(cd "$(dirname "$0")" && pwd)"
TASKDIR="${SCRIPTDIR}/tasks"
REPO_ROOT="$(cd "${SCRIPTDIR}/.." && pwd)"
sleepminutes="${AGENT_LOOP_SLEEP_MINUTES:-5}"
sleepseconds=$((sleepminutes * 60))
_tdir="${TMPDIR:-/tmp}"
_tdir="${_tdir%/}"
AGENT_LOOP_TMP="${AGENT_LOOP_TMP:-${_tdir}/ultron-agent-loop}"
unset _tdir
GH_REPO="${AGENT_GH_REPO:-Luipy56/ultron-redmine}"
LAST_REVIEW_FILE="${SCRIPTDIR}/001-redmine-reviewer/time-of-last-review.txt"
ENH_REVIEW_FILE="${SCRIPTDIR}/008-enhancement-reviewer/time-of-last-review.txt"
INTAKE_PREFLIGHT="${REPO_ROOT}/scripts/redmine-reviewer-preflight.sh"
ENH_PREFLIGHT="${REPO_ROOT}/scripts/enhancement-reviewer-preflight.sh"
# Local stamp (gitignored): last HEAD applied via scripts/ultron-dump.sh from this loop.
LAST_DUMP_SHA_FILE="${SCRIPTDIR}/.last-ultron-dump-sha"
ULTRON_DUMP_SCRIPT="${REPO_ROOT}/scripts/ultron-dump.sh"
# Paths that require pip/npm install + systemd restart when they change on main.
ULTRON_RUNTIME_PATHS=(
  ultron/
  pyproject.toml
  package.json
  package-lock.json
  scripts/ultron-dump.sh
)

cd "$SCRIPTDIR" || exit 1

have_cursor_agent() {
  command -v cursor-agent >/dev/null 2>&1
}

cursor_agent_timeout_seconds_for() {
  local step="${1:-default}"
  if [[ "${AGENT_CURSOR_TIMEOUT:-1}" == "0" ]]; then
    echo 0
    return 0
  fi
  local mins
  case "$step" in
    testing) mins="${AGENT_TESTER_TIMEOUT_MINUTES:-32}" ;;
    feat) mins="${AGENT_FEAT_TIMEOUT_MINUTES:-${AGENT_CURSOR_TIMEOUT_MINUTES:-25}}" ;;
    coding) mins="${AGENT_CODER_TIMEOUT_MINUTES:-${AGENT_CURSOR_TIMEOUT_MINUTES:-25}}" ;;
    handoff) mins="${AGENT_HANDOFF_TIMEOUT_MINUTES:-${AGENT_CURSOR_TIMEOUT_MINUTES:-20}}" ;;
    closing) mins="${AGENT_CLOSING_TIMEOUT_MINUTES:-${AGENT_CURSOR_TIMEOUT_MINUTES:-20}}" ;;
    committer) mins="${AGENT_COMMITTER_TIMEOUT_MINUTES:-${AGENT_CURSOR_TIMEOUT_MINUTES:-20}}" ;;
    intake|enhancement) mins="${AGENT_REVIEWER_TIMEOUT_MINUTES:-${AGENT_CURSOR_TIMEOUT_MINUTES:-25}}" ;;
    *) mins="${AGENT_CURSOR_TIMEOUT_MINUTES:-25}" ;;
  esac
  echo $((mins * 60))
}

invoke_cursor_agent() {
  local step="$1"
  shift
  local secs
  secs=$(cursor_agent_timeout_seconds_for "$step")
  if ((secs <= 0)); then
    cursor-agent "$@"
    return $?
  fi
  local mins=$((secs / 60))
  echo "cursor-agent wall-clock limit: ${mins}m (${secs}s, step=${step})" >&2
  if command -v timeout >/dev/null 2>&1; then
    timeout --preserve-status --foreground "$secs" cursor-agent "$@"
    return $?
  fi
  cursor-agent "$@" &
  local pid=$!
  local waited=0
  while kill -0 "$pid" 2>/dev/null && ((waited < secs)); do
    sleep 1
    waited=$((waited + 1))
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill -TERM "$pid" 2>/dev/null || true
    sleep 3
    kill -KILL "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
    return 124
  fi
  wait "$pid"
}

sync_repo() {
  if [[ "${AGENT_GIT_SYNC:-1}" == "0" ]]; then
    echo "----- git sync (skip: AGENT_GIT_SYNC=0)"
    return 0
  fi
  echo "-----> git sync main $(date "+%Y-%m-%d %H:%M:%S") <----"
  if ! bash "${REPO_ROOT}/scripts/git-sync-main.sh"; then
    echo "ERROR: git sync failed." >&2
    return 1
  fi
}

any_root_task_glob() {
  shopt -s nullglob
  local g matches
  for g in "$@"; do
    matches=("$TASKDIR"/$g)
    if (( ${#matches[@]} > 0 )); then
      shopt -u nullglob
      return 0
    fi
  done
  shopt -u nullglob
  return 1
}

has_uncommitted_changes() {
  ( cd "$REPO_ROOT" && { ! git diff --quiet 2>/dev/null || ! git diff --staged --quiet 2>/dev/null; } )
}

committer_changed_paths() {
  ( cd "$REPO_ROOT" && {
    git diff --name-only HEAD 2>/dev/null || true
    git ls-files --others --exclude-standard 2>/dev/null || true
  } | sort -u )
}

committer_paths_all_stamp_allowlist() {
  local f had=0
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    had=1
    case "$f" in
      autoagents/001-redmine-reviewer/time-of-last-review.txt) ;;
      autoagents/008-enhancement-reviewer/time-of-last-review.txt) ;;
      *) return 1 ;;
    esac
  done < <(committer_changed_paths)
  ((had == 1))
}

committer_try_local_stamp_only() {
  [[ "${AGENT_COMMITTER_LOCAL:-1}" == "0" ]] && return 1
  local br
  br=$(cd "$REPO_ROOT" && git rev-parse --abbrev-ref HEAD 2>/dev/null) || return 1
  [[ "$br" == "main" ]] || return 1
  committer_paths_all_stamp_allowlist || return 1
  (
    cd "$REPO_ROOT" || exit 1
    git add -- autoagents/001-redmine-reviewer/time-of-last-review.txt
    git add -- autoagents/008-enhancement-reviewer/time-of-last-review.txt 2>/dev/null || true
    git diff --staged --quiet && exit 1
    git commit -m "chore(autoagents): update reviewer time-of-last-review stamps"
    git pull --rebase --autostash origin main
    git push origin main
  )
}

run_agent() {
  local desc="$1" cond="$2" prompt="$3" msg="$4"
  local step="${5:-default}"
  local p="${SCRIPTDIR}/${prompt}"
  if [[ ! -f "$p" ]]; then
    echo "----- $desc (skip: missing prompt $prompt)"
    return 0
  fi
  if ! have_cursor_agent; then
    echo "----- $desc (skip: cursor-agent not on PATH)"
    return 0
  fi
  if eval "$cond" 2>/dev/null; then
    echo "-----> $desc $(date "+%Y-%m-%d %H:%M:%S") <----"
    set +e
    invoke_cursor_agent "$step" --yolo -p "$prompt" "$msg"
    local _rc=$?
    set -e
    if ((_rc == 124)); then
      echo "----- $desc: cursor-agent TIMED OUT (continuing loop)" >&2
    elif ((_rc != 0)); then
      echo "----- $desc: cursor-agent exited ${_rc} (continuing loop)" >&2
    fi
  else
    echo "----- $desc (skip: nothing to do)"
  fi
  echo "<-- end of $desc -->"
  echo ""
}

prepare_001_preflight_context() {
  local ctx="$1"
  G001_REDMINE_OK=0
  G001_GH_OK=0
  G001_GH_AUTH_FAILED=0
  G001_UNTRACKED_REDMINE=0
  G001_UNTRACKED_GH=0
  G001_LOG_SIGNALS=0
  if [[ -x "$INTAKE_PREFLIGHT" ]]; then
    bash "$INTAKE_PREFLIGHT" "$ctx" || true
    local line
    while IFS= read -r line; do
      case "$line" in
        G001_*) eval "$line" 2>/dev/null || true ;;
      esac
    done < <(grep -E '^G001_' "$ctx" 2>/dev/null || true)
  else
    echo "001 preflight script missing: $INTAKE_PREFLIGHT" >"$ctx"
  fi
}

should_run_001_cursor_agent() {
  [[ "${AGENT_INTAKE_REVIEWER_ALWAYS:-0}" == "1" ]] && return 0
  [[ "${AGENT_001_SKIP_PREFLIGHT:-0}" == "1" ]] && return 0
  if [[ "${G001_UNTRACKED_REDMINE:-0}" -gt 0 ]] || [[ "${G001_UNTRACKED_GH:-0}" -gt 0 ]]; then
    return 0
  fi
  [[ "$G001_LOG_SIGNALS" == "1" ]] && return 0
  return 1
}

prepare_008_preflight_context() {
  local ctx="$1"
  G008_OK=0
  G008_SIGNALS=0
  G008_DAYS_SINCE_LAST_REVIEW=999
  if [[ -x "$ENH_PREFLIGHT" ]]; then
    ENHANCEMENT_PREFLIGHT_READONLY="${2:-1}" bash "$ENH_PREFLIGHT" "$ctx" || true
    local line
    while IFS= read -r line; do
      case "$line" in
        G008_*) eval "$line" 2>/dev/null || true ;;
      esac
    done < <(grep -E '^G008_' "$ctx" 2>/dev/null || true)
  fi
}

should_run_008_cursor_agent() {
  [[ "${AGENT_ENHANCEMENT_REVIEWER_ALWAYS:-0}" == "1" ]] && return 0
  [[ "${AGENT_008_SKIP_PREFLIGHT:-0}" == "1" ]] && return 0
  [[ "${G008_OK:-0}" != "1" ]] && return 1
  (( G008_SIGNALS > 0 ))
}

step_intake_reviewer() {
  echo "-----> intake reviewer (001) <----"
  mkdir -p "$AGENT_LOOP_TMP"
  local ctx="${AGENT_LOOP_TMP}/001-latest-context.txt"
  prepare_001_preflight_context "$ctx"
  echo "----- 001 preflight digest: $ctx"
  if [[ "${G001_GH_AUTH_FAILED:-0}" == "1" ]]; then
    echo "!!! 001 / GitHub: auth failed — GH queue may be incomplete !!!" >&2
  fi
  if should_run_001_cursor_agent; then
    if ! have_cursor_agent; then
      echo "----- intake (001) (skip: cursor-agent not on PATH)" >&2
      return 0
    fi
    if ! sync_repo; then
      echo "----- intake (001) (skip: git sync failed)" >&2
      return 0
    fi
    prepare_001_preflight_context "$ctx"
    local msg
    msg="Run 001: Read preflight digest: $ctx
Then follow 001-redmine-reviewer.md — Redmine/GH → up to 3 × FEAT-*.md; logs → NEW-0-*.md. Task conventions: autoagents/TASKS-README.md."
    run_agent "intake reviewer (001)" "true" "001-redmine-reviewer.md" "$msg" "intake"
  else
    echo "----- intake (001) (skip: no untracked issues or log signals)"
    echo "----- Override: AGENT_INTAKE_REVIEWER_ALWAYS=1 or AGENT_001_SKIP_PREFLIGHT=1"
  fi
}

step_enhancement_reviewer() {
  echo "-----> enhancement reviewer (008) <----"
  local ctx="${AGENT_LOOP_TMP}/008-latest-context.txt"
  prepare_008_preflight_context "$ctx"
  echo "----- 008 preflight digest: $ctx"
  if should_run_008_cursor_agent; then
    if ! sync_repo; then return 0; fi
    local msg="Run 008: Read digest: $ctx
Follow 008-enhancement-reviewer.md — up to 3 FEAT-0 / NEW-0 tasks. TASKS-README.md."
    run_agent "enhancement reviewer (008)" "true" "008-enhancement-reviewer.md" "$msg" "enhancement"
    ENHANCEMENT_PREFLIGHT_READONLY=0 bash "$ENH_PREFLIGHT" "$ctx" >/dev/null 2>&1 || true
  else
    echo "----- enhancement (008) (skip: weekly not due and no signals; days=${G008_DAYS_SINCE_LAST_REVIEW:-?})"
    ENHANCEMENT_PREFLIGHT_READONLY=0 bash "$ENH_PREFLIGHT" "$ctx" >/dev/null 2>&1 || true
  fi
}

step_feat() {
  any_root_task_glob 'FEAT-*.md' || { echo "----- feature coding (skip: no FEAT-*.md)"; return 0; }
  sync_repo || return 0
  run_agent "feature coding (FEAT)" "any_root_task_glob 'FEAT-*.md'" "010-feature-coder.md" \
    "Start feature coding. Pick a FEAT task. Do your job." "feat"
}

step_feature_coder_handoff() {
  any_root_task_glob 'WIP-*.md' || { echo "----- handoff (012) (skip: no WIP-*.md)"; return 0; }
  sync_repo || return 0
  run_agent "feature coder handoff (012)" "any_root_task_glob 'WIP-*.md'" "012-feature-coder-handoff.md" \
    "Handoff: WIP → UNTESTED when complete per TASKS-README.md." "handoff"
}

step_coder() {
  any_root_task_glob 'NEW-*.md' 'WIP-*.md' || { echo "----- coding (skip: no NEW/WIP)"; return 0; }
  sync_repo || return 0
  run_agent "coding (NEW/WIP)" "any_root_task_glob 'NEW-*.md' 'WIP-*.md'" "002-coder/CODER.md" \
    "Start coding. Prefer NEW; rename to WIP on start; finish to UNTESTED." "coding"
}

step_tester() {
  any_root_task_glob 'UNTESTED-*.md' 'TESTING-*.md' || { echo "----- testing (skip)"; return 0; }
  sync_repo || return 0
  run_agent "testing" "any_root_task_glob 'UNTESTED-*.md' 'TESTING-*.md'" "020-test.md" \
    "Start testing. UNTESTED → TESTING → CLOSED or WIP." "testing"
}

step_closing_review() {
  any_root_task_glob 'CLOSED-*.md' || { echo "----- closing (skip: no CLOSED-*.md)"; return 0; }
  sync_repo || return 0
  run_agent "closing reviewer" "any_root_task_glob 'CLOSED-*.md'" "030-closing-reviewer.md" \
    "Process CLOSED-*.md; prepend summary; move to done/ with scripts/move-agent-task-to-done.sh." "closing"
}

step_committer() {
  has_uncommitted_changes || { echo "----- committer (skip: clean tree)"; return 0; }
  sync_repo || return 0
  has_uncommitted_changes || { echo "----- committer (skip after sync: clean)"; return 0; }
  if [[ "${AGENT_COMMITTER_LOCAL:-1}" != "0" ]] && committer_try_local_stamp_only; then
    echo "----- committer (local stamp-only push)"
    return 0
  fi
  run_agent "committer" "has_uncommitted_changes" "040-committer.md" \
    "Run 040-committer on main. Commit when ready; bump pyproject + __init__ version; push origin main. Do not run ultron-dump.sh — the orchestrator does that after committer." "committer"
}

ultron_runtime_diff_quiet() {
  # Exit 0 if no runtime-path diff between the two commits (or vs working tree).
  local from="$1"
  local to="${2:-}"
  (
    cd "$REPO_ROOT" || exit 0
    if [[ -n "$to" ]]; then
      git diff --quiet "$from" "$to" -- "${ULTRON_RUNTIME_PATHS[@]}"
    else
      git diff --quiet "$from" -- "${ULTRON_RUNTIME_PATHS[@]}"
    fi
  )
}

ultron_needs_dump() {
  local head
  head=$(cd "$REPO_ROOT" && git rev-parse HEAD 2>/dev/null) || return 1
  if [[ ! -f "$LAST_DUMP_SHA_FILE" ]]; then
    # No stamp yet: dump if HEAD itself has Ultron runtime files (always true) —
    # first loop after deploy catch-up when code may already be ahead of systemd.
    return 0
  fi
  local prev
  prev=$(tr -d '[:space:]' <"$LAST_DUMP_SHA_FILE")
  [[ -z "$prev" ]] && return 0
  if ! (cd "$REPO_ROOT" && git cat-file -e "${prev}^{commit}" 2>/dev/null); then
    return 0
  fi
  [[ "$prev" == "$head" ]] && return 1
  if ultron_runtime_diff_quiet "$prev" "$head"; then
    return 1
  fi
  return 0
}

step_ultron_dump() {
  if [[ "${AGENT_ULTRON_DUMP:-1}" == "0" ]]; then
    echo "----- ultron dump (skip: AGENT_ULTRON_DUMP=0)"
    return 0
  fi
  if [[ ! -x "$ULTRON_DUMP_SCRIPT" ]] && [[ ! -f "$ULTRON_DUMP_SCRIPT" ]]; then
    echo "----- ultron dump (skip: missing $ULTRON_DUMP_SCRIPT)" >&2
    return 0
  fi
  if ! ultron_needs_dump; then
    echo "----- ultron dump (skip: no runtime changes since last dump)"
    return 0
  fi
  echo "-----> ultron dump $(date "+%Y-%m-%d %H:%M:%S") <----"
  if bash "$ULTRON_DUMP_SCRIPT"; then
    (cd "$REPO_ROOT" && git rev-parse HEAD) >"$LAST_DUMP_SHA_FILE"
    echo "----- ultron dump: stamped $(tr -d '[:space:]' <"$LAST_DUMP_SHA_FILE")"
  else
    echo "ERROR: ultron-dump.sh failed — systemd may still be on an old revision." >&2
    return 1
  fi
}

run_full_cycle() {
  echo "$(date)"
  step_intake_reviewer
  step_enhancement_reviewer
  local i=0
  while (( i < 5 )); do
    any_root_task_glob 'FEAT-*.md' || break
    step_feat
    ((i++)) || true
  done
  step_coder
  step_feature_coder_handoff
  step_tester
  step_closing_review
  step_committer
  # After commits (or a pull that brought runtime changes), reinstall + restart.
  step_ultron_dump || true
}

# One-shot for /upgrade: implement FEAT → handoff → test → close (no intake/enhancement/committer).
# Prefer AGENT_GIT_SYNC=0 when Ultron just created the FEAT file locally.
run_upgrade_shot() {
  echo "-----> autoagents upgrade shot $(date "+%Y-%m-%d %H:%M:%S") <----"
  if ! any_root_task_glob 'FEAT-*.md' 'WIP-*.md' 'UNTESTED-*.md' 'TESTING-*.md'; then
    echo "----- upgrade shot (skip: no FEAT/WIP/UNTESTED/TESTING tasks)" >&2
    return 1
  fi
  # Prefer the FEAT queue first (typical /upgrade path).
  if any_root_task_glob 'FEAT-*.md'; then
    step_feat
  fi
  if any_root_task_glob 'WIP-*.md'; then
    # Coder may leave WIP; handoff promotes complete work to UNTESTED.
    step_feature_coder_handoff
    # If still WIP (incomplete handoff), main coder can finish NEW/WIP → UNTESTED.
    if any_root_task_glob 'WIP-*.md' 'NEW-*.md'; then
      step_coder
      step_feature_coder_handoff
    fi
  fi
  if any_root_task_glob 'UNTESTED-*.md' 'TESTING-*.md'; then
    step_tester
  fi
  if any_root_task_glob 'CLOSED-*.md'; then
    step_closing_review
  fi
  echo "----- upgrade shot finished $(date "+%Y-%m-%d %H:%M:%S")"
}

usage() {
  cat >&2 <<EOF
Usage: $(basename "$0") [COMMAND]

  (no args)     Full cycle every ${AGENT_LOOP_SLEEP_MINUTES:-5} minutes.

  Single run:
    intake, 001       Redmine / GH / log intake reviewer
    enhancement, 008  Enhancement reviewer
    feat, feature     Feature coder (FEAT-*.md)
    coder             Main coder (NEW- / WIP-)
    handoff, 012      WIP → UNTESTED handoff
    tester            Tester
    closing-review    Closing reviewer
    committer         Commit when tree dirty
    dump, ultron-dump Reinstall + systemctl restart when Ultron runtime paths changed
    shot, upgrade-shot  FEAT→handoff→tester→closing (used by Discord /upgrade)

Environment: AGENT_LOOP_SLEEP_MINUTES, AGENT_GIT_SYNC, AGENT_GH_REPO (default Luipy56/ultron-redmine),
  AGENT_ULTRON_DUMP (default 1; set 0 to skip dump/restart), AGENT_INTAKE_REVIEWER_ALWAYS,
  AGENT_ENHANCEMENT_REVIEWER_ALWAYS, AGENT_CURSOR_TIMEOUT_MINUTES, …

See docs/agent-loop.md and autoagents/TASKS-README.md.
EOF
}

if [[ -n "${1:-}" ]]; then
  case "$1" in
    help|-h|--help) usage; exit 0 ;;
    intake|001) step_intake_reviewer ;;
    enhancement|008) step_enhancement_reviewer ;;
    feat|feature) step_feat ;;
    coder) step_coder ;;
    handoff|012) step_feature_coder_handoff ;;
    tester) step_tester ;;
    closing-review|closing) step_closing_review ;;
    committer) step_committer ;;
    dump|ultron-dump) step_ultron_dump ;;
    shot|upgrade-shot) run_upgrade_shot ;;
    *) usage; exit 1 ;;
  esac
  exit 0
fi

if ! have_cursor_agent; then
  echo "cursor-agent not found on PATH." >&2
  exit 1
fi

while true; do
  run_full_cycle
  echo "----- sleeping ${sleepminutes}m; next cycle ~ $(date -d "+${sleepseconds} seconds" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || date)"
  sleep "$sleepseconds"
done
