"""Tests for the model generator.

Mock-based tests (always run) exercise generate_model's success path and
context building. They do not need an API key.  Tests that make actual LLM
calls are marked @pytest.mark.llm and skipped unless OPENAI_API_KEY is set.
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from templateer.generator import build_context, generate_model
from templateer.template import Template

# ---- Fixtures ----


@pytest.fixture
def pyproject_template() -> Template:
    """Load the pyproject-uv template from the templates directory."""
    return Template(Path("templates/pyproject-uv"))


# ---- Non-LLM tests (always pass) ----


class TestBuildContext:
    """Tests for build_context, which doesn't require an LLM."""

    def test_build_context_with_request_only(self):
        """Context with just a user request produces expected text."""
        ctx = build_context("Make a project", {})
        assert "Make a project" in ctx
        assert "Project facts:" not in ctx

    def test_build_context_with_facts(self):
        """Context with project facts includes them formatted as JSON."""
        ctx = build_context(
            "Make a FastAPI project",
            {"uses_fastapi": True, "python_version": "3.12"},
        )
        assert "Make a FastAPI project" in ctx
        assert "uses_fastapi" in ctx
        assert "python_version" in ctx

    def test_build_context_empty_request(self):
        """Empty user request still produces a valid context string."""
        ctx = build_context("", {"key": "value"})
        assert "User request:" in ctx
        assert "key" in ctx

    def test_build_context_empty_facts(self):
        """Empty facts dict produces request-only context."""
        ctx = build_context("Test", {})
        assert "User request: Test" in ctx
        assert ctx.count("\n") == 0  # single line

    def test_build_context_stringifies_path_facts(self):
        """Path objects in context are stringified, not rejected."""
        ctx = build_context("Test", {"root": Path("/tmp/proj")})
        assert "/tmp/proj" in ctx


# ---- Mock-based tests (no API key needed) ----


class TestGenerateModelMocked:
    """Tests that mock pydantic_ai.Agent to verify generate_model's
    success path and context handling — without an actual LLM call.
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

    def test_success_returns_validated_model(self, pyproject_template: Template) -> None:
        """When Agent returns a valid BaseModel, generate_model returns it."""
        schema_cls = pyproject_template.get_schema_class()
        valid_instance = schema_cls(**self._valid_model_dict())

        with patch("templateer.generator.Agent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run_sync.return_value = self._make_mock_result(valid_instance)
            mock_agent_cls.return_value = mock_agent

            model = generate_model(pyproject_template, user_request="test")

        assert isinstance(model, schema_cls)
        data = model.model_dump()
        assert data["project_name"] == "mock-project"
        assert data["python_version"] == "3.12"

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
        assert call_args[1]["retries"] == 2  # internal LLM output budget

        # run_sync called with context
        context_arg = mock_agent.run_sync.call_args[0][0]
        assert "Build a CLI tool" in context_arg
        assert "uses_click" in context_arg

    def test_example_fixture_reaches_the_prompt(self, pyproject_template: Template) -> None:
        """The few-shot example fixture is appended to the context text."""
        schema_cls = pyproject_template.get_schema_class()
        valid_instance = schema_cls(**self._valid_model_dict())

        with patch("templateer.generator.Agent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run_sync.return_value = self._make_mock_result(valid_instance)
            mock_agent_cls.return_value = mock_agent

            generate_model(pyproject_template, user_request="test")

        context_arg = mock_agent.run_sync.call_args[0][0]
        assert "Example of a well-formed response:" in context_arg
        assert '"project_name"' in context_arg  # fixture JSON content

    def test_run_sync_raises_propagates(self, pyproject_template: Template) -> None:
        """Exceptions from Agent.run_sync propagate; the pipeline classifies."""
        with patch("templateer.generator.Agent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run_sync.side_effect = RuntimeError("network down")
            mock_agent_cls.return_value = mock_agent

            with pytest.raises(RuntimeError, match="network down"):
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
        model = generate_model(
            pyproject_template,
            user_request="Generate a pyproject.toml for a basic Python project",
            context={"detected_python_version": "3.12", "package_manager": "uv"},
        )
        data = model.model_dump()
        assert isinstance(data["project_name"], str)
        assert len(data["project_name"]) > 0
        assert data["python_version"] is not None

    def test_generate_model_project_name_reflects_request(self, pyproject_template):
        """The generated model should reflect the user's project name request."""
        model = generate_model(
            pyproject_template,
            user_request="Generate a pyproject.toml for a CLI tool called my-cli-tool",
            context={"project_type": "cli", "python_version": "3.12"},
        )
        # The project name should contain 'my-cli-tool' or something close
        name = model.model_dump()["project_name"].lower()
        assert "my-cli-tool" in name or "cli" in name

    def test_generate_model_fastapi_dependencies(self, pyproject_template):
        """FastAPI projects should include fastapi as a dependency."""
        model = generate_model(
            pyproject_template,
            user_request="Generate a pyproject.toml for a FastAPI app using uv",
            context={"uses_fastapi": True, "uses_pytest": True},
        )
        dep_names = [d["name"].lower() for d in model.model_dump()["dependencies"]]
        assert "fastapi" in dep_names, f"Expected 'fastapi' in dependencies, got: {dep_names}"

    def test_generate_model_pytest_dev_dependency(self, pyproject_template):
        """Projects with testing should include pytest in dev dependencies."""
        model = generate_model(
            pyproject_template,
            user_request="Generate a pyproject.toml for a library with pytest testing",
            context={"uses_pytest": True, "package_manager": "uv"},
        )
        dev_names = [d["name"].lower() for d in model.model_dump()["dev_dependencies"]]
        assert "pytest" in dev_names, f"Expected 'pytest' in dev_dependencies, got: {dev_names}"

    def test_generate_model_returns_validated_instance(self, pyproject_template):
        """The returned model is a validated PyprojectUvModel instance."""
        model = generate_model(
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
    model = generate_model(
        pyproject_template,
        user_request="Generate a minimal pyproject.toml for a project called 'hello'",
        context={"python_version": "3.13"},
    )
    data = model.model_dump()
    assert len(data["project_name"]) > 0
    assert data["python_version"] is not None
