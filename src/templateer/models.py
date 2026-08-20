"""Core Pydantic models for Templateer."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, Field

# The output language selects three things at once: the escape grammar, the
# built-in parse check, and whether the escaping audit runs.  A free-text
# field let one typo disable all three in silence.  The set is closed, so a
# typo is a template load error.
StructuredLanguage = Literal["toml", "json", "yaml", "python"]
"""A language with a parser, an escape grammar and an audit payload set."""

UnstructuredLanguage = Literal["markdown", "text"]
"""A language with identity escaping and no parser."""

Language = StructuredLanguage | UnstructuredLanguage
"""Every language a template may declare."""


class ParseValidator(BaseModel):
    """A validator that parses the artifact in a target language.

    ``language`` is a ``StructuredLanguage``.  A parse validator only ever
    runs for the four structured languages; ``validate_output`` skips any
    other value.  So free text here disabled the exact check the template
    author asked for.  The closed set makes a typo a load error.
    """

    model_config = {"extra": "forbid"}

    kind: Literal["parse"]
    language: StructuredLanguage
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


class FullFileOutput(BaseModel):
    """A template that generates a whole file."""

    model_config = {"extra": "forbid"}

    kind: Literal["full_file"] = "full_file"
    path: str = Field(description="Target file path")
    language: Language = Field(description="Target language")


class RegionOutput(BaseModel):
    """A template that generates one bounded region inside a hosting page."""

    model_config = {"extra": "forbid"}

    kind: Literal["region"]
    # The 05 contract says the region payload is a YAML data block.  markdown
    # and text give identity escaping, which is the exact hole ``kind:
    # region`` exists to close.  So the language is pinned to yaml.
    language: Literal["yaml"] = "yaml"
    region: RegionBoundary = Field(description="The bounded slot this template owns")
    path: str | None = Field(
        default=None,
        description="Informational only — region.page is the real anchor",
    )


def _default_kind(v: Any) -> Any:
    """Inject ``kind: full_file`` when the metadata omits it.

    Existing metadata names no ``kind``.  The discriminator needs one, so
    supply the default before the union runs.
    """
    if isinstance(v, dict) and "kind" not in v:
        return {**v, "kind": "full_file"}
    return v


# Discriminated by ``kind``, the same discipline ``OutputValidator`` uses
# above.  ``OutputSpec`` is a type alias, not a class: construct
# ``FullFileOutput`` or ``RegionOutput``, and validate raw metadata with
# ``TypeAdapter(OutputSpec)``.  ``region`` exists on ``RegionOutput`` only.
OutputSpec = Annotated[
    FullFileOutput | RegionOutput,
    Field(discriminator="kind"),
    BeforeValidator(_default_kind),
]


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
