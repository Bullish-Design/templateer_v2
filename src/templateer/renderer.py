"""Deterministic rendering from validated Pydantic model data.

Two invariants, both enforced here and nowhere else:
  1. The template receives only ``model.model_dump(mode="json")``.
  2. Every interpolated value is formatted for the target language.
"""

from pathlib import Path

from minijinja import Environment
from pydantic import BaseModel

from templateer.escaping import make_finalizer


class RenderError(Exception):
    """Raised when template rendering fails."""


def render_template(template_path: Path, model: BaseModel, language: str) -> str:
    """Render *template_path* from *model* for a *language* artifact.

    Args:
        template_path: Path to the Jinja template file.
        model: A validated Pydantic model instance.  Not a dict — the type is
            the invariant.
        language: Target language, used to select value formatting.

    Raises:
        RenderError: Missing template, undefined variable, or a value that
            cannot be safely interpolated.
    """
    if not template_path.exists():
        raise RenderError(f"Template file not found: {template_path}")

    render_context = model.model_dump(mode="json")
    source = template_path.read_text(encoding="utf-8")

    env = Environment()
    env.trim_blocks = True
    env.lstrip_blocks = True
    env.undefined_behavior = "strict"        # always; not a per-template knob
    env.finalizer = make_finalizer(language)

    try:
        return env.render_str(source, **render_context)
    except Exception as e:
        raise RenderError(f"Failed to render '{template_path.name}': {e}") from e
