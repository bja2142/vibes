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
