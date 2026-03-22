from __future__ import annotations

import logging

import pytest

from browser_puppet.server import TRACE_LEVEL, normalize_log_level


def test_normalize_log_level_defaults_info() -> None:
    assert normalize_log_level(None) == logging.INFO


def test_normalize_log_level_supports_trace() -> None:
    assert normalize_log_level("trace") == TRACE_LEVEL


def test_normalize_log_level_rejects_unknown_value() -> None:
    with pytest.raises(ValueError):
        normalize_log_level("nope")
