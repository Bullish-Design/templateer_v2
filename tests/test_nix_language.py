"""The ``nix`` output language: escaper, filter, parse validator and lint.

``nix`` is a parsed language.  It is not in ``STRUCTURED_LANGUAGES`` and not in
``_QUOTED_STRING_LANGUAGES``: the finalizer passes strings unchanged, and the
``nix`` filter does all the quoting.  Tests marked ``needs_nix`` run
``nix-instantiate``; they skip when it is not on PATH.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, TypeAdapter

from templateer.audit import STRUCTURED_LANGUAGES as AUDIT_STRUCTURED
from templateer.audit import audit_template, lint_template_source
from templateer.escaping import (
    _QUOTED_STRING_LANGUAGES,
    EscapeError,
    escape_nix_string,
    make_finalizer,
)
from templateer.models import FullFileOutput, OutputSpec, ParseValidator
from templateer.nix_render import nix_attr, nix_attr_path, nix_value, tag_integral_floats
from templateer.renderer import RenderError, render_template
from templateer.template import Template
from templateer.validators import STRUCTURED_LANGUAGES, validate_output

needs_nix = pytest.mark.needs_nix

# A module head that binds the free names the filter emits.  ``--parse`` binds
# variables, so a bare `pkgs.x` is an "undefined variable" error without it.
HEAD = "{ pkgs, lib, ... }:\n"


def parse_errors(source: str) -> list[str]:
    """Parse *source* as a module with the built-in nix validator."""
    errors, _ = validate_output(HEAD + source, "nix")
    return errors


def nix_eval(source: str) -> Any:
    """Evaluate *source* once with ``nix-instantiate`` and return the JSON."""
    result = subprocess.run(
        ["nix-instantiate", "--eval", "--strict", "--json", "-"],
        input=source.encode("utf-8"),
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    return json.loads(result.stdout.decode("utf-8"))


# ---------------------------------------------------------------------------
# Language set
# ---------------------------------------------------------------------------


def test_nix_is_parsed_but_not_structured() -> None:
    assert "nix" not in STRUCTURED_LANGUAGES
    assert "nix" not in AUDIT_STRUCTURED
    assert "nix" not in _QUOTED_STRING_LANGUAGES


def test_nix_is_a_valid_output_and_parse_validator_language() -> None:
    spec = TypeAdapter(OutputSpec).validate_python({"path": "m.nix", "language": "nix"})
    assert isinstance(spec, FullFileOutput)
    assert ParseValidator(kind="parse", language="nix").language == "nix"


def test_unknown_language_is_still_rejected() -> None:
    with pytest.raises(ValueError):
        TypeAdapter(OutputSpec).validate_python({"path": "m.nix", "language": "nics"})


# ---------------------------------------------------------------------------
# The finalizer
# ---------------------------------------------------------------------------


def test_finalizer_passes_strings_unchanged() -> None:
    finalize = make_finalizer("nix")
    assert finalize('a"b\\${x}\n') == 'a"b\\${x}\n'


def test_finalizer_renders_nix_booleans() -> None:
    finalize = make_finalizer("nix")
    assert finalize(True) == "true"
    assert finalize(False) == "false"


def test_finalizer_rejects_none() -> None:
    with pytest.raises(EscapeError):
        make_finalizer("nix")(None)


# ---------------------------------------------------------------------------
# escape_nix_string
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "escaped"),
    [
        ("\\", "\\\\"),
        ('"', '\\"'),
        ("${", "\\${"),
        ("\n", "\\n"),
        ("\r", "\\r"),
        ("\t", "\\t"),
        ("plain", "plain"),
        ("$", "$"),
        ("{", "{"),
        ("$$", "$$"),
        ("$${x}", "$\\${x}"),
        ("\\${", "\\\\\\${"),
        ("é ﻿\U0001f600", "é ﻿\U0001f600"),
        ("\x01\x7f\x85", "\x01\x7f\x85"),
    ],
)
def test_escape_nix_string_rules(raw: str, escaped: str) -> None:
    assert escape_nix_string(raw) == escaped


def test_escape_nix_string_has_no_unicode_escape() -> None:
    """Nix has no ``\\uXXXX`` escape, so a non-ASCII character stays raw."""
    assert "\\u" not in escape_nix_string(" é\x1f")


def test_escape_nix_string_rejects_nul() -> None:
    with pytest.raises(EscapeError, match="U\\+0000"):
        escape_nix_string("a\x00b")


@pytest.mark.parametrize("codepoint", [0xD800, 0xDBFF, 0xDC00, 0xDFFF])
def test_escape_nix_string_rejects_lone_surrogate(codepoint: int) -> None:
    with pytest.raises(EscapeError):
        escape_nix_string("a" + chr(codepoint) + "b")


# Every codepoint in U+0001-U+011F, the separators, the byte order mark, one
# astral character, and the text `${x}`.
CODEPOINTS: list[int] = [*range(0x0001, 0x0120), 0x2028, 0x2029, 0xFEFF, 0x1F600]
TEXTS: list[str] = [*(f"a{chr(cp)}b" for cp in CODEPOINTS), "${x}", "\\${x}"]


@needs_nix
def test_escape_nix_string_round_trips_every_codepoint() -> None:
    """One Nix call evaluates every escaped string and returns it unchanged."""
    source = "[\n" + "\n".join(f'"{escape_nix_string(t)}"' for t in TEXTS) + "\n]"
    assert nix_eval(source) == TEXTS


@needs_nix
def test_nix_filter_round_trips_every_codepoint() -> None:
    """The same sweep through the filter, as a list value, in one Nix call."""
    assert nix_eval(nix_value(TEXTS)) == TEXTS


@needs_nix
def test_nix_filter_round_trips_through_render_template(tmp_path: Path) -> None:
    class M(BaseModel):
        texts: list[str]

    template_file = tmp_path / "t.j2"
    template_file.write_text("{{ texts | nix }}\n", encoding="utf-8")
    rendered = render_template(template_file, M(texts=TEXTS), "nix")
    assert nix_eval(rendered) == TEXTS


@needs_nix
def test_dollar_brace_does_not_interpolate() -> None:
    """The escaped text must not evaluate: `${x}` has no binding for x."""
    assert nix_eval(f'"{escape_nix_string("${x}")}"') == "${x}"


# ---------------------------------------------------------------------------
# The nix filter, one test per tagged form
# ---------------------------------------------------------------------------

FILTER_CASES: list[tuple[str, Any, str]] = [
    ("string", "hello", '"hello"'),
    ("string-escaped", 'a"b${c}\n', '"a\\"b\\${c}\\n"'),
    ("int", 42, "42"),
    ("negative-int", -3, "-3"),
    ("float", 1.5, "1.5"),
    ("float-exponent", 1e20, "1.0e+20"),
    ("float-small-exponent", 1.5e-07, "1.5e-07"),
    ("true", True, "true"),
    ("false", False, "false"),
    ("empty-list", [], "[ ]"),
    ("list", [1, "a", True], '[ 1 "a" true ]'),
    ("negative-in-list", [-1, -2.5], "[ (-1) (-2.5) ]"),
    ("empty-object", {}, "{ }"),
    ("object", {"a": 1, "b-c": "x"}, '{ "a" = 1; "b-c" = "x"; }'),
    ("object-key-escaped", {'k"${x}': 1}, '{ "k\\"\\${x}" = 1; }'),
    ("nested", {"a": [{"b": 1}]}, '{ "a" = [ { "b" = 1; } ]; }'),
    ("pkg", {"$pkg": "a.b"}, "pkgs.a.b"),
    ("pkg-out", {"$pkg": "a.b", "$output": "out"}, "pkgs.a.b"),
    ("pkg-output-null", {"$pkg": "a", "$output": None}, "pkgs.a"),
    ("pkg-bin", {"$pkg": "a.b", "$output": "bin"}, "pkgs.a.b.bin"),
    ("pkg-quoted-segment", {"$pkg": "a.2048"}, 'pkgs.a."2048"'),
    ("pkg-in-list", [{"$pkg": "git"}, {"$pkg": "uv"}], "[ pkgs.git pkgs.uv ]"),
    ("path", {"$path": "./x"}, "./x"),
    ("path-nested", {"$path": "../a/b.nix"}, "../a/b.nix"),
    ("nix", {"$nix": "pkgs.lib.mkIf true 1"}, "(pkgs.lib.mkIf true 1)"),
    ("nix-in-list", [{"$nix": "1 + 1"}, 2], "[ (1 + 1) 2 ]"),
    ("default", {"$priority": "default", "value": 1}, "lib.mkDefault 1"),
    ("force", {"$priority": "force", "value": "x"}, 'lib.mkForce "x"'),
    ("override", {"$priority": 50, "value": True}, "lib.mkOverride 50 true"),
    ("override-negative", {"$priority": -5, "value": -1}, "lib.mkOverride (-5) (-1)"),
    ("priority-list", {"$priority": "force", "value": [1]}, "lib.mkForce [ 1 ]"),
    (
        "priority-nested",
        {"$priority": "default", "value": {"$priority": "force", "value": 1}},
        "lib.mkDefault (lib.mkForce 1)",
    ),
    (
        "priority-in-list",
        [{"$priority": "force", "value": 1}, 2],
        "[ (lib.mkForce 1) 2 ]",
    ),
    (
        "priority-in-object",
        {"a": {"$priority": "default", "value": {"$pkg": "x"}}},
        '{ "a" = lib.mkDefault pkgs.x; }',
    ),
]


@pytest.mark.parametrize(
    ("value", "expected"),
    [(v, e) for _, v, e in FILTER_CASES],
    ids=[i for i, _, _ in FILTER_CASES],
)
def test_nix_filter_renders_text(value: Any, expected: str) -> None:
    assert nix_value(value) == expected


@needs_nix
@pytest.mark.parametrize(
    "value", [v for _, v, _ in FILTER_CASES], ids=[i for i, _, _ in FILTER_CASES]
)
def test_nix_filter_output_parses(value: Any) -> None:
    """Each form parses as a module body, alone and as a function argument."""
    assert parse_errors(nix_value(value)) == []
    assert parse_errors(f"[ {nix_value({'k': value})} ]") == []


@needs_nix
def test_nix_filter_output_parses_as_attribute_value_and_list_element() -> None:
    value = {"$priority": "force", "value": [-1, {"$nix": "pkgs"}]}
    assert parse_errors(f'{{ "a" = {nix_value(value)}; }}') == []
    assert parse_errors(f"[ {nix_value([value, value])} ]") == []


@needs_nix
def test_negative_number_in_list_is_one_element() -> None:
    assert nix_eval(nix_value([1, -1, 2])) == [1, -1, 2]


@needs_nix
def test_unparenthesised_negative_would_not_parse() -> None:
    """Why the filter wraps a negative number inside a list."""
    assert parse_errors("[ -1 ]") != []


@needs_nix
def test_float_exponent_forms_evaluate() -> None:
    assert nix_eval(nix_value([1e20, 1.5e-07, 2.0, -0.5])) == [1e20, 1.5e-07, 2.0, -0.5]


# ---------------------------------------------------------------------------
# Integral floats keep their type through MiniJinja
# ---------------------------------------------------------------------------

INTEGRAL_FLOATS = [0.0, 7.0, -125000.0, 1e20]

# Replace every leaf by its Nix type name, so JSON cannot hide int against float.
LIB_STUB = "lib = { mkForce = x: x; mkDefault = x: x; mkOverride = n: x: x; };\n"
TYPE_MAP = (
    "let " + LIB_STUB + "    t = v: if builtins.isList v then map t v\n"
    "           else if builtins.isAttrs v then builtins.mapAttrs (_: t) v\n"
    "           else builtins.typeOf v;\n"
    "in t ("
)


def nix_types(source: str) -> Any:
    return nix_eval(TYPE_MAP + source + ")")


class _FloatModel(BaseModel):
    x: float
    xs: list[float]
    obj: dict[str, Any]


def _render_floats(tmp_path: Path, template: str, model: BaseModel) -> str:
    template_file = tmp_path / "t.j2"
    template_file.write_text(template, encoding="utf-8")
    return render_template(template_file, model, "nix")


def test_tag_integral_floats_tags_only_integral_floats() -> None:
    data = {"a": 7.0, "b": [0.0, 2.5, 3, True, "s", None], "c": {"d": -125000.0}}
    assert tag_integral_floats(data) == {
        "a": {"$float": "7.0"},
        "b": [{"$float": "0.0"}, 2.5, 3, True, "s", None],
        "c": {"d": {"$float": "-125000.0"}},
    }


def test_tag_integral_floats_leaves_non_finite_floats() -> None:
    inf = float("inf")
    assert tag_integral_floats([inf]) == [inf]


@pytest.mark.parametrize(
    ("text", "expected"),
    [("0.0", "0.0"), ("7.0", "7.0"), ("1e+20", "1.0e+20")],
)
def test_float_tag_renders_a_float(text: str, expected: str) -> None:
    assert nix_value({"$float": text}) == expected
    assert nix_value([{"$float": text}]) == f"[ {expected} ]"


def test_float_tag_negative_in_a_list_gets_parentheses() -> None:
    assert nix_value([{"$float": "-125000.0"}]) == "[ (-125000.0) ]"
    assert nix_value({"$float": "-125000.0"}) == "-125000.0"


@pytest.mark.parametrize(
    "value",
    [
        {"$float": 7},
        {"$float": "seven"},
        {"$float": "inf"},
        {"$float": "nan"},
        {"$float": "7.0", "extra": 1},
    ],
)
def test_float_tag_rejects_bad_values(value: Any) -> None:
    with pytest.raises(EscapeError):
        nix_value(value)


@needs_nix
@pytest.mark.parametrize("value", INTEGRAL_FLOATS)
def test_integral_float_renders_as_a_nix_float(tmp_path: Path, value: float) -> None:
    model = _FloatModel(
        x=value,
        xs=[value, 2.5],
        obj={
            "plain": value,
            "list": [value],
            "nested": {"deep": value},
            "prio": {"$priority": "force", "value": value},
            "dflt": {"$priority": "default", "value": [value]},
            "ovr": {"$priority": 50, "value": value},
        },
    )
    rendered = _render_floats(
        tmp_path,
        "{\n"
        "  top = {{ x | nix }};\n"
        "  inlist = {{ xs | nix }};\n"
        "  item = {{ xs[0] | nix }};\n"
        "  inobj = {{ obj | nix }};\n"
        "  key = {{ obj.plain | nix }};\n"
        "  prio = {{ obj.prio | nix }};\n"
        "  looped = [ {% for v in xs %}({{ v | nix }}) {% endfor %}];\n"
        "}\n",
        model,
    )
    types = nix_types(rendered)
    assert types["top"] == "float"
    assert types["inlist"] == ["float", "float"]
    assert types["item"] == "float"
    assert types["inobj"] == {
        "plain": "float",
        "list": ["float"],
        "nested": {"deep": "float"},
        "prio": "float",
        "dflt": ["float"],
        "ovr": "float",
    }
    assert types["key"] == "float"
    assert types["prio"] == "float"
    assert types["looped"] == ["float", "float"]
    # The value itself survives, too.
    assert nix_eval(f"let {LIB_STUB} in {rendered}")["top"] == value


@needs_nix
def test_non_integral_floats_and_ints_keep_their_types(tmp_path: Path) -> None:
    class M(BaseModel):
        f: float
        i: int
        items: list[Any]

    model = M(f=2.5, i=7, items=[1.5, 3, -0.25, True, "s"])
    rendered = _render_floats(
        tmp_path,
        "{ f = {{ f | nix }}; i = {{ i | nix }}; items = {{ items | nix }}; }\n",
        model,
    )
    assert nix_types(rendered) == {
        "f": "float",
        "i": "int",
        "items": ["float", "int", "float", "bool", "string"],
    }


def test_integral_float_is_unchanged_for_other_languages(tmp_path: Path) -> None:
    class M(BaseModel):
        x: float

    template_file = tmp_path / "t.j2"
    template_file.write_text("x = {{ x }}\n", encoding="utf-8")
    rendered = render_template(template_file, M(x=7.0), "toml")
    assert "$float" not in rendered


@needs_nix
def test_priority_list_element_is_one_element() -> None:
    """`[ lib.mkForce 1 ]` is two elements; the filter parenthesises it."""
    source = "let lib = { mkForce = x: x; }; in " + nix_value(
        [{"$priority": "force", "value": 1}, 2]
    )
    assert nix_eval(source) == [1, 2]


@needs_nix
def test_pkg_with_output_selects_the_attribute() -> None:
    source = (
        "let pkgs = { a.b = { bin = 7; out = 1; }; }; in "
        + nix_value({"$pkg": "a.b", "$output": "bin"})
    )
    assert nix_eval(source) == 7


@needs_nix
def test_pkg_keyword_segment_is_quoted_and_selects() -> None:
    source = 'let pkgs = { "if" = { x = 3; }; }; in ' + nix_value({"$pkg": "if.x"})
    assert 'pkgs."if".x' in source
    assert nix_eval(source) == 3


@pytest.mark.parametrize(
    "value",
    [
        None,
        [None],
        {"a": None},
        float("nan"),
        float("inf"),
        2**63,
        -(2**63),
        {"$pkg": ""},
        {"$pkg": "a..b"},
        {"$pkg": 3},
        {"$pkg": "a", "extra": 1},
        {"$pkg": "a", "$output": ""},
        {"$path": "x"},
        {"$path": "/etc/x"},
        {"$path": "./x/"},
        {"$path": "./a//b"},
        {"$path": "./a b"},
        {"$path": "./${x}"},
        {"$path": "./x", "extra": 1},
        {"$nix": ""},
        {"$nix": "  "},
        {"$nix": 3},
        {"$nix": "a\x00b"},
        {"$priority": "default"},
        {"$priority": "weak", "value": 1},
        {"$priority": True, "value": 1},
        {"$priority": 1.5, "value": 1},
        {"$priority": "force", "value": 1, "extra": 1},
        {"a\x00": 1},
        "a\x00b",
        {1: "x"},
        {"$pkg": "a", "$path": "./x"},
        object(),
    ],
)
def test_nix_filter_rejects_bad_values(value: Any) -> None:
    with pytest.raises(EscapeError):
        nix_value(value)


def test_plain_object_with_dollar_key_is_not_a_tag() -> None:
    assert nix_value({"$other": 1}) == '{ "$other" = 1; }'


# ---------------------------------------------------------------------------
# Attribute-path segments
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("segment", "expected"),
    [
        ("foo", "foo"),
        ("_foo", "_foo"),
        ("foo-bar", "foo-bar"),
        ("foo'", "foo'"),
        ("a1", "a1"),
        ("1a", '"1a"'),
        ("-a", '"-a"'),
        ("", '""'),
        ("a.b", '"a.b"'),
        ("a b", '"a b"'),
        ('a"b', '"a\\"b"'),
        ("a${b}", '"a\\${b}"'),
        ("if", '"if"'),
        ("let", '"let"'),
        ("or", '"or"'),
        ("inherit", '"inherit"'),
        ("true", "true"),
        ("é", '"é"'),
    ],
)
def test_nix_attr_segment(segment: str, expected: str) -> None:
    assert nix_attr(segment) == expected


def test_nix_attr_path_joins_segments() -> None:
    assert nix_attr_path(["services", "my-app", "2x"]) == 'services.my-app."2x"'
    assert nix_attr_path("services") == "services"


@pytest.mark.parametrize("value", [None, 3, [], ["a", None], [["a"]]])
def test_nix_attr_path_rejects_bad_values(value: Any) -> None:
    with pytest.raises(EscapeError):
        nix_attr_path(value)


@needs_nix
def test_attr_segments_define_the_named_attributes() -> None:
    names = ["foo", "1a", "if", "or", "a.b", 'q"${x}', "", "foo-bar", "é"]
    body = " ".join(f"{nix_attr(n)} = 1;" for n in names)
    assert sorted(nix_eval("{ " + body + " }")) == sorted(names)
    assert nix_eval("{ " + nix_attr_path(["a", "if", "x y"]) + " = 1; }") == {
        "a": {"if": {"x y": 1}}
    }


# ---------------------------------------------------------------------------
# The render boundary
# ---------------------------------------------------------------------------


class _Model(BaseModel):
    name: str
    enabled: bool
    packages: list[dict[str, str]]


def _render(tmp_path: Path, source: str, model: BaseModel) -> str:
    template_file = tmp_path / "t.j2"
    template_file.write_text(source, encoding="utf-8")
    return render_template(template_file, model, "nix")


def test_filter_is_available_only_for_nix(tmp_path: Path) -> None:
    template_file = tmp_path / "t.j2"
    template_file.write_text("{{ name | nix }}\n", encoding="utf-8")
    model = _Model(name="x", enabled=True, packages=[])
    assert _render(tmp_path, "{{ name | nix }}", model) == '"x"'
    with pytest.raises(RenderError):
        render_template(template_file, model, "toml")


def test_render_uses_filters_and_native_booleans(tmp_path: Path) -> None:
    model = _Model(name='a"b', enabled=True, packages=[{"$pkg": "git"}])
    rendered = _render(
        tmp_path,
        "{ pkgs, ... }:\n"
        "{\n"
        "  {{ 'my-app' | nix_attr }}.name = {{ name | nix }};\n"
        "  {{ 'if' | nix_attr }}.on = {{ enabled | nix }};\n"
        "  packages = {{ packages | nix }};\n"
        "}\n",
        model,
    )
    assert rendered == (
        "{ pkgs, ... }:\n"
        "{\n"
        '  my-app.name = "a\\"b";\n'
        '  "if".on = true;\n'
        "  packages = [ pkgs.git ];\n"
        "}"
    )


def test_unfiltered_string_is_not_escaped(tmp_path: Path) -> None:
    """The finalizer passes strings through: this is why the lint exists."""
    model = _Model(name='a"b', enabled=True, packages=[])
    assert _render(tmp_path, "{{ name }}", model) == 'a"b'


def test_unfiltered_none_raises(tmp_path: Path) -> None:
    class M(BaseModel):
        x: str | None = None

    with pytest.raises(RenderError) as exc:
        _render(tmp_path, "{{ x }}", M())
    assert isinstance(exc.value.__cause__, EscapeError)


def test_filter_error_surfaces_as_render_error(tmp_path: Path) -> None:
    class M(BaseModel):
        x: str = "a\x00b"

    with pytest.raises(RenderError):
        _render(tmp_path, "{{ x | nix }}", M())


# ---------------------------------------------------------------------------
# The parse validator
# ---------------------------------------------------------------------------


@needs_nix
def test_validator_accepts_a_module() -> None:
    errors, warnings = validate_output("{ pkgs, ... }: { a = pkgs.git; }\n", "nix")
    assert errors == []
    assert warnings == []


@needs_nix
@pytest.mark.parametrize("source", ["{ a = ; }", "{ a = 1 }", '"unterminated', ""])
def test_validator_rejects_a_syntax_error(source: str) -> None:
    errors, _ = validate_output(source, "nix")
    assert len(errors) == 1
    assert errors[0].startswith("nix parse failed")


@needs_nix
def test_validator_reads_standard_input() -> None:
    """`-` reads stdin on the pinned Nix; a literal file named `-` is not read."""
    errors, _ = validate_output("{ a = 1; }", "nix")
    assert errors == []


@needs_nix
def test_validator_reports_a_free_variable() -> None:
    """`--parse` binds names, so a module must declare `pkgs` in its head."""
    errors, _ = validate_output("{ a = pkgs.git; }", "nix")
    assert errors and "undefined variable" in errors[0]


def test_validator_rejects_nul() -> None:
    errors, _ = validate_output("{ a = 1; }\x00 garbage", "nix")
    assert errors and "U+0000" in errors[0]


def test_validator_reports_missing_nix_instantiate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing binary is an error, never a silent pass."""
    monkeypatch.setenv("PATH", "")
    errors, _ = validate_output("{ a = 1; }", "nix")
    assert errors and "nix-instantiate not found" in errors[0]


