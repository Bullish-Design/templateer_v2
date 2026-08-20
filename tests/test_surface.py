"""Surface guardrails — §A4, §A8, §A9, §B2, §B6, §B7, §B8, §C1, §C2, §C7.

The library advertises itself for agent frameworks.  These tests exercise the
promises that claim makes: an async entry point, a repair loop that learns, a
total failure contract, and a populated top-level namespace.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from templateer.catalog import TemplateCatalog
from templateer.models import CommandValidator
from templateer.result import GenerationRequest, GenerationResult
from templateer.template import Template, TemplateLoadError
from templateer.validators import validate_output

SCHEMA_ONE_STRING = """\
from pydantic import BaseModel


class M(BaseModel):
    x: str
"""

# A subprocess needs no external tool: the interpreter running the tests is
# always present.  The markers are assembled at run time, so the reported
# error text cannot match them by quoting the command line back.
_OUT = "'DIAG' + '-ON-' + 'STDOUT'"
_ERR = "'DIAG' + '-ON-' + 'STDERR'"
STDOUT_MARKER = "DIAG-ON-STDOUT"
STDERR_MARKER = "DIAG-ON-STDERR"

STDOUT_ONLY = [
    sys.executable,
    "-c",
    f"import sys; sys.stdout.write({_OUT}); sys.exit(1)",
]
STDERR_ONLY = [
    sys.executable,
    "-c",
    f"import sys; sys.stderr.write({_ERR}); sys.exit(1)",
]
BOTH_STREAMS = [
    sys.executable,
    "-c",
    f"import sys; sys.stdout.write({_OUT}); sys.stderr.write({_ERR}); sys.exit(1)",
]
CLEAN_EXIT = [sys.executable, "-c", "import sys; sys.stdout.write('fine')"]


@pytest.mark.finding_c1
def test_wheel_configuration_excludes_the_development_templates() -> None:
    """The installed package must not imply a bundled template catalog."""
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    wheel = project["tool"]["hatch"]["build"]["targets"]["wheel"]

    assert wheel["packages"] == ["src/templateer"]
    assert "templates" not in wheel.get("include", [])


def _catalog(template_dir: Path) -> TemplateCatalog:
    catalog = TemplateCatalog()
    catalog.load_from_paths([template_dir.parent])
    return catalog


def _bad_toml_template(make_template: Callable[..., Path], **kwargs: Any) -> Path:
    """A template whose artifact never parses as TOML."""
    return make_template(
        "badtoml",
        output={"path": "out.toml", "language": "toml"},
        schema_source=SCHEMA_ONE_STRING,
        template_source="name = {{ x }}\n",
        **kwargs,
    )


# ---------------------------------------------------------------------------
# §A9 — the retry loop must learn
# ---------------------------------------------------------------------------


@pytest.mark.finding_a9
def test_attempt_two_prompt_differs_and_carries_attempt_ones_error(
    make_template: Callable[..., Path],
    stub_model_generation: Callable[..., list[dict]],
) -> None:
    """Re-asking with the same prompt is a slot machine, not a repair loop.

    Asserts on the prompt text the model receives, not on a call count.
    """
    from templateer.generator import build_context
    from templateer.pipeline import generate

    template_dir = _bad_toml_template(make_template)
    calls = stub_model_generation(
        lambda template, attempt: template.get_schema_class()(x="hi")
    )
    result = generate(
        _catalog(template_dir),
        GenerationRequest(
            template_name="badtoml", user_request="build it", max_attempts=3
        ),
    )
    assert not result.succeeded
    assert len(calls) == 3, "the pipeline must still retry a retryable failure"

    prompts = [
        build_context("build it", {}, prior_failure=call.get("prior_failure"))
        for call in calls
    ]
    assert prompts[0] != prompts[1], "attempt 2 re-asked with the same prompt"
    assert result.error_detail is not None
    assert result.error_detail in prompts[1], (
        "attempt 2's prompt does not carry attempt 1's error_detail"
    )


@pytest.mark.finding_a9
def test_backoff_applies_to_llm_failure_only(
    make_template: Callable[..., Path],
    stub_model_generation: Callable[..., list[dict]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider error is the one retryable failure that waiting can fix.

    ``conftest`` zeroes the backoff for every other test, so this is the only
    place the schedule is checked.  The sleep is recorded, never taken.
    """
    import templateer.pipeline as pipeline_module

    slept: list[float] = []

    async def _record(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(pipeline_module.asyncio, "sleep", _record)
    monkeypatch.setattr(pipeline_module, "RETRY_BACKOFF_SECONDS", 1.0)

    # An artifact that does not parse: OUTPUT_VALIDATION_FAILED, which the
    # repair loop fixes by changing the prompt.  Waiting buys nothing.
    template_dir = _bad_toml_template(make_template)
    stub_model_generation(
        lambda template, attempt: template.get_schema_class()(x="hi")
    )
    pipeline_module.generate(
        _catalog(template_dir),
        GenerationRequest(
            template_name="badtoml", user_request="x", max_attempts=3
        ),
    )
    assert slept == [], "a repairable failure must not wait"

    # A provider error: LLM_FAILED, doubling per attempt.
    slept.clear()

    def _boom(template: Template, attempt: int) -> Any:
        raise RuntimeError("provider exploded")

    stub_model_generation(_boom)
    pipeline_module.generate(
        _catalog(template_dir),
        GenerationRequest(
            template_name="badtoml", user_request="x", max_attempts=3
        ),
    )
    assert slept == [1.0, 2.0], "LLM_FAILED must back off, doubling per attempt"


@pytest.mark.finding_a9
def test_build_context_appends_the_prior_failure() -> None:
    """`build_context` is where the failure text joins the prompt."""
    from templateer.generator import build_context

    plain = build_context("build it", {})
    repaired = build_context("build it", {}, prior_failure="toml parse failed: boom")
    assert repaired != plain
    assert "toml parse failed: boom" in repaired


@pytest.mark.finding_a9
@pytest.mark.parametrize("attempts", [1, 3, 10])
def test_max_attempts_accepts_the_supported_range(attempts: int) -> None:
    """One to ten whole-pipeline attempts is the supported range."""
    request = GenerationRequest(
        template_name="t", user_request="u", max_attempts=attempts
    )
    assert request.max_attempts == attempts


@pytest.mark.finding_a9
@pytest.mark.parametrize("attempts", [0, 11, 100000])
def test_max_attempts_is_capped(attempts: int) -> None:
    """An uncapped retry budget burns tokens to reach the same answer."""
    with pytest.raises(ValidationError):
        GenerationRequest(template_name="t", user_request="u", max_attempts=attempts)


# ---------------------------------------------------------------------------
# §B6 — warnings survive a failure
# ---------------------------------------------------------------------------


@pytest.mark.finding_b6
def test_warnings_survive_on_a_failed_result(
    make_template: Callable[..., Path],
    stub_model_generation: Callable[..., list[dict]],
) -> None:
    """Optional-validator output is most useful when something also failed."""
    from templateer.pipeline import generate

    template_dir = _bad_toml_template(
        make_template,
        validators=[{"kind": "parse", "language": "json", "optional": True}],
    )
    stub_model_generation(
        lambda template, attempt: template.get_schema_class()(x="hi")
    )
    result = generate(
        _catalog(template_dir),
        GenerationRequest(
            template_name="badtoml", user_request="x", max_attempts=1
        ),
    )
    assert not result.succeeded
    assert result.warnings, "the optional validator's note was dropped"


# ---------------------------------------------------------------------------
# §A8 — the async entry point
# ---------------------------------------------------------------------------


@pytest.mark.finding_a8
def test_pipeline_generate_async_runs_inside_a_running_loop(
    make_template: Callable[..., Path],
    stub_model_generation: Callable[..., list[dict]],
) -> None:
    """`run_sync` raises inside a loop; the async path must not."""
    from templateer.pipeline import generate_async

    template_dir = make_template(
        "okyaml",
        output={"path": "out.yaml", "language": "yaml"},
        schema_source=SCHEMA_ONE_STRING,
        template_source='name: "{{ x }}"\n',
    )
    catalog = _catalog(template_dir)
    stub_model_generation(
        lambda template, attempt: template.get_schema_class()(x="hi")
    )

    async def drive() -> GenerationResult:
        return await generate_async(
            catalog,
            GenerationRequest(
                template_name="okyaml", user_request="x", max_attempts=1
            ),
        )

    result = asyncio.run(drive())
    assert result.succeeded, result.error_detail


@pytest.mark.finding_a8
def test_registry_generate_async_runs_inside_a_running_loop(
    make_template: Callable[..., Path],
    stub_model_generation: Callable[..., list[dict]],
) -> None:
    """The advertised embedding surface must work from an async framework."""
    from templateer.api import TemplateRegistry

    template_dir = make_template(
        "okyaml",
        output={"path": "out.yaml", "language": "yaml"},
        schema_source=SCHEMA_ONE_STRING,
        template_source='name: "{{ x }}"\n',
    )
    registry = TemplateRegistry.from_paths([template_dir.parent])
    stub_model_generation(
        lambda template, attempt: template.get_schema_class()(x="hi")
    )

    async def drive() -> GenerationResult:
        return await registry.generate_async(
            template_name="okyaml", user_request="x", max_attempts=1
        )

    result = asyncio.run(drive())
    assert result.succeeded, result.error_detail


# ---------------------------------------------------------------------------
# §C2 — the public surface
# ---------------------------------------------------------------------------


@pytest.mark.finding_c2
@pytest.mark.parametrize(
    "name",
    ["TemplateRegistry", "GenerationResult", "GenerationRequest", "FailureReason"],
)
def test_package_exports_the_public_surface(name: str) -> None:
    """`import templateer` must expose the names the README uses."""
    import templateer

    assert hasattr(templateer, name), f"templateer.{name} is not exported"
    assert name in templateer.__all__


@pytest.mark.finding_c2
def test_version_is_single_sourced() -> None:
    """A duplicated literal drifts; read it from the installed metadata."""
    import templateer

    assert templateer.__version__ == importlib.metadata.version("templateer")


# ---------------------------------------------------------------------------
# §B8 — one mistake, one error type
# ---------------------------------------------------------------------------


@pytest.mark.finding_b8
def test_render_from_model_raises_validation_error_for_non_mapping(
    repo_templates: Path,
) -> None:
    """The API and the CLI must report the same mistake the same way."""
    from templateer.api import TemplateRegistry

    registry = TemplateRegistry.from_paths([repo_templates])
    with pytest.raises(ValidationError):
        registry.render_from_model("pyproject-uv", ["not", "a", "dict"])


# ---------------------------------------------------------------------------
# §B7 — CommandValidator reports both streams
# ---------------------------------------------------------------------------


@pytest.mark.finding_b7
@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (STDOUT_ONLY, [STDOUT_MARKER]),
        (STDERR_ONLY, [STDERR_MARKER]),
        (BOTH_STREAMS, [STDOUT_MARKER, STDERR_MARKER]),
    ],
    ids=["stdout", "stderr", "both"],
)
def test_command_validator_reports_both_streams(
    command: list[str], expected: list[str]
) -> None:
    """Ruff and most linters write diagnostics to stdout, not stderr."""
    errors, _ = validate_output(
        'name = "x"\n',
        "toml",
        [CommandValidator(kind="command", command=command)],
    )
    assert errors, "a non-zero exit produced no error"
    joined = "\n".join(errors)
    for fragment in expected:
        assert fragment in joined, joined


