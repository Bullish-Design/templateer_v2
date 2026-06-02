"""Generation entity — the lifecycle of producing an artifact.

Tracks a single artifact generation from submission through completion
or failure, with retry support in accordance with the Allium spec.

Allium spec alignment:
  entity Generation {
      status: submitted | generating | ready | failed
      transitions status {
          submitted -> generating
          submitted -> failed
          generating -> ready
          generating -> failed
          terminal: ready, failed
      }
      failure_reason: FailureReason?
  }
"""

from enum import Enum

from pydantic import BaseModel, Field


class FailureReason(str, Enum):
    """Why a generation failed.

    Each variant corresponds to a specific failure point in the
    generation pipeline, as described in the generation.allium spec.
    """

    NO_TEMPLATE = "no_template"
    """The requested template name was not found in the catalog."""

    MODEL_VALIDATION_FAILED = "model_validation_failed"
    """The LLM-produced model failed Pydantic validation."""

    RENDER_FAILED = "render_failed"
    """The Jinja template rendering step failed."""

    OUTPUT_VALIDATION_FAILED = "output_validation_failed"
    """The rendered artifact failed output validation."""

    LLM_FAILED = "llm_failed"
    """The LLM call itself failed (network error, timeout, etc.)."""


class GenerationStatus(str, Enum):
    """Lifecycle status of a generation.

    The status transitions follow a simple state machine:
      submitted → generating → ready (success)
      submitted → generating → failed  (any error)
      submitted → failed               (no template found)
    """

    SUBMITTED = "submitted"
    """The generation has been submitted but not yet started."""

    GENERATING = "generating"
    """The generation is in progress (LLM call, rendering, validation)."""

    READY = "ready"
    """The generation completed successfully; artifact is available."""

    FAILED = "failed"
    """The generation failed; see failure_reason for details."""


class Generation(BaseModel):
    """Tracks a single artifact generation request.

    This entity models the full lifecycle: from initial template lookup
    through LLM generation, rendering, and output validation.  Failed
    generations can be retried a limited number of times.

    Allium spec invariant:
      - If status is READY, artifact must be non-null.
      - If status is FAILED, failure_reason must be set.
    """

    requested_path: str = Field(description="The artifact path requested (e.g. 'pyproject.toml')")
    template_name: str = Field(description="Name of the template to use (exact directory name)")

    status: GenerationStatus = Field(
        default=GenerationStatus.SUBMITTED,
        description="Current lifecycle status",
    )

    matched_template: str | None = Field(
        default=None,
        description="Name of the template that was actually matched",
    )

    artifact: str | None = Field(
        default=None,
        description="The rendered artifact text (set only on success)",
    )

    failure_reason: FailureReason | None = Field(
        default=None,
        description="Why the generation failed (set only on failure)",
    )

    retry_count: int = Field(
        default=0,
        description="Number of retry attempts so far",
        ge=0,
    )

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    @property
    def can_retry(self) -> bool:
        """Can this generation be retried?

        A generation is retryable when it has failed and the retry
        count has not yet reached the limit (3 by default).
        """
        return self.status == GenerationStatus.FAILED and self.retry_count < 3

    @property
    def is_done(self) -> bool:
        """Is this generation in a terminal state?

        READY and FAILED are terminal; SUBMITTED and GENERATING are not.
        """
        return self.status in (GenerationStatus.READY, GenerationStatus.FAILED)

    @property
    def succeeded(self) -> bool:
        """Did this generation succeed?"""
        return self.status == GenerationStatus.READY
