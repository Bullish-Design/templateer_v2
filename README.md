# Templateer

Templateer generates typed artifacts for people and AI agents.

The large language model (LLM) produces data for a Pydantic model. A MiniJinja
template renders the validated model into an artifact. The generation pipeline
then validates the artifact before it returns a successful result.

```text
User request and project facts
             |
             v
    Pydantic model from the LLM
             |
             v
      Pydantic validation
             |
             v
    MiniJinja template rendering
             |
             v
      Artifact validation
```

Templateer also supports LLM-free rendering from model data.

## Template sources

Templateer ships no templates in its installed package. Supply a template
library with one of these sources:

- Put template directories under `./templates` in the current directory.
- Pass one or more template-library paths with `-p` or `--paths`.

An explicit `-p` option replaces the `./templates` default. Repeat the option
to search more than one library.

This source checkout contains `templates/pyproject-uv` for development and
tests. The wheel configuration does not include that directory.

## Installation

Templateer requires Python 3.12 or later.

```bash
git clone <repo-url>
cd templateer
devenv shell
uv sync --locked --extra dev
```

## Quick start

These commands use a template named `my-template` under `./templates`.

```bash
# Discover templates and report load errors.
templateer list --strict

# Inspect metadata and the Pydantic schema.
templateer describe my-template
templateer schema my-template

# Render and validate model data without an LLM.
templateer render my-template --input model.json
templateer validate my-template --input model.json

# Audit the template source and its example fixtures.
templateer check my-template

# Generate an artifact with an LLM.
templateer generate my-template --request "Generate the configured artifact"
```

Use `-p` when the template library is not `./templates`:

```bash
templateer list -p /path/to/template-library --strict
templateer render my-template -p /path/to/template-library --input model.json
```

## Command-line interface

The command-line interface (CLI) provides these commands:

| Command | Purpose |
|---|---|
| `list` | List loaded templates and report load errors. |
| `describe NAME` | Show a template's metadata. |
| `schema NAME` | Emit the template's Pydantic schema as JSON. |
| `render NAME -i FILE` | Validate model data, render it, and validate the artifact. |
| `generate NAME` | Run the LLM generation pipeline. |
| `validate NAME -i FILE` | Test whether model data produces a valid artifact. |
| `check NAME` | Lint and audit a template. |

Run `templateer COMMAND --help` for the complete option list.

### JSON output

`list`, `describe`, `render`, `generate`, `validate`, and `check` accept
`--json`. Each command writes one JSON object to standard output in this mode.
The `schema` command emits JSON without this option.

`generate --json` emits the serialized `GenerationResult`. The other commands
emit command-specific records. Use `list --json` to read template load errors
from the `load_errors` field.

```bash
templateer list --json -p /path/to/template-library
templateer generate my-template --json --request "Generate it"
```

### Exit codes

Exit codes are part of the CLI contract.

| Code | Meaning | Examples |
|---:|---|---|
| `0` | Success | A command completed without findings. |
| `1` | Finding or invalid artifact | Model, render, output, or audit validation found a problem. |
| `2` | Infrastructure or configuration | The LLM failed, configuration failed, or an audit ran no probes. |
| `3` | Usage | A template, input file, or context file was unavailable or unusable. |

`list` reports broken templates without hiding them. Add `--strict` to make a
template load error exit with code 2.

Generation failures use this exact reason and exit-code table:

| Failure reason | Retryable | Exit code |
|---|---:|---:|
| `model_validation_failed` | Yes | `1` |
| `render_failed` | No | `1` |
| `output_validation_failed` | Yes | `1` |
| `config_error` | No | `2` |
| `llm_failed` | Yes | `2` |
| `internal_error` | No | `2` |
| `no_template` | No | `3` |

`internal_error` identifies an unforeseen implementation failure. Normal CLI
output reports the failure without a traceback. Debug logging retains the
traceback for diagnosis.

## Python API

The top-level package exports `TemplateRegistry`, `GenerationRequest`,
`GenerationResult`, and `FailureReason`.

### Async generation

Use `generate_async` in an application that has a running event loop.

```python
from templateer import TemplateRegistry

registry = TemplateRegistry.from_paths(["./templates"])

result = await registry.generate_async(
    template_name="my-template",
    user_request="Generate the configured artifact",
    context={"project": "example"},
    max_attempts=3,
)

if result.succeeded:
    print(result.artifact)
else:
    print(result.failure_reason, result.error_detail)
```

The result can also carry the validated model, warnings, token usage, and the
attempt number. A region result carries `kind="region"` and its `region`
boundary.

### Synchronous generation

Use `generate` from synchronous code that owns its thread. It calls
`asyncio.run`, so do not call it from a running event loop.

```python
result = registry.generate(
    template_name="my-template",
    user_request="Generate the configured artifact",
)
```

Generation failures use `failure_reason` and `error_detail`. Check
`result.succeeded` before you read `result.artifact`.

### LLM-free operations

```python
model_data = {"name": "example"}

artifact = registry.render_from_model(
    template_name="my-template",
    model_data=model_data,
)

errors, warnings = registry.validate_artifact(
    "my-template",
    artifact,
    model_data=model_data,
)

report = registry.audit("my-template")
print(report.audited, report.findings, report.fields_skipped)
```

