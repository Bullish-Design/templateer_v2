"""The Templateer generation pipeline.

  1. Resolve the template.
  2. Ask the LLM to fill its schema.
  3. Render deterministically from the validated model.
  4. Validate the rendered artifact.

Every failure returns a GenerationResult.  Nothing escapes as an exception —
that promise is either total or worthless.
"""

import logging
from typing import Any

from pydantic_ai.exceptions import UnexpectedModelBehavior, UserError

from templateer.catalog import TemplateCatalog
from templateer.generator import generate_model
from templateer.renderer import RenderError
from templateer.result import FailureReason, GenerationRequest, GenerationResult
from templateer.template import Template, TemplateLoadError, TemplateNotFoundError
from templateer.validators import effective_validators, validate_output

logger = logging.getLogger(__name__)


def generate(catalog: TemplateCatalog, request: GenerationRequest) -> GenerationResult:
    """Run the pipeline, retrying while the failure is worth retrying.

    This is the single entry point.  Set ``request.max_attempts = 1`` to
    disable retries; there is no separate retry function to keep in sync.
    """
    result = _attempt(catalog, request, attempt=1)
    while result.can_retry:
        reason = result.failure_reason
        assert reason is not None  # can_retry implies a retryable reason
        logger.info(
            "retrying %s after %s (attempt %d/%d)",
            request.template_name, reason.value,
            result.attempt + 1, request.max_attempts,
        )
        result = _attempt(catalog, request, attempt=result.attempt + 1)
    return result


def _attempt(
    catalog: TemplateCatalog, request: GenerationRequest, attempt: int
) -> GenerationResult:
    def fail(reason: FailureReason, detail: str, **extra: Any) -> GenerationResult:
        return GenerationResult(
            request=request, attempt=attempt,
            failure_reason=reason, error_detail=detail, **extra,
        )

    # 1 — Resolve ---------------------------------------------------------
    try:
        template: Template = catalog.get(request.template_name)
    except (TemplateNotFoundError, TemplateLoadError) as e:
        return fail(FailureReason.NO_TEMPLATE, str(e))

    output_path = template.metadata.output.path
    region = template.metadata.output.region
    # A region template's failures are grounded in the page it lives on;
    # ``path`` is informational for regions, ``region.page`` is the anchor.
    if template.metadata.output.kind == "region" and region is not None:
        output_path = region.page

    # 2 — Generate the model ----------------------------------------------
    #
    # The boundary is deliberately broad.  Schema loading, prompt loading,
    # context serialization, Agent construction and the network call all live
    # in here, and every one of them has been observed to raise.
    try:
        model = generate_model(
            template,
            user_request=request.user_request,
            context=request.context,
            model_name=request.model_name,
        )
    except UserError as e:
        # Missing API key, unknown model id — caller misconfiguration.
        return fail(FailureReason.CONFIG_ERROR, str(e), output_path=output_path)
    except UnexpectedModelBehavior as e:
        # Output-validation retries exhausted inside pydantic-ai.
        return fail(FailureReason.MODEL_VALIDATION_FAILED, str(e), output_path=output_path)
    except TemplateLoadError as e:
        return fail(FailureReason.NO_TEMPLATE, str(e), output_path=output_path)
    except Exception as e:
        logger.debug("model generation failed", exc_info=True)
        return fail(FailureReason.LLM_FAILED, f"{type(e).__name__}: {e}",
                    output_path=output_path)

    model_dump = model.model_dump(mode="json")

    # 3 — Render ----------------------------------------------------------
    try:
        artifact = template.render(model)
    except RenderError as e:
        return fail(FailureReason.RENDER_FAILED, str(e),
                    output_path=output_path, model=model_dump)

    # 4 — Validate the artifact -------------------------------------------
    errors, warnings = validate_output(
        artifact,
        template.metadata.output.language,
        effective_validators(
            template.metadata.output, template.metadata.validators
        ),
    )
    if errors:
        return fail(FailureReason.OUTPUT_VALIDATION_FAILED, "; ".join(errors),
                    output_path=output_path, model=model_dump)

    return GenerationResult(
        request=request, attempt=attempt, output_path=output_path,
        model=model_dump, artifact=artifact, warnings=warnings,
    )
