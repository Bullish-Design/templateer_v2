"""Core Pydantic models for Templateer."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class OutputValidator(BaseModel):
    """An output validator that checks the rendered artifact."""

    kind: Literal["parse", "command"]
    language: str | None = Field(
        default=None,
        description="Target language for parse validators (toml, json, yaml, python)",
    )
    command: list[str] | None = Field(
        default=None,
        description="Command and args for command validators",
    )
    optional: bool = Field(default=False)


class SchemaRef(BaseModel):
    """Reference to a Pydantic schema class within a Python file."""

    model_config = {"populate_by_name": True}

    module: str = Field(description="Python module name relative to template root")
    class_name: str = Field(alias="class", description="Pydantic model class name")


class PromptRef(BaseModel):
    """Reference to a prompt file."""

    file: str = Field(description="Path to prompt file relative to template root")


class RendererRef(BaseModel):
    """Reference to a renderer configuration."""

    engine: Literal["minijinja"] = "minijinja"
    file: str = Field(description="Path to Jinja template file relative to template root")


class OutputSpec(BaseModel):
    """Describes what artifact a template generates."""

    path: str = Field(description="Target file path (e.g., 'pyproject.toml')")
    kind: Literal["full_file"] = "full_file"
    language: str = Field(description="Target language (toml, yaml, json, python, etc.)")


class TemplateMetadata(BaseModel):
    """Metadata for a Templateer template, loaded from metadata.yml."""

    model_config = {"populate_by_name": True, "protected_namespaces": (), "extra": "forbid"}

    name: str = Field(description="Template directory name, the sole matching key")
    description: str = Field(description="What this template generates and when to use it")

    outputs: list[OutputSpec] = Field(description="Artifacts this template produces")

    schema_ref: SchemaRef = Field(
        validation_alias="schema", description="Pydantic schema reference"
    )
    prompt: PromptRef = Field(description="Prompt file reference")
    renderer: RendererRef = Field(description="Renderer configuration")

    strict_context: bool = Field(default=True)

    triggers: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Trigger conditions for template discovery",
    )

    validators: list[OutputValidator] = Field(
        default_factory=list,
        description="Optional output validators",
    )


class TemplateGenerationResult(BaseModel):
    """The result of a generation operation."""

    template_name: str
    model: dict[str, Any] = Field(description="The validated Pydantic model as a dict")
    rendered: str = Field(description="The rendered artifact text")
    validation_messages: list[str] = Field(default_factory=list)
