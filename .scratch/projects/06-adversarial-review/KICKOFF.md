# Kickoff — implement the round-2 adversarial review

> Paste the block below into a clean session at the repo root. Everything it
> needs is in this directory: the review, and 10 runnable probes that prove
> each finding.

---

You are implementing the fixes from a deep adversarial review of templateer v2.

## Read first (in this order)

1. `.scratch/projects/06-adversarial-review/TEMPLATEER-V2_ADVERSARIAL_REVIEW_2.md` — the review. Every finding is labelled (§A1, §B1, …) and every `[verified]` finding has a reproduction.
2. `.scratch/projects/06-adversarial-review/probes/README.md` — the 10 probes, what each proves, and the exact output recorded on 2026-08-20 at `f3b9193`.
3. `.agents/skills/my-ai/SKILL.md` — the standing cross-repo law (devenv discipline, exit-code contract, version-control lanes, writing style).

Run the probes before you change anything. They are your baseline; you need to
see the bugs with your own eyes before you fix them.

```bash
.venv/bin/python .scratch/projects/06-adversarial-review/probes/p_escaping_holes.py
bash          .scratch/projects/06-adversarial-review/probes/p_audit_blind.sh
bash          .scratch/projects/06-adversarial-review/probes/mutate.sh
# ... and the rest; probes/README.md lists them all
```

Environment: the checked-in `.venv` works (`.venv/bin/python -m pytest -q`).
Baseline is **257 passed, 9 skipped**, `ruff check` clean, `ty check src/` clean.

## The four decisions — already made, do not re-litigate

| # | Decision | Consequence |
|---|---|---|
| 1 | **Close rung 4 with both a runtime check and an authoring lint.** | New `check_round_trip()` in `validators.py`, called by the pipeline; new source-level unquoted-interpolation lint in `audit.py`, surfaced by `templateer check`. |
| 2 | **Keep the argentic region vocabulary; plumb it through.** | `RegionBoundary{page, ref, anchor}` stays. Add `kind` and `region` to `GenerationResult` so a consumer can splice. Do **not** rename to a generic slot. |
| 3 | **Build the repair loop.** | Attempt N+1 receives attempt N's `error_detail` in its prompt. Thread it as a parameter through `_attempt` → `generate_model` → `build_context`; do not put it on `GenerationRequest` (that is the caller's input). |
| 4 | **Drop the bundled-template story.** | `pyproject.toml` build config is left alone. Fix the README to say templateer ships no templates: `./templates` and `-p` are the only sources. |

## Scope boundary

**In scope:** every finding in the review — §A1–A9, §B1–B10, §C1–C7.

**Out of scope, file as follow-up instead:**
- Adding a new output language (nix, hcl, dockerfile). After §A2 lands, unknown
  languages fail at template load, so adding one becomes a deliberate act:
  escape grammar + parser validator + audit payload set. That is new feature
  work, not a fix. Note it in the report-back.
- The argentic-side consumer loop (argentic.space Phase 6).
- A second renderer engine, or a serializer path for structured languages.
  The review names this as the alternative that was considered and declined;
  decision 1 closes the gap without it.

## How to run this — waves, with parallel subagents

Use the **Agent** tool. Agents inside one wave run in parallel and **must never
share a file**; the file ownership below is the contract that makes that safe.
Send all agents of a wave in a single message. Wait for a wave to finish before
starting the next.

---

### Wave 0 — Guardrails (1 agent, `tests/` only)

Write the tests **before** any implementation. They are the spec. Some will be
red; that is correct and expected.

Owns: `tests/**` only. Touches no `src/` file.

