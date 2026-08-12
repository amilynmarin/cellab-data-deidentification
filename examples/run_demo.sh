#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEMO_ROOT="${1:-$(mktemp -d "${TMPDIR:-/tmp}/research-deid-demo.XXXXXX")}" 
CONTROL="$DEMO_ROOT/control"
RELEASE="$DEMO_ROOT/release"
mkdir -p "$CONTROL" "$RELEASE"

research-deid schema validate "$ROOT/examples/example_schema.yaml"
research-deid key generate \
  --output "$CONTROL/example.key.json" \
  --collaboration-id example-collaboration \
  --alias example-key \
  --version 1
research-deid run \
  --input "$ROOT/examples/example_input.csv" \
  --schema "$ROOT/examples/example_schema.yaml" \
  --key-file "$CONTROL/example.key.json" \
  --output-dir "$RELEASE" \
  --archive

printf 'Demo control material: %s\n' "$CONTROL"
printf 'Demo release: %s\n' "$RELEASE"
