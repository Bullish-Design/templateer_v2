"""Templateer: typed, constrained artifact generation for AI agents."""

from importlib.metadata import PackageNotFoundError, version

from templateer.api import TemplateRegistry
from templateer.result import FailureReason, GenerationRequest, GenerationResult

try:
    __version__ = version("templateer")
except PackageNotFoundError:  # pragma: no cover - a source tree, not installed
    __version__ = "0.0.0+unknown"

__all__ = [
    "FailureReason",
    "GenerationRequest",
    "GenerationResult",
    "TemplateRegistry",
    "__version__",
]
