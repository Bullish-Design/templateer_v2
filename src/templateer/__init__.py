"""Templateer: typed, constrained artifact generation for AI agents."""

from pathlib import Path

__version__ = "0.1.0"

# Default template search paths
DEFAULT_TEMPLATE_PATHS = [
    Path(__file__).parent / "templates",  # bundled templates
    Path.cwd() / "templates",             # project-local templates
]
