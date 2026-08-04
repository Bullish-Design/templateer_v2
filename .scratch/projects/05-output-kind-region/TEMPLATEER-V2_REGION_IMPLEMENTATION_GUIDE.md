# Templateer V2 — Region Output Kind: Implementation Guide

**Input:** `.scratch/projects/05-output-kind-region/TEMPLATEER-V2_OUTPUT_KIND_REGION.md` (the kickoff + design doc)
**Goal:** make "this template writes a bounded region of a page" a *declared, enforceable* property, so the argentic.space consumer can splice payloads without violating P5 (don't write what you don't own).
**Outcome:** `OutputSpec.kind: Literal["full_file", "region"]` + `RegionBoundary`, a markdown/YAML payload validator, non-optional enforcement in the pipeline, and a back-compatible default of `full_file`.

> **Status:** written *before* implementation. Facts about the current code are **[verified]** (reproduced against this checkout, 223 passed / 9 skipped). Every new code block is the **design target** — confirm it as you land it. The one contract you must pin against the real argentic repo before shipping is the exact fence grammar of `replace_range` (§ Design decisions, D6).

---

## Verified facts about the 0.2.0 code

- `src/templateer/models.py:57` — `OutputSpec` is `{path, language}`. No `kind`, no region metadata. **[verified]**
- `TemplateMetadata` is `extra="forbid"`; validators are a `kind`-discriminated union (`ParseValidator | CommandValidator`) that fails at template load on malformed metadata. **[verified]**
- `pipeline.py` — `output_path = template.metadata.output.path` (line ~66); the flow is resolve → `generate_model` → `template.render(model)` → `validate_output(artifact, output.language, metadata.validators)` → `GenerationResult`. **[verified]**
- `result.py` — `GenerationResult.output_path: str | None` already exists; the shape does not change in this work. **[verified]**
- `renderer.py` — template receives only `model.model_dump(mode="json")`; finalizer handles `yaml` (strings double-quoted, `true`/`false` tokens). **No change needed here.** **[verified]**
- `validators.py` — built-in `parser_validators` dict (`toml/json/yaml/python`); loop branches on `ParseValidator` / `CommandValidator`; `ValidatorSpec = ParseValidator | CommandValidator` is a local alias. **[verified]**
- `api.py:221` `validate_artifact` and `cli.py` `render` (:261) and `validate` (:438) all pass `template.metadata.validators` straight to `validate_output`. **[verified]**
- `cli.py` `describe` prints `Generates: {output.path} ({output.language})`. **[verified]**
- `templates/` ships exactly one template (`pyproject-uv`); `tests/test_integration.py:320` audits every bundled template, so adding a bundled template changes the shipped surface. **[verified]**
- PyYAML behaviors **[verified by probing]**:
  - a *trailing* `---` (second document) makes `yaml.safe_load` raise `ComposerError` — the natural "unclosed/stray `---` fence" detector;
  - a *leading* `---` is a legal YAML document-start marker (parses fine) — so a body-only payload that begins with `---` is ambiguous with a fence opener;
  - duplicate keys are **silently accepted** (`{'a': 2}` — last wins) — needs an explicit check;
  - `yaml.safe_dump` re-parses stably for plain data.

---

## Design decisions (with the ones the proposal asked you to justify)

### D1 — The artifact is the fence *body*, not the fenced block
The page owns the fences; the consumer swaps only the `CodeText` span (the body) via `replace_range`. So a region template's artifact is **bare YAML**. The validator *tolerates* a fenced artifact (for testing the gate), but the contract is body-only: a payload that begins with a fence line must end with a matching fence line, and a stray fence line inside the body is an error.

Consequence: `output.language: yaml` works with the existing renderer finalizer and the built-in YAML parse check.

### D2 — Region payload validation is non-optional
The boundedness property is the whole point of the feature; a template author forgetting to declare the validator would silently re-open the hole it exists to close. Enforcement therefore lives in the pipeline, not in metadata discipline: `effective_validators()` auto-prepends the markdown check for `kind: "region"`. The explicit `kind: "markdown"` validator is also added to the discriminated union so `full_file` templates can opt in.

