# Templateer v2 — Deep Adversarial Review (round 2)

**Date:** 2026-08-20
**Version reviewed:** 0.2.0 @ `f3b9193` (clean tree)
**Scope:** the concept (`.scratch/projects/01`), the architecture (04-refactor, 05-region), and the shipped code — `src/templateer/` (13 modules, 1,887 LOC), `templates/pyproject-uv/`, `tests/` (14 files, 3,496 LOC), `pyproject.toml`, `README.md`, `CONTRIBUTING.md`.
**Baseline:** `pytest` 257 passed / 9 skipped. `ruff check` clean. `ty check src/` clean.
**Method:** read every source file, then attack each stated guarantee with a runnable probe. Every finding marked **[verified]** was reproduced in this checkout. Two findings come from *mutation testing* — deleting a safety control and observing that the suite stays green. Probe scripts live in `probes/` next to this file.

---

## Verdict

The 04-refactor did what it set out to do. The three-pipeline duplication is gone, the state machine is gone, `escaping.py` is a genuinely good module, and the `GenerationResult` invariants are enforced rather than documented. The codebase is now small, legible, and typed. Compared to the version the last review attacked, this is a different and much better program.

That makes the remaining problems sharper, not softer. Three sentences:

1. **The safety guarantee stops one rung short of where the concept sells it.** Escaping protects the artifact's *lexical* structure. Nothing protects its *semantic* structure. A schema field declared `str` reaches the artifact as a **boolean**, or as **null**, and every layer — model validation, render, output validation, `templateer check` — reports success. **[verified, §A1]**

2. **The project verifies its safety claims with hand-picked positive examples and then states them as proofs.** `escaping.py` says "Verified to round-trip **exactly**" for four languages; exhaustive enumeration finds **34 codepoints** where it does not. **[verified, §B1]** `templateer check` prints `✓ escaping audit passed`; replacing `audit_template` with `return []` leaves **all 257 tests green**. **[verified, §A3]**

3. **`kind: region` shipped a declaration without a data path or an enforcement path.** The README says the region payload check "cannot be omitted or turned off by a template author". One metadata line turns it off, end to end. **[verified, §A5]** And `GenerationResult` carries the page but not the `ref` — the single datum a consumer needs to perform the splice the feature exists for. **[verified, §A6]**

The concept remains excellent and worth defending. The gap between what it claims and what it delivers is now narrow enough to close completely.

---

# Part I — The concept

## The guarantee ladder

Templateer's pitch is a chain of guarantees. It helps to name the rungs, because the codebase delivers a clean prefix and then stops.

| # | Guarantee | Status |
|---|---|---|
| 1 | The LLM produces a model, never a file. | **Enforced.** `output_type=schema_class`; no text path exists. |
| 2 | The renderer receives only `model.model_dump(mode="json")`. | **Enforced.** `renderer.py:36` is the single construction site. |
| 3 | A value cannot alter the artifact's *lexical* structure. | **Enforced with caveats.** True for `str` scalars, in the 4 quoted-string languages, inside a double-quoted context. False for containers (§B4), false outside those 4 languages (§A2), false for unquoted contexts (§A1). |
| 4 | A value cannot alter the artifact's *semantic* structure. | **Missing.** §A1. |
| 5 | The artifact's data equals the model's data. | **Not attempted.** No test, no validator, no claim tested. |

The concept doc's own words are rung 5: *"The rendered artifact is composed only from validated structured data."* The README's words are rung 4: *"a validated model cannot alter the artifact's structure."* The code delivers rung 3.

**This gap is the review.** Everything in Part III is either a hole in rung 3 or a consequence of rung 4 being absent.

## The decision that created the gap

The 04-refactor guide opens with it, honestly:

> You asked for **one way**: Jinja renders everything, including structured data, with after-the-fact checks confirming it came out right.

The alternative — for `language in {toml, json, yaml}`, build a `dict` and call `tomli_w.dumps` / `json.dumps` / `yaml.safe_dump` — is *correct by construction*: no escaping bug is possible, no authoring rule is needed, and rungs 3, 4 and 5 all close at once. The guide rejected it for a real reason: one rendering mode is simpler than two.

That reasoning was sound at the time. It is worth revisiting now, because the cost has become visible:

- The escaping module needs a per-language escape grammar, and its grammar is wrong for YAML (§B1).
- The correctness of every template depends on an **unenforced authoring rule** ("every string interpolation sits inside double quotes"). The guide promised this rule would be "mechanically checkable by `templateer check`". It is not checked (§A3).
- A whole class of bug — type confusion — has no defence at all (§A1).
- The audit exists only because the render path is unsafe by default. A serializer path needs no audit.