| Test | Finding | Red or green today |
|---|---|---|
| Escaper round-trips **every** codepoint in U+0000–U+011F plus U+2028/2029/D800/DFFF/FEFF, for all four structured languages | §B1 | **red** (34 codepoints fail) |
| A lone surrogate raises `EscapeError` | §B1 | red |
| A `list` or `dict` at a `{{ }}` site raises `EscapeError` | §B4 | red |
| `audit_template` **finds** an injection in a deliberately vulnerable fixture template | §A3 | green — this is the missing negative guard |
| `audit_template` reports "nothing audited" for a template with no `examples/`, and for an unknown language | §A3 | red |
| `templateer check` flags an unquoted `{{ }}` site in a structured-language template | §A1 | red |
| A model whose `str` field renders as a bare `true` / `#comment` fails output validation | §A1 | red |
| An unknown `output.language` fails at template load | §A2 | red |
| `pipeline.generate()` returns a `GenerationResult` — never raises — for a renderer file outside the template root | §A4 | red |
| A `schema.module` outside the template root fails to load | §B2 | red |
| A region template declaring `kind: markdown, optional: true` still gets a **fatal** payload check | §A5 | red |
| A region template with `language: markdown` or `text` fails at load | §A5 | red |
| A fenced region payload is **rejected** | §B3 | red |
| `{}` and `[]` are rejected as region payloads | §B5 | red |
| `GenerationResult` carries `kind` and `region` for a region generation | §A6 | red |
| `CommandValidator` reports **stdout** as well as stderr, and the branch is exercised end to end | §B7 | red — this branch has 0% coverage today |
| Attempt 2's prompt differs from attempt 1's and contains attempt 1's `error_detail` | §A9 | red |
| Warnings survive on a **failed** result | §B6 | red |
| `generate_async` works from inside a running event loop | §A8 | red |
| `import templateer` exposes `TemplateRegistry`, `GenerationResult`, `GenerationRequest`, `FailureReason` | §C2 | red |
| `render_from_model` raises `ValidationError` (not `TypeError`) for non-mapping input | §B8 | red |
| `--json` on `generate`, `validate`, `render`, `check`, `describe`, `list` emits parseable JSON; exit codes differ per `FailureReason` | §A7 | red |

Report back the exact list of node IDs, split into red and green. Later waves
are measured against those node IDs.

---

### Wave 1 — The type spine (1 agent, sequential — everything reads these)

Owns: `src/templateer/models.py`, `src/templateer/result.py`, `src/templateer/__init__.py`.

- **§A2 — close the language set.** Replace the free-text `OutputSpec.language: str` with a `Literal`. Split it so the intent is legible:
  ```python
  StructuredLanguage = Literal["toml", "json", "yaml", "python"]
  UnstructuredLanguage = Literal["markdown", "text"]
  ```
  An unknown language must fail at template load. This is the single
  highest-leverage change in the set: `language` currently selects the
  escaper, the parse validator, **and** whether the audit runs, so a typo
  silently disables all three.
- **§A5 (models half) — constrain a region's language.** `kind: region` must
  reject `markdown` and `text`; the 05 contract says the payload is YAML, so
  require `yaml` and comment why. `markdown`/`text` give identity escaping,
  which is exactly the hole the region kind exists to close.
- **§C8 — make `OutputSpec` a real discriminated union on `kind`,** the way
  `OutputValidator` already is one level up in the same file. Today it
  hand-rolls the same thing with a `model_validator`, and `path` means two
  different things depending on `kind`. Same file, two disciplines — pick the
  one the codebase already chose.
- **§A6 — plumb the region through the result.** Add `kind` and
  `region: RegionBoundary | None` to `GenerationResult`.
- **§C7 — add optional `usage`** to `GenerationResult` for the token counts
  pydantic-ai already returns and `generator.py` currently discards.
- **§A9 (models half) — cap `max_attempts`** (`ge=1, le=10`).
- **§C2 — export the public surface** from `__init__.py`: `TemplateRegistry`,
  `GenerationRequest`, `GenerationResult`, `FailureReason`. Also single-source
  `__version__` against `pyproject.toml` (`importlib.metadata.version`) instead
  of duplicating the literal.

Gate: `ruff`, `ty`, and the Wave-0 tests that target these files.

---

### Wave 2 — Fan-out (4 agents in parallel, file-disjoint)

All four read the new `models.py` types. None of them edits `models.py`.

