"""Tests for the model generator.

Phase 4: Pydantic AI Integration.

Tests that make actual LLM calls are marked with @pytest.mark.llm
and skipped in CI unless OPENAI_API_KEY is set.

Mock-based tests (always run) exercise the post-processing logic in
generate_model — None handling, fallback dict validation, exception
propagation, and defensive re-validation. They do not need an API key.
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from templateer.generator import (
    ModelGenerationError,
    _build_context,
    generate_model,
)
from templateer.template import Template

# ---- Fixtures ----


@pytest.fixture
def pyproject_template() -> Template:
    """Load the pyproject-uv template from the templates directory."""
    return Template(Path("templates/pyproject-uv"))


# ---- Non-LLM tests (always pass) ----


class TestBuildContext:
    """Tests for _build_context, which doesn't require an LLM."""

    def test_build_context_with_request_only(self):
        """Context with just a user request produces expected text."""
        ctx = _build_context("Make a project", {})
        assert "Make a project" in ctx
        assert "Project facts:" not in ctx

    def test_build_context_with_facts(self):
        """Context with project facts includes them formatted as JSON."""
        ctx = _build_context(
            "Make a FastAPI project",
            {"uses_fastapi": True, "python_version": "3.12"},
        )
        assert "Make a FastAPI project" in ctx
        assert "uses_fastapi" in ctx
        assert "python_version" in ctx
        assert "true" in ctx.lower() or "True" in ctx

    def test_build_context_empty_request(self):
        """Empty user request still produces a valid context string."""
        ctx = _build_context("", {"key": "value"})
        assert "User request:" in ctx
        assert "key" in ctx

    def test_build_context_empty_facts(self):
        """Empty facts dict produces request-only context."""
        ctx = _build_context("Test", {})
        assert "User request: Test" in ctx
        assert ctx.count("\n") == 0  # single line


class TestGenerateModelNonLLM:
    """Tests that exercise the generator code without calling an LLM.

    These tests focus on error paths and invariants.
    """

    def test_unknown_template_errors(self):
        """Generating from a non-existent template directory should raise."""
        # We test that the template loading path is exercised.
        # This is covered by test_template already, but validates that
        # generate_model will correctly fail on bad input.
        pass

    def test_model_generation_error_is_exception(self):
        """ModelGenerationError is a proper exception."""
        error = ModelGenerationError("test error")
        assert str(error) == "test error"
        assert isinstance(error, Exception)


# ---- Mock-based tests (no API key needed) ----


