"""Region-kind guardrails — §A5, §A6, §B3, §B5.

The README calls the region payload check non-negotiable: "it cannot be
omitted or turned off by a template author".  One metadata line turns it off
today.  These tests try to turn it off.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from templateer.catalog import TemplateCatalog
from templateer.models import MarkdownValidator
from templateer.result import GenerationRequest, GenerationResult
from templateer.template import Template, TemplateLoadError
from templateer.validators import effective_validators, validate_region_payload

REGION_SCHEMA = """\
from pydantic import BaseModel


class M(BaseModel):
    body: str
"""

REGION_BOUNDARY = {"page": "docs/status.md", "ref": "$block-status"}

# The declaration the review used to disable the "non-negotiable" check.
OPT_OUT: list[dict[str, Any]] = [{"kind": "markdown", "optional": True}]


def _region_output(language: str = "yaml") -> dict[str, Any]:
    return {
        "path": "docs/status.md",
        "language": language,
        "kind": "region",
        "region": dict(REGION_BOUNDARY),
    }


def _generate(
    make_template: Callable[..., Path],
    stub_model_generation: Callable[..., list[dict]],
    *,
    template_source: str,
    body: str,
    validators: list[dict[str, Any]] | None = None,
    name: str = "regopt",
) -> GenerationResult:
    from templateer.pipeline import generate

    template_dir = make_template(
        name,
        output=_region_output(),
        schema_source=REGION_SCHEMA,
        template_source=template_source,
        validators=OPT_OUT if validators is None else validators,
    )
    catalog = TemplateCatalog()
    catalog.load_from_paths([template_dir.parent])
    stub_model_generation(
        lambda template, attempt: template.get_schema_class()(body=body)
    )
    return generate(
        catalog,
        GenerationRequest(template_name=name, user_request="x", max_attempts=1),
    )


# ---------------------------------------------------------------------------
# §A5 — the check cannot be turned off
# ---------------------------------------------------------------------------


@pytest.mark.finding_a5
def test_effective_validators_prepends_a_non_optional_markdown_check(
    make_template: Callable[..., Path],
) -> None:
    """`optional: true` must not downgrade the region check to a warning."""
    template_dir = make_template(
        "regopt",
        output=_region_output(),
        schema_source=REGION_SCHEMA,
        template_source="{{ body }}",
        validators=OPT_OUT,
    )
    template = Template(template_dir)
    effective = effective_validators(
        template.metadata.output, template.metadata.validators
    )
    assert isinstance(effective[0], MarkdownValidator), [
        type(v).__name__ for v in effective
    ]
    assert effective[0].optional is False


@pytest.mark.finding_a5
def test_page_corrupting_payload_fails_generation(
    make_template: Callable[..., Path],
    stub_model_generation: Callable[..., list[dict]],
) -> None:
    """The review's exact case: a fenced, non-mapping artifact must fail."""
    result = _generate(
        make_template,
        stub_model_generation,
        template_source="```\n{{ body }}",
        body="just a sentence, not a mapping",
    )
    assert result.artifact is None
    assert result.succeeded is False


@pytest.mark.finding_a5
def test_bare_scalar_payload_fails_despite_an_optional_declaration(
    make_template: Callable[..., Path],
    stub_model_generation: Callable[..., list[dict]],
) -> None:
    """A bare scalar is valid YAML, so only the region check catches it."""
    result = _generate(
        make_template,
        stub_model_generation,
        template_source="{{ body }}",
        body="just a sentence, not a mapping",
    )
    assert result.succeeded is False


@pytest.mark.finding_a5
@pytest.mark.finding_b5
@pytest.mark.parametrize("body", ["{}", "[]"], ids=["empty-map", "empty-list"])
def test_empty_payload_fails_despite_an_optional_declaration(
    make_template: Callable[..., Path],
    stub_model_generation: Callable[..., list[dict]],
    body: str,
) -> None:
    """An empty payload is valid YAML and a generation bug (05 D7)."""
    result = _generate(
        make_template, stub_model_generation, template_source="{{ body }}", body=body
    )
    assert result.succeeded is False


