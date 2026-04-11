"""Integration tests for AzureStorageQueueProvider against a real Azurite instance.

These tests require Azurite (Azure Storage emulator) to be running.
By default the Queue service is expected at http://localhost:10001.
Override with the AZURITE_QUEUE_URL environment variable.

Skip condition:
    The tests are marked @pytest.mark.integration and are automatically
    skipped when Azurite is not reachable (checked once in a session-scoped
    fixture).  Run the full suite with Azurite via:

        docker compose up -d azurite
        pytest tests/integration/test_event_providers.py -m integration
"""

import json
import os
import socket
import uuid

import pytest
from azure.core.exceptions import ResourceExistsError
from azure.storage.queue import QueueClient

from app.models.events import DicomEvent
from app.services.events.providers import AzureStorageQueueProvider

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Azurite connection details
# ---------------------------------------------------------------------------
_AZURITE_CONNECTION_STRING = (
    "DefaultEndpointsProtocol=http;"
    "AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    "QueueEndpoint=http://localhost:10001/devstoreaccount1;"
)


def _azurite_connection_string() -> str:
    """Return the Azurite queue connection string.

    Allows overriding the default localhost address via the
    AZURITE_QUEUE_URL environment variable.  When the env var is set to a
    full connection string it is returned as-is; when it is a bare host:port
    URL the default connection string is rewritten to use that endpoint.
    """
    override = os.environ.get("AZURITE_QUEUE_URL", "")
    if not override:
        return _AZURITE_CONNECTION_STRING

    # If the caller passed a full connection string (contains "AccountName=")
    if "AccountName=" in override:
        return override

    # Otherwise treat it as a base URL and rewrite the QueueEndpoint
    base_url = override.rstrip("/")
    return (
        "DefaultEndpointsProtocol=http;"
        "AccountName=devstoreaccount1;"
        "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
        f"QueueEndpoint={base_url}/devstoreaccount1;"
    )


def _azurite_reachable() -> bool:
    """Return True if the Azurite queue port is open and accepting connections."""
    override = os.environ.get("AZURITE_QUEUE_URL", "")
    if override and "AccountName=" in override:
        # Full connection string provided — try to parse host/port out of it
        for part in override.split(";"):
            if part.startswith("QueueEndpoint="):
                endpoint = part.split("=", 1)[1]
                # http://host:port/...
                host_port = endpoint.split("//")[-1].split("/")[0]
                host, _, port_str = host_port.partition(":")
                port = int(port_str) if port_str else 10001
                break
        else:
            host, port = "localhost", 10001
    else:
        host = "localhost"
        port = int((override.split(":")[-1].split("/")[0]) if override else "10001")

    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Session-scoped availability check  (evaluated once per test session)
# ---------------------------------------------------------------------------
_azurite_available = _azurite_reachable()

