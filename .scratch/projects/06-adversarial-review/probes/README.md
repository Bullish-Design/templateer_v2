# Reproduction probes

Every finding in `../TEMPLATEER-V2_ADVERSARIAL_REVIEW_2.md` marked **[verified]**
reproduces here. Run each from the repo root with the checked-in `.venv`.

```bash
.venv/bin/python .scratch/projects/06-adversarial-review/probes/p_escaping_holes.py
bash          .scratch/projects/06-adversarial-review/probes/p_audit_blind.sh
```

Probes write only to `mktemp -d` directories and leave the repo unchanged.
`mutate.sh` edits `src/` in place and restores from a `.bak` — check
`git status` after it runs.

| Probe | Findings | Headline result |
|---|---|---|
| `p_escaping_holes.py` | A1, B4 | `str` → `bool`/`int`/`null`; containers bypass the finalizer |
| `p_audit_blind.sh` | A1, A3 | corrupted artifact; render, validate and `check` all pass |
| `p_audit_vacuous.sh` | A2, A3 | `✓ escaping audit passed` after auditing nothing (twice) |
| `mutate.sh` | A3 | `audit_template → return []` leaves 257/257 green |
| `escape_chars.py` | B1 | 34 codepoints break YAML or Python |
| `escape_fix.py` | B1 | candidate fix: 36 broken pairs → 2 |
| `fuzz_escape.py` | B1 | 16,000 random cases; 1,236 failures, all YAML/Python |
| `p_pipeline_escape.py` | A4, B2 | exception escapes `generate()`; schema module escapes the root |
| `p_region.py` | A5, A6, B3, B5 | region check is opt-out; result lacks `ref`; `{}` passes |
| `p_surface.py` | A8, A9, B6, B8, C2 | 3 identical retries; async blocked; empty top-level exports |

## Expected output (2026-08-20, 0.2.0 @ f3b9193)

```
mutate.sh
  (unmutated)                            257 passed, 9 skipped
  audit_template -> return []            257 passed, 9 skipped   <-- no negative coverage
  validate_region_payload -> return []     7 failed, 250 passed  <-- covered

escape_chars.py
  codepoints that break at least one target language: 34

escape_fix.py
  current    broken codepoint/language pairs: 36   ['python', 'yaml']
  candidate  broken codepoint/language pairs: 2    ['toml']       <-- lone surrogates; raise EscapeError

p_audit_blind.sh
  templateer check   -> ✓ escaping audit passed  exit=0
  validate_artifact  -> []
  yaml.safe_load     -> {'title': True, 'owner': None}            <-- schema said str, str

p_pipeline_escape.py
  !!! EXCEPTION ESCAPED generate(): TemplateLoadError

p_region.py
  succeeded : True
  artifact  : '```\njust a sentence, not a mapping'                <-- corrupts the hosting page

p_surface.py
  attempts made        : 3
  all inputs identical : True
  RuntimeError: This event loop is already running
```
