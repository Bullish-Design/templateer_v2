"""Deterministic rendering from validated Pydantic model data.

Two invariants, both enforced here and nowhere else:
  1. The template receives only ``model.model_dump(mode="json")``.
  2. Every interpolated value is formatted for the target language.  A `nix`
     template formats through its `nix` filter instead of the finalizer.
"""

import math
from pathlib import Path

from minijinja import Environment
from pydantic import BaseModel

from templateer.escaping import make_finalizer
from templateer.nix_render import (
    MAX_INT,
    nix_attr_path,
    nix_json,
    nix_json_nullable,
    nix_nullable,
    nix_value,
    route_nix_through_json,
)


class RenderError(Exception):
    """Raised when template rendering fails."""


def _reject_unwritable_numbers(value: object) -> None:
    """Raise RenderError when *value* holds a number Nix cannot write.

    That is NaN, infinity, or an integer beyond 64 bits, at any depth.  MiniJinja
    turns such an integer into a float before a filter runs, so the filter
    cannot refuse it.
    """
    if isinstance(value, float) and not math.isfinite(value):
        raise RenderError(f"Nix has no literal for the float {value!r}")
    if isinstance(value, int) and abs(value) > MAX_INT:
        raise RenderError(f"integer {value} is outside the Nix 64-bit literal range")
    if isinstance(value, dict):
        for item in value.values():
            _reject_unwritable_numbers(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_unwritable_numbers(item)


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
    if language == "nix":
        # `tojson` writes `null` for NaN and infinity, which would hide them.
        _reject_unwritable_numbers(render_context)
        # MiniJinja turns an integral float into an int when it calls a Python
        # filter.  `tojson` keeps the float, so `nix` runs on the JSON text.
        source = route_nix_through_json(source)

    env = Environment()
    env.trim_blocks = True
    env.lstrip_blocks = True
    env.undefined_behavior = "strict"        # always; not a per-template knob
    env.finalizer = make_finalizer(language)
    if language == "nix":
        # The finalizer passes nix strings unchanged, so these filters do all
        # the quoting.  The audit lint requires one on every `{{ }}` site.
        env.add_filter("nix", nix_value)
        env.add_filter("nix_attr", nix_attr_path)
        env.add_filter("nix_nullable", nix_nullable)
        env.add_filter("nix_json", nix_json)
        env.add_filter("nix_json_nullable", nix_json_nullable)

    try:
        return env.render_str(source, **render_context)
    except Exception as e:
        raise RenderError(f"Failed to render '{template_path.name}': {e}") from e
