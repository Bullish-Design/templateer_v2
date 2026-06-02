"""Python API for Templateer — programmatic artifact generation.

Provides a clean, typed Python interface for discovering and using
Templateer templates, suitable for embedding in other Python programs,
scripts, and agent frameworks.

Allium spec alignment:
  surface PythonAPI {
      provides:
          StartGeneration(...)
          ListAllTemplates
          GenerateFromTemplate(...)
          RenderFromModel(...)
          ValidateArtifact(...)
  }

Usage:
    registry = TemplateRegistry.from_paths(["./templates"])

    # List available templates
    for t in registry.list_templates():
        print(t.name, t.description)

    # Generate an artifact (requires LLM)
    result = registry.generate(
        template_name="pyproject-uv",
        user_request="Create a pyproject.toml for a FastAPI app using uv.",
        context={"uses_fastapi": True, "uses_pytest": True},
    )
    print(result.rendered)

    # Render from an existing model dict (LLM-free)
    rendered = registry.render_from_model(
        template_name="pyproject-uv",
        model_data={"project_name": "my-app", "python_version": "3.12"},
    )

    # Validate an artifact
    errors = registry.validate_artifact("pyproject-uv", rendered)
"""

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from templateer.catalog import TemplateCatalog
from templateer.generator import ModelGenerationError, generate_model
from templateer.models import TemplateGenerationResult
from templateer.renderer import RenderError
from templateer.template import Template
from templateer.validators import validate_output


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

    def generate(
        self,
        template_name: str,
        user_request: str,
        context: dict[str, Any] | None = None,
        model_name: str = "openai:gpt-4.1-mini",
    ) -> TemplateGenerationResult:
        """Generate an artifact using a template (full pipeline with LLM).

        This method:
          1. Resolves the named template from the catalog.
          2. Asks the LLM to fill the template's Pydantic schema.
          3. Renders the template with the validated model.
          4. Validates the rendered output.

        Args:
            template_name: Exact template directory name.
            user_request: What the user/agent wants to generate.
            context: Optional project facts (dict of key-value pairs)
                     that help the LLM make better choices.
            model_name: The LLM model identifier.

        Returns:
            A :class:`TemplateGenerationResult` containing the validated
            model, the rendered artifact, and any validation messages.

        Raises:
            TemplateNotFoundError: If no template matches *template_name*.
            ModelGenerationError: If the LLM fails to produce a valid model.
            RenderError: If the template rendering step fails.
            RuntimeError: If output validation fails.
        """
        validation_messages: list[str] = []

        # ── 1. Resolve template ──────────────────────────────────────
        template = self._catalog.get(template_name)

        # ── 2. Generate model via LLM ────────────────────────────────
        try:
            model, msgs = generate_model(
                template=template,
                user_request=user_request,
                context=context,
                model_name=model_name,
            )
            validation_messages.extend(msgs)
        except ModelGenerationError:
            raise
        except Exception as e:
            raise ModelGenerationError(f"Unexpected error during model generation: {e}") from e

        # ── 3. Render artifact ───────────────────────────────────────
        try:
            rendered = template.render(model)
        except RenderError:
            raise
        except Exception as e:
            raise RenderError(f"Unexpected error during rendering: {e}") from e

        # ── 4. Validate output ───────────────────────────────────────
        output_language = template.metadata.outputs[0].language
        output_validators = [v.model_dump() for v in template.metadata.validators]

        errors = validate_output(rendered, output_language, output_validators)
        if errors:
            raise RuntimeError(
                f"Output validation failed for '{template_name}': " + "; ".join(errors)
            )

        return TemplateGenerationResult(
            template_name=template_name,
            model=model.model_dump(mode="json"),
            rendered=rendered,
            validation_messages=validation_messages,
        )

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
            RenderError: If the template rendering step fails.
        """
        template = self._catalog.get(template_name)
        schema_class = template.get_schema_class()
        model = schema_class(**model_data)
        return template.render(model)

    # ------------------------------------------------------------------
    # Output validation
    # ------------------------------------------------------------------

    def validate_artifact(
        self,
        template_name: str,
        artifact: str,
    ) -> list[str]:
        """Validate a rendered artifact against the template's output validators.

        Runs the built-in parser validator for the template's target
        language plus any custom validators declared in the template's
        ``metadata.yml``.

        Args:
            template_name: Exact template directory name.
            artifact: The artifact text to validate.

        Returns:
            A list of error messages.  An empty list means the artifact
            passed all validators.

        Raises:
            TemplateNotFoundError: If the named template is not found.
        """
        template = self._catalog.get(template_name)
        output_language = template.metadata.outputs[0].language
        validators = [v.model_dump() for v in template.metadata.validators]
        return validate_output(artifact, output_language, validators)

    # ------------------------------------------------------------------
    # Model-only generation
    # ------------------------------------------------------------------

    def generate_model(
        self,
        template_name: str,
        user_request: str,
        context: dict[str, Any] | None = None,
    ) -> BaseModel:
        """Generate just the Pydantic model (no rendering).

        This is useful when the caller wants to inspect, modify, or
        persist the model before rendering.

        Args:
            template_name: Exact template directory name.
            user_request: What to generate.
            context: Optional project facts.

        Returns:
            A validated Pydantic model instance.

        Raises:
            TemplateNotFoundError: If the named template is not found.
            ModelGenerationError: If the LLM fails to produce a valid model.
        """
        template = self._catalog.get(template_name)
        model, _ = generate_model(
            template=template,
            user_request=user_request,
            context=context,
        )
        return model

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
