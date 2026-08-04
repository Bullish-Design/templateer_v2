"""The region output kind: declared boundary + enforceable payload check.

Covers the proposal's gate (``TEMPLATEER-V2_OUTPUT_KIND_REGION.md`` §4)
entirely offline: a fixture region template built in ``tmp_path``, a stubbed
``generate_model`` (the same pattern as ``tests/test_pipeline.py``), and
direct validator probes.  No LLM, no network, no argentic dependency.
"""

import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from pydantic import ValidationError

from templateer.catalog import TemplateCatalog
from templateer.models import MarkdownValidator, OutputSpec, TemplateMetadata
from templateer.pipeline import generate
from templateer.result import FailureReason, GenerationRequest
from templateer.validators import (
    effective_validators,
    validate_region_payload,
)

# ---------------------------------------------------------------------------
# Fixture: a self-contained region template (no bundled-template changes)
# ---------------------------------------------------------------------------

REGION_METADATA = {
    "name": "region-demo",
    "description": "Generate the payload for a fenced YAML data block.",
    "output": {
        "path": "docs/status.md",       # informational for regions
        "language": "yaml",
        "kind": "region",
        "region": {"page": "docs/status.md", "ref": "$block-status"},
    },
    "schema": {"module": "schema", "class": "RegionModel"},
    "prompt": {"file": "prompt.md"},
    "renderer": {"engine": "minijinja", "file": "template.j2"},
}

REGION_SCHEMA = '''\
from pydantic import BaseModel, Field


class RegionModel(BaseModel):
    status: str = Field(description="Block status, e.g. 'ok'")
    items: list[str] = Field(min_length=1, description="Payload items")
'''

REGION_TEMPLATE = '''\
status: "{{ status }}"
items:
{% for item in items %}
  - "{{ item }}"
{% endfor %}
'''

REGION_EXAMPLE = {"status": "ok", "items": ["alpha", "beta"]}


@pytest.fixture
def region_template(tmp_path: Path) -> Path:
    """Build a minimal kind:region template on disk."""
    root = tmp_path / "region-demo"
    (root / "examples").mkdir(parents=True)
    # deepcopy: tests mutate the metadata dict, and the module-level constant
    # must never be corrupted across tests.
    (root / "metadata.yml").write_text(
        yaml.safe_dump(deepcopy(REGION_METADATA)), encoding="utf-8")
    (root / "schema.py").write_text(REGION_SCHEMA, encoding="utf-8")
    (root / "prompt.md").write_text("Fill the schema.", encoding="utf-8")
    (root / "template.j2").write_text(REGION_TEMPLATE, encoding="utf-8")
    (root / "examples" / "ok.input.json").write_text(
        json.dumps(REGION_EXAMPLE), encoding="utf-8")
    return tmp_path


@pytest.fixture
def region_catalog(region_template: Path) -> TemplateCatalog:
    c = TemplateCatalog()
    c.load_from_paths([region_template])
    return c


def _stub_generate_model(template, **kwargs):
    data = json.loads(
        (template.root / "examples" / "ok.input.json").read_text(encoding="utf-8"))
    return template.get_schema_class().model_validate(data)


# ---------------------------------------------------------------------------
# Gate 1+5: a kind:region template loads, renders, and round-trips
# ---------------------------------------------------------------------------

def test_region_template_metadata_loads() -> None:
    meta = TemplateMetadata.model_validate(deepcopy(REGION_METADATA))
    assert meta.output.kind == "region"
    assert meta.output.region is not None
    assert meta.output.region.page == "docs/status.md"
    assert meta.output.region.ref == "$block-status"
    assert meta.output.region.anchor is None


def test_region_artifact_is_body_only_and_valid_yaml(
    region_catalog: TemplateCatalog,
) -> None:
    """Gate 1: payload splices as a clean fenced block — body-only YAML."""
    with patch("templateer.pipeline.generate_model", side_effect=_stub_generate_model):
        result = generate(region_catalog, GenerationRequest(
            template_name="region-demo", user_request="make it", max_attempts=1))
    assert result.succeeded, result.error_detail
    assert result.failure_reason is None
    assert result.output_path == "docs/status.md"       # the page path
    parsed = yaml.safe_load(result.artifact)            # body-only YAML
    assert parsed == REGION_EXAMPLE
    assert validate_region_payload(result.artifact) == []


# ---------------------------------------------------------------------------
# Gate 2: kind/region coupling fails at load
# ---------------------------------------------------------------------------

