"""Tests for the deterministic renderer."""

import json
from pathlib import Path

import pytest

from templateer.renderer import RenderError, render_template
from templateer.template import Template


@pytest.fixture
def pyproject_template():
    """Load the pyproject-uv test template."""
    return Template(Path("templates/pyproject-uv"))


@pytest.fixture
def fastapi_model(pyproject_template):
    """Create a validated model from the FastAPI example input."""
    input_data = json.loads(
        (Path("templates/pyproject-uv/examples/fastapi.input.json")).read_text()
    )
    schema_class = pyproject_template.get_schema_class()
    return schema_class(**input_data)


def test_render_from_valid_model(pyproject_template, fastapi_model):
    """Rendering from a valid model produces expected output."""
    rendered = pyproject_template.render(fastapi_model)
    assert "[project]" in rendered
    assert "name =" in rendered
    assert "fastapi-app" in rendered
    assert "dependencies" in rendered


def test_render_matches_expected_output(pyproject_template, fastapi_model):
    """Rendered output matches the example output fixture exactly."""
    expected_output = Path("templates/pyproject-uv/examples/fastapi.output.toml").read_text()
    rendered = pyproject_template.render(fastapi_model)
    assert rendered.strip() == expected_output.strip()


def test_deterministic_rendering(pyproject_template, fastapi_model):
    """Same model + same template = same output (determinism)."""
    output1 = pyproject_template.render(fastapi_model)
    output2 = pyproject_template.render(fastapi_model)
    assert output1 == output2


def test_render_with_minimal_model(pyproject_template):
    """A minimal model with only required fields renders successfully."""
    schema_class = pyproject_template.get_schema_class()
    model = schema_class(
        project_name="minimal-project",
        python_version="3.12",
    )
    rendered = pyproject_template.render(model)
    assert "minimal-project" in rendered
    assert "[project]" in rendered
    # Optional sections should be omitted
    assert "[dependency-groups]" not in rendered
    assert "[tool.ruff]" not in rendered
    assert "[tool.pytest]" not in rendered


def test_render_with_all_fields(pyproject_template):
    """A model with all optional fields filled renders them correctly."""
    schema_class = pyproject_template.get_schema_class()
    model = schema_class(
        project_name="full-project",
        project_description="A full-featured project",
        python_version="3.12",
        project_type="library",
        dependencies=[{"name": "requests", "version": ">=2.0"}],
        dev_dependencies=[{"name": "pytest", "version": ">=8.0"}],
        ruff={
            "line_length": 120,
            "target_version": "py313",
            "select": ["E", "F"],
            "ignore": ["E501"],
        },
        pytest={
            "testpaths": ["tests", "integration_tests"],
            "addopts": ["-v"],
        },
    )
    rendered = pyproject_template.render(model)
    assert "full-project" in rendered
    assert "A full-featured project" in rendered
    assert "[dependency-groups]" in rendered
    assert "requests>=2.0" in rendered
    assert "[tool.ruff]" in rendered
    assert "line-length = 120" in rendered
    assert "[tool.pytest.ini_options]" in rendered


def test_strict_mode_raises_on_undefined_variable(tmp_path):
    """In strict mode, referencing undefined variables raises RenderError."""
    template_file = tmp_path / "template.j2"
    template_file.write_text("Hello {{ undefined_var }}!")
    with pytest.raises(RenderError, match="undefined value"):
        render_template(template_file, {"name": "World"}, strict=True)


def test_lenient_mode_does_not_raise_on_undefined(tmp_path):
    """In lenient mode, undefined variables render as empty strings."""
    template_file = tmp_path / "template.j2"
    template_file.write_text("Hello {{ undefined_var }}!")
    result = render_template(template_file, {"name": "World"}, strict=False)
    # lenient mode should produce empty string for undefined
    assert "Hello " in result


def test_render_with_dict_model(tmp_path):
    """Rendering works with a plain dict as model."""
    template_file = tmp_path / "template.j2"
    template_file.write_text("name = {{ project_name }}")
    result = render_template(
        template_file,
        {"project_name": '"test-project"'},
        strict=True,
    )
    assert 'name = "test-project"' in result


def test_missing_template_file_raises(tmp_path):
    """Raising from a non-existent template file raises RenderError."""
    missing = tmp_path / "does-not-exist.j2"
    with pytest.raises(RenderError, match="Template file not found"):
        render_template(missing, {"key": "value"})


def test_render_respects_model_dump_json_mode(pyproject_template):
    """model_dump(mode='json') converts types to JSON-safe values."""
    input_data = json.loads(
        (Path("templates/pyproject-uv/examples/fastapi.input.json")).read_text()
    )
    schema_class = pyproject_template.get_schema_class()
    model = schema_class(**input_data)

    # Verify our model dump gives JSON-safe output
    dumped = model.model_dump(mode="json")
    assert isinstance(dumped["project_name"], str)
    assert isinstance(dumped["python_version"], str)

    rendered = pyproject_template.render(model)
    assert "[project]" in rendered


def test_render_template_directly(pyproject_template, fastapi_model):
    """render_template function works directly with a Template instance."""
    template_path = pyproject_template.resolve_path(pyproject_template.metadata.renderer.file)
    rendered = render_template(
        template_path,
        fastapi_model,
        strict=pyproject_template.metadata.strict_context,
    )
    assert "fastapi-app" in rendered
    assert "fastapi>=0.115.0" in rendered


def test_render_with_none_optional_field(pyproject_template):
    """A model where optional fields are None renders gracefully."""
    schema_class = pyproject_template.get_schema_class()
    model = schema_class(
        project_name="no-optional",
        python_version="3.12",
        project_description=None,  # explicit None
        ruff=None,
        pytest=None,
    )
    rendered = pyproject_template.render(model)
    assert "no-optional" in rendered
    # None should not trigger an error in strict mode if template uses {% if ... %}
    assert "no-optional" in rendered


def test_render_jinja_syntax_errors_raise(tmp_path):
    """Invalid Jinja syntax raises RenderError."""
    template_file = tmp_path / "template.j2"
    template_file.write_text("[project]\n{% if invalid... %}x")
    with pytest.raises(RenderError):
        render_template(template_file, {"name": "test"}, strict=True)


def test_render_edge_case_empty_strings(tmp_path):
    """Render handles empty string field values correctly."""
    template_file = tmp_path / "template.j2"
    template_file.write_text("value={{ val }}")
    result = render_template(template_file, {"val": ""}, strict=True)
    assert "value=" in result