@needs_nix
def test_declared_parse_validator_for_nix_runs() -> None:
    declared = [ParseValidator(kind="parse", language="nix")]
    errors, _ = validate_output("{ a = ; }", "markdown", declared)
    assert errors and errors[0].startswith("Custom parse (nix) failed")
    errors, _ = validate_output("{ a = 1; }", "markdown", declared)
    assert errors == []


@needs_nix
def test_injection_through_an_unescaped_value_fails_the_parse(tmp_path: Path) -> None:
    """The parse check catches a quote break-out in a template that skips the filter."""
    model = _Model(name='x"; evil = "y', enabled=True, packages=[])
    bad = _render(tmp_path, '{ name = "{{ name }}" ; }', model)
    assert validate_output(bad, "nix")[0] == []  # it still parses: a new key!
    good = _render(tmp_path, '{ name = {{ name | nix }}; }', model)
    assert nix_eval(good) == {"name": 'x"; evil = "y'}
    assert nix_eval(bad) != {"name": 'x"; evil = "y'}


# ---------------------------------------------------------------------------
# The audit lint
# ---------------------------------------------------------------------------

SCHEMA = """\
from pydantic import BaseModel


class M(BaseModel):
    x: str
    items: list[str] = []
"""

NIX_OUTPUT = {"path": "m.nix", "language": "nix"}


