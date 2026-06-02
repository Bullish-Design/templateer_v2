"""Templateer CLI — typed, constrained artifact generation for AI agents.

Expose Templateer via a CLI suitable for both human and agent use.
Allium spec alignment: CLI surface from generation.allium.

Usage:
    templateer list
    templateer describe <name>
    templateer schema <name>
    templateer render <name> --input model.json
    templateer generate <name> --request "..."
    templateer validate <name> --input model.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from templateer.catalog import TemplateCatalog
from templateer.generation import GenerationStatus
from templateer.pipeline import run_pipeline
from templateer.template import TemplateLoadError, TemplateNotFoundError
from templateer.validators import validate_output as validate_artifact

# ---------------------------------------------------------------------------
# Path resolution helpers
# ---------------------------------------------------------------------------


def _get_default_paths() -> list[Path]:
    """Get default template search paths.

    Returns:
        List of Paths: bundled templates (src/templateer/templates)
        and project-local templates (cwd/templates) if they exist.
    """
    paths: list[Path] = []

    # Bundled templates shipped with the package
    bundled = Path(__file__).parent / "templates"
    if bundled.exists() and bundled.is_dir():
        paths.append(bundled)

    # Project-local templates in the current working directory
    cwd_templates = Path.cwd() / "templates"
    if cwd_templates.exists() and cwd_templates.is_dir():
        paths.append(cwd_templates)

    return paths


def _resolve_paths(
    extra_paths: tuple[str, ...] | None,
) -> list[Path]:
    """Resolve template search paths from CLI options and defaults.

    If explicit paths are given via ``--paths``, use only those.
    Otherwise fall back to the default search paths.

    Args:
        extra_paths: Tuple of path strings from the ``--paths`` option.

    Returns:
        Resolved Path objects for template discovery.
    """
    if extra_paths:
        return [Path(p) for p in extra_paths]
    return _get_default_paths()


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
    "--paths", "-p",
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
        click.echo(f"    Output: {template.output_kind}")
        if template.trigger_paths:
            click.echo(
                f"    Generates: {', '.join(sorted(template.trigger_paths))}"
            )
        click.echo()


@main.command("describe")
@click.argument("template_name")
@click.option(
    "--paths", "-p",
    multiple=True,
    help="Additional template search paths (replaces defaults).",
)
def describe_template(template_name: str, paths: tuple[str, ...]) -> None:
    """Describe a template's metadata."""
    catalog = _load_catalog(paths)

    try:
        template = catalog.get(template_name)
    except (TemplateNotFoundError, TemplateLoadError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    click.echo(f"Name: {template.name}")
    click.echo(f"Description: {template.description}")
    click.echo(f"Output kind: {template.output_kind}")
    click.echo(f"Strict context: {template.metadata.strict_context}")
    click.echo(f"Trigger paths: {template.trigger_paths}")

    for output in template.metadata.outputs:
        click.echo(f"  Generates: {output.path} ({output.kind}, {output.language})")


@main.command("schema")
@click.argument("template_name")
@click.option(
    "--paths", "-p",
    multiple=True,
    help="Additional template search paths (replaces defaults).",
)
def show_schema(template_name: str, paths: tuple[str, ...]) -> None:
    """Show the JSON schema for a template."""
    catalog = _load_catalog(paths)

    try:
        template = catalog.get(template_name)
    except (TemplateNotFoundError, TemplateLoadError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    schema = template.get_schema_json()
    click.echo(json.dumps(schema, indent=2))


@main.command("render")
@click.argument("template_name")
@click.option(
    "--input", "-i",
    "input_file",
    required=True,
    help="JSON file with validated Pydantic model data.",
)
@click.option(
    "--output", "-o",
    "output_file",
    default=None,
    help="Output file (stdout if not given).",
)
@click.option(
    "--paths", "-p",
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

    This is the fast, deterministic path: load pre-built model data
    and render it through the Jinja template.
    """
    catalog = _load_catalog(paths)

    try:
        template = catalog.get(template_name)
    except (TemplateNotFoundError, TemplateLoadError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    # Load model data from the JSON input file
    input_path = Path(input_file)
    if not input_path.exists():
        click.echo(f"Error: input file not found: {input_file}", err=True)
        sys.exit(1)

    try:
        input_data = json.loads(input_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        click.echo(f"Error reading input file: {e}", err=True)
        sys.exit(1)

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

    if output_file:
        try:
            Path(output_file).write_text(rendered)
        except OSError as e:
            click.echo(f"Error writing output: {e}", err=True)
            sys.exit(1)
        click.echo(f"Written to {output_file}")
    else:
        click.echo(rendered)


@main.command("generate")
@click.argument("template_name")
@click.option(
    "--context", "-c",
    "context_file",
    default=None,
    help="JSON file with project facts context.",
)
@click.option(
    "--request", "-r",
    "user_request",
    default=None,
    help="User request description.",
)
@click.option(
    "--output", "-o",
    "output_file",
    default=None,
    help="Output file for the generated artifact.",
)
@click.option(
    "--model", "-m",
    "model_name",
    default="openai:gpt-4.1-mini",
    help="LLM model to use.",
)
@click.option(
    "--paths", "-p",
    multiple=True,
    help="Additional template search paths (replaces defaults).",
)
def generate_artifact(
    template_name: str,
    context_file: str | None,
    user_request: str | None,
    output_file: str | None,
    model_name: str,
    paths: tuple[str, ...],
) -> None:
    """Generate an artifact using a template (full pipeline with LLM).

    The LLM receives the user request, project facts, and template
    prompt, then produces a validated Pydantic model. That model is
    rendered deterministically through the Jinja template.
    """
    catalog = _load_catalog(paths)

    # Build context from file and/or explicit request
    context: dict[str, object] = {}
    if context_file:
        ctx_path = Path(context_file)
        if not ctx_path.exists():
            click.echo(f"Error: context file not found: {context_file}", err=True)
            sys.exit(1)
        try:
            context_data = json.loads(ctx_path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            click.echo(f"Error reading context file: {e}", err=True)
            sys.exit(1)
        # Support both flat dict and nested {"user_request": ..., "facts": ...}
        if isinstance(context_data, dict):
            if "facts" in context_data:
                facts = context_data["facts"]
                if isinstance(facts, dict):
                    context = dict(facts)  # type: ignore[arg-type]
                if "user_request" in context_data and not user_request:
                    raw = context_data["user_request"]
                    if isinstance(raw, str):
                        user_request = raw
            else:
                context = dict(context_data)  # type: ignore[arg-type]

    if not user_request:
        user_request = f"Generate {template_name} artifact"

    # Run the full generation pipeline
    gen = run_pipeline(
        catalog=catalog,
        template_name=template_name,
        user_request=user_request,
        context=context,  # type: ignore[arg-type]
        model_name=model_name,
    )

    if gen.status == GenerationStatus.FAILED:
        click.echo(f"Generation failed: {gen.failure_reason}", err=True)
        if gen.artifact:
            click.echo(gen.artifact, err=True)
        sys.exit(1)

    if gen.status == GenerationStatus.READY:
        artifact = gen.artifact
        if output_file and artifact:
            try:
                Path(output_file).write_text(artifact)
            except OSError as e:
                click.echo(f"Error writing output: {e}", err=True)
                sys.exit(1)
            click.echo(f"Generated {output_file}")
        elif artifact:
            click.echo(artifact)
    else:
        click.echo(f"Unexpected status: {gen.status}", err=True)
        sys.exit(1)


@main.command("validate")
@click.argument("template_name")
@click.option(
    "--input", "-i",
    "input_file",
    required=True,
    help="JSON file with model data to validate and render.",
)
@click.option(
    "--paths", "-p",
    multiple=True,
    help="Additional template search paths (replaces defaults).",
)
def validate_output(
    template_name: str,
    input_file: str,
    paths: tuple[str, ...],
) -> None:
    """Validate that a model file would produce valid output.

    Performs three checks:
      1. Model validation against the template's Pydantic schema
      2. Template rendering from the validated model
      3. Output validation (parse check of the rendered artifact)
    """
    catalog = _load_catalog(paths)

    try:
        template = catalog.get(template_name)
    except (TemplateNotFoundError, TemplateLoadError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    # Load and validate the model
    input_path = Path(input_file)
    if not input_path.exists():
        click.echo(f"Error: input file not found: {input_file}", err=True)
        sys.exit(1)

    try:
        input_data = json.loads(input_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        click.echo(f"Error reading input: {e}", err=True)
        sys.exit(1)

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

    # Run output validators
    output_language = template.metadata.outputs[0].language
    errors = validate_artifact(rendered, output_language)

    if errors:
        click.echo("✗ Output validation failed:")
        for err in errors:
            click.echo(f"  - {err}")
        sys.exit(1)

    click.echo("✓ Output validation passed")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def entrypoint() -> None:
    """Entry point for the Templateer CLI (console_scripts)."""
    main()


if __name__ == "__main__":
    entrypoint()