**Recommendation:** keep one rendering mode, but stop pretending the *authoring rule* is optional to enforce. The cheap version of option (a) is a **structural post-check**: after rendering, parse the artifact and compare it against the model dump (§A1 fix). That closes rungs 4 and 5 without a second renderer and without touching a single template.

## "Agent-facing" is a claim about the interface

The README's first line is "for AI agents". The API docstring says "suitable for embedding in ... agent frameworks". Two structural facts contradict that:

**The CLI speaks prose.** Of seven commands, exactly one (`schema`) emits machine-readable output. `generate` failure prints `Generation failed: output_validation_failed` on stderr and exits 1. `validate` prints `✓ Model validated against schema`. `describe` prints a raw Python set literal — `Trigger paths: {'pyproject.toml'}` **[verified]**. Every command exits `1` for every error class, so an agent cannot distinguish "no such template" from "the LLM failed" from "the artifact is invalid" without parsing English. `GenerationResult` already holds `{failure_reason, error_detail, attempt, warnings, model, output_path}` — a structured, versioned failure object that the CLI throws away.

**The Python API is sync-only.** `TemplateRegistry.generate` calls `agent.run_sync`. From inside a running event loop that raises `RuntimeError: This event loop is already running` **[verified, §A8]**. pydantic-ai, LangGraph and the Claude Agent SDK are all async. The single most likely embedding context cannot call the primary entry point.

Neither is hard to fix. Both are load-bearing for the stated audience.

## The retry loop does not learn

`pipeline.generate` retries `LLM_FAILED`, `MODEL_VALIDATION_FAILED` and `OUTPUT_VALIDATION_FAILED` up to `max_attempts`. Verified behaviour: **3 attempts, byte-identical input each time; `error_detail` is never fed back to the model; there is no backoff.** **[verified, §A9]**

This is the one place where the concept has a genuinely powerful move available and does not make it. The concept doc says so itself:

> validation errors can be fed back to the model

pydantic-ai already does this *inside* one call, for schema violations. The outer loop is for the failure class pydantic-ai cannot see: **the artifact did not validate**. Re-asking with `"your previous answer rendered to TOML that failed to parse: <error>"` is the repair loop the design implies. Re-asking with the same prompt is a slot machine that costs 3× the tokens.

Either feed the failure back, or delete the outer retry and let `max_attempts` mean what pydantic-ai already provides.

## `kind: region` imports a foreign domain

`RegionBoundary` is `{page, ref, anchor}` — vocabulary from argentic.space's page model. Templateer never interprets any of the three. It prints them in `describe`, and it copies `region.page` into `output_path`. They are pure passthrough metadata.

Passthrough is a defensible design. But the feature does not actually pass them through: `GenerationResult` has no `ref` (§A6). So the consumer receives a payload and a page name and must reopen the template metadata by name to learn which block to splice. The declaration exists; the data path does not.

Two honest resolutions, in order of preference:

1. **Make it generic and complete.** Rename to a domain-neutral shape (`kind: fragment`, `slot: {locator: str, ...}`) and put the locator on the result. Templateer then declares "this artifact is a bounded payload for the slot identified by *this opaque locator*", which is true, useful, and not coupled to one consumer.
2. **Keep the argentic vocabulary but plumb it.** Add `region: RegionBoundary | None` to `GenerationResult`.

Doing neither leaves a feature that names a boundary it cannot help anyone honour.

## Where the concept is genuinely right

Stated plainly, because these are the load-bearing decisions and they are correct:

- **The trust boundary is in the right place.** Prompt injection through `user_request` or `context` can only ever produce *a valid instance of the schema*. That is the whole design, and it works. An attacker who fully controls the LLM's output still cannot add a key the schema does not declare — provided rungs 3–4 hold. Fix rung 4 and this becomes an unusually strong property for an LLM system.
- **Exact-name lookup, no fuzzy matching.** Correct, and `catalog.py` remains the cleanest module in the repo.
- **Failure as a value, not an exception.** `GenerationResult` with a typed `FailureReason` is right for an agent-facing library, and the invariants are enforced in a `model_validator` rather than merely documented.
- **`extra="forbid"` on metadata**, and the `OutputValidator` discriminated union. Malformed metadata fails at load. This is the project's own best pattern — it should be applied to `OutputSpec` (§C8) and to `language` (§A2).
- **The MiniJinja `finalizer` hook.** One interception point, applied to every `{{ }}` site and nowhere else, impossible for a template author to route around. This is a genuinely elegant piece of engineering. Its *grammar* is wrong for YAML (§B1); its *architecture* is right.
- **`examples/*.input.json` as a few-shot exemplar.** Reusing an already-round-trip-tested asset at prompt time is exactly the right kind of frugal.

