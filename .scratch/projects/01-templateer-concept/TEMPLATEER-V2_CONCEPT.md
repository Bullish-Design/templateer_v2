# Templateer Concept

## Summary

Templateer is an agent-facing template generation library and CLI.

Its purpose is to replace freeform LLM file generation with a constrained, typed, deterministic generation pipeline.

Instead of asking an LLM to write an entire file directly, Templateer asks an LLM to instantiate a Pydantic model. That validated model is then passed to a Jinja-compatible template renderer. The final file is generated deterministically from the template.

Templateer is not primarily a long-lived project state manager, migration tool, or generated-code ownership system. It is a generation-time constraint system for AI agents.

The core idea:

```text
User intent + optional project facts
        ↓
LLM fills Pydantic model
        ↓
Pydantic validates model
        ↓
Jinja renders using only validated model values
        ↓
Generated file or artifact
```

The LLM never writes the target file directly.

The template never receives arbitrary raw context.

The rendered artifact is composed only from validated structured data.

---

## Problem

LLMs are useful for generating project files, configuration files, code scaffolds, and boilerplate. However, direct whole-file generation has several recurring problems:

1. The model must invent structure and content at the same time.
2. The model may hallucinate invalid syntax.
3. The model may produce inconsistent formatting.
4. The model may include unrequested sections.
5. The model may omit required fields.
6. The model may mix incompatible conventions.
7. The output is hard to validate before use.
8. Smaller models often struggle with full-file generation.
9. Repeated prompts for similar files waste context and tokens.

For common programming artifacts, much of the structure is already known:

- `pyproject.toml`
- `devenv.nix`
- `flake.nix`
- GitHub Actions workflows
- `Dockerfile`
- `docker-compose.yml`
- `devcontainer.json`
- `pre-commit-config.yaml`
- Ruff config
- Pytest config
- Mypy config
- FastAPI route files
- SQLModel/Pydantic model files
- README sections
- Makefile or Justfile tasks

These files usually have stable shapes but context-dependent values.

Templateer exists to separate those concerns.

The LLM chooses values.

The template controls structure.

Pydantic validates the interface between them.

---

## Non-Goals

Templateer is intentionally not designed as a full project mutation framework.

The initial concept does not require:

- persistent generation state;
- idempotency guarantees;
- managed regions;
- generated-code ownership tracking;
- template migrations;
- long-term project reconciliation;
- automatic merging of future edits;
- full-file lifecycle management;
- custom mixed Python/Jinja file formats.

Those features could be added later, but they are not part of the core concept.

Templateer’s primary job is simpler:

> When an AI agent needs to generate an artifact and a matching Templateer template exists, the agent should use the template instead of generating the artifact freeform.

---

## Core Principle

The central invariant of Templateer is:

```text
A renderer may only receive validated Pydantic model data.
```

This means:

- no raw LLM response is passed to the template;
- no original user prompt is passed to the template;
- no arbitrary repository context is passed to the template;
- no unvalidated agent scratchpad is passed to the template;
- no environment variables are passed to the template;
- no filesystem data is available to the template unless it was first converted into a validated model field.

The Jinja context is produced from a Pydantic model instance:

```python
render_context = model.model_dump(mode="json")
```

The template renders only from this context.

This makes Templateer closer to a typed compiler pipeline than a traditional prompt-based code generator.

---

## Mental Model

Templateer should be understood as a typed code generation backend for AI agents.

Traditional LLM generation:

```text
Prompt
  ↓
LLM writes entire file
  ↓
User hopes it is valid
```

Templateer generation:

```text
Prompt + facts
  ↓
LLM fills Pydantic model
  ↓
Pydantic validates model
  ↓
Jinja renders deterministic artifact
  ↓
Optional parser/tool validation
```

The generated artifact is still influenced by the LLM, but only through a typed intermediate representation.

---

## Terminology

### Template

A Templateer template is a folder containing the files needed to generate one kind of artifact.

