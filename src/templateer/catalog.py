"""Template catalog for discovery and lookup."""

import logging
from pathlib import Path

from templateer.template import Template, TemplateLoadError, TemplateNotFoundError

logger = logging.getLogger(__name__)


class TemplateCatalog:
    """A collection of available templates with exact-name lookup."""

    def __init__(self) -> None:
        self._templates: dict[str, Template] = {}

    @property
    def templates(self) -> list[Template]:
        """All loaded templates."""
        return list(self._templates.values())

    def load_from_paths(self, paths: list[Path]) -> None:
        """
        Load templates from one or more directories.

        Each directory is scanned for subdirectories containing metadata.yml.
        Templates are indexed by name (directory name). If a template with
        the same name appears in multiple paths, the first one wins.

        Args:
            paths: List of directories to scan for templates.

        Raises:
            TemplateLoadError: If a template directory has invalid structure.
        """
        for path in paths:
            if not path.exists():
                continue

            if not path.is_dir():
                continue

            for entry in sorted(path.iterdir()):
                if entry.is_dir() and (entry / "metadata.yml").exists():
                    if entry.name not in self._templates:
                        try:
                            template = Template(entry)
                            self._templates[template.name] = template
                        except TemplateLoadError as e:
                            logger.warning("Skipping template %s: %s", entry.name, e)

    def has_template(self, name: str) -> bool:
        """Check if a template with the exact name exists."""
        return name in self._templates

    def get(self, name: str) -> Template:
        """
        Get a template by exact name.

        Args:
            name: The template directory name (e.g., 'pyproject-uv').

        Returns:
            The Template instance.

        Raises:
            TemplateNotFoundError: If no template with that name exists.
        """
        if name not in self._templates:
            raise TemplateNotFoundError(f"No template found with name: {name}")
        return self._templates[name]

    def templates_by_language(self, language: str) -> list[Template]:
        """Find templates that produce a given output language."""
        return [t for t in self._templates.values() if t.output_language == language]

    def __len__(self) -> int:
        return len(self._templates)

    def __contains__(self, name: str) -> bool:
        return name in self._templates
