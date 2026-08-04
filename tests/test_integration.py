"""End-to-end integration tests for the complete Templateer pipeline.

These tests verify that all components work together correctly:
template loading → model validation → rendering → output validation.

No LLM calls are made; these tests use fixture data only.
"""

import json
from pathlib import Path

import pytest

from templateer.catalog import TemplateCatalog
from templateer.pipeline import generate
from templateer.result import GenerationRequest
from templateer.template import Template
from templateer.validators import validate_output


@pytest.fixture
def catalog():
    """Create a catalog loaded with development templates."""
    c = TemplateCatalog()
    c.load_from_paths([Path("templates")])
    return c


@pytest.fixture
def fastapi_input_data():
    """Load the FastAPI example input fixture."""
    return json.loads((Path("templates/pyproject-uv/examples/fastapi.input.json")).read_text())


@pytest.fixture
def fastapi_expected_output():
    """Load the FastAPI expected output fixture."""
    return Path("templates/pyproject-uv/examples/fastapi.output.toml").read_text().strip()


# ── Structural validation of all templates in catalog ──


def test_all_templates_load_and_have_valid_schemas(catalog):
    """Every template in the catalog loads and has a valid Pydantic schema."""
    assert len(catalog) > 0, "Catalog should have at least one template"

    for template in catalog.templates:
        # Metadata parses (already validated by Template.__init__)
        assert template.name
        assert template.description
        assert len(template.description) > 0

        # Schema loads and is a Pydantic BaseModel
        cls = template.get_schema_class()
        from pydantic import BaseModel

        assert issubclass(cls, BaseModel), (
            f"Template '{template.name}' schema is not a Pydantic BaseModel"
        )
        assert cls.__name__ is not None

        # Prompt exists
        prompt = template.load_prompt()
        assert len(prompt) > 0, f"Template '{template.name}' has empty prompt"

        # Renderer file exists
        renderer_path = template.resolve_path(template.metadata.renderer.file)
        assert renderer_path.exists(), (
            f"Template '{template.name}' missing renderer file: {renderer_path}"
        )

        # Renderer file is readable
        renderer_content = renderer_path.read_text()
        assert len(renderer_content) > 0, f"Template '{template.name}' renderer file is empty"


# ── Fixture roundtrip verification ──


def test_template_fixtures_match(catalog):
    """Every template with examples has matching input→output roundtrips."""
    templates_dir = Path("templates")
    fixture_count = 0

    for template_dir in sorted(templates_dir.iterdir()):
        if not template_dir.is_dir():
            continue
        metadata_file = template_dir / "metadata.yml"
        if not metadata_file.exists():
            continue

        examples_dir = template_dir / "examples"
        if not examples_dir.exists():
            continue

        for input_file in sorted(examples_dir.glob("*.input.json")):
            fixture_count += 1
            # Find matching output file
            stem = input_file.name.replace(".input.json", "")
            output_files = list(examples_dir.glob(f"{stem}.output.*"))
            assert len(output_files) == 1, (
                f"Missing output fixture for {input_file.name} in {template_dir.name}"
            )

            output_file = output_files[0]
            expected = output_file.read_text().strip()

            # Verify roundtrip
            t = Template(template_dir)
            input_data = json.loads(input_file.read_text())
            cls = t.get_schema_class()
            model = cls(**input_data)
            rendered = t.render(model).strip()

            assert rendered == expected, (
                f"Fixture mismatch in {template_dir.name}: "
                f"{input_file.name} → {output_file.name}\n"
                f"Expected length: {len(expected)}, Got length: {len(rendered)}"
            )

    assert fixture_count > 0, "No fixture files found in any template"


def test_fastapi_full_pipeline_without_llm(catalog, fastapi_input_data, fastapi_expected_output):
    """Integration: load template, validate fixture, render, validate output."""
    template = catalog.get("pyproject-uv")

    # Validate model
    cls = template.get_schema_class()
    model = cls(**fastapi_input_data)

    # Verify model has expected data
    assert model.project_name == "fastapi-app"
    assert model.project_description is not None

    # Render
    rendered = template.render(model)
    assert rendered.strip() == fastapi_expected_output

    # Output validation
    errors, _ = validate_output(rendered, "toml")
    assert errors == [], f"Output validation failed: {errors}"


# ── Roundtrip with different model configurations ──


def test_minimal_model_roundtrip(catalog):
    """Minimal model renders valid TOML and passes output validation."""
    template = catalog.get("pyproject-uv")
    cls = template.get_schema_class()
    model = cls(
        project_name="minimal-roundtrip",
        python_version="3.12",
    )
    rendered = template.render(model)
    assert "minimal-roundtrip" in rendered

    errors, _ = validate_output(rendered, "toml")
    assert errors == []


def test_full_model_roundtrip(catalog):
    """A model with all optional fields renders valid TOML."""
    template = catalog.get("pyproject-uv")
    cls = template.get_schema_class()
    model = cls(
        project_name="full-options",
        project_description="A project with everything",
        python_version="3.13",
        project_type="library",
        dependencies=[
            {"name": "requests", "version": ">=2.0"},
            {"name": "httpx", "version": ">=0.27.0"},
        ],
        dev_dependencies=[
            {"name": "pytest", "version": ">=8.0"},
            {"name": "pytest-cov", "version": ">=5.0"},
            {"name": "ruff", "version": ">=0.3.0"},
        ],
        ruff={
            "line_length": 100,
            "target_version": "py313",
            "select": ["E", "F", "I", "N", "W"],
            "ignore": [],
        },
        pytest={
            "testpaths": ["tests"],
            "addopts": ["-v", "--tb=short", "--strict-markers"],
        },
    )
    rendered = template.render(model)
    assert "full-options" in rendered
    assert "A project with everything" in rendered
    assert "requests>=2.0" in rendered
    assert "httpx>=0.27.0" in rendered
    assert "pytest>=8.0" in rendered
    assert "pytest-cov>=5.0" in rendered
    assert "ruff>=0.3.0" in rendered
    assert "[tool.ruff]" in rendered
    assert "[tool.pytest.ini_options]" in rendered
    assert "line-length = 100" in rendered

    errors, _ = validate_output(rendered, "toml")
    assert errors == []