Example:

```text
templates/
  pyproject-uv/
    metadata.yml
    schema.py
    prompt.md
    template.j2
    examples/
      fastapi.input.json
      fastapi.output.toml
```

### Artifact

The generated output of a template.

Examples:

- a complete `pyproject.toml`;
- a complete GitHub Actions workflow;
- a `Dockerfile`;
- a generated Python module;
- a README section;
- a Nix development shell file.

### Schema

A Pydantic model defining the structured data the LLM must produce.

The schema is the only allowed interface between the LLM and the renderer.

### Prompt

A template-specific instruction file that helps the LLM instantiate the schema correctly.

The prompt is not the source of truth. The schema is.

### Renderer

The deterministic rendering stage, typically powered by MiniJinja or another Jinja-compatible engine.

### Context

Input facts provided to the LLM to help it fill the schema.

Context may include the user request, agent-supplied information, or extracted project facts.

Context does not go directly to the template.

### Project Facts

Structured information about the repository or environment.

Examples:

```json
{
  "uses_fastapi": true,
  "uses_pytest": true,
  "detected_python_version": "3.12",
  "package_manager": "uv"
}
```

Project facts may be extracted by static analysis tools such as ast-grep, file inspection, or language-specific parsers.

---

## Template Folder Structure

A Templateer template should be a directory with a small number of explicit files.

Recommended structure:

```text
template-name/
  metadata.yml
  schema.py
  prompt.md
  template.j2
  examples/
    minimal.input.json
    minimal.output.txt
  tests/
    test_template.py
```

### `metadata.yml`

Declares what the template generates and when it is applicable.

Example:

```yaml
name: pyproject-uv
description: Generate a uv-style pyproject.toml for a Python project.

outputs:
  - path: pyproject.toml
    kind: full_file
    language: toml

schema:
  module: schema
  class: PyprojectUvModel

prompt:
  file: prompt.md

renderer:
  engine: minijinja
  file: template.j2

strict_context: true

triggers:
  filenames:
    - pyproject.toml
```

### `schema.py`

Defines the Pydantic model that the LLM must fill.

Example:

```python
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Dependency(BaseModel):
    name: str = Field(description="Package name.")
    version: str | None = Field(
        default=None,
        description="Optional version constraint, such as '>=1.0'.",
    )
    extras: list[str] = Field(default_factory=list)


class RuffConfig(BaseModel):
    line_length: int = Field(default=100, ge=79, le=120)
    target_version: str = Field(description="Python target version, such as 'py312'.")
    select: list[str] = Field(default_factory=lambda: ["E", "F", "I"])
    ignore: list[str] = Field(default_factory=list)


class PytestConfig(BaseModel):
    testpaths: list[str] = Field(default_factory=lambda: ["tests"])
    addopts: list[str] = Field(default_factory=list)


class PyprojectUvModel(BaseModel):
    project_name: str
    project_description: str | None = None
    python_version: str = Field(description="Minimum Python version, such as '3.12'.")
    project_type: Literal["application", "library", "cli"] = "application"

    dependencies: list[Dependency] = Field(default_factory=list)
    dev_dependencies: list[Dependency] = Field(default_factory=list)

    ruff: RuffConfig | None = None
    pytest: PytestConfig | None = None

    @model_validator(mode="after")
    def validate_web_framework_choices(self):
        framework_names = {
            dep.name.lower()
            for dep in self.dependencies
            if dep.name.lower() in {"fastapi", "django", "flask"}
        }

        if len(framework_names) > 1:
            raise ValueError(
                "Choose at most one primary Python web framework."
            )

        return self
```

### `prompt.md`

Helps the model fill the schema.

Example:

```markdown
You are filling a `PyprojectUvModel`.

Use the user request and provided project facts to choose values.

Rules:

- Prefer `uv`-style dependency groups.
- Do not invent unrelated frameworks.
- If the project uses FastAPI, include `fastapi` and `uvicorn`.
- If tests are requested or detected, include `pytest` in dev dependencies.
- If linting or formatting is requested, include Ruff config.
- Choose a Python version consistent with the project facts.
- Return only data that conforms to the schema.
```

