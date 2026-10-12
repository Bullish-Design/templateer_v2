"""Render tagged JSON values as Nix expressions.

The ``nix`` language does not escape at the finalizer.  The finalizer passes
strings unchanged, so every ``{{ }}`` site in a ``nix`` template must pass its
value through the ``nix`` filter, the ``nix_nullable`` filter, or ``nix_attr``
for an attribute-path segment.  The source lint in ``templateer.audit``
enforces that rule.  ``nix`` refuses ``None``.  ``nix_nullable`` renders it as
``null``.

The value grammar is plain JSON plus five tagged objects:

=====================================================  ==========================
Value                                                  Nix text
=====================================================  ==========================
``"text"``                                             ``"text"`` (escaped)
``1``, ``1.5``, ``true``, ``false``                    the literal
``[a, b]``                                             ``[ a b ]``
``{"k": v}``                                           ``{ "k" = v; }``
``{"$pkg": "a.b", "$output": "bin"}``                  ``pkgs.a.b.bin``
``{"$path": "./x"}``                                   ``./x``
``{"$nix": "src"}``                                    ``(src)``
``{"$priority": "default", "value": v}``               ``lib.mkDefault v``
``{"$priority": "force", "value": v}``                 ``lib.mkForce v``
``{"$priority": 50, "value": v}``                      ``lib.mkOverride 50 v``
``{"$float": "7.0"}``                                  ``7.0``
=====================================================  ==========================

A list element or a function argument is an *atom*.  A negative number or a
priority call in an atom position gets parentheses, because ``[ -1 ]`` and
``[ lib.mkForce 1 ]`` do not parse as one element.  ``$nix`` is raw Nix source
by design: the caller owns its content.

``$float`` writes a Nix float from text.  A caller can send it in model data.

MiniJinja turns a float with an integer value (``7.0``) into an int when it
hands the value to a Python filter, and Nix then reads ``7``.  The renderer
avoids this.  Before it renders a ``nix`` template, it rewrites each top-level
``| nix`` in a ``{{ }}`` site to ``| tojson | nix_json``.  ``tojson`` runs inside
MiniJinja and keeps ``7.0``.  :func:`nix_json` parses that text and calls
:func:`nix_value`.  The template context keeps its real floats, so comparison,
truth tests, and arithmetic on a float field work.
"""

from __future__ import annotations

import json
import math
import re
import sys
from collections.abc import Callable
from typing import Any, cast

from templateer.escaping import EscapeError, escape_nix_string

MAX_INT = 2**63 - 1  # Nix cannot write -2**63 as a literal.

# Nix refuses the literal of a non-zero float below the smallest normal double.
_MIN_NORMAL_FLOAT = sys.float_info.min  # 2.2250738585072014e-308

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_'-]*\Z")

# Keywords that the Nix grammar refuses as a bare attribute name.  ``or`` is
# legal in some places and not in others, so it is quoted too.
_KEYWORDS = frozenset(
    {"assert", "else", "if", "in", "inherit", "let", "or", "rec", "then", "with"}
)

# A relative path literal: segments of path characters, no empty segment, no
# trailing slash, and no ``${``.  Nix refuses a trailing slash and a double
# slash, and ``/*`` or ``//`` would start a comment or an operator.
_PATH = re.compile(r"\.{1,2}(?:/[A-Za-z0-9._+-]+)+\Z|\.{1,2}\Z")

_FLOAT_KEYS = frozenset({"$float"})
_PKG_KEYS = frozenset({"$pkg", "$output"})
_PRIORITY_KEYS = frozenset({"$priority", "value"})


def nix_attr(segment: object) -> str:
    """Render one attribute-path segment: bare when valid, else quoted."""
    if not isinstance(segment, str):
        raise EscapeError(
            f"an attribute-path segment must be a string, got {type(segment).__name__}"
        )
    if _IDENTIFIER.match(segment) and segment not in _KEYWORDS:
        return segment
    return f'"{escape_nix_string(segment)}"'


def nix_attr_path(value: object) -> str:
    """Render an attribute path: one segment, or a list of segments joined by dots."""
    if isinstance(value, (list, tuple)):
        if not value:
            raise EscapeError("an attribute path needs at least one segment")
        return ".".join(nix_attr(segment) for segment in value)
    return nix_attr(value)


def _int_literal(value: int, atom: bool) -> str:
    if abs(value) > MAX_INT:
        raise EscapeError(f"integer {value} is outside the Nix 64-bit literal range")
    return f"({value})" if value < 0 and atom else str(value)


