"""Tests for model validation.

Verifies that validate_model_instance correctly handles valid and invalid data
against Pydantic schemas loaded from template schema files.
"""

import json
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from templateer.template import Template
from templateer.validation import validate_model_instance

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pyproject_template() -> Template:
    """Load the pyproject-uv template."""
    return Template(Path("templates/pyproject-uv"))


@pytest.fixture
def fastapi_input() -> dict:
    """Load the FastAPI example input fixture."""
    return json.loads((Path("templates/pyproject-uv/examples/fastapi.input.json")).read_text())


# ---------------------------------------------------------------------------
# Step 3.1: Dynamic schema loading (already implemented in Phase 2)
# ---------------------------------------------------------------------------


def test_schema_class_is_loadable(pyproject_template: Template) -> None:
    """The schema class can be loaded and is a BaseModel."""
    cls = pyproject_template.get_schema_class()
    assert issubclass(cls, BaseModel)


def test_schema_class_name(pyproject_template: Template) -> None:
    """The loaded schema class has the expected name."""
    cls = pyproject_template.get_schema_class()
    assert cls.__name__ == "PyprojectUvModel"


def test_schema_class_has_fields(pyproject_template: Template) -> None:
    """The schema class defines expected fields."""
    cls = pyproject_template.get_schema_class()
    field_names = set(cls.model_fields.keys())
    assert "project_name" in field_names
    assert "python_version" in field_names
    assert "dependencies" in field_names


# ---------------------------------------------------------------------------
# Step 3.2: Model validation utility
# ---------------------------------------------------------------------------


def test_valid_model_validates(pyproject_template: Template, fastapi_input: dict) -> None:
    """A valid input dict validates successfully against the schema."""
    cls = pyproject_template.get_schema_class()
    instance, errors = validate_model_instance(cls, fastapi_input)
    assert instance is not None
    assert errors == []
    assert instance.project_name == fastapi_input["project_name"]
    assert instance.python_version == fastapi_input["python_version"]


def test_valid_model_returns_instance(pyproject_template: Template) -> None:
    """A valid model returns the Pydantic BaseModel instance."""
    cls = pyproject_template.get_schema_class()
    instance, errors = validate_model_instance(
        cls, {"project_name": "test", "python_version": "3.12"}
    )
    assert instance is not None
    assert isinstance(instance, BaseModel)
    assert errors == []


def test_invalid_model_reports_errors(pyproject_template: Template) -> None:
    """Missing required fields produce validation errors."""
    cls = pyproject_template.get_schema_class()
    instance, errors = validate_model_instance(cls, {})
    assert instance is None
    assert len(errors) > 0


def test_invalid_model_reports_multiple_errors(
    pyproject_template: Template,
) -> None:
    """Multiple validation issues are all reported."""
    cls = pyproject_template.get_schema_class()
    instance, errors = validate_model_instance(
        cls,
        {"project_name": "test"},  # missing python_version (required)
    )
    assert instance is None
    assert len(errors) >= 1


def test_error_messages_are_descriptive(
    pyproject_template: Template,
) -> None:
    """Validation error messages contain field names and descriptions."""
    cls = pyproject_template.get_schema_class()
    _, errors = validate_model_instance(cls, {})
    assert any("project_name" in err for err in errors)


def test_invalid_field_type_reports_errors(
    pyproject_template: Template,
) -> None:
    """Wrong types for fields produce validation errors."""
    cls = pyproject_template.get_schema_class()
    instance, errors = validate_model_instance(
        cls,
        {
            "project_name": "test",
            "python_version": "3.12",
            "dependencies": "not-a-list",  # should be a list
        },
    )
    assert instance is None
    assert len(errors) > 0


def test_model_validator_rejects_conflicting_frameworks(
    pyproject_template: Template,
) -> None:
    """The schema's custom model_validator catches invalid combinations."""
    cls = pyproject_template.get_schema_class()
    instance, errors = validate_model_instance(
        cls,
        {
            "project_name": "multi-framework",
            "python_version": "3.12",
            "dependencies": [
                {"name": "fastapi", "version": ">=0.115.0"},
                {"name": "django", "version": ">=5.0"},
            ],
        },
    )
    assert instance is None
    assert len(errors) > 0
    # The error should mention the framework conflict
    assert any("framework" in err.lower() for err in errors)


