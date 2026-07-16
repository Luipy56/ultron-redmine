#!/usr/bin/env bash
# Move a CLOSED- task into autoagents/tasks/done/YYYY/MM/DD/ using the YYYYMMDD segment in the filename.
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 autoagents/tasks/CLOSED-<ISSUE-ID>-YYYYMMDD-HHMM-<slug>.md" >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
src="$1"
bn="$(basename "$src")"

if [[ "$bn" != CLOSED-* ]]; then
  echo "Only CLOSED-*.md files are accepted." >&2
  exit 1
fi

# CLOSED-<issue>-YYYYMMDD-HHMM-slug.md → extract YYYYMMDD after second hyphen group
rest="${bn#CLOSED-}"
issue_part="${rest%%-*}"
date_rest="${rest#${issue_part}-}"
ymd="${date_rest%%-*}"

if [[ ! "$ymd" =~ ^[0-9]{8}$ ]]; then
  echo "Could not parse YYYYMMDD from filename: $bn" >&2
  exit 1
fi

yyyy="${ymd:0:4}"
mm="${ymd:4:2}"
dd="${ymd:6:2}"

if [[ ! "$src" = /* ]]; then
  src="$REPO_ROOT/$src"
fi

if [[ ! -f "$src" ]]; then
  echo "File not found: $src" >&2
  exit 1
fi

dest_dir="$REPO_ROOT/autoagents/tasks/done/$yyyy/$mm/$dd"
mkdir -p "$dest_dir"
mv "$src" "$dest_dir/$bn"
echo "Moved to $dest_dir/$bn"