### `template.j2`

Renders the artifact from the validated model data.

Example:

```jinja
[project]
name = "{{ project_name }}"
{% if project_description %}
description = "{{ project_description }}"
{% endif %}
requires-python = ">={{ python_version }}"
dependencies = [
{% for dependency in dependencies %}
  "{{ dependency.name }}{% if dependency.extras %}[{{ dependency.extras | join(',') }}]{% endif %}{% if dependency.version %}{{ dependency.version }}{% endif %}",
{% endfor %}
]

{% if dev_dependencies %}
[dependency-groups]
dev = [
{% for dependency in dev_dependencies %}
  "{{ dependency.name }}{% if dependency.extras %}[{{ dependency.extras | join(',') }}]{% endif %}{% if dependency.version %}{{ dependency.version }}{% endif %}",
{% endfor %}
]
{% endif %}

{% if ruff %}
[tool.ruff]
line-length = {{ ruff.line_length }}
target-version = "{{ ruff.target_version }}"

[tool.ruff.lint]
select = [
{% for code in ruff.select %}
  "{{ code }}",
{% endfor %}
]
ignore = [
{% for code in ruff.ignore %}
  "{{ code }}",
{% endfor %}
]
{% endif %}

{% if pytest %}
[tool.pytest.ini_options]
testpaths = [
{% for path in pytest.testpaths %}
  "{{ path }}",
{% endfor %}
]
{% if pytest.addopts %}
addopts = [
{% for opt in pytest.addopts %}
  "{{ opt }}",
{% endfor %}
]
{% endif %}
{% endif %}
```

The template should fail if it references undefined values.

Undefined variables should not silently render as empty strings.

---

## The Generation Pipeline

Templateer’s pipeline has five main stages.

### 1. Template Lookup

The agent or requester chooses a template by name and the system resolves it.

Example:

```bash
templateer describe pyproject-uv
```

If a template with that exact name exists in the catalog, it is used.
If no template with that name exists, the generation fails.

Template selection is an agent responsibility: the agent browses available
templates (by name, description, output kind, and trigger paths) and chooses
one explicitly.

### 2. Context Collection

Templateer or the agent gathers facts needed to fill the model.

Facts may come from:

- the user request;
- filenames;
- project metadata;
- existing files;
- package manager files;
- static analysis;
- ast-grep queries;
- CLI flags.

Example context:

```json
{
  "user_request": "Generate a pyproject.toml for a FastAPI app using uv, pytest, and ruff.",
  "facts": {
    "package_manager": "uv",
    "detected_python_version": "3.12",
    "uses_fastapi": true,
    "uses_pytest": true
  }
}
```

This context is used by the LLM.

It is not passed directly to Jinja.

### 3. Typed Model Instantiation

Pydantic AI is used to ask an LLM to return an object matching the template schema.

Example:

```python
from pydantic_ai import Agent

from schema import PyprojectUvModel


agent = Agent(
    "openai:gpt-4.1-mini",
    output_type=PyprojectUvModel,
    instructions=prompt_text,
)

result = agent.run_sync(context_text)

model = result.output
```

The result must be a valid `PyprojectUvModel`.

If validation fails, Templateer may retry with the validation error, fail explicitly, or ask the calling agent for correction.

### 4. Rendering

The validated model is converted into a render context:

```python
render_context = model.model_dump(mode="json")
```

The renderer receives only that context.

```python
rendered = render_template("template.j2", render_context)
```

No other values are available to the template.

### 5. Optional Output Validation

After rendering, Templateer may validate the generated artifact.

Examples:

- parse TOML for `pyproject.toml`;
- parse YAML for GitHub Actions;
- parse JSON for `devcontainer.json`;
- parse Python with `ast.parse`;
- run a formatter;
- run a project-specific validation command.

