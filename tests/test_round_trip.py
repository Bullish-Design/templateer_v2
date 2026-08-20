"""Rung 4 — the artifact's data must agree with the model's data (§A1).

The escaper protects the artifact's *lexical* structure.  Nothing protects its
*semantic* structure: a schema field declared `str` reaches the artifact as a
bool, an int or a null, and every layer reports success.

`check_round_trip` is the runtime half of the fix.  CONTRACT.md §4 pins its
signature:

    check_round_trip(artifact: str, language: str, model_dump: dict) -> list[str]
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from templateer.catalog import TemplateCatalog
from templateer.result import FailureReason, GenerationRequest
from templateer.template import Template

# The review's exact reproduction (§A1).
YAML_SCHEMA = """\
from pydantic import BaseModel


class M(BaseModel):
    title: str
    owner: str
"""

TOML_SCHEMA = """\
from pydantic import BaseModel


class M(BaseModel):
    name: str
"""


def _check_round_trip() -> Callable[[str, str, dict[str, Any]], list[str]]:
    """Import the checker at call time, so a missing symbol fails one test."""
    from templateer.validators import check_round_trip

    return check_round_trip


def _render(template_dir: Path, data: dict[str, Any]) -> str:
    template = Template(template_dir)
    return template.render(template.get_schema_class().model_validate(data))


# ---------------------------------------------------------------------------
# The reproduction, through a real render
# ---------------------------------------------------------------------------


@pytest.mark.finding_a1
def test_yaml_str_field_reaching_artifact_as_bool_is_reported(
    make_template: Callable[..., Path],
) -> None:
    """`title: str` with value "true" lands as a YAML boolean."""
    template_dir = make_template(
        "yamlvuln",
        output={"path": "out.yaml", "language": "yaml"},
        schema_source=YAML_SCHEMA,
        template_source="title: {{ title }}\nowner: \"{{ owner }}\"\n",
    )
    model_dump = {"title": "true", "owner": "andrew"}
    artifact = _render(template_dir, model_dump)
    assert artifact.splitlines()[0] == "title: true"

    findings = _check_round_trip()(artifact, "yaml", model_dump)
    assert findings, "a str field landing as a bool must be reported"
    assert any("title" in f for f in findings), findings


@pytest.mark.finding_a1
def test_yaml_str_field_reaching_artifact_as_null_is_reported(
    make_template: Callable[..., Path],
) -> None:
    """`owner: str` with value "#redacted" lands as YAML null."""
    template_dir = make_template(
        "yamlvuln",
        output={"path": "out.yaml", "language": "yaml"},
        schema_source=YAML_SCHEMA,
        template_source="title: \"{{ title }}\"\nowner: {{ owner }}\n",
    )
    model_dump = {"title": "Status", "owner": "#redacted"}
    artifact = _render(template_dir, model_dump)

    import yaml

    assert yaml.safe_load(artifact)["owner"] is None

    findings = _check_round_trip()(artifact, "yaml", model_dump)
    assert findings, "a str field landing as null must be reported"
    assert any("owner" in f for f in findings), findings


@pytest.mark.finding_a1
def test_toml_str_field_reaching_artifact_as_int_is_reported(
    make_template: Callable[..., Path],
) -> None:
    """`name: str` with value "123" lands as a TOML integer."""
    template_dir = make_template(
        "tomlvuln",
        output={"path": "out.toml", "language": "toml"},
        schema_source=TOML_SCHEMA,
        template_source="name = {{ name }}\n",
    )
    model_dump = {"name": "123"}
    artifact = _render(template_dir, model_dump)
    assert artifact.strip() == "name = 123"

    findings = _check_round_trip()(artifact, "toml", model_dump)
    assert findings, "a str field landing as an int must be reported"
    assert any("name" in f for f in findings), findings


@pytest.mark.finding_a1
def test_toml_str_field_reaching_artifact_as_list_is_reported() -> None:
    """A single-quoted TOML site can split one string into list items."""
    model_dump = {"value": "a', 'b"}
    artifact = "value = ['a', 'b']"

    findings = _check_round_trip()(artifact, "toml", model_dump)

    assert findings, "a str field landing as a list must be reported"
    assert "value" in findings[0]
    assert "list" in findings[0]


# ---------------------------------------------------------------------------
# The check must stay silent where nothing is wrong
# ---------------------------------------------------------------------------


@pytest.mark.finding_a1
@pytest.mark.false_positive_guard
def test_correct_artifact_reports_nothing(
    make_template: Callable[..., Path],
) -> None:
    """A quoted template keeps every str a str, so there is no finding."""
    template_dir = make_template(
        "yamlok",
        output={"path": "out.yaml", "language": "yaml"},
        schema_source=YAML_SCHEMA,
        template_source="title: \"{{ title }}\"\nowner: \"{{ owner }}\"\n",
    )
    model_dump = {"title": "true", "owner": "#redacted"}
    artifact = _render(template_dir, model_dump)
    assert _check_round_trip()(artifact, "yaml", model_dump) == []


@pytest.mark.finding_a1
@pytest.mark.false_positive_guard
@pytest.mark.parametrize("language", ["markdown", "text"])
def test_unstructured_languages_report_nothing(language: str) -> None:
    """There is no structure to compare, so the check returns nothing."""
    artifact = "title: true\nowner: #redacted\n"
    assert _check_round_trip()(artifact, language, {"title": "true"}) == []


@pytest.mark.finding_a1
@pytest.mark.false_positive_guard
def test_pyproject_uv_reports_nothing(
    repo_templates: Path, pyproject_uv_input: dict[str, Any]
) -> None:
    """The repository's own template must not trip the check.

    A check that fires on everything is as useless as one that fires on
    nothing.  `python_version: "3.12"` re-lexes to a float and `line_length`
    is a real integer; neither is a finding.
    """
    template = Template(repo_templates / "pyproject-uv")
    model = template.get_schema_class().model_validate(pyproject_uv_input)
    artifact = template.render(model)
    findings = _check_round_trip()(artifact, "toml", model.model_dump(mode="json"))
    assert findings == [], f"false positive on templates/pyproject-uv: {findings}"


@pytest.mark.finding_a1
@pytest.mark.false_positive_guard
def test_pyproject_uv_example_output_reports_nothing(repo_templates: Path) -> None:
    """The checked-in expected output agrees with the checked-in input."""
    template = Template(repo_templates / "pyproject-uv")
    fixture = repo_templates / "pyproject-uv/examples/fastapi.input.json"
    data = json.loads(fixture.read_text(encoding="utf-8"))
    model = template.get_schema_class().model_validate(data)
    expected = (repo_templates / "pyproject-uv/examples/fastapi.output.toml").read_text(
        encoding="utf-8"
    )
    findings = _check_round_trip()(expected, "toml", model.model_dump(mode="json"))
    assert findings == [], f"false positive on the example output: {findings}"


# ---------------------------------------------------------------------------
# End to end: the pipeline must fail the generation
# ---------------------------------------------------------------------------


@pytest.mark.finding_a1
@pytest.mark.parametrize(
    ("value", "expected_type"),
    [("true", "bool"), ("#redacted", "null")],
    ids=["bare-true", "bare-comment"],
)
def test_pipeline_fails_when_a_str_field_changes_type(
    make_template: Callable[..., Path],
    stub_model_generation: Callable[..., list[dict]],
    value: str,
    expected_type: str,
) -> None:
    """A type-confused artifact must fail output validation, not succeed."""
    from templateer.pipeline import generate

    template_dir = make_template(
        "yamlvuln",
        output={"path": "out.yaml", "language": "yaml"},
        schema_source=YAML_SCHEMA,
        template_source="title: {{ title }}\nowner: {{ owner }}\n",
    )
    catalog = TemplateCatalog()
    catalog.load_from_paths([template_dir.parent])

    stub_model_generation(
        lambda template, attempt: template.get_schema_class()(
            title=value, owner=value
        )
    )
    result = generate(
        catalog,
        GenerationRequest(
            template_name="yamlvuln", user_request="x", max_attempts=1
        ),
    )
    assert not result.succeeded, (
        f"a str field that reaches the artifact as {expected_type} must fail: "
        f"{result.artifact!r}"
    )
    assert result.failure_reason == FailureReason.OUTPUT_VALIDATION_FAILED


@pytest.mark.finding_a1
@pytest.mark.false_positive_guard
def test_pipeline_succeeds_for_a_correct_template(
    make_template: Callable[..., Path],
    stub_model_generation: Callable[..., list[dict]],
) -> None:
    """The same values pass when the template quotes its interpolations."""
    from templateer.pipeline import generate

    template_dir = make_template(
        "yamlok",
        output={"path": "out.yaml", "language": "yaml"},
        schema_source=YAML_SCHEMA,
        template_source="title: \"{{ title }}\"\nowner: \"{{ owner }}\"\n",
    )
    catalog = TemplateCatalog()
    catalog.load_from_paths([template_dir.parent])

    stub_model_generation(
        lambda template, attempt: template.get_schema_class()(
            title="true", owner="#redacted"
        )
    )
    result = generate(
        catalog,
        GenerationRequest(template_name="yamlok", user_request="x", max_attempts=1),
    )
    assert result.succeeded, result.error_detail
