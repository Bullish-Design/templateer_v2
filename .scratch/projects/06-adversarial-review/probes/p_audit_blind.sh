#!/usr/bin/env bash
# A1 + A3 -- a valid model produces a type-corrupted artifact, and every
# layer reports success: render, output validation, and `templateer check`.
#
# Run from the repo root:  bash .scratch/projects/06-adversarial-review/probes/p_audit_blind.sh
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
PY="$REPO/.venv/bin/python"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$WORK/templates/yamlvuln/examples"
cat > "$WORK/templates/yamlvuln/metadata.yml" <<'EOF'
name: yamlvuln
description: Schema says str; the artifact disagrees.
output:
  path: out.yaml
  language: yaml
schema: {module: schema, class: M}
prompt: {file: prompt.md}
renderer: {engine: minijinja, file: template.j2}
EOF
cat > "$WORK/templates/yamlvuln/schema.py" <<'EOF'
from pydantic import BaseModel
class M(BaseModel):
    title: str
    owner: str
EOF
echo "fill it" > "$WORK/templates/yamlvuln/prompt.md"
printf 'title: {{ title }}\nowner: {{ owner }}\n' > "$WORK/templates/yamlvuln/template.j2"
echo '{"title": "Status", "owner": "andrew"}' > "$WORK/templates/yamlvuln/examples/basic.input.json"
echo '{"title": "true", "owner": "#redacted"}' > "$WORK/corrupt.json"

cd "$WORK" || exit 1
export PYTHONPATH="$REPO/src"

echo "--- templateer check ---"
"$PY" -m templateer.cli check yamlvuln; echo "exit=$?"

echo
echo "--- templateer render (with the corrupting model) ---"
"$PY" -m templateer.cli render yamlvuln -i corrupt.json; echo "exit=$?"

echo
echo "--- what the consumer actually parses ---"
"$PY" - <<'PYEOF'
import json, sys, yaml
from templateer.api import TemplateRegistry
r = TemplateRegistry.from_paths(["templates"])
out = r.render_from_model("yamlvuln", json.load(open("corrupt.json")))
print("  artifact          :", repr(out))
print("  yaml.safe_load    :", yaml.safe_load(out))
print("  validate_artifact :", r.validate_artifact("yamlvuln", out))
print()
print("  schema declared title: str, owner: str")
print("  artifact carries      title: bool, owner: None")
PYEOF