---

# Part II — Method finding

**The project's verification method is positive-example-based, and it is stated as proof.** This is one root cause with three visible symptoms, so it deserves naming before the findings list.

| Claim | How it was verified | What exhaustive checking finds |
|---|---|---|
| `escaping.py`: "Verified to round-trip **exactly** through tomllib / json / yaml.safe_load / ast.literal_eval" | 18 hand-picked adversarial payloads (04 guide §"One escaper") | **34 codepoints fail** — YAML rejects U+0080–U+009F outright, silently folds U+0085, and both YAML and Python break on lone surrogates. §B1 |
| README: "`templateer check` … 0 findings is wired into the test suite" | One assertion: `audit_template(...) == []` | The audit has **no negative test**. Replace its body with `return []` → **257/257 still pass**. §A3 |
| README: region check "cannot be omitted or turned off by a template author" | Not tested | `validators: [{kind: markdown, optional: true}]` turns it into a warning. Generation succeeds with a page-corrupting payload. §A5 |
| 04 guide: the authoring rule is "mechanically checkable by `templateer check` (Phase 8)" | Not implemented | `check` never reads the template source. A template with `name = {{ x }}` and no fixtures passes. §A3 |

The pattern: a claim is tested with inputs chosen by the person who wrote the claim, and then written down as an absolute. Three of the four absolutes are false.

**Fix the method, not just the instances.** For the escaper, enumerate the codepoint space (it is small — `probes/escape_chars.py` does it in 40 lines). For the audit, add the negative test first. For every "cannot be bypassed" sentence in the README, write the test that tries to bypass it.

---

# Part III — Findings

Ranked by consequence. Every finding has a one-command reproduction in `probes/`.

## A — Concept & architecture

### A1 — A `str` field reaches the artifact as a boolean or as null **[verified]** — *Critical*

This is the finding. Rung 4.

The finalizer produces *content safe inside a double-quoted string literal*. Whether that content lands inside quotes is an **unenforced authoring rule**. When it does not, the value is re-lexed by the target language.

```python
# schema.py:  title: str   owner: str
# template.j2:  title: {{ title }}\nowner: {{ owner }}
render_from_model("yamlvuln", {"title": "true", "owner": "#redacted"})
```

```
artifact         'title: true\nowner: #redacted'
yaml.safe_load   {'title': True, 'owner': None}      <-- str -> bool, str -> null
validate_artifact []                                  <-- passes
templateer check  ✓ escaping audit passed             <-- passes
```

Same class in TOML: `name = {{ v }}` with `v="123"` yields the integer `123`; with `v="true"` yields the boolean `true`. **[`probes/p_escaping_holes.py`]**

**The audit cannot see this by construction.** `audit._key_paths` compares only the *set of key paths* before and after poking a payload. A change to a value's type or content leaves the key set identical. The audit is blind to every injection that does not add or remove a key.

**Why it matters more than it looks.** The concept's claim is not "the artifact parses". It is that the artifact is *composed from validated data*. A consumer that reads `artifact["title"]` expecting the `str` the schema promised gets `True`. Pydantic validated a `str`; the artifact carries a `bool`; nothing in the pipeline noticed.

**Fix (closes rungs 4 and 5 together, ~30 lines, no template changes).** Add a structural post-check to the pipeline for the four parseable languages:

```python
def check_round_trip(artifact: str, language: str, model_dump: dict) -> list[str]:
    """Every scalar the model declared must appear in the artifact with its
    declared type.  Parse the artifact; walk the model dump; assert type and
    value agreement for every leaf that the template interpolated."""
```

A weaker but still valuable version: assert that every `str` leaf of the model dump that appears in the artifact appears **as a string**. That single rule catches the whole type-confusion class.

**Complementary fix (cheap, do both).** Make the authoring rule mechanically checkable, as the 04 guide promised: scan the template source for `{{ ... }}` sites in a structured-language template and report any that are not delimited by `"` on both sides. This is a regex over the template file, and it turns the rule from a convention into a gate.

---

### A2 — One unvalidated free-text string selects every safety layer **[verified]** — *High*

`output.language` is `str`. It controls three independent things:

