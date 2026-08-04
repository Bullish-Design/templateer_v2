"""Tests for the generation pipeline.

Covers the single pipeline: template resolution → model generation →
rendering → output validation.  LLM-call tests are skipped unless
OPENAI_API_KEY is set.
"""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from templateer.catalog import TemplateCatalog
from templateer.pipeline import generate
from templateer.result import FailureReason, GenerationRequest, GenerationResult
from templateer.validators import validate_output

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def catalog() -> TemplateCatalog:
    """Catalog loaded with the project's bundled test templates."""
    c = TemplateCatalog()
    c.load_from_paths([Path("templates")])
    return c


@pytest.fixture
def fastapi_input() -> dict:
    """The FastAPI example input fixture as a dict."""
    return json.loads((Path("templates/pyproject-uv/examples/fastapi.input.json")).read_text())


# ---------------------------------------------------------------------------
# Template resolution (no LLM needed)
# ---------------------------------------------------------------------------


class TestTemplateResolution:
    """Tests for the template resolution step of the pipeline."""

    def test_pipeline_template_not_found(self, catalog: TemplateCatalog) -> None:
        """Pipeline fails cleanly when template doesn't exist."""
        result = generate(catalog, GenerationRequest(
            template_name="nonexistent-template-xyz", user_request="test"))
        assert not result.succeeded
        assert result.failure_reason == FailureReason.NO_TEMPLATE
        assert result.artifact is None

    def test_pipeline_no_template_is_terminal(self, catalog: TemplateCatalog) -> None:
        """A failed generation is not retryable — permanent failure."""
        result = generate(catalog, GenerationRequest(
            template_name="nonexistent", user_request="test"))
        assert not result.can_retry

    def test_pipeline_no_template_is_not_succeeded(self, catalog: TemplateCatalog) -> None:
        result = generate(catalog, GenerationRequest(
            template_name="nonexistent", user_request="test"))
        assert not result.succeeded


# ---------------------------------------------------------------------------
# GenerationResult
# ---------------------------------------------------------------------------


class TestGenerationResult:
    """Tests for the GenerationResult entity's real logic."""

    def test_success_invariant_requires_artifact(self) -> None:
        """A result with no failure reason must carry an artifact."""
        with pytest.raises(ValueError, match="artifact"):
            GenerationResult(request=GenerationRequest(
                template_name="t", user_request="u"))

    def test_failure_invariant_forbids_artifact(self) -> None:
        """A failed result must not carry an artifact."""
        with pytest.raises(ValueError, match="artifact"):
            GenerationResult(
                request=GenerationRequest(template_name="t", user_request="u"),
                failure_reason=FailureReason.LLM_FAILED,
                artifact="oops",
            )

    def test_can_retry_retryable_reason(self) -> None:
        result = GenerationResult(
            request=GenerationRequest(template_name="t", user_request="u", max_attempts=3),
            failure_reason=FailureReason.LLM_FAILED,
            error_detail="timeout",
            attempt=1,
        )
        assert result.can_retry

    def test_can_retry_false_for_permanent_reason(self) -> None:
        result = GenerationResult(
            request=GenerationRequest(template_name="t", user_request="u", max_attempts=3),
            failure_reason=FailureReason.CONFIG_ERROR,
            error_detail="missing key",
            attempt=1,
        )
        assert not result.can_retry

    def test_can_retry_false_when_exhausted(self) -> None:
        result = GenerationResult(
            request=GenerationRequest(template_name="t", user_request="u", max_attempts=2),
            failure_reason=FailureReason.LLM_FAILED,
            error_detail="timeout",
            attempt=2,
        )
        assert not result.can_retry

    def test_can_retry_false_when_succeeded(self) -> None:
        result = GenerationResult(
            request=GenerationRequest(template_name="t", user_request="u"),
            artifact="[project]",
        )
        assert not result.can_retry
        assert result.succeeded


