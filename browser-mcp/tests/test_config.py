from __future__ import annotations

import importlib

from browser_puppet import config


def test_get_default_artifact_dir_respects_env(monkeypatch) -> None:
    monkeypatch.setenv("BROWSER_PUPPET_ARTIFACT_DIR", "/tmp/browser-puppet-artifacts")
    reloaded = importlib.reload(config)

    assert str(reloaded.get_default_artifact_dir()) == "/tmp/browser-puppet-artifacts"


def test_get_default_transient_retry_delay_ms_respects_env(monkeypatch) -> None:
    monkeypatch.setenv("BROWSER_PUPPET_TRANSIENT_RETRY_DELAY_MS", "250")
    reloaded = importlib.reload(config)

    assert reloaded.get_default_transient_retry_delay_ms() == 250


def test_get_default_allow_local_network_respects_env(monkeypatch) -> None:
    monkeypatch.setenv("BROWSER_PUPPET_ALLOW_LOCAL_NETWORK", "true")
    reloaded = importlib.reload(config)

    assert reloaded.get_default_allow_local_network() is True


def test_get_default_max_contexts_defaults_to_ten(monkeypatch) -> None:
    monkeypatch.delenv("BROWSER_PUPPET_MAX_CONTEXTS", raising=False)
    reloaded = importlib.reload(config)

    assert reloaded.get_default_max_contexts() == 10


def test_get_default_max_contexts_respects_env(monkeypatch) -> None:
    monkeypatch.setenv("BROWSER_PUPPET_MAX_CONTEXTS", "12")
    reloaded = importlib.reload(config)

    assert reloaded.get_default_max_contexts() == 12


def test_get_default_auto_close_stale_contexts_defaults_to_true(monkeypatch) -> None:
    monkeypatch.delenv("BROWSER_PUPPET_AUTO_CLOSE_STALE_CONTEXTS", raising=False)
    reloaded = importlib.reload(config)

    assert reloaded.get_default_auto_close_stale_contexts() is True


def test_get_default_auto_close_stale_contexts_respects_env(monkeypatch) -> None:
    monkeypatch.setenv("BROWSER_PUPPET_AUTO_CLOSE_STALE_CONTEXTS", "false")
    reloaded = importlib.reload(config)

    assert reloaded.get_default_auto_close_stale_contexts() is False


def test_get_default_stale_context_timeout_seconds_defaults_to_one_hour(monkeypatch) -> None:
    monkeypatch.delenv("BROWSER_PUPPET_STALE_CONTEXT_TIMEOUT_SECONDS", raising=False)
    reloaded = importlib.reload(config)

    assert reloaded.get_default_stale_context_timeout_seconds() == 3600


def test_get_default_stale_context_timeout_seconds_respects_env(monkeypatch) -> None:
    monkeypatch.setenv("BROWSER_PUPPET_STALE_CONTEXT_TIMEOUT_SECONDS", "1800")
    reloaded = importlib.reload(config)

    assert reloaded.get_default_stale_context_timeout_seconds() == 1800
