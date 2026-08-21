# Round-2 follow-ups

Status reviewed on 2026-08-20.

## New output languages — Decision recorded

Do not add Nix, HCL, or Dockerfile syntax without a concrete consumer.

The dated decision records the available parser support, expected escaping
work, missing product use case, and evidence needed to start implementation.
See [OUTPUT_LANGUAGE_DECISION_2026-08-20.md](OUTPUT_LANGUAGE_DECISION_2026-08-20.md).

## Argentic Phase 6 consumer loop — Complete

The read-only audit found a narrow integration gap. Argentic already owned the
consumer loop and `replace_range`. Its adapter did not validate the returned
`GenerationResult.kind` and `GenerationResult.region` fields.

Argentic commit `8d67c9c` closes the gap. It validates `page`, `ref`, and
`anchor` before a write. It also preserves generation failure diagnostics in
the durable workflow outcome. Commit `ade3f09` pins the async integration hook
in the contract-test description.

See [ARGENTIC_COMPATIBILITY_AUDIT_2026-08-20.md](ARGENTIC_COMPATIBILITY_AUDIT_2026-08-20.md).

## Region fence grammar — Complete

Argentic's `replace_range` accepts code-point offsets. It does not parse fence
syntax. Argentic locates a fenced data block first. It passes only the block
body range to `replace_range`.

The verified scanner accepts an opener that matches
``^\s{0,3}(`{3,}|~{3,})``. A data block uses the exact info string `data` or an
info string that starts with `#`. The closer uses the same fence character.
Its run length is at least the opener length.

The page consumer owns the fences. Templateer returns bare YAML.

Argentic's `test_full_round_trip_stamps_writes_and_drops_the_echo` enforces the
body-only write. Its `test_invalid_generation_result_boundary_writes_nothing`
enforces the boundary failures.

## Schema-driven audit fixtures — Complete

Commit `555c388` discovers string-bearing fields from the Pydantic schema. It
synthesizes omitted optional, nullable, nested, and collection values when it
can validate a model. It records structured skip details when constraints
reject every bounded probe value. The audit probes at most 100 fields per
fixture.

The synthesis fixture previously probed one string field from its example. It
now probes five fields. Four of those fields are absent from the example. It
also reports one constrained field as skipped.

The `schema field discovery -> fixture-shaped` mutant proves that fixture-only
discovery fails the suite. `test_audit_synthesises_omitted_schema_fields`
enforces the five-probe count and the structured skip.

## Internal failure reason — Complete

Commit `1aaf619` adds `FailureReason.INTERNAL_ERROR`. An unforeseen exception
maps to `internal_error`. The failure is permanent. It does not retry. The CLI
uses exit code 2. Debug logging retains the traceback.

The `internal error -> render failed` mutant proves that mapping an unforeseen
exception back to `render_failed` fails the suite.
`test_internal_error_is_permanent_and_keeps_debug_traceback` enforces the retry
and logging behavior. `test_internal_failure_is_structured_and_has_no_cli_traceback`
enforces the CLI behavior.

## Allium specifications — Still open

The user directed this run to ignore Allium. No Allium specification changed.
The behavioral specification updates remain open for a separate run.