def _template(
    make_template: Callable[..., Path], source: str, fixtures: dict[str, Any] | None = None
) -> Template:
    return Template(
        make_template(
            "nixmod",
            output=NIX_OUTPUT,
            template_source=source,
            schema_source=SCHEMA,
            fixtures=fixtures,
        )
    )


@pytest.mark.parametrize(
    "site",
    [
        "{{ x | nix }}",
        "{{x|nix}}",
        "{{- x | nix -}}",
        "{{ x | nix_attr }}",
        "{{ 'if' | nix_attr }}",
        "{{ items | nix | indent(2) }}",
        "{{ x | default('a') | nix }}",
        "{{ (x if items else 'b') | nix }}",
        "{{ x | nix() }}",
        "{{ \"a|b\" | nix }}",
        "{{ 'a|b' | nix }}",
        "{{ items | join('|') | nix }}",
    ],
)
def test_lint_accepts_filtered_sites(make_template: Callable[..., Path], site: str) -> None:
    template = _template(make_template, f"{{ pkgs, ... }}:\n{{ a = {site}; }}\n")
    assert lint_template_source(template) == []


@pytest.mark.parametrize(
    ("site", "reason"),
    [
        ("{{ x }}", "does not pass through the nix filter"),
        ('"{{ x }}"', "does not pass through the nix filter"),
        ("{{ x | upper }}", "last filter is 'upper'"),
        ("{{ x | nix | upper }}", "last filter is 'upper'"),
        ("{{ x | nix | safe }}", "last filter is 'safe'"),
        ("{{ x | tojson }}", "last filter is 'tojson'"),
        ("{{ x | escape }}", "last filter is 'escape'"),
        ("{{ 'a' ~ x | nix }}", "binds looser than a filter"),
        ("{{ x if items else 'b' | nix }}", "binds looser than a filter"),
        ("{{ x + 'a' | nix }}", "binds looser than a filter"),
        ("{{ x | nix if items }}", "unexpected text"),
        ("{{  }}", "empty"),
        ("{{ x | }}", "not a filter name"),
    ],
)
def test_lint_flags_unfiltered_sites(
    make_template: Callable[..., Path], site: str, reason: str
) -> None:
    template = _template(make_template, f"{{ pkgs, ... }}:\n{{ a = {site}; }}\n")
    findings = lint_template_source(template)
    assert len(findings) == 1, findings
    assert findings[0].startswith("template.j2:2:")
    assert reason in findings[0]


