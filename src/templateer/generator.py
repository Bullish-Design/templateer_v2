"""LLM-based model generation using Pydantic AI.

This module bridges the gap between user intent and structured data.
Given a template with a Pydantic schema and a prompt, it asks an LLM
to produce validated model instances. Validation failures are fed back
to the LLM for retry.

Allium spec alignment:
  rule RequestModelFromLLM:
    when: gen.status becomes model_pending
    let response = produce_structured_model(
        config.default_model,
        template.schema_module,
        template.schema_class,
        template.prompt_file,
        gen.context
    )
    ensures: gen.raw_model_response = response
    ensures: gen.status = model_received
"""

import json
from typing import Any

from pydantic import BaseModel, ValidationError
from pydantic_ai import Agent, ModelRetry

from templateer.template import Template
from templateer.validation import validate_model_instance


class ModelGenerationError(Exception):
    """Raised when model generation fails after exhausting retries."""


# Default model used for generation when none is specified.
# Can be overridden via the model_name parameter or env var.
DEFAULT_MODEL = "openai:gpt-4.1-mini"

# Maximum number of retries on validation failure.
DEFAULT_MAX_RETRIES = 3


def generate_model(
    template: Template,
    user_request: str,
    context: dict[str, Any] | None = None,
    model_name: str = DEFAULT_MODEL,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> tuple[BaseModel, list[str]]:
    """
    Use Pydantic AI to fill a template schema from user intent.

    This function:
      1. Loads the template's schema class and prompt.
      2. Builds a Pydantic AI Agent with the schema as output type.
      3. Sends user request and project facts to the LLM.
      4. Validates the result against the schema.
      5. Retries with error feedback if validation fails.

    Args:
        template: The template whose schema should be filled.
        user_request: What the user/agent wants to generate.
        context: Optional project facts to help the LLM choose values.
        model_name: The LLM model identifier (e.g. "openai:gpt-4.1-mini").
        max_retries: Maximum number of retries on validation failure.
                     The initial attempt plus this many retries.

    Returns:
        Tuple of (validated_model, messages).
        messages includes any notes or warnings from the generation process.

    Raises:
        ModelGenerationError: If generation fails after all retries.
    """
    schema_class = template.get_schema_class()
    prompt = template.load_prompt()

    messages: list[str] = []

    # Build the context text the LLM will receive
    context_text = _build_context(user_request, context or {})

    agent = Agent(
        model_name,
        output_type=schema_class,
        instructions=prompt,
        retries=max_retries,
    )

    try:
        result = agent.run_sync(context_text)
    except Exception as e:
        raise ModelGenerationError(
            f"LLM call failed after {max_retries + 1} attempt(s): {e}"
        )

    # The Agent with output_type set returns a validated BaseModel instance
    # in result.output. Pydantic AI handles validation internally and retries
    # via ModelRetry. The result should already be valid.
    raw_output = result.output

    if raw_output is None:
        raise ModelGenerationError(
            "LLM returned None instead of a valid model after "
            f"{agent.retries} retries"
        )

    if not isinstance(raw_output, schema_class):
        # Fallback: try to validate the raw output manually.
        # This can happen with older pydantic-ai versions or custom models.
        if isinstance(raw_output, dict):
            validated, errors = validate_model_instance(schema_class, raw_output)
            if validated is not None:
                return validated, messages
            raise ModelGenerationError(
                f"Model validation failed: {'; '.join(errors)}"
            )
        raise ModelGenerationError(
            f"LLM returned unexpected output type: {type(raw_output).__name__}. "
            f"Expected {schema_class.__name__}."
        )

    # Defensive: validate the instance fields even though pydantic-ai
    # should have done so. This catches any schema mismatches.
    try:
        validated_data = raw_output.model_dump(mode="json")
        validated, errors = validate_model_instance(schema_class, validated_data)
        if errors:
            raise ModelGenerationError(
                f"Post-generation validation failed: {'; '.join(errors)}"
            )
    except ValidationError as e:
        raise ModelGenerationError(
            f"Post-generation validation error: {e}"
        )

    return raw_output, messages


def _build_context(user_request: str, context: dict[str, Any]) -> str:
    """Build the context text for the LLM.

    The context consists of the user's request followed by any
    project facts that help the LLM make better choices.

    Args:
        user_request: The user's generation request.
        context: Additional project facts as key-value pairs.

    Returns:
        A formatted string for the LLM.
    """
    parts = [f"User request: {user_request}"]

    if context:
        facts_json = json.dumps(context, indent=2)
        parts.append(f"Project facts:\n{facts_json}")

    return "\n\n".join(parts)
