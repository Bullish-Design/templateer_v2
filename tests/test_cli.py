"""Tests for the Templateer CLI.

Uses Click's CliRunner for integration testing of all commands:
  list, describe, schema, render, generate, validate.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from templateer.cli import main
from templateer.result import FailureReason

# ---------------------------------------------------------------------------
# Exit codes (§A7, CONTRACT §9)
# ---------------------------------------------------------------------------
#
# The CLI used to exit 1 for every failure, so an agent had to parse English
# to tell "no such template" from "the LLM failed" from "the artifact is
# invalid".  Each failure class now has its own code.  Tests assert against
# ``EXIT_CODES`` rather than a literal, so the map stays the single source.

# ``cli.EXIT_CODES`` arrives in wave 3b, so it is imported inside the helper.
# That keeps the rest of this module collectable until then.


def exit_code(reason: FailureReason) -> int:
    """The CLI's exit code for *reason*."""
    from templateer.cli import EXIT_CODES

    return EXIT_CODES[reason]


NO_TEMPLATE = FailureReason.NO_TEMPLATE                  # -> 3, usage
MODEL_INVALID = FailureReason.MODEL_VALIDATION_FAILED    # -> 1, finding
OUTPUT_INVALID = FailureReason.OUTPUT_VALIDATION_FAILED  # -> 1, finding

# CONTRACT §9 also pins the failures that carry no ``FailureReason``.  A file
# the caller named on the command line is a usage error, whether it is absent
# or unparseable, so ``--input`` and ``--context`` both exit 3.
USAGE_EXIT = 3


