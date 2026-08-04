# Templateer V2 — Second-Opinion Adversarial Review

**Date:** 2026-08-04
**Scope:** `src/templateer/` (12 modules, 1,886 LOC), `templates/pyproject-uv/`, `tests/` (13 files, 3,455 LOC), `pyproject.toml`, `README.md`, and the concept doc.
**Method:** Independent read of every source file, then empirical execution of each claim — mine and the prior review's. Every finding below marked **[verified]** was reproduced in this checkout with `.venv/bin/python`.
**Baseline:** 237 passed, 11 skipped.
**Posture:** Security is explicitly out of scope (single-developer personal library). Findings the prior review filed as security are re-scored here on the axis that actually matters: *does this violate the core concept, or is it dead weight?*

---

## Verdict

The concept is excellent and worth defending. The implementation is roughly **35% larger than the concept needs**, and — more importantly — **the headline guarantee is not actually delivered**.

Three sentences that summarize the whole review:

1. **The central invariant is enforced at the wrong layer.** "The renderer only receives validated model data" is true and verified. But the project sells a stronger claim — that the artifact is therefore structurally sound — and that claim is false. I produced a `pyproject.toml` containing a key the schema never declared, from a fully valid model, with every validator reporting success.
2. **There is no single pipeline.** There are three partial re-implementations of it (`pipeline.run_pipeline`, `api.TemplateRegistry.generate`, `cli.validate`), and they have already drifted apart in observable ways.
3. **The `Generation` entity is a state machine whose two non-terminal states are unobservable**, whose `matched_template` field is always equal to `template_name`, and whose `artifact` field doubles as an error-message slot. It is modelling an abandoned design.

The prior review is *directionally* useful and its verified crash vectors are real. But it audited the code against its own docstrings rather than against the concept, so it graded the architecture "sound" and missed the finding that matters most.

---

## Audit of the prior review

| Prior ID | Status | Note |
|---|---|---|
| C1a missing API key crashes | **Confirmed** | Reproduced: `UserError` escapes `run_pipeline`. |
| C1b non-JSON context crashes | **Confirmed** | Reproduced: `TypeError: Object of type PosixPath is not JSON serializable`. |
| C1c template-load errors escape | **Confirmed by inspection** | `get_schema_class`/`load_prompt` are outside every `try`. |
| C1d `agent.retries` AttributeError | **Confirmed** | `hasattr(agent, 'retries')` is `False`; only `_max_tool_retries` / `_max_output_retries` exist. But the branch is *unreachable* — see B3; the right fix is deletion, not repair. |
| C2 `pydantic-ai>=0.0.20` floor | **True but mis-scored as Critical.** | Nobody resolves this floor in practice; `uv.lock` is gitignored but the venv has 2.23.0. Real cost: a misleading declaration. One-line fix, Low priority. |
| C3 template code execution | **True, but not a finding.** | Templates executing `schema.py` *is the design*; the concept doc names it explicitly ("executable Python in `schema.py`"). Out of scope per your constraints. The **path traversal is worth keeping** — reframed as portability, not security (B7). |
| H1 `MODEL_VALIDATION_FAILED` dead | **Confirmed** | Never produced. |
| H2 `validation_messages` dead | **Confirmed** | `generator` returns `[]` unconditionally. |
| H3 retry budget compounds | **Confirmed, and worse than described** | See A3 — it's a name collision between two unrelated retry concepts. |
| H4 CLI `validate` skips validators | **Confirmed** | `cli.py:445` passes language only. |
| M1 `outputs: []` → IndexError | **Confirmed** | `min_length=1` is the wrong fix — see B5. |
| M2 `output_kind` returns language | **Confirmed** | And it makes the whole `kind` concept vacuous — see B6. |
| M3 CLI context parsing | **Confirmed** | `{"user_request": "..."}` alone is silently treated as project facts. |
| M4 wheel ships no templates | **Confirmed by inspection** | `templates/` lives at repo root, outside `packages = ["src/templateer"]`. |
| M5 `api.generate_model` leaks | **Confirmed** | Same root cause as C1. |
| M6 `render` writes unvalidated | **Confirmed, and understated** | This is the escape hatch through which A1 reaches disk. |
| M7 invariants unenforced | **Understated.** | The pipeline doesn't merely fail to enforce them — it *actively violates* them on three paths (A3). |
| L4 "YAML validation is nearly vacuous" | **Overstated** | Parse-checking is the documented job. Real gap is A1, not weak parsing. |
| L7 test hygiene | **Massively understated** | See A6 — this is a top-three finding, not a nit. |
| "What is genuinely good: output validation catches injection" | **False** | Directly falsified in A1. |