| Consumer | Behaviour when `language` is unknown |
|---|---|
| `escaping.make_finalizer` | **Identity** — no escaping at all. |
| `validators.validate_output` | **No parse check** — silently skipped. |
| `audit.audit_template` | **Returns `[]`** — reports "sound" without auditing. |

So `language: tomI` (capital i), `language: yml`, or `language: Python` disables all three, silently, with no warning at load or run time. `extra="forbid"` catches a mistyped *key*; nothing catches a mistyped *value*.

This is not hypothetical for real templates. The concept doc's motivating list opens with `devenv.nix` and `flake.nix`. A Nix template today gets identity escaping, no output validation, and a green `templateer check` **[verified, `probes/p_audit_vacuous.sh`]** — while the README states "every value interpolated into an artifact is escaped for the target language".

**Fix.** Make the language set closed and explicit:

```python
Language = Literal["toml", "json", "yaml", "python", "markdown", "text", ...]
```

and require an unstructured target to say so. Anything not in the set fails at template load — which is exactly the discipline the codebase already applies to `OutputValidator`. Then the README's sentence becomes true by construction, and adding Nix support becomes a visible, deliberate act (an escape grammar plus a parser) rather than a silent no-op.

---

### A3 — `templateer check` prints a proof and performs a spot check **[verified]** — *High*

The audit is the project's only adversarial control and its only claim of security testing. Three problems compound.

**(a) It is untested in the direction that matters.** Mutation test:

```
# src/templateer/audit.py :: audit_template()
    return []   # MUTANT: audit is a no-op

$ pytest -q
257 passed, 9 skipped
```

**[verified, `probes/mutate.sh`]** The only audit assertion in the suite is `audit_template(bundled) == []`. There is no test that the audit **can** return a finding. It could be entirely broken and CI would be green. Coverage confirms it: `audit.py` lines 77–79, 89–91, 94–96, 98 — every finding-emitting branch — are never executed.

**(b) It reports `✓ escaping audit passed` in three cases where it audited nothing.** **[verified]**

| Case | Result |
|---|---|
| Template has no `examples/` directory | `✓ passed`, exit 0 — zero fixtures probed |
| `language` not in `{toml,json,yaml,python}` | `✓ passed`, exit 0 — early `return []` |
| A nullable/optional field absent from the fixture | Never probed — coverage is fixture-shaped |

A template with a deliberately unquoted `name = {{ x }}` and no fixtures passes `check` with a green tick.

**(c) Its detection model is too weak for its languages.** The single `PAYLOAD = '"\nINJECTED = "yes'` is TOML/Python-shaped. For a YAML or JSON template it is not an injection payload at all, so a vulnerable YAML template audits clean. Combined with `_key_paths` ignoring values entirely (§A1), the audit's true statement is narrow: *"no fixture-present string field, poked with one TOML-shaped payload, added a key to the parsed artifact."*

**Fix, in order:**
1. Add the negative test — a deliberately vulnerable fixture template that the audit **must** flag. Do this first; it is the guard for everything else.
2. Make silence loud: `audit_template` should return a *report*, not a list of findings. `check` should print `✓ 12 probes across 2 fixtures, 0 findings` or `⚠ nothing audited: no examples/`. Exit non-zero (or at least warn) when nothing was audited.
3. Give each language its own payload set, and compare parsed *values*, not only key paths.
4. Add the source-level unquoted-interpolation lint promised by 04 Phase 8.

---

### A4 — `pipeline.generate()`'s absolute promise is false **[verified]** — *High*

`pipeline.py:8` — *"Every failure returns a GenerationResult. Nothing escapes as an exception — that promise is either total or worthless."*

```
!!! EXCEPTION ESCAPED pipeline.generate():
    TemplateLoadError: Template 'escape': path '../../outside.j2' escapes the template root
```

**[verified, `probes/p_pipeline_escape.py`]** Step 3 catches only `RenderError`. `Template.render` calls `resolve_path`, which raises `TemplateLoadError` for a renderer file outside the template root. The exception passes straight through `generate()` and out to the caller.

The docstring states the correct standard. Meet it: one `except Exception` at the pipeline boundary mapping to a `FailureReason`, exactly as the module's own words demand.

---

### A5 — The region safety property the README calls non-negotiable is opt-out **[verified]** — *High*

README: *"For `kind: region` templates the check is **enforced automatically** — it is the safety property the kind exists to declare, so it cannot be omitted or turned off by a template author."*

`effective_validators` returns `declared` unchanged if *any* declared validator is a `MarkdownValidator`. It does not check `optional`. So:

```yaml
output: {kind: region, region: {page: docs/status.md, ref: $block-status}, language: text}
validators:
  - kind: markdown
    optional: true          # <-- turns the "non-negotiable" check into a warning
```

End-to-end result **[verified, `probes/p_region.py`]**:

```
succeeded : True
warnings  : ["unclosed '```' fence: expected closing '```', got 'just a sentence, not a mapping'"]
artifact  : '```\njust a sentence, not a mapping'
```

A `kind: region` generation **succeeds** with a payload that will corrupt the hosting page's fenced block.

Two independent bypasses, both one line:
- `optional: true` on the declared markdown validator (above).
- `language: markdown` or `language: text` — which disables escaping entirely, so a plain `str` field injects arbitrary keys into the page's data block, and `validate_region_payload` reports zero errors because the injected YAML is perfectly valid YAML. **[verified, `probes/p_region.py`]**

**Fix.** `effective_validators` must prepend a *non-optional* `MarkdownValidator` for `kind: region` regardless of what is declared, and deduplicate on kind only for the non-optional case. Separately, constrain a region template's `language` to the set whose escaping actually protects the payload (§A2).

---

### A6 — The region result cannot be consumed **[verified]** — *Medium*

`GenerationResult` fields: `request, output_path, model, artifact, failure_reason, error_detail, warnings, attempt`.

For `kind: region`, `output_path` is set to `region.page`. The consumer's job is `replace_range` on the block identified by `region.ref`. **`ref` is not on the result.** The consumer must call `registry.get_template(name).metadata.output.region.ref` to finish the operation the result was produced for.

Worse, `output_path` now means two different things — a file path or a page name — with no field to disambiguate. A generic consumer cannot tell which it received.

**Fix.** Add `region: RegionBoundary | None` (and, ideally, `kind`) to `GenerationResult`. This is 3 lines and makes the feature usable.

---

### A7 — "Agent-facing" without a machine-readable surface **[verified]** — *Medium*

See Part I. Concretely: add `--json` to `generate`, `validate`, `render`, `check`, `describe` and `list`; emit `GenerationResult.model_dump()` verbatim on `generate`. Give distinct exit codes per `FailureReason`. Fix `describe` printing a Python `set` repr.

### A8 — Sync-only API blocks the stated audience **[verified]** — *Medium*

`RuntimeError: This event loop is already running` **[verified, `probes/p_surface.py`]**. Add `generate_async` using `agent.run`, and make `generate` a thin `asyncio.run` wrapper over it. The pipeline is otherwise pure; this is a small change with a large audience effect.

### A9 — The retry loop does not learn **[verified]** — *Medium*

3 identical calls, no feedback, no backoff, warnings discarded **[verified, `probes/p_surface.py`]**. See Part I. Also: `max_attempts` has `ge=1` and no ceiling — `max_attempts=100000` validates.

---

## B — Correctness

### B1 — `escape_string` is wrong for YAML and Python on 34 codepoints **[verified]** — *High*

`escaping.py:15` claims: *"Verified to round-trip **exactly** through tomllib / json / yaml.safe_load / ast.literal_eval."* Exhaustive enumeration of U+0000–U+011F plus the interesting outliers **[verified, `probes/escape_chars.py`]**:

| Codepoints | toml | json | **yaml** | **python** |
|---|---|---|---|---|
| U+0080–U+0084, U+0086–U+009F (C1 controls) | ok | ok | **`ReaderError`** | ok |
| U+0085 (NEL) | ok | ok | **silent corruption → space** | ok |
| U+D800–U+DFFF (lone surrogates) | ok | ok | **`ReaderError`** | **`UnicodeEncodeError`** |

`json.dumps(..., ensure_ascii=False)` leaves all of these bare. The module already patches U+007F for exactly this reason; the patch is one character short of a rule.

**Consequences.** A C1 control character in any `str` field — the classic Windows-1252 mojibake from copy-pasted text, which an LLM will happily reproduce — makes a YAML artifact **fail to parse**. The pipeline then burns `max_attempts` LLM calls and returns `output_validation_failed` with a message about an "unacceptable character". U+0085 is worse: it round-trips to a *different string*, silently.

**Fix, verified to work** (`probes/escape_fix.py` — reduces 36 broken pairs to 2):

```python
_UNSAFE = re.compile(r"[\x00-\x1f\x7f-\x9f  \ud800-\udfff]")

def escape_string(value: str) -> str:
    out = json.dumps(value, ensure_ascii=False)[1:-1]
    return _UNSAFE.sub(lambda m: "\\u%04x" % ord(m.group()), out)
