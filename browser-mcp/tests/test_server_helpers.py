import pytest


pytest.importorskip("mcp")
pytest.importorskip("playwright.async_api")

from browser_puppet.errors import SemanticError
from browser_puppet.server import BrowserPuppetApp


def test_diff_states_reports_changed_fields() -> None:
    app = BrowserPuppetApp()

    diff = app.diff_states({"url": "a", "count": 1}, {"url": "b", "count": 1, "title": "T"}, mode="standard")

    assert diff["meaningful_change"] is True
    assert "url" in diff["changed_fields"]
    assert "title" in diff["changed_fields"]


def test_normalize_exception_preserves_semantic_shape() -> None:
    app = BrowserPuppetApp()
    exc = SemanticError("element_not_found", "missing", target={"page_id": "p1"}, retryable=True)

    payload = app.normalize_exception(exc)

    assert payload["error_code"] == "element_not_found"
    assert payload["retryable"] is True
    assert payload["target"] == {"page_id": "p1"}
