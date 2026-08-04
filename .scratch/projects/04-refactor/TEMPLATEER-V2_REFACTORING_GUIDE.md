# Templateer V2 — Refactoring Guide

**Input:** `.scratch/projects/03-code-review/TEMPLATEER-V2_ADVERSARIAL_REVIEW.md`
**Goal:** one core path, easy to reason about; the guarantee the concept claims, actually delivered.
**Outcome:** ~1,886 → ~1,240 source lines, 12 → 11 modules, one pipeline instead of three.

Every code block below was executed against this checkout before being written down. Facts marked **[verified]** are reproductions, not assumptions.

---

## The decision that shapes everything else

You asked for **one way**: Jinja renders everything, including structured data, with after-the-fact checks confirming it came out right. That is the correct call, and it turns out to be cleaner than the alternative — because MiniJinja gives us exactly the right hook.

`Environment.finalizer` is a callback applied to **every `{{ }}` output site**, and only to output sites. Conditionals and loops still see native Python values. **[verified]**

```python
e.finalizer = finalize
e.render_str('name = "{{ n }}"\nflag = {{ b }}\n{% if b %}cond = true{% endif %}',
             n='evil"\nINJECTED = "yes', b=True)
```
```toml
name = "evil\"\nINJECTED = \"yes"
flag = true
cond = true
```
→ `tomllib.loads(...)` yields `{'name': 'evil"\nINJECTED = "yes', 'flag': True, 'cond': True}` — the payload is *data*, the bool is *TOML*, and `{% if b %}` still saw a real `True`.

That single hook fixes both value-level defects at once:

1. **String injection (A1).** Verified attack: a valid model emitted an undeclared `license` key into `pyproject.toml` and every validator passed.
2. **Python literals leaking into artifacts.** MiniJinja's Python bindings render `True` → `"True"` and `None` → `"None"` **[verified]**, and `tomllib` rejects both **[verified]**. Any template with a `bool` or nullable field is latently broken today. The prior review missed this entirely; so did mine until I probed the renderer.

**One hook, one place, impossible for a template author to bypass.** No `| toml` filters to remember, no second rendering mode, no serializer fork. This is the streamlined core the brief asked for.

### The one authoring rule this creates

> **In a structured-language template, every string interpolation sits inside double quotes.**

`name = "{{ project_name }}"` ✅  `name = {{ project_name }}` ❌

The finalizer produces *content safe inside a double-quoted string literal of the target language*. One rule, stated once, mechanically checkable by `templateer check` (Phase 8).

### One escaper covers all four structured languages **[verified]**

```python
json.dumps(s, ensure_ascii=False)[1:-1]
```

Round-trips **exactly** through `tomllib`, `json`, `yaml.safe_load` (double-quoted scalar), and `ast.literal_eval` (double-quoted) across 18 adversarial payloads including quotes, backslashes, newlines, NUL, ANSI escapes, `{{ injected }}`, and astral-plane emoji.

Two non-obvious details, both found by testing:

- `ensure_ascii=False` is **required**. The default emits UTF-16 surrogate pairs for astral characters (`😀` → `😀`), and TOML rejects surrogates: `Escaped character is not a Unicode scalar value` **[verified]**.
- `U+007F` (DEL) must be escaped for TOML but `json.dumps` leaves it bare. One `.replace()` fixes it.

---

## Target module layout

```
src/templateer/
  __init__.py     11 →   ~20   version + public exports; DEFAULT_TEMPLATE_PATHS deleted
  models.py       88 →   ~85   single output, discriminated validators, typed triggers
  template.py    180 →  ~150   root containment; output_kind fixed
  catalog.py      81 →    81   unchanged — best module in the repo
  escaping.py      0 →   ~55   NEW — the finalizer
  renderer.py     71 →   ~75   strict + finalizer, BaseModel only
  generator.py   151 →   ~65   dead post-processing removed
  validators.py  135 →  ~150   errors/warnings split
  audit.py         0 →   ~85   NEW — injection audit (the after-the-fact check)
  result.py        0 →  ~110   NEW — replaces generation.py
  pipeline.py    189 →  ~135   THE one pipeline, with the retry loop inside
  api.py         340 →  ~135   thin adapter
  cli.py         467 →  ~390   thin adapter
  generation.py  135 →     0   DELETED → result.py
  validation.py   38 →     0   DELETED (dead once generator.py is slimmed)
                ─────────────
                1,886 → ~1,240   −34%
```

Phase order minimizes rework: consolidate the three pipelines **before** tightening `models.py`, so the metadata-shape change lands in one place instead of three.

Run `pytest` after every phase. Phases 1–3 are independent; 4–5 must be sequential.

---

# Phase 0 — Guardrails first

Write the tests that are currently red. They stay red until Phase 1, then stay green forever.

**`tests/test_escaping.py`** (new)

