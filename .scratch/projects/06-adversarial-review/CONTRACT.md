# The shared API contract

Every wave reads this file. It pins the names, signatures and semantics that
cross a file boundary. If you need to change something here, stop and report —
do not change it alone. Another agent is coding against it right now.

Source of truth for behaviour: `TEMPLATEER-V2_ADVERSARIAL_REVIEW_2.md` and
`KICKOFF.md`. This file only removes ambiguity.

---

## 1. `models.py` — the language set (§A2, §A5, §C8)

```python
StructuredLanguage = Literal["toml", "json", "yaml", "python"]
UnstructuredLanguage = Literal["markdown", "text"]
Language = StructuredLanguage | UnstructuredLanguage
```

`OutputSpec` becomes a real discriminated union on `kind`:

```python
class FullFileOutput(BaseModel):
    model_config = {"extra": "forbid"}
    kind: Literal["full_file"] = "full_file"
    path: str
    language: Language

class RegionOutput(BaseModel):
    model_config = {"extra": "forbid"}
    kind: Literal["region"]
    # The 05 contract says the payload is a YAML data block.  markdown/text
    # give identity escaping — the hole this kind exists to close.
    language: Literal["yaml"] = "yaml"
    region: RegionBoundary
    path: str | None = None      # informational; region.page is the anchor

OutputSpec = Annotated[
    FullFileOutput | RegionOutput,
    Field(discriminator="kind"),
    BeforeValidator(_default_kind),   # inject kind="full_file" when absent
]
```

```python
def _default_kind(v: Any) -> Any:
    if isinstance(v, dict) and "kind" not in v:
        return {**v, "kind": "full_file"}
    return v
```

`_default_kind` keeps existing metadata that omits `kind` loading unchanged.
`templates/pyproject-uv/metadata.yml` omits it; that must keep working.

**This design is verified.** It was run against pydantic in this checkout on
2026-08-20. Confirmed: `kind` omitted loads as `full_file`; a region loads and
keeps `region.ref`; and each of these raises `ValidationError` — an unknown
`language`, `kind: region` with `language: markdown`, `kind: region` with
`region: null`, `kind: full_file` carrying a `region`, and an extra key inside
`region`. `TypeAdapter(OutputSpec)` validates directly. Build it this way.

Consequences other agents rely on:

- `template.metadata.output.kind` still reads `"full_file"` / `"region"`.
- `template.metadata.output.region` exists on `RegionOutput` only. Use
  `getattr(output, "region", None)` or narrow on `kind` before you touch it.
- `OutputSpec` is a type alias, not a class. Construct `FullFileOutput(...)`
  or `RegionOutput(...)`. Validate with `TypeAdapter(OutputSpec)`.
- An unknown `language` now raises at template load.

**`ParseValidator.language` closes too.** It was free text:

```python
class ParseValidator(BaseModel):
    kind: Literal["parse"]
    language: StructuredLanguage      # was: str
    optional: bool = False
```

A `parse` validator only ever runs for the four structured languages —
`validate_output` looks the language up in `parser_validators` and skips it
otherwise. So `language: "tomll"` silently disabled the check the author asked
for. That is the exact §A2 failure mode, one level down, and the review missed
it. Closing it makes the typo a load error.

Otherwise `models.py` does not move. `RegionBoundary` keeps `{page, ref,
anchor}`.

## 2. `result.py` (§A6, §C7, §A9)

```python
class GenerationRequest(BaseModel):
    ...
    max_attempts: int = Field(default=3, ge=1, le=10)

class GenerationResult(BaseModel):
    ...                                   # every existing field stays
    kind: Literal["full_file", "region"] = "full_file"
    region: RegionBoundary | None = None
    usage: dict[str, int] | None = None   # token counts, None when unknown
```

`result.py` may import `RegionBoundary` from `models.py`. There is no cycle.

## 3. `escaping.py` (§B1, §B4)

```python
def escape_string(value: str) -> str: ...
```

- Signature unchanged. Escapes `[\x00-\x1f\x7f-\x9f  ﻿]` as
  `\uXXXX` on top of `json.dumps(..., ensure_ascii=False)[1:-1]`.