@pytest.mark.finding_a5
@pytest.mark.parametrize("language", ["markdown", "text"])
def test_region_template_rejects_unstructured_language(
    make_template: Callable[..., Path], language: str
) -> None:
    """markdown and text give identity escaping — the hole the kind closes."""
    template_dir = make_template(
        "regloose",
        output=_region_output(language),
        schema_source=REGION_SCHEMA,
        template_source="{{ body }}",
    )
    with pytest.raises(TemplateLoadError):
        Template(template_dir)


@pytest.mark.finding_a5
@pytest.mark.false_positive_guard
def test_region_template_with_yaml_language_loads(
    make_template: Callable[..., Path],
) -> None:
    """YAML stays legal: the 05 contract says the payload is a YAML block."""
    template_dir = make_template(
        "regyaml",
        output=_region_output("yaml"),
        schema_source=REGION_SCHEMA,
        template_source='status: "{{ body }}"',
    )
    assert Template(template_dir).metadata.output.kind == "region"


# ---------------------------------------------------------------------------
# §B3 — the page owns the fences
# ---------------------------------------------------------------------------


@pytest.mark.finding_b3
@pytest.mark.parametrize(
    "payload",
    [
        "```yaml\nstatus: ok\n```",
        "```\nstatus: ok\n```",
        "---\nstatus: ok\n---",
    ],
    ids=["tagged-fence", "bare-fence", "dashes"],
)
def test_fenced_region_payload_is_rejected(payload: str) -> None:
    """A fenced artifact double-fences the hosting block, so it is an error."""
    errors = validate_region_payload(payload)
    assert errors, f"a fenced payload was accepted: {payload!r}"


@pytest.mark.finding_b5
@pytest.mark.parametrize("payload", ["{}", "[]"], ids=["empty-map", "empty-list"])
def test_empty_region_payload_is_rejected(payload: str) -> None:
    """README and 05 D7 both say empty payloads are rejected."""
    assert validate_region_payload(payload), f"{payload!r} was accepted"


@pytest.mark.finding_b3
@pytest.mark.false_positive_guard
@pytest.mark.parametrize(
    "payload",
    ["status: ok\nowner: andrew\n", "- one\n- two\n", "status:\n  code: 200\n"],
    ids=["mapping", "list", "nested"],
)
def test_bare_region_payload_is_accepted(payload: str) -> None:
    """A bare YAML data block is the contract — it must pass."""
    assert validate_region_payload(payload) == []


# ---------------------------------------------------------------------------
# §A6 — the result must be consumable
# ---------------------------------------------------------------------------


@pytest.mark.finding_a6
def test_region_result_carries_kind_and_boundary(
    make_template: Callable[..., Path],
    stub_model_generation: Callable[..., list[dict]],
) -> None:
    """The consumer needs `ref` to splice; today it must reopen the metadata."""
    result = _generate(
        make_template,
        stub_model_generation,
        template_source='status: "{{ body }}"',
        body="ok",
        validators=[],
    )
    assert result.succeeded, result.error_detail
    assert result.kind == "region"
    assert result.region is not None
    assert result.region.ref == REGION_BOUNDARY["ref"]
    assert result.region.page == REGION_BOUNDARY["page"]
    assert result.output_path == REGION_BOUNDARY["page"]


@pytest.mark.finding_a6
@pytest.mark.false_positive_guard
def test_full_file_result_carries_no_region(
    make_template: Callable[..., Path],
    stub_model_generation: Callable[..., list[dict]],
) -> None:
    """A full-file generation must be distinguishable from a region one."""
    from templateer.pipeline import generate

    template_dir = make_template(
        "fullfile",
        output={"path": "out.yaml", "language": "yaml"},
        schema_source=REGION_SCHEMA,
        template_source='status: "{{ body }}"',
    )
    catalog = TemplateCatalog()
    catalog.load_from_paths([template_dir.parent])
    stub_model_generation(
        lambda template, attempt: template.get_schema_class()(body="ok")
    )
    result = generate(
        catalog,
        GenerationRequest(template_name="fullfile", user_request="x", max_attempts=1),
    )
    assert result.succeeded, result.error_detail
    assert result.kind == "full_file"
    assert result.region is None
