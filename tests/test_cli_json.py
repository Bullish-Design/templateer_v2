"""CLI guardrails — §A7 and §B9.

Of seven commands exactly one (`schema`) emits parseable output today, and
every error class exits 1.  An agent cannot tell "no such template" from "the
LLM failed" from "the artifact is invalid" without parsing English.

Exit codes come from CONTRACT.md §9.  Uses `click.testing.CliRunner`, as
`tests/test_cli.py` already does.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from templateer.cli import main
from templateer.result import FailureReason

SCHEMA_ONE_STRING = """\
from pydantic import BaseModel


class M(BaseModel):
    x: str
"""

# CONTRACT.md §9.  0 ok, 1 finding/decision, 2 infra/config, 3 usage.
EXPECTED_EXIT_CODES: dict[FailureReason, int] = {
    FailureReason.MODEL_VALIDATION_FAILED: 1,
    FailureReason.RENDER_FAILED: 1,
    FailureReason.OUTPUT_VALIDATION_FAILED: 1,
    FailureReason.CONFIG_ERROR: 2,
    FailureReason.LLM_FAILED: 2,
    FailureReason.NO_TEMPLATE: 3,
}


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def good_template(make_template: Callable[..., Path]) -> Path:
    """A sound template whose artifact always parses."""
    return make_template(
        "okltoml",
        output={"path": "out.toml", "language": "toml"},
        schema_source=SCHEMA_ONE_STRING,
        template_source='name = "{{ x }}"\n',
        fixtures={"basic.input.json": {"x": "hi"}},
        trigger_filenames=["out.toml"],
    )


@pytest.fixture
def input_file(tmp_path: Path) -> Path:
    path = tmp_path / "model.json"
    path.write_text(json.dumps({"x": "hi"}), encoding="utf-8")
    return path


def _paths(template_dir: Path) -> list[str]:
    return ["--paths", str(template_dir.parent)]


def _json_object(result: Any) -> dict[str, Any]:
    """Parse stdout as one JSON object, and require it to be the only output."""
    assert result.stdout.strip(), f"no stdout (exit={result.exit_code})"
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict), f"expected one JSON object, got {type(payload)}"
    return payload


# ---------------------------------------------------------------------------
# §A7 — every command emits one JSON object
# ---------------------------------------------------------------------------


@pytest.mark.finding_a7
def test_list_json_is_a_single_object(
    runner: CliRunner, good_template: Path
) -> None:
    result = runner.invoke(main, ["list", "--json", *_paths(good_template)])
    payload = _json_object(result)
    assert result.exit_code == 0, result.stderr
    assert "okltoml" in json.dumps(payload)


@pytest.mark.finding_a7
def test_describe_json_is_a_single_object(
    runner: CliRunner, good_template: Path
) -> None:
    result = runner.invoke(
        main, ["describe", "okltoml", "--json", *_paths(good_template)]
    )
    payload = _json_object(result)
    assert result.exit_code == 0, result.stderr
    assert payload["name"] == "okltoml"


@pytest.mark.finding_a7
def test_render_json_is_a_single_object(
    runner: CliRunner, good_template: Path, input_file: Path
) -> None:
    result = runner.invoke(
        main,
        ["render", "okltoml", "-i", str(input_file), "--json", *_paths(good_template)],
    )
    payload = _json_object(result)
    assert result.exit_code == 0, result.stderr
    # Assert against the field, not a re-serialization: json.dumps escapes the
    # artifact's own quotes, so `name = "hi"` never appears in the dumped text.
    assert 'name = "hi"' in payload["artifact"]


@pytest.mark.finding_a7
def test_validate_json_is_a_single_object(
    runner: CliRunner, good_template: Path, input_file: Path
) -> None:
    result = runner.invoke(
        main,
        [
            "validate",
            "okltoml",
            "-i",
            str(input_file),
            "--json",
            *_paths(good_template),
        ],
    )
    payload = _json_object(result)
    assert result.exit_code == 0, result.stderr
    assert payload


@pytest.mark.finding_a7
def test_check_json_is_the_audit_report(
    runner: CliRunner, good_template: Path
) -> None:
    result = runner.invoke(main, ["check", "okltoml", "--json", *_paths(good_template)])
    payload = _json_object(result)
    assert result.exit_code == 0, result.stderr
    assert payload["findings"] == []
    assert payload["fixtures_seen"] >= 1
    assert payload["fields_probed"] > 0
    assert payload["fields_skipped"] == []


@pytest.mark.finding_a3
def test_check_reports_schema_fields_that_constraints_skip(
    runner: CliRunner, make_template: Callable[..., Path]
) -> None:
    """Both CLI forms expose incomplete schema-field coverage."""
    template_dir = make_template(
        "skipfield",
        output={"path": "out.toml", "language": "toml"},
        schema_source=(
            "from typing import Literal\n"
            "from pydantic import BaseModel\n\n\n"
            "class M(BaseModel):\n"
            "    x: str\n"
            "    constrained: Literal['SAFE'] | None = None\n"
        ),
        template_source=(
            'x = "{{ x }}"\n'
            "{% if constrained is not none %}"
            'constrained = "{{ constrained }}"\n'
            "{% endif %}"
        ),
        fixtures={"minimal.input.json": {"x": "present"}},
    )

    structured = runner.invoke(
        main, ["check", "skipfield", "--json", *_paths(template_dir)]
    )
    payload = _json_object(structured)
    assert structured.exit_code == 0
    assert payload["fields_probed"] == 1
    assert payload["fields_skipped"][0]["field"] == "constrained"

    prose = runner.invoke(main, ["check", "skipfield", *_paths(template_dir)])
    assert prose.exit_code == 0
    assert "1 field(s) skipped" in prose.output
    assert "constrained" in prose.output


@pytest.mark.finding_a7
def test_generate_json_is_the_result_model_dump(
    runner: CliRunner,
    good_template: Path,
    stub_model_generation: Callable[..., list[dict]],
) -> None:
    """The structured failure object already exists; emit it verbatim."""
    from templateer.catalog import TemplateCatalog
    from templateer.pipeline import generate
    from templateer.result import GenerationRequest

    stub_model_generation(
        lambda template, attempt: template.get_schema_class()(x="hi")
    )
    result = runner.invoke(
        main,
        [
            "generate",
            "okltoml",
            "-r",
            "make it",
            "--max-attempts",
            "1",
            "--json",
            *_paths(good_template),
        ],
    )
    payload = _json_object(result)
    assert result.exit_code == 0, result.stderr

    catalog = TemplateCatalog()
    catalog.load_from_paths([good_template.parent])
    expected = generate(
        catalog,
        GenerationRequest(
            template_name="okltoml", user_request="make it", max_attempts=1
        ),
    )
    assert payload == json.loads(json.dumps(expected.model_dump(), default=str))


@pytest.mark.finding_a7
@pytest.mark.parametrize(
    "command",
    [
        ["list"],
        ["describe", "okltoml"],
        ["check", "okltoml"],
    ],
    ids=["list", "describe", "check"],
)
def test_json_mode_prints_no_prose_on_stdout(
    runner: CliRunner, good_template: Path, command: list[str]
) -> None:
    """Under `--json`: no colour, no ticks, no prose on stdout."""
    result = runner.invoke(main, [*command, "--json", *_paths(good_template)])
    assert "✓" not in result.stdout
    assert "✗" not in result.stdout
    json.loads(result.stdout)


# ---------------------------------------------------------------------------
# §A7 — describe must not print a Python repr
# ---------------------------------------------------------------------------


@pytest.mark.finding_a7
def test_describe_never_prints_a_set_repr(
    runner: CliRunner, good_template: Path
) -> None:
    """`Trigger paths: {'pyproject.toml'}` is a Python repr, not output."""
    result = runner.invoke(main, ["describe", "okltoml", *_paths(good_template)])
    assert result.exit_code == 0, result.stderr
    assert "{'" not in result.stdout, result.stdout
    assert "out.toml" in result.stdout


@pytest.mark.finding_a7
def test_describe_json_trigger_paths_is_a_list(
    runner: CliRunner, good_template: Path
) -> None:
    result = runner.invoke(
        main, ["describe", "okltoml", "--json", *_paths(good_template)]
    )
    payload = _json_object(result)
    triggers = payload.get("trigger_filenames", payload.get("trigger_paths"))
    assert isinstance(triggers, list), payload
    assert triggers == ["out.toml"]


# ---------------------------------------------------------------------------
# §A7 — one exit code per FailureReason
# ---------------------------------------------------------------------------


@pytest.mark.finding_a7
def test_exit_codes_table_matches_the_contract() -> None:
    """`templateer.cli.EXIT_CODES` is the machine-readable contract."""
    from templateer.cli import EXIT_CODES

    assert dict(EXIT_CODES) == EXPECTED_EXIT_CODES


@pytest.mark.finding_a7
def test_no_template_exits_three(runner: CliRunner, good_template: Path) -> None:
    """"No such template" is a usage error, not a finding."""
    result = runner.invoke(
        main,
        ["generate", "nosuch", "-r", "x", "--json", *_paths(good_template)],
    )
    assert result.exit_code == EXPECTED_EXIT_CODES[FailureReason.NO_TEMPLATE]
    payload = _json_object(result)
    assert payload["failure_reason"] == FailureReason.NO_TEMPLATE.value


@pytest.mark.finding_a7
def test_output_validation_failure_exits_one(
    runner: CliRunner,
    make_template: Callable[..., Path],
    stub_model_generation: Callable[..., list[dict]],
) -> None:
    """An invalid artifact is a finding: exit 1."""
    template_dir = make_template(
        "badtoml",
        output={"path": "out.toml", "language": "toml"},
        schema_source=SCHEMA_ONE_STRING,
        template_source="name = {{ x }}\n",
    )
    stub_model_generation(
        lambda template, attempt: template.get_schema_class()(x="hi")
    )
    result = runner.invoke(
        main,
        [
            "generate",
            "badtoml",
            "-r",
            "x",
            "--max-attempts",
            "1",
            "--json",
            *_paths(template_dir),
        ],
    )
    payload = _json_object(result)
    assert payload["failure_reason"] == FailureReason.OUTPUT_VALIDATION_FAILED.value
    assert result.exit_code == 1


@pytest.mark.finding_a7
def test_render_failure_exits_one(
    runner: CliRunner,
    make_template: Callable[..., Path],
    stub_model_generation: Callable[..., list[dict]],
) -> None:
    """A schema/template drift is a finding: exit 1."""
    template_dir = make_template(
        "drift",
        output={"path": "out.toml", "language": "toml"},
        schema_source=SCHEMA_ONE_STRING,
        template_source='name = "{{ absent_field }}"\n',
    )
    stub_model_generation(
        lambda template, attempt: template.get_schema_class()(x="hi")
    )
    result = runner.invoke(
        main,
        [
            "generate",
            "drift",
            "-r",
            "x",
            "--max-attempts",
            "1",
            "--json",
            *_paths(template_dir),
        ],
    )
    payload = _json_object(result)
    assert payload["failure_reason"] == FailureReason.RENDER_FAILED.value
    assert result.exit_code == 1


@pytest.mark.finding_a7
def test_llm_failure_exits_two(
    runner: CliRunner,
    good_template: Path,
    stub_model_generation: Callable[..., list[dict]],
) -> None:
    """A provider error is infrastructure, not a finding: exit 2."""

    def boom(template: Any, attempt: int) -> Any:
        raise RuntimeError("the provider exploded")

    stub_model_generation(boom)
    result = runner.invoke(
        main,
        [
            "generate",
            "okltoml",
            "-r",
            "x",
            "--max-attempts",
            "1",
            "--json",
            *_paths(good_template),
        ],
    )
    payload = _json_object(result)
    assert payload["failure_reason"] == FailureReason.LLM_FAILED.value
    assert result.exit_code == 2


@pytest.mark.finding_a7
def test_config_error_exits_two(
    runner: CliRunner,
    good_template: Path,
    stub_model_generation: Callable[..., list[dict]],
) -> None:
    """A missing API key is caller misconfiguration: exit 2."""
    from pydantic_ai.exceptions import UserError

    def boom(template: Any, attempt: int) -> Any:
        raise UserError("no API key configured")

    stub_model_generation(boom)
    result = runner.invoke(
        main,
        [
            "generate",
            "okltoml",
            "-r",
            "x",
            "--max-attempts",
            "1",
            "--json",
            *_paths(good_template),
        ],
    )
    payload = _json_object(result)
    assert payload["failure_reason"] == FailureReason.CONFIG_ERROR.value
    assert result.exit_code == 2


@pytest.mark.finding_a7
def test_model_validation_failure_exits_one(
    runner: CliRunner,
    good_template: Path,
    stub_model_generation: Callable[..., list[dict]],
) -> None:
    """The LLM could not satisfy the schema: a finding, exit 1."""
    from pydantic_ai.exceptions import UnexpectedModelBehavior

    def boom(template: Any, attempt: int) -> Any:
        raise UnexpectedModelBehavior("retries exhausted")

    stub_model_generation(boom)
    result = runner.invoke(
        main,
        [
            "generate",
            "okltoml",
            "-r",
            "x",
            "--max-attempts",
            "1",
            "--json",
            *_paths(good_template),
        ],
    )
    payload = _json_object(result)
    assert payload["failure_reason"] == FailureReason.MODEL_VALIDATION_FAILED.value
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# §A3 — `check` exits non-zero when nothing was audited
# ---------------------------------------------------------------------------


@pytest.mark.finding_a3
def test_check_exits_non_zero_when_nothing_was_audited(
    runner: CliRunner, make_template: Callable[..., Path]
) -> None:
    """A green tick after auditing nothing is a false proof."""
    template_dir = make_template(
        "noex",
        output={"path": "o.toml", "language": "toml"},
        schema_source=SCHEMA_ONE_STRING,
        template_source='name = "{{ x }}"\n',
    )
    result = runner.invoke(main, ["check", "noex", *_paths(template_dir)])
    assert result.exit_code != 0, result.stdout


@pytest.mark.finding_a3
def test_check_json_reports_the_skip_reason(
    runner: CliRunner, make_template: Callable[..., Path]
) -> None:
    template_dir = make_template(
        "noex",
        output={"path": "o.toml", "language": "toml"},
        schema_source=SCHEMA_ONE_STRING,
        template_source='name = "{{ x }}"\n',
    )
    result = runner.invoke(main, ["check", "noex", "--json", *_paths(template_dir)])
    payload = _json_object(result)
    assert payload["skipped_reason"]
    assert payload["fixtures_seen"] == 0
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# §B9 — a broken template must not disappear
# ---------------------------------------------------------------------------


@pytest.fixture
def broken_template(tmp_path: Path, good_template: Path) -> Path:
    """A second template beside the good one whose metadata does not load."""
    broken = good_template.parent / "brokentpl"
    broken.mkdir()
    (broken / "metadata.yml").write_text(
        "name: brokentpl\noutput: {path: o.toml, language: toml}\n", encoding="utf-8"
    )
    return broken


@pytest.mark.finding_b9
def test_list_surfaces_a_broken_template(
    runner: CliRunner, good_template: Path, broken_template: Path
) -> None:
    """"Broken" must be distinguishable from "absent"."""
    result = runner.invoke(main, ["list", *_paths(good_template)])
    combined = result.stdout + result.stderr
    assert "brokentpl" in combined, combined


@pytest.mark.finding_b9
def test_list_json_carries_the_load_errors(
    runner: CliRunner, good_template: Path, broken_template: Path
) -> None:
    result = runner.invoke(main, ["list", "--json", *_paths(good_template)])
    payload = _json_object(result)
    assert payload["load_errors"], payload
    assert "brokentpl" in json.dumps(payload["load_errors"])


@pytest.mark.finding_b9
def test_list_strict_is_fatal(
    runner: CliRunner, good_template: Path, broken_template: Path
) -> None:
    """`--strict` turns a load error into an infrastructure failure."""
    result = runner.invoke(main, ["list", "--strict", *_paths(good_template)])
    assert "No such option" not in result.stderr, "--strict is not implemented"
    assert result.exit_code == 2


@pytest.mark.finding_b9
@pytest.mark.false_positive_guard
def test_list_strict_is_clean_without_load_errors(
    runner: CliRunner, good_template: Path
) -> None:
    """`--strict` must not fail a catalog that loaded cleanly."""
    result = runner.invoke(main, ["list", "--strict", *_paths(good_template)])
    assert result.exit_code == 0, result.stderr