This validation is separate from Pydantic validation.

Pydantic validates the intermediate representation.

Output validation checks the final artifact.

---

## Pydantic AI Integration

Templateer should use Pydantic AI as the structured-output layer.

The important behavior is that the LLM is not asked to return arbitrary text. It is asked to return a specific Pydantic type.

Conceptual usage:

```python
from pydantic_ai import Agent


def generate_model(
    model_name: str,
    output_type: type,
    instructions: str,
    context: str,
):
    agent = Agent(
        model_name,
        output_type=output_type,
        instructions=instructions,
    )

    result = agent.run_sync(context)

    return result.output
```

Templateer should treat the result as a model instance, not as text.

The LLM output lifecycle should look like this:

```text
raw model response
  ↓
Pydantic AI structured output parsing
  ↓
Pydantic validation
  ↓
validated object
```

Only the validated object continues to rendering.

---

## Strict Rendering Contract

Templateer should enforce strict rendering.

The renderer should have access to:

```text
model.model_dump(mode="json")
```

The renderer should not have access to:

- raw LLM output;
- the full prompt;
- project files;
- environment variables;
- arbitrary Python objects;
- raw user request;
- external tools;
- hidden agent state.

This keeps the rendering layer deterministic and auditable.

### Good

```jinja
{% if ruff %}
[tool.ruff]
line-length = {{ ruff.line_length }}
target-version = "{{ ruff.target_version }}"
{% endif %}
```

### Bad

```jinja
{% if "fastapi" in raw_user_prompt %}
...
{% endif %}
```

### Bad

```jinja
{{ read_file("pyproject.toml") }}
```

### Bad

```jinja
{{ llm_raw_response }}
```

The template must only render fields that exist in the Pydantic model.

---

## The Role of ast-grep

ast-grep is useful, but it should not be the core rendering mechanism.

In Templateer, ast-grep is best used for context extraction.

Examples:

- detect whether a Python project uses FastAPI;
- detect whether SQLModel is imported;
- detect whether pytest tests exist;
- detect existing function or class patterns;
- detect whether a project already uses a particular framework.

Example metadata:

```yaml
context_extractors:
  - name: uses_fastapi
    tool: ast-grep
    language: python
    pattern: "from fastapi import $$$"

  - name: uses_sqlmodel
    tool: ast-grep
    language: python
    pattern: "from sqlmodel import $$$"

  - name: has_pytest_tests
    tool: file-glob
    pattern: "tests/test_*.py"
```

These facts can be supplied to the LLM:

```json
{
  "uses_fastapi": true,
  "uses_sqlmodel": false,
  "has_pytest_tests": true
}
```

The facts help fill the Pydantic model.

They do not go directly into the Jinja context unless the schema explicitly includes them.

---

## Template Selection

Template selection is exact name match.

The requester specifies the template by its directory name (e.g. `pyproject-uv`).
If a template with that name exists in the catalog, it is used. If not, the
generation fails with a "template not found" reason.

There is no scored matching, no heuristic selection, and no fuzzy lookup.
The agent is responsible for browsing available templates and choosing one
by name before submitting a generation request.

---

## Template Expressiveness

A template should encode stable structure.

A schema should encode meaningful choices.

The LLM should not be asked to produce raw syntax unless no better structured representation exists.

### Good Schema

```python
class Dependency(BaseModel):
    name: str
    version: str | None = None
    extras: list[str] = Field(default_factory=list)


class PyprojectModel(BaseModel):
    project_name: str
    python_version: str
    dependencies: list[Dependency]
    dev_dependencies: list[Dependency]
```

### Weak Schema

```python
class PyprojectModel(BaseModel):
    raw_toml: str
```

The weak schema collapses Templateer back into freeform file generation.

### Suspicious Schema

```python
class PyprojectModel(BaseModel):
    dependencies: list[str]
    extra_content: str | None = None
```

