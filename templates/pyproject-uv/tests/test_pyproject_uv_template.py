"""Template-specific tests for pyproject-uv.

These tests verify the template without making any LLM calls.
They exercise the schema, prompt, renderer, and fixture roundtrip.
"""

import json
from pathlib import Path

import pytest

from templateer.template import Template

TEMPLATE_DIR = Path(__file__).resolve().parent.parent


def test_schema_loads():
    """Schema module loads and contains the expected class."""
    t = Template(TEMPLATE_DIR)
    cls = t.get_schema_class()
    assert cls.__name__ == "PyprojectUvModel"


def test_schema_is_pydantic_base_model():
    """Schema class is a Pydantic BaseModel subclass."""
    t = Template(TEMPLATE_DIR)
    cls = t.get_schema_class()
    from pydantic import BaseModel

    assert issubclass(cls, BaseModel)


def test_schema_has_required_fields():
    """Schema has project_name and python_version as required fields."""
    t = Template(TEMPLATE_DIR)
    cls = t.get_schema_class()
    field_names = list(cls.model_fields.keys())
    assert "project_name" in field_names
    assert "python_version" in field_names


def test_prompt_loads():
    """Prompt file loads and mentions the schema class."""
    t = Template(TEMPLATE_DIR)
    prompt = t.load_prompt()
    assert len(prompt) > 0
    assert "PyprojectUvModel" in prompt


def test_prompt_contains_rules():
    """Prompt includes guidance for the LLM."""
    t = Template(TEMPLATE_DIR)
    prompt = t.load_prompt()
    assert "uv" in prompt.lower()
    assert "pydantic" in prompt.lower() or "schema" in prompt.lower()


def test_metadata_parses():
    """Metadata YAML parses with expected values."""
    t = Template(TEMPLATE_DIR)
    assert t.name == "pyproject-uv"
    assert t.metadata.output.language == "toml"
    assert t.metadata.output.path == "pyproject.toml"


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
    assert "requires-python" in rendered
    assert ">=" in rendered


def test_minimal_model_is_valid_toml():
    """A minimal model produces valid TOML."""
    t = Template(TEMPLATE_DIR)
    cls = t.get_schema_class()
    model = cls(
        project_name="valid-toml-project",
        python_version="3.12",
    )
    rendered = t.render(model)

    from templateer.validators import validate_output

    errors, _ = validate_output(rendered, "toml")
    assert errors == [], f"TOML validation failed: {errors}"


def test_full_fastapi_model_is_valid_toml():
    """The FastAPI fixture produces valid TOML."""
    t = Template(TEMPLATE_DIR)

    input_data = json.loads(
        (TEMPLATE_DIR / "examples" / "fastapi.input.json").read_text()
    )
    cls = t.get_schema_class()
    model = cls(**input_data)
    rendered = t.render(model)

    from templateer.validators import validate_output

    errors, _ = validate_output(rendered, "toml")
    assert errors == []


def test_rendering_is_deterministic():
    """Same model produces identical output every time."""
    t = Template(TEMPLATE_DIR)
    cls = t.get_schema_class()
    model = cls(
        project_name="deterministic-test",
        python_version="3.12",
        project_description="Test determinism",
        dependencies=[{"name": "pydantic", "version": ">=2.0"}],
    )

    rendered1 = t.render(model)
    rendered2 = t.render(model)
    rendered3 = t.render(model)

    assert rendered1 == rendered2 == rendered3


def test_web_framework_validator_rejects_multiple_frameworks():
    """The model validator rejects having both fastapi and flask."""
    t = Template(TEMPLATE_DIR)
    cls = t.get_schema_class()

    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="web framework"):
        cls(
            project_name="bad-combo",
            python_version="3.12",
            dependencies=[
                {"name": "fastapi"},
                {"name": "flask"},
            ],
        )


def test_extra_dependency_fields_are_ignored():
    """Pydantic ignores extra fields by default (they don't cause errors)."""
    t = Template(TEMPLATE_DIR)
    cls = t.get_schema_class()

    # Extra fields are silently ignored in Pydantic v2 (default: extra='ignore')
    model = cls(
        project_name="extra-fields",
        python_version="3.12",
        non_existent_field="will be ignored",
    )
    assert model.model_dump()["project_name"] == "extra-fields"
    # The extra field is not in model_fields
    assert "non_existent_field" not in cls.model_fields


def test_output_fixture_file_exists():
    """Each .input.json file has a matching .output.* file."""
    examples_dir = TEMPLATE_DIR / "examples"
    for input_file in sorted(examples_dir.glob("*.input.json")):
        stem = input_file.name.replace(".input.json", "")
        output_files = list(examples_dir.glob(f"{stem}.output.*"))
        assert len(output_files) == 1, (
            f"Missing output fixture for {input_file}"
        )


def test_json_schema_generates():
    """The template's schema can produce a JSON schema."""
    t = Template(TEMPLATE_DIR)
    json_schema = t.get_schema_json()
    assert json_schema["title"] == "PyprojectUvModel"
    properties = json_schema["properties"]
    assert "project_name" in properties
    assert properties["project_name"]["type"] == "string"
    assert "python_version" in properties
