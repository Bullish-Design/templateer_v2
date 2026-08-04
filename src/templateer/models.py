"""Core Pydantic models for Templateer."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class ParseValidator(BaseModel):
    """A validator that parses the artifact in a target language."""

    model_config = {"extra": "forbid"}

    kind: Literal["parse"]
    language: str
    optional: bool = False


class CommandValidator(BaseModel):
    """A validator that runs a command against the artifact."""

    model_config = {"extra": "forbid"}

    kind: Literal["command"]
    command: list[str] = Field(min_length=1)
    optional: bool = False


# Discriminated by ``kind``: malformed validator metadata fails at template
# load instead of silently no-oping at validation time.
OutputValidator = Annotated[
    ParseValidator | CommandValidator, Field(discriminator="kind")
]


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
    """What artifact this template generates."""

    path: str = Field(description="Target file path, e.g. 'pyproject.toml'")
    language: str = Field(description="Target language: toml, yaml, json, python, ...")


class TemplateMetadata(BaseModel):
    """Metadata for a Templateer template, loaded from metadata.yml."""

    model_config = {"populate_by_name": True, "protected_namespaces": (), "extra": "forbid"}

    name: str = Field(description="Template directory name, the sole matching key")
    description: str = Field(description="What this template generates and when to use it")

    output: OutputSpec = Field(description="Artifact this template produces")

    schema_ref: SchemaRef = Field(
        validation_alias="schema", description="Pydantic schema reference"
    )
    prompt: PromptRef = Field(description="Prompt file reference")
    renderer: RendererRef = Field(description="Renderer configuration")

    trigger_filenames: list[str] = Field(
        default_factory=list,
        description="Artifact paths this template can generate",
    )

    validators: list[OutputValidator] = Field(
        default_factory=list,
        description="Optional output validators",
    )
