# Contributing to Templateer

## Set up the project

Templateer requires Python 3.12 or later. The `devenv` shell supplies the
pinned Python and development tools.

```bash
devenv shell
uv sync --locked --extra dev
```

Run all later commands inside that shell. You can also run one command from
outside the shell with `devenv shell -- <command>`.

## Project structure

```text
templateer/
├── pyproject.toml              # Package metadata, dependencies, and tool settings
├── README.md                   # User documentation
├── CONTRIBUTING.md             # Contributor and template-author guidance
├── src/templateer/
│   ├── __init__.py             # Public exports and installed package version
│   ├── api.py                  # TemplateRegistry Python API
│   ├── audit.py                # Authoring lint and injection audit
│   ├── catalog.py              # Template discovery, lookup, and load errors
│   ├── cli.py                  # Click command-line interface
│   ├── escaping.py             # Language-aware interpolation escaping
│   ├── generator.py            # Pydantic AI generation and prompt context
│   ├── models.py               # Template metadata and validator models
│   ├── pipeline.py             # Generation, rendering, validation, and retries
│   ├── py.typed                # PEP 561 typing marker
│   ├── renderer.py             # MiniJinja rendering
│   ├── result.py               # Generation request, result, and failure types
│   ├── template.py             # Template loading and resource access
│   └── validators.py           # Artifact, region, and round-trip checks
├── tests/                      # Library and command-line interface tests
└── templates/pyproject-uv/     # Development example template
```

The wheel does not include the `templates/` directory. Users supply template
search paths at runtime.

## Architecture

Templateer uses this pipeline:

```text
Template lookup → Pydantic model generation → MiniJinja rendering → artifact validation
```

The language in `metadata.yml` selects the escape grammar and parser. It also
selects the audit payload set for structured languages.

The renderer receives validated Pydantic model data. The artifact then passes
its parse checks, declared validators, and round-trip checks.

## Run the quality gate

Run this gate before you submit a pull request:

```bash
pytest -q
ruff check src/ tests/ templates/
ty check src/
```

Run a focused test while you work:

```bash
pytest tests/test_models.py -q
pytest templates/pyproject-uv/tests/ -q
```

Run coverage when a change needs a coverage report:

```bash
pytest --cov=templateer --cov-report=term-missing
```

Tests use Pydantic AI test models by default. A real provider run needs its
provider API key.

## Add a template

Create a kebab-case directory under a template search path. This repository
uses `templates/` for development examples.

```text
templates/my-template/
├── metadata.yml
├── schema.py
├── prompt.md
├── template.j2
├── examples/
│   ├── minimal.input.json
│   └── minimal.output.toml
└── tests/
    └── test_my_template.py
```

### 1. Define the metadata

Use the singular `output` mapping. Use `trigger_filenames` for discovery hints.

```yaml
name: my-template
description: Generate one TOML configuration file.

output:
  kind: full_file
  path: output.toml
  language: toml

schema:
  module: schema
  class: MyModel

prompt:
  file: prompt.md

renderer:
  engine: minijinja
  file: template.j2

trigger_filenames:
  - output.toml
```

The `kind` field defaults to `full_file`. Set it explicitly when that improves
the metadata.

Full-file templates support these languages:

- `toml`
- `json`
- `yaml`
- `python`
- `markdown`
- `text`

A region template must use `kind: region` and `language: yaml`. It must also
define `output.region.page`, `output.region.ref`, and optional
`output.region.anchor`.

Metadata rejects unknown keys. Do not use the old `outputs`, `strict_context`,
or `triggers.filenames` fields.

### 2. Define the schema

Create a Pydantic model in `schema.py`:

```python
from pydantic import BaseModel, Field


class MyModel(BaseModel):
    name: str = Field(description="The package name")
    line_length: int = Field(default=100, ge=1)
```

Use literals for closed choices. Use nested models for structured concepts.
Add field descriptions because the language model reads them.

### 3. Write the prompt and renderer

Write task-specific instructions in `prompt.md`. The prompt must name the
schema and explain domain constraints.

Write deterministic MiniJinja syntax in `template.j2`:

```jinja
name = "{{ name }}"
line-length = {{ line_length }}
```

Quote string interpolations in structured output. Leave numeric and Boolean
interpolations unquoted when their schema types require it.

Use a loop to render a list or mapping. Direct container interpolation raises
`EscapeError`.

### 4. Add examples and tests

Add at least one `*.input.json` fixture. Templateer validates an example before
it uses that example in a generation prompt.

Add the expected artifact beside the input fixture. Add focused tests under
the template's `tests/` directory.

Test valid input and adversarial string values. Test every custom validator.

### 5. Check the template

Run the authoring audit, deterministic validation, and template tests:

```bash
templateer check my-template -p ./templates
templateer validate my-template \
  -p ./templates \
  --input templates/my-template/examples/minimal.input.json
pytest templates/my-template/tests/ -q
```

`templateer check` reports source-lint findings, fixture coverage, schema-field
coverage, skipped fields, and injection findings. The audit discovers string
fields from the Pydantic schema. It can construct omitted optional, nullable,
nested, and collection values. Pydantic validates every constructed model.
The audit records constraints that reject every probe value. It returns a
nonzero exit code when it audits no field.

## Code style

- Add type hints to public function signatures.
- Add docstrings to public modules, classes, functions, and methods.
- Use active voice and short sentences in documentation and messages.
- Include the template name, field name, or file path in actionable errors.
- Run the quality gate before you save a commit.

## Design decisions

### MiniJinja renderer

Templateer uses MiniJinja for deterministic rendering. Its strict undefined
behavior turns schema and renderer drift into an error.

### Pydantic AI generation

Templateer uses Pydantic AI for structured model generation. The outer pipeline
also retries repairable artifact failures with the prior error detail.

### Exact template names

The `name` in `metadata.yml` must match the directory name. Catalog lookup uses
that exact name and does not use fuzzy matching.

## Versioning

Templateer follows Semantic Versioning. Change the project version in
`pyproject.toml`. The public `templateer.__version__` value reads installed
package metadata.

## Behavioral specifications

The Allium specifications are in `.scratch/specs/allium/`. Update them when a
behavior change makes a specification stale.
