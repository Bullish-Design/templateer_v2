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
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import click

from templateer.audit import audit_template
from templateer.catalog import TemplateCatalog
from templateer.generator import DEFAULT_MODEL
from templateer.pipeline import generate
from templateer.result import GenerationRequest
from templateer.template import Template, TemplateLoadError, TemplateNotFoundError
from templateer.validators import effective_validators, validate_output

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
        A populated TemplateCatalog.
    """
    catalog = TemplateCatalog()
    resolved = _resolve_paths(paths)
    if resolved:
        catalog.load_from_paths(resolved)
    return catalog


def _get_template_or_exit(
    template_name: str, paths: tuple[str, ...] | None = None
) -> Template:
    """Load a template from the catalog, exiting with an error if unknown."""
    catalog = _load_catalog(paths)
    try:
        return catalog.get(template_name)
    except (TemplateNotFoundError, TemplateLoadError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


def _load_input_json(input_file: str) -> Any:
    """Load and parse a JSON input file, exiting with an error if unreadable."""
    input_path = Path(input_file)
    if not input_path.exists():
        click.echo(f"Error: input file not found: {input_file}", err=True)
        sys.exit(1)
    try:
        return json.loads(input_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        click.echo(f"Error reading input file: {e}", err=True)
        sys.exit(1)


def _load_context_file(path: Path) -> tuple[str | None, dict[str, Any]]:
    """Parse a context file into ``(user_request, facts)``.

    Accepts either shape, and errors on anything else rather than
    silently producing an empty context:
        {"user_request": "...", "facts": {...}}
        {"any": "flat", "project": "facts"}
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise click.ClickException(f"Error reading context file {path}: {e}")

    if not isinstance(data, dict):
        raise click.ClickException(
            f"{path}: expected a JSON object, got {type(data).__name__}"
        )
    if "facts" in data or "user_request" in data:
        facts = data.get("facts", {})
        if not isinstance(facts, dict):
            raise click.ClickException(f"{path}: 'facts' must be an object")
        request = data.get("user_request")
        if request is not None and not isinstance(request, str):
            raise click.ClickException(f"{path}: 'user_request' must be a string")
        return request, facts
    return None, data


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
@click.option(
    "--paths",
    "-p",
    multiple=True,
    help="Additional template search paths (replaces defaults).",
)
def list_templates(paths: tuple[str, ...]) -> None:
    """List all available templates."""
    catalog = _load_catalog(paths)

    if len(catalog) == 0:
        click.echo("No templates found.")
        return

    click.echo(f"Found {len(catalog)} template(s):\n")
    for template in catalog.templates:
        click.echo(f"  {template.name}")
        click.echo(f"    {template.description}")
        click.echo(f"    Output: {template.output_language}")
        if template.trigger_paths:
            click.echo(f"    Generates: {', '.join(sorted(template.trigger_paths))}")
        click.echo()


@main.command("describe")
@click.argument("template_name")
@click.option(
    "--paths",
    "-p",
    multiple=True,
    help="Additional template search paths (replaces defaults).",
)
def describe_template(template_name: str, paths: tuple[str, ...]) -> None:
    """Describe a template's metadata."""
    template = _get_template_or_exit(template_name, paths)

    click.echo(f"Name: {template.name}")
    click.echo(f"Description: {template.description}")
    click.echo(f"Output language: {template.output_language}")
    click.echo(f"Trigger paths: {template.trigger_paths}")
    output = template.metadata.output
    # ``region`` exists on RegionOutput only, so bind it before the test.
    region = getattr(output, "region", None)
    target = output.path or (region.page if region is not None else "-")
    click.echo(f"  Generates: {target} ({output.language})")
    if region is not None:
        anchor = region.anchor or "-"
        click.echo(
            f"  Region: page={region.page} "
            f"ref={region.ref} anchor={anchor}"
        )