**Agent E — `src/templateer/escaping.py`**
- **§B1.** `escape_string` is wrong for YAML on U+0080–U+009F (PyYAML
  `ReaderError`) and U+0085 (silent corruption to a space), and wrong for both
  YAML and Python on lone surrogates. The module docstring claims
  "Verified to round-trip **exactly**" for exactly these languages; that claim
  is false for 34 codepoints. A verified candidate fix lives in
  `probes/escape_fix.py` — it takes 36 broken pairs to 2. The remaining 2 are
  lone surrogates in TOML, which are genuinely unrepresentable: raise
  `EscapeError` for those. Fix the docstring to state what is now true.
- **§B4.** The finalizer returns non-`str`/`bool`/`None` values unchanged, so
  `{{ some_list }}` renders via MiniJinja's own Python-repr-shaped, single-quoted,
  language-blind conversion. Raise `EscapeError` with a message that names the
  fix (`interpolate elements with {% for %}, not the container`).

**Agent V — `src/templateer/validators.py`**
- **§A1 (runtime half).** Add `check_round_trip(artifact, language, model_dump)`:
  parse the artifact, walk the model dump, and report every scalar that reaches
  the artifact with a type other than the one the model declared. This is the
  missing rung — today a `str` field lands as a `bool` and every layer passes.
  Wave 3 wires it into the pipeline; export a clean function here.
- **§A5 (validators half).** `effective_validators` must prepend a
  **non-optional** `MarkdownValidator` for `kind: region` regardless of what the
  template declares. Today `optional: true` turns the "cannot be turned off"
  check into a warning, and a page-corrupting payload generates successfully.
  Deduplicate on kind only for the non-optional case.
- **§B3.** Delete the fence tolerance in `validate_region_payload`. It is
  unreachable (the built-in YAML parser rejects fenced text first) *and* it
  documents the wrong policy: the consumer owns the fences, so a fenced artifact
  double-fences the block. A leading fence line becomes an error.
- **§B5.** Reject `{}` and `[]` — the README and 05 D7 both say empty payloads
  are rejected, and they currently pass.
- **§B7.** `CommandValidator` reports only `stderr`; ruff and most linters write
  diagnostics to **stdout**, so the common case yields a failure with no detail.
  Report both.
- **§B10.** Remove the duplicate `bucket = warnings if validator.optional else errors`
  (it repeats the line 34 lines above it inside the same loop iteration).

**Agent U — `src/templateer/audit.py`**
- **§A3(b) — make silence loud.** Return an `AuditReport`
  (`fixtures_seen`, `fields_probed`, `findings`, `skipped_reason`), not a bare
  list. Today `audit_template` returns `[]` — indistinguishable from "clean" —
  when there is no `examples/` directory *and* when the language is unknown, and
  the CLI prints `✓ escaping audit passed` for both.
- **§A3(c) — strengthen detection.** The single `PAYLOAD = '"\nINJECTED = "yes'`
  is TOML/Python-shaped, so a vulnerable YAML or JSON template audits clean.
  Give each language its own payload set. And compare parsed **values**, not
  only `_key_paths`: the current comparison is structurally blind to every
  injection that changes a value rather than adding a key.
- **§A1 (lint half).** Add the source-level check the 04 guide promised for its
  Phase 8 and never implemented: in a structured-language template, flag every
  `{{ … }}` site not delimited by `"` on both sides. This catches the cause;
  Agent V's runtime check catches the symptom.

**Agent T — `src/templateer/template.py` + `src/templateer/catalog.py`**
- **§B2.** `load_schema_module` builds `root / f"{module}.py"` directly, bypassing
  `resolve_path`. Containment is enforced for `prompt.file` and `renderer.file`
  but not for the one file that is **executed**. Route it through `resolve_path`.
- **§C5.** `load_example` picks `sorted(...)[0]` — alphabetical, so
  `full.input.json` beats `minimal.input.json` by accident of spelling — and its
  docstring asserts the fixture "is already schema-validated by the template's
  own tests", which is an assumption, not an invariant. Validate the exemplar
  against the schema before it reaches the LLM prompt; let metadata name it.
- **§C6.** `sys.modules[spec_name] = module` is assigned *before* `exec_module`,
  so a `schema.py` that raises during import leaves a half-initialized module
  registered. Assign after, or `del` on failure.
- **§B9.** A template that fails to load vanishes from the catalog with exit
  code 0, indistinguishable from absent. Surface load errors on the catalog so
  the CLI can report them.