@pytest.mark.finding_b7
@pytest.mark.false_positive_guard
def test_command_validator_is_silent_on_success() -> None:
    """A clean exit is not a finding, whatever the command printed."""
    errors, warnings = validate_output(
        'name = "x"\n',
        "toml",
        [CommandValidator(kind="command", command=CLEAN_EXIT)],
    )
    assert errors == []
    assert warnings == []


@pytest.mark.finding_b7
def test_command_validator_failure_reaches_the_pipeline(
    make_template: Callable[..., Path],
    stub_model_generation: Callable[..., list[dict]],
) -> None:
    """The subprocess branch, end to end — 0% covered before this test."""
    from templateer.pipeline import generate

    template_dir = make_template(
        "cmdcheck",
        output={"path": "out.toml", "language": "toml"},
        schema_source=SCHEMA_ONE_STRING,
        template_source='name = "{{ x }}"\n',
        validators=[{"kind": "command", "command": STDOUT_ONLY}],
    )
    stub_model_generation(
        lambda template, attempt: template.get_schema_class()(x="hi")
    )
    result = generate(
        _catalog(template_dir),
        GenerationRequest(
            template_name="cmdcheck", user_request="x", max_attempts=1
        ),
    )
    assert not result.succeeded
    assert result.error_detail is not None
    assert STDOUT_MARKER in result.error_detail


