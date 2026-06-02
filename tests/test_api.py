"""Tests for the Python API (TemplateRegistry).

Covers discovery, rendering, validation, and error handling.
LLM-dependent tests are skipped unless OPENAI_API_KEY is set.
"""

import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from templateer.api import TemplateRegistry
from templateer.models import TemplateGenerationResult
from templateer.template import TemplateNotFoundError

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

has_api_key = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY required for LLM pipeline tests",
)


@pytest.fixture
def registry() -> TemplateRegistry:
    """Registry loaded with the project's bundled test templates."""
    return TemplateRegistry.from_paths([Path("templates")])


@pytest.fixture
def fastapi_input() -> dict:
    """The FastAPI example input fixture as a dict."""
    return json.loads((Path("templates/pyproject-uv/examples/fastapi.input.json")).read_text())


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    """Tests for TemplateRegistry construction."""

    def test_from_paths_creates_registry(self, registry: TemplateRegistry) -> None:
        """from_paths() returns a TemplateRegistry with templates loaded."""
        assert isinstance(registry, TemplateRegistry)
        assert len(registry) > 0

    def test_from_paths_with_strings(self) -> None:
        """Paths can be passed as strings, not just Path objects."""
        r = TemplateRegistry.from_paths(["templates"])
        assert len(r) > 0

    def test_from_paths_empty_dir(self, tmp_path: Path) -> None:
        """Loading from an empty directory is safe."""
        empty = tmp_path / "empty"
        empty.mkdir()
        r = TemplateRegistry.from_paths([empty])
        assert len(r) == 0

    def test_from_paths_missing_dir(self) -> None:
        """Loading from a non-existent directory is safe (nothing loaded)."""
        r = TemplateRegistry.from_paths(["/nonexistent/path/xyz"])
        assert len(r) == 0

    def test_from_paths_multiple_paths(self, tmp_path: Path) -> None:
        """Loading from multiple paths accumulates templates."""
        empty = tmp_path / "empty"
        empty.mkdir()
        r = TemplateRegistry.from_paths(["templates", empty])
        assert len(r) > 0

    def test_from_paths_does_not_fail_on_unknown_path(self) -> None:
        """Loading from a non-existent path is safe."""
        r = TemplateRegistry.from_paths(["/no/such/path"])
        assert len(r) == 0


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class TestDiscovery:
    """Tests for template listing and lookup."""

    def test_list_templates(self, registry: TemplateRegistry) -> None:
        """list_templates returns all available templates."""
        templates = registry.list_templates()
        assert len(templates) > 0
        assert any(t.name == "pyproject-uv" for t in templates)

    def test_get_template(self, registry: TemplateRegistry) -> None:
        """get_template retrieves a specific template by exact name."""
        template = registry.get_template("pyproject-uv")
        assert template.name == "pyproject-uv"
        assert len(template.description) > 0

    def test_get_template_unknown_raises(self, registry: TemplateRegistry) -> None:
        """get_template raises TemplateNotFoundError for unknown names."""
        with pytest.raises(TemplateNotFoundError):
            registry.get_template("nonexistent-template-xyz")

    def test_has_template_true(self, registry: TemplateRegistry) -> None:
        """has_template returns True for existing templates."""
        assert registry.has_template("pyproject-uv")

    def test_has_template_false(self, registry: TemplateRegistry) -> None:
        """has_template returns False for unknown templates."""
        assert not registry.has_template("nonexistent")

    def test_contains_operator(self, registry: TemplateRegistry) -> None:
        """'in' operator works for template existence check."""
        assert "pyproject-uv" in registry
        assert "nonexistent" not in registry

    def test_len(self, registry: TemplateRegistry) -> None:
        """len(registry) returns the number of loaded templates."""
        assert len(registry) > 0
        assert len(registry) == len(registry.list_templates())

    def test_repr(self, registry: TemplateRegistry) -> None:
        """repr() includes the template count."""
        r = repr(registry)
        assert "TemplateRegistry" in r
        assert str(len(registry)) in r


# ---------------------------------------------------------------------------
# LLM-free rendering (render_from_model)
# ---------------------------------------------------------------------------


