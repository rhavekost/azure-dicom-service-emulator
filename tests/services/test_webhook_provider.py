"""Tests for WebhookEventProvider."""

import pytest
from unittest.mock import AsyncMock, patch

from app.models.events import DicomEvent
from app.services.events.providers import WebhookEventProvider


@pytest.mark.asyncio
async def test_webhook_provider_publish_success():
    """WebhookProvider sends HTTP POST on publish."""
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value.status_code = 200

        provider = WebhookEventProvider("https://webhook.site/test")
        event = DicomEvent.from_instance_created("1.2.3", "4.5.6", "7.8.9", 1, "http://localhost")

        await provider.publish(event)

        mock_post.assert_awaited_once()
        call_args = mock_post.call_args
        assert call_args.args[0] == "https://webhook.site/test"
        assert call_args.kwargs["json"] == event.to_dict()


@pytest.mark.asyncio
async def test_webhook_provider_publish_batch():
    """WebhookProvider sends each event in batch separately."""
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value.status_code = 200

        provider = WebhookEventProvider("https://webhook.site/test")
        events = [
            DicomEvent.from_instance_created("1.2.3", "4.5.6", "7.8.9", 1, "http://localhost"),
            DicomEvent.from_instance_created("1.2.3", "4.5.6", "10.11.12", 2, "http://localhost"),
        ]

        await provider.publish_batch(events)

        assert mock_post.await_count == 2


@pytest.mark.asyncio
async def test_webhook_provider_retry_on_failure():
    """WebhookProvider retries on HTTP errors."""
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        # Fail first 2 attempts, succeed on 3rd
        mock_post.side_effect = [
            Exception("Network error"),
            Exception("Network error"),
            AsyncMock(status_code=200),
        ]

        provider = WebhookEventProvider("https://webhook.site/test", retry_attempts=3)
        event = DicomEvent.from_instance_created("1.2.3", "4.5.6", "7.8.9", 1, "http://localhost")

        await provider.publish(event)

        # Should have retried 3 times total
        assert mock_post.await_count == 3


@pytest.mark.asyncio
async def test_webhook_provider_gives_up_after_retries():
    """WebhookProvider raises after max retries."""
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = Exception("Network error")

        provider = WebhookEventProvider("https://webhook.site/test", retry_attempts=3)
        event = DicomEvent.from_instance_created("1.2.3", "4.5.6", "7.8.9", 1, "http://localhost")

        with pytest.raises(Exception, match="Network error"):
            await provider.publish(event)

        # Should have tried 3 times
        assert mock_post.await_count == 3
