#!/usr/bin/env bash
# A2 + A3 -- `templateer check` prints a proof after auditing nothing.
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
echo '  In both cases audit_template() probed zero fields and check reported a'
echo "  pass.  The concept doc's motivating list opens with devenv.nix."