class TestRenderFromModel:
    """Tests for the render_from_model method (no LLM required)."""

    def test_render_from_model_produces_output(
        self, registry: TemplateRegistry, fastapi_input: dict
    ) -> None:
        """render_from_model renders a template from a valid model dict."""
        rendered = registry.render_from_model("pyproject-uv", fastapi_input)
        assert "[project]" in rendered
        assert "fastapi-app" in rendered

    def test_render_from_model_matches_fixture(self, registry: TemplateRegistry) -> None:
        """render_from_model output matches the expected output fixture."""
        input_data = json.loads(
            (Path("templates/pyproject-uv/examples/fastapi.input.json")).read_text()
        )
        expected = (Path("templates/pyproject-uv/examples/fastapi.output.toml")).read_text()

        rendered = registry.render_from_model("pyproject-uv", input_data)
        assert rendered.strip() == expected.strip()

    def test_render_from_model_minimal_data(self, registry: TemplateRegistry) -> None:
        """render_from_model works with minimal model data (only required fields)."""
        minimal = {
            "project_name": "minimal-project",
            "python_version": "3.12",
        }
        rendered = registry.render_from_model("pyproject-uv", minimal)
        assert "minimal-project" in rendered
        assert "[project]" in rendered

    def test_render_from_model_unknown_template(self, registry: TemplateRegistry) -> None:
        """render_from_model raises on unknown template name."""
        with pytest.raises(TemplateNotFoundError):
            registry.render_from_model("nonexistent", {})

    def test_render_from_model_invalid_data(self, registry: TemplateRegistry) -> None:
        """render_from_model raises ValidationError on invalid model data."""
        with pytest.raises(ValidationError):
            registry.render_from_model("pyproject-uv", {})

    def test_render_from_model_deterministic(
        self, registry: TemplateRegistry, fastapi_input: dict
    ) -> None:
        """render_from_model is deterministic: same input → same output."""
        out1 = registry.render_from_model("pyproject-uv", fastapi_input)
        out2 = registry.render_from_model("pyproject-uv", fastapi_input)
        assert out1 == out2

    def test_render_from_model_returns_str(
        self, registry: TemplateRegistry, fastapi_input: dict
    ) -> None:
        """render_from_model returns a string."""
        rendered = registry.render_from_model("pyproject-uv", fastapi_input)
        assert isinstance(rendered, str)
        assert len(rendered) > 0


# ---------------------------------------------------------------------------
# Output validation (validate_artifact)
# ---------------------------------------------------------------------------


class TestValidateArtifact:
    """Tests for the validate_artifact method."""

    def test_validate_valid_toml(self, registry: TemplateRegistry) -> None:
        """Valid TOML passes validation."""
        errors = registry.validate_artifact(
            "pyproject-uv",
            '[project]\nname = "test"\n',
        )
        assert errors == []

    def test_validate_invalid_toml(self, registry: TemplateRegistry) -> None:
        """Invalid TOML produces validation errors."""
        errors = registry.validate_artifact(
            "pyproject-uv",
            "not valid toml {{{",
        )
        assert len(errors) > 0

    def test_validate_unknown_template(self, registry: TemplateRegistry) -> None:
        """validate_artifact raises on unknown template name."""
        with pytest.raises(TemplateNotFoundError):
            registry.validate_artifact("nonexistent", "text")

    def test_validate_valid_fixture_output(self, registry: TemplateRegistry) -> None:
        """The FastAPI output fixture passes validation."""
        output = (Path("templates/pyproject-uv/examples/fastapi.output.toml")).read_text()
        errors = registry.validate_artifact("pyproject-uv", output)
        assert errors == []

    def test_validate_invalid_toml_balanced_braces(self, registry: TemplateRegistry) -> None:
        """Invalid TOML with unbalanced braces fails validation."""
        errors = registry.validate_artifact("pyproject-uv", "[project\nname = [[[")
        assert len(errors) > 0


# ---------------------------------------------------------------------------
# Model-only generation (LLM required)
# ---------------------------------------------------------------------------


class TestGenerateModel:
    """Tests for the generate_model method (LLM required)."""

    @has_api_key
    def test_generate_model_produces_pydantic_instance(self, registry: TemplateRegistry) -> None:
        """generate_model returns a validated Pydantic model."""
        model = registry.generate_model(
            template_name="pyproject-uv",
            user_request=("Generate config for a minimal Python project using uv with pytest."),
        )
        from pydantic import BaseModel

        assert isinstance(model, BaseModel)
        assert model.project_name is not None  # type: ignore[attr-defined]
        assert len(model.project_name) > 0  # type: ignore[attr-defined]

    @has_api_key
    def test_generate_model_with_context(self, registry: TemplateRegistry) -> None:
        """generate_model accepts and uses project context."""
        model = registry.generate_model(
            template_name="pyproject-uv",
            user_request="Generate a FastAPI project pyproject.toml",
            context={"uses_fastapi": True, "python_version": "3.12"},
        )
        assert model.project_name is not None  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Full generation (LLM required)
