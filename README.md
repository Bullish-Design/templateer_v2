# Templateer

**Typed, constrained artifact generation for AI agents.**

Instead of asking an LLM to write entire files directly, Templateer asks an LLM to instantiate a *Pydantic model*. That validated model is then passed to a deterministic Jinja renderer. The final file is generated deterministically from the template — never from raw LLM output.

```
User intent + project facts
        ↓
LLM fills Pydantic model  ←  the LLM never writes a file directly
        ↓
Pydantic validates model
        ↓
Jinja renders using only validated model values
        ↓
Generated file or artifact
```

## Why Templateer?

- **Deterministic output**: Same validated model → same rendered artifact. Always.
- **Typed constraints**: Pydantic schemas define exactly what data the LLM must produce. No raw string generation.
- **Strict rendering**: undefined variables in templates are errors, not silent empty strings.
- **Escaping at the render boundary**: every value interpolated into a structured artifact is escaped for the target language, so a validated model cannot alter the artifact's structure (see Security Considerations).
- **Agent-friendly**: CLI and Python API designed for use by both humans and AI agents.

## Installation

```bash
# Install from source
git clone <repo-url>
cd templateer
uv pip install -e ".[dev]"
```

Requires Python ≥ 3.12.

## Quick Start

```bash
# List available templates
uv run templateer list

# Describe a template
uv run templateer describe pyproject-uv

# Show the Pydantic schema (what the LLM needs to fill)
uv run templateer schema pyproject-uv

# Render from a pre-built model (no LLM needed)
uv run templateer render pyproject-uv --input templates/pyproject-uv/examples/fastapi.input.json

# Generate with an LLM (requires API key)
uv run templateer generate pyproject-uv \
  --request "Generate a pyproject.toml for a FastAPI app using uv, pytest, and ruff"
```

## CLI Reference

### `templateer list`

List all available templates.

```
templateer list [-p PATH ...]
```

| Option | Description |
|--------|-------------|
| `-p`, `--paths` | Additional template search paths (replaces defaults) |

### `templateer describe <name>`

Show a template's metadata: description, output language, trigger paths, and output path.

```
templateer describe pyproject-uv
```

### `templateer schema <name>`

Output the template's Pydantic schema as JSON. Useful for tooling and inspection.

```
templateer schema pyproject-uv
```

### `templateer render <name> --input <file>`

Render a template from a model JSON file. This is the fast, deterministic path that does not call an LLM.

```
templateer render pyproject-uv --input model.json
templateer render pyproject-uv --input model.json --output pyproject.toml
```

### `templateer generate <name> [options]`

Run the full generation pipeline with an LLM. The LLM receives the user request, project facts, and template prompt to produce a validated Pydantic model.

```
templateer generate pyproject-uv --request "Create a FastAPI project"
templateer generate pyproject-uv --request "..." --context facts.json
templateer generate pyproject-uv --request "..." --model openai:gpt-4o
```

| Option | Description |
|--------|-------------|
| `-r`, `--request` | User request description |
| `-c`, `--context` | JSON file with project facts context |
| `-o`, `--output` | Output file for generated artifact |
| `-m`, `--model` | LLM model to use (default: `openai:gpt-4.1-mini`) |
| `--max-attempts` | Whole-pipeline attempts (default 3) |
| `-p`, `--paths` | Additional template search paths |

The context file accepts either shape, and errors on anything else rather
than silently producing an empty context:

```json
{"user_request": "Build a CLI tool", "facts": {"uses_click": true}}
{"any": "flat", "project": "facts"}
```

### `templateer validate <name> --input <file>`

Validate that a model JSON file produces valid output. Runs three checks: model validation, rendering, and output validation.

```
templateer validate pyproject-uv --input model.json
```

### `templateer check <name>`

Audit a template for template authors: every example fixture must render,
parse, and resist injection. The audit pokes a hostile payload into each
string field of each fixture, re-renders, and compares the artifact's key
set against the benign render — an injected payload changes the key set.

```
templateer check pyproject-uv
```

## Python API

Templateer provides a clean, typed Python API for programmatic use:

```python
from templateer.api import TemplateRegistry

# Create a registry from template directories
registry = TemplateRegistry.from_paths(["./templates"])

# List available templates
for t in registry.list_templates():
    print(t.name, t.description)

# Render from a pre-built model (LLM-free)
rendered = registry.render_from_model(
    template_name="pyproject-uv",
    model_data={"project_name": "my-app", "python_version": "3.12"},
)
print(rendered)

# Generate with an LLM
result = registry.generate(
    template_name="pyproject-uv",
    user_request="Create a pyproject.toml for a FastAPI app using uv.",
    context={"uses_fastapi": True, "uses_pytest": True},
)
if not result.succeeded:
    print(result.error_detail)
else:
    print(result.artifact)   # the rendered artifact
    print(result.model)      # the validated model dump

# Validate an artifact
errors = registry.validate_artifact("pyproject-uv", rendered)
```

