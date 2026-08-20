#!/usr/bin/env bash
# A1 + A3 + A5 + B1 + B4 -- replace each safety control with a no-op and re-run
# the suite.  A control whose mutant stays green has no negative coverage.
#
# Gate 2 of the round-2 remediation: every mutated control must turn the
# suite red.  At f3b9193 the audit mutant stayed green -- 257/257 passed with
# audit_template replaced by `return []`.  That is the finding this gate
# closes.
#
# Run from the repo root:  bash .scratch/projects/06-adversarial-review/probes/mutate.sh
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
cd "$REPO" || exit 1
PY="$REPO/.venv/bin/python"

# A mutant must fail because a control is gone, never because the file no
# longer parses.  Both helpers below check that the mutated file still
# imports; a SyntaxError means the mutant is invalid, not that the suite
# caught anything.
_run_mutant () {   # $1 = file  $2 = label
  if ! "$PY" -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" "$1" 2>/dev/null
  then
    printf '%-46s ' "$2"; echo "INVALID MUTANT -- file no longer parses"
    return
  fi
  printf '%-46s ' "$2"
  "$PY" -m pytest -q 2>&1 | tail -1
}

# mutate FILE ANCHOR LABEL BODY
#   Insert BODY immediately before ANCHOR, so the function returns early.
mutate () {
  cp "$1" "$1.bak"
  "$PY" - "$1" "$2" "$4" <<'PYEOF'
import sys
path, anchor, body = sys.argv[1], sys.argv[2], sys.argv[3]
src = open(path).read()
assert anchor in src, "anchor not found: " + anchor
open(path, "w").write(src.replace(anchor, body + "\n" + anchor, 1))
PYEOF
  _run_mutant "$1" "$3"
  mv "$1.bak" "$1"
}

# substitute FILE OLD NEW LABEL
#   Replace OLD with NEW.  Use this when an early return cannot express the
#   mutant -- disabling a guard inside a function body, for instance.
substitute () {
  cp "$1" "$1.bak"
  "$PY" - "$1" "$2" "$3" <<'PYEOF'
import sys
path, old, new = sys.argv[1], sys.argv[2], sys.argv[3]
src = open(path).read()
assert old in src, "anchor not found: " + old
open(path, "w").write(src.replace(old, new, 1))
PYEOF
  _run_mutant "$1" "$4"
  mv "$1.bak" "$1"
}

echo "baseline:"
printf '%-46s ' "(unmutated)"; "$PY" -m pytest -q 2>&1 | tail -1
echo
echo "mutants -- every one must turn the suite RED:"

# --- the control the review found unguarded (A3) ---------------------------
mutate src/templateer/audit.py \
  '    language = template.metadata.output.language' \
  'audit_template -> clean report' \
  '    return AuditReport(template=template.name, language="toml",
                       fixtures_seen=1, fields_probed=1, sites_linted=1,
                       findings=[])  # MUTANT'

# --- schema-driven field discovery, the round-2 follow-up (A3) ------------
# Restore the old fixture-shaped boundary: omitted nullable/nested fields and
# empty collection elements disappear before the synthesizer can probe them.
mutate src/templateer/audit.py \
  '    """Discover concrete string-bearing paths from the Pydantic JSON Schema.' \
  'schema field discovery -> fixture-shaped' \
  '    if data is _MISSING or data is None or data == []:
        return []  # MUTANT'

# --- unforeseen failures keep a distinct infrastructure classification -----
mutate src/templateer/pipeline.py \
  '            request, attempt, FailureReason.INTERNAL_ERROR,' \
  'internal error -> render failed' \
  '            request, attempt, FailureReason.RENDER_FAILED,  # MUTANT'

# --- the authoring lint, new in this round (A1 lint half) ------------------
mutate src/templateer/audit.py \
  '    language = template.metadata.output.language
    if language not in STRUCTURED_LANGUAGES:
        return [], 0' \
  'lint_template_source -> no findings' \
  '    return [], 0  # MUTANT'

# --- the missing rung, new in this round (A1 runtime half) -----------------
mutate src/templateer/validators.py \
  '    if language not in STRUCTURED_LANGUAGES:
        return []

    artifact_data = _artifact_data(artifact, language)' \
  'check_round_trip -> no findings' \
  '    return []  # MUTANT'

# --- the non-optional region prepend, new in this round (A5) ---------------
mutate src/templateer/validators.py \
  '    if output.kind != "region":
        return declared' \
  'effective_validators -> declared only' \
  '    return declared  # MUTANT'

# --- the region payload check (already covered at f3b9193) -----------------
mutate src/templateer/validators.py \
  '    fence_errors = _check_no_fence(artifact)' \
  'validate_region_payload -> no errors' \
  '    return []  # MUTANT'

# --- the escaper (B1) ------------------------------------------------------
mutate src/templateer/escaping.py \
  '    found = _SURROGATE.search(value)' \
  'escape_string -> bare json.dumps' \
  '    return json.dumps(value, ensure_ascii=False)[1:-1]  # MUTANT'

# --- the container guard (B4) ----------------------------------------------
# Empty the tuple rather than inserting a branch: inserting `if False:` before
# an `if` leaves an empty block, and the SyntaxError reds the suite for the
# wrong reason.
substitute src/templateer/escaping.py \
  '_CONTAINERS = (list, dict, tuple, set, frozenset)' \
  '_CONTAINERS = ()  # MUTANT' \
  'finalizer -> containers pass through'

echo
echo "restored:"
printf '%-46s ' "(unmutated)"; "$PY" -m pytest -q 2>&1 | tail -1