# ---------------------------------------------------------------------------
# §B2 — containment for the one file that is executed
# ---------------------------------------------------------------------------


@pytest.mark.finding_b2
def test_schema_module_outside_the_template_root_fails_to_load(
    tmp_path: Path, make_template: Callable[..., Path]
) -> None:
    """`resolve_path` guards prompt.file and renderer.file — and now this."""
    template_dir = make_template(
        "escape",
        output={"path": "out.toml", "language": "toml"},
        template_source='name = "{{ x }}"\n',
        schema_ref={"module": "../../outside_schema", "class": "M"},
        write_schema=False,
        root=tmp_path / "templates_b2",
    )
    (tmp_path / "outside_schema.py").write_text(
        'MARKER = "executed from outside the template root"\n' + SCHEMA_ONE_STRING,
        encoding="utf-8",
    )
    template = Template(template_dir)
    with pytest.raises(TemplateLoadError):
        template.load_schema_module()


# ---------------------------------------------------------------------------
# §A4 — the pipeline promise is total
# ---------------------------------------------------------------------------


@pytest.mark.finding_a4
def test_pipeline_returns_a_result_for_a_renderer_outside_the_root(
    tmp_path: Path,
    make_template: Callable[..., Path],
    stub_model_generation: Callable[..., list[dict]],
) -> None:
    """"Nothing escapes as an exception — that promise is either total or
    worthless." — pipeline.py:8."""
    from templateer.pipeline import generate

    template_dir = make_template(
        "escape",
        output={"path": "out.toml", "language": "toml"},
        schema_source=SCHEMA_ONE_STRING,
        template_source="unused",
        renderer_ref={"engine": "minijinja", "file": "../../outside.j2"},
        write_template_file=False,
        root=tmp_path / "templates_a4",
    )
    (tmp_path / "outside.j2").write_text('name = "{{ x }}"\n', encoding="utf-8")
    stub_model_generation(
        lambda template, attempt: template.get_schema_class()(x="hi")
    )

    result = generate(
        _catalog(template_dir),
        GenerationRequest(template_name="escape", user_request="x", max_attempts=1),
    )
    assert isinstance(result, GenerationResult)
    assert not result.succeeded
    assert result.failure_reason is not None


