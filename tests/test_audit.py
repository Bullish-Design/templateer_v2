"""Audit guardrails — §A3 and the lint half of §A1.

The negative guard comes first.  The only audit assertion in the suite today
is `audit_template(pyproject-uv) == []`, so replacing the audit body with
`return []` leaves every test green.  These tests build deliberately
vulnerable templates and require the audit to flag them.

CONTRACT.md §5 pins the shapes:

    audit_template(template) -> AuditReport
    lint_template_source(template) -> list[str]
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from templateer.audit import audit_template
from templateer.template import Template, TemplateLoadError

SCHEMA_ONE_STRING = """\
from pydantic import BaseModel


class M(BaseModel):
    x: str
"""

SCHEMA_TAGS = """\
from pydantic import BaseModel


class M(BaseModel):
    tag: str
    note: str
"""


def _findings(report: Any) -> list[str]:
    """The findings, whatever shape `audit_template` returns.

    `audit_template` returns a bare list today and an `AuditReport` after
    Wave 2.  The detection tests care about detection, not about the shape;
    the shape has its own tests below.
    """
    if isinstance(report, list):
        return report
    return list(report.findings)


# Each case: a structured language, an unquoted `{{ }}` site, and a benign
# fixture that renders and parses.  Only the injection breaks it.
VULNERABLE: dict[str, tuple[str, dict[str, Any]]] = {
    "toml": ('name = {{ x }}\nother = "z"\n', {"x": "true"}),
    "json": ('{"name": {{ x }}, "other": "z"}\n', {"x": "1"}),
    "yaml": ("name: {{ x }}\nother: z\n", {"x": "hi"}),
    "python": ('NAME = {{ x }}\nOTHER = "z"\n', {"x": "hi"}),
}


def _vulnerable_template(
    make_template: Callable[..., Path], language: str
) -> Template:
    source, fixture = VULNERABLE[language]
    template_dir = make_template(
        f"vuln{language}",
        output={"path": f"out.{language}", "language": language},
        schema_source=SCHEMA_ONE_STRING,
        template_source=source,
        fixtures={"basic.input.json": fixture},
    )
    return Template(template_dir)


# ---------------------------------------------------------------------------
# §A3(a) + §A3(c) — the negative guard, for every structured language
# ---------------------------------------------------------------------------


@pytest.mark.finding_a3
@pytest.mark.parametrize("language", sorted(VULNERABLE))
def test_audit_flags_a_vulnerable_template(
    make_template: Callable[..., Path], language: str
) -> None:
    """The audit must find an unquoted interpolation site.

    This is the test whose absence let the `audit_template -> return []`
    mutant stay green across 257 tests.
    """
    template = _vulnerable_template(make_template, language)
    findings = _findings(audit_template(template))
    assert findings, (
        f"a {language} template with an unquoted {{{{ }}}} site audited clean"
    )


@pytest.mark.finding_a3
def test_audit_detects_a_value_change_not_only_a_new_key(
    make_template: Callable[..., Path],
) -> None:
    """A poked list item can never add a key path, only change a value.

    `_key_paths` records mapping keys and recurses into lists without
    recording indexes.  So no payload at this site can ever change the key
    set.  Detecting it requires comparing parsed *values*.
    """
    template_dir = make_template(
        "listvuln",
        output={"path": "out.yaml", "language": "yaml"},
        schema_source=SCHEMA_TAGS,
        template_source='tags:\n  - {{ tag }}\nnote: "{{ note }}"\n',
        fixtures={"basic.input.json": {"tag": "alpha", "note": "n"}},
    )
    findings = _findings(audit_template(Template(template_dir)))
    assert findings, "a value-only injection audited clean"
    assert any("tag" in f for f in findings), findings


# ---------------------------------------------------------------------------
# §A3(b) — silence must be loud
# ---------------------------------------------------------------------------


@pytest.mark.finding_a3
def test_audit_reports_skipped_when_no_examples(
    make_template: Callable[..., Path],
) -> None:
    """No fixtures means nothing was audited — say so, and do not claim ok."""
    template_dir = make_template(
        "noex",
        output={"path": "o.toml", "language": "toml"},
        schema_source=SCHEMA_ONE_STRING,
        template_source="name = {{ x }}\n",
    )
    report = audit_template(Template(template_dir))
    assert report.skipped_reason, "a template with no examples/ audited nothing"
    assert report.ok is False
    assert report.audited is False
    assert report.fixtures_seen == 0


@pytest.mark.finding_a3
def test_audit_reports_skipped_for_an_unstructured_language(
    make_template: Callable[..., Path],
) -> None:
    """Markdown has no structure to subvert, so the audit says it skipped."""
    template_dir = make_template(
        "mdtpl",
        output={"path": "o.md", "language": "markdown"},
        schema_source=SCHEMA_ONE_STRING,
        template_source="# {{ x }}\n",
        fixtures={"basic.input.json": {"x": "hi"}},
    )
    report = audit_template(Template(template_dir))
    assert report.skipped_reason
    assert report.ok is False


@pytest.mark.finding_a3
@pytest.mark.false_positive_guard
def test_audit_of_pyproject_uv_is_ok_and_loud(repo_templates: Path) -> None:
    """A clean template reports what it actually did — not a bare tick."""
    report = audit_template(Template(repo_templates / "pyproject-uv"))
    assert report.skipped_reason is None
    assert report.findings == []
    assert report.ok is True
    assert report.fixtures_seen >= 1
    assert report.fields_probed > 0
    assert report.template == "pyproject-uv"
    assert report.language == "toml"


# ---------------------------------------------------------------------------
# §A2 — an unknown language never reaches the audit
# ---------------------------------------------------------------------------


@pytest.mark.finding_a2
@pytest.mark.parametrize("language", ["nix", "tomI", "yml", "Python", "hcl"])
def test_unknown_output_language_fails_at_template_load(
    make_template: Callable[..., Path], language: str
) -> None:
    """`language` selects the escaper, the parse check and the audit.

    A typo must not silently disable all three.  It fails at load instead.
    """
    template_dir = make_template(
        "badlang",
        output={"path": "o.out", "language": language},
        schema_source=SCHEMA_ONE_STRING,
        template_source="name = {{ x }}\n",
        fixtures={"basic.input.json": {"x": "hi"}},
    )
    with pytest.raises(TemplateLoadError):
        Template(template_dir)


@pytest.mark.finding_a2
@pytest.mark.false_positive_guard
@pytest.mark.parametrize(
    "language", ["toml", "json", "yaml", "python", "markdown", "text"]
)
def test_known_output_languages_still_load(
    make_template: Callable[..., Path], language: str
) -> None:
    """The closed set must keep every language the project supports."""
    template_dir = make_template(
        "goodlang",
        output={"path": "o.out", "language": language},
        schema_source=SCHEMA_ONE_STRING,
        template_source='name = "{{ x }}"\n',
    )
    assert Template(template_dir).metadata.output.language == language


# ---------------------------------------------------------------------------
# §A1 (lint half) — the authoring rule, mechanically checked
# ---------------------------------------------------------------------------


def _lint() -> Callable[[Template], list[str]]:
    """Import the lint at call time, so a missing symbol fails one test."""
    from templateer.audit import lint_template_source

    return lint_template_source


@pytest.mark.finding_a1
@pytest.mark.parametrize("language", sorted(VULNERABLE))
def test_lint_flags_an_unquoted_interpolation_site(
    make_template: Callable[..., Path], language: str
) -> None:
    """Every `{{ }}` site outside a double-quoted span is a finding."""
    template = _vulnerable_template(make_template, language)
    assert _lint()(template), f"the unquoted {language} site was not flagged"


@pytest.mark.finding_a1
@pytest.mark.false_positive_guard
def test_lint_accepts_a_quoted_interpolation_site(
    make_template: Callable[..., Path],
) -> None:
    """A quoted site is the authoring rule, honoured — no finding."""
    template_dir = make_template(
        "quoted",
        output={"path": "o.yaml", "language": "yaml"},
        schema_source=SCHEMA_TAGS,
        template_source='tag: "{{ tag }}"\nnote: "{{ note }}{% if tag %}!{% endif %}"\n',
    )
    assert _lint()(Template(template_dir)) == []


@pytest.mark.finding_a1
@pytest.mark.false_positive_guard
def test_lint_accepts_an_unquoted_integer_site(
    make_template: Callable[..., Path],
) -> None:
    """An unquoted site is fine when the schema proves the value is a number."""
    template_dir = make_template(
        "intsite",
        output={"path": "o.toml", "language": "toml"},
        schema_source=(
            "from pydantic import BaseModel\n\n\n"
            "class M(BaseModel):\n"
            "    line_length: int\n"
            "    enabled: bool\n"
        ),
        template_source="line-length = {{ line_length }}\nenabled = {{ enabled }}\n",
    )
    assert _lint()(Template(template_dir)) == []


@pytest.mark.finding_a1
@pytest.mark.false_positive_guard
def test_lint_is_clean_for_pyproject_uv(repo_templates: Path) -> None:
    """The repository's own template must lint clean.

    Its one unquoted site is `line-length = {{ ruff.line_length }}`, and
    `ruff.line_length` is an integer.
    """
    findings = _lint()(Template(repo_templates / "pyproject-uv"))
    assert findings == [], f"false positive on templates/pyproject-uv: {findings}"


@pytest.mark.finding_a1
@pytest.mark.false_positive_guard
def test_lint_reports_nothing_for_an_unstructured_language(
    make_template: Callable[..., Path],
) -> None:
    """Markdown has no string literal syntax, so the rule does not apply."""
    template_dir = make_template(
        "mdlint",
        output={"path": "o.md", "language": "markdown"},
        schema_source=SCHEMA_ONE_STRING,
        template_source="# {{ x }}\n",
    )
    assert _lint()(Template(template_dir)) == []


@pytest.mark.finding_a1
def test_audit_report_carries_the_lint_findings(
    make_template: Callable[..., Path],
) -> None:
    """Lint findings and injection findings land in the same list."""
    template = _vulnerable_template(make_template, "yaml")
    report = audit_template(template)
    assert report.sites_linted > 0
    assert report.findings


@pytest.mark.finding_a3
def test_audit_flags_a_fixture_that_does_not_render(
    make_template: Callable[..., Path],
) -> None:
    """A fixture the template cannot render is a finding, not a silent pass."""
    template_dir = make_template(
        "brokenfixture",
        output={"path": "o.toml", "language": "toml"},
        schema_source=SCHEMA_ONE_STRING,
        template_source='name = "{{ missing_field }}"\n',
        fixtures={"basic.input.json": {"x": "hi"}},
    )
    assert _findings(audit_template(Template(template_dir)))


@pytest.mark.finding_a3
def test_audit_reads_every_fixture(make_template: Callable[..., Path]) -> None:
    """`fixtures_seen` counts what was probed, so silence can be checked."""
    template_dir = make_template(
        "twofix",
        output={"path": "o.toml", "language": "toml"},
        schema_source=SCHEMA_ONE_STRING,
        template_source='name = "{{ x }}"\n',
        fixtures={
            "minimal.input.json": {"x": "a"},
            "full.input.json": {"x": "b"},
        },
    )
    report = audit_template(Template(template_dir))
    assert report.fixtures_seen == 2
    assert report.fields_probed >= 2
    assert report.ok is True


@pytest.mark.finding_a3
def test_audit_report_is_json_serialisable(repo_templates: Path) -> None:
    """`templateer check --json` needs the report to dump."""
    report = audit_template(Template(repo_templates / "pyproject-uv"))
    payload = json.loads(json.dumps(report.model_dump()))
    assert payload["template"] == "pyproject-uv"
    assert payload["findings"] == []


# ---------------------------------------------------------------------------
# Round-2 follow-up §A3 — schema-driven field synthesis
# ---------------------------------------------------------------------------


SCHEMA_SYNTHESIS = """\
from typing import Literal

