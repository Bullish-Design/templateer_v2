"""The complete Templateer generation pipeline.

Wires together template resolution, LLM model generation,
deterministic rendering, and output validation into a single
end-to-end flow that mirrors the Gen entity lifecycle from the
generation.allium spec.

Pipeline flow:
  1. Resolve the named template from the catalog.
  2. Ask the LLM to fill the template's Pydantic schema.
  3. Render the template with the validated model.
  4. Validate the rendered output (syntax check).
  5. Return the Generation with status READY and the artifact.

On any failure the Generation is returned with status FAILED
and an appropriate FailureReason.
"""

from typing import Any

from templateer.catalog import TemplateCatalog
from templateer.generation import (
    FailureReason,
    Generation,
    GenerationStatus,
)
from templateer.generator import ModelGenerationError, generate_model
from templateer.renderer import RenderError
from templateer.template import TemplateNotFoundError
from templateer.validators import validate_output


class PipelineError(Exception):
    """Raised when the generation pipeline encounters an unrecoverable error."""

    def __init__(self, message: str, reason: FailureReason) -> None:
        self.reason = reason
        super().__init__(message)


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------


def run_pipeline(
    catalog: TemplateCatalog,
    template_name: str,
    user_request: str,
    context: dict[str, Any] | None = None,
    model_name: str = "openai:gpt-4.1-mini",
    max_retries: int = 3,
) -> Generation:
    """Execute the full generation pipeline.

    Implements the Generation lifecycle from the Allium spec:
    submitted → generating → ready (success) or failed (error).

    On failure, the caller can inspect ``gen.can_retry`` and call
    :func:`retry_generation` to re-attempt.

    Args:
        catalog: The template catalog to search.
        template_name: Exact template directory name.
        user_request: What the user/agent wants to generate.
        context: Optional project facts to help the LLM.
        model_name: The LLM model identifier.
        max_retries: Maximum LLM retry attempts (default 3).

    Returns:
        A :class:`Generation` entity with the final status and
        artifact text (if successful) or failure details.
    """
    gen = Generation(
        requested_path="",
        template_name=template_name,
    )

    # ----------------------------------------------------------------
    # Step 1 — Resolve template
    # ----------------------------------------------------------------
    try:
        template = catalog.get(template_name)
        gen.matched_template = template.name
    except TemplateNotFoundError:
        gen.status = GenerationStatus.FAILED
        gen.failure_reason = FailureReason.NO_TEMPLATE
        return gen

    gen.requested_path = template.metadata.outputs[0].path

    # ----------------------------------------------------------------
    # Step 2 — Generate the Pydantic model via LLM
    # ----------------------------------------------------------------
    gen.status = GenerationStatus.GENERATING

    try:
        model, _messages = generate_model(
            template,
            user_request=user_request,
            context=context,
            model_name=model_name,
            max_retries=max_retries,
        )
    except ModelGenerationError as e:
        gen.status = GenerationStatus.FAILED
        gen.failure_reason = FailureReason.LLM_FAILED
        gen.artifact = str(e)
        return gen

    # ----------------------------------------------------------------
    # Step 3 — Render the artifact deterministically
    # ----------------------------------------------------------------
    try:
        rendered = template.render(model)
    except RenderError as e:
        gen.status = GenerationStatus.FAILED
        gen.failure_reason = FailureReason.RENDER_FAILED
        gen.artifact = str(e)
        return gen

    # ----------------------------------------------------------------
    # Step 4 — Validate the rendered output
    # ----------------------------------------------------------------
    output_language = template.metadata.outputs[0].language
    output_validators = [v.model_dump() for v in template.metadata.validators]

    errors = validate_output(rendered, output_language, output_validators)

    if errors:
        gen.status = GenerationStatus.FAILED
        gen.failure_reason = FailureReason.OUTPUT_VALIDATION_FAILED
        gen.artifact = "\n".join(errors)
        return gen

    # ----------------------------------------------------------------
    # Success
    # ----------------------------------------------------------------
    gen.status = GenerationStatus.READY
    gen.artifact = rendered
    return gen


# ---------------------------------------------------------------------------
# Retry support
# ---------------------------------------------------------------------------


def retry_generation(
    catalog: TemplateCatalog,
    gen: Generation,
    user_request: str,
    context: dict[str, Any] | None = None,
) -> Generation:
    """Retry a previously failed generation.

    Only generations with ``can_retry == True`` may be retried.
    The retry count is carried forward and incremented.

    Args:
        catalog: The template catalog.
        gen: The failed generation to retry.
        user_request: Original user request.
        context: Original context.

    Returns:
        A new :class:`Generation` entity with the retry result.

    Raises:
        ValueError: If the generation cannot be retried (not FAILED
            or retry count exhausted).
    """
    if not gen.can_retry:
        raise ValueError(
            f"Generation cannot be retried: status={gen.status.value}, retries={gen.retry_count}"
        )

    next_attempt = gen.retry_count + 1

    result = run_pipeline(
        catalog=catalog,
        template_name=gen.template_name,
        user_request=user_request,
        context=context,
        max_retries=next_attempt,  # ← carry forward the retry budget
    )

    result.retry_count = next_attempt
    return result