def _float_literal(value: float, atom: bool) -> str:
    if not math.isfinite(value):
        raise EscapeError(f"float {value!r} has no Nix literal")
    if value != 0.0 and abs(value) < _MIN_NORMAL_FLOAT:
        raise EscapeError(f"float {value!r} is subnormal; Nix refuses its literal")
    text = repr(value)
    if "e" in text and "." not in text:
        # Nix floats need a decimal point before the exponent: 1e+20 is not one.
        mantissa, exponent = text.split("e")
        text = f"{mantissa}.0e{exponent}"
    return f"({text})" if text.startswith("-") and atom else text


def _render_float(obj: dict[str, Any], atom: bool) -> str:
    if set(obj) != _FLOAT_KEYS:
        raise EscapeError(f"$float object has unexpected keys: {sorted(set(obj) - _FLOAT_KEYS)}")
    text = obj["$float"]
    if not isinstance(text, str):
        raise EscapeError("$float must be a string")
    try:
        value = float(text)
    except ValueError:
        raise EscapeError(f"$float {text!r} is not a float") from None
    return _float_literal(value, atom)


def _render_pkg(obj: dict[str, Any]) -> str:
    if not _PKG_KEYS.issuperset(obj):
        raise EscapeError(f"$pkg object has unexpected keys: {sorted(set(obj) - _PKG_KEYS)}")
    attr = obj["$pkg"]
    if not isinstance(attr, str) or not attr:
        raise EscapeError("$pkg must be a non-empty dotted attribute string")
    parts = attr.split(".")
    if not all(parts):
        raise EscapeError(f"$pkg {attr!r} has an empty path segment")
    output = obj.get("$output")
    if output is not None:
        if not isinstance(output, str) or not output:
            raise EscapeError("$output must be a non-empty string")
        parts.append(output)
    return "pkgs." + ".".join(nix_attr(part) for part in parts)


def _render_path(obj: dict[str, Any]) -> str:
    if set(obj) != {"$path"}:
        raise EscapeError(f"$path object has unexpected keys: {sorted(set(obj) - {'$path'})}")
    path = obj["$path"]
    if not isinstance(path, str) or not _PATH.match(path):
        raise EscapeError(
            f"$path {path!r} is not a safe relative path; use './name' or "
            f"'../name' with letters, digits and . _ + - only"
        )
    return path


def _render_nix(obj: dict[str, Any]) -> str:
    if set(obj) != {"$nix"}:
        raise EscapeError(f"$nix object has unexpected keys: {sorted(set(obj) - {'$nix'})}")
    source = obj["$nix"]
    if not isinstance(source, str) or not source.strip():
        raise EscapeError("$nix must be a non-empty Nix source string")
    if "\x00" in source:
        raise EscapeError("U+0000 in $nix source")
    return f"({source})"


def _render_priority(obj: dict[str, Any], atom: bool, allow_null: bool = False) -> str:
    if set(obj) != _PRIORITY_KEYS:
        raise EscapeError(
            "$priority object needs exactly the keys '$priority' and 'value'"
        )
    priority = obj["$priority"]
    value = _render(obj["value"], atom=True, allow_null=allow_null)
    if priority == "default":
        text = f"lib.mkDefault {value}"
    elif priority == "force":
        text = f"lib.mkForce {value}"
    elif isinstance(priority, int) and not isinstance(priority, bool):
        text = f"lib.mkOverride {_int_literal(priority, True)} {value}"
    else:
        raise EscapeError(
            f"$priority {priority!r} must be 'default', 'force' or an integer"
        )
    return f"({text})" if atom else text


_TAGS: dict[str, Callable[..., str]] = {
    "$float": _render_float,
    "$pkg": lambda obj, atom: _render_pkg(obj),
    "$path": lambda obj, atom: _render_path(obj),
    "$nix": lambda obj, atom: _render_nix(obj),
    "$priority": _render_priority,
}


