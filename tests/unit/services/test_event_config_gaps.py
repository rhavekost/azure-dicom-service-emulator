"""Unit tests for event provider config loading (gap coverage)."""

import pytest

from app.services.events.config import load_providers_from_config
from app.services.events.providers import InMemoryEventProvider

pytestmark = pytest.mark.unit


def test_no_env_var_returns_empty(monkeypatch):
    monkeypatch.delenv("EVENT_PROVIDERS", raising=False)
    result = load_providers_from_config()
    assert result == []


def test_malformed_json_returns_empty(monkeypatch):
    monkeypatch.setenv("EVENT_PROVIDERS", "not valid json {{")
    result = load_providers_from_config()
    assert result == []


def test_disabled_provider_is_skipped(monkeypatch):
    monkeypatch.setenv(
        "EVENT_PROVIDERS",
        '[{"type": "in_memory", "enabled": false}]',
    )
    result = load_providers_from_config()
    assert result == []


def test_enabled_in_memory_provider_loaded(monkeypatch):
    monkeypatch.setenv(
        "EVENT_PROVIDERS",
        '[{"type": "in_memory", "enabled": true}]',
    )
    result = load_providers_from_config()
    assert len(result) == 1
    assert isinstance(result[0], InMemoryEventProvider)


def test_object_format_with_providers_key(monkeypatch):
    monkeypatch.setenv(
        "EVENT_PROVIDERS",
        '{"providers": [{"type": "in_memory", "enabled": true}]}',
    )
    result = load_providers_from_config()
    assert len(result) == 1


def test_unknown_provider_type_returns_empty(monkeypatch):
    monkeypatch.setenv(
        "EVENT_PROVIDERS",
        '[{"type": "unknown_provider", "enabled": true}]',
    )
    result = load_providers_from_config()
    assert result == []


def test_file_provider_missing_path_returns_empty(monkeypatch):
    monkeypatch.setenv(
        "EVENT_PROVIDERS",
        '[{"type": "file", "enabled": true}]',
    )
    result = load_providers_from_config()
    assert result == []


def test_webhook_provider_missing_url_returns_empty(monkeypatch):
    monkeypatch.setenv(
        "EVENT_PROVIDERS",
        '[{"type": "webhook", "enabled": true}]',
    )
    result = load_providers_from_config()
    assert result == []


def test_azure_queue_missing_connection_string_returns_empty(monkeypatch):
    monkeypatch.setenv(
        "EVENT_PROVIDERS",
        '[{"type": "azure_storage_queue", "enabled": true, "queue_name": "q"}]',
    )
    result = load_providers_from_config()
    assert result == []


def test_provider_missing_type_is_skipped(monkeypatch):
    monkeypatch.setenv(
        "EVENT_PROVIDERS",
        '[{"enabled": true}]',
    )
    result = load_providers_from_config()
    assert result == []