### D3 — Validator decision: offline (LocalIndex-equivalent) is the default
The proposal's §2.2 says the validator "runs the artifact through the argentic index (LocalIndex offline / RuntimeIndex live)". argentic is a *separate repo* — not a dependency of templateer. The offline checks (fence balance, one YAML document, structured payload, round-trip stability, duplicate keys) are implemented directly in `validators.py`; **the live RuntimeIndex check belongs to argentic at splice time**, where the page and its index actually exist. Templateer gains nothing from a network/index dependency at render time and would otherwise be untestable offline — which the gate (offline-first) requires.

### D4 — `GenerationResult.output_path` for a region = the page path
Per proposal §3: for `kind: "region"`, `output_path = region.page`. Failure reporting stays grounded in a real path. `OutputSpec.path` remains required and is informational for regions (convention: set it to the page path too).

### D5 — No bundled region template
Back-compat gate (proposal §4.5) means "every existing template untouched" — adding a bundled template changes the shipped surface and makes the injection audit (which iterates `templates/`) a moving target. Tests build a region template as a `tmp_path` fixture instead. **argentic Phase 6 owns the first real consumer.**

### D6 — The `---` ambiguity is resolved by contract; pin it against argentic
`---` is simultaneously a YAML document-start marker and (per the concept) the data-block fence. This guide treats a leading fence line as an opener that *requires* a closer; body-only payloads must not include doc-start markers. **Before shipping, pin the exact fence grammar of argentic's `replace_range` against this implementation and adjust `_extract_fenced_body` if it differs** (e.g. `~~~` fences, fence attributes). This is a named report-back item in the proposal §6.

### D7 — Payload quality rules
- Must be **one** YAML document (multi-doc → `ComposerError` → error).
- Must be a **mapping or list** — a structured data block. Bare scalars and `null`/empty payloads are rejected (a generated empty payload is a bug).
- Must **round-trip**: `safe_load` then `safe_dump` then `safe_load` must yield an equal document.
- **Duplicate keys are errors** — PyYAML silently keeps the last (verified); a swapped payload must not corrupt meaning silently.

---

## Target module layout (deltas only)

```
src/templateer/
  models.py       +RegionBoundary, OutputSpec.kind/region + model_validator, +MarkdownValidator, union widened
  validators.py   +validate_region_payload, +_extract_fenced_body, +_find_duplicate_keys,
                  +effective_validators, markdown branch in the loop, ValidatorSpec widened
  pipeline.py     output_path from region.page; effective_validators; nothing else
  api.py          validate_artifact uses effective_validators
  cli.py          describe prints region line; render/validate use effective_validators
  template.py     +output_kind property (the 04-refactor "re-add when partial rendering arrives" loop closes)
tests/
  test_region.py  NEW — the whole gate, offline
```

No changes to `renderer.py`, `generator.py`, `result.py`, `catalog.py`, `audit.py`. Run `pytest` after every phase.

---

# Phase 0 — Guardrails (red first)

**`tests/test_region.py`** (new). These stay red until their phase lands, then stay green forever.