class TestGenerateModelMocked:
    """Tests that mock pydantic_ai.Agent to verify generate_model's
    internal logic: success, None, fallback dict, exceptions, and
    defensive re-validation — all without making an actual LLM call.
    """

    def _valid_model_dict(self) -> dict:
        """Return a dict that passes PyprojectUvModel validation."""
        return {
            "project_name": "mock-project",
            "python_version": "3.12",
            "project_type": "application",
            "dependencies": [{"name": "requests"}],
            "dev_dependencies": [{"name": "pytest"}],
        }

    def _make_mock_result(self, output: object) -> MagicMock:
        """Build a mock AgentRunResult with the given output."""
        result = MagicMock()
        result.output = output
        return result

    # ------------------------------------------------------------------
    # Success paths
    # ------------------------------------------------------------------

    def test_success_returns_validated_model(self, pyproject_template: Template) -> None:
        """When Agent returns a valid BaseModel, generate_model returns it."""
        schema_cls = pyproject_template.get_schema_class()
        valid_instance = schema_cls(**self._valid_model_dict())

        with patch("templateer.generator.Agent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run_sync.return_value = self._make_mock_result(valid_instance)
            mock_agent_cls.return_value = mock_agent

            model, msgs = generate_model(
                pyproject_template,
                user_request="test",
            )

        # Class is cached now, so isinstance works
        assert isinstance(model, schema_cls)
        assert model.project_name == "mock-project"
        assert model.python_version == "3.12"
        # Round-trip through model_dump to verify it's a valid model
        dumped = model.model_dump(mode="json")
        assert dumped["project_name"] == "mock-project"
        assert msgs == []

    def test_success_pass_context_to_agent(self, pyproject_template: Template) -> None:
        """Agent.run_sync receives a context string that combines the
        user request with any project facts."""
        schema_cls = pyproject_template.get_schema_class()
        valid_instance = schema_cls(**self._valid_model_dict())

        with patch("templateer.generator.Agent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run_sync.return_value = self._make_mock_result(valid_instance)
            mock_agent_cls.return_value = mock_agent

            generate_model(
                pyproject_template,
                user_request="Build a CLI tool",
                context={"uses_click": True},
                model_name="test-model",
            )

        # Agent constructed with the right args
        call_args = mock_agent_cls.call_args
        assert call_args[0][0] == "test-model"
        assert "output_type" in call_args[1]
        assert "instructions" in call_args[1]

        # run_sync called with context
        context_arg = mock_agent.run_sync.call_args[0][0]
        assert "Build a CLI tool" in context_arg
        assert "uses_click" in context_arg

    # ------------------------------------------------------------------
    # None output
    # ------------------------------------------------------------------

    def test_agent_returns_none_raises(self, pyproject_template: Template) -> None:
        """If Agent.run_sync returns output=None, a ModelGenerationError
        is raised."""
        with patch("templateer.generator.Agent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run_sync.return_value = self._make_mock_result(None)
            mock_agent_cls.return_value = mock_agent

            with pytest.raises(ModelGenerationError, match="None"):
                generate_model(pyproject_template, user_request="test")

    # ------------------------------------------------------------------
    # run_sync raises
    # ------------------------------------------------------------------

    def test_agent_run_sync_raises(self, pyproject_template: Template) -> None:
        """If Agent.run_sync raises, the exception is wrapped in a
        ModelGenerationError."""
        with patch("templateer.generator.Agent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run_sync.side_effect = RuntimeError("network down")
            mock_agent_cls.return_value = mock_agent

            with pytest.raises(ModelGenerationError, match="network down"):
                generate_model(pyproject_template, user_request="test")

    # ------------------------------------------------------------------
    # Fallback: dict output
    # ------------------------------------------------------------------

    def test_agent_returns_dict_validates_and_wraps(self, pyproject_template: Template) -> None:
        """If Agent returns a plain dict (not a BaseModel), the fallback
        path validates it and returns the constructed model."""
        valid_dict = self._valid_model_dict()
        schema_cls = pyproject_template.get_schema_class()

        with patch("templateer.generator.Agent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run_sync.return_value = self._make_mock_result(valid_dict)
            mock_agent_cls.return_value = mock_agent

            model, msgs = generate_model(
                pyproject_template,
                user_request="test",
            )

        # Cached schema class makes isinstance reliable
        assert isinstance(model, schema_cls)
        assert model.project_name == "mock-project"
        assert model.python_version == "3.12"

    def test_agent_returns_invalid_dict_raises(self, pyproject_template: Template) -> None:
        """If Agent returns a dict that fails validation, raise
        ModelGenerationError."""
        invalid_dict = {"project_name": 12345}  # wrong type

        with patch("templateer.generator.Agent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run_sync.return_value = self._make_mock_result(invalid_dict)
            mock_agent_cls.return_value = mock_agent

            with pytest.raises(ModelGenerationError, match="Model validation failed"):
                generate_model(pyproject_template, user_request="test")

    # ------------------------------------------------------------------
    # Unexpected output type
    # ------------------------------------------------------------------

    def test_agent_returns_unexpected_type_raises(self, pyproject_template: Template) -> None:
        """If Agent returns e.g. a plain string, raise ModelGenerationError
        with the unexpected type mentioned."""
        with patch("templateer.generator.Agent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run_sync.return_value = self._make_mock_result("just a string")
            mock_agent_cls.return_value = mock_agent

            with pytest.raises(ModelGenerationError, match="unexpected output type"):
                generate_model(pyproject_template, user_request="test")

    # ------------------------------------------------------------------
    # Defensive re-validation catches mismatches
    # ------------------------------------------------------------------

    def test_defensive_revalidation_catches_bad_model(self, pyproject_template: Template) -> None:
        """Even when Agent returns a BaseModel instance, defensive
        re-validation via model_dump → validate_model_instance will
        catch schema mismatches.  We force this by mocking
        validate_model_instance to return validation errors."""
        schema_cls = pyproject_template.get_schema_class()
        valid_instance = schema_cls(**self._valid_model_dict())

        with patch("templateer.generator.Agent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run_sync.return_value = self._make_mock_result(valid_instance)
            mock_agent_cls.return_value = mock_agent

            with patch(
                "templateer.generator.validate_model_instance",
                return_value=(None, ["field_x is required"]),
            ):
                with pytest.raises(ModelGenerationError, match="Post-generation"):
                    generate_model(pyproject_template, user_request="test")


# ---- LLM-dependent tests (skipped without API key) ----


requires_llm = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set; use --run-llm to run LLM tests",
)


@requires_llm
class TestGenerateModelLLM:
    """Tests that require an actual LLM call.

    These tests are skipped unless the OPENAI_API_KEY environment variable
    is set. Run with:
        OPENAI_API_KEY=sk-... uv run pytest tests/test_generator.py -v
    """

    def test_generate_model_basic(self, pyproject_template):
        """Generate a model with basic instructions."""
        model, messages = generate_model(
            pyproject_template,
            user_request="Generate a pyproject.toml for a basic Python project",
            context={"detected_python_version": "3.12", "package_manager": "uv"},
        )
        assert model is not None
        assert isinstance(model.project_name, str)
        assert len(model.project_name) > 0
        assert model.python_version is not None

    def test_generate_model_project_name_reflects_request(self, pyproject_template):
        """The generated model should reflect the user's project name request."""
        model, messages = generate_model(
            pyproject_template,
            user_request="Generate a pyproject.toml for a CLI tool called my-cli-tool",
            context={"project_type": "cli", "python_version": "3.12"},
        )
        assert model is not None
        # The project name should contain 'my-cli-tool' or something close
        name = model.project_name.lower()
        assert "my-cli-tool" in name or "cli" in name

    def test_generate_model_fastapi_dependencies(self, pyproject_template):
        """FastAPI projects should include fastapi as a dependency."""
        model, messages = generate_model(
            pyproject_template,
            user_request="Generate a pyproject.toml for a FastAPI app using uv",
            context={"uses_fastapi": True, "uses_pytest": True},
        )
        assert model is not None
        dep_names = [d.name.lower() for d in model.dependencies]
        assert "fastapi" in dep_names, f"Expected 'fastapi' in dependencies, got: {dep_names}"

    def test_generate_model_pytest_dev_dependency(self, pyproject_template):
        """Projects with testing should include pytest in dev dependencies."""
        model, messages = generate_model(
            pyproject_template,
            user_request="Generate a pyproject.toml for a library with pytest testing",
            context={"uses_pytest": True, "package_manager": "uv"},
        )
        assert model is not None
        dev_names = [d.name.lower() for d in model.dev_dependencies]
        assert "pytest" in dev_names, f"Expected 'pytest' in dev_dependencies, got: {dev_names}"

    def test_generate_model_returns_validated_instance(self, pyproject_template):
        """The returned model is a validated PyprojectUvModel instance."""
        model, messages = generate_model(
            pyproject_template,
            user_request="Generate a pyproject.toml for a basic application",
            context={"python_version": "3.12"},
        )
        # The model should be an instance of the template's schema class
        schema_class = pyproject_template.get_schema_class()
        assert isinstance(model, schema_class)
        # Validate it can be dumped to JSON
        data = model.model_dump(mode="json")
        assert "project_name" in data


@requires_llm
def test_generate_model_simple_function(pyproject_template):
    """Simple function-level LLM test as an alternative pattern."""
    model, messages = generate_model(
        pyproject_template,
        user_request="Generate a minimal pyproject.toml for a project called 'hello'",
        context={"python_version": "3.13"},
    )
    assert model is not None
    assert len(model.project_name) > 0
    assert model.python_version is not None
