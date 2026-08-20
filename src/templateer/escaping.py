"""Language-aware value formatting for the render boundary.

Pydantic constrains *which values* reach a template.  This module constrains
how those values *lex* once they land in the artifact — the other half of the
guarantee, and the half that was missing.

Installed as the MiniJinja ``finalizer``, so it runs at every ``{{ }}`` output
site and nowhere else: ``{% if flag %}`` still sees a native bool.
"""

import json
import re
from collections.abc import Callable

# Languages whose string literals share JSON's escape grammar.
#
# The claim, and only the claim the suite checks: every codepoint in
# U+0000-U+011F, plus U+2028, U+2029, U+FEFF and one astral character,
# round-trips through tomllib / json.loads / yaml.safe_load /
# ast.literal_eval.  A lone surrogate raises instead.  The test
# ``test_escape_string_round_trips_every_codepoint`` in
# ``tests/test_escaping_exhaustive.py`` enumerates that set on every run.
#
# A wider sweep of all of U+0000-U+10FFFF also passed on 2026-08-20, with
# 2048 lone surrogates rejected and nothing else broken.  No test repeats
# that sweep, so it is a measurement, not a guarantee.
_QUOTED_STRING_LANGUAGES = frozenset({"toml", "json", "yaml", "python"})

# Language token for boolean output.  Python source needs True/False;
# TOML, JSON and YAML all need lowercase.
_BOOLEANS = {
    "python": ("True", "False"),
    "toml": ("true", "false"),
    "json": ("true", "false"),
    "yaml": ("true", "false"),
}

# Codepoints that at least one target language refuses to carry raw.
# ``json.dumps`` already escapes the C0 range; the range stays here so the
# rule reads as one rule.  The C1 half is the part that ``json.dumps`` leaves
# bare: PyYAML raises ``ReaderError`` on U+0080-U+0084 and U+0086-U+009F, and
# folds U+0085 (NEL) to a space, which corrupts the value silently.  U+007F
# (DEL) is the case this module patched before.
#
# U+FFFE and U+FFFF are noncharacters; PyYAML raises ``ReaderError`` on them
# too, and the same escape clears it.
#
# U+2028, U+2029 and U+FEFF are *not* here.  Measured against all four
# loaders, at four positions, in multi-line documents: each one round-trips
# raw.  Escaping them would be an untested precaution.
_UNSAFE = re.compile(r"[\x00-\x1f\x7f-\x9f\ufffe\uffff]")

# A lone surrogate is unrepresentable in TOML ("Escaped character is not a
# Unicode scalar value"), breaks PyYAML, and breaks ``ast.literal_eval``.
_SURROGATE = re.compile(r"[\ud800-\udfff]")

# MiniJinja renders a container with Python repr: single-quoted, and blind to
# the target language.  In TOML a single-quoted string is a *literal* string,
# so escapes do not apply and an apostrophe in the data breaks out.
_CONTAINERS = (list, dict, tuple, set, frozenset)


class EscapeError(ValueError):
    """A value cannot be safely interpolated into the target language."""


def escape_string(value: str) -> str:
    """Escape *value* for use inside a double-quoted string literal.

    Returns the string *content* only — the template supplies the quotes.

    ``ensure_ascii=False`` is required: the default emits UTF-16 surrogate
    pairs for astral characters, and TOML rejects surrogates.  ``json.dumps``
    then leaves U+007F, the C1 controls and the two BMP noncharacters bare,
    so this function escapes them as ``\\uXXXX`` — the one escape form all
    four grammars share.

    Raises:
        EscapeError: *value* holds a lone surrogate (U+D800-U+DFFF).  TOML
            cannot represent one at all, so every language rejects it.  The
            behaviour is uniform on purpose: a lone surrogate is never
            legitimate content.
    """
    found = _SURROGATE.search(value)
    if found:
        raise EscapeError(
            f"lone surrogate U+{ord(found.group()):04X} in an interpolated "
            f"value; no target language can represent it — remove it at the "
            f"source, or encode the text as UTF-8 with a replacement handler"
        )
    out = json.dumps(value, ensure_ascii=False)[1:-1]
    return _UNSAFE.sub(lambda m: "\\u%04x" % ord(m.group()), out)


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
        if isinstance(value, _CONTAINERS):
            raise EscapeError(
                f"{type(value).__name__} value interpolated into the "
                f"artifact; a container renders through MiniJinja as a Python "
                f"repr, which is single-quoted and blind to {language} — "
                f"interpolate the elements with a {{% for %}} loop, not the "
                f"container"
            )
        if isinstance(value, str) and quote:
            return escape_string(value)
        return value  # int, float, and already-rendered filter output

    return finalize
