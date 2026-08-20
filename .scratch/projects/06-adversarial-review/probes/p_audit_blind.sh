#!/usr/bin/env bash
# A1 + A3 -- a valid model produces a type-corrupted artifact, and every
# layer reports success: render, output validation, and `templateer check`.
#
# NOW GUARDS (round-2 remediation):
#   A1 lint  -> tests/test_audit.py::test_lint_flags_an_unquoted_interpolation_site
#   A1 check -> tests/test_round_trip.py::test_yaml_str_field_reaching_artifact_as_bool_is_reported
#   A3       -> tests/test_audit.py::test_audit_flags_a_vulnerable_template
#
# check_round_trip needs the model to compare the artifact against, so the
# validate_artifact call below passes model_data.  Without it the API cannot
# know what type the schema declared.
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
model_data = json.load(open("corrupt.json"))
out = r.render_from_model("yamlvuln", model_data)
print("  artifact          :", repr(out))
print("  yaml.safe_load    :", yaml.safe_load(out))
print("  validate_artifact :", r.validate_artifact("yamlvuln", out))
print("  ... with model    :", r.validate_artifact("yamlvuln", out, model_data))
print()
print("  schema declared title: str, owner: str")
print("  artifact carries      title: bool, owner: None")
PYEOF