# ---------------------------------------------------------------------------
# FailureReason enum
# ---------------------------------------------------------------------------


class TestFailureReason:
    """Tests for the FailureReason enumeration."""

    def test_all_reasons_exist(self) -> None:
        reasons = set(FailureReason)
        expected = {
            FailureReason.NO_TEMPLATE,
            FailureReason.CONFIG_ERROR,
            FailureReason.MODEL_VALIDATION_FAILED,
            FailureReason.RENDER_FAILED,
            FailureReason.OUTPUT_VALIDATION_FAILED,
            FailureReason.LLM_FAILED,
        }
        assert reasons == expected

    def test_reason_string_values(self) -> None:
        assert FailureReason.NO_TEMPLATE == "no_template"
        assert FailureReason.RENDER_FAILED == "render_failed"
        assert FailureReason.OUTPUT_VALIDATION_FAILED == "output_validation_failed"
        assert FailureReason.CONFIG_ERROR == "config_error"


# ---------------------------------------------------------------------------
# Retry behavior (no LLM needed)
# ---------------------------------------------------------------------------


class TestRetryBehavior:
    """Tests for the pipeline's internal retry loop."""

    def test_permanent_failure_does_not_retry(self, catalog: TemplateCatalog) -> None:
        """NO_TEMPLATE must not burn max_attempts."""
        result = generate(catalog, GenerationRequest(
            template_name="nope", user_request="x", max_attempts=5))
        assert result.failure_reason is FailureReason.NO_TEMPLATE
        assert result.attempt == 1

    def test_config_error_does_not_retry(self, catalog: TemplateCatalog, monkeypatch) -> None:
        """CONFIG_ERROR (missing API key) must not burn max_attempts."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        result = generate(catalog, GenerationRequest(
            template_name="pyproject-uv", user_request="x", max_attempts=5))
        assert result.failure_reason is FailureReason.CONFIG_ERROR
        assert result.attempt == 1

    def test_retryable_failure_retries_up_to_max(self, catalog: TemplateCatalog) -> None:
        """LLM_FAILED is retried until max_attempts is reached."""
        with patch("templateer.pipeline.generate_model",
                   side_effect=RuntimeError("network")):
            result = generate(catalog, GenerationRequest(
                template_name="pyproject-uv", user_request="x", max_attempts=3))
        assert result.failure_reason is FailureReason.LLM_FAILED
        assert result.attempt == 3

    def test_model_output_retries_do_not_grow_across_attempts(
        self, catalog: TemplateCatalog
    ) -> None:
        """Regression: max_retries used to be passed the accumulated pipeline
        attempt count, inflating the LLM budget 3 -> 4 -> 5."""
        with patch("templateer.pipeline.generate_model",
                   side_effect=RuntimeError("network")) as mock_gen:
            generate(catalog, GenerationRequest(
                template_name="pyproject-uv", user_request="x", max_attempts=3))
        # generate_model now takes no retries argument; each call uses the
        # fixed internal MODEL_OUTPUT_RETRIES.
        for call in mock_gen.call_args_list:
            assert "retries" not in call.kwargs


# ---------------------------------------------------------------------------
# End-to-end (LLM-free via stubbed generator)
# ---------------------------------------------------------------------------


class TestEndToEndWithoutLLM:
    """Integration-style tests exercising the full pipeline with a stubbed
    generate_model — no LLM API key required."""

    def _stub_generate_model(self):
        def _make_valid_model(template, **kwargs):
            data = json.loads(
                Path("templates/pyproject-uv/examples/fastapi.input.json").read_text()
            )
            return template.get_schema_class().model_validate(data)
        return _make_valid_model

    def test_full_pipeline_resolve_render_validate(
        self, catalog: TemplateCatalog, fastapi_input: dict
    ) -> None:
        """Resolve template, build model, render, validate output — no LLM."""
        template = catalog.get("pyproject-uv")
        cls = template.get_schema_class()
        model = cls(**fastapi_input)

        rendered = template.render(model)
        assert "[project]" in rendered
        assert "fastapi-app" in rendered

        # Output validation
        errors, warnings = validate_output(rendered, "toml")
        assert errors == []
        assert warnings == []

    def test_invalid_model_rendering_fails_validation(self, catalog: TemplateCatalog) -> None:
        """A rendered artifact that fails the parse check is an error."""
        errors, _ = validate_output("not valid toml {{{", "toml")
        assert len(errors) > 0

    def test_pipeline_success_with_stubbed_generator(self, catalog: TemplateCatalog) -> None:
        """The full pipeline succeeds when generate_model returns a model."""
        with patch("templateer.pipeline.generate_model",
                   side_effect=self._stub_generate_model()):
            result = generate(catalog, GenerationRequest(
                template_name="pyproject-uv", user_request="make a thing"))
        assert result.succeeded
        assert result.artifact is not None
        assert "[project]" in result.artifact
        assert result.model is not None
        assert result.model["project_name"] == "fastapi-app"

    def test_render_failure_returns_failed_result(self, catalog: TemplateCatalog) -> None:
        """A render failure is captured as RENDER_FAILED, not raised."""
        from templateer.renderer import RenderError

        with patch("templateer.pipeline.generate_model",
                   side_effect=self._stub_generate_model()):
            with patch("templateer.template.Template.render",
                       side_effect=RenderError("boom")):
                result = generate(catalog, GenerationRequest(
                    template_name="pyproject-uv", user_request="x"))
        assert not result.succeeded
        assert result.failure_reason is FailureReason.RENDER_FAILED


# ---------------------------------------------------------------------------
# LLM-dependent pipeline test (guarded by API key)
# ---------------------------------------------------------------------------


has_api_key = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY required for LLM pipeline tests",
)


@has_api_key
def test_pipeline_generate_end_to_end(catalog: TemplateCatalog) -> None:
    """Live smoke test: full pipeline with a real LLM call."""
    result = generate(catalog, GenerationRequest(
        template_name="pyproject-uv",
        user_request=(
            "Generate a pyproject.toml for a minimal Python project "
            "using uv with pytest and ruff, Python 3.12."
        ),
        context={"detected_python_version": "3.12"},
    ))
    if result.succeeded:
        assert result.artifact is not None
        assert "[project]" in result.artifact
    else:
        # LLM failure is data, not a crash: the result must still be coherent.
        assert result.failure_reason is not None
        assert result.artifact is None
        assert result.error_detail


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestPipelineEdgeCases:
    """Edge-case and robustness tests for the pipeline."""

    def test_empty_context_is_handled(self, catalog: TemplateCatalog) -> None:
        """Pipeline accepts empty/None context."""
        result = generate(catalog, GenerationRequest(
            template_name="nonexistent-template", user_request="test", context={}))
        assert not result.succeeded

    def test_unicode_user_request(self, catalog: TemplateCatalog) -> None:
        """Pipeline handles unicode in user_request without crashing."""
        result = generate(catalog, GenerationRequest(
            template_name="nonexistent-template",
            user_request="Generer un projet avec des caractères spéciaux: café, naïve",
            context={"emoji": "🚀✨"},
        ))
        assert not result.succeeded

    def test_whitespace_only_template_name_is_rejected(self, catalog: TemplateCatalog) -> None:
        """Template with whitespace-only name fails with NO_TEMPLATE."""
        result = generate(catalog, GenerationRequest(
            template_name="   ", user_request="test"))
        assert result.failure_reason == FailureReason.NO_TEMPLATE

    def test_pipeline_returns_result_not_none(self, catalog: TemplateCatalog) -> None:
        """Pipeline always returns a GenerationResult instance."""
        result = generate(catalog, GenerationRequest(
            template_name="nonexistent-template", user_request="test"))
        assert isinstance(result, GenerationResult)
