"""LLM-based model generation using Pydantic AI.

The LLM's output type is the template's Pydantic schema.  Pydantic AI performs
structured-output parsing, validation, and validation-feedback retries; this
module does not second-guess it.
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


def generate_model(
    template: Template,
    user_request: str,
    context: dict[str, Any] | None = None,
    model_name: str = DEFAULT_MODEL,
) -> BaseModel:
    """Ask an LLM to fill *template*'s schema.

    Raises whatever pydantic-ai raises; the pipeline classifies it.
    """
    agent = Agent(
        model_name,
        output_type=template.get_schema_class(),
        instructions=template.load_prompt(),
        retries=MODEL_OUTPUT_RETRIES,
    )
    context_text = build_context(user_request, context or {})
    if example := template.load_example():
        context_text += f"\n\nExample of a well-formed response:\n{example}"
    # With output_type set, pydantic-ai guarantees result.output is a validated
    # instance of the schema class; the stubs type it loosely.
    return cast(BaseModel, agent.run_sync(context_text).output)


def build_context(user_request: str, context: dict[str, Any]) -> str:
    """Build the context text for the LLM."""
    parts = [f"User request: {user_request}"]
    if context:
        # default=str: Path objects are the most plausible "project fact" an
        # agent will pass, and stringifying them is obviously the intent.
        parts.append(f"Project facts:\n{json.dumps(context, indent=2, default=str)}")
    return "\n\n".join(parts)
