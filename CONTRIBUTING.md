# Contributing to Templateer

## Development Setup

```bash
# Clone the repository
git clone <repo-url>
cd templateer

# Install development dependencies
uv pip install -e ".[dev]"

# Verify everything works
uv run pytest
uv run ty check src/
uv run ruff check src/ tests/
```

Requirements: Python ≥ 3.12, uv.

## Project Structure

```
templateer/
├── pyproject.toml          # Project metadata and dependencies
├── README.md               # User-facing documentation
├── CONTRIBUTING.md         # This file
├── src/
│   └── templateer/
│       ├── __init__.py     # Version, default template paths
│       ├── py.typed        # PEP 561 marker
│       ├── api.py          # Python API (TemplateRegistry)
│       ├── catalog.py      # TemplateCatalog
│       ├── cli.py          # CLI (Click)
│       ├── generation.py   # Generation entity + enums
│       ├── generator.py    # Pydantic AI model generation
│       ├── models.py       # Pydantic models (metadata, etc.)
│       ├── pipeline.py     # End-to-end pipeline
│       ├── renderer.py     # MiniJinja renderer
│       ├── template.py     # Template loader
│       ├── validation.py   # Model validation utilities
│       └── validators.py   # Output validators
├── tests/
│   ├── conftest.py
│   ├── test_api.py
│   ├── test_catalog.py
│   ├── test_cli.py
│   ├── test_generator.py
│   ├── test_integration.py
│   ├── test_models.py
│   ├── test_pipeline.py
│   ├── test_renderer.py
│   ├── test_template.py
│   └── test_validators.py
└── templates/
    └── pyproject-uv/
        ├── metadata.yml
        ├── schema.py
        ├── prompt.md
        ├── template.j2
        ├── examples/
        │   ├── fastapi.input.json
        │   └── fastapi.output.toml
        └── tests/
            └── test_pyproject_uv_template.py
```

## Architecture

Templateer follows a pipeline architecture:

```
Template catalog → Template resolution → LLM model generation → Jinja rendering → Output validation
```

### Key Concepts

| Concept | Description |
|---------|-------------|
| **Template** | A directory containing everything needed to generate one kind of artifact |
| **Schema** | A Pydantic model defining the structured data the LLM must produce |
| **Prompt** | Instructions that help the LLM fill the schema correctly |
| **Renderer** | Deterministic Jinja rendering from validated model data only |
| **Context** | Input facts passed to the LLM — never the template |
| **Catalog** | Collection of all available templates, lookup by exact name |

### Central Invariant

> **A renderer may only receive validated Pydantic model data.** No raw LLM output, user prompt, environment variables, or filesystem context reaches the template.

## Running Tests

```bash
# Run all tests
uv run pytest

# Run specific test files
uv run pytest tests/test_models.py -v

# Run with coverage
uv run pytest --cov=templateer --cov-report=term-missing

# Run LLM-dependent tests (requires OPENAI_API_KEY)
OPENAI_API_KEY=sk-... uv run pytest tests/test_generator.py -v

# Run template-specific tests
uv run pytest templates/pyproject-uv/tests/ -v
```

## Quality Gates

Before submitting a PR, ensure:

```bash
# All tests pass
uv run pytest

# Strict type checking passes
uv run ty check src/

# Ruff linting passes
uv run ruff check src/ tests/

# Formatted correctly
uv run ruff format src/ tests/

# Coverage ≥ 80%
uv run pytest --cov=templateer --cov-report=term-missing
```

## Adding a New Template

1. Create a directory under `templates/` with your template name (kebab-case):
   ```
   templates/my-template/
   ```

2. Create `metadata.yml`:
   ```yaml
   name: my-template
   description: What this template generates.

   outputs:
     - path: output.ext
       kind: full_file
       language: toml

   schema:
     module: schema
     class: MyModel

   prompt:
     file: prompt.md

   renderer:
     engine: minijinja
     file: template.j2

   strict_context: true

   triggers:
     filenames:
       - output.ext
   ```

3. Create `schema.py` with your Pydantic model.

4. Create `prompt.md` with LLM instructions.

5. Create `template.j2` with the Jinja template.

6. Create `examples/scenario.input.json` and `examples/scenario.output.ext`.

7. Create `tests/test_my_template.py` with template-specific tests.

8. Verify:
   ```bash
   uv run templateer validate my-template --input examples/scenario.input.json
   uv run pytest templates/my-template/tests/ -v
   ```

### Template Design Guidelines

- **Model decisions, not syntax.** The schema constrains what choices the LLM can make.
- **Use enums/literals** where possible to limit options.
- **Nested models** for structured concepts.
- **Validators** (`@model_validator`) for cross-field constraints.
- **Field descriptions** — the LLM reads them.
- **Useful defaults** so minimal inputs work.
- **Strict context** — always enable `strict_context: true`.

## Architecture Decision Records

### ADR 1: MiniJinja over Jinja2

**Decision**: Use MiniJinja for template rendering.

**Rationale**: MiniJinja provides the same Jinja syntax but with better sandboxing guarantees. Strict mode causes errors on undefined variables rather than silently producing empty strings, which aligns with Templateer's security model.

### ADR 2: Pydantic AI over direct API calls

**Decision**: Use Pydantic AI for LLM integration instead of raw LLM API calls.

**Rationale**: Pydantic AI provides built-in structured output support with automatic retry on validation failure. This aligns with Templateer's philosophy of constrained generation — the LLM is forced to produce valid structured data.

### ADR 3: Template name must match directory name

**Decision**: Enforce that `metadata.yml`'s `name` field matches the directory name.

**Rationale**: Eliminates confusion about template identity. The directory name is the sole lookup key, and the invariant ensures consistency.

### ADR 4: Exact name matching only (no fuzzy search)

**Decision**: TemplateCatalog uses exact string matching for template names.

**Rationale**: Predictable, deterministic behavior. Fuzzy matching would introduce ambiguity that is undesirable for agent-driven workflows.

## Code Style

- **Docstrings**: All public functions, classes, and methods must have docstrings with Google-style Args/Returns/Raises.
- **Type hints**: All function signatures must be fully type-hinted.
- **Error messages**: Error messages should include enough context to identify the problem:
  - Template name when template lookup fails
  - File path when a file is missing
  - Variable name when rendering fails
- **Ruff**: Configuration is in `pyproject.toml`. Run `uv run ruff format` before committing.

## Versioning

Templateer follows [Semantic Versioning](https://semver.org/). The version is stored in `src/templateer/__init__.py`.

## Questions?

Open an issue or consult the Allium specs at `.scratch/specs/allium/` for the behavioural specification.