azurite_required = pytest.mark.skipif(
    not _azurite_available,
    reason="Azurite queue service is not reachable at the configured endpoint. "
    "Start it with `docker compose up -d azurite` or set AZURITE_QUEUE_URL.",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def connection_string() -> str:
    """Return the Azurite connection string for the current test run."""
    return _azurite_connection_string()


@pytest.fixture
def queue_name() -> str:
    """Return a unique queue name for each test to guarantee isolation."""
    return f"test-dicom-events-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def azurite_queue(connection_string: str, queue_name: str):
    """Create a fresh Azurite queue before the test and delete it afterwards.

    Yields the QueueClient so tests can inspect queue state directly.
    """
    client: QueueClient = QueueClient.from_connection_string(connection_string, queue_name)
    try:
        client.create_queue()
    except ResourceExistsError:
        pass  # queue already exists — fine for isolation by name

    yield client

    # Teardown: remove the queue regardless of test outcome
    try:
        client.delete_queue()
    except Exception:
        pass
    client.close()


def _make_event(seq: int = 1) -> DicomEvent:
    """Create a minimal DicomEvent for testing."""
    return DicomEvent.from_instance_created(
        study_uid="1.2.3.4.5",
        series_uid="1.2.3.4.5.6",
        instance_uid="1.2.3.4.5.6.7",
        sequence_number=seq,
        service_url="http://localhost:8080",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@azurite_required
def test_publish_single_event_appears_in_queue(
    connection_string: str,
    queue_name: str,
    azurite_queue: QueueClient,
):
    """publish() sends a JSON-encoded event that can be dequeued from Azurite."""
    import asyncio

    provider = AzureStorageQueueProvider(connection_string=connection_string, queue_name=queue_name)

    event = _make_event(seq=1)
    asyncio.get_event_loop().run_until_complete(provider.publish(event))

    # Retrieve messages from the real queue
    messages = list(azurite_queue.receive_messages(max_messages=10))

    assert len(messages) == 1, f"Expected 1 message in queue, got {len(messages)}"
    payload = json.loads(messages[0].content)
    assert payload["eventType"] == DicomEvent.EVENT_TYPE_CREATED
    assert payload["data"]["imageStudyInstanceUid"] == "1.2.3.4.5"
    assert payload["data"]["sequenceNumber"] == 1


@azurite_required
async def test_publish_async_single_event(
    connection_string: str,
    queue_name: str,
    azurite_queue: QueueClient,
):
    """publish() works in a native async context."""
    provider = AzureStorageQueueProvider(connection_string=connection_string, queue_name=queue_name)

    event = _make_event(seq=42)
    await provider.publish(event)

    messages = list(azurite_queue.receive_messages(max_messages=10))
    assert len(messages) == 1
    payload = json.loads(messages[0].content)
    assert payload["data"]["sequenceNumber"] == 42


@azurite_required
async def test_publish_batch_all_events_appear_in_queue(
    connection_string: str,
    queue_name: str,
    azurite_queue: QueueClient,
):
    """publish_batch() sends all events individually to the queue."""
    provider = AzureStorageQueueProvider(connection_string=connection_string, queue_name=queue_name)

    events = [_make_event(seq=i) for i in range(1, 4)]
    await provider.publish_batch(events)

    messages = list(azurite_queue.receive_messages(max_messages=32))
    assert len(messages) == 3

    sequence_numbers = sorted(json.loads(msg.content)["data"]["sequenceNumber"] for msg in messages)
    assert sequence_numbers == [1, 2, 3]


@azurite_required
async def test_publish_event_payload_is_valid_azure_event_grid_schema(
    connection_string: str,
    queue_name: str,
    azurite_queue: QueueClient,
):
    """The enqueued message conforms to the Azure Event Grid schema."""
    provider = AzureStorageQueueProvider(connection_string=connection_string, queue_name=queue_name)

    event = _make_event(seq=7)
    await provider.publish(event)

    messages = list(azurite_queue.receive_messages(max_messages=10))
    assert len(messages) == 1

    payload = json.loads(messages[0].content)

    # Required Azure Event Grid top-level fields
    required_fields = {"id", "eventType", "subject", "eventTime", "dataVersion", "data"}
    missing = required_fields - payload.keys()
    assert not missing, f"Event Grid schema fields missing: {missing}"

    # Required DICOM data fields
    data = payload["data"]
    required_data_fields = {
        "partitionName",
        "imageStudyInstanceUid",
        "imageSeriesInstanceUid",
        "imageSopInstanceUid",
        "serviceHostName",
        "sequenceNumber",
    }
    missing_data = required_data_fields - data.keys()
    assert not missing_data, f"DICOM data fields missing: {missing_data}"


@azurite_required
async def test_close_does_not_raise(
    connection_string: str,
    queue_name: str,
    azurite_queue: QueueClient,
):
    """close() can be called without errors after publish operations."""
    provider = AzureStorageQueueProvider(connection_string=connection_string, queue_name=queue_name)
    await provider.publish(_make_event())
    await provider.close()  # must not raise
