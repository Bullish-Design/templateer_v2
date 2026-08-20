"""Tests for core Pydantic models."""

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import TypeAdapter, ValidationError

from templateer.models import (
    CommandValidator,
    OutputSpec,
    ParseValidator,
    PromptRef,
    RendererRef,
    SchemaRef,
    TemplateMetadata,
)

# ``FullFileOutput`` arrives with the §C8 discriminated union in wave 1.  It
# is imported inside each test that needs it, so the rest of this module
# still collects until then.


def test_template_metadata_parses_from_minimal_dict() -> None:
    """Test that TemplateMetadata validates a minimal correct metadata dict."""
    data: dict[str, Any] = {
        "name": "pyproject-uv",
        "description": "Generate a uv-style pyproject.toml",
        "output": {"path": "pyproject.toml", "language": "toml"},
        "schema": {"module": "schema", "class": "PyprojectUvModel"},
        "prompt": {"file": "prompt.md"},
        "renderer": {"engine": "minijinja", "file": "template.j2"},
    }
    meta = TemplateMetadata.model_validate(data)
    assert meta.name == "pyproject-uv"
    assert meta.output.path == "pyproject.toml"
    assert meta.output.language == "toml"
    assert meta.trigger_filenames == []


def test_template_metadata_rejects_missing_name() -> None:
    """Template name is required."""
    data: dict[str, Any] = {
        "description": "...",
        "output": {"path": "x", "language": "toml"},
        "schema": {"module": "s", "class": "M"},
        "prompt": {"file": "p.md"},
        "renderer": {"engine": "minijinja", "file": "t.j2"},
    }
    with pytest.raises(ValidationError):
        TemplateMetadata.model_validate(data)


def test_parse_validator_requires_language() -> None:
    """A parse validator without a language is rejected at load."""
    with pytest.raises(ValidationError):
        ParseValidator.model_validate({"kind": "parse"})


def test_command_validator_requires_command() -> None:
    """A command validator without a command is rejected at load."""
    with pytest.raises(ValidationError):
        CommandValidator.model_validate({"kind": "command"})


def test_validator_rejects_unknown_kind() -> None:
    """An unknown validator kind fails template load, not validation."""
    data: dict[str, Any] = {
        "name": "pyproject-uv",
        "description": "...",
        "output": {"path": "x", "language": "toml"},
        "schema": {"module": "s", "class": "M"},
        "prompt": {"file": "p.md"},
        "renderer": {"engine": "minijinja", "file": "t.j2"},
        "validators": [{"kind": "bogus", "language": "toml"}],
    }
    with pytest.raises(ValidationError):
        TemplateMetadata.model_validate(data)


def test_validator_rejects_extra_fields() -> None:
    """Validator metadata is extra=forbid, like TemplateMetadata."""
    with pytest.raises(ValidationError):
        ParseValidator.model_validate({"kind": "parse", "language": "toml", "typo": 1})


def test_schema_ref_uses_alias() -> None:
    """SchemaRef maps 'class' YAML field to class_name."""
    s = SchemaRef.model_validate({"module": "schema", "class": "PyprojectUvModel"})
    assert s.class_name == "PyprojectUvModel"
    assert s.module == "schema"


def test_output_spec_requires_fields() -> None:
    """A full-file output requires path and language.

    §C8: ``OutputSpec`` is now a discriminated union alias, not a class.
    Construct the union member; validate the alias with a ``TypeAdapter``.
    """
    from templateer.models import FullFileOutput

    spec = FullFileOutput(path="pyproject.toml", language="toml")
    assert spec.path == "pyproject.toml"
    assert spec.language == "toml"
    assert spec.kind == "full_file"


def test_output_spec_alias_validates_through_a_type_adapter() -> None:
    """§C8: ``TypeAdapter(OutputSpec)`` is the way to validate raw metadata."""
    from templateer.models import FullFileOutput

    spec = TypeAdapter(OutputSpec).validate_python(
        {"path": "pyproject.toml", "language": "toml"}
    )
    assert isinstance(spec, FullFileOutput)
    assert spec.kind == "full_file"


def test_prompt_ref_accepts_file() -> None:
    """PromptRef stores file path."""
    p = PromptRef(file="prompt.md")
    assert p.file == "prompt.md"


