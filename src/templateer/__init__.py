"""Templateer: typed, constrained artifact generation for AI agents."""

from templateer.api import TemplateRegistry
from templateer.constants import DEFAULT_MODEL
from templateer.result import FailureReason, GenerationRequest, GenerationResult


def preload() -> None:
    """Import the generation stack now, so the first generation does not."""
    from templateer.generator import preload as _preload

    _preload()


def __getattr__(name: str) -> object:
    """Load package metadata only when a caller requests the version."""
    if name != "__version__":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from importlib.metadata import PackageNotFoundError, version

    try:
        value = version("templateer")
    except PackageNotFoundError:  # pragma: no cover - a source tree, not installed
        value = "0.0.0+unknown"
    globals()[name] = value
    return value

__all__ = [
    "FailureReason",
    "GenerationRequest",
    "GenerationResult",
    "TemplateRegistry",
    "DEFAULT_MODEL",
    "preload",
    "__version__",
]
