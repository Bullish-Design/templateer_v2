# Argentic compatibility audit

- **Date:** 2026-08-20
- **Templateer baseline:** `1075ca8`
- **Argentic baseline:** `dc9f86c`
- **Initial disposition:** B. Narrow integration gap
- **Final Argentic integration:** `8d67c9c`
- **Final documentation pin:** `ade3f09`

## Scope

The audit inspected the Phase 6 consumer loop without editing Argentic first.
It covered the Templateer adapter, page index, fence scanner, body-range write,
workflow outcome, tests, and documented quality gate.

Argentic had no `.allium` files. It also had no standing personal skill. The
repository `AGENTS.md` supplied its project instructions.

## Read-only findings

The existing adapter received Templateer `GenerationResult` values. It checked
`succeeded` and `artifact`. It selected region metadata from the template
before generation. It did not validate the returned `kind` or `region`.

The existing fake results defaulted to `kind="full_file"`. The tests therefore
passed without proving the returned boundary contract. Failed generation also
dropped `warnings`, `attempt`, and `usage` before workflow persistence.

Argentic already parsed the artifact as bare YAML. The loop added `$ref`,
`generated_from`, and `addressed`. It used its fence-safe serializer. It then
called `Page.replace_range` with the indexed body span.

This evidence required disposition B. The gap was limited to result-boundary
validation, failure-field preservation, and cross-repository contract tests.
No `replace_range` reimplementation was needed.

## Answers

1. **Does Argentic receive Templateer `GenerationResult` values?** Yes.
   `src/argentic/loop_templateer.py` calls the Templateer generation seam and
   handles the returned model. `tests/test_loop_templateer.py` uses real
   `GenerationResult` and `RegionBoundary` models.

2. **Does it distinguish `full_file` and `region`?** Yes after `8d67c9c`.
   `TemplateerRenderer.render` requires `result.kind == "region"`.
   `test_invalid_generation_result_boundary_writes_nothing` covers a
   successful `full_file` result.

3. **Does it read `region.page`, `region.ref`, and `region.anchor`?** Yes after
   `8d67c9c`. The adapter requires an exact live page match. It normalizes one
   leading `$` for the ref match. It requires the returned anchor to equal the
   template declaration. The parametrized boundary test covers every field.

4. **Does it pass a bare YAML artifact to `replace_range`?** It consumes a
   bare YAML artifact. It does not pass the original string verbatim. The
   adapter parses the artifact into a mapping. The loop adds its bookkeeping
   fields and serializes a bare YAML body. The full round-trip test asserts
   that neither the generated artifact nor the replacement contains a
   backtick or tilde fence.

5. **Does `replace_range` replace only the block body?** Yes. `LocalIndex`
   records the `code_fence_content` span. `RegenerationLoop` passes that span
   to `Page.replace_range`. `test_full_round_trip_stamps_writes_and_drops_the_echo`
   records the call and proves that text before and after the body is unchanged.

6. **Which component owns the fences?** Argentic owns them as the page
   consumer. Templateer returns bare YAML. `RegenerationLoop` changes only the
   body span.

7. **What fence grammar does the consumer accept?** The scanner opener is
   ``^\s{0,3}(`{3,}|~{3,})``. A data info string is exactly `data` or starts with
   `#`. A closer uses the same character and a run at least as long as the
   opener. `replace_range` itself accepts offsets and has no fence grammar.
   `src/argentic/loop.py` defines the scanner. `src/argentic/index_local.py`
   extracts the fenced content span.

8. **Does a failed `GenerationResult` prevent every write?** Yes. The adapter
   raises `LoopError` before artifact parsing or replacement. The failure test
   asserts that the page remains byte-identical. It also checks
   `failure_reason`, `error_detail`, `warnings`, `attempt`, and `usage`.

9. **Does a missing page, ref, or anchor prevent every write?** Yes after
   `8d67c9c`. Empty or mismatched page and ref values fail. A missing anchor
   fails when the template declares one. The boundary test asserts no write
   for each case. `test_page_mismatch_is_a_loop_error` covers template-to-live
   page resolution.

10. **Are these behaviors covered by automated tests?** Yes. The primary
    contract is `tests/test_loop_templateer.py`. Body-range behavior also has
    coverage in `tests/test_loop_digest.py` and `tests/test_page_writes.py`.
    Durable failure bookkeeping is covered by
    `consumer/tests/test_workunit.py::test_templateer_failure_metadata_is_a_durable_outcome`.

11. **Does a cross-repository contract test prove compatibility?** Yes after
    `8d67c9c`. `test_full_round_trip_stamps_writes_and_drops_the_echo` uses
    Templateer's real result models. It proves one body-range call, one fence
    structure, successful re-indexing, and a no-write echo. The negative tests
    use the same models for result failures and invalid boundaries.

## Verification

Argentic's documented gate is `testee verify --mode ci`.

The final gate passed at `ade3f09`:

- Ruff passed.
- Ruff format passed.
- Ty passed.
- Pytest passed with 671 collected and 671 passed.

The gate used deterministic Templateer test seams. It required no provider.

## Final disposition

The initial disposition was **B. Narrow integration gap**.

The narrow gap is complete at `8d67c9c`. Argentic production code changed only
in the adapter and structured failure path. Existing `replace_range` behavior
did not change.