**The prior review's largest omission:** it verified that the *render context* is clean and concluded the pipeline is sound, without ever asking whether a clean context can still produce a corrupt artifact. It can. Trivially.

---

# A — Concept & architecture

## A1 — A validated model can silently produce a structurally wrong artifact **[verified]**

**This is the finding.** Everything else is secondary.

`renderer.render_template` does raw textual interpolation of model strings into a structured-syntax target. Pydantic validates *which values* reach the template. It says nothing about how those values **lex** once spliced into TOML/YAML/JSON/Python. There is no escaping layer anywhere in the codebase.

```python
m = cls(project_name="ok", python_version="3.12",
        project_description='benign"\nlicense = "PROPRIETARY')
r = t.render(m)
```

Output:

```toml
[project]
name = "ok"
description = "benign"
license = "PROPRIETARY"
requires-python = ">=3.12"
```

```
validate_output(r, "toml")  →  []          # passes
tomllib.loads(r)["project"] →  {'name': 'ok', 'description': 'benign',
                                'license': 'PROPRIETARY', ...}
```

A `license` key that exists in no schema, no template, and no model. Model validation passed. Render succeeded. Output validation passed. `Generation.status == READY`. Every layer of Templateer reported success while emitting a field the type system never authorized.

Two weaker variants also verified — `project_description='He said "hi"'` and `project_name='x", version = "9.9.9'` — happen to break the TOML parser, so the pipeline path catches them. That is *luck*, not design: the parser catches the crashing subset and misses the injecting subset. And `templateer render --output` skips output validation entirely, so even the crashing subset reaches disk on that path.

This falsifies the README's "rendered artifacts are parsed to catch injection or corruption before they hit disk" and hollows out concept-doc guarantee #2 ("the rendered artifact was generated from a Pydantic model") — technically true, practically meaningless if arbitrary syntax can ride in on a string field.

**Fix — two options, in order of elegance.**

**(a) Serialize structured targets; don't template them.** For `language in {toml, json, yaml}` the artifact *is* a data structure. `tomli_w.dumps` / `json.dumps` / `yaml.safe_dump` are correct by construction: no escaping bugs are possible, ever. The `.j2` file becomes unnecessary for exactly the artifact types the concept doc lists first (`pyproject.toml`, `devcontainer.json`, GitHub Actions). Jinja stays for genuinely textual targets — Dockerfile, Makefile, Python source, Markdown — where it is the right tool. This also deletes the cosmetic warts in the current template (`ignore = [\n]`, `dependencies = [\n]` on minimal models).

**(b) If you keep Jinja everywhere, add a language-aware escaping boundary.** Register per-language filters on the MiniJinja environment (`| toml`, `| json`, `| yaml`, `| py`) selected by `outputs[0].language`, and make bare interpolation of a `str` into a structured target a template-authoring error rather than a silent hazard. This is the minimum bar for the guarantee the README already makes.

Whichever you pick, **A1 is the difference between Templateer being a real constraint system and being a nicely-typed string formatter.**

---

## A2 — Three partial re-implementations of one pipeline **[verified]**