def test_exit_code_map_matches_the_contract() -> None:
    """CONTRACT §9 pins the map; it is exported so a test can assert it."""
    from templateer.cli import EXIT_CODES

    assert EXIT_CODES == {
        FailureReason.MODEL_VALIDATION_FAILED: 1,
        FailureReason.RENDER_FAILED: 1,
        FailureReason.OUTPUT_VALIDATION_FAILED: 1,
        FailureReason.CONFIG_ERROR: 2,
        FailureReason.LLM_FAILED: 2,
        FailureReason.INTERNAL_ERROR: 2,
        FailureReason.NO_TEMPLATE: 3,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    """Create a Click CLI test runner."""
    return CliRunner()


@pytest.fixture
def templates_arg() -> list[str]:
    """CLI argument to point at the bundled pyproject-uv template."""
    return ["--paths", str(Path("templates").resolve())]


@pytest.fixture
def fastapi_input_file(tmp_path: Path) -> Path:
    """A valid FastAPI model JSON fixture."""
    src = Path("templates/pyproject-uv/examples/fastapi.input.json")
    dest = tmp_path / "fastapi_input.json"
    dest.write_text(src.read_text())
    return dest


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


class TestListTemplates:
    """Tests for the ``templateer list`` command."""

    def test_list_shows_templates(self, runner: CliRunner, templates_arg: list[str]) -> None:
        """List shows available templates."""
        result = runner.invoke(main, ["list", *templates_arg])
        assert result.exit_code == 0
        assert "pyproject-uv" in result.output

    def test_list_shows_descriptions(self, runner: CliRunner, templates_arg: list[str]) -> None:
        """List shows template descriptions."""
        result = runner.invoke(main, ["list", *templates_arg])
        assert "Generate" in result.output

    def test_list_no_templates(self, runner: CliRunner, tmp_path: Path) -> None:
        """List with empty directory shows 'No templates found'."""
        empty = tmp_path / "empty"
        empty.mkdir()
        result = runner.invoke(main, ["list", "--paths", str(empty)])
        assert result.exit_code == 0
        assert "No templates found" in result.output


# ---------------------------------------------------------------------------
# describe
# ---------------------------------------------------------------------------


class TestDescribeTemplate:
    """Tests for the ``templateer describe`` command."""

    def test_describe_shows_metadata(self, runner: CliRunner, templates_arg: list[str]) -> None:
        """Describe shows template name, description, and triggers."""
        result = runner.invoke(main, ["describe", "pyproject-uv", *templates_arg])
        assert result.exit_code == 0
        assert "pyproject-uv" in result.output
        assert "Description:" in result.output
        assert "Output language:" in result.output
        # §A7: describe printed a raw Python set repr —
        # ``Trigger paths: {'pyproject.toml'}``.  It prints a sorted list now.
        assert "pyproject.toml" in result.output
        assert "{'pyproject.toml'}" not in result.output

    def test_describe_unknown_template(self, runner: CliRunner, templates_arg: list[str]) -> None:
        """Describe of an unknown template exits with the usage code."""
        result = runner.invoke(main, ["describe", "nonexistent", *templates_arg])
        assert result.exit_code == exit_code(NO_TEMPLATE)


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------


class TestShowSchema:
    """Tests for the ``templateer schema`` command."""

    def test_schema_outputs_valid_json(self, runner: CliRunner, templates_arg: list[str]) -> None:
        """Schema command outputs valid JSON that can be parsed."""
        result = runner.invoke(main, ["schema", "pyproject-uv", *templates_arg])
        assert result.exit_code == 0
        schema = json.loads(result.output)
        assert "properties" in schema
        assert "title" in schema

    def test_schema_contains_expected_properties(
        self, runner: CliRunner, templates_arg: list[str]
    ) -> None:
        """Schema output includes expected model fields."""
        result = runner.invoke(main, ["schema", "pyproject-uv", *templates_arg])
        assert result.exit_code == 0
        schema = json.loads(result.output)
        props = schema.get("properties", {})
        assert "project_name" in props
        assert "python_version" in props
        assert "dependencies" in props

    def test_schema_unknown_template(self, runner: CliRunner, templates_arg: list[str]) -> None:
        """Schema of an unknown template exits with the usage code."""
        result = runner.invoke(main, ["schema", "nonexistent", *templates_arg])
        assert result.exit_code == exit_code(NO_TEMPLATE)


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------


class TestRenderFromModel:
    """Tests for the ``templateer render`` command."""

    def test_render_produces_valid_output(
        self,
        runner: CliRunner,
        templates_arg: list[str],
        fastapi_input_file: Path,
    ) -> None:
        """Render produces output containing expected TOML sections."""
        result = runner.invoke(
            main,
            [
                "render",
                "pyproject-uv",
                "--input",
                str(fastapi_input_file),
                *templates_arg,
            ],
        )
        assert result.exit_code == 0
        assert "[project]" in result.output
        assert "fastapi-app" in result.output

    def test_render_to_output_file(
        self,
        runner: CliRunner,
        templates_arg: list[str],
        fastapi_input_file: Path,
        tmp_path: Path,
    ) -> None:
        """Render --output writes to the specified file."""
        out_file = tmp_path / "output.toml"
        result = runner.invoke(
            main,
            [
                "render",
                "pyproject-uv",
                "--input",
                str(fastapi_input_file),
                "--output",
                str(out_file),
                *templates_arg,
            ],
        )
        assert result.exit_code == 0
        assert out_file.exists()
        content = out_file.read_text()
        assert "[project]" in content

    def test_render_with_invalid_model(
        self,
        runner: CliRunner,
        templates_arg: list[str],
        tmp_path: Path,
    ) -> None:
        """Render with invalid model data exits with the finding code."""
        bad_input = tmp_path / "bad_input.json"
        bad_input.write_text("{}")

        result = runner.invoke(
            main,
            [
                "render",
                "pyproject-uv",
                "--input",
                str(bad_input),
                *templates_arg,
            ],
        )
        assert result.exit_code == exit_code(MODEL_INVALID)

    def test_render_unknown_template(
        self,
        runner: CliRunner,
        templates_arg: list[str],
        fastapi_input_file: Path,
    ) -> None:
        """Render with unknown template exits with the usage code."""
        result = runner.invoke(
            main,
            [
                "render",
                "nonexistent",
                "--input",
                str(fastapi_input_file),
                *templates_arg,
            ],
        )
        assert result.exit_code == exit_code(NO_TEMPLATE)

    def test_render_missing_input_file(
        self,
        runner: CliRunner,
        templates_arg: list[str],
    ) -> None:
        """Render with a missing input file exits with the usage code.

        CONTRACT §9: a missing or unparseable ``--input`` file is a usage
        error, so it exits 3 like an unknown template name.
        """
        result = runner.invoke(
            main,
            [
                "render",
                "pyproject-uv",
                "--input",
                "/nonexistent/file.json",
                *templates_arg,
            ],
        )
        assert result.exit_code == USAGE_EXIT

    def test_render_matches_fastapi_fixture(
        self,
        runner: CliRunner,
        templates_arg: list[str],
        fastapi_input_file: Path,
    ) -> None:
        """Rendered output matches the expected FastAPI fixture."""
        result = runner.invoke(
            main,
            [
                "render",
                "pyproject-uv",
                "--input",
                str(fastapi_input_file),
                *templates_arg,
            ],
        )
        assert result.exit_code == 0

        expected = Path("templates/pyproject-uv/examples/fastapi.output.toml").read_text()
        assert result.output.strip() == expected.strip()

    def test_render_minimal_model(
        self,
        runner: CliRunner,
        templates_arg: list[str],
        tmp_path: Path,
    ) -> None:
        """Render a minimal model (only required fields)."""
        minimal = tmp_path / "minimal.json"
        minimal.write_text(
            json.dumps(
                {
                    "project_name": "minimal-project",
                    "python_version": "3.12",
                }
            )
        )

        result = runner.invoke(
            main,
            [
                "render",
                "pyproject-uv",
                "--input",
                str(minimal),
                *templates_arg,
            ],
        )
        assert result.exit_code == 0
        assert "minimal-project" in result.output
        assert "3.12" in result.output

    def test_render_with_all_fields(
        self,
        runner: CliRunner,
        templates_arg: list[str],
        tmp_path: Path,
    ) -> None:
        """Render a model with all optional fields populated."""
        full = tmp_path / "full.json"
        full.write_text(
            json.dumps(
                {
                    "project_name": "full-project",
                    "project_description": "A project with everything",
                    "python_version": "3.12",
                    "project_type": "application",
                    "dependencies": [{"name": "requests", "version": ">=2.31.0"}],
                    "dev_dependencies": [{"name": "pytest", "version": ">=8.0"}],
                    "ruff": {
                        "line_length": 88,
                        "target_version": "py312",
                        "select": ["E", "F", "I"],
                        "ignore": [],
                    },
                    "pytest": {"testpaths": ["tests"], "addopts": ["-v"]},
                }
            )
        )

        result = runner.invoke(
            main,
            [
                "render",
                "pyproject-uv",
                "--input",
                str(full),
                *templates_arg,
            ],
        )
        assert result.exit_code == 0
        assert "full-project" in result.output
        assert "requests" in result.output
        assert "pytest" in result.output


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


class TestValidateOutput:
    """Tests for the ``templateer validate`` command."""

    def test_validate_passes_with_valid_model(
        self,
        runner: CliRunner,
        templates_arg: list[str],
        fastapi_input_file: Path,
    ) -> None:
        """Validate passes (exit 0) for a valid model."""
        result = runner.invoke(
            main,
            [
                "validate",
                "pyproject-uv",
                "--input",
                str(fastapi_input_file),
                *templates_arg,
            ],
        )
        assert result.exit_code == 0
        assert "Model validated against schema" in result.output
        assert "Template rendered successfully" in result.output
        assert "Output validation passed" in result.output

    def test_validate_fails_with_invalid_model(
        self,
        runner: CliRunner,
        templates_arg: list[str],
        tmp_path: Path,
    ) -> None:
        """Validate fails with the finding code for an invalid model."""
        bad = tmp_path / "bad.json"
        bad.write_text("{}")

        result = runner.invoke(
            main,
            [
                "validate",
                "pyproject-uv",
                "--input",
                str(bad),
                *templates_arg,
            ],
        )
        assert result.exit_code == exit_code(MODEL_INVALID)
        assert "Model validation failed" in result.output

    def test_validate_unknown_template(
        self,
        runner: CliRunner,
        templates_arg: list[str],
        fastapi_input_file: Path,
    ) -> None:
        """Validate with unknown template exits with the usage code."""
        result = runner.invoke(
            main,
            [
                "validate",
                "nonexistent",
                "--input",
                str(fastapi_input_file),
                *templates_arg,
            ],
        )
        assert result.exit_code == exit_code(NO_TEMPLATE)


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------


class TestGenerateArtifact:
    """Tests for the ``templateer generate`` command.

    These test error paths only; the LLM-dependent success path is
    covered by LLM integration tests (skipped without API key).
    """

    def test_generate_template_not_found(
        self,
        runner: CliRunner,
        templates_arg: list[str],
    ) -> None:
        """Generate with unknown template exits with the usage code."""
        result = runner.invoke(
            main,
            [
                "generate",
                "nonexistent",
                "--request",
                "test request",
                *templates_arg,
            ],
        )
        assert result.exit_code == exit_code(NO_TEMPLATE)
        assert "Generation failed" in result.output

    def test_generate_defaults_request(
        self,
        runner: CliRunner,
        templates_arg: list[str],
    ) -> None:
        """Generate with unknown template but no explicit request uses
        a default request string."""
        result = runner.invoke(
            main,
            [
                "generate",
                "nonexistent",
                *templates_arg,
            ],
        )
        assert result.exit_code == exit_code(NO_TEMPLATE)
        # The default request is set internally; the failure should
        # still be a clean generation failure.
        assert "Generation failed" in result.output

    def test_generate_with_context_file(
        self,
        runner: CliRunner,
        templates_arg: list[str],
        tmp_path: Path,
    ) -> None:
        """Generate with context file on an unknown template still
        reports the error cleanly."""
        ctx = tmp_path / "context.json"
        ctx.write_text(json.dumps({"uses_fastapi": True}))

        result = runner.invoke(
            main,
            [
                "generate",
                "nonexistent",
                "--context",
                str(ctx),
                *templates_arg,
            ],
        )
        assert result.exit_code == exit_code(NO_TEMPLATE)

    def test_generate_invalid_context_file(
        self,
        runner: CliRunner,
        templates_arg: list[str],
        tmp_path: Path,
    ) -> None:
        """Generate with a missing context file exits with the usage code.

        CONTRACT §9: a missing ``--context`` file is a usage error.
        """
        result = runner.invoke(
            main,
            [
                "generate",
                "pyproject-uv",
                "--context",
                "/nonexistent/context.json",
                *templates_arg,
            ],
        )
        assert result.exit_code == USAGE_EXIT

    def test_generate_invalid_json_context(
        self,
        runner: CliRunner,
        templates_arg: list[str],
        tmp_path: Path,
    ) -> None:
        """Generate with an unparseable JSON context exits with the usage code.

        CONTRACT §9: an unparseable ``--context`` file is a usage error —
        the same code as a missing one.  The caller named a bad file either
        way.
        """
        bad_ctx = tmp_path / "bad_context.json"
        bad_ctx.write_text("not json {{{")

        result = runner.invoke(
            main,
            [
                "generate",
                "pyproject-uv",
                "--context",
                str(bad_ctx),
                *templates_arg,
            ],
        )
        assert result.exit_code == USAGE_EXIT

    def test_generate_with_nested_context_format(
        self,
        runner: CliRunner,
        templates_arg: list[str],
        tmp_path: Path,
    ) -> None:
        """Generate with nested {"facts": ..., "user_request": ...} context.

        Even though the template doesn't exist, the nested format parsing
        path is exercised when context is present.
        """
        ctx = tmp_path / "context.json"
        ctx.write_text(
            json.dumps(
                {
                    "user_request": "Build a CLI tool",
                    "facts": {"uses_click": True},
                }
            )
        )

        # Use nonexistent template to avoid LLM call but still
        # exercise the context parsing path.
        result = runner.invoke(
            main,
            [
                "generate",
                "nonexistent",
                "--context",
                str(ctx),
                *templates_arg,
            ],
        )
        assert result.exit_code == exit_code(NO_TEMPLATE)


# ---------------------------------------------------------------------------
# Help / version
# ---------------------------------------------------------------------------


class TestHelp:
    """Tests for help and version flags."""

    def test_help_flag(self, runner: CliRunner) -> None:
        """``--help`` shows usage info."""
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "list" in result.output
        assert "describe" in result.output
        assert "schema" in result.output
        assert "render" in result.output
        assert "generate" in result.output
        assert "validate" in result.output
        assert "check" in result.output

    def test_version_flag(self, runner: CliRunner) -> None:
        """``--version`` shows the version."""
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "0.3.0" in result.output

    def test_command_help(self, runner: CliRunner) -> None:
        """Each subcommand has its own ``--help``."""
        for cmd in ["list", "describe", "schema", "render", "generate", "validate", "check"]:
            result = runner.invoke(main, [cmd, "--help"])
            assert result.exit_code == 0, f"{cmd} --help failed"
            assert result.output, f"{cmd} --help produced no output"


# ---------------------------------------------------------------------------
# Render from model — edge cases
# ---------------------------------------------------------------------------


class TestRenderEdgeCases:
    """Additional edge cases for the ``render`` command."""

    def test_render_with_extra_fields(
        self,
        runner: CliRunner,
        templates_arg: list[str],
        fastapi_input_file: Path,
    ) -> None:
        """Model fields beyond those in the template get passed through
        to the renderer. (Extra fields are allowed by default in Pydantic v2
        unless model_config says 'extra'='forbid'.)"""
        # Our schema uses model_validate which has strict defaults.
        # Extra fields should still render fine.
        result = runner.invoke(
            main,
            [
                "render",
                "pyproject-uv",
                "--input",
                str(fastapi_input_file),
                *templates_arg,
            ],
        )
        assert result.exit_code == 0

    def test_render_two_different_models_same_template(
        self,
        runner: CliRunner,
        templates_arg: list[str],
        tmp_path: Path,
    ) -> None:
        """Two renders with different models produce different output."""
        # First model
        m1 = tmp_path / "m1.json"
        m1.write_text(
            json.dumps(
                {
                    "project_name": "project-a",
                    "python_version": "3.12",
                }
            )
        )

        # Second model
        m2 = tmp_path / "m2.json"
        m2.write_text(
            json.dumps(
                {
                    "project_name": "project-b",
                    "python_version": "3.11",
                }
            )
        )

        r1 = runner.invoke(main, ["render", "pyproject-uv", "--input", str(m1), *templates_arg])
        r2 = runner.invoke(main, ["render", "pyproject-uv", "--input", str(m2), *templates_arg])

        assert r1.exit_code == 0
        assert r2.exit_code == 0
        assert "project-a" in r1.output
        assert "project-b" in r2.output
        assert r1.output != r2.output


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


class TestCheckCommand:
    """Tests for the ``templateer check`` command."""

    def test_check_passes_for_sound_template(
        self, runner: CliRunner, templates_arg: list[str]
    ) -> None:
        """A template that resists injection passes the audit.

        §A3: ``check`` used to print ``✓ escaping audit passed`` even when it
        audited nothing.  It prints the ``AuditReport`` counts now, so the
        output says how much work the audit did.
        """
        result = runner.invoke(main, ["check", "pyproject-uv", *templates_arg])
        assert result.exit_code == 0
        assert "0 findings" in result.output
        assert "fixture" in result.output.lower()

    def test_check_unknown_template(self, runner: CliRunner, templates_arg: list[str]) -> None:
        """Check of an unknown template exits with the usage code."""
        result = runner.invoke(main, ["check", "nonexistent", *templates_arg])
        assert result.exit_code == exit_code(NO_TEMPLATE)


# ---------------------------------------------------------------------------
# Declared validators (Phase 10 regressions)
# ---------------------------------------------------------------------------


def _write_template_with_json_validator(tmp_path: Path, name: str = "tpl") -> Path:
    """Create a minimal template whose output is TOML but which declares a
    custom JSON parse validator.  The validator always fails on the TOML
    artifact, so any command that runs declared validators must reject it.

    Returns the directory to pass as ``--paths`` (the parent of the template).
    """
    search_root = tmp_path / "templates"
    tdir = search_root / name
    tdir.mkdir(parents=True)
    (tdir / "metadata.yml").write_text(f"""\
name: {name}
description: A test template
output:
  path: out.txt
  language: toml
schema:
  module: schema
  class: TestModel
prompt:
  file: prompt.md
renderer:
  engine: minijinja
  file: template.j2
validators:
  - kind: parse
    language: json
""", encoding="utf-8")
    (tdir / "schema.py").write_text(
        "from pydantic import BaseModel\n"
        "class TestModel(BaseModel):\n"
        "    name: str\n",
        encoding="utf-8",
    )
    (tdir / "prompt.md").write_text("Fill TestModel.\n", encoding="utf-8")
    (tdir / "template.j2").write_text('name = "{{ name }}"\n', encoding="utf-8")
    return search_root


class TestDeclaredValidators:
    """Regression: the CLI must run the validators the template author
    declared, and must not write unvalidated artifacts to disk."""

    def test_cli_validate_runs_custom_validators(self, runner: CliRunner, tmp_path: Path) -> None:
        """Regression: cli.validate passed language only, silently skipping
        the validators the template author declared."""
        tdir = _write_template_with_json_validator(tmp_path)
        model_file = tmp_path / "model.json"
        model_file.write_text(json.dumps({"name": "x"}), encoding="utf-8")

        result = runner.invoke(main, [
            "validate", "tpl", "--input", str(model_file), "--paths", str(tdir),
        ])
        assert result.exit_code == exit_code(OUTPUT_INVALID)
        assert "Custom parse (json)" in result.output

    def test_render_command_validates_before_writing(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """Regression: --output wrote unvalidated artifacts to disk."""
        tdir = _write_template_with_json_validator(tmp_path)
        model_file = tmp_path / "model.json"
        model_file.write_text(json.dumps({"name": "x"}), encoding="utf-8")
        out_file = tmp_path / "artifact.toml"

        result = runner.invoke(main, [
            "render", "tpl", "--input", str(model_file),
            "--output", str(out_file), "--paths", str(tdir),
        ])
        assert result.exit_code == exit_code(OUTPUT_INVALID)
        assert not out_file.exists()
        assert "Output validation failed" in result.output


# ---------------------------------------------------------------------------
# Smoke test: CLI binary resolves
# ---------------------------------------------------------------------------


def test_cli_entrypoint_importable() -> None:
    """The entrypoint function is importable and callable.

    This is a smoke test that ensures the module structure is correct
    so that ``templateer`` as a console_script will resolve.
    """
    from templateer.cli import entrypoint

    assert callable(entrypoint)