```

The 2 remaining pairs are lone surrogates in TOML, which are genuinely unrepresentable (`Escaped character is not a Unicode scalar value` **[verified]**). Raise `EscapeError` for them — that exception exists and currently fires only for `None`.

**Then add the exhaustive test**, not 18 more payloads.

---

### B2 — Path containment is not enforced for the one file that is executed **[verified]** — *Medium*

README rule 6: *"paths are resolved relative to the template root, and a path escaping the root is a load error."*

`resolve_path` enforces this for `prompt.file` and `renderer.file`. `load_schema_module` does not use it:

```python
schema_file = self.root / f"{module_name}.py"     # template.py:125 — no containment check
```

```
metadata.yml:  schema: {module: ../../outside_schema, class: M}
→ LOADED + EXECUTED: schema module executed from OUTSIDE the template root
```

**[verified, `probes/p_pipeline_escape.py`]**

Templates are trusted code, so this is filed on the axis the README uses — **self-containment / portability**, not privilege. But it is the *executed* file, and it is the one place the rule was not applied. One line: route it through `resolve_path`.

---

### B3 — The region fence tolerance is unreachable and documents the wrong policy **[verified]** — *Medium*

`validate_region_payload` accepts a fenced payload:

```
validate_region_payload("```yaml\nstatus: ok\n```")  -> []
```

Through the pipeline, the built-in `yaml` parse validator rejects the same text first:

```
validate_output(...)  -> ["yaml parse failed: found character '`' that cannot start any token"]
```

**[verified, `probes/p_region.py`]** So the tolerance is dead code on every real path.

It is also the wrong policy. D1 in the 05 guide states the contract: *"The page owns the fences … a region template's artifact is **bare YAML**."* An artifact that carries fences will be spliced *into* an already-fenced block, producing a double fence. Tolerating it accepts an artifact that corrupts the page. Delete the tolerance and make a leading fence line an error — the contract, stated once, enforced once.

The 05 guide also names an unfinished item here: *"Before shipping, pin the exact fence grammar of argentic's `replace_range` against this implementation."* Still open.

---

### B4 — Container interpolation bypasses the finalizer **[verified]** — *Medium*

```python
render('deps = {{ deps }}', "toml", deps=['a"\nINJECTED = "yes'])
→  deps = ['a"\nINJECTED = "yes']
```

**[verified]** The finalizer returns non-`str`, non-`bool`, non-`None` values unchanged, so MiniJinja's own value-to-string conversion produces the output. That conversion is Python-repr-shaped and **single-quoted** — a TOML *literal* string, where escapes do not apply and a `'` in the data breaks out. It is also language-blind: `{'k': 'v'}` is valid Python and invalid TOML/JSON.

The README says "every value interpolated into an artifact is escaped for the target language". That is false for lists and dicts.

**Fix.** Make the finalizer explicit about containers: either serialize them for the target language, or raise `EscapeError` ("interpolate list/dict elements with a `{% for %}` loop, not the container"). Raising is the smaller, more honest change and matches the existing `None` handling.

---

### B5 — `{}` and `[]` pass region validation **[verified]** — *Low*

README and 05 D7 both say *"bare scalars and empty payloads are rejected (a generated empty payload is a bug)"*. `validate_region_payload("{}")` → `[]`; `validate_region_payload("[]")` → `[]`. **[verified]** An empty mapping is exactly the generated-empty-payload case D7 names.

### B6 — Warnings are dropped on failure **[verified]** — *Low*

`_attempt`'s `fail()` never passes `warnings`, so optional-validator output is lost whenever the generation also has an error — precisely when the diagnostic is most useful. **[verified, `probes/p_surface.py`]**

### B7 — `CommandValidator` reports only stderr, and is never tested — *Low*

`validators.py:216` captures both streams and appends only `result.stderr`. Ruff, and most linters and formatters, write diagnostics to **stdout**. The common case therefore yields `Command 'ruff check -' failed` with no detail. The whole branch (`validators.py:206–226`) has **0% test coverage** — the only code in the project that spawns a subprocess is unexercised.

### B8 — `api.render_from_model` uses `**` unpacking instead of `model_validate` **[verified]** — *Low*

```
api  -> TypeError: ...() argument after ** must be a mapping, not list
cli  -> ValidationError: 1 validation error for PyprojectUvModel
```

Two surfaces, two error types, for the same mistake. Use `model_validate` in both. Related: `api.validate_artifact` discards `warnings`, so an optional validator that fails is invisible through the Python API.

### B9 — A broken template disappears with exit code 0 **[verified]** — *Low*