Raw escape hatches should be rare.

If a template frequently needs `extra_content`, the schema is probably missing a first-class concept.

---

## Escape Hatches

Templateer should avoid raw-text escape hatches by default.

Fields such as the following should be treated carefully:

```python
raw_text: str
extra_block: str
custom_section: str
additional_content: list[str]
```

These fields weaken the core guarantee.

Sometimes they may be necessary, but they should be explicit and visible.

If supported, metadata should flag them:

```yaml
allows_raw_output_fields: false
```

Or, for exceptional templates:

```yaml
allows_raw_output_fields: true
raw_output_fields:
  - custom_markdown_section
```

The default should be no raw output fields.

---

## Schema Design Guidelines

A good Templateer schema should:

1. Model decisions, not syntax.
2. Use enums or literals where possible.
3. Use nested models for structured concepts.
4. Use validation to reject obviously incompatible choices.
5. Avoid freeform raw output fields.
6. Include field descriptions for the LLM.
7. Provide useful defaults.
8. Separate renderable artifact data from non-rendered diagnostics.

### Example: Separate Artifact and Notes

Sometimes the agent should return notes or rationale.

Those notes should not be rendered unless intentionally part of the artifact.

Good:

```python
class ArtifactModel(BaseModel):
    project_name: str
    dependencies: list[str]


class GenerationResult(BaseModel):
    artifact: ArtifactModel
    notes: list[str] = Field(default_factory=list)
```

The renderer receives only:

```python
result.artifact.model_dump(mode="json")
```

Not:

```python
result.model_dump(mode="json")
```

This preserves the strict rendering contract.

---

## Rendering Guidelines

The renderer should be deterministic and strict.

Recommended rules:

1. Undefined variables are errors.
2. The template receives only the validated model dump.
3. Template logic should be shallow.
4. Complex decisions should live in the schema/model, not Jinja.
5. The renderer should not call external tools.
6. The renderer should not read files.
7. The renderer should not access environment variables.
8. Rendered output should be parsed or checked when possible.

### Good Jinja

```jinja
{% if pytest %}
[tool.pytest.ini_options]
testpaths = [
{% for path in pytest.testpaths %}
  "{{ path }}",
{% endfor %}
]
{% endif %}
```

### Problematic Jinja

```jinja
{% if "fastapi" in dependencies and project_type != "library" and python_version >= "3.12" %}
...
{% endif %}
```

That conditional likely belongs in the Pydantic model as an explicit field.

---

## Output Validation

Pydantic validates the intermediate model.

It does not prove that the rendered file is syntactically or semantically valid.

Templateer should optionally support output validators.

Example metadata:

```yaml
validators:
  - type: parse
    language: toml

  - type: command
    command: ["uv", "lock", "--check"]
    optional: true
```

Examples:

| Artifact | Validation |
|---|---|
| `pyproject.toml` | TOML parse |
| `*.json` | JSON parse |
| `*.yaml` | YAML parse |
| `*.py` | Python AST parse |
| GitHub Actions workflow | YAML parse, optional action lint |
| Nix files | formatter/checker if available |
| Markdown | optional markdownlint |
| Shell script | shell parser if available |

Output validation should be explicit and visible.

---

## CLI Concept

Templateer should expose a CLI that is convenient for agents and humans.

### List templates

```bash
templateer list
```

### Describe a template

```bash
templateer describe pyproject-uv
```

### Show the schema

```bash
templateer schema pyproject-uv
```

### Generate the Pydantic model

```bash
templateer model pyproject-uv --context context.json
```

### Render from a model JSON file

```bash
templateer render pyproject-uv --input model.json
```

### Generate full artifact

```bash
templateer generate pyproject-uv --context context.json --output pyproject.toml
```

### Dry run

```bash
templateer generate pyproject-uv --context context.json --stdout
```

### Validate rendered output

```bash
templateer validate pyproject-uv --input model.json
```