```python
"""The region output kind: declared boundary + enforceable payload check.

Covers the proposal's gate (§4) entirely offline: a fixture region template
built in tmp_path, a stubbed generate_model, and direct validator probes.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from pydantic import ValidationError

from templateer.catalog import TemplateCatalog
from templateer.models import MarkdownValidator, OutputSpec, TemplateMetadata
from templateer.pipeline import generate
from templateer.result import FailureReason, GenerationRequest
from templateer.validators import (
    effective_validators,
    validate_output,
    validate_region_payload,
)


# ---------------------------------------------------------------------------
# Fixture: a self-contained region template (no bundled-template changes)
# ---------------------------------------------------------------------------

REGION_METADATA = {
    "name": "region-demo",
    "description": "Generate the payload for a fenced YAML data block.",
    "output": {
        "path": "docs/status.md",       # informational for regions
        "language": "yaml",
        "kind": "region",
        "region": {"page": "docs/status.md", "ref": "$block-status"},
    },
    "schema": {"module": "schema", "class": "RegionModel"},
    "prompt": {"file": "prompt.md"},
    "renderer": {"engine": "minijinja", "file": "template.j2"},
}

REGION_SCHEMA = '''\
from pydantic import BaseModel, Field


class RegionModel(BaseModel):
    status: str = Field(description="Block status, e.g. 'ok'")
    items: list[str] = Field(min_length=1, description="Payload items")
'''

REGION_TEMPLATE = '''\
status: "{{ status }}"
items:
{% for item in items %}
  - "{{ item }}"
{% endfor %}
'''

REGION_EXAMPLE = {"status": "ok", "items": ["alpha", "beta"]}


@pytest.fixture
def region_template(tmp_path: Path) -> Path:
    """Build a minimal kind:region template on disk."""
    root = tmp_path / "region-demo"
    (root / "examples").mkdir(parents=True)
    (root / "metadata.yml").write_text(
        yaml.safe_dump(REGION_METADATA), encoding="utf-8")
    (root / "schema.py").write_text(REGION_SCHEMA, encoding="utf-8")
    (root / "prompt.md").write_text("Fill the schema.", encoding="utf-8")
    (root / "template.j2").write_text(REGION_TEMPLATE, encoding="utf-8")
    (root / "examples" / "ok.input.json").write_text(
        json.dumps(REGION_EXAMPLE), encoding="utf-8")
    return tmp_path


@pytest.fixture
def region_catalog(region_template: Path) -> TemplateCatalog:
    c = TemplateCatalog()
    c.load_from_paths([region_template])
    return c


def _stub_generate_model(template, **kwargs):
    data = json.loads(
        (template.root / "examples" / "ok.input.json").read_text(encoding="utf-8"))
    return template.get_schema_class().model_validate(data)


# ---------------------------------------------------------------------------
# Gate 1+5: a kind:region template loads, renders, and round-trips
# ---------------------------------------------------------------------------

def test_region_template_metadata_loads(region_template: Path) -> None:
    meta = TemplateMetadata.model_validate(REGION_METADATA)
    assert meta.output.kind == "region"
    assert meta.output.region is not None
    assert meta.output.region.page == "docs/status.md"
    assert meta.output.region.ref == "$block-status"
    assert meta.output.region.anchor is None


def test_region_artifact_is_body_only_and_valid_yaml(
    region_catalog: TemplateCatalog,
) -> None:
    """Gate 1: payload splices as a clean fenced block — body-only YAML."""
    with patch("templateer.pipeline.generate_model", side_effect=_stub_generate_model):
        result = generate(region_catalog, GenerationRequest(
            template_name="region-demo", user_request="make it", max_attempts=1))
    assert result.succeeded, result.error_detail
    assert result.failure_reason is None
    assert result.output_path == "docs/status.md"       # the page path
    parsed = yaml.safe_load(result.artifact)            # body-only YAML
    assert parsed == REGION_EXAMPLE
    assert validate_region_payload(result.artifact) == []


# ---------------------------------------------------------------------------
# Gate 2: kind/region coupling fails at load
# ---------------------------------------------------------------------------

def test_region_kind_requires_region() -> None:
    data = dict(REGION_METADATA)
    data["output"] = {**data["output"], "region": None}   # kind: region, no region
    with pytest.raises(ValidationError):
        TemplateMetadata.model_validate(data)


def test_full_file_kind_rejects_region() -> None:
    data = dict(REGION_METADATA)
    data["output"] = {**data["output"], "kind": "full_file"}
    with pytest.raises(ValidationError):
        TemplateMetadata.model_validate(data)


def test_region_boundary_rejects_extra_fields() -> None:
    data = dict(REGION_METADATA)
    data["output"]["region"] = {
        "page": "x", "ref": "y", "typo": 1,
    }
    with pytest.raises(ValidationError):
        TemplateMetadata.model_validate(data)


# ---------------------------------------------------------------------------
# Gate 4: the markdown validator rejects broken blocks
# ---------------------------------------------------------------------------

class TestValidateRegionPayload:
    def test_accepts_clean_body(self) -> None:
        assert validate_region_payload("status: ok\nitems:\n  - a\n") == []

    def test_accepts_clean_fenced_block(self) -> None:
        # Tolerated for convenience; the contract is body-only (D1).
        assert validate_region_payload("---\nstatus: ok\n---\n") == []
        assert validate_region_payload("```yaml\nstatus: ok\n```\n") == []

    def test_rejects_unclosed_fence(self) -> None:
        errors = validate_region_payload("```yaml\nstatus: ok\n")
        assert errors and "fence" in errors[0].lower()

    def test_rejects_stray_interior_fence(self) -> None:
        errors = validate_region_payload("```yaml\nstatus: ok\n```\nmore: x\n```\n")
        assert errors and "fence" in errors[0].lower()

    def test_rejects_non_yaml_body(self) -> None:
        errors = validate_region_payload("status: [unclosed\n")
        assert errors and "yaml" in errors[0].lower()

    def test_rejects_multi_document_payload(self) -> None:
        # A trailing --- is a second document: ComposerError (verified).
        errors = validate_region_payload("status: ok\n---\nother: 1\n")
        assert errors

    def test_rejects_scalar_payload(self) -> None:
        errors = validate_region_payload("just a string\n")
        assert errors and "mapping" in errors[0].lower()

    def test_rejects_empty_payload(self) -> None:
        assert validate_region_payload("") != []
        assert validate_region_payload("# only a comment\n") != []

    def test_rejects_duplicate_keys(self) -> None:
        # PyYAML silently keeps the last (verified); we must not.
        errors = validate_region_payload("status: a\nstatus: b\n")
        assert errors and "duplicate" in errors[0].lower()

    def test_round_trip_is_stable(self) -> None:
        assert validate_region_payload("a: 1\nb:\n  - x\n  - y\n") == []