# ---------------------------------------------------------------------------


class TestGenerate:
    """Tests for the generate method (full pipeline, LLM required)."""

    @has_api_key
    def test_generate_returns_result(self, registry: TemplateRegistry) -> None:
        """generate returns a TemplateGenerationResult."""
        result = registry.generate(
            template_name="pyproject-uv",
            user_request=(
                "Generate a pyproject.toml for a minimal Python project "
                "using uv, Python 3.12, with pytest and ruff."
            ),
            context={"python_version": "3.12"},
        )
        assert isinstance(result, TemplateGenerationResult)
        assert result.template_name == "pyproject-uv"
        assert isinstance(result.model, dict)
        assert len(result.rendered) > 0
        assert "[project]" in result.rendered

    @has_api_key
    def test_generate_result_model_is_dict(self, registry: TemplateRegistry) -> None:
        """generate result.model is a dict matching the schema."""
        result = registry.generate(
            template_name="pyproject-uv",
            user_request="Generate a pyproject.toml for a minimal Python project.",
        )
        assert isinstance(result.model, dict)
        assert "project_name" in result.model


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Tests for error handling across API methods."""

    def test_get_template_nonexistent_raises_typed_error(self, registry: TemplateRegistry) -> None:
        """get_template raises TemplateNotFoundError (not a generic exception)."""
        with pytest.raises(TemplateNotFoundError) as exc_info:
            registry.get_template("nonexistent-template-abc")
        assert "nonexistent-template-abc" in str(exc_info.value)

    def test_get_template_empty_string(self, registry: TemplateRegistry) -> None:
        """get_template with empty string raises TemplateNotFoundError."""
        with pytest.raises(TemplateNotFoundError):
            registry.get_template("")

    def test_get_template_whitespace(self, registry: TemplateRegistry) -> None:
        """get_template with whitespace-only name raises."""
        with pytest.raises(TemplateNotFoundError):
            registry.get_template("   ")

    def test_render_from_model_nonexistent_template_raises(
        self, registry: TemplateRegistry
    ) -> None:
        """render_from_model raises on unknown template."""
        with pytest.raises(TemplateNotFoundError):
            registry.render_from_model("does-not-exist", {"name": "test"})

    def test_validate_artifact_nonexistent_template_raises(
        self, registry: TemplateRegistry
    ) -> None:
        """validate_artifact raises on unknown template."""
        with pytest.raises(TemplateNotFoundError):
            registry.validate_artifact("does-not-exist", "[project]")


# ---------------------------------------------------------------------------
# Integration: cross-method consistency
# ---------------------------------------------------------------------------


class TestIntegration:
    """Integration tests that exercise multiple API methods together."""

    def test_render_then_validate(self, registry: TemplateRegistry, fastapi_input: dict) -> None:
        """Artifact produced by render_from_model passes validate_artifact."""
        rendered = registry.render_from_model("pyproject-uv", fastapi_input)
        errors = registry.validate_artifact("pyproject-uv", rendered)
        assert errors == []

    def test_template_from_list_can_be_used(
        self, registry: TemplateRegistry, fastapi_input: dict
    ) -> None:
        """A template obtained via list_templates can be used for rendering."""
        templates = registry.list_templates()
        pyproject = next(t for t in templates if t.name == "pyproject-uv")
        schema_class = pyproject.get_schema_class()
        model = schema_class(**fastapi_input)
        rendered = pyproject.render(model)
        assert "[project]" in rendered

    def test_fastapi_fixture_full_roundtrip(self, registry: TemplateRegistry) -> None:
        """FastAPI fixture: input → render → validate → matches expected output."""
        input_data = json.loads(
            (Path("templates/pyproject-uv/examples/fastapi.input.json")).read_text()
        )
        expected = (Path("templates/pyproject-uv/examples/fastapi.output.toml")).read_text()

        rendered = registry.render_from_model("pyproject-uv", input_data)
        assert rendered.strip() == expected.strip()

        errors = registry.validate_artifact("pyproject-uv", rendered)
        assert errors == []

    def test_registry_from_empty_paths_is_empty(self, tmp_path: Path) -> None:
        """A registry from an empty dir has zero templates."""
        empty = tmp_path / "empty"
        empty.mkdir()
        r = TemplateRegistry.from_paths([empty])
        assert len(r) == 0
        assert r.list_templates() == []
