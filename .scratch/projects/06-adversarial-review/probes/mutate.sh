#!/usr/bin/env bash
# A3 -- mutation test: replace each safety control with a no-op and re-run
# the suite.  A control whose mutant stays green has no negative coverage.
#
# Run from the repo root:  bash .scratch/projects/06-adversarial-review/probes/mutate.sh
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
cd "$REPO" || exit 1
PY="$REPO/.venv/bin/python"

mutate () {   # $1 = file  $2 = anchor line  $3 = label
  cp "$1" "$1.bak"
  "$PY" - "$1" "$2" <<'PYEOF'
import sys
path, anchor = sys.argv[1], sys.argv[2]
src = open(path).read()
assert anchor in src, "anchor not found: " + anchor
open(path, "w").write(src.replace(anchor, "    return []  # MUTANT\n" + anchor, 1))
PYEOF
  printf '%-38s ' "$3"
  "$PY" -m pytest -q 2>&1 | tail -1
  mv "$1.bak" "$1"
}

echo "baseline:"
printf '%-38s ' "(unmutated)"; "$PY" -m pytest -q 2>&1 | tail -1
echo
echo "mutants:"
mutate src/templateer/audit.py \
  '    language = template.metadata.output.language' \
  'audit_template -> return []'
mutate src/templateer/validators.py \
  '    body, fence_errors = _extract_fenced_body(artifact)' \
  'validate_region_payload -> return []'
echo
echo "restored:"
printf '%-38s ' "(unmutated)"; "$PY" -m pytest -q 2>&1 | tail -1
