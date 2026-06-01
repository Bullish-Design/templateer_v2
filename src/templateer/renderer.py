"""Deterministic template rendering from validated Pydantic model data.

The central invariant of Templateer: a renderer may only receive validated
Pydantic model data. No raw LLM output, user prompt, environment variables,
or filesystem context reaches the Jinja template.
"""

from pathlib import Path
from typing import Any

from minijinja import Environment
from pydantic import BaseModel


class RenderError(Exception):
    """Raised when template rendering fails."""


def render_template(
    template_path: Path,
    model: BaseModel | dict[str, Any],
    strict: bool = True,
) -> str:
    """
    Render a Jinja template from a validated model.

    This function implements the strict rendering contract: the template
    receives ONLY the validated model data, nothing else. In strict mode,
    references to undefined variables are errors (not silent empty strings).

    Args:
        template_path: Path to the Jinja template file (.j2).
        model: A validated Pydantic model instance or a dict from model_dump.
        strict: If True, undefined variables raise errors.

    Returns:
        The rendered artifact text.

    Raises:
        RenderError: If the template references undefined variables or
                     if the template file is missing.
    """
    if not template_path.exists():
        raise RenderError(f"Template file not found: {template_path}")

    # Extract render context: only model data
    if isinstance(model, BaseModel):
        render_context = model.model_dump(mode="json")
    else:
        render_context = model

    template_source = template_path.read_text()

    # Create a MiniJinja environment with strict undefined behavior
    # and whitespace control (trim trailing newlines after blocks,
    # strip leading whitespace before blocks)
    env = Environment()
    env.trim_blocks = True
    env.lstrip_blocks = True

    if strict:
        env.undefined_behavior = "strict"

    try:
        result = env.render_str(template_source, **render_context)
    except Exception as e:
        raise RenderError(
            f"Failed to render template '{template_path.name}': {e}"
        ) from e

    # IMPORTANT: The render context is not exposed beyond this function.
    # The template cannot access anything except what was in the model.
    return result
