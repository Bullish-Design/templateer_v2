"""LLM-based model generation using Pydantic AI.

The LLM's output type is the template's Pydantic schema.  Pydantic AI performs
structured-output parsing, validation, and validation-feedback retries; this
module does not second-guess it.

The entry point is async (§A8).  ``Agent.run_sync`` raises ``RuntimeError``
inside a running event loop, so a synchronous core makes the library
unreachable from the async frameworks it advertises itself to.  The sync
wrapper lives in ``pipeline.generate``.
"""

import json
from typing import Any, cast

from pydantic import BaseModel
from pydantic_ai import Agent

from templateer.template import Template

DEFAULT_MODEL = "openai:gpt-4.1-mini"

# Pydantic AI's *internal* budget for re-asking the LLM when its output fails
# schema validation.  Distinct from GenerationRequest.max_attempts, which
# re-runs the whole pipeline.  Conflating these two is what made the old retry
# budget grow 3 -> 4 -> 5 across pipeline retries.
MODEL_OUTPUT_RETRIES = 2

# The token counts read off a pydantic-ai run result (§C7).  Every name is an
# integer field of ``pydantic_ai.usage.RunUsage``; ``total_tokens`` is a
# derived property.  A name the installed version does not carry is skipped.
_USAGE_FIELDS = (
    "requests",
    "input_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "output_tokens",
    "total_tokens",
)


async def generate_model_async(
    template: Template,
    user_request: str,
    context: dict[str, Any] | None = None,
    model_name: str = DEFAULT_MODEL,
    prior_failure: str | None = None,
) -> tuple[BaseModel, dict[str, int] | None]:
    """Ask an LLM to fill *template*'s schema.

    Args:
        template: The template whose schema the model must fill.
        user_request: What the caller wants generated.
        context: Project facts the LLM can use.
        model_name: The pydantic-ai model identifier.
        prior_failure: The previous attempt's ``error_detail``.  The repair
            loop feeds it back so this attempt's prompt differs from the last
            one (§A9).

    Returns:
        ``(model, usage)``.  ``usage`` is ``None`` when the provider reports
        no token counts.

    Raises:
        Whatever pydantic-ai raises; the pipeline classifies it.
    """
    agent = Agent(
        model_name,
        output_type=template.get_schema_class(),
        instructions=template.load_prompt(),
        retries=MODEL_OUTPUT_RETRIES,
    )
    context_text = build_context(user_request, context or {}, prior_failure)
    if example := template.load_example():
        context_text += f"\n\nExample of a well-formed response:\n{example}"
    result = await agent.run(context_text)
    # With output_type set, pydantic-ai guarantees result.output is a validated
    # instance of the schema class; the stubs type it loosely.
    return cast(BaseModel, result.output), _usage_counts(result)


def build_context(
    user_request: str,
    context: dict[str, Any],
    prior_failure: str | None = None,
) -> str:
    """Build the context text for the LLM.

    A repair attempt must not re-ask the same question.  When *prior_failure*
    is set, the text names the failure and asks for a correction, so attempt
    N+1's prompt differs from attempt N's (§A9).
    """
    parts = [f"User request: {user_request}"]
    if context:
        # default=str: Path objects are the most plausible "project fact" an
        # agent will pass, and stringifying them is obviously the intent.
        parts.append(f"Project facts:\n{json.dumps(context, indent=2, default=str)}")
    if prior_failure:
        parts.append(
            "The previous attempt failed. Report from that attempt:\n"
            f"{prior_failure}\n"
            "Correct this failure. Do not repeat the previous answer."
        )
    return "\n\n".join(parts)


def _usage_counts(run_result: Any) -> dict[str, int] | None:
    """Return a pydantic-ai run result's token counts as plain integers.

    Tokens per artifact is the metric that proves the project's thesis, so the
    counts travel with the result (§C7).

    pydantic-ai 2.23 exposes ``AgentRunResult.usage`` as a property that
    returns a ``RunUsage``.  Earlier versions expose it as a method.  This
    function calls the attribute when it is callable, so both shapes work.

    Returns:
        The counts, or ``None`` when the provider reports none.  Invented
        numbers would be worse than no numbers.
    """
    usage: Any = getattr(run_result, "usage", None)
    if callable(usage):
        try:
            usage = usage()
        except Exception:
            return None
    if usage is None:
        return None

    counts: dict[str, int] = {}
    for name in _USAGE_FIELDS:
        value = getattr(usage, name, None)
        # bool is a subclass of int, and a flag is not a token count.
        if isinstance(value, int) and not isinstance(value, bool):
            counts[name] = value
    return counts or None
