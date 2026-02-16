"""Configuration loading for event providers."""

import json
import logging
import os

from app.services.events.providers import (
    AzureStorageQueueProvider,
    EventProvider,
    FileEventProvider,
    InMemoryEventProvider,
    WebhookEventProvider,
)

logger = logging.getLogger(__name__)


def load_providers_from_config() -> list[EventProvider]:
    """
    Load event providers from EVENT_PROVIDERS environment variable.

    Expected JSON format:
    {
        "providers": [
            {"type": "in_memory", "enabled": true},
            {"type": "file", "enabled": true, "file_path": "/tmp/events.jsonl"},
            {"type": "webhook", "enabled": true, "url": "https://...", "retry_attempts": 3},
            {"type": "azure_storage_queue", "enabled": true, "connection_string": "...", "queue_name": "..."}
        ]
    }

    Returns:
        List of instantiated EventProvider instances.
    """
    providers: list[EventProvider] = []

    # Load configuration from environment variable
    config_json = os.getenv("EVENT_PROVIDERS")
    if not config_json:
        logger.info("No EVENT_PROVIDERS environment variable found, using empty provider list")
        return providers

    try:
        config = json.loads(config_json)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse EVENT_PROVIDERS JSON: {e}")
        return providers

    # Extract providers list from config
    # Support both array format and object with "providers" key
    if isinstance(config, list):
        provider_configs = config
    elif isinstance(config, dict):
        provider_configs = config.get("providers", [])
    else:
        logger.error("EVENT_PROVIDERS must be a JSON array or object with 'providers' key")
        return providers

    if not provider_configs:
        logger.info("No providers configured in EVENT_PROVIDERS")
        return providers

    # Instantiate each provider
    for provider_config in provider_configs:
        # Skip disabled providers
        if not provider_config.get("enabled", True):
            logger.debug(f"Skipping disabled provider: {provider_config.get('type')}")
            continue

        provider_type = provider_config.get("type")
        if not provider_type:
            logger.warning("Provider configuration missing 'type' field, skipping")
            continue

        try:
            provider = _create_provider(provider_type, provider_config)
            if provider:
                providers.append(provider)
                logger.info(f"Loaded provider: {provider_type}")
        except Exception as e:
            logger.error(f"Failed to create provider of type '{provider_type}': {e}")
            continue

    logger.info(f"Loaded {len(providers)} event provider(s)")
    return providers


def _create_provider(provider_type: str, config: dict[str, any]) -> EventProvider | None:
    """
    Create a provider instance based on type and configuration.

    Args:
        provider_type: Type of provider (in_memory, file, webhook, azure_storage_queue)
        config: Provider configuration dictionary

    Returns:
        EventProvider instance or None if type is unknown
    """
    if provider_type == "in_memory":
        return InMemoryEventProvider()

    elif provider_type == "file":
        file_path = config.get("file_path")
        if not file_path:
            logger.error("File provider missing 'file_path' configuration")
            return None
        return FileEventProvider(file_path=file_path)

    elif provider_type == "webhook":
        url = config.get("url")
        if not url:
            logger.error("Webhook provider missing 'url' configuration")
            return None
        retry_attempts = config.get("retry_attempts", 3)
        return WebhookEventProvider(url=url, retry_attempts=retry_attempts)

    elif provider_type == "azure_storage_queue":
        connection_string = config.get("connection_string")
        queue_name = config.get("queue_name")
        if not connection_string or not queue_name:
            logger.error(
                "Azure Storage Queue provider missing 'connection_string' or 'queue_name' configuration"
            )
            return None
        return AzureStorageQueueProvider(connection_string=connection_string, queue_name=queue_name)

    else:
        logger.warning(f"Unknown provider type: {provider_type}")
        return None