---

## Agent Workflow

An AI agent using Templateer should follow this flow:

```text
1. User asks for an artifact.
2. Agent browses available templates by name, description, and trigger paths.
3. Agent selects a template by its exact name.
4. Templateer exposes the schema and prompt.
5. Agent or Templateer invokes Pydantic AI to fill the schema.
6. Templateer validates the Pydantic model.
7. Templateer renders the artifact using only model values.
8. Agent returns or writes the generated artifact.
```

Example:

```text
User:
  Generate a pyproject.toml for a FastAPI app using uv, pytest, and ruff.

Agent:
  Calls `templateer list` to browse templates, then selects `pyproject-uv` by name.

Agent:
  Calls `templateer generate pyproject-uv`.

Templateer:
  Collects context.
  Uses Pydantic AI to produce `PyprojectUvModel`.
  Validates the model.
  Renders `template.j2`.
  Returns generated TOML.
```

---

## Programmatic API Concept

Templateer should also expose a Python API.

Example:

```python
from templateer import TemplateRegistry, TemplateRenderer


registry = TemplateRegistry.from_paths(["./templates"])

template = registry.get("pyproject-uv")

result = template.generate(
    user_request="Generate a pyproject.toml for a FastAPI app using uv.",
    context={
        "uses_fastapi": True,
        "uses_pytest": True,
        "detected_python_version": "3.12",
    },
)

print(result.artifact)
```

Possible result object:

```python
class TemplateGenerationResult(BaseModel):
    template_name: str
    model: BaseModel
    rendered: str
    validation_messages: list[str] = []
```

---

## Security Model

Templateer should treat templates as potentially risky unless trusted.

Security concerns include:

- executable Python in `schema.py`;
- arbitrary validators;
- prompt injection through context;
- leaking repository secrets into LLM context;
- rendering untrusted raw fields;
- writing generated artifacts to sensitive paths.

Recommended defaults:

1. Show or return output before writing files.
2. Do not include secrets in context.
3. Do not pass environment variables to templates.
4. Do not pass raw repo files unless requested.
5. Prefer structured facts over large raw context.
6. Treat templates from unknown sources as untrusted.
7. Make external command validators opt-in.
8. Avoid arbitrary template hooks in the core design.

The safest version of Templateer has a narrow runtime:

```text
load schema
load prompt
collect allowed context
call Pydantic AI
validate model
render Jinja from model dump
validate output
return artifact
```

---

## Why This Helps Smaller Models

Templateer reduces the task size for the model.

Without Templateer:

```text
Generate an entire correct pyproject.toml.
```

With Templateer:

```text
Fill this PyprojectUvModel.
```

That is easier because:

- the output shape is known;
- the model has fewer formatting responsibilities;
- the allowed fields are explicit;
- validation errors can be fed back to the model;
- rendering is deterministic;
- the model does not need to remember full file syntax.

This enables parallel generation across smaller, specialized models.

For example:

```text
Model A fills pyproject template.
Model B fills GitHub Actions template.
Model C fills Dockerfile template.
Model D fills devcontainer template.
```

Each model performs a small structured task.

The outputs can be rendered independently.

---

## What Templateer Guarantees

Templateer can guarantee:

1. The LLM did not directly write the artifact.
2. The rendered artifact was generated from a Pydantic model.
3. The model satisfied the declared schema.
4. The template only saw validated model fields.
5. Rendering was deterministic with respect to the model and template.
6. Optional output validators passed, if configured.

Templateer cannot guarantee by itself:

1. The generated artifact is semantically perfect.
2. The selected dependencies are ideal.
3. The project architecture is correct.
4. The template reflects the latest ecosystem best practices.
5. The chosen template was the best possible template.
6. The artifact will work without project-specific testing.

Templateer constrains generation. It does not eliminate the need for verification.

---

## Core Design Constraints

Templateer should preserve the following constraints:

### 1. The LLM produces models, not files