def test_markdown_validator_kind_is_in_the_union() -> None:
    """Explicit kind: 'markdown' is declarable, for full_file authors."""
    data = dict(REGION_METADATA)
    data["output"] = {**data["output"], "kind": "full_file", "region": None}
    data["validators"] = [{"kind": "markdown"}]
    meta = TemplateMetadata.model_validate(data)
    assert isinstance(meta.validators[0], MarkdownValidator)


# ---------------------------------------------------------------------------
# Gate 4 via the pipeline: a broken payload fails the generation
# ---------------------------------------------------------------------------

def test_region_pipeline_fails_on_broken_payload(
    region_catalog: TemplateCatalog,
) -> None:
    """The payload check is non-optional: even a 'clean' declared validator
    list cannot let a broken block through."""
    with patch("templateer.pipeline.generate_model", side_effect=_stub_generate_model):
        with patch("templateer.template.Template.render",
                   return_value="```yaml\nstatus: nope\n"):
            result = generate(region_catalog, GenerationRequest(
                template_name="region-demo", user_request="x", max_attempts=1))
    assert not result.succeeded
    assert result.failure_reason is FailureReason.OUTPUT_VALIDATION_FAILED
    assert result.artifact is None


def test_effective_validators_prepends_markdown_for_region(
    region_catalog: TemplateCatalog,
) -> None:
    template = region_catalog.get("region-demo")
    declared = template.metadata.validators
    effective = effective_validators(template.metadata.output, declared)
    assert len(effective) == len(declared) + 1
    assert isinstance(effective[0], MarkdownValidator)


# ---------------------------------------------------------------------------
# Gate 5: back-compat — full_file unchanged
# ---------------------------------------------------------------------------

def test_kind_defaults_to_full_file() -> None:
    spec = OutputSpec(path="pyproject.toml", language="toml")
    assert spec.kind == "full_file"
    assert spec.region is None
```

**Phase 0 exit:** every test above exists and is **red** for the right reason (fails because the feature is absent, not because of a typo).

---

# Phase 1 — `models.py`: the declared boundary

Add to `src/templateer/models.py` (below `CommandValidator`, above the union):

```python
class MarkdownValidator(BaseModel):
    """Validates the artifact as a fenced YAML region payload."""

    model_config = {"extra": "forbid"}

    kind: Literal["markdown"]
    optional: bool = False
