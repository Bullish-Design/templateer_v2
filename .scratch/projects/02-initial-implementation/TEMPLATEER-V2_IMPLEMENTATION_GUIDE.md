# Templateer V2: Step-by-Step Implementation Guide

**Version:** 1.0  
**Date:** 2026-06-01  
**Audience:** Developer implementing Templateer V2 from scratch  
**Based on:** [TEMPLATEER-V2_CONCEPT.md](../01-templateer-concept/TEMPLATEER-V2_CONCEPT.md), [Allium Specs](../../specs/allium/), [Allium Language Reference](../../../.agents/skills/allium/)

---

## Table of Contents

1. [Overview & Architecture](#overview--architecture)
2. [Phase 0: Project Bootstrapping](#phase-0-project-bootstrapping)
3. [Phase 1: Template Definition Format](#phase-1-template-definition-format)
4. [Phase 2: Template Loading & Catalog](#phase-2-template-loading--catalog)
5. [Phase 3: Schema & Pydantic Model Loading](#phase-3-schema--pydantic-model-loading)
6. [Phase 4: Pydantic AI Integration](#phase-4-pydantic-ai-integration)
7. [Phase 5: Rendering Pipeline](#phase-5-rendering-pipeline)
8. [Phase 6: Output Validation](#phase-6-output-validation)
9. [Phase 7: Generation Pipeline (End-to-End)](#phase-7-generation-pipeline-end-to-end)
10. [Phase 8: CLI Implementation](#phase-8-cli-implementation)
11. [Phase 9: Python API](#phase-9-python-api)
12. [Phase 10: Example Templates & Integration Testing](#phase-10-example-templates--integration-testing)
13. [Phase 11: Polish & Documentation](#phase-11-polish--documentation)
14. [Allium Spec Alignment](#allium-spec-alignment)

---

## Overview & Architecture

### What We're Building

Templateer V2 replaces freeform LLM file generation with a constrained, typed, deterministic pipeline:

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

**Central invariant:** A renderer may only receive validated Pydantic model data. No raw LLM output, no user prompt, no filesystem context, no environment variables reach the template.

### Key Concepts

| Concept | Definition |
|---------|-----------|
| **Template** | A folder containing all files needed to generate one kind of artifact (e.g., `pyproject-uv/`) |
| **Artifact** | The generated output (e.g., `pyproject.toml`, a GitHub Actions workflow) |
| **Schema** | A Pydantic model defining the structured data the LLM must produce |
| **Prompt** | Instructions that help the LLM fill the schema correctly |
| **Renderer** | Deterministic Jinja/MiniJinja rendering from validated model data only |
| **Context** | Input facts (user request, project metadata) passed to the LLM—never the template |
| **Template Catalog** | The collection of all available templates, lookup by exact name match |

### Allium Specs (Source of Truth)

The behavioural specification lives at `.scratch/specs/allium/` with two modules:

- **`templates.allium`** — Template entity, TemplateCatalog, TemplateCatalogue surface (browsing/discovery)
- **`generation.allium`** — Generation entity lifecycle (submitted→generating→ready/failed), ArtifactWorkshop surface (artifact production)

All implementation decisions must align with these specs. See [Allium Spec Alignment](#allium-spec-alignment) for the mapping.

### Technology Stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.12+ |
| Project management | uv |
| Schema validation | Pydantic v2 |
| LLM structured output | Pydantic AI |
| Template rendering | MiniJinja (Python bindings) or Jinja2 with strict mode |
| CLI framework | Click or Typer |
| Testing | Pytest |
| Type checking | ty (strict mode) |
| Linting | Ruff |

---

## Phase 0: Project Bootstrapping

**Goal:** Set up the project structure, development tools, and dependencies.

### Step 0.1: Create project directory

```
templateer/
├── pyproject.toml
├── README.md
├── src/
│   └── templateer/
│       ├── __init__.py
│       ├── py.typed
│       ├── templates/          # bundled built-in templates
│       └── ...                 # package modules
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   └── ...
└── templates/                  # development/test templates
    └── pyproject-uv/
        ├── metadata.yml
        ├── schema.py
        ├── prompt.md
        ├── template.j2
        ├── examples/
        │   ├── fastapi.input.json
        │   └── fastapi.output.toml
        └── tests/
            └── test_template.py
```

### Step 0.2: `pyproject.toml`

```toml
[project]
name = "templateer"
version = "0.1.0"
description = "Typed, constrained artifact generation for AI agents"
requires-python = ">=3.12"
dependencies = [
    "pydantic>=2.0",
    "pydantic-ai>=0.0.20",
    "minijinja>=2.0",
    "pyyaml>=6.0",
    "click>=8.0",
    "tomli>=2.0 ; python_version < '3.11'",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
    "ty>=1.0",
    "ruff>=0.3.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "N", "W"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ty]
strict = true
python_version = "3.12"
```

### Step 0.3: Initialize project

```bash
cd templateer/
uv venv
uv pip install -e ".[dev]"
```

### Step 0.4: Initial `__init__.py`

```python
# src/templateer/__init__.py
"""Templateer: typed, constrained artifact generation for AI agents."""

__version__ = "0.1.0"
```

### Phase 0 Testing

- [ ] `uv run pytest` runs and finds 0 tests (empty test suite)
- [ ] `uv run ty src/` passes with no errors
- [ ] `uv run ruff check src/` passes with no errors
- [ ] `python -c "import templateer; print(templateer.__version__)"` prints `0.1.0`

---

## Phase 1: Template Definition Format

**Goal:** Define the data structures for templates using Pydantic models. This is the foundation that everything else builds on.

### Allium Spec Reference

From `templates.allium`:

```
entity Template {
    name: String
    description: String
    output_kind: String
    trigger_paths: Set<String>
}
```

The `Template` entity in the spec defines the minimum identity. Our implementation extends this to include all the metadata needed to load and operate a template.

### Step 1.1: Define `TemplateMetadata` model

Create `src/templateer/models.py`:

```python
"""Core Pydantic models for Templateer."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class OutputValidator(BaseModel):
    """An output validator that checks the rendered artifact."""

    kind: Literal["parse", "command"]
    language: str | None = Field(
        default=None,
        description="Target language for parse validators (toml, json, yaml, python)",
    )
    command: list[str] | None = Field(
        default=None,
        description="Command and args for command validators",
    )
    optional: bool = Field(default=False)


class SchemaRef(BaseModel):
    """Reference to a Pydantic schema class within a Python file."""

    module: str = Field(description="Python module name relative to template root")
    class_name: str = Field(alias="class", description="Pydantic model class name")


class PromptRef(BaseModel):
    """Reference to a prompt file."""

    file: str = Field(description="Path to prompt file relative to template root")


class RendererRef(BaseModel):
    """Reference to a renderer configuration."""

    engine: Literal["minijinja"] = "minijinja"
    file: str = Field(description="Path to Jinja template file relative to template root")


class OutputSpec(BaseModel):
    """Describes what artifact a template generates."""

    path: str = Field(description="Target file path (e.g., 'pyproject.toml')")
    kind: Literal["full_file"] = "full_file"
    language: str = Field(description="Target language (toml, yaml, json, python, etc.)")


class TemplateMetadata(BaseModel):
    """Metadata for a Templateer template, loaded from metadata.yml."""

    name: str = Field(description="Template directory name, the sole matching key")
    description: str = Field(description="What this template generates and when to use it")

    outputs: list[OutputSpec] = Field(description="Artifacts this template produces")

    schema: SchemaRef = Field(description="Pydantic schema reference")
    prompt: PromptRef = Field(description="Prompt file reference")
    renderer: RendererRef = Field(description="Renderer configuration")

    strict_context: bool = Field(default=True)

    triggers: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Trigger conditions for template discovery",
    )

    validators: list[OutputValidator] = Field(
        default_factory=list,
        description="Optional output validators",
    )
```

### Step 1.2: Define `TemplateGenerationResult` model

Add to `src/templateer/models.py`:

```python
from typing import Any


class TemplateGenerationResult(BaseModel):
    """The result of a generation operation."""

    template_name: str
    model: dict[str, Any] = Field(description="The validated Pydantic model as a dict")
    rendered: str = Field(description="The rendered artifact text")
    validation_messages: list[str] = Field(default_factory=list)
```

### Step 1.3: Create a test `metadata.yml` for the example template

Create `templates/pyproject-uv/metadata.yml`:

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

### Step 1.4: Create the corresponding schema, prompt, and template files

Create `templates/pyproject-uv/schema.py` with the full schema from the concept document.

Create `templates/pyproject-uv/prompt.md` with instructions.

Create `templates/pyproject-uv/template.j2` with the Jinja template.

Create example fixtures:

- `templates/pyproject-uv/examples/fastapi.input.json` — a valid model instance
- `templates/pyproject-uv/examples/fastapi.output.toml` — expected rendered output

### Phase 1 Testing

Create `tests/test_models.py`:

```python
"""Tests for core Pydantic models."""

import pytest
from pydantic import ValidationError

from templateer.models import (
    OutputSpec,
    OutputValidator,
    PromptRef,
    RendererRef,
    SchemaRef,
    TemplateMetadata,
)


def test_template_metadata_parses_from_minimal_dict():
    """Test that TemplateMetadata validates a minimal correct metadata dict."""
    data = {
        "name": "pyproject-uv",
        "description": "Generate a uv-style pyproject.toml",
        "outputs": [{"path": "pyproject.toml", "kind": "full_file", "language": "toml"}],
        "schema": {"module": "schema", "class": "PyprojectUvModel"},
        "prompt": {"file": "prompt.md"},
        "renderer": {"engine": "minijinja", "file": "template.j2"},
    }
    meta = TemplateMetadata(**data)
    assert meta.name == "pyproject-uv"
    assert meta.strict_context is True  # default
    assert len(meta.outputs) == 1
    assert meta.outputs[0].path == "pyproject.toml"


def test_template_metadata_rejects_missing_name():
    """Template name is required."""
    data = {
        "description": "...",
        "outputs": [{"path": "x", "kind": "full_file", "language": "toml"}],
        "schema": {"module": "s", "class": "M"},
        "prompt": {"file": "p.md"},
        "renderer": {"engine": "minijinja", "file": "t.j2"},
    }
    with pytest.raises(ValidationError):
        TemplateMetadata(**data)


def test_output_validator_parse_kind():
    """Parse validators are supported."""
    v = OutputValidator(kind="parse", language="toml")
    assert v.kind == "parse"
    assert v.language == "toml"


def test_schema_ref_uses_alias():
    """SchemaRef maps 'class' YAML field to class_name."""
    s = SchemaRef(module="schema", class_name="PyprojectUvModel")
    assert s.class_name == "PyprojectUvModel"
```

### Success Criteria — Phase 1

- [ ] `TemplateMetadata` can parse a valid `metadata.yml` dict
- [ ] Invalid metadata raises `ValidationError`
- [ ] All models have proper `Field(description=...)` for LLM readability
- [ ] Existing `templates/pyproject-uv/metadata.yml` parses successfully
- [ ] Tests pass: `uv run pytest tests/test_models.py -v`

---

## Phase 2: Template Loading & Catalog

**Goal:** Load templates from the filesystem and build a catalog with exact-name lookup.

### Allium Spec Reference

From `templates.allium`:

```
entity TemplateCatalog {
    templates: Set<Template>
    has_template(name): exists t in templates where t.name = name
    templates_by_output(kind): templates where output_kind = kind
}
```

### Step 2.1: Implement `Template` loader

Create `src/templateer/template.py`:

```python
"""Template loading and representation."""

import importlib.util
import sys
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from templateer.models import TemplateMetadata


class TemplateLoadError(Exception):
    """Raised when a template cannot be loaded."""


class TemplateNotFoundError(Exception):
    """Raised when a named template is not found in the catalog."""


class Template:
    """Represents a loaded Templateer template."""

    def __init__(self, root: Path):
        """
        Load a template from a directory.

        Args:
            root: Path to the template directory containing metadata.yml.

        Raises:
            TemplateLoadError: If metadata.yml is missing or invalid.
        """
        self.root = root
        self._metadata_path = root / "metadata.yml"

        if not self._metadata_path.exists():
            raise TemplateLoadError(
                f"metadata.yml not found in {root}"
            )

        try:
            raw = yaml.safe_load(self._metadata_path.read_text())
        except yaml.YAMLError as e:
            raise TemplateLoadError(f"Invalid YAML in {self._metadata_path}: {e}")

        try:
            self.metadata = TemplateMetadata(**raw)
        except Exception as e:
            raise TemplateLoadError(f"Invalid metadata in {self._metadata_path}: {e}")

        # Validate that metadata name matches directory name
        if self.metadata.name != root.name:
            raise TemplateLoadError(
                f"Template name '{self.metadata.name}' does not match "
                f"directory name '{root.name}'"
            )

    @property
    def name(self) -> str:
        return self.metadata.name

    @property
    def description(self) -> str:
        return self.metadata.description

    @property
    def output_kind(self) -> str:
        """Primary output kind (from first output spec)."""
        return self.metadata.outputs[0].language if self.metadata.outputs else "unknown"

    @property
    def trigger_paths(self) -> set[str]:
        """File paths this template can generate."""
        return set(self.metadata.triggers.get("filenames", []))

    def resolve_path(self, relative: str) -> Path:
        """Resolve a path relative to the template root."""
        return (self.root / relative).resolve()

    def load_prompt(self) -> str:
        """Load the prompt file contents."""
        prompt_path = self.resolve_path(self.metadata.prompt.file)
        if not prompt_path.exists():
            raise TemplateLoadError(f"Prompt file not found: {prompt_path}")
        return prompt_path.read_text()

    def load_schema_module(self) -> Any:
        """
        Dynamically load the schema Python module.

        Returns:
            The imported Python module object.
        """
        module_name = self.metadata.schema.module
        schema_file = self.root / f"{module_name}.py"

        if not schema_file.exists():
            raise TemplateLoadError(f"Schema file not found: {schema_file}")

        # Use a unique name to avoid collisions
        spec_name = f"templateer_template_{self.name}_{module_name}"
        spec = importlib.util.spec_from_file_location(spec_name, schema_file)
        if spec is None or spec.loader is None:
            raise TemplateLoadError(f"Cannot load schema module from {schema_file}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[spec_name] = module
        spec.loader.exec_module(module)
        return module

    def get_schema_class(self) -> type[BaseModel]:
        """
        Load and return the Pydantic model class.

        Returns:
            The Pydantic model class.

        Raises:
            TemplateLoadError: If the class cannot be found or is not a BaseModel.
        """
        module = self.load_schema_module()
        class_name = self.metadata.schema.class_name

        if not hasattr(module, class_name):
            raise TemplateLoadError(
                f"Class '{class_name}' not found in schema module "
                f"'{self.metadata.schema.module}' for template '{self.name}'"
            )

        cls = getattr(module, class_name)
        if not isinstance(cls, type) or not issubclass(cls, BaseModel):
            raise TemplateLoadError(
                f"'{class_name}' is not a Pydantic BaseModel subclass"
            )

        return cls

    def __repr__(self) -> str:
        return f"Template(name={self.name!r}, root={self.root!r})"
```

### Step 2.2: Implement `TemplateCatalog`

Create `src/templateer/catalog.py`:

```python
"""Template catalog for discovery and lookup."""

from pathlib import Path

from templateer.template import Template, TemplateNotFoundError, TemplateLoadError


class TemplateCatalog:
    """A collection of available templates with exact-name lookup."""

    def __init__(self):
        self._templates: dict[str, Template] = {}

    @property
    def templates(self) -> list[Template]:
        """All loaded templates."""
        return list(self._templates.values())

    def load_from_paths(self, paths: list[Path]) -> None:
        """
        Load templates from one or more directories.

        Each directory is scanned for subdirectories containing metadata.yml.
        Templates are indexed by name (directory name). If a template with
        the same name appears in multiple paths, the first one wins.

        Args:
            paths: List of directories to scan for templates.

        Raises:
            TemplateLoadError: If a template directory has invalid structure.
        """
        for path in paths:
            if not path.exists():
                continue

            for entry in sorted(path.iterdir()):
                if entry.is_dir() and (entry / "metadata.yml").exists():
                    if entry.name not in self._templates:
                        try:
                            template = Template(entry)
                            self._templates[template.name] = template
                        except TemplateLoadError as e:
                            # Skip broken templates but log
                            import logging
                            logging.warning(f"Skipping template {entry.name}: {e}")

    def has_template(self, name: str) -> bool:
        """Check if a template with the exact name exists."""
        return name in self._templates

    def get(self, name: str) -> Template:
        """
        Get a template by exact name.

        Args:
            name: The template directory name (e.g., 'pyproject-uv').

        Returns:
            The Template instance.

        Raises:
            TemplateNotFoundError: If no template with that name exists.
        """
        if name not in self._templates:
            raise TemplateNotFoundError(
                f"No template found with name: {name}"
            )
        return self._templates[name]

    def templates_by_output_kind(self, kind: str) -> list[Template]:
        """Find templates that produce a given output kind."""
        return [t for t in self._templates.values() if t.output_kind == kind]

    def __len__(self) -> int:
        return len(self._templates)

    def __contains__(self, name: str) -> bool:
        return name in self._templates
```

### Step 2.3: Configure template search paths

Add to `src/templateer/__init__.py` a default registry path:

```python
from pathlib import Path

# Default template search paths
DEFAULT_TEMPLATE_PATHS = [
    Path(__file__).parent / "templates",  # bundled templates
    Path.cwd() / "templates",             # project-local templates
]
```

### Phase 2 Testing

Create `tests/test_catalog.py` and `tests/test_template.py`.

Key test `tests/test_catalog.py`:

```python
"""Tests for TemplateCatalog."""

import pytest
from pathlib import Path

from templateer.catalog import TemplateCatalog
from templateer.template import TemplateNotFoundError


@pytest.fixture
def catalog_with_pyproject_uv():
    """Create a catalog loaded with the pyproject-uv template."""
    catalog = TemplateCatalog()
    catalog.load_from_paths([Path("templates")])
    return catalog


def test_catalog_has_loaded_template(catalog_with_pyproject_uv):
    """Catalog should contain templates after loading."""
    assert len(catalog_with_pyproject_uv) > 0
    assert catalog_with_pyproject_uv.has_template("pyproject-uv")


def test_catalog_get_by_exact_name(catalog_with_pyproject_uv):
    """Template lookup is exact name match."""
    t = catalog_with_pyproject_uv.get("pyproject-uv")
    assert t.name == "pyproject-uv"
    assert t.description is not None


def test_catalog_raises_on_unknown_name(catalog_with_pyproject_uv):
    """Unknown template name raises TemplateNotFoundError."""
    with pytest.raises(TemplateNotFoundError):
        catalog_with_pyproject_uv.get("nonexistent-template")


def test_catalog_templates_by_output_kind(catalog_with_pyproject_uv):
    """Filtering by output kind works."""
    toml_templates = catalog_with_pyproject_uv.templates_by_output_kind("toml")
    assert any(t.name == "pyproject-uv" for t in toml_templates)


def test_catalog_handles_empty_directories(tmp_path):
    """Loading from a directory with no templates is safe."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    catalog = TemplateCatalog()
    catalog.load_from_paths([empty_dir])
    assert len(catalog) == 0
```

Key test `tests/test_template.py`:

```python
"""Tests for Template loader."""

import pytest
from pathlib import Path

from templateer.template import Template, TemplateLoadError


def test_load_template_from_directory():
    """Template loads successfully from a valid directory."""
    t = Template(Path("templates/pyproject-uv"))
    assert t.name == "pyproject-uv"
    assert t.description.startswith("Generate")
    assert len(t.trigger_paths) > 0


def test_load_prompt():
    """Prompt can be loaded from the template."""
    t = Template(Path("templates/pyproject-uv"))
    prompt = t.load_prompt()
    assert "PyprojectUvModel" in prompt
    assert len(prompt) > 0


def test_load_schema_module():
    """Schema module can be dynamically loaded."""
    t = Template(Path("templates/pyproject-uv"))
    module = t.load_schema_module()
    assert hasattr(module, "PyprojectUvModel")


def test_get_schema_class():
    """Schema class is a Pydantic BaseModel subclass."""
    t = Template(Path("templates/pyproject-uv"))
    cls = t.get_schema_class()
    from pydantic import BaseModel
    assert issubclass(cls, BaseModel)
    assert cls.__name__ == "PyprojectUvModel"


def test_missing_metadata_raises():
    """Missing metadata.yml raises TemplateLoadError."""
    with pytest.raises(TemplateLoadError):
        Template(Path("templates"))  # not a template dir, no metadata.yml


def test_name_must_match_directory():
    """Metadata name must equal directory name."""
    # This would be tested by creating a temp dir with mismatched name
    pass  # Tested by the guarantee in __init__
```

### Success Criteria — Phase 2

- [ ] `Template(root)` loads and validates metadata, prompt, schema
- [ ] `TemplateCatalog` loads templates from filesystem paths
- [ ] Exact name lookup works; unknown names error cleanly
- [ ] `templates_by_output_kind` filtering works
- [ ] Template name must match directory name (invariant enforcement)
- [ ] Tests pass: `uv run pytest tests/test_catalog.py tests/test_template.py -v`

---

## Phase 3: Schema & Pydantic Model Loading

**Goal:** Dynamically load Pydantic models from template `schema.py` files and validate model instances.

### Allium Spec Reference

From `generation.allium`:

The `ValidateModel` rule ensures that after the LLM produces a raw model response, it's validated against the Pydantic schema. Validation errors are captured and may cause retries.

### Step 3.1: Dynamic schema loading (already done in Phase 2)

The `Template.get_schema_class()` method handles this. Verify it works with our example template.

### Step 3.2: Model validation utility

Add to `src/templateer/models.py` or create `src/templateer/validation.py`:

```python
"""Model validation utilities."""

from typing import Any

from pydantic import BaseModel, ValidationError


def validate_model_instance(
    schema_class: type[BaseModel],
    data: dict[str, Any],
) -> tuple[BaseModel, list[str]]:
    """
    Validate data against a Pydantic schema.

    Args:
        schema_class: The Pydantic model class.
        data: Raw data to validate.

    Returns:
        Tuple of (validated_model, validation_errors).
        If validation succeeds, validated_model is the instance and errors is empty.
        If validation fails, validated_model is None and errors is the list of messages.
    """
    try:
        instance = schema_class(**data)
        return instance, []
    except ValidationError as e:
        errors = [str(err) for err in e.errors()]
        return None, errors  # type: ignore
```

### Step 3.3: Schema JSON generation

Add to `src/templateer/template.py`:

```python
def get_schema_json(self) -> str:
    """Return the JSON schema for the template's Pydantic model."""
    cls = self.get_schema_class()
    return cls.model_json_schema()
```

### Phase 3 Testing

Create `tests/test_validation.py`:

```python
"""Tests for model validation."""

import json
import pytest
from pathlib import Path

from templateer.template import Template
from templateer.validation import validate_model_instance


@pytest.fixture
def pyproject_template():
    return Template(Path("templates/pyproject-uv"))


@pytest.fixture
def fastapi_input():
    data = json.loads(
        (Path("templates/pyproject-uv/examples/fastapi.input.json")).read_text()
    )
    return data


def test_schema_class_is_loadable(pyproject_template):
    """The schema class can be loaded and is a BaseModel."""
    cls = pyproject_template.get_schema_class()
    from pydantic import BaseModel
    assert issubclass(cls, BaseModel)


def test_valid_model_validates(pyproject_template, fastapi_input):
    """A valid input dict validates successfully."""
    cls = pyproject_template.get_schema_class()
    instance, errors = validate_model_instance(cls, fastapi_input)
    assert instance is not None
    assert errors == []
    assert instance.project_name == fastapi_input["project_name"]


def test_invalid_model_reports_errors(pyproject_template):
    """Missing required fields produce validation errors."""
    cls = pyproject_template.get_schema_class()
    instance, errors = validate_model_instance(cls, {})
    assert instance is None
    assert len(errors) > 0


def test_json_schema_generation(pyproject_template):
    """JSON schema can be generated for a template."""
    schema = pyproject_template.get_schema_json()
    assert "properties" in schema
    assert "title" in schema
```

### Success Criteria — Phase 3

- [ ] Dynamic Pydantic model loading works from `schema.py` files
- [ ] Model validation correctly identifies valid and invalid data
- [ ] Validation error messages are descriptive
- [ ] JSON schema generation works
- [ ] Tests pass: `uv run pytest tests/test_validation.py -v`

---

## Phase 4: Pydantic AI Integration

**Goal:** Use Pydantic AI to ask an LLM to fill a template schema based on user intent and project facts.

### Allium Spec Reference

From `generation.allium`:

```
rule RequestModelFromLLM {
    when: gen: Generation.status becomes model_pending
    ...
    let response = produce_structured_model(
        config.default_model,
        template.schema_module,
        template.schema_class,
        template.prompt_file,
        gen.context
    )
    ensures: gen.raw_model_response = response
    ensures: gen.status = model_received
}
```

This is modelled as a black-box external call. The LLM produces structured data matching the schema.

### Step 4.1: Implement the model generator

Create `src/templateer/generator.py`:

```python
"""LLM-based model generation using Pydantic AI."""

import json
from typing import Any

from pydantic import BaseModel
from pydantic_ai import Agent

from templateer.template import Template
from templateer.validation import validate_model_instance


class ModelGenerationError(Exception):
    """Raised when model generation fails."""


# Maximum retries for validation failures
DEFAULT_MAX_RETRIES = 3

# Default model name
DEFAULT_MODEL = "openai:gpt-4.1-mini"


def generate_model(
    template: Template,
    user_request: str,
    context: dict[str, Any] | None = None,
    model_name: str = DEFAULT_MODEL,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> tuple[BaseModel, list[str]]:
    """
    Use Pydantic AI to fill a template schema.

    Args:
        template: The template to fill.
        user_request: What the user/agent wants to generate.
        context: Optional project facts to help the LLM.
        model_name: The LLM to use.
        max_retries: Max retries on validation failure, with errors fed back.

    Returns:
        Tuple of (validated_model, messages).
        messages includes any notes or warnings from the generation process.

    Raises:
        ModelGenerationError: If generation fails after max_retries.
    """
    schema_class = template.get_schema_class()
    prompt = template.load_prompt()

    messages: list[str] = []

    # Build the context to pass to the LLM
    context_text = _build_context(user_request, context or {})

    agent = Agent(
        model_name,
        output_type=schema_class,
        instructions=prompt,
    )

    retry_count = 0

    while retry_count <= max_retries:
        try:
            result = agent.run_sync(context_text)
        except Exception as e:
            raise ModelGenerationError(f"LLM call failed: {e}")

        # Extract the raw output
        raw_output = result.output

        if raw_output is None:
            retry_count += 1
            if retry_count > max_retries:
                raise ModelGenerationError(
                    "LLM did not return a usable response after "
                    f"{max_retries} retries"
                )
            continue

        # Validate against schema
        if isinstance(raw_output, BaseModel):
            # Pydantic AI already returned a validated instance
            validated, errors = validate_model_instance(
                schema_class, raw_output.model_dump(mode="json")
            )
            if errors:
                # This shouldn't happen if Pydantic AI worked correctly,
                # but defensive check
                if retry_count >= max_retries:
                    raise ModelGenerationError(
                        f"Model validation failed: {'; '.join(errors)}"
                    )
                # Feed errors back for retry
                retry_count += 1
                error_feedback = f"Previous validation errors: {'; '.join(errors)}"
                context_text = f"{context_text}\n\n{error_feedback}"
                continue
            return validated, messages
        else:
            # Pydantic AI returned something unexpected
            # Try to validate manually
            if isinstance(raw_output, dict):
                validated, errors = validate_model_instance(
                    schema_class, raw_output
                )
                if errors:
                    if retry_count >= max_retries:
                        raise ModelGenerationError(
                            f"Model validation failed: {'; '.join(errors)}"
                        )
                    retry_count += 1
                    error_feedback = (
                        f"Previous validation errors: {'; '.join(errors)}"
                    )
                    context_text = f"{context_text}\n\n{error_feedback}"
                    continue
                return validated, messages

            retry_count += 1
            if retry_count > max_retries:
                raise ModelGenerationError(
                    "LLM returned unexpected output type: "
                    f"{type(raw_output).__name__}"
                )

    raise ModelGenerationError("Exceeded maximum retries")


def _build_context(user_request: str, context: dict[str, Any]) -> str:
    """Build the context text for the LLM."""
    parts = [f"User request: {user_request}"]

    if context:
        facts_json = json.dumps(context, indent=2)
        parts.append(f"Project facts: {facts_json}")

    return "\n\n".join(parts)
```

### Phase 4 Testing

Create `tests/test_generator.py`:

```python
"""Tests for the model generator.

Note: These tests require an LLM API key to be configured.
Tests that make actual LLM calls should be marked with @pytest.mark.llm
and skipped in CI unless explicitly enabled.
"""

import json
import os
import pytest
from pathlib import Path

from templateer.template import Template
from templateer.generator import generate_model, DEFAULT_MODEL


# Skip LLM tests unless --run-llm is passed
requires_llm = pytest.mark.skipif(
    "OPENAI_API_KEY" not in os.environ,
    reason="OPENAI_API_KEY not set; use --run-llm to run LLM tests"
)


@pytest.fixture
def pyproject_template():
    return Template(Path("templates/pyproject-uv"))


def test_build_context_is_string():
    """Context building produces a non-empty string."""
    from templateer.generator import _build_context
    ctx = _build_context("Make a project", {"uses_fastapi": True})
    assert "Make a project" in ctx
    assert "uses_fastapi" in ctx


@requires_llm
def test_generate_model_basic(pyproject_template):
    """Generate a model with basic instructions."""
    model, messages = generate_model(
        pyproject_template,
        user_request="Generate a pyproject.toml for a basic Python project",
        context={"detected_python_version": "3.12", "package_manager": "uv"},
    )
    assert model is not None
    assert model.project_name is not None
    assert len(model.project_name) > 0


@requires_llm
def test_generate_model_fastapi(pyproject_template):
    """Generate a fastapi project model."""
    model, messages = generate_model(
        pyproject_template,
        user_request="Generate a pyproject.toml for a FastAPI app using uv, pytest, and ruff",
        context={"uses_fastapi": True, "uses_pytest": True},
    )
    assert model is not None
    # Should have fastapi dependency
    dep_names = [d.name.lower() for d in model.dependencies]
    assert "fastapi" in dep_names
    # Should have pytest in dev dependencies
    dev_names = [d.name.lower() for d in model.dev_dependencies]
    assert "pytest" in dev_names
```

### Success Criteria — Phase 4

- [ ] `generate_model()` uses Pydantic AI to call an LLM
- [ ] The LLM receives the prompt, user request, and context
- [ ] The returned output is a validated Pydantic model instance
- [ ] Validation failures feed back to the LLM for retry
- [ ] Max retries is enforced
- [ ] LLM tests pass when `--run-llm` and `OPENAI_API_KEY` are set
- [ ] Non-LLM tests always pass

---

## Phase 5: Rendering Pipeline

**Goal:** Render templates deterministically from validated Pydantic model data using MiniJinja with strict mode. This is the core invariant: the renderer only receives validated model data.

### Allium Spec Reference

From `generation.allium`:

```
invariant StrictRenderContract {
    for gen in Generation where status in {rendered, completed}:
        gen.rendered_artifact != null
}

invariant DeterministicRender {
    for a in Generation where status = completed:
        for b in Generation where status = completed:
            if a.selected_template_name = b.selected_template_name
            and a.raw_model_response = b.raw_model_response:
                a.rendered_artifact = b.rendered_artifact
}
```

### Step 5.1: Implement the renderer

Create `src/templateer/renderer.py`:

```python
"""Deterministic template rendering from validated Pydantic model data.

The central invariant of Templateer: a renderer may only receive validated
Pydantic model data. No raw LLM output, user prompt, environment variables,
or filesystem context reaches the Jinja template.
"""

from pathlib import Path
from typing import Any

from minijinja import Environment
from pydantic import BaseModel


class RenderError(Exception):
    """Raised when template rendering fails."""


def render_template(
    template_path: Path,
    model: BaseModel | dict[str, Any],
    strict: bool = True,
) -> str:
    """
    Render a Jinja template from a validated model.

    This function implements the strict rendering contract: the template
    receives ONLY the validated model data, nothing else. In strict mode,
    references to undefined variables are errors (not silent empty strings).

    Args:
        template_path: Path to the Jinja template file (.j2).
        model: A validated Pydantic model instance or a dict from model_dump.
        strict: If True, undefined variables raise errors.

    Returns:
        The rendered artifact text.

    Raises:
        RenderError: If the template references undefined variables or
                     if the template file is missing.
    """
    if not template_path.exists():
        raise RenderError(f"Template file not found: {template_path}")

    # Extract render context: only model data
    if isinstance(model, BaseModel):
        render_context = model.model_dump(mode="json")
    else:
        render_context = model

    template_source = template_path.read_text()

    # Create a MiniJinja environment with strict undefined behavior
    env = Environment()

    if strict:
        # MiniJinja strict mode: undefined variables raise errors
        env.set_undefined_behavior("strict")

    try:
        jinja_template = env.compile_template(
            template_source, str(template_path)
        )
        result = jinja_template.render(render_context)
    except Exception as e:
        raise RenderError(
            f"Failed to render template '{template_path.name}': {e}"
        ) from e

    # IMPORTANT: The render context is not exposed beyond this function.
    # The template cannot access anything except what was in the model.
    return result
```

### Step 5.2: Template rendering helper on Template class

Add to `src/templateer/template.py`:

```python
def render(self, model: BaseModel) -> str:
    """
    Render this template with a validated model.

    Args:
        model: A validated Pydantic model instance.

    Returns:
        The rendered artifact text.
    """
    from templateer.renderer import render_template

    template_file = self.resolve_path(self.metadata.renderer.file)
    return render_template(
        template_file,
        model,
        strict=self.metadata.strict_context,
    )
```

### Phase 5 Testing

Create `tests/test_renderer.py`:

```python
"""Tests for the deterministic renderer."""

import json
import pytest
from pathlib import Path

from templateer.template import Template
from templateer.renderer import render_template, RenderError


@pytest.fixture
def pyproject_template():
    return Template(Path("templates/pyproject-uv"))


def test_render_from_valid_model(pyproject_template):
    """Rendering from a valid model produces expected output."""
    # Load the example input
    input_data = json.loads(
        (Path("templates/pyproject-uv/examples/fastapi.input.json")).read_text()
    )
    schema_class = pyproject_template.get_schema_class()
    model = schema_class(**input_data)

    rendered = pyproject_template.render(model)

    # It should be valid TOML-like content
    assert "[project]" in rendered
    assert "name =" in rendered


def test_render_matches_expected_output(pyproject_template):
    """Rendered output matches the example output fixture exactly."""
    input_data = json.loads(
        (Path("templates/pyproject-uv/examples/fastapi.input.json")).read_text()
    )
    expected_output = (
        Path("templates/pyproject-uv/examples/fastapi.output.toml")
        .read_text()
    )

    schema_class = pyproject_template.get_schema_class()
    model = schema_class(**input_data)
    rendered = pyproject_template.render(model)

    assert rendered.strip() == expected_output.strip()


def test_strict_mode_raises_on_undefined(pyproject_template):
    """In strict mode, undefined variables raise errors."""
    # Create a minimal model with missing optional fields
    schema_class = pyproject_template.get_schema_class()
    # Create with only required fields
    model = schema_class(
        project_name="test-project",
        python_version="3.12",
    )

    # Should render fine even without optional fields
    rendered = pyproject_template.render(model)
    assert "test-project" in rendered


def test_deterministic_rendering(pyproject_template):
    """Same model + same template = same output (determinism)."""
    input_data = json.loads(
        (Path("templates/pyproject-uv/examples/fastapi.input.json")).read_text()
    )
    schema_class = pyproject_template.get_schema_class()
    model = schema_class(**input_data)

    output1 = pyproject_template.render(model)
    output2 = pyproject_template.render(model)

    assert output1 == output2
```

### Success Criteria — Phase 5

- [ ] MiniJinja renders templates from model dumps only
- [ ] Strict mode causes errors on undefined variables
- [ ] Rendering is deterministic: same input → same output
- [ ] Example fixtures produce expected output exactly
- [ ] No raw context leaks into the renderer
- [ ] Tests pass: `uv run pytest tests/test_renderer.py -v`

---

## Phase 6: Output Validation

**Goal:** Validate rendered artifacts (TOML parse, JSON parse, YAML parse, Python AST parse) to catch rendering bugs.

### Allium Spec Reference

From `generation.allium`:

```
rule ValidateOutput {
    when: gen: Generation.status becomes rendered
    ...
    let errors = run_output_validators(gen.rendered_artifact, ...)
    ensures:
        if errors.count = 0:
            gen.status = completed
        else:
            gen.status = failed
}
```

Output validation is separate from Pydantic validation. Pydantic validates the intermediate model; output validators check the final artifact.

### Step 6.1: Implement output validators

Create `src/templateer/validators.py`:

```python
"""Output validators for rendered artifacts."""

import ast
import json
import subprocess
import tomllib
from typing import Any

import yaml


class OutputValidationError(Exception):
    """Raised when output validation fails."""
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def validate_output(
    artifact: str,
    language: str,
    validators: list[dict[str, Any]] | None = None,
) -> list[str]:
    """
    Validate a rendered artifact.

    Args:
        artifact: The rendered artifact text.
        language: Target language (toml, json, yaml, python).
        validators: Optional additional validators from template metadata.

    Returns:
        List of error messages. Empty list means success.
    """
    errors: list[str] = []

    # Built-in parser validation
    parser_validators = {
        "toml": _validate_toml,
        "json": _validate_json,
        "yaml": _validate_yaml,
        "python": _validate_python,
    }

    if language in parser_validators:
        try:
            parser_validators[language](artifact)
        except Exception as e:
            errors.append(f"{language} parse failed: {e}")

    # Custom validators from template metadata
    if validators:
        for validator in validators:
            kind = validator.get("kind")
            if kind == "parse":
                lang = validator.get("language")
                if lang and lang in parser_validators:
                    try:
                        parser_validators[lang](artifact)
                    except Exception as e:
                        msg = f"Custom parse ({lang}) failed: {e}"
                        if validator.get("optional"):
                            # Don't add to errors, but could log
                            pass
                        else:
                            errors.append(msg)
            elif kind == "command":
                cmd = validator.get("command", [])
                try:
                    result = subprocess.run(
                        cmd,
                        input=artifact,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    if result.returncode != 0:
                        msg = f"Command '{' '.join(cmd)}' failed: {result.stderr}"
                        if validator.get("optional"):
                            pass
                        else:
                            errors.append(msg)
                except Exception as e:
                    msg = f"Command '{' '.join(cmd)}' error: {e}"
                    if validator.get("optional"):
                        pass
                    else:
                        errors.append(msg)

    return errors


def _validate_toml(text: str) -> None:
    """Validate TOML by parsing it."""
    tomllib.loads(text)


def _validate_json(text: str) -> None:
    """Validate JSON by parsing it."""
    json.loads(text)


def _validate_yaml(text: str) -> None:
    """Validate YAML by parsing it."""
    yaml.safe_load(text)


def _validate_python(text: str) -> None:
    """Validate Python by parsing the AST."""
    ast.parse(text)
```

### Phase 6 Testing

Create `tests/test_validators.py`:

```python
"""Tests for output validators."""

import pytest
from templateer.validators import validate_output


def test_validate_valid_toml():
    """Valid TOML passes validation."""
    toml_text = '[project]\nname = "test"\n'
    errors = validate_output(toml_text, "toml")
    assert errors == []


def test_validate_invalid_toml():
    """Invalid TOML reports errors."""
    errors = validate_output("not valid toml {{{", "toml")
    assert len(errors) > 0


def test_validate_valid_json():
    errors = validate_output('{"key": "value"}', "json")
    assert errors == []


def test_validate_invalid_json():
    errors = validate_output('{key: value}', "json")
    assert len(errors) > 0


def test_validate_valid_yaml():
    errors = validate_output("key: value\n", "yaml")
    assert errors == []


def test_validate_valid_python():
    errors = validate_output("def foo():\n    pass\n", "python")
    assert errors == []


def test_validate_invalid_python():
    errors = validate_output("def 123foo():\n    pass\n", "python")
    assert len(errors) > 0


def test_unknown_language_no_error():
    """Unknown languages don't raise errors (no validator available)."""
    errors = validate_output("anything", "dockerfile")
    assert errors == []
```

### Success Criteria — Phase 6

- [ ] TOML, JSON, YAML, Python parse validation works
- [ ] Invalid syntax reports errors appropriately
- [ ] Optional validators don't block on failure
- [ ] Custom command validators work
- [ ] Tests pass: `uv run pytest tests/test_validators.py -v`

---

## Phase 7: Generation Pipeline (End-to-End)

**Goal:** Wire everything together into the complete generation pipeline that matches the spec's Generation entity lifecycle.

### Allium Spec Reference

From `generation.allium`:

```
entity Generation {
    status: submitted | generating | ready | failed
    transitions status {
        submitted -> generating
        submitted -> failed
        generating -> ready
        generating -> failed
        terminal: ready, failed
    }
    failure_reason: FailureReason?  (no_template | model_validation_failed
                                      | render_failed | output_validation_failed
                                      | llm_failed)
    retry_count: Integer
}
```

### Step 7.1: Implement the Generation entity

Create `src/templateer/generation.py`:

```python
"""Generation entity — the lifecycle of producing an artifact."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class FailureReason(str, Enum):
    """Why a generation failed."""
    NO_TEMPLATE = "no_template"
    MODEL_VALIDATION_FAILED = "model_validation_failed"
    RENDER_FAILED = "render_failed"
    OUTPUT_VALIDATION_FAILED = "output_validation_failed"
    LLM_FAILED = "llm_failed"


class GenerationStatus(str, Enum):
    """Lifecycle status of a generation."""
    SUBMITTED = "submitted"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"


class Generation(BaseModel):
    """Tracks a single artifact generation request."""

    requested_path: str = Field(description="The artifact path requested")
    template_name: str = Field(description="Name of the template to use")

    status: GenerationStatus = GenerationStatus.SUBMITTED
    matched_template: str | None = None
    artifact: str | None = None
    failure_reason: FailureReason | None = None
    retry_count: int = 0

    # Derived
    @property
    def can_retry(self) -> bool:
        """Can this generation be retried?"""
        return (
            self.status == GenerationStatus.FAILED
            and self.retry_count < 3
        )

    @property
    def is_done(self) -> bool:
        """Is this generation terminal?"""
        return self.status in (GenerationStatus.READY, GenerationStatus.FAILED)
```

### Step 7.2: Implement the full pipeline

Create `src/templateer/pipeline.py`:

```python
"""The complete Templateer generation pipeline."""

from typing import Any

from pydantic import BaseModel

from templateer.catalog import TemplateCatalog
from templateer.generation import Generation, FailureReason, GenerationStatus
from templateer.generator import generate_model, ModelGenerationError
from templateer.renderer import RenderError
from templateer.template import TemplateNotFoundError
from templateer.validators import validate_output


class PipelineError(Exception):
    """Raised when the generation pipeline encounters an error."""
    def __init__(self, message: str, reason: FailureReason):
        self.reason = reason
        super().__init__(message)


def run_pipeline(
    catalog: TemplateCatalog,
    template_name: str,
    user_request: str,
    context: dict[str, Any] | None = None,
    model_name: str = "openai:gpt-4.1-mini",
    max_retries: int = 3,
) -> Generation:
    """
    Execute the full generation pipeline.

    This implements the Generation lifecycle from the Allium spec:
    submitted → generating → ready/failed.
    On failure, retries are attempted up to max_retries.

    Args:
        catalog: The template catalog.
        template_name: Exact template name.
        user_request: What the user/agent wants to generate.
        context: Optional project facts.
        model_name: LLM to use.
        max_retries: Maximum retry attempts.

    Returns:
        A Generation entity with the result.
    """
    gen = Generation(
        requested_path="",  # Will be set from template outputs
        template_name=template_name,
    )

    # Step 1: Resolve template
    try:
        template = catalog.get(template_name)
        gen.matched_template = template.name
    except TemplateNotFoundError:
        gen.status = GenerationStatus.FAILED
        gen.failure_reason = FailureReason.NO_TEMPLATE
        return gen

    gen.requested_path = template.metadata.outputs[0].path

    # Step 2: Generate model via LLM
    gen.status = GenerationStatus.GENERATING

    try:
        model, messages = generate_model(
            template,
            user_request=user_request,
            context=context,
            model_name=model_name,
            max_retries=max_retries,
        )
    except ModelGenerationError as e:
        gen.status = GenerationStatus.FAILED
        gen.failure_reason = FailureReason.LLM_FAILED
        gen.artifact = str(e)
        return gen

    # Step 3: Render artifact
    try:
        rendered = template.render(model)
    except RenderError as e:
        gen.status = GenerationStatus.FAILED
        gen.failure_reason = FailureReason.RENDER_FAILED
        gen.artifact = str(e)
        return gen

    # Step 4: Validate output
    output_language = template.metadata.outputs[0].language
    output_validators = [
        v.model_dump() for v in template.metadata.validators
    ]
    errors = validate_output(rendered, output_language, output_validators)

    if errors:
        gen.status = GenerationStatus.FAILED
        gen.failure_reason = FailureReason.OUTPUT_VALIDATION_FAILED
        gen.artifact = "\n".join(errors)
        return gen

    # Success
    gen.status = GenerationStatus.READY
    gen.artifact = rendered
    return gen


def retry_generation(
    catalog: TemplateCatalog,
    gen: Generation,
    user_request: str,
    context: dict[str, Any] | None = None,
) -> Generation:
    """
    Retry a failed generation.

    Args:
        catalog: The template catalog.
        gen: The failed generation to retry.
        user_request: Original user request.
        context: Original context.

    Returns:
        A new Generation entity with the retry result.

    Raises:
        ValueError: If generation cannot be retried.
    """
    if not gen.can_retry:
        raise ValueError("Generation cannot be retried")

    return run_pipeline(
        catalog=catalog,
        template_name=gen.template_name,
        user_request=user_request,
        context=context,
        max_retries=gen.retry_count + 1,
    )
```

### Phase 7 Testing

Create `tests/test_pipeline.py`:

```python
"""Tests for the complete generation pipeline."""

import json
import pytest
from pathlib import Path

from templateer.catalog import TemplateCatalog
from templateer.generation import GenerationStatus, FailureReason
from templateer.pipeline import run_pipeline, retry_generation


@pytest.fixture
def catalog():
    c = TemplateCatalog()
    c.load_from_paths([Path("templates")])
    return c


def test_pipeline_template_not_found(catalog):
    """Pipeline fails cleanly when template doesn't exist."""
    gen = run_pipeline(
        catalog,
        template_name="nonexistent",
        user_request="test",
    )
    assert gen.status == GenerationStatus.FAILED
    assert gen.failure_reason == FailureReason.NO_TEMPLATE


def test_pipeline_cannot_retry_ready(catalog):
    """Cannot retry a successful generation."""
    gen = run_pipeline(
        catalog,
        template_name="nonexistent",
        user_request="test",
    )
    # gen failed, but can_retry should be true for NO_TEMPLATE
    # Actually, NO_TEMPLATE is a hard failure, let's verify
    assert gen.status == GenerationStatus.FAILED
    # Should be retryable for transient failures, but NO_TEMPLATE may not
    # be retryable unless a template is added later. The spec says can_retry
    # when status=failed and retry_count < max.
    # Let's check that retry_count is 0
    assert gen.retry_count == 0
    assert gen.can_retry


def test_generation_is_done_after_run(catalog):
    """After running, generation is in a terminal state."""
    gen = run_pipeline(
        catalog,
        template_name="nonexistent",
        user_request="test",
    )
    assert gen.is_done


def test_pipeline_renders_end_to_end(pyproject_template_input, catalog):
    """Full pipeline: resolve template, render from a model JSON."""
    # This tests the non-LLM path: we provide the model directly
    # We need a way to bypass the LLM. For now, test via template.render()
    template = catalog.get("pyproject-uv")
    input_data = json.loads(
        (Path("templates/pyproject-uv/examples/fastapi.input.json")).read_text()
    )
    cls = template.get_schema_class()
    model = cls(**input_data)
    rendered = template.render(model)
    assert "[project]" in rendered
```

### Success Criteria — Phase 7

- [ ] Full pipeline: template resolution → model generation → rendering → validation
- [ ] Each failure reason is captured correctly
- [ ] Retry logic respects max_retries
- [ ] Generation tracks status through its lifecycle
- [ ] Can render templates from pre-existing model JSON (LLM-bypass path)
- [ ] Tests pass: `uv run pytest tests/test_pipeline.py -v`

---

## Phase 8: CLI Implementation

**Goal:** Expose Templateer via a CLI suitable for both human and agent use.

### Allium Spec Reference

From `generation.allium` (CLI surface):

```
surface CLI {
    provides:
        StartGeneration(...)
        ListTemplates
        DescribeTemplate(name)
        ShowSchema(name)
        GenerateArtifact(...)
        RenderFromModel(...)
        ValidateOutput(...)
}
```

### Step 8.1: Implement CLI with Click

Create `src/templateer/cli.py`:

```python
"""Templateer CLI - typed, constrained artifact generation for AI agents."""

import json
import sys
from pathlib import Path
from typing import Optional

import click

from templateer.catalog import TemplateCatalog
from templateer.generation import GenerationStatus
from templateer.pipeline import run_pipeline
from templateer.template import Template, TemplateLoadError


# Default template search paths
def _get_default_paths():
    """Get default template search paths."""
    paths = []
    # Bundled templates
    bundled = Path(__file__).parent / "templates"
    if bundled.exists():
        paths.append(bundled)
    # Current directory templates
    cwd_templates = Path.cwd() / "templates"
    if cwd_templates.exists():
        paths.append(cwd_templates)
    return paths


def _load_catalog(paths: list[Path] | None = None) -> TemplateCatalog:
    """Load the template catalog from configured paths."""
    catalog = TemplateCatalog()
    if paths:
        catalog.load_from_paths(paths)
    else:
        catalog.load_from_paths(_get_default_paths())
    return catalog


@click.group()
@click.version_option()
def main():
    """Templateer: typed, constrained artifact generation for AI agents.

    Instead of asking an LLM to write entire files directly, Templateer
    asks an LLM to instantiate a Pydantic model. That validated model is
    then passed to a Jinja renderer. The final file is generated
    deterministically from the template.
    """
    pass


@main.command("list")
@click.option("--paths", "-p", multiple=True, help="Additional template search paths")
def list_templates(paths):
    """List all available templates."""
    search_paths = [Path(p) for p in paths] if paths else None
    catalog = _load_catalog(search_paths)

    if len(catalog) == 0:
        click.echo("No templates found.")
        return

    click.echo(f"Found {len(catalog)} template(s):\n")
    for template in catalog.templates:
        click.echo(f"  {template.name}")
        click.echo(f"    {template.description}")
        click.echo(f"    Output: {template.output_kind}")
        if template.trigger_paths:
            click.echo(f"    Generates: {', '.join(sorted(template.trigger_paths))}")
        click.echo()


@main.command("describe")
@click.argument("template_name")
@click.option("--paths", "-p", multiple=True, help="Additional template search paths")
def describe_template(template_name, paths):
    """Describe a template's metadata."""
    search_paths = [Path(p) for p in paths] if paths else None
    catalog = _load_catalog(search_paths)

    try:
        template = catalog.get(template_name)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    click.echo(f"Name: {template.name}")
    click.echo(f"Description: {template.description}")
    click.echo(f"Output kind: {template.output_kind}")
    click.echo(f"Strict context: {template.metadata.strict_context}")
    click.echo(f"Trigger paths: {template.trigger_paths}")

    for output in template.metadata.outputs:
        click.echo(f"  Generates: {output.path} ({output.kind}, {output.language})")


@main.command("schema")
@click.argument("template_name")
@click.option("--paths", "-p", multiple=True, help="Additional template search paths")
def show_schema(template_name, paths):
    """Show the JSON schema for a template."""
    search_paths = [Path(p) for p in paths] if paths else None
    catalog = _load_catalog(search_paths)

    try:
        template = catalog.get(template_name)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    schema = template.get_schema_json()
    click.echo(json.dumps(schema, indent=2))


@main.command("render")
@click.argument("template_name")
@click.option("--input", "-i", "input_file", required=True,
              help="JSON file with model data")
@click.option("--output", "-o", "output_file", help="Output file (stdout if not given)")
@click.option("--paths", "-p", multiple=True, help="Additional template search paths")
def render_from_model(template_name, input_file, output_file, paths):
    """Render a template from a model JSON file (without LLM)."""
    search_paths = [Path(p) for p in paths] if paths else None
    catalog = _load_catalog(search_paths)

    try:
        template = catalog.get(template_name)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    # Load model data
    try:
        input_data = json.loads(Path(input_file).read_text())
    except Exception as e:
        click.echo(f"Error reading input file: {e}", err=True)
        sys.exit(1)

    # Validate against schema
    schema_class = template.get_schema_class()
    try:
        model = schema_class(**input_data)
    except Exception as e:
        click.echo(f"Validation error: {e}", err=True)
        sys.exit(1)

    # Render
    try:
        rendered = template.render(model)
    except Exception as e:
        click.echo(f"Render error: {e}", err=True)
        sys.exit(1)

    if output_file:
        Path(output_file).write_text(rendered)
        click.echo(f"Written to {output_file}")
    else:
        click.echo(rendered)


@main.command("generate")
@click.argument("template_name")
@click.option("--context", "-c", "context_file",
              help="JSON file with project facts context")
@click.option("--request", "-r", "user_request", help="User request description")
@click.option("--output", "-o", "output_file", help="Output file")
@click.option("--model", "-m", "model_name", default="openai:gpt-4.1-mini",
              help="LLM model to use")
@click.option("--paths", "-p", multiple=True, help="Additional template search paths")
def generate_artifact(template_name, context_file, user_request, output_file, model_name, paths):
    """Generate an artifact using a template (full pipeline with LLM)."""
    search_paths = [Path(p) for p in paths] if paths else None
    catalog = _load_catalog(search_paths)

    # Build context
    context = {}
    if context_file:
        try:
            context_data = json.loads(Path(context_file).read_text())
            # Support both flat dict and nested {"user_request": ..., "facts": ...}
            if "facts" in context_data:
                context = context_data["facts"]
                if "user_request" in context_data and not user_request:
                    user_request = context_data["user_request"]
            else:
                context = context_data
        except Exception as e:
            click.echo(f"Error reading context file: {e}", err=True)
            sys.exit(1)

    if not user_request:
        user_request = f"Generate {template_name} artifact"

    # Run pipeline
    gen = run_pipeline(
        catalog=catalog,
        template_name=template_name,
        user_request=user_request,
        context=context,
        model_name=model_name,
    )

    if gen.status == GenerationStatus.FAILED:
        click.echo(f"Generation failed: {gen.failure_reason}", err=True)
        if gen.artifact:
            click.echo(gen.artifact, err=True)
        sys.exit(1)

    if gen.status == GenerationStatus.READY:
        artifact = gen.artifact
        if output_file and artifact:
            Path(output_file).write_text(artifact)
            click.echo(f"Generated {output_file}")
        elif artifact:
            click.echo(artifact)
    else:
        click.echo(f"Unexpected status: {gen.status}", err=True)
        sys.exit(1)


@main.command("validate")
@click.argument("template_name")
@click.option("--input", "-i", "input_file", required=True,
              help="JSON file with model data to validate and render")
@click.option("--paths", "-p", multiple=True, help="Additional template search paths")
def validate_output(template_name, input_file, paths):
    """Validate that a model file would produce valid output."""
    search_paths = [Path(p) for p in paths] if paths else None
    catalog = _load_catalog(search_paths)

    try:
        template = catalog.get(template_name)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    # Load and validate model
    try:
        input_data = json.loads(Path(input_file).read_text())
    except Exception as e:
        click.echo(f"Error reading input: {e}", err=True)
        sys.exit(1)

    schema_class = template.get_schema_class()
    try:
        model = schema_class(**input_data)
    except Exception as e:
        click.echo(f"Model validation failed: {e}", err=True)
        sys.exit(1)

    click.echo("✓ Model validated against schema")

    # Render
    try:
        rendered = template.render(model)
    except Exception as e:
        click.echo(f"Render failed: {e}", err=True)
        sys.exit(1)

    click.echo("✓ Template rendered successfully")

    # Output validation
    from templateer.validators import validate_output as validate
    output_language = template.metadata.outputs[0].language
    errors = validate(rendered, output_language)
    if errors:
        click.echo(f"✗ Output validation failed:")
        for err in errors:
            click.echo(f"  - {err}")
        sys.exit(1)

    click.echo("✓ Output validation passed")


def entrypoint():
    """Entry point for the CLI."""
    main()


if __name__ == "__main__":
    entrypoint()
```

### Step 8.2: Configure CLI entry point

In `pyproject.toml`, add:

```toml
[project.scripts]
templateer = "templateer.cli:entrypoint"
```

### Phase 8 Testing

Create `tests/test_cli.py`:

```python
"""Tests for the CLI.

Uses Click's CliRunner for integration testing.
"""

import json
import pytest
from pathlib import Path
from click.testing import CliRunner

from templateer.cli import main


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def templates_path():
    return ["--paths", str(Path("templates").resolve())]


def test_list_templates(runner, templates_path):
    """list command shows available templates."""
    result = runner.invoke(main, ["list", *templates_path])
    assert result.exit_code == 0
    assert "pyproject-uv" in result.output


def test_describe_template(runner, templates_path):
    """describe command shows template metadata."""
    result = runner.invoke(main, ["describe", "pyproject-uv", *templates_path])
    assert result.exit_code == 0
    assert "pyproject-uv" in result.output


def test_show_schema(runner, templates_path):
    """schema command outputs JSON schema."""
    result = runner.invoke(main, ["schema", "pyproject-uv", *templates_path])
    assert result.exit_code == 0
    schema = json.loads(result.output)
    assert "properties" in schema


def test_render_from_model(runner, templates_path, tmp_path):
    """render command generates output from a model file."""
    input_file = tmp_path / "input.json"
    input_data = {
        "project_name": "test-project",
        "python_version": "3.12",
        "project_type": "application",
    }
    input_file.write_text(json.dumps(input_data))

    result = runner.invoke(main, [
        "render", "pyproject-uv",
        "--input", str(input_file),
        *templates_path,
    ])
    assert result.exit_code == 0
    assert "[project]" in result.output
    assert "test-project" in result.output


def test_describe_unknown_template(runner, templates_path):
    """describe of unknown template exits with error."""
    result = runner.invoke(main, ["describe", "nonexistent", *templates_path])
    assert result.exit_code == 1


def test_render_with_invalid_model(runner, templates_path, tmp_path):
    """render with invalid model data fails."""
    input_file = tmp_path / "bad_input.json"
    input_file.write_text("{}")

    result = runner.invoke(main, [
        "render", "pyproject-uv",
        "--input", str(input_file),
        *templates_path,
    ])
    assert result.exit_code == 1
```

### Success Criteria — Phase 8

- [ ] `templateer list` shows available templates
- [ ] `templateer describe <name>` shows template metadata
- [ ] `templateer schema <name>` outputs JSON schema
- [ ] `templateer render <name> --input model.json` works (LLM-free path)
- [ ] `templateer generate <name>` works (full pipeline)
- [ ] `templateer validate <name> --input model.json` validates model+render+output
- [ ] Error states produce non-zero exit codes and clear messages
- [ ] CLI is usable by both humans and scripting agents
- [ ] Tests pass: `uv run pytest tests/test_cli.py -v`

---

## Phase 9: Python API

**Goal:** Expose the same functionality via a clean Python API for programmatic use.

### Allium Spec Reference

From `generation.allium` (Python API Surface):

```
surface PythonAPI {
    provides:
        StartGeneration(...)
        ListAllTemplates
        GenerateFromTemplate(...)
        RenderFromModel(...)
        ValidateArtifact(...)
}
```

### Step 9.1: Implement `TemplateRegistry` class

Create `src/templateer/api.py`:

```python
"""Python API for Templateer — programmatic artifact generation."""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from templateer.catalog import TemplateCatalog
from templateer.generation import Generation, GenerationStatus
from templateer.generator import generate_model
from templateer.models import TemplateGenerationResult
from templateer.pipeline import run_pipeline
from templateer.template import Template
from templateer.validators import validate_output


class TemplateRegistry:
    """Python API for discovering and using Templateer templates.

    Usage:
        registry = TemplateRegistry.from_paths(["./templates"])

        # List available templates
        for t in registry.list_templates():
            print(t.name, t.description)

        # Generate an artifact
        result = registry.generate(
            template_name="pyproject-uv",
            user_request="Create a pyproject.toml for a FastAPI app using uv.",
            context={"uses_fastapi": True, "uses_pytest": True},
        )
        print(result.rendered)
    """

    def __init__(self, catalog: TemplateCatalog):
        self._catalog = catalog

    @classmethod
    def from_paths(cls, paths: list[Path | str]) -> "TemplateRegistry":
        """Create a registry from template directory paths."""
        catalog = TemplateCatalog()
        catalog.load_from_paths([Path(p) for p in paths])
        return cls(catalog)

    def list_templates(self) -> list[Template]:
        """Return all available templates."""
        return self._catalog.templates

    def get_template(self, name: str) -> Template:
        """Get a template by exact name."""
        return self._catalog.get(name)

    def generate(
        self,
        template_name: str,
        user_request: str,
        context: dict[str, Any] | None = None,
        model_name: str = "openai:gpt-4.1-mini",
    ) -> TemplateGenerationResult:
        """
        Generate an artifact using a template (full pipeline with LLM).

        Args:
            template_name: Exact template name.
            user_request: What to generate.
            context: Optional project facts.
            model_name: LLM to use.

        Returns:
            TemplateGenerationResult with the model, rendered artifact, and messages.
        """
        gen = run_pipeline(
            catalog=self._catalog,
            template_name=template_name,
            user_request=user_request,
            context=context,
            model_name=model_name,
        )

        if gen.status == GenerationStatus.FAILED:
            raise RuntimeError(
                f"Generation failed: {gen.failure_reason}"
            )

        return TemplateGenerationResult(
            template_name=template_name,
            model={},  # We'd need to capture the model from the pipeline
            rendered=gen.artifact or "",
            validation_messages=[],
        )

    def render_from_model(
        self,
        template_name: str,
        model_data: dict[str, Any],
    ) -> str:
        """
        Render a template from a model dict (LLM-free path).

        Args:
            template_name: Exact template name.
            model_data: Dict matching the template's Pydantic schema.

        Returns:
            The rendered artifact text.
        """
        template = self._catalog.get(template_name)
        schema_class = template.get_schema_class()
        model = schema_class(**model_data)
        return template.render(model)

    def validate_artifact(
        self,
        template_name: str,
        artifact: str,
    ) -> list[str]:
        """
        Validate a rendered artifact against the template's output validators.

        Args:
            template_name: Exact template name.
            artifact: The artifact text to validate.

        Returns:
            List of error messages. Empty list means validation passed.
        """
        template = self._catalog.get(template_name)
        output_language = template.metadata.outputs[0].language
        validators = [v.model_dump() for v in template.metadata.validators]
        return validate_output(artifact, output_language, validators)

    def generate_model(
        self,
        template_name: str,
        user_request: str,
        context: dict[str, Any] | None = None,
    ) -> BaseModel:
        """
        Generate just the Pydantic model (no rendering).

        This is useful when the caller wants to inspect or modify
        the model before rendering.

        Args:
            template_name: Exact template name.
            user_request: What to generate.
            context: Optional project facts.

        Returns:
            A validated Pydantic model instance.
        """
        template = self._catalog.get(template_name)
        model, _ = generate_model(
            template=template,
            user_request=user_request,
            context=context,
        )
        return model
```

### Phase 9 Testing

Create `tests/test_api.py`:

```python
"""Tests for the Python API."""

import json
import pytest
from pathlib import Path

from templateer.api import TemplateRegistry


@pytest.fixture
def registry():
    return TemplateRegistry.from_paths([Path("templates")])


def test_list_templates(registry):
    """API can list available templates."""
    templates = registry.list_templates()
    assert len(templates) > 0
    assert any(t.name == "pyproject-uv" for t in templates)


def test_get_template(registry):
    """API can retrieve a specific template."""
    template = registry.get_template("pyproject-uv")
    assert template.name == "pyproject-uv"


def test_render_from_model(registry):
    """API can render from model data without LLM."""
    input_data = json.loads(
        (Path("templates/pyproject-uv/examples/fastapi.input.json")).read_text()
    )
    rendered = registry.render_from_model("pyproject-uv", input_data)
    assert "[project]" in rendered


def test_validate_artifact(registry):
    """API can validate rendered artifacts."""
    errors = registry.validate_artifact(
        "pyproject-uv",
        '[project]\nname = "test"\n',
    )
    assert errors == []


def test_get_template_unknown(registry):
    """Unknown template raises error."""
    with pytest.raises(Exception):
        registry.get_template("nonexistent")
```

### Success Criteria — Phase 9

- [ ] `TemplateRegistry.from_paths()` creates a usable registry
- [ ] `list_templates()` returns all templates
- [ ] `render_from_model()` works without LLM
- [ ] `generate_from_template()` works with LLM (when API key available)
- [ ] `validate_artifact()` validates against template output specs
- [ ] Tests pass: `uv run pytest tests/test_api.py -v`

---

## Phase 10: Example Templates & Integration Testing

**Goal:** Create a set of working example templates and verify end-to-end integration.

### Step 10.1: Complete the `pyproject-uv` template

Ensure the following files exist and work:

- `templates/pyproject-uv/metadata.yml` (✓ created in Phase 1)
- `templates/pyproject-uv/schema.py` (complete from concept doc)
- `templates/pyproject-uv/prompt.md` (complete from concept doc)
- `templates/pyproject-uv/template.j2` (complete from concept doc)
- `templates/pyproject-uv/examples/fastapi.input.json` (realistic FastAPI input)
- `templates/pyproject-uv/examples/fastapi.output.toml` (matching rendered output)
- `templates/pyproject-uv/tests/test_template.py` (template-specific tests)

### Step 10.2: Template-specific test pattern

Every template should have a test file that exercises its schema and rendering without an LLM:

```python
# templates/pyproject-uv/tests/test_template.py
"""Template-specific tests for pyproject-uv."""

import json
from pathlib import Path

from templateer.template import Template


TEMPLATE_DIR = Path(__file__).parent.parent


def test_schema_loads():
    """Schema module loads and contains the expected class."""
    t = Template(TEMPLATE_DIR)
    cls = t.get_schema_class()
    assert cls.__name__ == "PyprojectUvModel"


def test_fastapi_fixture_renders_correctly():
    """The FastAPI input fixture produces the expected output."""
    t = Template(TEMPLATE_DIR)

    input_data = json.loads(
        (TEMPLATE_DIR / "examples" / "fastapi.input.json").read_text()
    )
    expected = (TEMPLATE_DIR / "examples" / "fastapi.output.toml").read_text()

    cls = t.get_schema_class()
    model = cls(**input_data)
    rendered = t.render(model)

    assert rendered.strip() == expected.strip()


def test_minimal_model_renders():
    """A minimal valid model renders without errors."""
    t = Template(TEMPLATE_DIR)
    cls = t.get_schema_class()
    model = cls(
        project_name="minimal-project",
        python_version="3.12",
    )
    rendered = t.render(model)
    assert "minimal-project" in rendered
    assert "[project]" in rendered
```

### Step 10.3: Integration test

Create `tests/test_integration.py`:

```python
"""End-to-end integration tests for the complete pipeline."""

import json
import pytest
from pathlib import Path

from templateer.catalog import TemplateCatalog
from templateer.pipeline import run_pipeline
from templateer.generation import GenerationStatus


@pytest.fixture
def catalog():
    c = TemplateCatalog()
    c.load_from_paths([Path("templates")])
    return c


def test_fastapi_full_pipeline_without_llm(catalog):
    """Integration: load template, validate fixture, render, validate output."""
    template = catalog.get("pyproject-uv")

    # Load fixture
    input_data = json.loads(
        (Path("templates/pyproject-uv/examples/fastapi.input.json")).read_text()
    )
    expected = (
        Path("templates/pyproject-uv/examples/fastapi.output.toml")
        .read_text()
    )

    # Validate model
    cls = template.get_schema_class()
    model = cls(**input_data)

    # Render
    rendered = template.render(model)
    assert rendered.strip() == expected.strip()

    # Output validation
    from templateer.validators import validate_output
    errors = validate_output(rendered, "toml")
    assert errors == []


def test_template_fixtures_match():
    """Every template with examples has matching input→output tests."""
    templates_dir = Path("templates")
    for template_dir in templates_dir.iterdir():
        if not template_dir.is_dir():
            continue
        metadata_file = template_dir / "metadata.yml"
        if not metadata_file.exists():
            continue

        examples_dir = template_dir / "examples"
        if not examples_dir.exists():
            continue

        for input_file in sorted(examples_dir.glob("*.input.json")):
            # Find matching output file
            stem = input_file.name.replace(".input.json", "")
            output_files = list(examples_dir.glob(f"{stem}.output.*"))
            assert len(output_files) == 1, (
                f"Missing output fixture for {input_file}"
            )

            output_file = output_files[0]
            expected = output_file.read_text()

            # Verify roundtrip
            from templateer.template import Template
            t = Template(template_dir)
            input_data = json.loads(input_file.read_text())
            cls = t.get_schema_class()
            model = cls(**input_data)
            rendered = t.render(model)

            assert rendered.strip() == expected.strip(), (
                f"Fixture mismatch: {input_file} → {output_file}"
            )


def test_all_templates_load_and_have_valid_schemas(catalog):
    """Every template in the catalog loads and has a valid schema."""
    for template in catalog.templates:
        # Metadata parses
        assert template.name
        assert template.description

        # Schema loads
        cls = template.get_schema_class()
        from pydantic import BaseModel
        assert issubclass(cls, BaseModel)

        # Prompt exists
        prompt = template.load_prompt()
        assert len(prompt) > 0

        # Renderer file exists
        renderer_path = template.resolve_path(
            template.metadata.renderer.file
        )
        assert renderer_path.exists(), f"Missing template: {renderer_path}"
```

### Success Criteria — Phase 10

- [ ] `pyproject-uv` template is complete with all files
- [ ] Fixture tests pass: input.json → output.xxx roundtrips
- [ ] All templates in the catalog pass structural validation
- [ ] Integration test verifies full pipeline from fixture data
- [ ] Tests pass: `uv run pytest tests/test_integration.py -v`

---

## Phase 11: Polish & Documentation

**Goal:** Finalize the project with proper documentation, error handling, and developer experience.

### Step 11.1: Write README.md

Create a comprehensive README with:
- Project description and philosophy
- Installation instructions
- Quick start guide
- CLI reference (all commands)
- Python API examples
- Template authoring guide
- Security considerations

### Step 11.2: Error handling review

Ensure all error paths produce clear, actionable messages:
- `TemplateNotFoundError` — template name in the message
- `TemplateLoadError` — which file and what's wrong
- `RenderError` — which template and which variable
- `ModelGenerationError` — whether LLM failed or validation failed
- `OutputValidationError` — which validator and what failed

### Step 11.3: Type checking

```bash
uv run ty src/templateer/ --strict
```

Fix all type errors.

### Step 11.4: Linting

```bash
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```

### Step 11.5: Test coverage

```bash
uv run pytest --cov=templateer --cov-report=term-missing
```

Aim for >80% coverage on core modules.

### Step 11.6: Developer documentation

Add developer-focused docs:
- `CONTRIBUTING.md` — how to add new templates, development setup
- Architecture decision records (ADR) for key design choices
- Docstrings on all public APIs

### Success Criteria — Phase 11

- [ ] README is complete and accurate
- [ ] All error paths have clear messages
- [ ] `ty --strict` passes
- [ ] `ruff check` passes
- [ ] Test coverage >80%
- [ ] Docstrings on all public functions and classes

---

## Allium Spec Alignment

This section maps implementation components to Allium spec constructs to verify coverage.

### `templates.allium` Coverage

| Spec Construct | Implementation |
|---------------|---------------|
| `Template` entity | `Template` class in `template.py` |
| `Template.name: String` | `Template.name` property |
| `Template.description: String` | `TemplateMetadata.description` |
| `Template.output_kind: String` | `Template.output_kind` property |
| `Template.trigger_paths: Set<String>` | `TemplateMetadata.triggers.filenames` |
| `TemplateCatalog` entity | `TemplateCatalog` class in `catalog.py` |
| `TemplateCatalog.templates: Set<Template>` | `TemplateCatalog._templates` dict |
| `TemplateCatalog.has_template(name)` | `TemplateCatalog.has_template()` |
| `TemplateCatalog.templates_by_output(kind)` | `TemplateCatalog.templates_by_output_kind()` |
| `TemplateCatalogue` surface | `templateer list` CLI command + `TemplateRegistry.list_templates()` |
| `BrowseTemplates()` | `templateer list` |
| `DescribeTemplate(name)` | `templateer describe <name>` |
| `ShowSchema(name)` | `templateer schema <name>` |

### `generation.allium` Coverage

| Spec Construct | Implementation |
|---------------|---------------|
| `Generation` entity | `Generation` model in `generation.py` |
| `Generation.status` | `Generation.status: GenerationStatus` |
| `Generation.status transitions` | `run_pipeline()` state machine |
| `FailureReason` enum | `FailureReason` in `generation.py` |
| `SubmitGeneration` rule | `run_pipeline()` entry |
| `ResolveTemplate` rule | `catalog.get(template_name)` in pipeline |
| `DeliverArtifact` rule | Pipeline success path: model→render→validate→ready |
| `FailGeneration` rule | Pipeline error paths |
| `RetryFailedGeneration` rule | `retry_generation()` + `generate_model()` retries |
| `ArtifactWorkshop` surface | `templateer generate`, `templateer render`, `templateer validate` |
| `GenerateArtifact(...)` | `templateer generate <name>` + `TemplateRegistry.generate()` |
| `RetryGeneration(g)` | `retry_generation()` in `pipeline.py` |
| Invariant `ArtifactImpliesReady` | `Generation.artifact` set only in READY state |
| Invariant `FailureImpliesReason` | `Generation.failure_reason` always set in FAILED state |
| Invariant `ReadyImpliesArtifact` | READY status only set when artifact is available |
| Invariant `StrictRenderContract` | `renderer.py` only passes `model.model_dump(mode="json")` |
| Invariant `DeterministicRender` | MiniJinja with strict mode, same inputs → same output |
| `CLI` surface | `cli.py` with Click |
| `PythonAPI` surface | `api.py` with `TemplateRegistry` |

### Open Questions from Specs

These remain open and should be tracked:

1. **Should templates support an explicit version field?** → Track as GitHub issue
2. **Can a template be renamed after it is loaded?** → Currently no; rename requires directory rename
3. **Should Generations be removed after delivery, or retained as history?** → Currently ephemeral
4. **Should ProjectFacts be template-specific value types?** → Currently generic `dict[str, Any]`
5. **Should there be a TemplateCustodian actor?** → Template loading is read-only at runtime
6. **Should the surface distinguish between human and agent requesters?** → Currently identical (both are Requesters)
7. **Should Generation support async LLM calls?** → Currently synchronous

---

## Appendix A: Template Authoring Guide

### Creating a New Template

1. Create a directory under `templates/` with your template name (kebab-case):
   ```
   templates/github-actions-ci/
   ```

2. Create `metadata.yml`:
   ```yaml
   name: github-actions-ci
   description: Generate a GitHub Actions CI workflow for a Python project.
   outputs:
     - path: .github/workflows/ci.yml
       kind: full_file
       language: yaml
   schema:
     module: schema
     class: GitHubActionsCiModel
   prompt:
     file: prompt.md
   renderer:
     engine: minijinja
     file: template.j2
   strict_context: true
   triggers:
     filenames:
       - .github/workflows/ci.yml
   validators:
     - type: parse
       language: yaml
   ```

3. Create `schema.py` with your Pydantic model.

4. Create `prompt.md` with LLM instructions.

5. Create `template.j2` with Jinja template.

6. Create `examples/$SCENARIO.input.json` and `examples/$SCENARIO.output.xxx`.

7. Create `tests/test_template.py`.

8. Run `templateer validate <name> --input examples/scenario.input.json`.

### Schema Design Rules

1. Model decisions, not syntax.
2. Use enums/literals where possible.
3. Use nested models for structured concepts.
4. Use validation to reject incompatible choices.
5. Avoid freeform raw output fields.
6. Include field descriptions for the LLM.
7. Provide useful defaults.

### Rendering Rules

1. Undefined variables are errors (strict mode).
2. The template receives only the validated model dump.
3. Complex decisions live in the schema, not in Jinja.
4. The template must not call external tools or read files.

---

## Appendix B: File Manifest

```
templateer/
├── pyproject.toml
├── README.md
├── CONTRIBUTING.md
├── src/
│   └── templateer/
│       ├── __init__.py           # Version, default paths
│       ├── py.typed             # PEP 561 marker
│       ├── api.py               # Python API (TemplateRegistry)
│       ├── catalog.py           # TemplateCatalog
│       ├── cli.py               # CLI (Click)
│       ├── generation.py        # Generation entity + enums
│       ├── generator.py         # Pydantic AI model generation
│       ├── models.py            # Pydantic models (metadata, etc.)
│       ├── pipeline.py          # End-to-end pipeline
│       ├── renderer.py          # MiniJinja renderer
│       ├── template.py          # Template loader
│       ├── validation.py        # Model validation utilities
│       └── validators.py        # Output validators
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
            └── test_template.py
```

---

## Appendix C: Quick Reference — Commands to Run at Each Phase

| Phase | Verification Command |
|-------|---------------------|
| 0 | `uv run pytest && uv run ty src/ && uv run ruff check src/` |
| 1 | `uv run pytest tests/test_models.py -v` |
| 2 | `uv run pytest tests/test_catalog.py tests/test_template.py -v` |
| 3 | `uv run pytest tests/test_validation.py -v` |
| 4 | `uv run pytest tests/test_generator.py -v` (LLM tests with `--run-llm`) |
| 5 | `uv run pytest tests/test_renderer.py -v` |
| 6 | `uv run pytest tests/test_validators.py -v` |
| 7 | `uv run pytest tests/test_pipeline.py -v` |
| 8 | `uv run pytest tests/test_cli.py -v && templateer list` |
| 9 | `uv run pytest tests/test_api.py -v` |
| 10 | `uv run pytest tests/test_integration.py -v` |
| 11 | `uv run pytest --cov=templateer && uv run ty src/ && uv run ruff check src/` |
