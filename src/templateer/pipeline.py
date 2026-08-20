"""The Templateer generation pipeline.

  1. Resolve the template.
  2. Ask the LLM to fill its schema.
  3. Render deterministically from the validated model.
  4. Validate the rendered artifact.

Every failure returns a GenerationResult.  Nothing escapes as an exception —
that promise is either total or worthless.  ``_attempt_async`` therefore wraps
all four steps in one boundary, not only the steps whose failures somebody
thought of.

The async path is the only implementation.  ``generate`` runs it with
``asyncio.run``.
"""

import asyncio
import logging
from typing import Any

from pydantic_ai.exceptions import UnexpectedModelBehavior, UserError

from templateer.catalog import TemplateCatalog
from templateer.generator import generate_model_async
from templateer.renderer import RenderError
from templateer.result import FailureReason, GenerationRequest, GenerationResult
from templateer.template import Template, TemplateLoadError, TemplateNotFoundError
from templateer.validators import (
    check_round_trip,
    effective_validators,
    validate_output,
)

logger = logging.getLogger(__name__)

# Wait this long before a retry of LLM_FAILED, doubled per attempt.  A provider
# error is the one retryable failure that time alone can fix; the other two
# are fixed by the repair loop, which changes the prompt, so waiting there buys
# nothing.  Patch this to 0 to make a test run without sleeping.
RETRY_BACKOFF_SECONDS: float = 1.0


def generate(catalog: TemplateCatalog, request: GenerationRequest) -> GenerationResult:
    """Run the pipeline synchronously.

    This wrapper is for callers that own the thread.  Inside a running event
    loop ``asyncio.run`` raises; call :func:`generate_async` there.
    """
    return asyncio.run(generate_async(catalog, request))


async def generate_async(
    catalog: TemplateCatalog, request: GenerationRequest
) -> GenerationResult:
    """Run the pipeline, retrying while the failure is worth retrying.

    This is the single implementation.  Set ``request.max_attempts = 1`` to
    disable retries; there is no separate retry function to keep in sync.

    A retry feeds the previous attempt's ``error_detail`` back to the model.
    Re-asking with the same prompt costs the same tokens and asks the same
    question, so the loop repairs instead of repeating (§A9).
    """
    result = await _attempt_async(catalog, request, attempt=1)
    while result.can_retry:
        reason = result.failure_reason
        assert reason is not None  # can_retry implies a retryable reason
        logger.info(
            "retrying %s after %s (attempt %d/%d)",
            request.template_name, reason.value,
            result.attempt + 1, request.max_attempts,
        )
        if reason is FailureReason.LLM_FAILED:
            await asyncio.sleep(RETRY_BACKOFF_SECONDS * 2 ** (result.attempt - 1))
        result = await _attempt_async(
            catalog, request,
            attempt=result.attempt + 1,
            prior_failure=result.error_detail,
        )
    return result


async def _attempt_async(
    catalog: TemplateCatalog,
    request: GenerationRequest,
    attempt: int,
    prior_failure: str | None = None,
) -> GenerationResult:
    """Run one whole attempt and turn every outcome into a result.

    This is the boundary the module docstring promises.  ``_run_attempt``
    classifies the failures it knows; this function catches the rest.  Before
    it existed, a renderer file outside the template root raised
    ``TemplateLoadError`` through ``generate()`` and into the caller.

    ``slot`` carries what the attempt learned before it failed — the output
    path, the kind, the region, the token counts, the model, the warnings — so
    a boundary failure still reports them.
    """
    slot: dict[str, Any] = {}
    try:
        return await _run_attempt(catalog, request, attempt, prior_failure, slot)
    except TemplateLoadError as e:
        # A broken template: a renderer or prompt file outside the root, or a
        # schema class that will not load.  Another attempt reads the same
        # files, so this is permanent.
        return _fail(request, attempt, FailureReason.NO_TEMPLATE, str(e), **slot)
    except Exception as e:
        logger.debug("pipeline attempt raised", exc_info=True)
        return _fail(
            request, attempt, FailureReason.INTERNAL_ERROR,
            f"{type(e).__name__}: {e}", **slot,
        )


async def _run_attempt(
    catalog: TemplateCatalog,
    request: GenerationRequest,
    attempt: int,
    prior_failure: str | None,
    slot: dict[str, Any],
) -> GenerationResult:
    """Run the four steps.  Fill *slot* as each step learns something."""

    def fail(reason: FailureReason, detail: str) -> GenerationResult:
        return _fail(request, attempt, reason, detail, **slot)

    # 1 — Resolve ---------------------------------------------------------
    try:
        template: Template = catalog.get(request.template_name)
    except TemplateNotFoundError as e:
        return fail(FailureReason.NO_TEMPLATE, str(e))

    output = template.metadata.output
    slot["output_path"] = output.path
    slot["kind"] = output.kind
    # ``region`` exists on RegionOutput only, so bind it before the test:
    # a full_file output has no such attribute.
    region = getattr(output, "region", None)
    slot["region"] = region
    # A region template's failures are grounded in the page it lives on;
    # ``path`` is informational for regions, ``region.page`` is the anchor.
    if region is not None:
        slot["output_path"] = region.page

    # 2 — Generate the model ----------------------------------------------
    #
    # The boundary is deliberately broad.  Schema loading, prompt loading,
    # context serialization, Agent construction and the network call all live
    # in here, and every one of them has been observed to raise.
    try:
        model, usage = await generate_model_async(
            template,
            user_request=request.user_request,
            context=request.context,
            model_name=request.model_name,
            prior_failure=prior_failure,
        )
    except UserError as e:
        # Missing API key, unknown model id — caller misconfiguration.
        return fail(FailureReason.CONFIG_ERROR, str(e))
    except UnexpectedModelBehavior as e:
        # Output-validation retries exhausted inside pydantic-ai.
        return fail(FailureReason.MODEL_VALIDATION_FAILED, str(e))
    except TemplateLoadError as e:
        return fail(FailureReason.NO_TEMPLATE, str(e))
    except Exception as e:
        logger.debug("model generation failed", exc_info=True)
        return fail(FailureReason.LLM_FAILED, f"{type(e).__name__}: {e}")

    slot["usage"] = usage
    slot["model"] = model.model_dump(mode="json")

    # 3 — Render ----------------------------------------------------------
    try:
        artifact = template.render(model)
    except RenderError as e:
        return fail(FailureReason.RENDER_FAILED, str(e))

    # 4 — Validate the artifact -------------------------------------------
    errors, warnings = validate_output(
        artifact,
        output.language,
        effective_validators(output, template.metadata.validators),
    )
    # Warnings are most useful when something else also failed, so they reach
    # the result either way (§B6).
    slot["warnings"] = warnings
    # The escaper protects the artifact's lexical structure; this check
    # protects its semantic structure.  A str field that reaches the artifact
    # as a bool is a wrong artifact, so the finding is fatal (§A1).
    errors = [*errors, *check_round_trip(artifact, output.language, slot["model"])]
    if errors:
        return fail(FailureReason.OUTPUT_VALIDATION_FAILED, "; ".join(errors))

    return GenerationResult(
        request=request, attempt=attempt, artifact=artifact, **slot
    )


def _fail(
    request: GenerationRequest,
    attempt: int,
    reason: FailureReason,
    detail: str,
    **extra: Any,
) -> GenerationResult:
    """Build a failed result."""
    return GenerationResult(
        request=request, attempt=attempt,
        failure_reason=reason, error_detail=detail, **extra,
    )