def _render(value: object, atom: bool, allow_null: bool = False) -> str:
    # bool before int: bool is a subclass of int in Python.
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None and allow_null:
        return "null"
    if value is None:
        raise EscapeError(
            "null value passed to the nix filter; guard the field with "
            "{% if field %} ... {% endif %} or give it a default"
        )
    if isinstance(value, int):
        return _int_literal(value, atom)
    if isinstance(value, float):
        return _float_literal(value, atom)
    if isinstance(value, str):
        return f'"{escape_nix_string(value)}"'
    if isinstance(value, (list, tuple)):
        if not value:
            return "[ ]"
        items = (_render(item, atom=True, allow_null=allow_null) for item in value)
        return "[ " + " ".join(items) + " ]"
    if isinstance(value, dict):
        for tag, render_tag in _TAGS.items():
            if tag in value:
                if tag == "$priority":
                    return _render_priority(cast("dict[str, Any]", value), atom, allow_null)
                return render_tag(value, atom)
        if not value:
            return "{ }"
        pairs = []
        for key, item in value.items():
            if not isinstance(key, str):
                raise EscapeError(f"object key {key!r} is not a string")
            text = _render(item, atom=False, allow_null=allow_null)
            pairs.append(f'"{escape_nix_string(key)}" = {text};')
        return "{ " + " ".join(pairs) + " }"
    raise EscapeError(f"cannot render a {type(value).__name__} value as Nix")


def nix_value(value: object) -> str:
    """Render a tagged JSON value as a Nix expression.

    Raises:
        EscapeError: *value* is ``None``, a malformed tagged object, a
            non-finite float, an integer outside the 64-bit range, or text
            that Nix cannot carry.
    """
    return _render(value, atom=False)


def nix_nullable(value: object) -> str:
    """Like :func:`nix_value`, but ``None`` renders as ``null`` at any depth.

    This includes a ``None`` inside a list, an object, or a ``$priority`` value.
    The strict :func:`nix_value` still refuses ``None``.
    """
    return _render(value, atom=False, allow_null=True)


def _parse_json(text: object, filter_name: str) -> object:
    if not isinstance(text, str):
        raise EscapeError(
            f"{filter_name} needs the text from tojson, got {type(text).__name__}"
        )
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise EscapeError(f"{filter_name}: the text is not JSON: {e}") from None


def nix_json(text: object) -> str:
    """Render a JSON text (the output of ``| tojson``) as a Nix expression.

    Raises:
        EscapeError: *text* is not a string, is not JSON, or holds a value that
            :func:`nix_value` refuses.
    """
    return nix_value(_parse_json(text, "nix_json"))


def nix_json_nullable(text: object) -> str:
    """Like :func:`nix_json`, but ``null`` renders as ``null`` (see :func:`nix_nullable`)."""
    return nix_nullable(_parse_json(text, "nix_json_nullable"))


_SITE = re.compile(r"\{\{.*?\}\}", re.DOTALL)
_MASK = re.compile(r"\{#.*?#\}|\{%-?\s*raw\s*-?%\}.*?\{%-?\s*endraw\s*-?%\}", re.DOTALL)
_NIX_FILTER = re.compile(r"\|\s*(nix|nix_nullable)(?!\w)")
_JSON_FILTER = {"nix": "nix_json", "nix_nullable": "nix_json_nullable"}


def _rewrite_site(inner: str) -> str:
    """Route each top-level ``| nix`` (or ``| nix_nullable``) in one site through JSON."""
    out: list[str] = []
    quote = ""
    escaped = False
    depth = 0
    index = 0
    while index < len(inner):
        char = inner[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "|" and depth == 0 and inner[index + 1 : index + 2] != "|":
            found = _NIX_FILTER.match(inner, index)
            if found:
                out.append(f"| tojson | {_JSON_FILTER[found.group(1)]}")
                index = found.end()
                continue
        out.append(char)
        index += 1
    return "".join(out)


def route_nix_through_json(source: str) -> str:
    """Rewrite ``| nix`` to ``| tojson | nix_json`` in every ``{{ }}`` site.

    ``| nix_nullable`` becomes ``| tojson | nix_json_nullable``.

    Comments, ``{% raw %}`` blocks, string literals, and other filters (such as
    ``nix_attr``) stay unchanged.  The audit lint reads the original source,
    so the template author never writes ``nix_json``
    or ``nix_json_nullable``.
    """
    masked = _MASK.sub(lambda m: re.sub(r"[^\n]", " ", m.group()), source)
    pieces: list[str] = []
    last = 0
    for site in _SITE.finditer(masked):
        pieces.append(source[last : site.start()])
        pieces.append("{{" + _rewrite_site(source[site.start() + 2 : site.end() - 2]) + "}}")
        last = site.end()
    pieces.append(source[last:])
    return "".join(pieces)