The LLM’s output type is a Pydantic model.

### 2. The template sees only the model

The render context is derived only from the validated model.

### 3. The template is deterministic

Given the same model and template, rendering should produce the same output.

### 4. The schema is the contract

The schema defines the generation surface.

### 5. The prompt is advisory

The prompt helps the model instantiate the schema but does not replace validation.

### 6. Raw output fields are discouraged

Raw text fields should not be the main way to express customization.

### 7. Output validation is separate

Pydantic validates the model. Artifact validators check the rendered file.

### 8. ast-grep extracts facts

ast-grep helps understand the project. It does not directly drive rendering.

---

## Example End-to-End Template

### `metadata.yml`

```yaml
name: github-actions-python-ci
description: Generate a GitHub Actions CI workflow for a Python project.

outputs:
  - path: .github/workflows/ci.yml
    kind: full_file
    language: yaml

schema:
  module: schema
  class: GitHubActionsPythonCiModel

prompt:
  file: prompt.md

renderer:
  engine: minijinja
  file: template.j2

strict_context: true

triggers:
  filenames:
    - .github/workflows/ci.yml
```

### `schema.py`

```python
from typing import Literal

from pydantic import BaseModel, Field


class PythonVersion(BaseModel):
    version: str = Field(description="Python version such as '3.12'.")


class GitHubActionsPythonCiModel(BaseModel):
    workflow_name: str = "CI"
    branches: list[str] = Field(default_factory=lambda: ["main"])
    python_versions: list[PythonVersion]
    package_manager: Literal["uv", "poetry", "pip"] = "uv"
    install_command: str
    test_command: str
    lint_command: str | None = None
```

### `prompt.md`

```markdown
Fill `GitHubActionsPythonCiModel`.

Rules:

- Use the package manager detected in project facts.
- If the package manager is uv, prefer `uv sync`.
- If pytest is detected, use `uv run pytest` or the equivalent for the package manager.
- Include a lint command only if linting is requested or detected.
- Use the detected Python version unless the user requests a matrix.
```

### `template.j2`

```jinja
name: {{ workflow_name }}

on:
  push:
    branches:
{% for branch in branches %}
      - {{ branch }}
{% endfor %}
  pull_request:
    branches:
{% for branch in branches %}
      - {{ branch }}
{% endfor %}

jobs:
  test:
    runs-on: ubuntu-latest

    strategy:
      matrix:
        python-version:
{% for python in python_versions %}
          - "{{ python.version }}"
{% endfor %}

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: {% raw %}${{ matrix.python-version }}{% endraw %}

{% if package_manager == "uv" %}
      - name: Install uv
        uses: astral-sh/setup-uv@v5
{% endif %}

      - name: Install dependencies
        run: {{ install_command }}

{% if lint_command %}
      - name: Lint
        run: {{ lint_command }}
{% endif %}

      - name: Test
        run: {{ test_command }}
```

---

## Implementation Sketch

A simple internal implementation could look like this:

```python
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydantic_ai import Agent


class TemplateMetadata(BaseModel):
    name: str
    description: str
    schema: dict[str, str]
    prompt: dict[str, str]
    renderer: dict[str, str]
    strict_context: bool = True


class Template:
    root: Path
    metadata: TemplateMetadata
    schema_model: type[BaseModel]

    def load_prompt(self) -> str:
        return (self.root / self.metadata.prompt["file"]).read_text()

    def generate_model(
        self,
        model_name: str,
        user_request: str,
        context: dict[str, Any],
    ) -> BaseModel:
        prompt = self.load_prompt()

        agent = Agent(
            model_name,
            output_type=self.schema_model,
            instructions=prompt,
        )

        result = agent.run_sync(
            {
                "user_request": user_request,
                "context": context,
            }
        )

        return result.output

    def render(self, model: BaseModel) -> str:
        render_context = model.model_dump(mode="json")
        return render_minijinja(
            self.root / self.metadata.renderer["file"],
            render_context,
            strict=self.metadata.strict_context,
        )
```

