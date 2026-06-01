"""Pydantic schema for uv-style pyproject.toml generation."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Dependency(BaseModel):
    """A Python package dependency."""

    name: str = Field(description="Package name.")
    version: str | None = Field(
        default=None,
        description="Optional version constraint, such as '>=1.0'.",
    )
    extras: list[str] = Field(default_factory=list)


class RuffConfig(BaseModel):
    """Configuration for the Ruff linter/formatter."""

    line_length: int = Field(default=100, ge=79, le=120)
    target_version: str = Field(description="Python target version, such as 'py312'.")
    select: list[str] = Field(default_factory=lambda: ["E", "F", "I"])
    ignore: list[str] = Field(default_factory=list)


class PytestConfig(BaseModel):
    """Configuration for pytest."""

    testpaths: list[str] = Field(default_factory=lambda: ["tests"])
    addopts: list[str] = Field(default_factory=list)


class PyprojectUvModel(BaseModel):
    """Model for a uv-style pyproject.toml."""

    project_name: str
    project_description: str | None = None
    python_version: str = Field(description="Minimum Python version, such as '3.12'.")
    project_type: Literal["application", "library", "cli"] = "application"

    dependencies: list[Dependency] = Field(default_factory=list)
    dev_dependencies: list[Dependency] = Field(default_factory=list)

    ruff: RuffConfig | None = None
    pytest: PytestConfig | None = None

    @model_validator(mode="after")
    def validate_web_framework_choices(self):
        framework_names = {
            dep.name.lower()
            for dep in self.dependencies
            if dep.name.lower() in {"fastapi", "django", "flask"}
        }

        if len(framework_names) > 1:
            raise ValueError(
                "Choose at most one primary Python web framework."
            )

        return self