@pytest.mark.finding_a4
def test_pipeline_returns_a_result_when_the_generator_raises(
    make_template: Callable[..., Path],
    stub_model_generation: Callable[..., list[dict]],
) -> None:
    """An unexpected exception from the model call is still a result."""
    from templateer.pipeline import generate

    def boom(template: Any, attempt: int) -> Any:
        raise RuntimeError("the provider exploded")

    template_dir = make_template(
        "okyaml",
        output={"path": "out.yaml", "language": "yaml"},
        schema_source=SCHEMA_ONE_STRING,
        template_source='name: "{{ x }}"\n',
    )
    stub_model_generation(boom)
    result = generate(
        _catalog(template_dir),
        GenerationRequest(template_name="okyaml", user_request="x", max_attempts=1),
    )
    assert isinstance(result, GenerationResult)
    assert not result.succeeded


# ---------------------------------------------------------------------------
# §C7 — usage on the result
# ---------------------------------------------------------------------------


@pytest.mark.finding_c7
def test_result_carries_usage_when_the_generator_reports_it(
    make_template: Callable[..., Path],
    stub_model_generation: Callable[..., list[dict]],
) -> None:
    """Tokens per artifact is the metric that proves the project's thesis."""
    from templateer.pipeline import generate

    template_dir = make_template(
        "okyaml",
        output={"path": "out.yaml", "language": "yaml"},
        schema_source=SCHEMA_ONE_STRING,
        template_source='name: "{{ x }}"\n',
    )
    stub_model_generation(
        lambda template, attempt: template.get_schema_class()(x="hi"),
        usage={"input_tokens": 30, "output_tokens": 12, "total_tokens": 42},
    )
    result = generate(
        _catalog(template_dir),
        GenerationRequest(template_name="okyaml", user_request="x", max_attempts=1),
    )
    assert result.succeeded, result.error_detail
    assert result.usage == {"input_tokens": 30, "output_tokens": 12, "total_tokens": 42}


@pytest.mark.finding_c7
def test_result_usage_is_none_when_unknown(
    make_template: Callable[..., Path],
    stub_model_generation: Callable[..., list[dict]],
) -> None:
    """`usage` is optional: None means the provider reported nothing."""
    from templateer.pipeline import generate

    template_dir = make_template(
        "okyaml",
        output={"path": "out.yaml", "language": "yaml"},
        schema_source=SCHEMA_ONE_STRING,
        template_source='name: "{{ x }}"\n',
    )
    stub_model_generation(
        lambda template, attempt: template.get_schema_class()(x="hi"), usage=None
    )
    result = generate(
        _catalog(template_dir),
        GenerationRequest(template_name="okyaml", user_request="x", max_attempts=1),
    )
    assert result.usage is None