`TemplateCatalog.load_from_paths` logs a warning and continues. `templateer list` then prints `No templates found.` and exits **0**. The Pydantic error text does reach stderr via logging's last-resort handler, but no machine-readable signal distinguishes "template is broken" from "template is absent". For an agent-facing CLI, add a `--strict` flag or surface load errors in the JSON output.

### B10 — Dead code **[verified]**

- `except TemplateLoadError` at `pipeline.py:58` and `cli.py:80` — `catalog.get` raises only `TemplateNotFoundError`. Unreachable.
- `TemplateCatalog.templates_by_language` — one caller: its own test.
- `Template.output_kind` — zero callers anywhere, including tests.
- `validators.py:229` — `bucket = warnings if validator.optional else errors` repeats line 195 verbatim inside the same loop iteration.

---

## C — Packaging, docs, hygiene

### C1 — The wheel ships zero templates **[verified]** — *High for a released library*

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/templateer"]          # templates/ lives at repo root

[tool.hatch.build.targets.sdist]
include = ["src", "README.md", "tests", "pyproject.toml"]   # no templates
```

`pip install templateer` → `templateer list` → `No templates found.` The 04 guide's Phase 11 flagged this and offered two fixes; **neither was applied.** The other Phase 11 items (dead `DEFAULT_TEMPLATE_PATHS`, `OutputValidationError`, the hardcoded model string, the dependency floor) *were* all completed — this is the one that slipped.

Decide and act: `force-include` the templates, or delete the bundled-templates story from the README and make `./templates` + `-p` the only documented sources.

### C2 — `import templateer` exports nothing **[verified]** — *Low*

`dir(templateer)` → `[]` beyond dunders. The README's own example needs `from templateer.api import TemplateRegistry`. Export `TemplateRegistry`, `GenerationResult`, `GenerationRequest`, `FailureReason` from `__init__`. Also: `__version__` is duplicated between `__init__.py` and `pyproject.toml` with no sync mechanism.

### C3 — `CONTRIBUTING.md` describes a codebase that no longer exists **[verified]** — *Low*

Documents `generation.py` and `validation.py` (both deleted in the 04 refactor). Omits `audit.py`, `escaping.py`, `result.py`. It also still says `__init__.py # Version, default template paths`; the default paths are gone.

### C4 — `CLAUDE.md` / `AGENTS.md` is the unedited seed **[verified]** — *Low*

Still contains `_One paragraph: what it does, who uses it, what it is not._` and `_Add the build / test / lint commands…_`. The one file every agent reads first is a template.

### C5 — `load_example` picks alphabetically and trusts an untested assumption — *Low*

`sorted(...)[0]` means `full.input.json` beats `minimal.input.json` by accident of spelling. The docstring says the fixture "is already schema-validated by the template's own tests" — that is an assumption about tests existing, not an enforced invariant. A template with no tests ships an unvalidated exemplar straight into the LLM prompt. Validate it against the schema at load, and let metadata name the exemplar.

### C6 — `sys.modules` is polluted on a failed import — *Low*

`template.py:137` assigns `sys.modules[spec_name] = module` *before* `exec_module`. A `schema.py` that raises during import leaves a half-initialized module registered. Assign after, or `del` on failure.

### C7 — No usage or cost data on the result — *Low*

pydantic-ai returns token usage; `generator.generate_model` discards it. For a library whose thesis is "this makes the task small enough for small models", tokens-per-artifact is the metric that proves the thesis. It is free to collect.

---

# Part IV — Remediation order

Ordered so each step's test is written before the step that could break it.

