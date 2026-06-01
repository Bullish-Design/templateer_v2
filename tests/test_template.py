"""Tests for Template loader."""

from pathlib import Path

import pytest
from pydantic import BaseModel

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
    assert issubclass(cls, BaseModel)
    assert cls.__name__ == "PyprojectUvModel"


def test_get_schema_json():
    """JSON schema can be generated for a template."""
    t = Template(Path("templates/pyproject-uv"))
    schema = t.get_schema_json()
    assert "properties" in schema
    assert "title" in schema


def test_missing_metadata_raises(tmp_path):
    """Missing metadata.yml raises TemplateLoadError."""
    empty_dir = tmp_path / "empty-template"
    empty_dir.mkdir()
    with pytest.raises(TemplateLoadError, match="metadata.yml not found"):
        Template(empty_dir)


def test_name_must_match_directory(tmp_path):
    """Metadata name must equal directory name."""
    template_dir = tmp_path / "mismatched-name"
    template_dir.mkdir()
    metadata = template_dir / "metadata.yml"
    metadata.write_text("""\
name: different-name
description: A test template
outputs:
  - path: out.txt
    kind: full_file
    language: toml
schema:
  module: schema
  class: TestModel
prompt:
  file: prompt.md
renderer:
  engine: minijinja
  file: template.j2
""")
    with pytest.raises(TemplateLoadError, match="does not match"):
        Template(template_dir)


def test_invalid_yaml_metadata_raises(tmp_path):
    """Invalid YAML in metadata.yml raises TemplateLoadError."""
    template_dir = tmp_path / "bad-yaml"
    template_dir.mkdir()
    metadata = template_dir / "metadata.yml"
    metadata.write_text("not: valid: yaml: {{{")
    with pytest.raises(TemplateLoadError):
        Template(template_dir)


def test_resolve_path():
    """Resolve path works relative to template root."""
    t = Template(Path("templates/pyproject-uv"))
    resolved = t.resolve_path("schema.py")
    assert resolved.exists()
    assert str(resolved).endswith("schema.py")


def test_properties():
    """Template properties return expected values."""
    t = Template(Path("templates/pyproject-uv"))
    assert t.name == "pyproject-uv"
    assert t.output_kind == "toml"
    assert "pyproject.toml" in t.trigger_paths
