"""Core Pydantic models for Templateer."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator


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


class MarkdownValidator(BaseModel):
    """Validates the artifact as a fenced YAML region payload."""

    model_config = {"extra": "forbid"}

    kind: Literal["markdown"]
    optional: bool = False


# Discriminated by ``kind``: malformed validator metadata fails at template
# load instead of silently no-oping at validation time.
OutputValidator = Annotated[
    ParseValidator | CommandValidator | MarkdownValidator,
    Field(discriminator="kind"),
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


class RegionBoundary(BaseModel):
    """The page region a ``kind: "region"`` template may be spliced into.

    The consumer owns the fences and the surrounding page; this declares the
    bounded slot: which page, which ``$ref``'d block's payload the template
    replaces, and which annotation it resolves.
    """

    model_config = {"extra": "forbid"}

    page: str = Field(description="Hosting page name (or page-name pattern)")
    ref: str = Field(description="The data block's $ref — the payload this region owns")
    anchor: str | None = Field(
        default=None,
        description="Annotation ref recorded in the block's addressed: list",
    )


class OutputSpec(BaseModel):
    """What artifact this template generates."""

    path: str = Field(
        description=(
            "Target file path; for kind=region this is informational — "
            "region.page is the real anchor"
        )
    )
    language: str = Field(description="Target language: toml, yaml, json, python, ...")
    kind: Literal["full_file", "region"] = "full_file"
    region: RegionBoundary | None = Field(
        default=None,
        description="Required iff kind=region; forbidden for full_file",
    )

    @model_validator(mode="after")
    def _kind_region_consistency(self) -> "OutputSpec":
        if self.kind == "region" and self.region is None:
            raise ValueError("kind='region' requires a region boundary")
        if self.kind == "full_file" and self.region is not None:
            raise ValueError("kind='full_file' must not carry a region boundary")
        return self


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
