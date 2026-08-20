"""Python API for Templateer — programmatic artifact generation.

Provides a clean, typed Python interface for discovering and using
Templateer templates, suitable for embedding in other Python programs,
scripts, and agent frameworks.

Agent frameworks run an event loop.  ``generate_async`` is therefore the
primary entry point, and ``generate`` is a thin wrapper for callers that own
the thread.  Inside a running loop, call ``generate_async``.

Usage:
    registry = TemplateRegistry.from_paths(["./templates"])

    # List available templates
    for t in registry.list_templates():
        print(t.name, t.description)

    # Generate an artifact (requires LLM).  From async code, await
    # registry.generate_async(...) with the same arguments.
    result = registry.generate(
        template_name="pyproject-uv",
        user_request="Create a pyproject.toml for a FastAPI app using uv.",
        context={"uses_fastapi": True, "uses_pytest": True},
    )
    if not result.succeeded:
        print(result.error_detail)
    else:
        print(result.artifact)

    # Render from an existing model dict (LLM-free)
    model_data = {"project_name": "my-app", "python_version": "3.12"}
    rendered = registry.render_from_model(
        template_name="pyproject-uv",
        model_data=model_data,
    )

    # Validate an artifact.  Pass the model data to also check that the
    # artifact carries every field with its declared type.
    errors, warnings = registry.validate_artifact(
        "pyproject-uv", rendered, model_data=model_data
    )

    # Audit a template for injection and unquoted-site holes
    report = registry.audit("pyproject-uv")
    print(report.ok, report.findings)
"""

import asyncio
from pathlib import Path
from typing import Any

from templateer.audit import AuditReport, audit_template
from templateer.catalog import TemplateCatalog
from templateer.generator import DEFAULT_MODEL
from templateer.pipeline import generate_async as pipeline_generate_async
from templateer.result import GenerationRequest, GenerationResult
from templateer.template import Template
from templateer.validators import (
    check_round_trip,
    effective_validators,
    validate_output,
)