# ── Edge cases ──


def test_empty_dependencies_roundtrip(catalog):
    """Model with empty dependency lists renders correctly."""
    template = catalog.get("pyproject-uv")
    cls = template.get_schema_class()
    model = cls(
        project_name="no-deps",
        python_version="3.12",
        dependencies=[],
        dev_dependencies=[],
    )
    rendered = template.render(model)
    assert "no-deps" in rendered
    assert "[dependency-groups]" not in rendered

    errors, _ = validate_output(rendered, "toml")
    assert errors == []


def test_special_characters_in_name(catalog):
    """Model with names containing dashes renders correctly."""
    template = catalog.get("pyproject-uv")
    cls = template.get_schema_class()
    model = cls(
        project_name="my-awesome-project",
        python_version="3.12",
    )
    rendered = template.render(model)
    assert "my-awesome-project" in rendered

    errors, _ = validate_output(rendered, "toml")
    assert errors == []


def test_dependency_with_extras(catalog):
    """Dependency with extras render properly."""
    template = catalog.get("pyproject-uv")
    cls = template.get_schema_class()
    model = cls(
        project_name="extras-test",
        python_version="3.12",
        dependencies=[
            {"name": "pydantic", "extras": ["email"], "version": ">=2.0"},
        ],
    )
    rendered = template.render(model)
    assert "pydantic[email]>=2.0" in rendered

    errors, _ = validate_output(rendered, "toml")
    assert errors == []


# ── Pipeline integration ──


def test_pipeline_end_to_end_without_llm(catalog):
    """Full pipeline returns a coherent failure for an unknown template."""
    result = generate(catalog, GenerationRequest(
        template_name="nonexistent",
        user_request="test",
    ))
    assert not result.succeeded
    assert result.failure_reason is not None


def test_multiple_render_same_template_deterministic(catalog):
    """Multiple renders with the same model produce identical output."""
    template = catalog.get("pyproject-uv")
    cls = template.get_schema_class()
    model = cls(
        project_name="deterministic-integration",
        python_version="3.12",
        dependencies=[{"name": "pydantic", "version": ">=2.0"}],
    )

    outputs = [template.render(model) for _ in range(10)]
    assert all(o == outputs[0] for o in outputs)


def test_template_catalog_integration_end_to_end(
    catalog, fastapi_input_data, fastapi_expected_output
):
    """Full end-to-end: catalog → get template → validate → render → validate output."""
    # Step 1: Catalog lookup
    assert catalog.has_template("pyproject-uv")
    template = catalog.get("pyproject-uv")
    assert template.name == "pyproject-uv"

    # Step 2: Schema loading
    cls = template.get_schema_class()
    assert cls.__name__ == "PyprojectUvModel"

    # Step 3: Model validation
    model = cls(**fastapi_input_data)

    # Step 4: Rendering
    rendered = template.render(model)
    assert rendered.strip() == fastapi_expected_output

    # Step 5: Output validation
    errors, _ = validate_output(rendered, "toml")
    assert errors == []


# ── Injection audit (Phase 8: templateer check) ──


@pytest.mark.parametrize("name", [p.name for p in sorted(Path("templates").iterdir())
                                  if (p / "metadata.yml").exists()])
def test_bundled_template_resists_injection(name):
    """Every bundled template passes the escaping audit — 0 findings."""
    from templateer.audit import audit_template

    assert audit_template(Template(Path("templates") / name)) == []


# ── Invariant checks ──


def test_renderer_receives_only_model_data(catalog, fastapi_input_data):
    """Verify the renderer only receives validated model data (central invariant)."""
    template = catalog.get("pyproject-uv")
    cls = template.get_schema_class()
    model = cls(**fastapi_input_data)

    # The model_dump in renderer only contains model fields
    dumped = model.model_dump(mode="json")

    # All keys in the dump are Pydantic field names
    for key in dumped:
        assert key in cls.model_fields, f"Unexpected key '{key}' in model dump"

    # The render should work with only these keys
    rendered = template.render(model)
    assert len(rendered) > 0
    assert "fastapi-app" in rendered


def test_output_validator_accepts_valid_fixture(catalog):
    """Output validator accepts the fixture output for each template."""
    templates_dir = Path("templates")
    for template_dir in sorted(templates_dir.iterdir()):
        if not template_dir.is_dir():
            continue
        metadata_file = template_dir / "metadata.yml"
        if not metadata_file.exists():
            continue

        examples_dir = template_dir / "examples"
        if not examples_dir.exists():
            continue

        t = Template(template_dir)
        output_language = t.metadata.output.language

        for output_file in sorted(examples_dir.glob("*.output.*")):
            artifact = output_file.read_text()
            errors, _ = validate_output(artifact, output_language)
            assert errors == [], (
                f"Output validation failed for {output_file} "
                f"in template {template_dir.name}: {errors}"
            )
