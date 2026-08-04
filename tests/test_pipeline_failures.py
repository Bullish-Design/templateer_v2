from pathlib import Path

import pytest

from templateer.catalog import TemplateCatalog
from templateer.pipeline import generate
from templateer.result import FailureReason, GenerationRequest


@pytest.fixture
def catalog():
    c = TemplateCatalog()
    c.load_from_paths([Path("templates")])
    return c

def test_missing_api_key_returns_failed_never_raises(catalog, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    r = generate(catalog, GenerationRequest(
        template_name="pyproject-uv", user_request="x", max_attempts=1))
    assert not r.succeeded
    assert r.failure_reason is FailureReason.CONFIG_ERROR
    assert r.artifact is None and r.error_detail

def test_non_serializable_context_returns_failed(catalog, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    r = generate(catalog, GenerationRequest(
        template_name="pyproject-uv", user_request="x",
        context={"p": Path("/tmp")}, max_attempts=1))
    assert not r.succeeded          # must not raise TypeError

def test_unknown_template_is_not_retryable(catalog):
    r = generate(catalog, GenerationRequest(template_name="nope", user_request="x"))
    assert r.failure_reason is FailureReason.NO_TEMPLATE
    assert not r.can_retry
    assert r.attempt == 1           # no wasted retries on a permanent failure
