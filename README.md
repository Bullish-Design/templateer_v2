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
- **Output validation**: rendered artifacts are parsed/validated (TOML, JSON, YAML, Python) to catch rendering bugs.
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

Show a template's metadata: description, output kind, trigger paths, and output specs.

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
| `-p`, `--paths` | Additional template search paths |

### `templateer validate <name> --input <file>`

Validate that a model JSON file produces valid output. Runs three checks: model validation, rendering, and output validation.

```
templateer validate pyproject-uv --input model.json
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
print(result.rendered)

# Validate an artifact
errors = registry.validate_artifact("pyproject-uv", rendered)
```

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

outputs:
  - path: output.txt
    kind: full_file
    language: toml       # toml, json, yaml, python, ...

schema:
  module: schema         # Python module name (without .py)
  class: MyModel         # Pydantic model class name

prompt:
  file: prompt.md

renderer:
  engine: minijinja
  file: template.j2

strict_context: true     # Undefined variables in template = error

triggers:
  filenames:
    - output.txt
```

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

The template receives ONLY the validated model dump. In strict mode, references to undefined variables are errors.

```jinja
[project]
name = "{{ name }}"
language = "{{ language }}"
features = [
{% for f in features %}
  "{{ f }}",
{% endfor %}
]
```

### 5. Add examples and tests

Create `examples/scenario.input.json` and `examples/scenario.output.txt`, then verify:

```bash
uv run templateer validate my-template --input examples/scenario.input.json
```

### Schema Design Rules

1. **Model decisions, not syntax.** The schema tells the LLM *what* to choose, not how to format it.
2. **Use enums and literals** wherever possible to constrain choices.
3. **Use nested models** for structured concepts (e.g., `Dependency`, `RuffConfig`).
4. **Use validators** (`@model_validator`) to reject incompatible combinations (e.g., multiple web frameworks).
5. **Avoid freeform raw output fields.** If the LLM can write arbitrary text, it can bypass the pipeline.
6. **Include field descriptions** — the LLM reads them.
7. **Provide useful defaults** to make minimal inputs work.

### Rendering Rules

1. Undefined variables are errors (strict mode).
2. The template receives only the validated model dump — no raw LLM output, no filesystem access.
3. Complex decisions live in the schema, not in Jinja logic.
4. The template must not call external tools or read files.

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

- **Template sandboxing**: MiniJinja templates have no filesystem access and no shell execution. They can only access the validated model data they receive.
- **Strict mode**: undefined variable references are errors, preventing accidental data leaks.
- **Output validation**: rendered artifacts are parsed to catch injection or corruption before they hit disk.
- **No raw LLM output in templates**: the LLM never writes a file directly. Templates are static, reviewed files.

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