def test_lint_reports_every_site_with_its_line(make_template: Callable[..., Path]) -> None:
    source = "a = {{ x }};\nb = {{ x | nix }};\n\nc = {{\n  x\n}};\n"
    findings = lint_template_source(_template(make_template, source))
    assert [f.split(" ")[0] for f in findings] == ["template.j2:1:", "template.j2:4:"]


def test_lint_ignores_comments_raw_blocks_and_statements(
    make_template: Callable[..., Path],
) -> None:
    source = (
        "{# {{ x }} #}\n"
        "{% raw %}{{ x }}{% endraw %}\n"
        "{% for i in items %}\n"
        "  {{ i | nix }}\n"
        "{% endfor %}\n"
        "{% if x %}a{% endif %}\n"
    )
    assert lint_template_source(_template(make_template, source)) == []


def test_lint_does_not_apply_quote_rules_to_nix(make_template: Callable[..., Path]) -> None:
    """A filtered site inside quotes is still clean: only the filter is checked."""
    assert lint_template_source(_template(make_template, 'a = "{{ x | nix }}";')) == []


def test_lint_for_other_languages_is_unchanged(make_template: Callable[..., Path]) -> None:
    template = Template(
        make_template(
            "tomlmod",
            output={"path": "m.toml", "language": "toml"},
            template_source='a = "{{ x }}"\n',
            schema_source=SCHEMA,
        )
    )
    assert lint_template_source(template) == []


