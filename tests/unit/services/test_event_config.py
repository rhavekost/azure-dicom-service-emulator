"""Tests for event provider configuration."""

import pytest
import json

from app.services.events.config import load_providers_from_config
from app.services.events.providers import (

    AzureStorageQueueProvider,
    FileEventProvider,
    InMemoryEventProvider,
    WebhookEventProvider,
)

pytestmark = pytest.mark.unit


def test_load_providers_from_json_env(monkeypatch):
    """Test loading providers from EVENT_PROVIDERS JSON environment variable."""
    config = {
        "providers": [
            {"type": "in_memory", "enabled": True},
            {"type": "file", "enabled": True, "file_path": "/tmp/events.jsonl"},
        ]
    }
    monkeypatch.setenv("EVENT_PROVIDERS", json.dumps(config))

    providers = load_providers_from_config()

    assert len(providers) == 2
    assert isinstance(providers[0], InMemoryEventProvider)
    assert isinstance(providers[1], FileEventProvider)


def test_skip_disabled_providers(monkeypatch):
    """Test that disabled providers are skipped."""
    config = {
        "providers": [
            {"type": "in_memory", "enabled": True},
            {"type": "file", "enabled": False, "file_path": "/tmp/events.jsonl"},
            {"type": "webhook", "enabled": True, "url": "http://example.com/webhook"},
        ]
    }
    monkeypatch.setenv("EVENT_PROVIDERS", json.dumps(config))

    providers = load_providers_from_config()

    assert len(providers) == 2
    assert isinstance(providers[0], InMemoryEventProvider)
    assert isinstance(providers[1], WebhookEventProvider)


def test_load_webhook_provider_with_config(monkeypatch):
    """Test loading webhook provider with configuration."""
    config = {
        "providers": [
            {
                "type": "webhook",
                "enabled": True,
                "url": "https://api.example.com/events",
                "retry_attempts": 5,
            }
        ]
    }
    monkeypatch.setenv("EVENT_PROVIDERS", json.dumps(config))

    providers = load_providers_from_config()

    assert len(providers) == 1
    webhook_provider = providers[0]
    assert isinstance(webhook_provider, WebhookEventProvider)
    assert webhook_provider.url == "https://api.example.com/events"
    assert webhook_provider.retry_attempts == 5


def test_load_azure_queue_provider(monkeypatch):
    """Test loading Azure Storage Queue provider."""
    config = {
        "providers": [
            {
                "type": "azure_storage_queue",
                "enabled": True,
                "connection_string": "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=test;",
                "queue_name": "dicom-events",
            }
        ]
    }
    monkeypatch.setenv("EVENT_PROVIDERS", json.dumps(config))

    providers = load_providers_from_config()

    assert len(providers) == 1
    assert isinstance(providers[0], AzureStorageQueueProvider)


def test_empty_config_returns_empty_list(monkeypatch):
    """Test that empty or missing config returns empty list."""
    # Test with empty providers list
    config = {"providers": []}
    monkeypatch.setenv("EVENT_PROVIDERS", json.dumps(config))
    assert load_providers_from_config() == []

    # Test with no EVENT_PROVIDERS env var
    monkeypatch.delenv("EVENT_PROVIDERS", raising=False)
    assert load_providers_from_config() == []


def test_invalid_provider_type_skipped_with_warning(monkeypatch, caplog):
    """Test that invalid provider types are skipped with a warning logged."""
    config = {
        "providers": [
            {"type": "in_memory", "enabled": True},
            {"type": "invalid_type", "enabled": True},
            {"type": "webhook", "enabled": True, "url": "http://example.com/webhook"},
        ]
    }
    monkeypatch.setenv("EVENT_PROVIDERS", json.dumps(config))

    providers = load_providers_from_config()

    # Should skip invalid provider but load valid ones
    assert len(providers) == 2
    assert isinstance(providers[0], InMemoryEventProvider)
    assert isinstance(providers[1], WebhookEventProvider)

    # Check warning was logged
    assert "Unknown provider type: invalid_type" in caplog.text
