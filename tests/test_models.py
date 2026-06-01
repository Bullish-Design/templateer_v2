"""Tests for core Pydantic models."""

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from templateer.models import (
    OutputSpec,
    OutputValidator,
    PromptRef,
    RendererRef,
    SchemaRef,
    TemplateMetadata,
)


def test_template_metadata_parses_from_minimal_dict() -> None:
    """Test that TemplateMetadata validates a minimal correct metadata dict."""
    data: dict[str, Any] = {
        "name": "pyproject-uv",
        "description": "Generate a uv-style pyproject.toml",
        "outputs": [{"path": "pyproject.toml", "kind": "full_file", "language": "toml"}],
        "schema": {"module": "schema", "class": "PyprojectUvModel"},
        "prompt": {"file": "prompt.md"},
        "renderer": {"engine": "minijinja", "file": "template.j2"},
    }
    meta = TemplateMetadata.model_validate(data)
    assert meta.name == "pyproject-uv"
    assert meta.strict_context is True  # default
    assert len(meta.outputs) == 1
    assert meta.outputs[0].path == "pyproject.toml"


def test_template_metadata_rejects_missing_name() -> None:
    """Template name is required."""
    data: dict[str, Any] = {
        "description": "...",
        "outputs": [{"path": "x", "kind": "full_file", "language": "toml"}],
        "schema": {"module": "s", "class": "M"},
        "prompt": {"file": "p.md"},
        "renderer": {"engine": "minijinja", "file": "t.j2"},
    }
    with pytest.raises(ValidationError):
        TemplateMetadata.model_validate(data)


def test_output_validator_parse_kind() -> None:
    """Parse validators are supported."""
    v = OutputValidator(kind="parse", language="toml")
    assert v.kind == "parse"
    assert v.language == "toml"


def test_schema_ref_uses_alias() -> None:
    """SchemaRef maps 'class' YAML field to class_name."""
    s = SchemaRef.model_validate({"module": "schema", "class": "PyprojectUvModel"})
    assert s.class_name == "PyprojectUvModel"
    assert s.module == "schema"


def test_output_spec_requires_fields() -> None:
    """OutputSpec requires path, kind, and language."""
    spec = OutputSpec(path="pyproject.toml", kind="full_file", language="toml")
    assert spec.path == "pyproject.toml"
    assert spec.language == "toml"


def test_prompt_ref_accepts_file() -> None:
    """PromptRef stores file path."""
    p = PromptRef(file="prompt.md")
    assert p.file == "prompt.md"


def test_renderer_ref_defaults_to_minijinja() -> None:
    """RendererRef defaults engine to minijinja."""
    r = RendererRef(file="template.j2")
    assert r.engine == "minijinja"
    assert r.file == "template.j2"


def test_template_metadata_with_triggers() -> None:
    """TemplateMetadata can include trigger conditions."""
    data: dict[str, Any] = {
        "name": "pyproject-uv",
        "description": "Generate a pyproject.toml",
        "outputs": [{"path": "pyproject.toml", "kind": "full_file", "language": "toml"}],
        "schema": {"module": "schema", "class": "PyprojectUvModel"},
        "prompt": {"file": "prompt.md"},
        "renderer": {"engine": "minijinja", "file": "template.j2"},
        "triggers": {"filenames": ["pyproject.toml"]},
    }
    meta = TemplateMetadata.model_validate(data)
    assert meta.triggers["filenames"] == ["pyproject.toml"]


def test_template_metadata_with_validators() -> None:
    """TemplateMetadata can include output validators."""
    data: dict[str, Any] = {
        "name": "pyproject-uv",
        "description": "Generate a pyproject.toml",
        "outputs": [{"path": "pyproject.toml", "kind": "full_file", "language": "toml"}],
        "schema": {"module": "schema", "class": "PyprojectUvModel"},
        "prompt": {"file": "prompt.md"},
        "renderer": {"engine": "minijinja", "file": "template.j2"},
        "validators": [{"kind": "parse", "language": "toml"}],
    }
    meta = TemplateMetadata.model_validate(data)
    assert len(meta.validators) == 1
    assert meta.validators[0].kind == "parse"
    assert meta.validators[0].language == "toml"


def test_metadata_yml_parses_successfully() -> None:
    """The actual templates/pyproject-uv/metadata.yml parses correctly."""
    raw = Path("templates/pyproject-uv/metadata.yml").read_text()
    data: dict[str, Any] = yaml.safe_load(raw)
    meta = TemplateMetadata.model_validate(data)
    assert meta.name == "pyproject-uv"
    assert meta.schema_ref.module == "schema"
    assert meta.schema_ref.class_name == "PyprojectUvModel"
    assert meta.prompt.file == "prompt.md"
    assert meta.renderer.engine == "minijinja"
    assert meta.renderer.file == "template.j2"
    assert meta.strict_context is True
    assert meta.triggers["filenames"] == ["pyproject.toml"]


def test_template_metadata_rejects_invalid_output_kind() -> None:
    """OutputSpec kind must be 'full_file'."""
    data: dict[str, Any] = {
        "name": "test",
        "description": "...",
        "outputs": [{"path": "x", "kind": "invalid_kind", "language": "toml"}],
        "schema": {"module": "s", "class": "M"},
        "prompt": {"file": "p.md"},
        "renderer": {"engine": "minijinja", "file": "t.j2"},
    }
    with pytest.raises(ValidationError):
        TemplateMetadata.model_validate(data)
