"""Templateer CLI — typed, constrained artifact generation for AI agents.

Expose Templateer via a CLI suitable for both human and agent use.

Usage:
    templateer list
    templateer describe <name>
    templateer schema <name>
    templateer render <name> --input model.json
    templateer generate <name> --request "..."
    templateer validate <name> --input model.json
    templateer check <name>

Every command except ``schema`` accepts ``--json``.  ``schema`` already emits
JSON.  Under ``--json`` stdout carries exactly one JSON object: no ticks, no
colour, no prose.  Prose goes to stderr, or nowhere.

Exit codes follow the standing law and CONTRACT.md §9:

    0  the command succeeded
    1  a finding: the model, the render or the artifact is bad
    2  infrastructure or configuration: the LLM failed, a key is missing,
       a template on disk is broken, or the audit could run nothing
    3  usage: the caller named a template or a file the CLI cannot use

``EXIT_CODES`` maps every ``FailureReason`` to its code, so an agent can read
the table instead of parsing English.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, NoReturn

import click

from templateer.audit import audit_template
from templateer.catalog import TemplateCatalog
from templateer.generator import DEFAULT_MODEL
from templateer.pipeline import generate
from templateer.result import FailureReason, GenerationRequest
from templateer.template import Template, TemplateNotFoundError
from templateer.validators import (
    check_round_trip,
    effective_validators,
    validate_output,
)

# ---------------------------------------------------------------------------
# The exit-code contract (§A7, CONTRACT.md §9)
# ---------------------------------------------------------------------------

EXIT_OK = 0
"""The command succeeded."""

EXIT_FINDING = 1
"""The command ran and found a problem in the caller's data."""

EXIT_CONFIG = 2
"""Infrastructure or configuration: nothing the caller's data can fix."""

EXIT_USAGE = 3
"""The caller named a template or a file the CLI cannot use."""

EXIT_CODES: dict[FailureReason, int] = {
    FailureReason.MODEL_VALIDATION_FAILED: EXIT_FINDING,
    FailureReason.RENDER_FAILED: EXIT_FINDING,
    FailureReason.OUTPUT_VALIDATION_FAILED: EXIT_FINDING,
    FailureReason.CONFIG_ERROR: EXIT_CONFIG,
    FailureReason.LLM_FAILED: EXIT_CONFIG,
    FailureReason.NO_TEMPLATE: EXIT_USAGE,
}
"""Exit code per failure reason.  The machine-readable form of the table."""


# ---------------------------------------------------------------------------
# Shared options
# ---------------------------------------------------------------------------


def json_option(command: Callable[..., Any]) -> Callable[..., Any]:
    """Add ``--json`` to a command."""
    return click.option(
        "--json",
        "as_json",
        is_flag=True,
        default=False,
        help="Emit one JSON object on stdout. No prose, no colour.",
    )(command)


def paths_option(command: Callable[..., Any]) -> Callable[..., Any]:
    """Add ``--paths`` to a command."""
    return click.option(
        "--paths",
        "-p",
        multiple=True,
        help="Additional template search paths (replaces defaults).",
    )(command)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _emit_json(payload: dict[str, Any]) -> None:
    """Print one JSON object on stdout."""
    click.echo(json.dumps(payload, indent=2, default=str))


def _finish(
    payload: dict[str, Any],
    as_json: bool,
    prose: Sequence[str],
    code: int,
    *,
    err: bool = True,
) -> NoReturn:
    """Emit the payload under ``--json``, else the prose lines, then exit."""
    if as_json:
        _emit_json(payload)
    else:
        for line in prose:
            click.echo(line, err=err)
    sys.exit(code)


# ---------------------------------------------------------------------------
# Path resolution helpers
# ---------------------------------------------------------------------------


def _resolve_paths(
    extra_paths: tuple[str, ...] | None,
) -> list[Path]:
    """Resolve template search paths from CLI options and defaults.

    If explicit paths are given via ``--paths``, use only those.
    Otherwise use ``./templates`` relative to the current directory.

    Args:
        extra_paths: Tuple of path strings from the ``--paths`` option.

    Returns:
        Resolved Path objects for template discovery.
    """
    if extra_paths:
        return [Path(p) for p in extra_paths]
    cwd_templates = Path.cwd() / "templates"
    return [cwd_templates] if cwd_templates.exists() and cwd_templates.is_dir() else []