- **§B10.** Delete `TemplateCatalog.templates_by_language` (one caller: its own
  test) and `Template.output_kind` (zero callers anywhere).

---

### Wave 3a — The pipeline (1 agent, sequential — Wave 3b depends on its shape)

Owns: `src/templateer/pipeline.py`, `src/templateer/generator.py`.

- **§A4 — make the promise total.** `pipeline.py:8` says *"Nothing escapes as an
  exception — that promise is either total or worthless."* It is not total: a
  `TemplateLoadError` from `Template.render` → `resolve_path` escapes, because
  step 3 catches only `RenderError`. Add one `except Exception` at the boundary
  mapping to a `FailureReason`. The docstring already states the right standard;
  meet it.
- **§A1 (wiring).** Call Agent V's `check_round_trip` in step 4.
- **§A9 — build the repair loop.** Today: 3 attempts, byte-identical input each
  time, `error_detail` never fed back, no backoff. pydantic-ai already feeds
  schema errors back *inside* one call; the outer loop exists for the failure
  class it cannot see — the artifact did not validate. Thread the prior
  attempt's `error_detail` into the next attempt's context via
  `generate_model(..., prior_failure=...)` → `build_context`. Add backoff for
  `LLM_FAILED`.
- **§B6.** `fail()` never carries `warnings`, so optional-validator output is
  lost exactly when it is most useful. Carry it.
- **§C7.** Capture pydantic-ai's usage onto the result.
- **§B10.** Delete the unreachable `except TemplateLoadError` arm at
  `pipeline.py:58` — `catalog.get` raises only `TemplateNotFoundError`.

---

### Wave 3b — The two surfaces (2 agents in parallel, file-disjoint)

**Agent API — `src/templateer/api.py`**
- **§A8 — add `generate_async`** using `agent.run`, and make `generate` a thin
  `asyncio.run` wrapper over it. `run_sync` raises
  `RuntimeError: This event loop is already running` from inside a loop, so the
  primary entry point of a library that advertises itself for agent frameworks
  is unreachable from the async frameworks that are its stated audience.
- **§B8.** `render_from_model` uses `schema_class(**model_data)`; the CLI uses
  `model_validate`. Two surfaces, two error types for the same mistake. Use
  `model_validate` here too.
- Return warnings from `validate_artifact` instead of discarding them, and
  expose the audit (`registry.audit(name)`) — `templateer check` has no Python
  equivalent today.

**Agent CLI — `src/templateer/cli.py`**
- **§A7 — make the surface machine-readable.** Of seven commands exactly one
  (`schema`) emits parseable output. Add `--json` to `generate`, `validate`,
  `render`, `check`, `describe` and `list`; on `generate`, emit
  `GenerationResult.model_dump()` verbatim — the structured failure object
  already exists and is currently thrown away. Give distinct exit codes per
  `FailureReason` so an agent can tell "no such template" from "the LLM failed"
  from "the artifact is invalid" without parsing English.
- Fix `describe` printing a raw Python `set` repr (`Trigger paths: {'pyproject.toml'}`).
- Print Agent U's `AuditReport` honestly: `✓ 12 probes across 2 fixtures, 0 findings`
  or `⚠ nothing audited: no examples/`, and exit non-zero when nothing was audited.
- **§B9.** Surface catalog load errors; `--strict` makes them fatal.
- **§B10.** Delete the unreachable `except TemplateLoadError` arm at `cli.py:80`.

---

### Wave 4 — Docs and packaging (2 agents in parallel, file-disjoint)

Runs last, when behaviour is final. Write in the STE style the standing law
requires: short sentences, active voice, one idea per sentence, no filler.

**Agent DOC1 — `README.md`**
- Rewrite every claim the review falsified so it states what the code now does.
  The failed ones were: *"a validated model cannot alter the artifact's
  structure"*, *"every value interpolated into an artifact is escaped"*,
  *"cannot be bypassed by a template author"*, *"[the region check] cannot be
  omitted or turned off by a template author"*, *"bare scalars and empty
  payloads are rejected"*.
