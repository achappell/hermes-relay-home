#!/usr/bin/env bash
set -euo pipefail

mode="${1:-apply}"
if [[ "$mode" != "apply" && "$mode" != "--check" ]]; then
  printf '%s\n' "Usage: $0 [--check]" >&2
  exit 2
fi

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
source_root="$repo_root/_bmad/custom/home-issue-tracking/workflows"
target_root="$repo_root/_bmad/_config/custom/workflows"

if [[ ! -d "$source_root" ]]; then
  printf '%s\n' "Home workflow sources are missing: $source_root" >&2
  exit 1
fi

stale=0
applied=0
while IFS= read -r -d '' source_file; do
  relative_path="${source_file#"${source_root}"/}"
  target_file="$target_root/$relative_path"

  if [[ "$mode" == "--check" ]]; then
    if ! cmp -s "$source_file" "$target_file"; then
      printf '%s\n' "Stale Home workflow copy: $relative_path" >&2
      stale=1
    fi
    continue
  fi

  mkdir -p "$(dirname "$target_file")"
  if ! cmp -s "$source_file" "$target_file"; then
    cp "$source_file" "$target_file"
    printf '%s\n' "Applied Home workflow: $relative_path"
    applied=1
  fi
done < <(find "$source_root" -type f -name '*.yaml' -print0)

if [[ "$mode" == "--check" ]]; then
  exit "$stale"
fi

if [[ "$applied" == "0" ]]; then
  printf '%s\n' "Home issue-tracking workflows are already current."
fi
