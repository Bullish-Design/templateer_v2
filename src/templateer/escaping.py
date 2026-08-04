"""Language-aware value formatting for the render boundary.

Pydantic constrains *which values* reach a template.  This module constrains
how those values *lex* once they land in the artifact — the other half of the
guarantee, and the half that was missing.

Installed as the MiniJinja ``finalizer``, so it runs at every ``{{ }}`` output
site and nowhere else: ``{% if flag %}`` still sees a native bool.
"""

import json
from collections.abc import Callable

# Languages whose string literals share JSON's escape grammar.  Verified to
# round-trip exactly through tomllib / json / yaml.safe_load / ast.literal_eval.
_QUOTED_STRING_LANGUAGES = frozenset({"toml", "json", "yaml", "python"})

# Language token for boolean output.  Python source needs True/False;
# TOML, JSON and YAML all need lowercase.
_BOOLEANS = {
    "python": ("True", "False"),
    "toml": ("true", "false"),
    "json": ("true", "false"),
    "yaml": ("true", "false"),
}


class EscapeError(ValueError):
    """A value cannot be safely interpolated into the target language."""


def escape_string(value: str) -> str:
    """Escape *value* for use inside a double-quoted string literal.

    Returns the string *content* only — the template supplies the quotes.

    ``ensure_ascii=False`` is required: the default emits UTF-16 surrogate
    pairs for astral characters, and TOML rejects surrogates.  ``json.dumps``
    leaves U+007F bare, which TOML also rejects, so it is escaped explicitly.
    """
    return json.dumps(value, ensure_ascii=False)[1:-1].replace("\x7f", "\\u007f")


def make_finalizer(language: str) -> Callable[[object], object]:
    """Build the MiniJinja finalizer for *language*.

    Unknown languages (markdown, dockerfile, text, ...) get identity treatment
    for strings — there is no string literal syntax to protect — but still get
    correct boolean and null handling.
    """
    quote = language in _QUOTED_STRING_LANGUAGES
    true_token, false_token = _BOOLEANS.get(language, ("true", "false"))

    def finalize(value: object) -> object:
        # bool before int: bool is a subclass of int in Python.
        if isinstance(value, bool):
            return true_token if value else false_token
        if value is None:
            raise EscapeError(
                "null value interpolated into the artifact; guard the field "
                "with {% if field %} ... {% endif %} or give it a default"
            )
        if isinstance(value, str) and quote:
            return escape_string(value)
        return value  # int, float, and already-rendered filter output

    return finalize
