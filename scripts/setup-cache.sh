#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$project_dir"

if ! command -v uv >/dev/null 2>&1; then
  printf 'Install uv before running this script: https://docs.astral.sh/uv/getting-started/installation/\n' >&2
  exit 1
fi

printf 'Populating uv cache at: %s\n' "$(uv cache dir)"
# Cache production and development dependencies.
uv sync --all-extras
# Cache build dependencies.
uv build

printf 'Checking installation and packaging with networking disabled...\n'
uv sync --offline --locked --all-extras
uv build --offline

printf 'Cache is ready. uv.lock now records the resolved dependencies.\n'