`generate` returns a [`GenerationResult`](src/templateer/result.py): on failure it
carries `failure_reason` and `error_detail` instead of raising — an LLM failure
is an expected outcome, not an exceptional one. The validated model is available
on `result.model`; there is no separate model-only entry point.

`render_from_model` and `validate_artifact` are deterministic operations whose
failures are programmer errors, so they raise (or return error lists) rather
than returning a result object.

## Template Authoring Guide

A Templateer template is a directory containing everything needed to generate one kind of artifact.

### Directory Structure

```
templates/pyproject-uv/
├── metadata.yml       # Template identity and configuration
├── schema.py          # Pydantic model the LLM must fill
├── prompt.md          # Instructions for the LLM
├── template.j2        # Jinja template (rendered with model data)
├── examples/          # Input/output fixtures for testing
│   ├── fastapi.input.json
│   └── fastapi.output.toml
└── tests/             # Template-specific tests
    └── test_pyproject_uv_template.py
```

### 1. Create `metadata.yml`

```yaml
name: my-template
description: What this template generates and when to use it.

output:
  path: output.txt
  language: toml       # toml, json, yaml, python, ...
  kind: full_file      # full_file (default) | region — see “Region outputs” below

# For kind: region templates, declare the bounded slot (required iff
# kind is region; forbidden for full_file):
#   region:
#     page: docs/status.md   # hosting page name (or page-name pattern)
#     ref: $block-status     # the data block's $ref whose payload this replaces
#     anchor: $fix-tuesday   # optional annotation ref recorded in addressed:

schema:
  module: schema         # Python module name (without .py)
  class: MyModel         # Pydantic model class name

prompt:
  file: prompt.md

renderer:
  engine: minijinja
  file: template.j2

trigger_filenames:
  - output.txt

# Optional output validators:
# validators:
#   - kind: parse
#     language: toml
#   - kind: command
#     command: ["python", "-m", "ruff", "check", "-"]
#     optional: true
#   - kind: markdown
#     # fence balance, single-document YAML, structured payload,
#     # round-trip stability, duplicate-key rejection (enforced
#     # automatically for kind: region templates)
```

`extra="forbid"` is enforced on metadata: malformed validator metadata, the
old `outputs:` list shape, or unknown keys fail loudly at template load.

### 2. Create `schema.py` (Pydantic model)

The model defines the structured data the LLM must produce. Use field descriptions, enums, and validators to guide the LLM.

```python
from typing import Literal
from pydantic import BaseModel, Field

class MyModel(BaseModel):
    name: str = Field(description="Name of the thing")
    language: Literal["python", "rust", "go"] = Field(description="Target language")
    features: list[str] = Field(default_factory=list)
```

### 3. Create `prompt.md`

Instructions that help the LLM fill the schema correctly. Write in plain English — the LLM will see the schema definition automatically.

### 4. Create `template.j2` (Jinja template)

The template receives ONLY the validated model dump. Undefined variables
are always errors — strictness is the contract, not a per-template knob.

**The one authoring rule:** in a structured-language template, every string
interpolation sits inside double quotes.

```jinja
[project]
name = "{{ name }}"      # ✅ correct
language = "{{ language }}"  # ✅ correct
features = [
{% for f in features %}
  "{{ f }}",
{% endfor %}
]
```

```jinja
name = {{ name }}        # ❌ wrong: a raw interpolation can break out of
                         #    the string literal and alter the structure
```

Values are escaped for the target language at the render boundary
(`escaping.py`), producing content that is safe inside a double-quoted string
literal of that language. Guard nullable fields with `{% if %}` — interpolating
`None` is a template authoring error, caught by `templateer check`:

```jinja
{% if project_description %}
description = "{{ project_description }}"
{% endif %}
```

### 5. Add examples and tests

Create `examples/scenario.input.json` and `examples/scenario.output.txt`, then verify:

```bash
uv run templateer validate my-template --input examples/scenario.input.json
```

### Region outputs (`kind: region`)

By default a template produces a **whole file** (`kind: full_file`): its
artifact is the complete contents of `output.path`.  A template can instead
declare that it produces a **bounded payload** for one fenced data block of a
page (`kind: region`):