class TemplateRegistry:
    """Python API for discovering and using Templateer templates.

    This is the primary programmatic interface to Templateer.  It
    wraps a :class:`TemplateCatalog` and provides high-level methods
    for listing templates, generating artifacts, rendering from
    pre-existing models, and validating output.

    Create a registry from one or more template directories::

        registry = TemplateRegistry.from_paths([
            "/path/to/bundled/templates",
            "./my-project/templates",
        ])

    All methods that need a template name expect the **exact directory
    name** (e.g. ``"pyproject-uv"``).  If no template matches,
    :class:`TemplateNotFoundError` is raised.
    """

    def __init__(self, catalog: TemplateCatalog) -> None:
        """Wrap an existing catalog.

        Prefer the :meth:`from_paths` constructor for normal use.

        Args:
            catalog: A pre-built :class:`TemplateCatalog`.
        """
        self._catalog = catalog

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_paths(cls, paths: list[Path | str]) -> "TemplateRegistry":
        """Create a registry from one or more template directory paths.

        Each path is scanned for immediate subdirectories containing
        a ``metadata.yml`` file.  If the same template name appears in
        multiple paths the first one wins.

        Args:
            paths: List of directories containing template folders.

        Returns:
            A :class:`TemplateRegistry` with all templates loaded.

        Example:
            >>> registry = TemplateRegistry.from_paths(["./templates"])
        """
        catalog = TemplateCatalog()
        catalog.load_from_paths([Path(p) for p in paths])
        return cls(catalog)

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def list_templates(self) -> list[Template]:
        """Return all available templates.

        Returns:
            A list of :class:`Template` instances, one per loaded template.
        """
        return self._catalog.templates

    def get_template(self, name: str) -> Template:
        """Get a single template by exact directory name.

        Args:
            name: Template directory name (e.g. ``"pyproject-uv"``).

        Returns:
            The :class:`Template` instance.

        Raises:
            TemplateNotFoundError: If no template with that name exists.
        """
        return self._catalog.get(name)

    def has_template(self, name: str) -> bool:
        """Check whether a template with the given name exists.

        Args:
            name: Template directory name.

        Returns:
            ``True`` if the template is available.
        """
        return self._catalog.has_template(name)

    # ------------------------------------------------------------------
    # Generation (LLM required)
    # ------------------------------------------------------------------

    async def generate_async(
        self,
        template_name: str,
        user_request: str,
        context: dict[str, Any] | None = None,
        model_name: str = DEFAULT_MODEL,
        max_attempts: int = 3,
    ) -> GenerationResult:
        """Generate an artifact (full pipeline with LLM).

        This is the primary entry point.  Agent frameworks — pydantic-ai,
        LangGraph, the Claude Agent SDK — run an event loop, and the
        synchronous wrapper cannot run inside one.

        Returns a GenerationResult rather than raising: LLM failure is an
        expected outcome, not an exceptional one.  Check ``result.succeeded``.
        """
        return await pipeline_generate_async(self._catalog, GenerationRequest(
            template_name=template_name, user_request=user_request,
            context=context or {}, model_name=model_name, max_attempts=max_attempts,
        ))

    def generate(
        self,
        template_name: str,
        user_request: str,
        context: dict[str, Any] | None = None,
        model_name: str = DEFAULT_MODEL,
        max_attempts: int = 3,
    ) -> GenerationResult:
        """Run :meth:`generate_async` on a new event loop.

        Use this from synchronous code that owns the thread.  Inside a
        running event loop ``asyncio.run`` raises ``RuntimeError``; await
        :meth:`generate_async` there instead.

        The arguments and the return value match :meth:`generate_async`.
        """
        return asyncio.run(self.generate_async(
            template_name=template_name, user_request=user_request,
            context=context, model_name=model_name, max_attempts=max_attempts,
        ))

    # ------------------------------------------------------------------
    # LLM-free rendering
    # ------------------------------------------------------------------

    def render_from_model(
        self,
        template_name: str,
        model_data: dict[str, Any],
    ) -> str:
        """Render a template from a model dict (LLM-free path).

        This is the deterministic, synchronous path.  No LLM is called.
        The provided *model_data* dict is validated against the template's
        Pydantic schema and then passed to the Jinja renderer.

        Args:
            template_name: Exact template directory name.
            model_data: A dict matching the template's Pydantic schema.
                Required fields must be present; optional fields may
                be omitted.

        Returns:
            The rendered artifact text.

        Raises:
            TemplateNotFoundError: If the named template is not found.
            pydantic.ValidationError: If *model_data* fails schema validation.
                A non-mapping argument fails the same way, because the CLI
                and this method both use ``model_validate`` (§B8).
            RenderError: If the template rendering step fails.
        """
        template = self._catalog.get(template_name)
        schema_class = template.get_schema_class()
        model = schema_class.model_validate(model_data)
        return template.render(model)

    # ------------------------------------------------------------------
    # Output validation
    # ------------------------------------------------------------------

    def validate_artifact(
        self,
        template_name: str,
        artifact: str,
        model_data: dict[str, Any] | None = None,
    ) -> tuple[list[str], list[str]]:
        """Validate a rendered artifact against the template's output validators.

        Runs the built-in parser validator for the template's target
        language plus any custom validators declared in the template's
        ``metadata.yml``.

        Warnings reach the caller.  This method used to drop them, which made
        a failing ``optional: true`` validator invisible through the Python
        API (§B8).

        Give *model_data* to also run the round-trip check.  A parser only
        proves that the artifact is well formed.  The round-trip check proves
        that the artifact carries each field with the type the schema
        declares — it catches a ``str`` field that reaches the artifact as a
        bool, which every other layer reports as success (§A1).

        Args:
            template_name: Exact template directory name.
            artifact: The artifact text to validate.
            model_data: Optional dict matching the template's Pydantic
                schema.  It is validated against the schema first, so the
                round-trip check reads the model's values, not raw input.

        Returns:
            ``(errors, warnings)``.  Two empty lists mean the artifact passed
            every check.  Errors are fatal; warnings come from validators
            declared ``optional: true``.

        Raises:
            TemplateNotFoundError: If the named template is not found.
            pydantic.ValidationError: If *model_data* fails schema validation.
        """
        template = self._catalog.get(template_name)
        errors, warnings = validate_output(
            artifact,
            template.output_language,
            effective_validators(template.metadata.output, template.metadata.validators),
        )
        if model_data is not None:
            model = template.get_schema_class().model_validate(model_data)
            errors = [
                *errors,
                *check_round_trip(
                    artifact, template.output_language, model.model_dump(mode="json")
                ),
            ]
        return errors, warnings

    # ------------------------------------------------------------------
    # Template audit
    # ------------------------------------------------------------------

    def audit(self, template_name: str) -> AuditReport:
        """Audit a template for injection and unquoted-site holes.

        This is the Python equivalent of ``templateer check``.  The report
        says what the audit did, not only what it found: read
        ``report.audited`` before you read ``report.findings``.

        Args:
            template_name: Exact template directory name.

        Returns:
            An :class:`AuditReport`.

        Raises:
            TemplateNotFoundError: If the named template is not found.
        """
        return audit_template(self._catalog.get(template_name))

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        """Return the number of loaded templates."""
        return len(self._catalog)

    def __contains__(self, name: str) -> bool:
        """Check template existence with ``name in registry``."""
        return name in self._catalog

    def __repr__(self) -> str:
        return f"TemplateRegistry(templates={len(self._catalog)})"