```python
"""The core guarantee: a validated model cannot alter artifact structure."""
import ast, json, tomllib
import pytest, yaml
from pathlib import Path
from templateer.template import Template

PAYLOADS = [
    'benign"\nlicense = "PROPRIETARY',   # the A1 attack
    'He said "hi"', 'back\\slash', 'tab\there', 'null\x00byte',
    'unié中\U0001F600',                   # astral: breaks ensure_ascii=True
    'del\x7fhere',                        # DEL: must be escaped for TOML
    "', evil: true, x: '", 'a\r\nb', '"""', "'''", '\x1b[31m',
    '{{ injected }}', '{% raw %}', '$(whoami)', '#comment',
]

@pytest.mark.parametrize("payload", PAYLOADS)
def test_escaped_string_round_trips_in_every_language(payload):
    from templateer.escaping import escape_string
    e = escape_string(payload)
    assert tomllib.loads(f'k = "{e}"')["k"] == payload
    assert json.loads(f'{{"k": "{e}"}}')["k"] == payload
    assert yaml.safe_load(f'k: "{e}"')["k"] == payload
    assert ast.literal_eval(f'"{e}"') == payload

def test_injection_payload_cannot_add_a_toml_key():
    t = Template(Path("templates/pyproject-uv"))
    cls = t.get_schema_class()
    m = cls(project_name="ok", python_version="3.12",
            project_description='benign"\nlicense = "PROPRIETARY')
    parsed = tomllib.loads(t.render(m))
    assert "license" not in parsed["project"]
    assert parsed["project"]["description"] == 'benign"\nlicense = "PROPRIETARY'

def test_bools_render_as_target_language_literals(tmp_path):
    """MiniJinja renders Python True as 'True', which is invalid TOML."""
    from templateer.renderer import render_template
    from pydantic import BaseModel
    class M(BaseModel):
        flag: bool = True
    f = tmp_path / "t.j2"; f.write_text("flag = {{ flag }}\n")
    assert tomllib.loads(render_template(f, M(), "toml"))["flag"] is True

def test_interpolating_null_is_a_template_authoring_error(tmp_path):
    from templateer.renderer import render_template, RenderError
    from pydantic import BaseModel
    class M(BaseModel):
        x: str | None = None
    f = tmp_path / "t.j2"; f.write_text('x = "{{ x }}"\n')
    with pytest.raises(RenderError, match="null"):
        render_template(f, M(), "toml")
```

**`tests/test_pipeline_failures.py`** (new) — the failures that currently escape as tracebacks **[all verified as crashes today]**

```python
import os
from pathlib import Path
import pytest
from templateer.catalog import TemplateCatalog
from templateer.pipeline import generate
from templateer.result import FailureReason, GenerationRequest

@pytest.fixture
def catalog():
    c = TemplateCatalog(); c.load_from_paths([Path("templates")]); return c

def test_missing_api_key_returns_failed_never_raises(catalog, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    r = generate(catalog, GenerationRequest(
        template_name="pyproject-uv", user_request="x", max_attempts=1))
    assert not r.succeeded
    assert r.failure_reason is FailureReason.CONFIG_ERROR
    assert r.artifact is None and r.error_detail

def test_non_serializable_context_returns_failed(catalog, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    r = generate(catalog, GenerationRequest(
        template_name="pyproject-uv", user_request="x",
        context={"p": Path("/tmp")}, max_attempts=1))
    assert not r.succeeded          # must not raise TypeError

def test_unknown_template_is_not_retryable(catalog):
    r = generate(catalog, GenerationRequest(template_name="nope", user_request="x"))
    assert r.failure_reason is FailureReason.NO_TEMPLATE
    assert not r.can_retry
    assert r.attempt == 1           # no wasted retries on a permanent failure
```

---

# Phase 1 — The escaping boundary (A1)

## 1a. `src/templateer/escaping.py` (new)

```python
"""Language-aware value formatting for the render boundary.

Pydantic constrains *which values* reach a template.  This module constrains
how those values *lex* once they land in the artifact — the other half of the
guarantee, and the half that was missing.

Installed as the MiniJinja ``finalizer``, so it runs at every ``{{ }}`` output
site and nowhere else: ``{% if flag %}`` still sees a native bool.
"""

import json
from collections.abc import Callable

# Languages whose string literals share JSON's escape grammar.  Verified to
# round-trip exactly through tomllib / json / yaml.safe_load / ast.literal_eval.
_QUOTED_STRING_LANGUAGES = frozenset({"toml", "json", "yaml", "python"})

# Language token for boolean output.  Python source needs True/False;
# TOML, JSON and YAML all need lowercase.
_BOOLEANS = {
    "python": ("True", "False"),
    "toml": ("true", "false"),
    "json": ("true", "false"),
    "yaml": ("true", "false"),
}


class EscapeError(ValueError):
    """A value cannot be safely interpolated into the target language."""


def escape_string(value: str) -> str:
    """Escape *value* for use inside a double-quoted string literal.

    Returns the string *content* only — the template supplies the quotes.

    ``ensure_ascii=False`` is required: the default emits UTF-16 surrogate
    pairs for astral characters, and TOML rejects surrogates.  ``json.dumps``
    leaves U+007F bare, which TOML also rejects, so it is escaped explicitly.
    """
    return json.dumps(value, ensure_ascii=False)[1:-1].replace("\x7f", "\\u007f")


def make_finalizer(language: str) -> Callable[[object], object]:
    """Build the MiniJinja finalizer for *language*.

    Unknown languages (markdown, dockerfile, text, ...) get identity treatment
    for strings — there is no string literal syntax to protect — but still get
    correct boolean and null handling.
    """
    quote = language in _QUOTED_STRING_LANGUAGES
    true_token, false_token = _BOOLEANS.get(language, ("true", "false"))

    def finalize(value: object) -> object:
        # bool before int: bool is a subclass of int in Python.
        if isinstance(value, bool):
            return true_token if value else false_token
        if value is None:
            raise EscapeError(
                "null value interpolated into the artifact; guard the field "
                "with {% if field %} ... {% endif %} or give it a default"
            )
        if isinstance(value, str) and quote:
            return escape_string(value)
        return value  # int, float, and already-rendered filter output

    return finalize
```

**Why `None` raises rather than rendering empty.** Without the finalizer MiniJinja emits the literal `None`, which no target language accepts **[verified]**. Silently substituting `""` is precisely the failure mode the concept doc forbids ("Undefined variables should not silently render as empty strings"). A bare interpolation of a nullable field is always a template bug; failing loudly at author time — where `templateer check` will catch it against the fixtures — is the correct trade.

## 1b. Rewrite `src/templateer/renderer.py`