from pydantic import BaseModel


class Details(BaseModel):
    note: str


class M(BaseModel):
    required: str
    optional_text: str = "safe"
    nullable_text: str | None = None
    details: Details | None = None
    tags: list[str] = []
    constrained: Literal["SAFE"] | None = None
"""


SYNTHESIS_TEMPLATE = """\
required: {{ required }}
optional_text: {{ optional_text }}
{% if nullable_text is not none %}
nullable_text: {{ nullable_text }}
{% endif %}
{% if details is not none %}
details:
  note: {{ details.note }}
{% endif %}
tags:
{% for tag in tags %}
  - {{ tag }}
{% endfor %}
{% if constrained is not none %}
constrained: {{ constrained }}
{% endif %}
"""


@pytest.mark.finding_a3
def test_audit_synthesises_omitted_schema_fields(
    make_template: Callable[..., Path],
) -> None:
    """Optional, nullable, nested, and collection fields get real probes."""
    template_dir = make_template(
        "schemafields",
        output={"path": "out.yaml", "language": "yaml"},
        schema_source=SCHEMA_SYNTHESIS,
        template_source=SYNTHESIS_TEMPLATE,
        fixtures={"minimal.input.json": {"required": "present"}},
    )

    report = audit_template(Template(template_dir))

    assert report.fixtures_seen == 1
    assert report.fields_probed == 5
    assert [skip.field for skip in report.fields_skipped] == ["constrained"]
    assert "schema constraints" in report.fields_skipped[0].reason

    injection_findings = [
        finding for finding in report.findings if "minimal.input.json:" in finding
    ]
    for field in (
        "details.note",
        "nullable_text",
        "optional_text",
        "required",
        "tags[0]",
    ):
        assert any(f":{field}:" in finding for finding in injection_findings), (
            field,
            injection_findings,
        )


@pytest.mark.finding_a3
def test_audit_probe_order_is_stable(
    make_template: Callable[..., Path],
) -> None:
    """Schema fields use lexical paths, independent of declaration order."""
    template_dir = make_template(
        "probeorder",
        output={"path": "out.yaml", "language": "yaml"},
        schema_source=(
            "from pydantic import BaseModel\n\n\n"
            "class M(BaseModel):\n"
            "    zed: str\n"
            "    alpha: str = 'safe'\n"
            "    middle: list[str] = []\n"
        ),
        template_source=(
            "zed: {{ zed }}\n"
            "alpha: {{ alpha }}\n"
            "middle:\n{% for item in middle %}\n  - {{ item }}\n{% endfor %}\n"
        ),
        fixtures={"minimal.input.json": {"zed": "present"}},
    )

    report = audit_template(Template(template_dir))
    paths = [
        finding.split(":", 2)[1]
        for finding in report.findings
        if finding.startswith("minimal.input.json:")
    ]
    assert paths == ["alpha", "middle[0]", "zed"]
    assert report.fields_probed == 3
    assert report.fields_skipped == []


@pytest.mark.finding_a3
def test_audit_reports_schema_with_no_probeable_strings(
    make_template: Callable[..., Path],
) -> None:
    """A valid fixture does not make a string-free schema look audited."""
    template_dir = make_template(
        "nostrings",
        output={"path": "out.yaml", "language": "yaml"},
        schema_source=(
            "from pydantic import BaseModel\n\n\n"
            "class M(BaseModel):\n"
            "    count: int\n"
            "    enabled: bool = True\n"
        ),
        template_source="count: {{ count }}\nenabled: {{ enabled }}\n",
        fixtures={"minimal.input.json": {"count": 2}},
    )

    report = audit_template(Template(template_dir))
    assert report.fixtures_seen == 1
    assert report.fields_probed == 0
    assert report.fields_skipped == []
    assert report.audited is False
    assert report.skipped_reason == "schema has no string-bearing fields"


@pytest.mark.finding_a3
def test_audit_skip_details_are_json_serialisable(
    make_template: Callable[..., Path],
) -> None:
    """The Python and command-line JSON contracts share structured reasons."""
    template_dir = make_template(
        "skipjson",
        output={"path": "out.yaml", "language": "yaml"},
        schema_source=SCHEMA_SYNTHESIS,
        template_source=SYNTHESIS_TEMPLATE,
        fixtures={"minimal.input.json": {"required": "present"}},
    )

    payload = audit_template(Template(template_dir)).model_dump()
    assert payload["fields_probed"] == 5
    assert payload["fields_skipped"] == [
        {
            "fixture": "minimal.input.json",
            "field": "constrained",
            "reason": payload["fields_skipped"][0]["reason"],
        }
    ]