Pass `model_data` to `validate_artifact` to enable the round-trip type check.
That check finds model strings that reach the parsed artifact as another type.

`render_from_model` validates the input against the template's Pydantic model.
Invalid model data raises `pydantic.ValidationError`.

## Template authoring

A template directory contains its metadata, schema, prompt, renderer, and
example fixtures.

```text
templates/my-template/
|-- metadata.yml
|-- schema.py
|-- prompt.md
|-- template.j2
|-- examples/
|   |-- my-template.input.json
|   `-- my-template.output.toml
`-- tests/
    `-- test_my_template.py
```

The directory name must equal `name` in `metadata.yml`.

### Output languages

The language set is closed. A spelling error or unsupported language causes a
template load error.

| Group | Allowed values | Behavior |
|---|---|---|
| Structured | `toml`, `json`, `yaml`, `python` | Language-aware string escaping, parser validation, source lint, and fixture audit. |
| Unstructured | `markdown`, `text` | Identity string handling and no structural parser or injection audit. |

A `full_file` output can use any language in the table. A `region` output must
use `yaml`.

### Metadata

This example declares a full-file template:

```yaml
name: my-template
description: Generate one configuration file.

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

validators:
  - kind: parse
    language: toml
  - kind: command
    command: ["python", "-m", "ruff", "check", "-"]
    optional: true
```

`kind` defaults to `full_file`. Metadata models reject unknown keys. They also
reject the old `outputs:` and `triggers:` shapes.

An optional validator produces a warning. A non-optional validator produces an
error. A failed command validator reports both standard output and standard
error.

### Schema and prompt

Define the LLM output with a Pydantic model:

```python
from typing import Literal

from pydantic import BaseModel, Field


class MyModel(BaseModel):
    name: str = Field(description="Project name")
    runtime: Literal["python", "rust"]
    line_length: int = Field(default=100, ge=40, le=160)
```

Use field descriptions, literals, defaults, and validators to constrain the
model. Put formatting rules in `template.j2`, not in free-form model fields.

Write `prompt.md` to explain the task. The generation prompt also receives the
Pydantic schema and one schema-valid example when one is available.

### Renderer authoring rule

Put each string interpolation inside double quotes in a structured-language
template.

```jinja
name = "{{ name }}"
line-length = {{ line_length }}
```

The source lint permits the unquoted `line_length` site because the schema
proves that it is an integer. It reports an unquoted string site. It also
reports an expression whose type it cannot resolve.

Use a loop to render a container:

```jinja
features = [
{% for feature in features %}
  "{{ feature }}",
{% endfor %}
]
```

Do not interpolate a list, dictionary, tuple, or set at one `{{ }}` site. The
renderer reports an `EscapeError` for these container values.

Guard nullable values before interpolation:

```jinja
{% if description %}
description = "{{ description }}"
{% endif %}
```

Undefined variables and interpolated null values cause render errors. Template
paths are resolved relative to the template root.

### Authoring checks

Add at least one `examples/*.input.json` fixture before you run the audit.

```bash
templateer validate my-template --input templates/my-template/examples/my-template.input.json
templateer check my-template
```

`validate` checks the model, renderer, output validators, and model-to-artifact
type round trip. `check` starts from each valid fixture. It discovers string
fields from the Pydantic schema. It also probes omitted optional fields,
nullable fields, nested fields, and collection elements when it can construct
a valid model.

The audit report includes `fixtures_seen`, `fields_probed`, `sites_linted`,
`fields_skipped`, `findings`, and `skipped_reason`. Each skipped-field record
names the fixture, field, and reason. The audit validates every synthesized
model through Pydantic. It probes at most 100 schema fields per fixture. It
records fields above that bound as skipped. A template without example
fixtures produces an unaudited report. A schema without string fields also
produces an unaudited report. `check` exits 2 when it audits no field. It exits
1 when the source lint or injection audit reports a finding.

### Region outputs

A region template produces the YAML body for one bounded page region. The page
consumer owns the fences and performs the write.

```yaml
output:
  kind: region
  language: yaml
  region:
    page: docs/status.md
    ref: $block-status
    anchor: $fix-tuesday
```

The template artifact contains bare YAML. It does not contain Markdown fences
or YAML document fences.

The region check rejects a fenced payload, a bare scalar, an empty payload, a
multi-document payload, and duplicate keys. Tests also try to downgrade this
check with `optional: true`. The effective validator keeps the region check
non-optional.

Successful region generation returns the boundary in `GenerationResult.region`.
The consumer can use `page`, `ref`, and optional `anchor` to identify its write
target.

## Security model

Templateer treats template libraries as trusted code. Loading a template
imports its `schema.py`. A template can also declare command validators.

MiniJinja receives the validated model dump. For structured languages, the
renderer escapes string content for a double-quoted literal. It rejects lone
surrogates, null interpolation, and direct container interpolation.

The parser check proves that an artifact has valid syntax. The round-trip check
adds a type check for model strings. The authoring lint and fixture audit add
independent checks for unsafe interpolation sites.

Do not load a template library from an untrusted source.

## Development

Read [CONTRIBUTING.md](CONTRIBUTING.md) before you change the project.

Enter the pinned environment before you run project commands:

```bash
devenv shell
```

Run the required checks:

```bash
pytest -q
ruff check src/ tests/ templates/
ty check src/
```

## License

No license is declared yet.