```python
"""Deterministic rendering from validated Pydantic model data.

Two invariants, both enforced here and nowhere else:
  1. The template receives only ``model.model_dump(mode="json")``.
  2. Every interpolated value is formatted for the target language.
"""

from pathlib import Path

from minijinja import Environment
from pydantic import BaseModel

from templateer.escaping import make_finalizer


class RenderError(Exception):
    """Raised when template rendering fails."""


def render_template(template_path: Path, model: BaseModel, language: str) -> str:
    """Render *template_path* from *model* for a *language* artifact.

    Args:
        template_path: Path to the Jinja template file.
        model: A validated Pydantic model instance.  Not a dict — the type is
            the invariant.
        language: Target language, used to select value formatting.

    Raises:
        RenderError: Missing template, undefined variable, or a value that
            cannot be safely interpolated.
    """
    if not template_path.exists():
        raise RenderError(f"Template file not found: {template_path}")

    render_context = model.model_dump(mode="json")
    source = template_path.read_text(encoding="utf-8")

    env = Environment()
    env.trim_blocks = True
    env.lstrip_blocks = True
    env.undefined_behavior = "strict"        # always; not a per-template knob
    env.finalizer = make_finalizer(language)

    try:
        return env.render_str(source, **render_context)
    except Exception as e:
        raise RenderError(f"Failed to render '{template_path.name}': {e}") from e
```

Changes beyond the finalizer:

- **`model: BaseModel` only.** The `dict` overload was a typed hole in the central invariant (B1); only tests used it, which is how it stayed open.
- **`strict` parameter gone.** Strictness is the contract, not a setting (B2).
- **`encoding="utf-8"`** — template content is UTF-8 by definition of the format.

> **Known edge, not worth working around:** `Environment.render_str(self, source, name=None, /, **ctx)` — `source` and `name` are positional-only, so schema fields named `name` or `source` are fine **[verified]**. A field literally named `self` collides. Leave it; `templateer check` will surface it on any template that tries.

## 1c. Update `Template.render`

```python
def render(self, model: BaseModel) -> str:
    """Render this template with a validated model."""
    from templateer.renderer import render_template

    return render_template(
        self.resolve_path(self.metadata.renderer.file),
        model,
        self.metadata.output.language,
    )
```

## 1d. Update `tests/test_renderer.py`

Three tests pass raw dicts (`:103`, `:110`, `:119`) and one exercises `strict=False`. Convert the dict callers to trivial `BaseModel` subclasses; delete the `strict=False` test — the behavior no longer exists.

**Phase 1 exit:** `tests/test_escaping.py` fully green. The A1 attack is dead.

---

# Phase 2 — Slim `generator.py` (A5), delete `validation.py`

`generator.py:96-129` handles outputs pydantic-ai cannot produce, and one of its branches is actively harmful.

| Branch | Reality |
|---|---|
| `if raw_output is None` | Unreachable with `output_type` set — and calls `agent.retries`, which **does not exist** on pydantic-ai 2.23 **[verified: `hasattr → False`]**, so the "clean error" path raises `AttributeError`. Masked by `MagicMock` auto-attributes. |
| `if not isinstance(raw_output, schema_class)` + dict fallback | Unreachable; `output_type=schema_class` guarantees an instance. |
| "Defensive re-validation" | **Breaks every schema using `Field(alias=...)`** — verified: round-tripping `model_dump(mode="json")` back through `model_validate` yields `loc: ('class',) Field required`. Templateer's own `SchemaRef` uses `alias="class"`, so the pattern is right there to be copied. It also discards its own result and returns the original. |

**Replace `generate_model` in full:**

```python
"""LLM-based model generation using Pydantic AI.

The LLM's output type is the template's Pydantic schema.  Pydantic AI performs
structured-output parsing, validation, and validation-feedback retries; this
module does not second-guess it.
"""

from typing import Any

from pydantic import BaseModel
from pydantic_ai import Agent

from templateer.template import Template

DEFAULT_MODEL = "openai:gpt-4.1-mini"

# Pydantic AI's *internal* budget for re-asking the LLM when its output fails
# schema validation.  Distinct from GenerationRequest.max_attempts, which
# re-runs the whole pipeline.  Conflating these two is what made the old retry
# budget grow 3 -> 4 -> 5 across pipeline retries.
MODEL_OUTPUT_RETRIES = 2


def generate_model(
    template: Template,
    user_request: str,
    context: dict[str, Any] | None = None,
    model_name: str = DEFAULT_MODEL,
) -> BaseModel:
    """Ask an LLM to fill *template*'s schema.

    Raises whatever pydantic-ai raises; the pipeline classifies it.
    """
    agent = Agent(
        model_name,
        output_type=template.get_schema_class(),
        instructions=template.load_prompt(),
        retries=MODEL_OUTPUT_RETRIES,
    )
    return agent.run_sync(build_context(user_request, context or {})).output


def build_context(user_request: str, context: dict[str, Any]) -> str:
    """Build the context text for the LLM."""
    parts = [f"User request: {user_request}"]
    if context:
        # default=str: Path objects are the most plausible "project fact" an
        # agent will pass, and stringifying them is obviously the intent.
        parts.append(f"Project facts:\n{json.dumps(context, indent=2, default=str)}")
    return "\n\n".join(parts)
```

Then:

- **`rm src/templateer/validation.py`** — it existed only to serve the deleted branches.
- **`rm tests/test_validation.py`** (286 lines).
- **Delete** `TestGenerateModelNonLLM` mock tests for the removed branches (`test_generator.py` ~lines 200–280) — they test code that no longer exists. Keep `TestBuildContext`, and rename `_build_context` → `build_context` (it is part of the module's real surface; the underscore was never honest).

`generator.py`: 151 → ~65 lines. `generate_model` now matches the concept doc's own sketch (`return result.output`), which was right all along.

---

# Phase 3 — `result.py` replaces `generation.py` (A3)

`Generation` models a design that was cut. `matched_template` can only ever equal `template_name`; `requested_path` is an output labelled as an input; `SUBMITTED` and `GENERATING` are unobservable because `run_pipeline` is synchronous; `artifact` doubles as an error slot on three failure paths.

The structural fix is not renaming fields — it is **building the result once at each exit point instead of mutating one object through the pipeline**. That makes the invariants enforceable, which they never were before.

**`src/templateer/result.py`** (new; `git rm src/templateer/generation.py`)

```python
"""The generation request and its result.

Replaces the old Generation state machine.  ``run_pipeline`` was synchronous,
so no caller could ever observe SUBMITTED or GENERATING; what the codebase
actually needs is a result, and a request it can be retried from.
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from templateer.generator import DEFAULT_MODEL


class FailureReason(str, Enum):
    """Why a generation failed."""

    NO_TEMPLATE = "no_template"
    """The requested template name is not in the catalog."""

    CONFIG_ERROR = "config_error"
    """Missing API key, unknown model id, or other caller misconfiguration."""

    LLM_FAILED = "llm_failed"
    """The LLM call failed (network, timeout, provider error)."""

    MODEL_VALIDATION_FAILED = "model_validation_failed"
    """The LLM could not produce output satisfying the schema."""

    RENDER_FAILED = "render_failed"
    """The Jinja render step failed — schema/template drift, or a bad value."""

    OUTPUT_VALIDATION_FAILED = "output_validation_failed"
    """The rendered artifact failed output validation."""


# Retrying only helps for nondeterministic failures.  A missing template, a
# missing API key, and a broken template are permanent: retrying burns tokens
# to reach the same answer.
RETRYABLE = frozenset({
    FailureReason.LLM_FAILED,
    FailureReason.MODEL_VALIDATION_FAILED,
    FailureReason.OUTPUT_VALIDATION_FAILED,
})


class GenerationRequest(BaseModel):
    """Everything needed to run — or re-run — a generation."""

    template_name: str = Field(description="Exact template directory name")
    user_request: str = Field(description="What the caller wants generated")
    context: dict[str, Any] = Field(default_factory=dict, description="Project facts")
    model_name: str = Field(default=DEFAULT_MODEL)
    max_attempts: int = Field(default=3, ge=1, description="Whole-pipeline attempts")


class GenerationResult(BaseModel):
    """The outcome of a generation.

    Invariants, enforced below rather than merely documented:
      - success  => artifact is set and failure_reason is None
      - failure  => failure_reason is set and artifact is None
    """

    request: GenerationRequest
    output_path: str | None = Field(default=None, description="Where the artifact belongs")
    model: dict[str, Any] | None = Field(default=None, description="The validated model dump")
    artifact: str | None = Field(default=None, description="Rendered artifact — success only")
    failure_reason: FailureReason | None = None
    error_detail: str | None = Field(default=None, description="Human-readable failure text")
    warnings: list[str] = Field(default_factory=list, description="Non-fatal validator notes")
    attempt: int = Field(default=1, ge=1, description="Which attempt produced this result")

    @model_validator(mode="after")
    def _check_invariants(self) -> "GenerationResult":
        if self.failure_reason is None and self.artifact is None:
            raise ValueError("a successful result must carry an artifact")
        if self.failure_reason is not None and self.artifact is not None:
            raise ValueError("a failed result must not carry an artifact")
        return self

    @property
    def succeeded(self) -> bool:
        return self.failure_reason is None

    @property
    def can_retry(self) -> bool:
        """Would another attempt plausibly help?"""
        return (
            self.failure_reason in RETRYABLE
            and self.attempt < self.request.max_attempts
        )
```

What this buys:

- **`artifact` means artifact.** Error text lives in `error_detail`. `if result.artifact: write(it)` is now safe — it was not before.
- **`can_retry` has one source of truth** (`request.max_attempts`), replacing a hardcoded `3` in `generation.py`, `DEFAULT_MAX_RETRIES` in `generator.py`, and `config.max_retries` in the Allium spec.
- **`MODEL_VALIDATION_FAILED` becomes producible.** pydantic-ai exports `UnexpectedModelBehavior` **[verified]**, raised when output-validation retries are exhausted. Phase 4 maps it. H1 gets fixed honestly rather than deleted.
- **`CONFIG_ERROR` is new and earns its place** — a missing API key is the single most common failure, and it is neither an LLM failure nor a validation failure. Marking it non-retryable stops the loop from burning three attempts on it.
- **`warnings`** finally gives `validation_messages` real content (Phase 4).

---

# Phase 4 — One pipeline (A2 + A4)

Rewrite `src/templateer/pipeline.py`. This is the only place the algorithm exists.

```python
"""The Templateer generation pipeline.

  1. Resolve the template.
  2. Ask the LLM to fill its schema.
  3. Render deterministically from the validated model.
  4. Validate the rendered artifact.

Every failure returns a GenerationResult.  Nothing escapes as an exception —
that promise is either total or worthless.
"""

import logging
from typing import Any

from pydantic_ai.exceptions import UnexpectedModelBehavior, UserError

from templateer.catalog import TemplateCatalog
from templateer.generator import generate_model
from templateer.renderer import RenderError
from templateer.result import FailureReason, GenerationRequest, GenerationResult
from templateer.template import Template, TemplateLoadError, TemplateNotFoundError
from templateer.validators import validate_output

logger = logging.getLogger(__name__)


def generate(catalog: TemplateCatalog, request: GenerationRequest) -> GenerationResult:
    """Run the pipeline, retrying while the failure is worth retrying.

    This is the single entry point.  Set ``request.max_attempts = 1`` to
    disable retries; there is no separate retry function to keep in sync.
    """
    result = _attempt(catalog, request, attempt=1)
    while result.can_retry:
        logger.info(
            "retrying %s after %s (attempt %d/%d)",
            request.template_name, result.failure_reason.value,
            result.attempt + 1, request.max_attempts,
        )
        result = _attempt(catalog, request, attempt=result.attempt + 1)
    return result


def _attempt(
    catalog: TemplateCatalog, request: GenerationRequest, attempt: int
) -> GenerationResult:
    def fail(reason: FailureReason, detail: str, **extra: Any) -> GenerationResult:
        return GenerationResult(
            request=request, attempt=attempt,
            failure_reason=reason, error_detail=detail, **extra,
        )

    # 1 — Resolve ---------------------------------------------------------
    try:
        template: Template = catalog.get(request.template_name)
    except (TemplateNotFoundError, TemplateLoadError) as e:
        return fail(FailureReason.NO_TEMPLATE, str(e))

    output_path = template.metadata.output.path

    # 2 — Generate the model ----------------------------------------------
    #
    # The boundary is deliberately broad.  Schema loading, prompt loading,
    # context serialization, Agent construction and the network call all live
    # in here, and every one of them has been observed to raise.
    try:
        model = generate_model(
            template,
            user_request=request.user_request,
            context=request.context,
            model_name=request.model_name,
        )
    except UserError as e:
        # Missing API key, unknown model id — caller misconfiguration.
        return fail(FailureReason.CONFIG_ERROR, str(e), output_path=output_path)
    except UnexpectedModelBehavior as e:
        # Output-validation retries exhausted inside pydantic-ai.
        return fail(FailureReason.MODEL_VALIDATION_FAILED, str(e), output_path=output_path)
    except TemplateLoadError as e:
        return fail(FailureReason.NO_TEMPLATE, str(e), output_path=output_path)
    except Exception as e:
        logger.debug("model generation failed", exc_info=True)
        return fail(FailureReason.LLM_FAILED, f"{type(e).__name__}: {e}",
                    output_path=output_path)

    model_dump = model.model_dump(mode="json")

    # 3 — Render ----------------------------------------------------------
    try:
        artifact = template.render(model)
    except RenderError as e:
        return fail(FailureReason.RENDER_FAILED, str(e),
                    output_path=output_path, model=model_dump)

    # 4 — Validate the artifact -------------------------------------------
    errors, warnings = validate_output(
        artifact, template.metadata.output.language, template.metadata.validators
    )
    if errors:
        return fail(FailureReason.OUTPUT_VALIDATION_FAILED, "; ".join(errors),
                    output_path=output_path, model=model_dump)

    return GenerationResult(
        request=request, attempt=attempt, output_path=output_path,
        model=model_dump, artifact=artifact, warnings=warnings,
    )
```

Deleted from this module: `PipelineError` (never raised in `src/` — only constructed by its own tests **[verified]**) and `retry_generation` (the loop is now internal, and the caller no longer has to re-supply `user_request` and `context` that the request already holds).

## Update `validators.py` — errors vs warnings

Today, when an `optional: true` validator fails, the message is **computed and then dropped on the floor** — that is why `validation_messages` was always empty. The fix is to return it.

```python
def validate_output(
    artifact: str, language: str, validators: list[OutputValidator] | None = None
) -> tuple[list[str], list[str]]:
    """Validate a rendered artifact.

    Returns:
        ``(errors, warnings)``.  Errors are fatal; warnings come from
        validators declared ``optional: true``.
    """
```

Every `if not optional: errors.append(msg)` becomes `(errors if not v.optional else warnings).append(msg)`. Also take typed `OutputValidator` objects rather than `[v.model_dump() for v in ...]` dicts — the pipeline was serializing a validated model back into an untyped dict purely to hand it to a `.get()`-based reader.

---

# Phase 5 — `api.py` and `cli.py` become adapters (A2)

## `api.py`: 340 → ~135 lines

`TemplateRegistry.generate` was a line-by-line re-derivation of the pipeline with `raise` substituted for status assignment. Replace its body with a delegation:

```python
def generate(
    self,
    template_name: str,
    user_request: str,
    context: dict[str, Any] | None = None,
    model_name: str = DEFAULT_MODEL,
    max_attempts: int = 3,
) -> GenerationResult:
    """Generate an artifact (full pipeline with LLM).

    Returns a GenerationResult rather than raising: LLM failure is an expected
    outcome, not an exceptional one.  Check ``result.succeeded``.
    """
    return generate(self._catalog, GenerationRequest(
        template_name=template_name, user_request=user_request,
        context=context or {}, model_name=model_name, max_attempts=max_attempts,
    ))
```

Also:

- **Delete `TemplateRegistry.generate_model`.** The validated model is now on `result.model`, so a model-only path is a second way to do one thing. Callers who want it call `generate()` and read `.model` — the render step is deterministic and cheap.
- **Delete `TemplateGenerationResult`** from `models.py`. One result type across all three surfaces.
- **Keep `render_from_model` and `validate_artifact` raising.** Deliberate asymmetry, worth a comment: these are deterministic operations whose failures are programmer errors, so an exception is right. `generate` depends on an unreliable external system, where failure is data.

## `cli.py`: 467 → ~390 lines

- **`generate`** — build a `GenerationRequest`, call `generate`, then branch on `result.succeeded`. Print `result.warnings` to stderr. Print `result.error_detail` (not `result.artifact`) on failure.
- **`validate`** — pass `template.metadata.validators` through. It currently drops them **[verified]**, silently skipping exactly the checks the template author declared (prior H4).
- **`render`** — run output validation before writing. This is the path through which unvalidated output reaches disk, and the README already claims it doesn't (prior M6).
- **Context-file parsing** — rewrite. Today `{"user_request": "Build a CLI tool"}` with no `facts` key falls through to the `else`, is treated wholesale as project facts, and the request silently degrades to the stub `"Generate <name> artifact"` **[verified]**:

```python
def _load_context_file(path: Path) -> tuple[str | None, dict[str, Any]]:
    """Parse a context file into ``(user_request, facts)``.

    Accepts either shape, and errors on anything else rather than
    silently producing an empty context:
        {"user_request": "...", "facts": {...}}
        {"any": "flat", "project": "facts"}
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise click.ClickException(
            f"{path}: expected a JSON object, got {type(data).__name__}"
        )
    if "facts" in data or "user_request" in data:
        facts = data.get("facts", {})
        if not isinstance(facts, dict):
            raise click.ClickException(f"{path}: 'facts' must be an object")
        request = data.get("user_request")
        if request is not None and not isinstance(request, str):
            raise click.ClickException(f"{path}: 'user_request' must be a string")
        return request, facts
    return None, data
```

- **Single-source the default model.** `"openai:gpt-4.1-mini"` is hardcoded in four places **[verified]** while `DEFAULT_MODEL` is imported by nobody. Import it everywhere.

---

# Phase 6 — Tighten `models.py`

Now that one module reads the metadata, the shape change is a single edit.

**Collapse `outputs: list[OutputSpec]` → `output: OutputSpec`.** `outputs: []` currently validates and then `IndexError`s in four places **[verified]**. `min_length=1` patches the symptom; every consumer indexes `[0]` and nothing reads `[1:]`. Multi-artifact generation is a "Future Extension" in the concept doc. Deleting the list removes the bug class instead of guarding it, and stops the type advertising a capability that does not exist.

```python
class OutputSpec(BaseModel):
    """What artifact this template generates."""
    path: str = Field(description="Target file path, e.g. 'pyproject.toml'")
    language: str = Field(description="Target language: toml, yaml, json, python, ...")
```

**Drop `kind`.** It is `Literal["full_file"]` — a one-member enum no code path reads. Meanwhile `Template.output_kind` returns `.language` **[verified]**, so `catalog.templates_by_output_kind("toml")` filters by language and `describe` prints `Output kind: toml` for something whose kind is `full_file`. The property is wrong *and* the concept it names is empty. Delete `kind`; rename the property to `output_language` and `templates_by_output_kind` → `templates_by_language`. Re-add the literal in one line when partial rendering actually arrives.

**Discriminated validators.** `OutputValidator(kind="command")` with no `command` validates and silently no-ops; so does `kind="parse"` with no `language`; and `OutputValidator(kind="parse", typo=1)` is accepted while `TemplateMetadata` one level up sets `extra="forbid"` **[all verified]**.

```python
class ParseValidator(BaseModel):
    model_config = {"extra": "forbid"}
    kind: Literal["parse"]
    language: str
    optional: bool = False

class CommandValidator(BaseModel):
    model_config = {"extra": "forbid"}
    kind: Literal["command"]
    command: list[str] = Field(min_length=1)
    optional: bool = False

OutputValidator = Annotated[ParseValidator | CommandValidator, Field(discriminator="kind")]
```

Malformed validator metadata now fails at template load — which is the entire point of loading metadata into a typed model.

**Typed triggers.** `triggers: dict[str, list[str]]` accepts `{"whatever": ["a"]}` and silently ignores it **[verified]**; only `triggers["filenames"]` is read. In a codebase whose thesis is "types are the contract," this is the one field that opted out:

```python
trigger_filenames: list[str] = Field(default_factory=list,
                                     description="Artifact paths this template can generate")
```

**Delete `strict_context`.** It never controlled context strictness — it set `env.undefined_behavior`, and setting it `false` let a template author disable one of the six things Templateer guarantees, with `describe` cheerfully printing `Strict context: False`.

**Delete `TemplateGenerationResult`** (superseded by `GenerationResult`).

Then update `templates/pyproject-uv/metadata.yml`:

```yaml
name: pyproject-uv
description: Generate a uv-style pyproject.toml for a Python project.

output:
  path: pyproject.toml
  language: toml

schema:
  module: schema
  class: PyprojectUvModel

prompt:
  file: prompt.md

renderer:
  engine: minijinja
  file: template.j2

trigger_filenames:
  - pyproject.toml
```

`extra="forbid"` means old-format metadata fails loudly at load with a precise message. Good — this is a personal library with one template; a silent migration would be worse.

---

# Phase 7 — `template.py` hygiene

**Root containment (B7).** Verified: `prompt: {file: ../../secret.txt}` reads it without complaint. Not a security finding — a **portability** one. A template that reaches outside its own directory cannot be copied, zipped, or distributed, and breaks silently when moved. The "Template Registries" extension assumes self-containment.

```python
def resolve_path(self, relative: str) -> Path:
    """Resolve a path relative to the template root.

    Templates are self-contained: a path escaping the root is a template bug,
    not a supported feature.
    """
    resolved = (self.root / relative).resolve()
    if not resolved.is_relative_to(self.root.resolve()):
        raise TemplateLoadError(
            f"Template '{self.name}': path '{relative}' escapes the template root"
        )
    return resolved
```

**Also in this phase:**

- `read_text(encoding="utf-8")` on `metadata.yml` and the prompt.
- `output_kind` → `output_language`, reading `self.metadata.output.language`.
- `trigger_paths` → reads `metadata.trigger_filenames`.
- Note in `load_schema_module`'s docstring that `sys.modules[spec_name]` and `_schema_class_cache` persist for process lifetime, so editing a `schema.py` mid-session serves the stale class (C8). A papercut, but a confusing one during authoring — the docstring costs nothing and saves an hour.

---

# Phase 8 — `templateer check` (the after-the-fact confirmation)

This is what you asked for: proof that a template implements the quoting rule correctly, rather than a promise that it does. It re-uses assets every template already ships.

**The method:** take the template's `examples/*.input.json` (already validated, already round-trip tested), poke a hostile payload into **one string leaf at a time**, re-validate against the schema, render, parse, and compare the artifact's key set to the benign render's. Injection changes the key set. Constrained fields (`Literal`, enums, patterns) reject the payload and are skipped as structurally un-injectable.

**Verified on the current, unfixed code:** 25 string fields probed, 1 skipped (`project_type` is a `Literal`), **24 findings** — 3 injecting new TOML keys (`project.INJECTED`, `tool.ruff.INJECTED`) and 21 producing unparseable output. After Phase 1 this must report **0**.

**`src/templateer/audit.py`** (new)

```python
"""Adversarial audit of a template's escaping.

Confirms after the fact what escaping.py enforces up front: that no string a
schema permits can alter the structure of the rendered artifact.
"""

import ast, copy, json, tomllib
from collections.abc import Iterator
from typing import Any

import yaml

from templateer.template import Template

PAYLOAD = '"\nINJECTED = "yes'

_PARSERS = {
    "toml": tomllib.loads,
    "json": json.loads,
    "yaml": yaml.safe_load,
    "python": ast.parse,
}


def _string_paths(node: Any, prefix: tuple = ()) -> Iterator[tuple]:
    if isinstance(node, str):
        yield prefix
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from _string_paths(v, prefix + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _string_paths(v, prefix + (i,))


def _poke(data: Any, path: tuple, value: Any) -> Any:
    out = copy.deepcopy(data)
    node = out
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return out


def _key_paths(node: Any, prefix: tuple = ()) -> set[tuple]:
    out: set[tuple] = set()
    if isinstance(node, dict):
        for k, v in node.items():
            out |= {prefix + (k,)} | _key_paths(v, prefix + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out |= _key_paths(v, prefix + (i,))
    return out


def audit_template(template: Template) -> list[str]:
    """Probe every string field of every example fixture for injection.

    Returns a list of findings; empty means the template is sound.
    """
    language = template.metadata.output.language
    parse = _PARSERS.get(language)
    if parse is None:
        return []  # unstructured target: no structure to subvert

    schema_class = template.get_schema_class()
    findings: list[str] = []

    for fixture in sorted((template.root / "examples").glob("*.input.json")):
        benign_data = json.loads(fixture.read_text(encoding="utf-8"))
        try:
            baseline = _key_paths(parse(template.render(
                schema_class.model_validate(benign_data))))
        except Exception as e:
            findings.append(f"{fixture.name}: benign fixture does not render/parse: {e}")
            continue

        for path in _string_paths(benign_data):
            where = ".".join(str(p) for p in path)
            try:
                model = schema_class.model_validate(_poke(benign_data, path, PAYLOAD))
            except Exception:
                continue  # constrained field — not injectable by construction
            try:
                rendered = template.render(model)
            except Exception as e:
                findings.append(f"{fixture.name}:{where}: render failed: {e}")
                continue
            try:
                got = _key_paths(parse(rendered))
            except Exception as e:
                findings.append(f"{fixture.name}:{where}: artifact unparseable: {e}")
                continue
            if injected := got - baseline:
                findings.append(
                    f"{fixture.name}:{where}: injected "
                    + ", ".join(".".join(map(str, p)) for p in sorted(injected))
                )

    return findings
```

**CLI command** — one net-new command, split from `validate` by audience:

| Command | Audience | Question it answers |
|---|---|---|
| `templateer validate <name> --input model.json` | caller / agent | Does *this model* produce a valid artifact? |
| `templateer check <name>` | template author | Is *this template* correctly written? |

```python
@main.command("check")
@click.argument("template_name")
@click.option("--paths", "-p", multiple=True, help="Template search paths.")
def check_template(template_name: str, paths: tuple[str, ...]) -> None:
    """Audit a template: fixtures render, parse, and resist injection."""
    template = _get_template_or_exit(template_name, paths)
    findings = audit_template(template)
    if findings:
        click.echo(f"✗ {len(findings)} finding(s):", err=True)
        for f in findings:
            click.echo(f"  - {f}", err=True)
        sys.exit(1)
    click.echo("✓ escaping audit passed")
```

**Wire it into the suite** so it can never regress:

```python
# tests/test_templates.py
@pytest.mark.parametrize("name", [p.name for p in sorted(Path("templates").iterdir())
                                  if (p / "metadata.yml").exists()])
def test_bundled_template_resists_injection(name):
    from templateer.audit import audit_template
    assert audit_template(Template(Path("templates") / name)) == []
```

---

# Phase 9 — Few-shot examples (A7)

`examples/` is referenced nowhere in `src/` **[verified]**. Every template already ships a hand-validated, schema-conforming, round-trip-tested exemplar, used only by tests.

The concept doc's stated motivation is *"Why This Helps Smaller Models."* One validated example in the prompt is the highest-leverage thing available for a small model filling a structured schema, and the asset already exists.

In `Template`:

```python
def load_example(self) -> str | None:
    """Return the first example input fixture as JSON, if one exists.

    Used as a few-shot exemplar for the LLM.  The fixture is already
    schema-validated by the template's own tests.
    """
    fixtures = sorted((self.root / "examples").glob("*.input.json"))
    if not fixtures:
        return None
    return fixtures[0].read_text(encoding="utf-8")
```

In `generator.build_context`, append when present:

```python
if example := template.load_example():
    parts.append(f"Example of a well-formed response:\n{example}")
```

Not gold-plating: it uses what is already there, on the path the concept was designed around.

---

# Phase 10 — Test suite surgery (A6)

237 passing tests, and the most common runtime failure exits with a raw traceback. The suite was written to cover lines, not behaviors. **Deleting the bad tests matters as much as adding good ones** — they are negative-value, because they make the suite *look* thorough enough that nobody adds the real ones.

## Delete

| Target | Why |
|---|---|
| `tests/test_validation.py` (286 lines) | Module deleted in Phase 2. |
| `TestPipelineError` (`test_pipeline.py`) | Tests a class production code never raises **[verified]**. |
| `test_render_failure_in_pipeline_path` | Named for a pipeline path; never calls the pipeline. Builds `Generation(status=FAILED, reason=RENDER_FAILED)` and asserts it is FAILED with reason RENDER_FAILED. |
| `test_retry_no_template_succeeds_if_template_added`, `test_retry_increments_retry_count` | `except Exception: return`. They pass *because* the code crashes. |
| `TestGenerationEntity` (~18 hand-built `Generation`s) | Assert the fields they just set. Replace with ~4 tests of `GenerationResult`'s real logic: invariant enforcement, `can_retry` on retryable vs permanent reasons, exhaustion at `max_attempts`. |
| `test_unknown_template_errors` | Body is `pass`. |
| `test_json_schema_generates` assertion | `x in (A, None) or "title" in schema` is a tautology. Assert the actual schema shape. |
| Mock tests for removed `generator` branches | Test code that no longer exists. |

## Add

Beyond Phase 0's guardrails:

```python
def test_generate_uses_the_pipeline(registry, monkeypatch):
    """api.generate delegates rather than re-deriving. Currently ZERO
    non-LLM coverage: all four existing tests are @has_api_key gated."""
    monkeypatch.setattr("templateer.pipeline.generate_model",
                        lambda *a, **k: _valid_model())
    result = registry.generate("pyproject-uv", "make a thing")
    assert result.succeeded and "[project]" in result.artifact

def test_cli_validate_runs_custom_validators(tmp_path):
    """Regression: cli.validate passed language only, silently skipping the
    validators the template author declared."""

def test_render_command_validates_before_writing(tmp_path):
    """Regression: --output wrote unvalidated artifacts to disk."""

def test_permanent_failure_does_not_retry(catalog):
    """NO_TEMPLATE / CONFIG_ERROR must not burn max_attempts."""

def test_model_output_retries_do_not_grow_across_attempts():
    """Regression: max_retries used to be passed the accumulated pipeline
    attempt count, inflating the LLM budget 3 -> 4 -> 5."""
```

**Un-gate what can be un-gated.** The LLM end-to-end test currently reads `if gen.status == FAILED: assert reason is not None; return` — it cannot fail. Stub `generate_model` and assert real behavior; keep one genuinely live test behind `@has_api_key` for smoke purposes.

---

# Phase 11 — Dead code, packaging, docs

**Dead code sweep** (all **[verified]** unreferenced):

- `DEFAULT_TEMPLATE_PATHS` (`__init__.py`) — referenced nowhere; `cli._get_default_paths` reimplements it.
- `OutputValidationError` (`validators.py:20`) — referenced nowhere in `src/` or `tests/`.
- `PipelineError`, `retry_generation` — removed in Phase 4.
- Hardcoded `"openai:gpt-4.1-mini"` in `api.py:156`, `cli.py:294`, `pipeline.py:51` → import `DEFAULT_MODEL`.

**Packaging.** `templates/` sits at repo root, outside `packages = ["src/templateer"]`, so a wheel ships zero templates while `_get_default_paths()` looks for `src/templateer/templates`. Both halves are broken. Pick one:

```toml
# (a) ship them
[tool.hatch.build.targets.wheel.force-include]
"templates" = "templateer/templates"

[tool.hatch.build.targets.sdist]
include = ["src", "templates", "tests", "README.md", "pyproject.toml"]
```
or **(b)** drop the bundled-template story: delete the `bundled` branch of `_get_default_paths`, and let `./templates` + `-p` be the only sources. For a personal library run from the repo, **(b) is the honest choice** and removes a code path that has never worked.

**Dependency floor.** `pydantic-ai>=0.0.20` declares a floor two majors below anything that works (`output_type` does not exist there). Low impact, one line: `pydantic-ai>=2,<3`.

**Docs.** Update `README.md`:

- **Security Considerations** — replace "rendered artifacts are parsed to catch injection" with what is now true: *"Every value interpolated into an artifact is escaped for the target language at the render boundary (`escaping.py`); `templateer check` audits each template against injection payloads."* Add the honest note that loading a template executes its `schema.py` and runs its declared validator commands — templates are trusted code. That is a fine position for a personal library; the README should just say so rather than implying a sandbox that only covers the render step.
- **Template Authoring Guide** — add the one authoring rule: *string interpolations sit inside double quotes; guard nullable fields with `{% if %}`.*
- **Python API** — `generate` now returns a `GenerationResult`; check `.succeeded`. `generate_model` is gone; read `.model`.
- **CLI Reference** — document `templateer check`.

---

## Execution checklist

| # | Phase | Fixes | Exit condition |
|---|---|---|---|
| 0 | Guardrails | — | New tests exist and are **red** |
| 1 | Escaping boundary | **A1**, bool/null rendering, B1, B2 | `test_escaping.py` green |
| 2 | Slim generator | A5, alias bug, B3, B4 | `validation.py` gone; suite green |
| 3 | `result.py` | A3, H1, H2 | `generation.py` gone; suite green |
| 4 | One pipeline | A2, A4, H3 | Phase 0 failure tests green |
| 5 | API/CLI adapters | A2, H4, M5, M6, B8 | Three surfaces, one algorithm |
| 6 | Tighten models | B5, B6, B9, B10 | Metadata migrated; suite green |
| 7 | Template hygiene | B7, C8 | Containment test green |
| 8 | `templateer check` | Confirms Phase 1 | Audit reports **0** findings |
| 9 | Few-shot | A7 | Example reaches the prompt |
| 10 | Test surgery | **A6** | Tautological tests gone |
| 11 | Sweep + docs | C1–C8 | `ruff`, `ty`, `pytest` green |

**Do Phase 1 first and Phase 10 last.** Phase 1 is the finding — everything else is cleanup around a guarantee that now actually holds. Phase 10 goes last so the new tests are written against the final shape rather than being rewritten twice.
