"""Tests for TemplateCatalog."""

from pathlib import Path

import pytest

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


def test_catalog_handles_nonexistent_path():
    """Loading from a non-existent path is safe."""
    catalog = TemplateCatalog()
    catalog.load_from_paths([Path("/nonexistent/path")])
    assert len(catalog) == 0


def test_catalog_contains_operator(catalog_with_pyproject_uv):
    """In operator works on template names."""
    assert "pyproject-uv" in catalog_with_pyproject_uv
    assert "nonexistent" not in catalog_with_pyproject_uv


def test_catalog_templates_property(catalog_with_pyproject_uv):
    """The templates property returns all loaded templates."""
    templates = catalog_with_pyproject_uv.templates
    assert len(templates) > 0
    assert any(t.name == "pyproject-uv" for t in templates)


def test_catalog_load_from_paths_duplicate_first_wins(tmp_path):
    """When same template name appears in multiple paths, the first one wins."""
    # Create two directories with the same template name
    dir1 = tmp_path / "dir1"
    dir2 = tmp_path / "dir2"
    dir1.mkdir()
    dir2.mkdir()

    tmpl1 = dir1 / "same-name"
    tmpl1.mkdir()
    (tmpl1 / "metadata.yml").write_text("""\
name: same-name
description: First version
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

    tmpl2 = dir2 / "same-name"
    tmpl2.mkdir()
    (tmpl2 / "metadata.yml").write_text("""\
name: same-name
description: Second version (should be ignored)
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

    catalog = TemplateCatalog()
    catalog.load_from_paths([dir1, dir2])
    assert len(catalog) == 1
    assert catalog.get("same-name").description == "First version"


def test_catalog_load_skips_broken_templates(tmp_path):
    """Broken templates (invalid metadata.yml) are skipped with a warning."""
    # Invalid: missing required 'description' field
    template_dir = tmp_path / "broken"
    template_dir.mkdir()
    metadata = template_dir / "metadata.yml"
    metadata.write_text("""\
name: broken
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

    # Valid template
    good_dir = tmp_path / "good-template"
    good_dir.mkdir()
    good_metadata = good_dir / "metadata.yml"
    good_metadata.write_text("""\
name: good-template
description: A valid template
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

    catalog = TemplateCatalog()
    catalog.load_from_paths([tmp_path])
    # The broken template is skipped, the good one loads
    assert "good-template" in catalog
    assert "broken" not in catalog