| | resolve | LLM | render | built-in validator | custom validators | error model |
|---|---|---|---|---|---|---|
| `pipeline.run_pipeline` | ✓ | ✓ | ✓ | ✓ | ✓ | returns `Generation` |
| `api.TemplateRegistry.generate` | ✓ | ✓ | ✓ | ✓ | ✓ | raises 4 exception types |
| `cli.validate` | ✓ | — | ✓ | ✓ | **✗** | `sys.exit(1)` |

`api.generate` (`api.py:183-225`) is a line-by-line re-derivation of `run_pipeline` steps 1–4 with `raise` substituted for `gen.status = FAILED`. The CLI bypasses the API entirely and calls `run_pipeline` directly. The drift is already observable: `cli.validate` silently skips the custom validators the template author declared (prior H4), and `api.generate` raises bare `RuntimeError` for output-validation failure while the pipeline returns a typed `FailureReason`.

Three surfaces, three error models, one algorithm. Every future change must be made in three places or the surfaces diverge further — which is exactly what has already happened.

**Fix:** one pipeline function. `api` and `cli` become adapters over it (`Generation` → exception, `Generation` → exit code). Deletes ~80 lines and makes drift structurally impossible.

---

## A3 — `Generation` models a design that was abandoned **[verified]**

The concept doc explicitly cut fuzzy matching: *"Template selection is exact name match. There is no scored matching, no heuristic selection, and no fuzzy lookup."* The entity still carries the machinery for it.

- **`matched_template` is always `template_name`.** `pipeline.py:84` sets it from `catalog.get(template_name).name`, which by construction equals the input. It is a field that can only ever hold one of two values: the input, or `None`. Pure vestige.
- **`requested_path` is an output mislabeled as an input.** Documented "the artifact path requested", but no caller ever supplies it — `run_pipeline` initializes it to `""` and then overwrites it from `template.metadata.outputs[0].path`. On the `NO_TEMPLATE` path it stays `""` forever.
- **`artifact` doubles as an error-message field.** Documented "set only on success". `pipeline.py:108/119/133` assign `str(e)` and joined validator errors to it on three failure paths. So `gen.artifact` is non-null-but-garbage whenever `gen.succeeded` is `False`, and any caller writing `if gen.artifact: write(gen.artifact)` writes an exception message to `pyproject.toml`. The class docstring asserts invariants (`READY ⇒ artifact`, `FAILED ⇒ reason`) that the pipeline itself breaks the spirit of.
- **`SUBMITTED` and `GENERATING` are unobservable.** `run_pipeline` is synchronous and returns only in a terminal state. No caller in any surface can ever witness either value. Half the state machine exists only for internal assignment.
- **Two unrelated concepts share the word "retries".** `max_retries` → `Agent(retries=)` is pydantic-ai's *internal output-validation* retry budget. `Generation.retry_count` is the *pipeline-level* re-attempt count. `retry_generation` passes the second as the first (`pipeline.py:185`), so the LLM's internal budget grows 3 → 4 → 5 across pipeline retries. The comment `← carry forward the retry budget` describes a budget that is not carried; it is inflated.
- **`retry_generation(catalog, gen, user_request, context)`** requires the caller to re-supply the request, because `Generation` doesn't hold it. A generation you cannot re-run from itself is not a retryable entity.
- **`PipelineError` is never raised anywhere in `src/`** — only constructed and re-raised by its own tests. Dead.
- **`FailureReason.MODEL_VALIDATION_FAILED` is never produced** — every model failure maps to `LLM_FAILED`, collapsing the one distinction the enum exists to draw.

**Fix, minimal:** make `Generation` hold the *request* (`template_name`, `user_request`, `context`, `model_name`) alongside the *result*; drop `matched_template`; rename `requested_path` → `output_path`; add a distinct `error_detail: str | None` so `artifact` means artifact. Then `retry(gen)` is a one-argument function, `can_retry` reads a stored budget instead of a hardcoded `3`, and the invariants become expressible as two `@model_validator`s.

