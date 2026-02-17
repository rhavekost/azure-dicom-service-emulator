"""Test event serialization edge cases."""

import json
import uuid
from datetime import datetime, timezone

import pytest

from app.models.events import DicomEvent

pytestmark = pytest.mark.unit


def test_event_to_dict_with_uuid_sequence_fails():
    """Event with UUID sequence_number should fail JSON serialization."""
    uuid_obj = uuid.uuid4()

    event = DicomEvent(
        id=str(uuid.uuid4()),
        event_type="Microsoft.HealthcareApis.DicomImageCreated",
        subject="/dicom/studies/1.2.3/series/4.5.6/instances/7.8.9",
        event_time=datetime.now(timezone.utc),
        data_version="1.0",
        metadata_version="1",
        topic="http://localhost:8080",
        data={
            "partitionName": "Microsoft.Default",
            "imageStudyInstanceUid": "1.2.3",
            "imageSeriesInstanceUid": "4.5.6",
            "imageSopInstanceUid": "7.8.9",
            "serviceHostName": "localhost:8080",
            "sequenceNumber": uuid_obj,  # UUID object (not serializable)
        },
    )

    # Attempt JSON serialization (simulating event provider behavior)
    with pytest.raises(TypeError, match="UUID.*not JSON serializable"):
        json.dumps(event.to_dict())


def test_event_to_dict_with_int_sequence_succeeds():
    """Event with integer sequence_number should serialize successfully."""
    event = DicomEvent(
        id=str(uuid.uuid4()),
        event_type="Microsoft.HealthcareApis.DicomImageCreated",
        subject="/dicom/studies/1.2.3/series/4.5.6/instances/7.8.9",
        event_time=datetime.now(timezone.utc),
        data_version="1.0",
        metadata_version="1",
        topic="http://localhost:8080",
        data={
            "partitionName": "Microsoft.Default",
            "imageStudyInstanceUid": "1.2.3",
            "imageSeriesInstanceUid": "4.5.6",
            "imageSopInstanceUid": "7.8.9",
            "serviceHostName": "localhost:8080",
            "sequenceNumber": 42,  # Integer (serializable)
        },
    )

    # Should serialize without error
    event_dict = event.to_dict()
    json_str = json.dumps(event_dict)

    # Verify deserialization
    parsed = json.loads(json_str)
    assert parsed["data"]["sequenceNumber"] == 42
