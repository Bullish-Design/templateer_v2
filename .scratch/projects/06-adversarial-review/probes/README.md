# Adversarial review probes

These probes preserve the evidence for the round-2 adversarial review. The
baseline column records results from `0.2.0` at `f3b9193` on 2026-08-20. The
remediated column records the current results after the review fixes.

Run each probe from the repository root inside the devenv shell:

```bash
devenv shell -- bash -lc \
  '.venv/bin/python .scratch/projects/06-adversarial-review/probes/p_escaping_holes.py'
devenv shell -- bash -lc \
  'bash .scratch/projects/06-adversarial-review/probes/p_audit_blind.sh'
```

The probes use temporary directories and leave the repository unchanged.
`mutate.sh` changes `src/` in place and restores each file from a backup.
Check `git status` after it runs.

## Before and after

| Probe | Findings | Baseline at `f3b9193` | Remediated result |
|---|---|---|---|
| `p_escaping_holes.py` | A1, B4 | Strings re-lex as other scalar types. Containers render as Python repr. | The scalar reproduction remains visible. Container interpolation raises `EscapeError`. |
| `p_audit_blind.sh` | A1, A3 | `check` exits 0. Render and validation accept type changes. | `check` exits 1 with four findings. Render exits 1. Validation with model data reports both type changes. |
| `p_audit_vacuous.sh` | A2, A3 | Both cases report a passing audit after zero probes. | No fixtures reports “nothing audited” and exits 1. `language: nix` fails to load and exits 3. |
| `mutate.sh` | A1, A3, A5, B1, B4 | The audit mutant leaves 257 tests green. | All seven safety-control mutants turn the 435-test suite red. |
| `escape_chars.py` | B1 | 34 codepoints break YAML or Python. | 0 codepoints break. Two lone surrogates raise `EscapeError`. |
| `escape_fix.py` | B1 | Current code has 36 broken pairs. The candidate has two TOML failures. | Current and selected candidate have 0 broken pairs. Both reject two lone surrogates. |
| `fuzz_escape.py` | B1 | 1,236 of 16,000 language cases fail. | 0 language cases fail. The escaper rejects 437 inputs that contain lone surrogates. |
| `p_pipeline_escape.py` | A4, B2 | A schema escapes the root. `generate()` raises `TemplateLoadError`. | Schema loading rejects the escape. `generate()` returns `FailureReason.NO_TEMPLATE`. |
| `p_region.py` | A5, A6, B3, B5 | The optional check wins. Empty payloads pass. The result lacks splice metadata. | The required check wins. Fences and empty payloads fail. Markdown fails model validation. Results carry `kind` and `region`. |
| `p_surface.py` | A8, A9, B6, B8, C2 | Three prompts are identical. Async use fails. Warnings and exports are absent. | Repair prompts differ. Async use returns a result. Warnings survive. Validation errors agree. Exports are populated. |

## Mutation gate

The current mutation run produces this table:

```text
(unmutated)                                    435 passed, 9 skipped
audit_template -> clean report                 25 failed, 410 passed, 9 skipped
lint_template_source -> no findings             5 failed, 430 passed, 9 skipped
check_round_trip -> no findings                 7 failed, 428 passed, 9 skipped
effective_validators -> declared only           5 failed, 430 passed, 9 skipped
validate_region_payload -> no errors            16 failed, 419 passed, 9 skipped
escape_string -> bare json.dumps                 9 failed, 426 passed, 9 skipped
finalizer -> containers pass through            24 failed, 411 passed, 9 skipped
(unmutated, restored)                          435 passed, 9 skipped
```

Each mutant remains valid Python. A syntax error does not count as negative
coverage.

## Review corrections

The review said that one payload left vulnerable YAML and JSON templates
clean. The baseline probe showed that JSON and Python were already detected.
Only YAML stayed clean.

The original character sweep found 34 broken codepoints. A wider sweep found
two more: U+FFFE and U+FFFF. The remediated fuzz probe then found a separate
interaction. PyYAML folds spaces adjacent to raw U+2028 or U+2029 separators.
The escaper now escapes both separators, and a regression test covers that
interaction.