@main.command("schema")
@click.argument("template_name")
@click.option(
    "--paths",
    "-p",
    multiple=True,
    help="Additional template search paths (replaces defaults).",
)
def show_schema(template_name: str, paths: tuple[str, ...]) -> None:
    """Show the JSON schema for a template."""
    template = _get_template_or_exit(template_name, paths)
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
@click.option(
    "--paths",
    "-p",
    multiple=True,
    help="Additional template search paths (replaces defaults).",
)
def render_from_model(
    template_name: str,
    input_file: str,
    output_file: str | None,
    paths: tuple[str, ...],
) -> None:
    """Render a template from a model JSON file (no LLM).

    This is the fast, deterministic path: load pre-built model data,
    render it through the Jinja template, and validate the artifact
    before anything is written to disk.
    """
    template = _get_template_or_exit(template_name, paths)
    input_data = _load_input_json(input_file)

    # Validate against the template's Pydantic schema
    schema_class = template.get_schema_class()
    try:
        model = schema_class.model_validate(input_data)
    except Exception as e:
        click.echo(f"Validation error: {e}", err=True)
        sys.exit(1)

    # Render the template
    try:
        rendered = template.render(model)
    except Exception as e:
        click.echo(f"Render error: {e}", err=True)
        sys.exit(1)

    # Validate the rendered artifact before it can reach disk
    errors, warnings = validate_output(
        rendered,
        template.output_language,
        effective_validators(template.metadata.output, template.metadata.validators),
    )
    for warning in warnings:
        click.echo(f"Warning: {warning}", err=True)
    if errors:
        click.echo("✗ Output validation failed:", err=True)
        for err in errors:
            click.echo(f"  - {err}", err=True)
        sys.exit(1)

    if output_file:
        try:
            Path(output_file).write_text(rendered, encoding="utf-8")
        except OSError as e:
            click.echo(f"Error writing output: {e}", err=True)
            sys.exit(1)
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
@click.option(
    "--paths",
    "-p",
    multiple=True,
    help="Additional template search paths (replaces defaults).",
)
def generate_artifact(
    template_name: str,
    context_file: str | None,
    user_request: str | None,
    output_file: str | None,
    model_name: str,
    max_attempts: int,
    paths: tuple[str, ...],
) -> None:
    """Generate an artifact using a template (full pipeline with LLM).

    The LLM receives the user request, project facts, and template
    prompt, then produces a validated Pydantic model. That model is
    rendered deterministically through the Jinja template.
    """
    catalog = _load_catalog(paths)

    # Build context from file and/or explicit request
    context: dict[str, Any] = {}
    if context_file:
        ctx_path = Path(context_file)
        if not ctx_path.exists():
            click.echo(f"Error: context file not found: {context_file}", err=True)
            sys.exit(1)
        request_from_file, facts = _load_context_file(ctx_path)
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
        click.echo(f"Generation failed: {reason.value}", err=True)
        if result.error_detail:
            click.echo(result.error_detail, err=True)
        sys.exit(1)

    for warning in result.warnings:
        click.echo(f"Warning: {warning}", err=True)

    artifact = result.artifact or ""
    if output_file:
        try:
            Path(output_file).write_text(artifact, encoding="utf-8")
        except OSError as e:
            click.echo(f"Error writing output: {e}", err=True)
            sys.exit(1)
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
@click.option(
    "--paths",
    "-p",
    multiple=True,
    help="Additional template search paths (replaces defaults).",
)
def validate_output_command(
    template_name: str,
    input_file: str,
    paths: tuple[str, ...],
) -> None:
    """Validate that a model file would produce valid output.

    Performs three checks:
      1. Model validation against the template's Pydantic schema
      2. Template rendering from the validated model
      3. Output validation (parse check of the rendered artifact,
         including the template author's declared validators)
    """
    template = _get_template_or_exit(template_name, paths)
    input_data = _load_input_json(input_file)

    schema_class = template.get_schema_class()
    try:
        model = schema_class.model_validate(input_data)
    except Exception as e:
        click.echo(f"Model validation failed: {e}", err=True)
        sys.exit(1)

    click.echo("✓ Model validated against schema")

    # Render the template
    try:
        rendered = template.render(model)
    except Exception as e:
        click.echo(f"Render failed: {e}", err=True)
        sys.exit(1)

    click.echo("✓ Template rendered successfully")

    # Run output validators, including custom ones from metadata
    errors, warnings = validate_output(
        rendered,
        template.output_language,
        effective_validators(template.metadata.output, template.metadata.validators),
    )
    for warning in warnings:
        click.echo(f"Warning: {warning}", err=True)
    if errors:
        click.echo("✗ Output validation failed:")
        for err in errors:
            click.echo(f"  - {err}")
        sys.exit(1)

    click.echo("✓ Output validation passed")


@main.command("check")
@click.argument("template_name")
@click.option(
    "--paths",
    "-p",
    multiple=True,
    help="Template search paths.",
)
def check_template(template_name: str, paths: tuple[str, ...]) -> None:
    """Audit a template: fixtures render, parse, and resist injection."""
    template = _get_template_or_exit(template_name, paths)
    findings = audit_template(template)
    if findings:
        click.echo(f"✗ {len(findings)} finding(s):", err=True)
        for finding in findings:
            click.echo(f"  - {finding}", err=True)
        sys.exit(1)
    click.echo("✓ escaping audit passed")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def entrypoint() -> None:
    """Entry point for the Templateer CLI (console_scripts)."""
    main()


if __name__ == "__main__":
    entrypoint()