- Raises `EscapeError` on any lone surrogate (U+D800–U+DFFF), for **every**
  language. Uniform: a lone surrogate is never legitimate content.
- The finalizer raises `EscapeError` for `list`, `dict`, `tuple` and `set`.
  Message names the fix: interpolate elements with `{% for %}`, not the
  container.
- `int` and `float` still pass through unchanged.

Do not break `templates/pyproject-uv/template.j2`. It uses
`{{ dependency.extras | join(',') }}` — filter output is a `str`, not a list.

## 4. `validators.py` (§A1, §A5, §B3, §B5, §B7, §B10)

```python
def check_round_trip(
    artifact: str, language: str, model_dump: dict[str, Any]
) -> list[str]: ...
```

Returns findings; empty means the artifact's data agrees with the model's.

Algorithm — the rule that catches the type-confusion class without false
positives on legitimate templates:

1. Return `[]` when `language` is not one of the four structured ones.
2. Parse the artifact. For `python`, collect `ast.Constant` values; for the
   other three, walk the parsed data.
3. Collect every `str` leaf of `model_dump`.
4. For each such string `v`: compute `relex(v, language)` — what the target
   language reads if `v` lands unquoted:
   - `yaml`  → `yaml.safe_load(v)`
   - `json`  → `json.loads(v)`
   - `toml`  → `tomllib.loads(f"x = {v}")["x"]`
   - `python`→ `ast.literal_eval(v)`
   A parse failure means `v` cannot be re-lexed: no finding.
5. If `relex(v)` is **not** a `str`, and that re-lexed value appears among the
   artifact's non-string leaves, and `v` itself does **not** appear among the
   artifact's string leaves — report a finding naming the field's declared
   type and the type the artifact carries.

This catches `title: "true"` → `True`, `owner: "#redacted"` → `None`, and
`name = "123"` → `123`. It must **not** fire on `templates/pyproject-uv`.

Other changes in this file:

- `effective_validators` prepends a **non-optional** `MarkdownValidator` for
  `kind: region` whenever no *non-optional* markdown validator is declared.
  A declared `optional: true` markdown validator no longer suppresses it.
- `validate_region_payload`: a leading ` ``` ` or `---` line is now an
  **error** — the page owns the fences. `{}` and `[]` are rejected as empty.
- `CommandValidator` reports stdout and stderr, both, labelled.
- Delete the duplicated `bucket = ...` line in the `MarkdownValidator` arm.

## 5. `audit.py` (§A3, §A1 lint half)

```python
class AuditReport(BaseModel):
    template: str
    language: str
    fixtures_seen: int
    fields_probed: int
    sites_linted: int
    findings: list[str]
    skipped_reason: str | None = None

    @property
    def audited(self) -> bool: ...   # skipped_reason is None
    @property
    def ok(self) -> bool: ...        # audited and not findings


def audit_template(template: Template) -> AuditReport: ...
def lint_template_source(template: Template) -> list[str]: ...
```

`audit_template` returns a report, never a bare list. `skipped_reason` is set
when nothing was audited: an unstructured language, or no `examples/`
fixtures. Lint findings and injection findings both land in `findings`.

Per-language payload sets replace the single TOML-shaped `PAYLOAD`. The
comparison covers parsed **values**, not only `_key_paths`.

### The source lint

Flag every `{{ … }}` site in a structured-language template that does not sit
inside a double-quoted span **and** whose expression is not provably a
non-string scalar.

- "Inside a double-quoted span": scan the line left to right tracking `"`
  parity. Treat `{{ … }}` and `{% … %}` as opaque units that do not change
  quote state. `"{{ a }}{% if b %}x{% endif %}"` is one quoted span, so both
  sites are quoted.
- Resolve the expression's leading dotted path against
  `template.get_schema_json()`, following `$ref`, `$defs` and `anyOf`. Bind
  `{% for VAR in EXPR %}` variables to `EXPR`'s item schema.
- `type` in `{integer, number, boolean}` → no finding. Anything else, or an
  unresolvable expression → finding.

`templates/pyproject-uv/template.j2` must lint clean. Its one unquoted site is
`line-length = {{ ruff.line_length }}`, and `ruff.line_length` is an integer.