**Fix, honest:** ask whether the entity earns its keep at all. There is no persistence, no async, no polling consumer, and two of four states are unreachable. What the code actually needs is a result type — `Ok(artifact) | Err(reason, detail)` — plus a retry loop in the caller. That is the non-gold-plated shape, and it deletes `generation.py` almost entirely.

---

## A4 — Failure handling is scoped to the wrong unit of work **[verified]**

Confirmed, with reproductions:

```
run_pipeline(c, "pyproject-uv", "x")                        → UserError escapes (no API key)
run_pipeline(c, "pyproject-uv", "x", context={"p": Path()}) → TypeError escapes
```

The prior review is right that these crash. It's wrong about the cause being a too-narrow `try`. The cause is that `generate_model` interleaves four different kinds of work — filesystem I/O (`get_schema_class`, `load_prompt`), context serialization, agent construction, and the network call — and only the fourth is guarded. The `try` isn't too narrow; the function is too wide.

`pipeline.py:33-35` promises *"On any failure the Generation is returned with status FAILED"*. That promise is either true or it isn't; there is no partial version. One `except Exception` at the pipeline boundary makes it true. `_build_context` should also use `json.dumps(context, indent=2, default=str)` — `Path` objects are the single most plausible "project fact" an agent will pass, and stringifying them is obviously the intent.

---

## A5 — `generator.py`'s post-processing is dead code that is also *wrong* **[verified]**

