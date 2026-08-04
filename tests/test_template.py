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
output:
  path: out.txt
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


def test_resolve_path_escapes_root_raises(tmp_path):
    """A path escaping the template root is a template bug, not a feature."""
    template_dir = tmp_path / "contained"
    template_dir.mkdir()
    metadata = template_dir / "metadata.yml"
    metadata.write_text("""\
name: contained
description: A test template
output:
  path: out.txt
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
    t = Template(template_dir)
    with pytest.raises(TemplateLoadError, match="escapes the template root"):
        t.resolve_path("../../secret.txt")


def test_resolve_path_inside_root_ok(tmp_path):
    """Paths inside the template root resolve normally."""
    template_dir = tmp_path / "contained"
    template_dir.mkdir()
    metadata = template_dir / "metadata.yml"
    metadata.write_text("""\
name: contained
description: A test template
output:
  path: out.txt
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
    t = Template(template_dir)
    resolved = t.resolve_path("metadata.yml")
    assert resolved == metadata.resolve()


def test_properties():
    """Template properties return expected values."""
    t = Template(Path("templates/pyproject-uv"))
    assert t.name == "pyproject-uv"
    assert t.output_language == "toml"
    assert "pyproject.toml" in t.trigger_paths


def test_load_example(tmp_path):
    """load_example returns the first input fixture as JSON, or None."""
    t = Template(Path("templates/pyproject-uv"))
    example = t.load_example()
    assert example is not None
    assert '"project_name"' in example

    # A template with no examples returns None
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "metadata.yml").write_text("""\
name: bare
description: no examples
output:
  path: out.txt
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
    assert Template(bare).load_example() is None