The real implementation would need robust dynamic imports, sandboxing decisions, error handling, and renderer configuration.

---

## Error Handling

Templateer should make failures explicit.

Common errors:

### Template Not Found

```text
No template found with name: pyproject-uv
```

The agent may fall back to free generation or try a different template name.

### Model Validation Failed

```text
The LLM response did not satisfy PyprojectUvModel.

Validation errors:
- python_version: expected string like '3.12'
- dependencies.0.name: field required
```

Templateer may retry with validation errors.

### Render Failed

```text
Template referenced undefined variable: ruff.line_width
```

This likely indicates schema/template drift.

### Output Validation Failed

```text
Rendered artifact is not valid TOML.
```

This indicates either a template bug or insufficient escaping/filtering.

---

## Testing Strategy

Every template should be testable without an LLM.

A template test should provide a known model JSON input and expected output.

Example:

```text
examples/
  fastapi.input.json
  fastapi.output.toml
```

Test flow:

```text
load input JSON
validate against schema
render template
compare to expected output
run output validators
```

This tests:

- schema compatibility;
- template rendering;
- output syntax;
- formatting;
- fixture stability.

LLM behavior can be tested separately with snapshot or integration tests.

---

## Version 1 Scope

A practical first version of Templateer should include:

1. Folder-based template loading.
2. `metadata.yml` parsing.
3. Dynamic Pydantic schema loading.
4. Pydantic AI model instantiation.
5. Strict Jinja/MiniJinja rendering.
6. CLI commands:
   - `list`
   - `describe`
   - `schema`
   - `render`
   - `generate`
7. JSON-based context input.
8. Example-based template tests.
9. Optional output parser validation for common file types.

A first version does not need:

- persistent state;
- patch management;
- generated region ownership;
- template migrations;
- custom template file format;
- automatic project-wide orchestration;
- generalized refactoring;
- deep semantic validation.

---

## Future Extensions

Possible future additions:

### Template Registries

Allow templates to be packaged and distributed.

```text
templateer add registry-url
templateer install python/pyproject-uv
```

### Context Extractors

Support reusable extractors for project facts:

- ast-grep;
- file globs;
- TOML readers;
- package manager detection;
- import scanners;
- test framework detection.

### Multi-Artifact Generation

Generate several files from a shared high-level model.

Example:

```text
ProjectPlan
  → pyproject.toml
  → .github/workflows/ci.yml
  → Dockerfile
  → devcontainer.json
```

### Template Discovery Improvements

Enhance template browsing with filtering by output kind, trigger path, or
project facts. The agent always selects a template by exact name; discovery
tools help the agent find the right name.

### Output Validators

Add built-in validators for common artifact types.

### Editor/Agent Protocol

Expose Templateer templates as tools to coding agents.

Example capabilities:

```text
templateer.list
templateer.describe
templateer.generate_model
templateer.render
templateer.validate
```

### Partial Artifact Rendering

Support generating sections or fragments rather than full files.

This should remain secondary to the core full-artifact generation model.

---

## Design Summary

Templateer is a library and CLI for typed, constrained, agent-friendly artifact generation.

Its key design statement:

> Templateer does not ask LLMs to generate files. It asks LLMs to instantiate Pydantic models, then renders files deterministically from those models.

The result is a smaller, safer, more testable generation surface.

The essential flow is:

```text
intent + facts
  ↓
Pydantic AI structured output
  ↓
validated Pydantic model
  ↓
strict Jinja render context
  ↓
deterministic artifact
```

The most important rule:

```text
The Jinja template can only use values from the validated Pydantic model.
```

This rule is what distinguishes Templateer from ordinary prompt templates, code generators, and freeform LLM file generation.

Templateer’s value is not that it makes generated files perfect.

Its value is that it makes generation structured, inspectable, constrained, and composable.