## 6. `template.py` / `catalog.py` (§B2, §C5, §C6, §B9, §B10)

- `load_schema_module` routes through `resolve_path`.
- `load_example` validates the exemplar against the schema before returning
  it; metadata may name the exemplar. Add an optional field for that name on
  `TemplateMetadata`? **No** — do not touch `models.py`. Use
  `examples/<template-name>.input.json` first, then alphabetical, and say so
  in the docstring.
- `sys.modules[spec_name]` is assigned after a successful `exec_module`.
- `TemplateCatalog` grows `load_errors: list[tuple[str, str]]` —
  `(template_dir_name, message)` — populated by `load_from_paths`.
- Delete `TemplateCatalog.templates_by_language` and `Template.output_kind`.

## 7. `generator.py` / `pipeline.py` (§A4, §A8, §A9, §B6, §C7, §B10)

Wave 3a owns the async core. Wave 3b's `api.py` only wraps it.

```python
# generator.py
async def generate_model_async(
    template: Template,
    user_request: str,
    context: dict[str, Any] | None = None,
    model_name: str = DEFAULT_MODEL,
    prior_failure: str | None = None,
) -> tuple[BaseModel, dict[str, int] | None]: ...   # (model, usage)

def build_context(
    user_request: str,
    context: dict[str, Any],
    prior_failure: str | None = None,
) -> str: ...

# pipeline.py
async def generate_async(
    catalog: TemplateCatalog, request: GenerationRequest
) -> GenerationResult: ...

def generate(
    catalog: TemplateCatalog, request: GenerationRequest
) -> GenerationResult: ...          # asyncio.run(generate_async(...))
```

- The async path is the only implementation. `generate` is a thin wrapper.
- `_attempt` becomes `_attempt_async` and threads `prior_failure`.
- Attempt N+1 receives attempt N's `error_detail` through `prior_failure`.
  `build_context` appends it so attempt 2's prompt differs from attempt 1's.
- One `except Exception` at the pipeline boundary. Nothing escapes.
- `check_round_trip` runs in step 4, and its findings are fatal
  (`OUTPUT_VALIDATION_FAILED`).
- `fail()` carries `warnings`.
- The result carries `kind`, `region` and `usage`.
- Backoff before a retry of `LLM_FAILED` only. Export the delay as a
  module-level constant so a test can patch it to zero and never sleep:

  ```python
  RETRY_BACKOFF_SECONDS: float = 1.0   # doubled per attempt; patch to 0 in tests
  ```

  Use `await asyncio.sleep(RETRY_BACKOFF_SECONDS * 2 ** (attempt - 1))`.
  Retries of the other two retryable reasons do **not** sleep — the repair
  loop changes the prompt, so there is nothing to wait for.

**Test patch sites move.** Existing tests patch
`templateer.pipeline.generate_model`. The new name is
`templateer.pipeline.generate_model_async` and stubs must be `async def`
returning `(model, usage)`. Wave 0 owns that update.

## 8. `api.py` (§A8, §B8)

```python
async def generate_async(self, ...) -> GenerationResult: ...
def generate(self, ...) -> GenerationResult: ...      # asyncio.run wrapper
def render_from_model(self, ...) -> str: ...          # model_validate, not **
def validate_artifact(
    self, name, artifact, model_data: dict[str, Any] | None = None
) -> tuple[list[str], list[str]]:
    """(errors, warnings)"""
def audit(self, name) -> AuditReport: ...
```

`validate_artifact` returning a tuple is a **breaking change**. Wave 0 updates
its callers in `tests/`.

When the caller supplies `model_data`, `validate_artifact` also runs
`check_round_trip` and folds its findings into `errors`. This is what makes
`probes/p_audit_blind.sh` report the type mismatch.

## 9. `cli.py` (§A7, §B9, §B10)

`--json` on `generate`, `validate`, `render`, `check`, `describe`, `list`.
Output is a single JSON object on stdout. Under `--json`, no colour, no ticks,
no prose on stdout.

Exit codes. The standing law fixes the classes: `0` ok, `1` finding/decision,
`2` infra/config, `3` usage. The review wants "no such template" separable
from "the LLM failed" from "the artifact is invalid". Both hold with:

| `FailureReason` | Exit | Class |
|---|---|---|
| — (success) | 0 | ok |
| `MODEL_VALIDATION_FAILED` | 1 | finding |
| `RENDER_FAILED` | 1 | finding |
| `OUTPUT_VALIDATION_FAILED` | 1 | finding |
| `CONFIG_ERROR` | 2 | infra/config |
| `LLM_FAILED` | 2 | infra/config |
| `NO_TEMPLATE` | 3 | usage |

The exact reason is always in `--json` output as `failure_reason`. Export the
map as `templateer.cli.EXIT_CODES: dict[FailureReason, int]` so a test can
assert it.

Three CLI failures carry no `FailureReason`, because they happen before the
pipeline runs. Pin them at **3 (usage)** — the caller named a file that the
CLI cannot use:

| Case | Exit |
|---|---|
| `--input` file missing or unparseable | 3 |
| `--context` file missing or unparseable | 3 |
| unknown template name (`_get_template_or_exit`) | 3 |

The last one keeps `NO_TEMPLATE` consistent across every command.

`render` and `validate` both hold the validated model, so both run
`check_round_trip` and fail on its findings.

The `--json` payload shapes. `generate` emits `GenerationResult.model_dump()`
verbatim and `check` emits the `AuditReport`. The other four:

```jsonc
// list
{"templates": [{"name": …, "description": …, "language": …, "kind": …,
                "path": …, "trigger_paths": [ … ]}],   // sorted list, never a set
 "load_errors": [{"template": …, "error": …}]}
// describe
{"name": …, "description": …, "language": …, "kind": …, "path": …,
 "trigger_paths": [ … ],                                // sorted list
 "region": {"page": …, "ref": …, "anchor": …} | null}
// render
{"template": …, "artifact": …, "output_path": … | null, "written": bool,
 "errors": [ … ], "warnings": [ … ]}
// validate
{"template": …, "model_valid": bool, "rendered": bool,
 "errors": [ … ], "warnings": [ … ]}
```

Other CLI work: `describe` prints a sorted list, not a `set` repr. `check`
prints the `AuditReport` honestly and exits non-zero when nothing was audited.
`list` surfaces `catalog.load_errors`; `--strict` makes them fatal (exit 2).
Delete the unreachable `except TemplateLoadError` arm.

## 10. `__init__.py` (§C2)

Export `TemplateRegistry`, `GenerationRequest`, `GenerationResult`,
`FailureReason`. `__version__` comes from
`importlib.metadata.version("templateer")`, with a fallback for a tree that is
not installed.

---

## Where the review is wrong

Wave 0 measured these at `f3b9193`. The review was verified empirically, but
three of its claims do not survive an exhaustive check. Fix the finding, not
the sentence.

1. **§A3(c) overstates the audit's blindness.** It says the TOML-shaped
   `PAYLOAD` lets "a vulnerable YAML **or JSON** template audit clean".
   Measured: a vulnerable JSON template and a vulnerable Python template are
   **both flagged today** — the payload breaks their grammars too. Only YAML
   audits clean. Per-language payload sets and value comparison are still
   needed; the hole is one language wide, not two.

2. **§A5's headline reproduction stops reproducing after Wave 1.** The review
   sets `language: text` on a `kind: region` template. §1 above pins
   `RegionOutput.language` to `yaml`, so that repro is unreachable, and with a
   legal language the built-in YAML parser already rejects the review's
   corrupting payload. The `optional: true` bypass is still real — but the
   cases that isolate it are a **bare scalar** and `{}` / `[]`: valid YAML that
   only the region check can reject. Agent V, treat those as the gate.

3. **§C2's `__version__` half does not reproduce.** `templateer.__version__`
   already equals `importlib.metadata.version("templateer")`. The duplication
   is real; the mismatch is not. Single-source it to stop future drift.

## Standing rules for every agent

1. Run the toolchain through the checked-in venv: `.venv/bin/python -m pytest`,
   `.venv/bin/python -m ruff check`, `.venv/bin/ty check src/`.
2. Touch only the files your wave owns. If you need another wave's file, stop
   and report it.
3. Write in Simplified Technical English: short sentences, active voice, one
   idea per sentence, no filler.
4. Do not write an absolute you cannot test.
