# Templateer V2 — Region Output Kind: Kickoff Prompt

> Paste this to the implementing agent as the session's operating brief.
> It assumes the agent has this repo checked out and can read the two
> companion documents below.

---

You are implementing the region write-back seam for Templateer v2. This is the
templateer side of argentic.space concept §5.4: `OutputSpec.kind` gains a
`"region"` value so that a template can *declare* it produces a bounded payload
for one fenced data block of a page, and so that declaration is *enforced*.

**Read first, in order:**
1. `.scratch/projects/05-output-kind-region/TEMPLATEER-V2_OUTPUT_KIND_REGION.md` — the kickoff + design doc (problem, pinned shapes, gate, non-goals, report-back).
2. `.scratch/projects/05-output-kind-region/TEMPLATEER-V2_REGION_IMPLEMENTATION_GUIDE.md` — the phased implementation plan (verified current-code facts, design decisions D1–D7, per-phase code, exit conditions, execution checklist).

## Scope — do exactly this

1. `OutputSpec.kind: Literal["full_file", "region"]` (default `full_file`), plus
   `RegionBoundary{page, ref, anchor=None}` and a `model_validator` coupling
   `kind` and `region` — fails at template load, not at render time.
2. `MarkdownValidator(kind: "markdown")` added to the discriminated validator
   union, and `validate_region_payload()` in `validators.py`: fence balance,
   single-document YAML, structured payload (mapping or list), round-trip
   stability, duplicate-key rejection.
3. Non-optional enforcement via `effective_validators()`: for `kind: "region"`
   the markdown check is auto-prepended; an explicit `kind: "markdown"` is not
   duplicated.
4. Wire `effective_validators` into **all** surfaces that run validators:
   `pipeline.py` (also: `output_path = region.page` for region templates),
   `api.py` `validate_artifact`, `cli.py` `render` and `validate`.
5. `cli.py` `describe` prints the region line; `Template.output_kind` property.
6. `tests/test_region.py` — the full gate, offline (tmp_path fixture template +
   stubbed `generate_model`). README updates last.

The guide's code blocks were mechanically smoke-tested against this checkout
(fence extraction, duplicate keys, fixture render); treat them as correct-until-
proven-otherwise and confirm each as you land it.

## Hard constraints

- **Offline-first.** No LLM call, no argentic dependency, no network. Stub
  `generate_model` exactly like `tests/test_pipeline.py` does.
- **No bundled template changes.** Do not add `templates/*`; build the region
  template as a `tmp_path` fixture. `templates/pyproject-uv` stays byte-identical.
- **No new module.** Everything lands in `models.py`, `validators.py`,
  `pipeline.py`, `api.py`, `cli.py`, `template.py` (one property), README,
  and the new test file.
- **Render invariant intact.** `renderer.py` untouched: templates still receive
  only `model.model_dump(mode="json")`. Templateer renders and validates; it
  never writes regions (or files) — that is the consumer's `replace_range`.
- **Back-compat.** `kind` defaults to `full_file`, `region` to `None`;
  `GenerationResult` shape unchanged. Full suite (223 passed / 9 skipped
  before your work) stays green at every phase boundary.
- **Run `pytest` after every phase.** Phases 0→1→2→3→4 sequential; 5 last.

## The gate (falsifiable; all five must pass)

1. A `kind: region` template loads and renders; its artifact is body-only YAML
   that round-trips into a `$ref`'d block with the fence intact.
2. `kind: region` without `region:` fails template load; `kind: full_file`
   with `region:` fails template load.
3. The renderer invariant holds: region context is model-dump-only.
4. The markdown validator rejects unclosed fence, stray fence, non-YAML,
   multi-document, scalar/empty, and duplicate-key payloads; accepts a clean
   body and a clean fenced block.
5. Back-compat: every existing template and `GenerationResult` shape untouched.

## Explicit non-goals

Writing regions; the consumer loop (digest, termination, `addressed:`
bookkeeping — that is argentic Phase 6); a second renderer engine; re-adding
`catalog.templates_by_output_kind`; any change to the Pydantic-model-only
invariant.

## Report back (end of session)

1. Files touched (final list, not the planned one).
2. The final `OutputSpec` / `RegionBoundary` shape.
3. Gate results — one line per check above.
4. The validator decision (default = offline/LocalIndex-equivalent) and why.
5. Drift vs the design doc — especially the fence grammar (`---` vs ```):
   note that it must be pinned against the *real* argentic `replace_range`
   before shipping; and `region.page` "name vs pattern" semantics.
6. Trunk state: committed + pushed, or not, with reason.
7. If you stopped early: exactly what is unfinished and why.

**Stop the moment you are no longer confident.** Better an honest partial
report than a green-but-wrong suite.