- **§C1 (decision 4).** State plainly that templateer ships no templates:
  `./templates` and `-p` are the only sources. Remove any implication of a
  bundled catalog.
- Document `--json`, the exit-code table, `generate_async`, the closed language
  set, and the authoring lint.
- Rule for this file: **do not write an absolute unless a test enforces it.**
  Every "cannot" needs a test that tries. That rule is the review's Part II
  finding, and it is the one worth carrying forward.

**Agent DOC2 — `CONTRIBUTING.md` + `AGENTS.md`**
- **§C3.** `CONTRIBUTING.md` documents `generation.py` and `validation.py`
  (both deleted in the 04 refactor) and omits `audit.py`, `escaping.py`,
  `result.py`. It also still describes `__init__.py` as holding "default
  template paths", which are gone. Make the module list match reality.
- **§C4.** `AGENTS.md` (which `CLAUDE.md` symlinks to) is still the unedited
  seed: `_One paragraph: what it does, who uses it, what it is not._` and
  `_Add the build / test / lint commands…_`. Fill it in. It is the first file
  every agent reads.

---

## Acceptance gates

**Gate 1 — the suite.** Every Wave-0 test green. `ruff check src/ tests/` and
`ty check src/` clean. No test deleted without saying so in the report.

**Gate 2 — mutation.** `bash .scratch/projects/06-adversarial-review/probes/mutate.sh`
must show **every** mutated safety control turning the suite red. Today
`audit_template → return []` leaves all 257 tests green — that is the finding
this gate exists to close. Extend `mutate.sh` with a mutant for each new control
(`check_round_trip`, the source lint, the non-optional region prepend).

**Gate 3 — the probes flip.** Re-run all 10 and record before/after in
`probes/README.md`. Expected:

| Probe | Before | After |
|---|---|---|
| `escape_chars.py` | 34 broken codepoints | **0** |
| `escape_fix.py` | current 36 / candidate 2 | current == candidate |
| `p_escaping_holes.py` | containers render as Python repr | `EscapeError` |
| `p_audit_blind.sh` | `check` exit 0, `validate_artifact` `[]` | `check` exit 1 (unquoted site), validation reports the type mismatch |
| `p_audit_vacuous.sh` | `✓ passed` twice | "nothing audited" + non-zero; `language: nix` fails at load |
| `mutate.sh` | audit mutant green | audit mutant red |
| `p_pipeline_escape.py` | exception escapes `generate()` | returns a `GenerationResult`; schema module outside root fails to load |
| `p_region.py` | opt-out works, `{}` passes, result lacks `ref` | prepend wins, `{}` rejected, `language: markdown` fails at load, result carries `region` + `kind` |
| `p_surface.py` | 3 identical attempts; `RuntimeError` on async; `[]` exports | attempt 2 differs; `generate_async` works; exports populated |

Keep the probes as historical evidence — they are the review's proof. Add a
header comment to each noting which finding it now guards, and put the
before/after in `probes/README.md`.

**Gate 4 — honesty.** Grep the README for every absolute claim and confirm a
test enforces each one. The review's Part II finding is that three of four such
claims were false because they were verified with hand-picked positive examples
and then written down as proofs. Do not recreate that.

## Version control

Follow the standing lane law in `.agents/skills/my-ai/SKILL.md`: one lane per
wave, verify, save, land into trunk, push. Land automatically when verify
passes. Stop and ask if verify fails, if a merge conflict appears, or if a
wave turns out to need a file another wave owns.

## Report back

End with:
- Files touched, per wave.
- Which Wave-0 tests were red at the start and are green now.
- Gate 2 output — the full mutation table.
- Gate 3 output — the before/after probe table.
- Any finding you could **not** close, and exactly why. Say it plainly; do not
  quietly narrow the scope.
- Any place the review was **wrong**. It was written against `f3b9193` and
  verified empirically, but verify before you trust — that is the whole point
  of Part II. If a finding does not reproduce, say so and show the evidence.
- The follow-ups you filed (new output languages, argentic Phase 6 coordination,
  and the 05 D6 open item: pin the fence grammar against argentic's
  `replace_range`).
- State of trunk: pushed or not.
