"""The generation request and its result.

Replaces the old Generation state machine.  ``run_pipeline`` was synchronous,
so no caller could ever observe SUBMITTED or GENERATING; what the codebase
actually needs is a result, and a request it can be retried from.
"""

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from templateer.generator import DEFAULT_MODEL
from templateer.models import RegionBoundary


class FailureReason(str, Enum):
    """Why a generation failed."""

    NO_TEMPLATE = "no_template"
    """The requested template name is not in the catalog."""

    CONFIG_ERROR = "config_error"
    """Missing API key, unknown model id, or other caller misconfiguration."""

    LLM_FAILED = "llm_failed"
    """The LLM call failed (network, timeout, provider error)."""

    MODEL_VALIDATION_FAILED = "model_validation_failed"
    """The LLM could not produce output satisfying the schema."""

    RENDER_FAILED = "render_failed"
    """The Jinja render step failed — schema/template drift, or a bad value."""

    OUTPUT_VALIDATION_FAILED = "output_validation_failed"
    """The rendered artifact failed output validation."""


# Retrying only helps for nondeterministic failures.  A missing template, a
# missing API key, and a broken template are permanent: retrying burns tokens
# to reach the same answer.
RETRYABLE = frozenset({
    FailureReason.LLM_FAILED,
    FailureReason.MODEL_VALIDATION_FAILED,
    FailureReason.OUTPUT_VALIDATION_FAILED,
})


class GenerationRequest(BaseModel):
    """Everything needed to run — or re-run — a generation."""

    template_name: str = Field(description="Exact template directory name")
    user_request: str = Field(description="What the caller wants generated")
    context: dict[str, Any] = Field(default_factory=dict, description="Project facts")
    model_name: str = Field(default=DEFAULT_MODEL)
    # An uncapped budget burns tokens to reach the same answer.
    max_attempts: int = Field(
        default=3, ge=1, le=10, description="Whole-pipeline attempts"
    )


class GenerationResult(BaseModel):
    """The outcome of a generation.

    Invariants, enforced below rather than merely documented:
      - success  => artifact is set and failure_reason is None
      - failure  => failure_reason is set and artifact is None
    """

    request: GenerationRequest
    output_path: str | None = Field(default=None, description="Where the artifact belongs")
    model: dict[str, Any] | None = Field(default=None, description="The validated model dump")
    artifact: str | None = Field(default=None, description="Rendered artifact — success only")
    failure_reason: FailureReason | None = None
    error_detail: str | None = Field(default=None, description="Human-readable failure text")
    warnings: list[str] = Field(default_factory=list, description="Non-fatal validator notes")
    attempt: int = Field(default=1, ge=1, description="Which attempt produced this result")

    # A region generation produces a payload, not a file.  The consumer needs
    # the slot to splice the payload into.
    kind: Literal["full_file", "region"] = Field(
        default="full_file", description="What the template generates"
    )
    region: RegionBoundary | None = Field(
        default=None, description="The bounded slot — region generations only"
    )
    usage: dict[str, int] | None = Field(
        default=None, description="Token counts; None when the provider reports none"
    )

    @model_validator(mode="after")
    def _check_invariants(self) -> "GenerationResult":
        if self.failure_reason is None and self.artifact is None:
            raise ValueError("a successful result must carry an artifact")
        if self.failure_reason is not None and self.artifact is not None:
            raise ValueError("a failed result must not carry an artifact")
        return self

    @property
    def succeeded(self) -> bool:
        return self.failure_reason is None

    @property
    def can_retry(self) -> bool:
        """Would another attempt plausibly help?"""
        return (
            self.failure_reason in RETRYABLE
            and self.attempt < self.request.max_attempts
        )
