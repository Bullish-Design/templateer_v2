#!/usr/bin/env bash
# A2 + A3 -- guard closed languages and make an empty audit visible.
#
# Round 2 changes both outcomes. Case 1 reports no fixtures and exits 1.
# Case 2 rejects the unknown language during template loading and exits 3.
#
# Run from the repo root:  bash .scratch/projects/06-adversarial-review/probes/p_audit_vacuous.sh
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
PY="$REPO/.venv/bin/python"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$WORK/templates/noex"
cat > "$WORK/templates/noex/metadata.yml" <<'EOF'
name: noex
description: unquoted interpolation, no fixtures.
output: {path: o.toml, language: toml}
schema: {module: schema, class: M}
prompt: {file: p.md}
renderer: {engine: minijinja, file: t.j2}
EOF
printf 'from pydantic import BaseModel\nclass M(BaseModel):\n    x: str\n' > "$WORK/templates/noex/schema.py"
echo p > "$WORK/templates/noex/p.md"
printf 'name = {{ x }}\n' > "$WORK/templates/noex/t.j2"   # deliberately UNQUOTED

cd "$WORK" || exit 1
export PYTHONPATH="$REPO/src"

echo "--- case 1: template has an unquoted interpolation and no examples/ ---"
"$PY" -m templateer.cli check noex; echo "exit=$?"

echo
echo "--- case 2: language the auditor and the escaper both skip ---"
sed -i 's/language: toml/language: nix/' templates/noex/metadata.yml
mkdir -p templates/noex/examples
echo '{"x": "hi"}' > templates/noex/examples/a.input.json
"$PY" -m templateer.cli check noex; echo "exit=$?"

echo
echo '  Round 2 makes both silent cases visible and non-zero.'