| # | Work | Fixes | Exit condition |
|---|---|---|---|
| 1 | **Negative test for the audit** — a deliberately vulnerable fixture template the audit must flag. | A3(a) | The `return []` mutant fails the suite. |
| 2 | **Exhaustive escaper test + fix** — enumerate the codepoint space; add the `_UNSAFE` sub; `EscapeError` on lone surrogates. | B1 | 0 broken codepoint/language pairs. |
| 3 | **Close rung 4** — structural post-check comparing the parsed artifact against the model dump; plus the source-level unquoted-interpolation lint. | **A1** | The `title: true` reproduction fails validation. |
| 4 | **Close `language`** — `Literal` set; unknown language is a load error. | A2 | A typo'd language fails at load, not at runtime. |
| 5 | **Total the pipeline promise** — one `except Exception` at the boundary; containment for `schema.module`; container handling in the finalizer. | A4, B2, B4 | `probes/p_pipeline_escape.py` returns a result. |
| 6 | **Repair the region kind** — non-optional prepend regardless of declaration; constrain region `language`; `region` + `kind` on `GenerationResult`; delete the fence tolerance; reject `{}`/`[]`; pin the fence grammar against argentic. | A5, A6, B3, B5 | `probes/p_region.py` fails to generate. |
| 7 | **Make the surface agent-facing** — `--json` everywhere, distinct exit codes, `generate_async`, fix `describe`. | A7, A8 | An agent can drive every command without parsing prose. |
| 8 | **Fix or delete the retry** — feed `error_detail` back into the next attempt, or drop the outer loop. Cap `max_attempts`. Carry warnings on failure. | A9, B6 | Attempt 2's prompt differs from attempt 1's. |
| 9 | **Ship the templates** — decide, then make `pip install` + `templateer list` work or stop claiming it. | C1 | A wheel built from a clean tree lists ≥1 template. |
| 10 | **Sweep** — dead code, `**`→`model_validate`, stderr+stdout in `CommandValidator` (with a test), `__init__` exports, CONTRIBUTING, AGENTS.md, `sys.modules`, usage stats. | B7–B10, C2–C7 | `ruff`, `ty`, `pytest` green; docs match reality. |

**Do 1 and 2 first.** They are cheap, and they repair the *method* — after them, every later claim in this list is checkable rather than assertable.

---

# Part V — The shape this wants to be

Not a rewrite. The 04-refactor already found the right decomposition. These are the deltas that make the guarantee true.

| Module | Now | Delta |
|---|---:|---|
| `escaping.py` | 67 | `+ _UNSAFE` sub; `EscapeError` for surrogates and containers. **+10** |
| `models.py` | 134 | `Language` literal; `OutputSpec` as a real discriminated union on `kind`; `max_attempts` ceiling. **+15/−10** |
| `validators.py` | 252 | `+ check_round_trip` (rung 4); non-optional region prepend; delete fence tolerance; stdout in command errors. **+40/−25** |
| `audit.py` | 103 | Return an `AuditReport` (probes run, fixtures seen, findings) not a bare list; per-language payload sets; value comparison; source-level unquoted-interpolation lint. **+60** |
| `pipeline.py` | 117 | One boundary `except`; error feedback into the retry; warnings on failure. **+15** |
| `result.py` | 93 | `kind` + `region` on the result; optional `usage`. **+8** |
| `api.py` | 241 | `generate_async`; `model_validate`; expose `audit`; return warnings. **+30** |
| `cli.py` | 493 | `--json` on every command; exit codes per `FailureReason`; fix `describe`. **+50/−20** |
| `template.py` | 197 | `resolve_path` for `schema.module`; validate the exemplar; `sys.modules` hygiene; drop `output_kind`. **+5/−10** |
| `catalog.py` | 81 | Drop `templates_by_language`; surface load errors. **±0** |
| **src total** | **1,887** | **≈ +170 net.** The codebase gets slightly bigger. What it buys is that every sentence in the README becomes true. |

That is the trade worth making. The 04-refactor bought elegance by removing duplication; this round buys correctness by adding the enforcement the elegance was standing in for. A concept this good deserves a codebase whose claims survive an exhaustive check — not just a well-chosen one.

---

## Appendix — reproductions

All probes assume the repo root and the checked-in `.venv`.

| Probe | Shows |
|---|---|
| `probes/p_escaping_holes.py` | §A1 unquoted interpolation: `str` → bool / int / null, TOML and YAML |
| `probes/p_audit_blind.sh` | §A1/§A3 the audit passes on the corrupted artifact |
| `probes/escape_chars.py` | §B1 the 34 failing codepoints, per language |
| `probes/escape_fix.py` | §B1 candidate fix: 36 broken pairs → 2 |
| `probes/mutate.sh` | §A3 `audit_template → return []` leaves 257/257 green |
| `probes/p_audit_vacuous.sh` | §A2/§A3 `✓ passed` with no fixtures, and with `language: nix` |
| `probes/p_pipeline_escape.py` | §A4 `TemplateLoadError` escapes `generate()`; §B2 schema module executed from outside the template root |
| `probes/p_region.py` | §A5 region generation succeeds with a page-corrupting payload; `language: markdown` disables region escaping; §B3 fence tolerance unreachable; §B5 `{}` passes |
| `probes/p_surface.py` | §A8 `run_sync` inside a running event loop; §A9/§B6 3 identical attempts, warnings dropped; §B8 `**` vs `model_validate`; §C2 empty exports |
| `probes/fuzz_escape.py` | §B1 16,000 random cases; 1,236 failures, all YAML/Python |