```

Replace `OutputSpec`:

```python
class RegionBoundary(BaseModel):
    """The page region a ``kind: "region"`` template may be spliced into.

    The consumer owns the fences and the surrounding page; this declares the
    bounded slot: which page, which ``$ref``'d block's payload the template
    replaces, and which annotation it resolves.
    """

    model_config = {"extra": "forbid"}

    page: str = Field(description="Hosting page name (or page-name pattern)")
    ref: str = Field(description="The data block's $ref — the payload this region owns")
    anchor: str | None = Field(
        default=None,
        description="Annotation ref recorded in the block's addressed: list",
    )


class OutputSpec(BaseModel):
    """What artifact this template generates."""

    path: str = Field(
        description=(
            "Target file path; for kind=region this is informational — "
            "region.page is the real anchor"
        )
    )
    language: str = Field(description="Target language: toml, yaml, json, python, ...")
    kind: Literal["full_file", "region"] = "full_file"
    region: RegionBoundary | None = Field(
        default=None,
        description="Required iff kind=region; forbidden for full_file",
    )

    @model_validator(mode="after")
    def _kind_region_consistency(self) -> "OutputSpec":
        if self.kind == "region" and self.region is None:
            raise ValueError("kind='region' requires a region boundary")
        if self.kind == "full_file" and self.region is not None:
            raise ValueError("kind='full_file' must not carry a region boundary")
        return self
```

Widen the union:

```python
OutputValidator = Annotated[
    ParseValidator | CommandValidator | MarkdownValidator,
    Field(discriminator="kind"),
]
```

Notes:
- `model_validator` runs at template load because `TemplateMetadata` (extra=forbid) validates into this type — the "fail at load, not at render" discipline, same as the validator union.
- `kind` defaults to `full_file` and `region` defaults to `None`, so every existing `metadata.yml` and every existing test dict still parses unchanged (Gate 5).
- `RegionBoundary.page` is documented as "name or pattern"; exact pattern-matching semantics are argentic-side and deferred (D6 / report-back).

**Phase 1 exit:** the model guardrails in Phase 0 are green; the full suite stays green (223 passed).

---

# Phase 2 — `validators.py`: the payload check

Add (imports: `yaml` already present; add `MarkdownValidator, OutputSpec` to the `templateer.models` import):

```python
def effective_validators(
    output: OutputSpec, declared: list[OutputValidator]
) -> list[OutputValidator]:
    """The validators that actually run for an output.

    A region template's payload check is the safety property the kind exists
    to declare (D2): it is not an authoring choice, so it is prepended no
    matter what the template declares.  An explicit markdown validator is
    not duplicated.
    """
    if output.kind != "region":
        return declared
    if any(isinstance(v, MarkdownValidator) for v in declared):
        return declared
    return [MarkdownValidator(kind="markdown"), *declared]
```

```python
_FENCES = ("```", "---")


def validate_region_payload(artifact: str) -> list[str]:
    """Validate *artifact* as the payload of a fenced YAML region block.

    Returns a list of errors; empty means the payload is clean.

    The consumer swaps the fence *body* (the block's CodeText span) and owns
    the fences, so the artifact is bare YAML by contract (D1).  A fenced
    artifact is tolerated for gate testing: a leading fence line must be
    matched by a trailing fence line, and the body must not contain a fence
    line.  A payload that begins with ``---`` is therefore treated as a
    fence opener, never as a YAML document-start marker.
    """
    body, fence_errors = _extract_fenced_body(artifact)
    if fence_errors:
        return fence_errors

    # One YAML document ------------------------------------------------
    try:
        parsed = yaml.safe_load(body)
    except yaml.YAMLError as e:
        return [f"region payload is not valid YAML: {e}"]
    if not isinstance(parsed, (dict, list)):
        kind = type(parsed).__name__
        return [
            f"region payload must be a YAML mapping or list (a data block), "
            f"got {kind}"
        ]

    # Round-trip stability ---------------------------------------------
    try:
        re_parsed = yaml.safe_load(yaml.safe_dump(parsed))
    except yaml.YAMLError as e:
        return [f"region payload does not round-trip through YAML: {e}"]
    if re_parsed != parsed:
        return ["region payload does not round-trip: parse/dump changes the document"]

    # Duplicate keys ----------------------------------------------------
    return _find_duplicate_keys(body)