def _load_catalog(paths: tuple[str, ...] | None = None) -> TemplateCatalog:
    """Load the template catalog from configured paths.

    Args:
        paths: Optional explicit paths. If None, defaults are used.

    Returns:
        A populated TemplateCatalog. Broken templates land in
        ``catalog.load_errors`` rather than vanishing.
    """
    catalog = TemplateCatalog()
    resolved = _resolve_paths(paths)
    if resolved:
        catalog.load_from_paths(resolved)
    return catalog


def _die_no_template(template_name: str, message: str, as_json: bool) -> NoReturn:
    """Report an unknown template name and exit with the usage code."""
    _finish(
        {
            "template": template_name,
            "failure_reason": FailureReason.NO_TEMPLATE.value,
            "error_detail": message,
        },
        as_json,
        [f"Error: {message}"],
        EXIT_CODES[FailureReason.NO_TEMPLATE],
    )


def _get_template_or_exit(
    template_name: str,
    paths: tuple[str, ...] | None = None,
    as_json: bool = False,
) -> Template:
    """Load a template from the catalog, exiting 3 when the name is unknown.

    ``catalog.get`` raises ``TemplateNotFoundError`` only. A template that
    fails to load is recorded in ``catalog.load_errors``; ``list`` surfaces it.
    """
    catalog = _load_catalog(paths)
    try:
        return catalog.get(template_name)
    except TemplateNotFoundError as e:
        _die_no_template(template_name, str(e), as_json)