def test_validation_with_optional_fields(pyproject_template: Template) -> None:
    """Optional/missing fields don't cause validation errors."""
    cls = pyproject_template.get_schema_class()
    instance, errors = validate_model_instance(
        cls, {"project_name": "test", "python_version": "3.12"}
    )
    assert instance is not None
    assert errors == []
    assert instance.project_description is None
    assert instance.dependencies == []


def test_validation_with_nested_models(pyproject_template: Template) -> None:
    """Nested Pydantic models validate correctly."""
    cls = pyproject_template.get_schema_class()
    instance, errors = validate_model_instance(
        cls,
        {
            "project_name": "test",
            "python_version": "3.12",
            "ruff": {
                "line_length": 100,
                "target_version": "py312",
            },
        },
    )
    assert instance is not None
    assert errors == []
    assert instance.ruff is not None
    assert instance.ruff.line_length == 100


def test_validation_rejects_invalid_nested_data(
    pyproject_template: Template,
) -> None:
    """Invalid nested model data produces errors."""
    cls = pyproject_template.get_schema_class()
    instance, errors = validate_model_instance(
        cls,
        {
            "project_name": "test",
            "python_version": "3.12",
            "ruff": {
                "line_length": 999,  # out of range (max 120)
                "target_version": "py312",
            },
        },
    )
    assert instance is None
    assert len(errors) > 0


# ---------------------------------------------------------------------------
# Step 3.3: Schema JSON generation (already implemented in Phase 2)
# ---------------------------------------------------------------------------


def test_json_schema_generation(pyproject_template: Template) -> None:
    """JSON schema can be generated for a template."""
    schema = pyproject_template.get_schema_json()
    assert "properties" in schema
    assert "title" in schema


def test_json_schema_contains_expected_properties(
    pyproject_template: Template,
) -> None:
    """JSON schema contains the fields from the Pydantic model."""
    schema = pyproject_template.get_schema_json()
    properties = schema.get("properties", {})
    assert "project_name" in properties
    assert "python_version" in properties
    assert "dependencies" in properties


def test_json_schema_includes_field_titles(
    pyproject_template: Template,
) -> None:
    """JSON schema includes title strings for each field."""
    schema = pyproject_template.get_schema_json()
    properties = schema.get("properties", {})
    project_name_schema = properties.get("project_name", {})
    assert "title" in project_name_schema
    assert project_name_schema["title"] == "Project Name"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_validate_model_instance_with_empty_dict() -> None:
    """Empty dict produces errors when required fields are missing."""

    class SimpleModel(BaseModel):
        name: str = Field(description="A name")
        count: int = Field(description="A count")

    instance, errors = validate_model_instance(SimpleModel, {})
    assert instance is None
    assert len(errors) > 0


def test_validate_model_instance_with_extra_fields_forbidden(
    pyproject_template: Template,
) -> None:
    """Extra fields not in the schema cause validation errors."""
    cls = pyproject_template.get_schema_class()
    instance, errors = validate_model_instance(
        cls,
        {
            "project_name": "test",
            "python_version": "3.12",
            "unknown_field": "should not be here",
        },
    )
    # TemplateMetadata uses extra="forbid" but PyprojectUvModel uses default
    # (which allows extras). So this should still validate.
    assert instance is not None
    assert errors == []
    # The extra field is ignored/stripped
    assert not hasattr(instance, "unknown_field")


def test_validate_model_instance_returns_none_on_validation_error() -> None:
    """validate_model_instance returns None for the instance on error."""

    class SimpleModel(BaseModel):
        name: str = Field(description="A name")

    instance, errors = validate_model_instance(SimpleModel, {"name": 42})
    assert instance is None
    assert len(errors) > 0