def _extract_fenced_body(artifact: str) -> tuple[str, list[str]]:
    """Return ``(body, errors)`` for a possibly-fenced artifact."""
    lines = artifact.splitlines()
    if not lines:
        return "", ["region payload is empty"]

    first = lines[0].strip()
    opener: str | None = None
    if first.startswith("```"):
        opener = "```"  # trailing language tag allowed: ```yaml
    elif first == "---" or first.startswith("--- "):
        opener = "---"

    if opener is None:
        return artifact, []  # bare body — the normal case

    if len(lines) < 2:
        return "", [f"unclosed '{opener}' fence: opener without a closer"]
    if lines[-1].strip() != opener:
        return "", [
            f"unclosed '{opener}' fence: expected closing '{opener}', "
            f"got {lines[-1].strip()!r}"
        ]

    body_lines = lines[1:-1]
    errors: list[str] = []
    for i, line in enumerate(body_lines, start=2):
        if line.strip().startswith(opener):
            errors.append(f"stray fence '{opener}' at line {i}: the body must not "
                          f"contain a fence line")
    return "\n".join(body_lines), errors


def _find_duplicate_keys(text: str) -> list[str]:
    """Report duplicate mapping keys.

    PyYAML's ``safe_load`` silently keeps the last duplicate (verified); a
    swapped payload must not corrupt meaning silently, so duplicates are
    errors.  ``yaml.compose`` builds the node tree without constructing
    values, so this never executes anything.
    """
    try:
        node = yaml.compose(text)
    except yaml.YAMLError:
        return []  # already reported by the parse check
    if node is None:
        return []

    found: list[str] = []

    def walk(n: yaml.Node) -> None:
        if isinstance(n, yaml.MappingNode):
            seen: set[str] = set()
            for key_node, _ in n.value:
                if isinstance(key_node, yaml.ScalarNode):
                    key = key_node.value
                    if key in seen:
                        found.append(f"duplicate key {key!r} in region payload")
                    seen.add(key)
            for _, value_node in n.value:
                walk(value_node)
        elif isinstance(n, yaml.SequenceNode):
            for item in n.value:
                walk(item)

    walk(node)
    return found
```

Wire the markdown branch into the validator loop in `validate_output`:

```python
            elif isinstance(validator, MarkdownValidator):
                bucket = warnings if validator.optional else errors
                bucket += validate_region_payload(artifact)
```

And widen the local alias:

```python
ValidatorSpec = ParseValidator | CommandValidator | MarkdownValidator
```

Ordering note: for a `kind: region` template, `effective_validators` puts the markdown check first, so a broken block fails before any declared command validator runs — and the built-in `language="yaml"` parse runs first of all, on the bare body, which is exactly right.

**Phase 2 exit:** the `TestValidateRegionPayload` guardrails are green; `test_effective_validators_prepends_markdown_for_region` green; suite green.

---

# Phase 3 — `pipeline.py`: enforce the boundary

Two surgical changes in `_attempt`:

```python
    # 1 — Resolve ---------------------------------------------------------
    ...
    output = template.metadata.output
    region = output.region
    # A region template's failures are grounded in the page it lives on (D4).
    output_path = region.page if output.kind == "region" and region is not None \
        else output.path
```

```python
    # 4 — Validate the artifact -------------------------------------------
    errors, warnings = validate_output(
        artifact,
        output.language,
        effective_validators(output, template.metadata.validators),
    )