def _load_input_json(input_file: str, on_error: Callable[[str], NoReturn]) -> Any:
    """Load and parse a JSON input file.

    A file the caller named that the CLI cannot use is a usage error, whether
    it is absent or unparseable. ``on_error`` reports it and exits.
    """
    input_path = Path(input_file)
    if not input_path.exists():
        on_error(f"input file not found: {input_file}")
    try:
        return json.loads(input_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        on_error(f"error reading input file: {e}")


def _load_context_file(path: Path) -> tuple[str | None, dict[str, Any]]:
    """Parse a context file into ``(user_request, facts)``.

    Accepts either shape, and errors on anything else rather than
    silently producing an empty context:
        {"user_request": "...", "facts": {...}}
        {"any": "flat", "project": "facts"}

    Raises:
        ValueError: the file does not parse, or does not carry the shape.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise ValueError(f"error reading context file {path}: {e}")

    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object, got {type(data).__name__}")
    if "facts" in data or "user_request" in data:
        facts = data.get("facts", {})
        if not isinstance(facts, dict):
            raise ValueError(f"{path}: 'facts' must be an object")
        request = data.get("user_request")
        if request is not None and not isinstance(request, str):
            raise ValueError(f"{path}: 'user_request' must be a string")
        return request, facts
    return None, data


def _template_summary(template: Template) -> dict[str, Any]:
    """The machine-readable description of one template.

    ``trigger_paths`` is a sorted list. A Python ``set`` repr is not output.
    """
    output = template.metadata.output
    return {
        "name": template.name,
        "description": template.description,
        "language": output.language,
        "kind": output.kind,
        "path": output.path,
        "trigger_paths": sorted(template.trigger_paths),
    }


def _load_error_list(catalog: TemplateCatalog) -> list[dict[str, str]]:
    """The catalog's load errors, as JSON-shaped records."""
    return [
        {"template": name, "error": message} for name, message in catalog.load_errors
    ]


# ---------------------------------------------------------------------------
# Click group
# ---------------------------------------------------------------------------


@click.group()
@click.version_option()
def main() -> None:
    """Templateer: typed, constrained artifact generation for AI agents.

    Instead of asking an LLM to write entire files directly, Templateer
    asks an LLM to instantiate a Pydantic model. That validated model is
    then passed to a Jinja renderer. The final file is generated
    deterministically from the template.
    """


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@main.command("list")
@paths_option
@json_option
@click.option(
    "--strict",
    is_flag=True,
    default=False,
    help="Treat a template that failed to load as fatal (exit 2).",
)
def list_templates(paths: tuple[str, ...], as_json: bool, strict: bool) -> None:
    """List all available templates.

    A template that fails to load is reported, not hidden. ``--strict`` makes
    it fatal: a broken template on disk is a configuration problem.
    """
    catalog = _load_catalog(paths)
    load_errors = _load_error_list(catalog)
    payload = {
        "templates": [_template_summary(t) for t in catalog.templates],
        "load_errors": load_errors,
    }

    if as_json:
        _emit_json(payload)
    else:
        if len(catalog) == 0:
            click.echo("No templates found.")
        else:
            click.echo(f"Found {len(catalog)} template(s):\n")
            for template in catalog.templates:
                click.echo(f"  {template.name}")
                click.echo(f"    {template.description}")
                click.echo(f"    Output: {template.output_language}")
                if template.trigger_paths:
                    triggers = ", ".join(sorted(template.trigger_paths))
                    click.echo(f"    Generates: {triggers}")
                click.echo()
        for record in load_errors:
            click.echo(
                f"⚠ {record['template']}: failed to load: {record['error']}", err=True
            )

    if load_errors and strict:
        sys.exit(EXIT_CONFIG)


@main.command("describe")
@click.argument("template_name")
@paths_option
@json_option
def describe_template(
    template_name: str, paths: tuple[str, ...], as_json: bool
) -> None:
    """Describe a template's metadata."""
    template = _get_template_or_exit(template_name, paths, as_json)
    output = template.metadata.output
    # ``region`` exists on RegionOutput only, so bind it before the test.
    region = getattr(output, "region", None)

    payload = _template_summary(template)
    payload["region"] = (
        None
        if region is None
        else {"page": region.page, "ref": region.ref, "anchor": region.anchor}
    )

    if as_json:
        _emit_json(payload)
        return

    triggers = ", ".join(payload["trigger_paths"]) or "-"
    click.echo(f"Name: {template.name}")
    click.echo(f"Description: {template.description}")
    click.echo(f"Output language: {template.output_language}")
    click.echo(f"Trigger paths: {triggers}")
    target = output.path or (region.page if region is not None else "-")
    click.echo(f"  Generates: {target} ({output.language})")
    if region is not None:
        anchor = region.anchor or "-"
        click.echo(f"  Region: page={region.page} ref={region.ref} anchor={anchor}")


@main.command("schema")
@click.argument("template_name")
@paths_option
def show_schema(template_name: str, paths: tuple[str, ...]) -> None:
    """Show the JSON schema for a template."""
    template = _get_template_or_exit(template_name, paths, as_json=True)
    schema = template.get_schema_json()
    click.echo(json.dumps(schema, indent=2))


@main.command("render")
@click.argument("template_name")
@click.option(
    "--input",
    "-i",
    "input_file",
    required=True,
    help="JSON file with validated Pydantic model data.",
)
@click.option(
    "--output",
    "-o",
    "output_file",
    default=None,
    help="Output file (stdout if not given).",
)
@paths_option
@json_option
def render_from_model(
    template_name: str,
    input_file: str,
    output_file: str | None,
    paths: tuple[str, ...],
    as_json: bool,
) -> None:
    """Render a template from a model JSON file (no LLM).

    This is the fast, deterministic path: load pre-built model data,
    render it through the Jinja template, and validate the artifact
    before anything is written to disk. The round-trip check runs too,
    because this command holds the validated model.
    """
    payload: dict[str, Any] = {
        "template": template_name,
        "artifact": None,
        "output_path": output_file,
        "written": False,
        "errors": [],
        "warnings": [],
    }

    def fail(code: int, messages: list[str], prose: list[str]) -> NoReturn:
        payload["errors"].extend(messages)
        _finish(payload, as_json, prose, code)

    template = _get_template_or_exit(template_name, paths, as_json)
    input_data = _load_input_json(
        input_file,
        lambda message: fail(EXIT_USAGE, [message], [f"Error: {message}"]),
    )

    # Validate against the template's Pydantic schema
    schema_class = template.get_schema_class()
    try:
        model = schema_class.model_validate(input_data)
    except Exception as e:
        fail(
            EXIT_CODES[FailureReason.MODEL_VALIDATION_FAILED],
            [f"model validation failed: {e}"],
            [f"Validation error: {e}"],
        )

    # Render the template
    try:
        rendered = template.render(model)
    except Exception as e:
        fail(
            EXIT_CODES[FailureReason.RENDER_FAILED],
            [f"render failed: {e}"],
            [f"Render error: {e}"],
        )

    payload["artifact"] = rendered

    # Validate the rendered artifact before it can reach disk
    errors, warnings = validate_output(
        rendered,
        template.output_language,
        effective_validators(template.metadata.output, template.metadata.validators),
    )
    # The model is known here, so the round trip is checkable: a field the
    # schema declares ``str`` must not reach the artifact as another type.
    errors = list(errors) + check_round_trip(
        rendered, template.output_language, model.model_dump(mode="json")
    )
    payload["warnings"] = list(warnings)

    if not as_json:
        for warning in warnings:
            click.echo(f"Warning: {warning}", err=True)
    if errors:
        fail(
            EXIT_CODES[FailureReason.OUTPUT_VALIDATION_FAILED],
            errors,
            ["✗ Output validation failed:", *(f"  - {err}" for err in errors)],
        )

    if output_file:
        try:
            Path(output_file).write_text(rendered, encoding="utf-8")
        except OSError as e:
            fail(EXIT_CONFIG, [f"error writing output: {e}"], [f"Error: {e}"])
        payload["written"] = True

    if as_json:
        _emit_json(payload)
    elif output_file:
        click.echo(f"Written to {output_file}")
    else:
        click.echo(rendered)


@main.command("generate")
@click.argument("template_name")
@click.option(
    "--context",
    "-c",
    "context_file",
    default=None,
    help="JSON file with project facts context.",
)
@click.option(
    "--request",
    "-r",
    "user_request",
    default=None,
    help="User request description.",
)
@click.option(
    "--output",
    "-o",
    "output_file",
    default=None,
    help="Output file for the generated artifact.",
)
@click.option(
    "--model",
    "-m",
    "model_name",
    default=DEFAULT_MODEL,
    help="LLM model to use.",
)
@click.option(
    "--max-attempts",
    "max_attempts",
    default=3,
    help="Whole-pipeline attempts.",
)
@paths_option
@json_option
def generate_artifact(
    template_name: str,
    context_file: str | None,
    user_request: str | None,
    output_file: str | None,
    model_name: str,
    max_attempts: int,
    paths: tuple[str, ...],
    as_json: bool,
) -> None:
    """Generate an artifact using a template (full pipeline with LLM).

    The LLM receives the user request, project facts, and template
    prompt, then produces a validated Pydantic model. That model is
    rendered deterministically through the Jinja template.

    Under ``--json`` the command emits ``GenerationResult.model_dump()``
    verbatim, so the caller reads the structured failure instead of prose.
    """
    catalog = _load_catalog(paths)

    def die_usage(message: str) -> NoReturn:
        _finish(
            {"template": template_name, "failure_reason": None,
             "error_detail": message},
            as_json,
            [f"Error: {message}"],
            EXIT_USAGE,
        )

    # Build context from file and/or explicit request
    context: dict[str, Any] = {}
    if context_file:
        ctx_path = Path(context_file)
        if not ctx_path.exists():
            die_usage(f"context file not found: {context_file}")
        try:
            request_from_file, facts = _load_context_file(ctx_path)
        except ValueError as e:
            die_usage(str(e))
        context = facts
        if request_from_file and not user_request:
            user_request = request_from_file

    if not user_request:
        user_request = f"Generate {template_name} artifact"

    result = generate(catalog, GenerationRequest(
        template_name=template_name,
        user_request=user_request,
        context=context,
        model_name=model_name,
        max_attempts=max_attempts,
    ))

    if not result.succeeded:
        reason = result.failure_reason
        assert reason is not None  # a failed result always carries a reason
        _finish(
            result.model_dump(),
            as_json,
            [
                f"Generation failed: {reason.value}",
                *([result.error_detail] if result.error_detail else []),
            ],
            EXIT_CODES[reason],
        )

    artifact = result.artifact or ""
    if not as_json:
        for warning in result.warnings:
            click.echo(f"Warning: {warning}", err=True)

    if output_file:
        try:
            Path(output_file).write_text(artifact, encoding="utf-8")
        except OSError as e:
            _finish(
                {"template": template_name, "failure_reason": None,
                 "error_detail": f"error writing output: {e}"},
                as_json,
                [f"Error writing output: {e}"],
                EXIT_CONFIG,
            )

    if as_json:
        _emit_json(result.model_dump())
    elif output_file:
        click.echo(f"Generated {output_file}")
    else:
        click.echo(artifact)


@main.command("validate")
@click.argument("template_name")
@click.option(
    "--input",
    "-i",
    "input_file",
    required=True,
    help="JSON file with model data to validate and render.",
)
@paths_option
@json_option
def validate_output_command(
    template_name: str,
    input_file: str,
    paths: tuple[str, ...],
    as_json: bool,
) -> None:
    """Validate that a model file would produce valid output.

    Performs four checks:
      1. Model validation against the template's Pydantic schema
      2. Template rendering from the validated model
      3. Output validation (parse check of the rendered artifact,
         including the template author's declared validators)
      4. The round trip: every model string reaches the artifact as a string
    """
    payload: dict[str, Any] = {
        "template": template_name,
        "model_valid": False,
        "rendered": False,
        "errors": [],
        "warnings": [],
    }

    def fail(code: int, messages: list[str], prose: list[str]) -> NoReturn:
        payload["errors"].extend(messages)
        _finish(payload, as_json, prose, code, err=False)

    template = _get_template_or_exit(template_name, paths, as_json)
    input_data = _load_input_json(
        input_file,
        lambda message: fail(EXIT_USAGE, [message], [f"Error: {message}"]),
    )

    schema_class = template.get_schema_class()
    try:
        model = schema_class.model_validate(input_data)
    except Exception as e:
        fail(
            EXIT_CODES[FailureReason.MODEL_VALIDATION_FAILED],
            [f"model validation failed: {e}"],
            [f"Model validation failed: {e}"],
        )

    payload["model_valid"] = True
    if not as_json:
        click.echo("✓ Model validated against schema")

    # Render the template
    try:
        rendered = template.render(model)
    except Exception as e:
        fail(
            EXIT_CODES[FailureReason.RENDER_FAILED],
            [f"render failed: {e}"],
            [f"Render failed: {e}"],
        )

    payload["rendered"] = True
    if not as_json:
        click.echo("✓ Template rendered successfully")

    # Run output validators, including custom ones from metadata
    errors, warnings = validate_output(
        rendered,
        template.output_language,
        effective_validators(template.metadata.output, template.metadata.validators),
    )
    # The model is known here, so the round trip is checkable.
    errors = list(errors) + check_round_trip(
        rendered, template.output_language, model.model_dump(mode="json")
    )
    payload["warnings"] = list(warnings)

    if not as_json:
        for warning in warnings:
            click.echo(f"Warning: {warning}", err=True)
    if errors:
        fail(
            EXIT_CODES[FailureReason.OUTPUT_VALIDATION_FAILED],
            errors,
            ["✗ Output validation failed:", *(f"  - {err}" for err in errors)],
        )

    if as_json:
        _emit_json(payload)
    else:
        click.echo("✓ Output validation passed")


@main.command("check")
@click.argument("template_name")
@paths_option
@json_option
def check_template(template_name: str, paths: tuple[str, ...], as_json: bool) -> None:
    """Audit a template: fixtures render, parse, and resist injection.

    The report says how much work the audit did. A green tick that cannot
    separate "clean" from "audited nothing" is a false proof, so an audit that
    ran nothing exits 2.
    """
    template = _get_template_or_exit(template_name, paths, as_json)
    report = audit_template(template)
    payload = report.model_dump()

    prose: list[str] = []
    if report.findings:
        prose.append(f"✗ {len(report.findings)} finding(s):")
        prose.extend(f"  - {finding}" for finding in report.findings)
    if not report.audited:
        prose.append(f"⚠ nothing audited: {report.skipped_reason}")

    if report.findings:
        _finish(payload, as_json, prose, EXIT_FINDING)
    if not report.audited:
        _finish(payload, as_json, prose, EXIT_CONFIG)

    if as_json:
        _emit_json(payload)
    else:
        click.echo(
            f"✓ {report.fields_probed} probes across "
            f"{report.fixtures_seen} fixture(s), "
            f"{report.sites_linted} site(s) linted, 0 findings"
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def entrypoint() -> None:
    """Entry point for the Templateer CLI (console_scripts)."""
    main()


if __name__ == "__main__":
    entrypoint()
