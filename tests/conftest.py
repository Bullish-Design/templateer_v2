"""Shared test fixtures and configuration.

The guardrail suites (`test_escaping_exhaustive`, `test_round_trip`,
`test_audit`, `test_region_hardening`, `test_surface`, `test_cli_json`) build
throwaway templates on disk.  `make_template` is the single builder they use.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

# Finding tags from TEMPLATEER-V2_ADVERSARIAL_REVIEW_2.md.  Registered as
# markers so a later wave can run one finding's guardrails with
# ``pytest -m finding_a1``.
FINDING_TAGS = (
    "a1", "a2", "a3", "a4", "a5", "a6", "a7", "a8", "a9",
    "b1", "b2", "b3", "b4", "b5", "b6", "b7", "b8", "b9",
    "c1", "c2", "c7", "c8",
)


def pytest_configure(config: pytest.Config) -> None:
    """Register one marker per review finding."""
    for tag in FINDING_TAGS:
        config.addinivalue_line(
            "markers", f"finding_{tag}: guards review finding §{tag.upper()}"
        )
    config.addinivalue_line(
        "markers", "false_positive_guard: the check must stay silent here"
    )


@pytest.fixture(autouse=True)
def _no_retry_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove the LLM_FAILED retry backoff for every test.

    ``pipeline.RETRY_BACKOFF_SECONDS`` doubles per attempt, so three attempts
    cost three seconds of real time.  A test that measures the repair loop
    must not also measure the clock.  ``tests/test_surface.py`` asserts the
    backoff schedule directly, with a recorded sleep instead of a real one.
    """
    monkeypatch.setattr("templateer.pipeline.RETRY_BACKOFF_SECONDS", 0.0)


DEFAULT_SCHEMA_SOURCE = """\
from pydantic import BaseModel


class M(BaseModel):
    x: str
"""


@pytest.fixture
def repo_templates() -> Path:
    """The repository's own ``templates/`` directory."""
    return REPO_ROOT / "templates"


@pytest.fixture
def pyproject_uv_input() -> dict[str, Any]:
    """The pyproject-uv example fixture, as a dict."""
    fixture = REPO_ROOT / "templates/pyproject-uv/examples/fastapi.input.json"
    return json.loads(fixture.read_text(encoding="utf-8"))


@pytest.fixture
def make_template(tmp_path: Path) -> Callable[..., Path]:
    """Return a builder for throwaway template directories.

    The builder returns the template directory.  Its parent is a templates
    root, so a catalog loads it with ``load_from_paths([path.parent])``.
    """
    counter = itertools.count()

    def _make(
        name: str,
        *,
        output: dict[str, Any],
        template_source: str,
        schema_source: str = DEFAULT_SCHEMA_SOURCE,
        fixtures: dict[str, Any] | None = None,
        validators: list[dict[str, Any]] | None = None,
        schema_ref: dict[str, str] | None = None,
        renderer_ref: dict[str, str] | None = None,
        trigger_filenames: list[str] | None = None,
        prompt: str = "Fill the schema.\n",
        description: str = "A template built by the guardrail tests.",
        root: Path | None = None,
        write_schema: bool = True,
        write_template_file: bool = True,
    ) -> Path:
        base = root if root is not None else tmp_path / f"templates{next(counter)}"
        template_dir = base / name
        template_dir.mkdir(parents=True)

        metadata: dict[str, Any] = {
            "name": name,
            "description": description,
            "output": output,
            "schema": schema_ref or {"module": "schema", "class": "M"},
            "prompt": {"file": "prompt.md"},
            "renderer": renderer_ref or {"engine": "minijinja", "file": "template.j2"},
        }
        if validators is not None:
            metadata["validators"] = validators
        if trigger_filenames is not None:
            metadata["trigger_filenames"] = trigger_filenames

        (template_dir / "metadata.yml").write_text(
            yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8"
        )
        (template_dir / "prompt.md").write_text(prompt, encoding="utf-8")
        if write_schema:
            (template_dir / "schema.py").write_text(schema_source, encoding="utf-8")
        if write_template_file:
            (template_dir / "template.j2").write_text(template_source, encoding="utf-8")

        if fixtures:
            examples = template_dir / "examples"
            examples.mkdir()
            for fixture_name, data in fixtures.items():
                (examples / fixture_name).write_text(
                    json.dumps(data, indent=2), encoding="utf-8"
                )

        return template_dir

    return _make


@pytest.fixture
def stub_model_generation(monkeypatch: pytest.MonkeyPatch) -> Callable[..., list[dict]]:
    """Replace the pipeline's model-generation call and record every call.

    The pipeline calls ``generate_model`` today.  CONTRACT.md §7 renames it to
    ``generate_model_async`` and changes the return to ``(model, usage)``.
    This helper patches every name the pipeline exposes, so a test measures
    the finding it targets and not the rename.

    Args:
        factory: ``factory(template, attempt)`` returns the model instance, or
            raises to simulate an LLM failure.
        usage: token counts the async entry point reports alongside the model.

    Returns:
        The list of recorded calls.  Each call is the keyword arguments, plus
        ``args`` for positional ones and ``template``.
    """

    def _stub(
        factory: Callable[..., Any], usage: dict[str, int] | None = None
    ) -> list[dict[str, Any]]:
        import templateer.pipeline as pipeline

        calls: list[dict[str, Any]] = []
        names = [
            n
            for n in ("generate_model", "generate_model_async")
            if hasattr(pipeline, n)
        ]
        assert names, "templateer.pipeline exposes no model-generation entry point"

        def record(template: Any, args: tuple, kwargs: dict) -> int:
            calls.append({"template": template, "args": args, **kwargs})
            return len(calls)

        def sync_stub(template: Any, *args: Any, **kwargs: Any) -> Any:
            attempt = record(template, args, kwargs)
            return factory(template, attempt)

        async def async_stub(template: Any, *args: Any, **kwargs: Any) -> Any:
            attempt = record(template, args, kwargs)
            return factory(template, attempt), usage

        for name in names:
            monkeypatch.setattr(
                pipeline, name, async_stub if name.endswith("_async") else sync_stub
            )
        return calls

    return _stub