Lines 96–129 (35 of the module's 151) exist to handle outputs pydantic-ai cannot produce:

- `if raw_output is None` — unreachable with `output_type` set; and its body calls `agent.retries`, which doesn't exist, so the "clean error" branch would itself raise `AttributeError`.
- `if not isinstance(raw_output, schema_class)` / dict fallback — unreachable; `output_type=schema_class` guarantees an instance.
- **The "defensive re-validation" is actively harmful.** It round-trips `model_dump(mode="json")` back through `model_validate`, then *discards the result and returns the original*. For any schema using field aliases, that round-trip fails:

```python
class Aliased(BaseModel):
    class_name: str = Field(alias="class")

inst.model_dump(mode="json")          → {'class_name': 'Foo'}
validate_model_instance(Aliased, ...) → SPURIOUS FAILURE: "loc: ('class',) Field required"
```

A template author whose schema uses `Field(alias=...)` gets `ModelGenerationError: Post-generation validation failed` on every successful generation. Templateer's *own* `SchemaRef` model uses `alias="class"` — the pattern is right there in the codebase for an author to copy.

**Fix:** delete lines 96–129. `generate_model` becomes: load schema, load prompt, build context, construct agent, `run_sync`, return `result.output`. That is what the concept doc's own implementation sketch says (`return result.output`), and it's correct.

**Consequence:** `validation.py` (38 lines) exists solely to serve those dead branches. Deleting them deletes the module — and `tests/test_validation.py` (286 lines) with it. Its error formatting was low-quality anyway: `[str(err) for err in e.errors()]` stringifies raw dicts, producing `"{'type': 'missing', 'loc': ('class',), 'msg': ..., 'url': 'https://errors.pydantic.dev/...'}"` where a human-readable `class: Field required` was intended.

---

## A6 — The test suite manufactures confidence it hasn't earned **[verified]**

237 passing tests, 1.8:1 test-to-source ratio, and *the single most common runtime failure — no API key — exits with a raw traceback.* That combination is the finding. The suite was written to cover lines, not behaviors.

- **18 `Generation(...)` constructions in `test_pipeline.py`** set fields by hand and then assert those same fields. `test_render_failure_in_pipeline_path` is named for a pipeline path and never calls the pipeline — it builds a `Generation(status=FAILED, failure_reason=RENDER_FAILED)` and asserts it is FAILED with reason RENDER_FAILED. Its own comment admits it: *"Verify the error handling path works by constructing a generation with RENDER_FAILED."*
- **`TestPipelineError`** (3 tests) exercises a class production code never raises.
- **Two retry tests pass *because* the code crashes**: `except Exception: return  # If LLM call fails, that's fine`. They would go green against literally any implementation.
- **`test_unknown_template_errors` has a body of `pass`.**
- **`api.TemplateRegistry.generate` — the primary Python entrypoint — has zero non-LLM coverage.** All four tests are `@has_api_key`-gated. Nothing in CI has ever executed it.
- **The end-to-end LLM test cannot fail**: `if gen.status == FAILED: assert reason is not None; return`.
- **`test_json_schema_generates`** asserts `json_schema.get("title") in ("PyprojectUvModel", None) or "title" in json_schema` — a tautology.
- **Not one test asserts A1's escaping behavior**, for any language, in any direction.

The suite tests that the code is shaped the way it is shaped. It does not test that the code works.

**Fix:** delete the tautological and self-raising tests outright (they are negative-value — they make the suite look thorough). Add, in priority order: (1) escaping/injection cases per output language; (2) `run_pipeline` with `OPENAI_API_KEY` unset returns `FAILED`, never raises; (3) non-serializable context; (4) `api.generate` end-to-end with a stubbed generator. Four tests that would each have caught a real bug beat 237 that caught none.

---

## A7 — `examples/` fixtures are the obvious few-shot source, and go unused **[verified]**

No reference to `examples/` anywhere in `src/`. Every template already ships `*.input.json` — a hand-validated, schema-conforming exemplar — and the codebase uses it only for round-trip tests.

The concept doc's stated motivation is "Why This Helps Smaller Models." One validated example in the prompt is the single highest-leverage thing you can do for a small model filling a structured schema, and the asset already exists, already round-trip-tested. Passing the first `*.input.json` to the agent as a few-shot example is a handful of lines against an existing invariant. This is not gold-plating — it's using what's already there.

---

# B — Correctness

## B1 — `render_template` accepts `dict`, which is a typed hole in the central invariant **[verified]**

```python
def render_template(template_path, model: BaseModel | dict[str, Any], strict=True)
```

The one function whose entire job is enforcing "only validated model data reaches the renderer" has a signature admitting arbitrary dicts. Only tests use the dict path (`test_renderer.py:103,110,119`), which is exactly how the hole stays open: the tests document the bypass as supported. Narrow to `BaseModel`.

## B2 — `strict_context` is a misnamed, dangerous knob **[verified]**

It does not control context strictness; it sets `env.undefined_behavior`. The concept doc states the rule unconditionally: *"Undefined variables should not silently render as empty strings."* Making it a per-template opt-out means a template author can disable one of the six things Templateer guarantees, and `describe` will cheerfully print `Strict context: False`. Delete the knob; strict is the contract.

## B3 — `agent.retries` does not exist **[verified]**

`hasattr(agent, 'retries')` → `False` (pydantic-ai 2.23.0 stores `_max_output_retries`). Masked by `MagicMock` auto-attributes in tests. Moot once A5's dead branch is deleted.

## B4 — Non-JSON-serializable context crashes generation **[verified]**

`_build_context` → `json.dumps(context, indent=2)` with no `default=`. `default=str` is the one-word fix.

## B5 — `outputs: []` validates, then `IndexError`s in four places **[verified]**

`TemplateMetadata.model_validate({..., "outputs": []})` succeeds; `metadata.outputs[0]` crashes in `run_pipeline`, `api.generate`, `api.validate_artifact`, and `cli.validate`.

`min_length=1` (the prior review's fix) is a patch. **Every consumer indexes `[0]` and nothing reads `[1:]`.** Multi-artifact generation is listed under "Future Extensions" in the concept doc. Collapse `outputs: list[OutputSpec]` to `output: OutputSpec` and the entire bug class disappears rather than being guarded against — and the type stops lying about a capability that doesn't exist.

## B6 — `output_kind` returns the language, making `kind` vacuous **[verified]**

```python
@property
def output_kind(self) -> str:
    return self.metadata.outputs[0].language   # → "toml"
```

`catalog.templates_by_output_kind("toml")` filters by language. `describe` prints `Output kind: toml` for something whose kind is `full_file`. Meanwhile `OutputSpec.kind` is `Literal["full_file"]` — a one-member enum, never read by any code path.

So: the property is wrong, *and* the concept it names is currently empty. Either delete `kind` (nothing depends on it) or fix the property and give `kind` a second member. Deleting is more honest today; "Partial Artifact Rendering" is a Future Extension, and adding the literal back costs one line when it arrives.

## B7 — `resolve_path` does not contain paths to the template root **[verified]**

```yaml
prompt: {file: ../../secret.txt}   # → reads it, no complaint
```

Not filed as security (out of scope). Filed as **portability**: a template that reaches outside its own directory is not a self-contained unit, cannot be copied, zipped, or distributed, and silently breaks when moved. The concept's "Template Registries" extension assumes self-containment. Three lines in `resolve_path` make the property real.

## B8 — CLI `generate` context parsing silently discards the request **[verified]**

`cli.py:331-342`: `user_request` is only extracted inside the `if "facts" in context_data:` branch. A context file of `{"user_request": "Build a CLI tool"}` falls to the `else`, is treated wholesale as project facts, and the request degrades to the stub `"Generate pyproject-uv artifact"`. Non-dict top-level JSON yields empty context with no error. Malformed context should be an error, not a silent no-op.

## B9 — `OutputValidator` has no cross-field validation and no `extra="forbid"` **[verified]**

`OutputValidator(kind="command")` with no `command`, and `OutputValidator(kind="parse")` with no `language`, both validate and then silently no-op at runtime. `OutputValidator(kind="parse", typo=1)` is also accepted — while `TemplateMetadata` sets `extra="forbid"` one level up, so typo-catching is inconsistent across the same file. Make it a discriminated union on `kind`: `ParseValidator(language: str)` | `CommandValidator(command: list[str])`. Wrong metadata then fails at load, which is the whole point of loading it into a typed model.

## B10 — `triggers: dict[str, list[str]]` is an untyped bag in a strictly-typed model **[verified]**

Only `triggers["filenames"]` is ever read; `{"whatever": ["a"]}` validates and is silently ignored. In a codebase whose thesis is "types are the contract," this is the one field that opted out. `trigger_filenames: list[str] = []`.

---

# C — Surface & hygiene

- **C1.** `"openai:gpt-4.1-mini"` is hardcoded in four places (`api.py:156`, `cli.py:294`, `generator.py:38`, `pipeline.py:51`). `DEFAULT_MODEL` exists and is imported by nobody. **[verified]**
- **C2.** `DEFAULT_TEMPLATE_PATHS` in `__init__.py` is referenced nowhere; `cli._get_default_paths` reimplements it. Both point at `src/templateer/templates`, which doesn't exist — `templates/` is at repo root, outside `packages = ["src/templateer"]`, so a wheel ships zero templates. Either `force-include` the templates or drop the bundled-templates story. Don't keep both halves broken. **[verified]**
- **C3.** `OutputValidationError` (`validators.py:20`) is referenced nowhere in `src/` or `tests/`. Dead. **[verified]**
- **C4.** `validation_messages` is plumbed through `models.py`, `api.py`, and `generator.py` and is always `[]`. Remove it, or populate it with the parse/validator warnings that already exist and are currently discarded. **[verified]**
- **C5.** `pydantic-ai>=0.0.20` declares a floor two majors below anything that works (`output_type` doesn't exist there). Real but low-impact for a personal library: `>=2,<3`. **[verified by API inspection]**
- **C6.** The `pyproject-uv` template renders `dependencies = [\n]` and `ignore = [\n]` for empty lists — valid TOML, ugly output. Vanishes under A1 fix (a). **[verified]**
- **C7.** Concept doc uses `validators: - type: parse`; the model requires `kind:`. Doc/impl drift in the one place a template author copy-pastes from.
- **C8.** `_schema_class_cache` is never invalidated, and `sys.modules[f"templateer_template_{name}_{module}"]` persists across `Template` instances — editing a `schema.py` mid-session silently keeps serving the stale class. Papercut, but a confusing one during template authoring.

---

## What is genuinely good (verified, not assumed)

- **MiniJinja strict mode really is strict** — undefined variables raise `TemplateError`; `__import__`, globals, and `self` are unreachable from a template. The sandbox claim holds for the render step.
- **`model_dump(mode="json")` is the only path into the render context.** The invariant the concept cares most about is structurally enforced, not merely intended. (A1 is a layer *below* it, not a breach of it.)
- **Exact-name lookup with no fuzzy matching** is the right call and is implemented cleanly. `catalog.py` is the best module in the repo — 81 lines, one job, no cleverness.
- **`extra="forbid"` on `TemplateMetadata`** catches metadata typos at load time. Exactly right; just apply it consistently (B9).
- **Determinism is real and tested.** `test_rendering_is_deterministic` is one of the genuinely load-bearing tests.
- **The fixture round-trip pattern** (`examples/*.input.json` → `*.output.toml`) is the right way to test a template without an LLM. It just needs to also be *used* at runtime (A7).

---

## The smaller codebase hiding inside this one

Sketching what the concept actually requires, with nothing else:

| Module | Now | After | Change |
|---|---:|---:|---|
| `template.py` | 180 | ~150 | root containment; drop `output_kind` misnomer |
| `catalog.py` | 81 | 81 | keep as-is |
| `renderer.py` | 71 | ~110 | **+ escaping / serializer boundary (A1)**; drop `dict` overload |
| `generator.py` | 151 | ~60 | delete dead post-processing (A5) |
| `validation.py` | 38 | **0** | dead once A5 lands |
| `generation.py` | 135 | ~60 | result type, not state machine (A3) |
| `pipeline.py` | 189 | ~120 | one pipeline, one broad boundary (A2/A4) |
| `api.py` | 340 | ~140 | thin adapter over pipeline (A2) |
| `cli.py` | 467 | ~380 | thin adapter; fix context parsing |
| `models.py` | 88 | ~80 | discriminated validators; single `output` |
| **src total** | **1,886** | **~1,240** | **−34%** |
| `tests/` | 3,455 | ~2,300 | delete tautological + `test_validation.py`; add the four that matter |

Net: about a third less code, one pipeline instead of three, and — the point — a guarantee that is actually true.

---

## Remediation order

1. **A1 — escaping/serialization boundary.** Nothing else matters if a valid model can emit an unauthorized key. Pick option (a) or (b), then add per-language injection tests.
2. **A5 — delete the dead post-processing in `generator.py`** (and `validation.py` with it). Removes an active bug against aliased schemas and shrinks the surface that A4 has to guard.
3. **A4 — one `except Exception` at the pipeline boundary** + `json.dumps(..., default=str)`. Makes the module docstring's promise true. Regression test: no API key → `FAILED`, never raises.
4. **A2 — collapse the three pipelines into one**, with `api`/`cli` as adapters. Fixes H4 and M5 for free, permanently.
5. **A3 — decide what `Generation` is.** Result type is the honest answer; if you keep the entity, store the request on it and split `artifact` from `error_detail`.
6. **A6 — delete the tautological tests**, then add the four behavioral ones. Do this *after* 1–5 so the new tests are written against the intended shape.
7. **B5/B6/B9/B10 — tighten `models.py`**: single `output`, drop or fix `kind`, discriminated validators, typed triggers.
8. **C-series** — dead-code sweep and single-sourcing. Mechanical; batch it last.