```yaml
output:
  path: docs/status.md        # informational; region.page is the real anchor
  language: yaml
  kind: region
  region:
    page: docs/status.md      # hosting page name (or page-name pattern)
    ref: $block-status        # the $ref'd block whose payload this replaces
    anchor: $fix-tuesday      # optional annotation ref (addressed: list)
```

The kind/`region` coupling is enforced at **template load** (not render time):
`kind: region` without a `region:` block fails to load, and `kind: full_file`
with a `region:` block fails too.

**The body-only contract:** a region template's artifact is the *bare YAML
payload* — the page owns the fences.  The consumer splices the payload into
the block via its `replace_range` (the block's `CodeText` span), leaving the
fence and the surrounding human zone untouched.  Templateer renders and
*validates* the payload; it never writes regions (or files).

**The markdown validator.** `kind: "markdown"` checks fence balance,
single-document YAML, a structured payload (mapping or list — bare scalars
and empty payloads are rejected), round-trip stability, and duplicate-key
rejection (PyYAML silently keeps the last duplicate; a swapped payload must
not corrupt meaning silently).  For `kind: region` templates the check is
**enforced automatically** — it is the safety property the kind exists to
declare, so it cannot be omitted or turned off by a template author.  It is
prepended to whatever validators the template declares (an explicit
`kind: markdown` is not duplicated), so a broken block fails the generation
with `output_validation_failed`.

Safety semantics: templateer declares the boundary and validates the
artifact; the bounded write is the consumer's `replace_range`.  See the
region write-back seam in the argentic.space event-driven pipeline concept
(§5.3 owned regions, §5.4 the `kind: "region"` seam) for the consumer side.

### Schema Design Rules

1. **Model decisions, not syntax.** The schema tells the LLM *what* to choose, not how to format it.
2. **Use enums and literals** wherever possible to constrain choices.
3. **Use nested models** for structured concepts (e.g., `Dependency`, `RuffConfig`).
4. **Use validators** (`@model_validator`) to reject incompatible combinations (e.g., multiple web frameworks).
5. **Avoid freeform raw output fields.** If the LLM can write arbitrary text, it can bypass the pipeline.
6. **Include field descriptions** — the LLM reads them.
7. **Provide useful defaults** to make minimal inputs work.

### Rendering Rules

1. Undefined variables are errors — always. There is no lenient mode.
2. The template receives only the validated model dump — no raw LLM output, no filesystem access.
3. Complex decisions live in the schema, not in Jinja logic.
4. The template must not call external tools or read files.
5. In structured languages, every string interpolation sits inside double quotes; nullable fields are guarded with `{% if %}`.
6. The template is self-contained: paths are resolved relative to the template root, and a path escaping the root is a load error.

## Architecture

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Language | Python 3.12+ | Core implementation |
| Schema validation | Pydantic v2 | Type-safe, validated data |
| LLM integration | Pydantic AI | Structured output from LLMs |
| Template rendering | MiniJinja | Deterministic, sandboxed rendering |
| CLI | Click | Human and agent-friendly CLI |
| Testing | Pytest | Unit, integration, and template tests |
| Type checking | ty | Strict type checking |
| Linting | Ruff | Fast, comprehensive linting |

## Security Considerations

- **Escaping at the render boundary**: every value interpolated into an
  artifact is escaped for the target language (`escaping.py`), so a validated
  model cannot alter the artifact's structure — a string that looks like
  `\"\nlicense = \"PROPRIETARY` stays a string. This is enforced by the
  MiniJinja finalizer at every `{{ }}` output site, and cannot be bypassed by
  a template author.
- **`templateer check`**: each bundled template is audited against injection
  payloads — the audit probes every string field of every example fixture,
  re-renders, and verifies the artifact's structure is unchanged (0 findings
  is wired into the test suite).
- **Strict mode**: undefined variable references are errors, preventing
  accidental data leaks.
- **No raw LLM output in templates**: the LLM never writes a file directly.
  Templates are static, reviewed files.

**Trusted templates.** Loading a template executes its `schema.py` (it is
imported as Python) and runs any `command` validators declared in its metadata.
Templates are trusted code — the sandbox only covers the render step, not
template loading. For a personal library this is the right trade; do not load
templates from untrusted sources.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, testing guidelines, and how to add new templates.

```bash
# Install development dependencies
uv pip install -e ".[dev]"

# Run tests
uv run pytest

# Run tests with coverage
uv run pytest --cov=templateer --cov-report=term-missing

# Type checking
uv run ty check src/

# Linting
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```

## License

[License to be determined]