def test_renderer_ref_defaults_to_minijinja() -> None:
    """RendererRef defaults engine to minijinja."""
    r = RendererRef(file="template.j2")
    assert r.engine == "minijinja"
    assert r.file == "template.j2"


def test_template_metadata_with_trigger_filenames() -> None:
    """TemplateMetadata can include trigger filenames."""
    data: dict[str, Any] = {
        "name": "pyproject-uv",
        "description": "Generate a pyproject.toml",
        "output": {"path": "pyproject.toml", "language": "toml"},
        "schema": {"module": "schema", "class": "PyprojectUvModel"},
        "prompt": {"file": "prompt.md"},
        "renderer": {"engine": "minijinja", "file": "template.j2"},
        "trigger_filenames": ["pyproject.toml"],
    }
    meta = TemplateMetadata.model_validate(data)
    assert meta.trigger_filenames == ["pyproject.toml"]


def test_template_metadata_rejects_old_triggers_shape() -> None:
    """The old triggers: {filenames: [...]} shape fails loudly at load."""
    data: dict[str, Any] = {
        "name": "pyproject-uv",
        "description": "Generate a pyproject.toml",
        "output": {"path": "pyproject.toml", "language": "toml"},
        "schema": {"module": "schema", "class": "PyprojectUvModel"},
        "prompt": {"file": "prompt.md"},
        "renderer": {"engine": "minijinja", "file": "template.j2"},
        "triggers": {"filenames": ["pyproject.toml"]},
    }
    with pytest.raises(ValidationError):
        TemplateMetadata.model_validate(data)


def test_template_metadata_with_validators() -> None:
    """TemplateMetadata can include output validators."""
    data: dict[str, Any] = {
        "name": "pyproject-uv",
        "description": "Generate a pyproject.toml",
        "output": {"path": "pyproject.toml", "language": "toml"},
        "schema": {"module": "schema", "class": "PyprojectUvModel"},
        "prompt": {"file": "prompt.md"},
        "renderer": {"engine": "minijinja", "file": "template.j2"},
        "validators": [{"kind": "parse", "language": "toml"}],
    }
    meta = TemplateMetadata.model_validate(data)
    assert len(meta.validators) == 1
    validator = meta.validators[0]
    assert validator.kind == "parse"
    assert isinstance(validator, ParseValidator)
    assert validator.language == "toml"


def test_metadata_yml_parses_successfully() -> None:
    """The actual templates/pyproject-uv/metadata.yml parses correctly."""
    raw = Path("templates/pyproject-uv/metadata.yml").read_text(encoding="utf-8")
    data: dict[str, Any] = yaml.safe_load(raw)
    meta = TemplateMetadata.model_validate(data)
    assert meta.name == "pyproject-uv"
    assert meta.schema_ref.module == "schema"
    assert meta.schema_ref.class_name == "PyprojectUvModel"
    assert meta.prompt.file == "prompt.md"
    assert meta.renderer.engine == "minijinja"
    assert meta.renderer.file == "template.j2"
    assert meta.output.path == "pyproject.toml"
    assert meta.output.language == "toml"
    assert meta.trigger_filenames == ["pyproject.toml"]


def test_template_metadata_rejects_missing_output() -> None:
    """A template without an output is rejected at load."""
    data: dict[str, Any] = {
        "name": "test",
        "description": "...",
        "schema": {"module": "s", "class": "M"},
        "prompt": {"file": "p.md"},
        "renderer": {"engine": "minijinja", "file": "t.j2"},
    }
    with pytest.raises(ValidationError):
        TemplateMetadata.model_validate(data)


def test_template_metadata_rejects_old_outputs_shape() -> None:
    """The old outputs: [{...}] shape fails loudly at load."""
    data: dict[str, Any] = {
        "name": "test",
        "description": "...",
        "outputs": [{"path": "x", "kind": "full_file", "language": "toml"}],
        "schema": {"module": "s", "class": "M"},
        "prompt": {"file": "p.md"},
        "renderer": {"engine": "minijinja", "file": "t.j2"},
    }
    with pytest.raises(ValidationError):
        TemplateMetadata.model_validate(data)