def test_region_kind_requires_region() -> None:
    data = deepcopy(REGION_METADATA)
    data["output"] = {**data["output"], "region": None}   # kind: region, no region
    with pytest.raises(ValidationError):
        TemplateMetadata.model_validate(data)


def test_full_file_kind_rejects_region() -> None:
    data = deepcopy(REGION_METADATA)
    data["output"] = {**data["output"], "kind": "full_file"}
    with pytest.raises(ValidationError):
        TemplateMetadata.model_validate(data)


def test_region_boundary_rejects_extra_fields() -> None:
    data = deepcopy(REGION_METADATA)
    data["output"]["region"] = {
        "page": "x", "ref": "y", "typo": 1,
    }
    with pytest.raises(ValidationError):
        TemplateMetadata.model_validate(data)


# ---------------------------------------------------------------------------
# Gate 4: the markdown validator rejects broken blocks
# ---------------------------------------------------------------------------

class TestValidateRegionPayload:
    def test_accepts_clean_body(self) -> None:
        assert validate_region_payload("status: ok\nitems:\n  - a\n") == []

    def test_accepts_clean_fenced_block(self) -> None:
        # Tolerated for convenience; the contract is body-only (D1).
        assert validate_region_payload("---\nstatus: ok\n---\n") == []
        assert validate_region_payload("```yaml\nstatus: ok\n```\n") == []

    def test_rejects_unclosed_fence(self) -> None:
        errors = validate_region_payload("```yaml\nstatus: ok\n")
        assert errors and "fence" in errors[0].lower()

    def test_rejects_stray_interior_fence(self) -> None:
        errors = validate_region_payload("```yaml\nstatus: ok\n```\nmore: x\n```\n")
        assert errors and "fence" in errors[0].lower()

    def test_rejects_non_yaml_body(self) -> None:
        errors = validate_region_payload("status: [unclosed\n")
        assert errors and "yaml" in errors[0].lower()

    def test_rejects_multi_document_payload(self) -> None:
        # A trailing --- is a second document: ComposerError (verified).
        errors = validate_region_payload("status: ok\n---\nother: 1\n")
        assert errors

    def test_rejects_scalar_payload(self) -> None:
        errors = validate_region_payload("just a string\n")
        assert errors and "mapping" in errors[0].lower()

    def test_rejects_empty_payload(self) -> None:
        assert validate_region_payload("") != []
        assert validate_region_payload("# only a comment\n") != []

    def test_rejects_duplicate_keys(self) -> None:
        # PyYAML silently keeps the last (verified); we must not.
        errors = validate_region_payload("status: a\nstatus: b\n")
        assert errors and "duplicate" in errors[0].lower()

    def test_round_trip_is_stable(self) -> None:
        assert validate_region_payload("a: 1\nb:\n  - x\n  - y\n") == []


def test_markdown_validator_kind_is_in_the_union() -> None:
    """Explicit kind: 'markdown' is declarable, for full_file authors."""
    data = deepcopy(REGION_METADATA)
    data["output"] = {**data["output"], "kind": "full_file", "region": None}
    data["validators"] = [{"kind": "markdown"}]
    meta = TemplateMetadata.model_validate(data)
    assert isinstance(meta.validators[0], MarkdownValidator)


# ---------------------------------------------------------------------------
# Gate 4 via the pipeline: a broken payload fails the generation
# ---------------------------------------------------------------------------

def test_region_pipeline_fails_on_broken_payload(
    region_catalog: TemplateCatalog,
) -> None:
    """The payload check is non-optional: even a 'clean' declared validator
    list cannot let a broken block through."""
    with patch("templateer.pipeline.generate_model", side_effect=_stub_generate_model):
        with patch("templateer.template.Template.render",
                   return_value="```yaml\nstatus: nope\n"):
            result = generate(region_catalog, GenerationRequest(
                template_name="region-demo", user_request="x", max_attempts=1))
    assert not result.succeeded
    assert result.failure_reason is FailureReason.OUTPUT_VALIDATION_FAILED
    assert result.artifact is None


def test_effective_validators_prepends_markdown_for_region(
    region_catalog: TemplateCatalog,
) -> None:
    template = region_catalog.get("region-demo")
    declared = template.metadata.validators
    effective = effective_validators(template.metadata.output, declared)
    assert len(effective) == len(declared) + 1
    assert isinstance(effective[0], MarkdownValidator)


# ---------------------------------------------------------------------------
# Gate 5: back-compat — full_file unchanged
# ---------------------------------------------------------------------------

def test_kind_defaults_to_full_file() -> None:
    spec = OutputSpec(path="pyproject.toml", language="toml")
    assert spec.kind == "full_file"
    assert spec.region is None