# ---------------------------------------------------------------------------
# The audit report
# ---------------------------------------------------------------------------


@needs_nix
def test_audit_parses_fixtures_and_counts_sites(make_template: Callable[..., Path]) -> None:
    template = _template(
        make_template,
        "{ pkgs, ... }:\n{ a = {{ x | nix }}; b = {{ items | nix }}; }\n",
        fixtures={"one.input.json": {"x": "v", "items": ["a"]}},
    )
    report = audit_template(template)
    assert report.findings == []
    assert report.audited
    assert report.ok
    assert report.fixtures_seen == 1
    assert report.sites_linted == 2


@needs_nix
def test_audit_flags_an_unfiltered_site(make_template: Callable[..., Path]) -> None:
    template = _template(
        make_template,
        '{ pkgs, ... }:\n{ a = "{{ x }}"; }\n',
        fixtures={"one.input.json": {"x": "v"}},
    )
    report = audit_template(template)
    assert not report.ok
    assert any("skips the nix filter" in f for f in report.findings)


@needs_nix
def test_audit_flags_a_fixture_that_does_not_parse(make_template: Callable[..., Path]) -> None:
    template = _template(
        make_template,
        "{ pkgs, ... }:\n{ a = {{ x | nix }} }\n",
        fixtures={"one.input.json": {"x": "v"}},
    )
    report = audit_template(template)
    assert any("one.input.json" in f and "nix parse failed" in f for f in report.findings)


def test_audit_without_fixtures_is_not_a_pass(make_template: Callable[..., Path]) -> None:
    report = audit_template(_template(make_template, "a = {{ x | nix }};\n"))
    assert not report.audited
    assert not report.ok
    assert report.skipped_reason is not None
    assert report.sites_linted == 1
