#!/usr/bin/env bash
set -euo pipefail

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_dir"

if command -v asciinema >/dev/null; then
  recorder=(asciinema)
elif command -v uvx >/dev/null; then
  recorder=(uvx asciinema)
else
  echo "asciinema or uvx is required to record the terminal demo" >&2
  exit 2
fi
mkdir -p artifacts
output=${STAGE7_DEMO_RECORDING:-artifacts/stage7-demo.cast}
mkdir -p "$(dirname -- "$output")"
"${recorder[@]}" rec \
  --overwrite \
  --title "AI-SRE Copilot V1 stage-7 demo" \
  --command ./demo/run-stage7.sh \
  "$output"
echo "recording: $output"