```

That is the whole phase. The renderer invariant, the retry loop, `GenerationResult` shape, and the failure taxonomy are untouched. **`OUTPUT_VALIDATION_FAILED` is already in `RETRYABLE`** — a bad payload is a nondeterministic failure, so a retry genuinely may help; that is correct and needs no change.

**Phase 3 exit:** the pipeline guardrails (`test_region_artifact_is_body_only_and_valid_yaml`, `test_region_pipeline_fails_on_broken_payload`) are green; suite green.

---

# Phase 4 — the other two surfaces + one property

`validate_artifact` in `api.py` and the `render`/`validate` commands in `cli.py` pass `template.metadata.validators` straight through — they would silently skip the non-optional region check. Same one-line fix everywhere:

```python
from templateer.validators import effective_validators
...
errors, warnings = validate_output(
    artifact, template.output_language,
    effective_validators(template.metadata.output, template.metadata.validators),
)
```

In `cli.py` `describe_template`, after the `Generates:` line:

```python
    output = template.metadata.output
    if output.kind == "region" and output.region is not None:
        anchor = output.region.anchor or "-"
        click.echo(
            f"  Region: page={output.region.page} "
            f"ref={output.region.ref} anchor={anchor}"
        )
```

In `template.py`, one property — the `kind` concept is real now (the 04-refactor guide said "re-add the literal in one line when partial rendering actually arrives"):

```python
    @property
    def output_kind(self) -> str:
        """What kind of artifact this template produces."""
        return self.metadata.output.kind
```

Do **not** re-add `catalog.templates_by_output_kind` — nothing consumes it (YAGNI; the catalog is flat and the region set is filtered fine by `templates_by_language("yaml")`).

**Phase 4 exit:** suite green; `templateer describe` on a region template prints the region line; `templateer validate`/`render` reject a broken region payload.

---

# Phase 5 — docs

`README.md`:

- **OutputSpec / template authoring**: document `kind: "full_file" | "region"` (default `full_file`), the `region:` block (`page`, `ref`, `anchor`), and the body-only contract: *a region template's artifact is the bare YAML payload; the page owns the fences.*
- **The markdown validator**: `kind: "markdown"` checks fence balance, single-document YAML, structured payload, round-trip stability, and duplicate keys. For `kind: region` templates it is enforced automatically and cannot be omitted.
- **Safety semantics**: templateer declares and validates the boundary; it never writes regions (or files) — the consumer's `replace_range` owns the bytes. Cross-reference argentic.space §5.3/§5.4.

---

## Execution checklist

| # | Phase | Builds | Exit condition |
|---|---|---|---|
| 0 | Guardrails | — | `tests/test_region.py` exists and is **red** |
| 1 | `models.py` | `RegionBoundary`, `OutputSpec.kind/region`, `MarkdownValidator`, union | Model guardrails green; suite green (back-compat) |
| 2 | `validators.py` | `validate_region_payload`, `_extract_fenced_body`, `_find_duplicate_keys`, `effective_validators`, markdown branch | Validator guardrails green; suite green |
| 3 | `pipeline.py` | `output_path = region.page`, `effective_validators` | Pipeline guardrails green; suite green |
| 4 | api/cli/template | `effective_validators` on all surfaces, describe region line, `output_kind` | Suite green; describe/validate/render verified |
| 5 | README | Region authoring + safety semantics | Docs match code |

**Order matters:** 0 → 1 → 2 → 3 → 4 are sequential (each phase's guardrails need the previous phase). Phase 5 last, against the final shape.

---

## Report back (proposal §6, answered from this guide)

1. **Files touched** — `models.py`, `validators.py`, `pipeline.py`, `api.py`, `cli.py`, `template.py`, `README.md`, `tests/test_region.py`. (Adjust if the working tree drifted.)
2. **Final `OutputSpec` shape** — as pinned in Phase 1: `{path, language, kind="full_file", region=None}` + `RegionBoundary{page, ref, anchor=None}`, extra=forbid, coupling enforced by `model_validator`.
3. **The five gate checks** — each maps to a Phase 0 test class; report pass/fail per check.
4. **Validator decision** — offline (LocalIndex-equivalent) is the default (D3): static checks in `validators.py`; RuntimeIndex live-checking is argentic's at splice time. Why: no new dependency, offline-first gate, and the page/index only exist on the consumer side.
5. **Drift vs this doc** — D6 is the big one: confirm `---` vs ``` fence grammar against the *real* argentic `replace_range` before shipping; also confirm `region.page` "name vs pattern" semantics on the argentic side. Everything else in the proposal held against the 0.2.0 code.
6. **Trunk state** — push only when the suite (223 pre-existing + new) is green.
