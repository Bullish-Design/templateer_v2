"""Exhaustive escaper guardrails — §B1 and §B4.

`escaping.py` claims its output round-trips *exactly* through tomllib,
json.loads, yaml.safe_load and ast.literal_eval.  Part II of the review says
the claim was checked with 18 hand-picked payloads.  These tests enumerate the
space instead: every codepoint in U+0000–U+011F plus the interesting outliers,
against all four structured languages.

Modelled on `.scratch/projects/06-adversarial-review/probes/escape_chars.py`.
"""

from __future__ import annotations

import ast
import json
import tomllib
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel

from templateer.escaping import EscapeError, escape_string, make_finalizer
from templateer.renderer import RenderError, render_template

# The whole Latin-1 + Latin Extended-A range, plus the separators and the byte
# order mark.  Lone surrogates are excluded: CONTRACT.md §3 says they raise.
CODEPOINTS: list[int] = [*range(0x0000, 0x0120), 0x2028, 0x2029, 0xFEFF]

LONE_SURROGATES: list[int] = [0xD800, 0xDBFF, 0xDC00, 0xDFFF]

# Each loader wraps the escaped content in that language's double-quoted
# string literal and returns the value the language reads back.
LOADERS: dict[str, Callable[[str], str]] = {
    "toml": lambda text: tomllib.loads(f'k = "{text}"')["k"],
    "json": lambda text: json.loads(f'{{"k": "{text}"}}')["k"],
    "yaml": lambda text: yaml.safe_load(f'k: "{text}"')["k"],
    "python": lambda text: ast.literal_eval(f'"{text}"'),
}


# ---------------------------------------------------------------------------
# §B1 — every codepoint, every structured language
# ---------------------------------------------------------------------------


@pytest.mark.finding_b1
@pytest.mark.parametrize("language", sorted(LOADERS))
def test_escape_string_round_trips_every_codepoint(language: str) -> None:
    """Every codepoint survives escape_string plus a parse, unchanged.

    Reports every failing (codepoint, language) pair, not the first one.
    """
    load = LOADERS[language]
    broken: list[str] = []

    for codepoint in CODEPOINTS:
        original = "a" + chr(codepoint) + "b"
        escaped = escape_string(original)
        try:
            restored = load(escaped)
        except Exception as e:
            broken.append(f"U+{codepoint:04X} {type(e).__name__}: {e}")
            continue
        if restored != original:
            broken.append(f"U+{codepoint:04X} mismatch: got {restored!r}")

    assert not broken, (
        f"{len(broken)} of {len(CODEPOINTS)} codepoints do not round-trip "
        f"through {language}:\n  " + "\n  ".join(broken)
    )


@pytest.mark.finding_b1
@pytest.mark.parametrize(
    "codepoint", LONE_SURROGATES, ids=[f"U+{cp:04X}" for cp in LONE_SURROGATES]
)
def test_lone_surrogate_raises_escape_error(codepoint: int) -> None:
    """A lone surrogate is never legitimate content — it raises."""
    with pytest.raises(EscapeError):
        escape_string("a" + chr(codepoint) + "b")


@pytest.mark.finding_b1
def test_astral_character_still_round_trips() -> None:
    """The surrogate rule must not reject a real astral character."""
    original = "emoji \U0001f600 here"
    for language, load in LOADERS.items():
        assert load(escape_string(original)) == original, language


# ---------------------------------------------------------------------------
# §B4 — containers at a `{{ }}` site
# ---------------------------------------------------------------------------


class _ListModel(BaseModel):
    deps: list[str]


class _DictModel(BaseModel):
    conf: dict[str, str]


@pytest.mark.finding_b4
@pytest.mark.parametrize("language", sorted(LOADERS))
@pytest.mark.parametrize(
    ("model", "source"),
    [
        (_ListModel(deps=['a"\nINJECTED = "yes']), "deps = {{ deps }}\n"),
        (_DictModel(conf={"k": "v"}), "conf = {{ conf }}\n"),
    ],
    ids=["list", "dict"],
)
def test_container_at_output_site_is_rejected(
    tmp_path: Path, language: str, model: BaseModel, source: str
) -> None:
    """A container reaches the artifact through MiniJinja's own conversion.

    That conversion is Python-repr-shaped and language-blind.  The finalizer
    must refuse it, and the message must name the fix.
    """
    template_file = tmp_path / "t.j2"
    template_file.write_text(source, encoding="utf-8")

    with pytest.raises(RenderError) as exc:
        render_template(template_file, model, language)

    assert isinstance(exc.value.__cause__, EscapeError), (
        f"expected an EscapeError cause, got {exc.value.__cause__!r}"
    )
    assert "{% for %}" in str(exc.value), (
        "the message must name the fix: interpolate elements with {% for %}"
    )


@pytest.mark.finding_b4
@pytest.mark.parametrize("language", sorted(LOADERS))
@pytest.mark.parametrize(
    "value",
    [["a", "b"], {"k": "v"}, ("a", "b"), {"a", "b"}],
    ids=["list", "dict", "tuple", "set"],
)
def test_finalizer_rejects_every_container_type(language: str, value: object) -> None:
    """list, dict, tuple and set are all refused by the finalizer."""
    finalize = make_finalizer(language)
    with pytest.raises(EscapeError):
        finalize(value)


@pytest.mark.finding_b4
@pytest.mark.parametrize("language", sorted(LOADERS))
@pytest.mark.parametrize("value", [0, 1, -17, 3.5, 0.0], ids=str)
def test_numbers_still_interpolate_unchanged(language: str, value: object) -> None:
    """int and float pass through the finalizer untouched."""
    assert make_finalizer(language)(value) == value


@pytest.mark.finding_b4
@pytest.mark.false_positive_guard
def test_filter_output_is_a_string_and_still_renders(tmp_path: Path) -> None:
    """`{{ x | join(',') }}` yields a str, so the container rule must not fire.

    `templates/pyproject-uv/template.j2` depends on this.
    """
    template_file = tmp_path / "t.j2"
    template_file.write_text('extras = "{{ deps | join(\',\') }}"\n', encoding="utf-8")
    rendered = render_template(template_file, _ListModel(deps=["a", "b"]), "toml")
    assert tomllib.loads(rendered) == {"extras": "a,b"}
