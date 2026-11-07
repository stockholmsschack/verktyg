#!/usr/bin/env bash
# Pull a newer version of a standings tool into its subtree under tools/.
# Usage: scripts/update-tool.sh <tool> <branch>
#   e.g. scripts/update-tool.sh jgp jgp
#        scripts/update-tool.sh flickornas main
# <branch> is the tool repository's branch; the merge lands on the portal branch checked out here.
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$project_dir"

if [[ $# -ne 2 ]]; then
  printf 'Usage: %s <jgp|flickornas|senior> <branch>\n' "$0" >&2
  exit 1
fi

tool="$1"
branch="$2"
case "$tool" in
  jgp)        repository=stsf_jgp_standings ;;
  flickornas) repository=stsf_flickornas_standings ;;
  senior)     repository=stsf_senior_gp ;;
  *)
    printf 'Unknown tool: %s (expected jgp, flickornas or senior)\n' "$tool" >&2
    exit 1
    ;;
esac

if ! git diff-index --quiet HEAD --; then
  printf 'Commit or set aside your changes to tracked files first; git subtree refuses to pull over them.\n' >&2
  exit 1
fi

url="git@github.com:bganesh/${repository}.git"
if [[ -d "tools/$tool" ]]; then
  git subtree pull --prefix="tools/$tool" "$url" "$branch" -m "Update $tool from its $branch branch"
else
  git subtree add --prefix="tools/$tool" "$url" "$branch" -m "Add $tool from its $branch branch"
fi
