"""Template loading and representation."""

import importlib.util
import sys
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from templateer.models import TemplateMetadata


class TemplateLoadError(Exception):
    """Raised when a template cannot be loaded."""


class TemplateNotFoundError(Exception):
    """Raised when a named template is not found in the catalog."""


class Template:
    """Represents a loaded Templateer template."""

    def __init__(self, root: Path) -> None:
        """
        Load a template from a directory.

        Args:
            root: Path to the template directory containing metadata.yml.

        Raises:
            TemplateLoadError: If metadata.yml is missing or invalid.
        """
        self.root = root
        self._metadata_path = root / "metadata.yml"

        if not self._metadata_path.exists():
            raise TemplateLoadError(f"metadata.yml not found in {root}")

        try:
            raw = yaml.safe_load(self._metadata_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            raise TemplateLoadError(f"Invalid YAML in {self._metadata_path}: {e}")

        if not isinstance(raw, dict):
            raise TemplateLoadError(
                f"metadata.yml must contain a mapping, got {type(raw).__name__}"
            )

        try:
            self.metadata = TemplateMetadata.model_validate(raw)
        except Exception as e:
            raise TemplateLoadError(f"Invalid metadata in {self._metadata_path}: {e}")

        # Validate that metadata name matches directory name
        if self.metadata.name != root.name:
            raise TemplateLoadError(
                f"Template name '{self.metadata.name}' does not match directory name '{root.name}'"
            )

        # Cache for lazily-loaded resources
        self._schema_class_cache: type[BaseModel] | None = None

    @property
    def name(self) -> str:
        """The template name (matches directory name)."""
        return self.metadata.name

    @property
    def description(self) -> str:
        """Description of what this template generates."""
        return self.metadata.description

    @property
    def output_language(self) -> str:
        """Target language of the artifact this template generates."""
        return self.metadata.output.language

    @property
    def output_kind(self) -> str:
        """What kind of artifact this template produces: full_file or region."""
        return self.metadata.output.kind

    @property
    def trigger_paths(self) -> set[str]:
        """File paths this template can generate."""
        return set(self.metadata.trigger_filenames)

    def resolve_path(self, relative: str) -> Path:
        """Resolve a path relative to the template root.

        Templates are self-contained: a path escaping the root is a template bug,
        not a supported feature.
        """
        resolved = (self.root / relative).resolve()
        if not resolved.is_relative_to(self.root.resolve()):
            raise TemplateLoadError(
                f"Template '{self.name}': path '{relative}' escapes the template root"
            )
        return resolved

    def load_prompt(self) -> str:
        """Load the prompt file contents."""
        prompt_path = self.resolve_path(self.metadata.prompt.file)
        if not prompt_path.exists():
            raise TemplateLoadError(f"Prompt file not found: {prompt_path}")
        return prompt_path.read_text(encoding="utf-8")

    def load_schema_module(self) -> Any:
        """
        Dynamically load the schema Python module.

        Note: ``sys.modules[spec_name]`` and ``_schema_class_cache`` persist for
        the process lifetime, so editing a ``schema.py`` mid-session serves the
        stale cached class.  Restart to pick up changes.

        Returns:
            The imported Python module object.

        Raises:
            TemplateLoadError: If the schema file cannot be found or loaded.
        """
        module_name = self.metadata.schema_ref.module
        schema_file = self.root / f"{module_name}.py"

        if not schema_file.exists():
            raise TemplateLoadError(f"Schema file not found: {schema_file}")

        # Use a unique name to avoid collisions
        spec_name = f"templateer_template_{self.name}_{module_name}"
        spec = importlib.util.spec_from_file_location(spec_name, schema_file)
        if spec is None or spec.loader is None:
            raise TemplateLoadError(f"Cannot load schema module from {schema_file}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[spec_name] = module
        spec.loader.exec_module(module)
        return module

    def get_schema_class(self) -> type[BaseModel]:
        """
        Load and return the Pydantic model class (cached).

        Returns:
            The Pydantic model class.

        Raises:
            TemplateLoadError: If the class cannot be found or is not a BaseModel.
        """
        if self._schema_class_cache is not None:
            return self._schema_class_cache

        module = self.load_schema_module()
        class_name = self.metadata.schema_ref.class_name

        if not hasattr(module, class_name):
            raise TemplateLoadError(
                f"Class '{class_name}' not found in schema module "
                f"'{self.metadata.schema_ref.module}' for template '{self.name}'"
            )

        cls = getattr(module, class_name)
        if not isinstance(cls, type) or not issubclass(cls, BaseModel):
            raise TemplateLoadError(f"'{class_name}' is not a Pydantic BaseModel subclass")

        self._schema_class_cache = cls
        return cls

    def get_schema_json(self) -> dict[str, Any]:
        """Return the JSON schema for the template's Pydantic model."""
        cls = self.get_schema_class()
        return cls.model_json_schema()

    def render(self, model: BaseModel) -> str:
        """Render this template with a validated model."""
        from templateer.renderer import render_template

        return render_template(
            self.resolve_path(self.metadata.renderer.file),
            model,
            self.metadata.output.language,
        )

    def load_example(self) -> str | None:
        """Return the first example input fixture as JSON, if one exists.

        Used as a few-shot exemplar for the LLM.  The fixture is already
        schema-validated by the template's own tests.
        """
        fixtures = sorted((self.root / "examples").glob("*.input.json"))
        if not fixtures:
            return None
        return fixtures[0].read_text(encoding="utf-8")

    def __repr__(self) -> str:
        return f"Template(name={self.name!r}, root={self.root!r})"
