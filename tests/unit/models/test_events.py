"""Tests for event data models."""

from datetime import datetime, timezone
from uuid import UUID

import pytest

from app.models.events import DicomEvent

pytestmark = pytest.mark.unit


def test_dicom_event_creation():
    """Create a DicomEvent with all required fields."""
    event = DicomEvent(
        id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
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
            "sequenceNumber": 1,
        },
    )

    assert event.event_type == "Microsoft.HealthcareApis.DicomImageCreated"
    assert event.data["imageStudyInstanceUid"] == "1.2.3"
    # Verify ID is valid UUID
    UUID(event.id)


def test_dicom_event_to_dict():
    """Convert DicomEvent to Azure Event Grid format."""
    event = DicomEvent(
        id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        event_type="Microsoft.HealthcareApis.DicomImageCreated",
        subject="/dicom/studies/1.2.3",
        event_time=datetime(2026, 2, 15, 10, 30, 0, tzinfo=timezone.utc),
        data_version="1.0",
        metadata_version="1",
        topic="http://localhost:8080",
        data={"test": "value"},
    )

    result = event.to_dict()

    assert result["id"] == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    assert result["eventType"] == "Microsoft.HealthcareApis.DicomImageCreated"
    assert result["subject"] == "/dicom/studies/1.2.3"
    assert result["eventTime"] == "2026-02-15T10:30:00+00:00"
    assert result["dataVersion"] == "1.0"
    assert result["metadataVersion"] == "1"
    assert result["topic"] == "http://localhost:8080"
    assert result["data"] == {"test": "value"}


def test_dicom_event_from_instance():
    """Create DicomImageCreated event from instance data."""
    event = DicomEvent.from_instance_created(
        study_uid="1.2.3",
        series_uid="4.5.6",
        instance_uid="7.8.9",
        sequence_number=42,
        service_url="http://localhost:8080",
    )

    assert event.event_type == "Microsoft.HealthcareApis.DicomImageCreated"
    assert event.subject == "/dicom/studies/1.2.3/series/4.5.6/instances/7.8.9"
    assert event.data["imageStudyInstanceUid"] == "1.2.3"
    assert event.data["sequenceNumber"] == 42
    # Verify ID was auto-generated
    UUID(event.id)


def test_dicom_event_from_instance_deleted():
    """Create DicomImageDeleted event from instance data."""
    event = DicomEvent.from_instance_deleted(
        study_uid="1.2.3",
        series_uid="4.5.6",
        instance_uid="7.8.9",
        sequence_number=42,
        service_url="http://localhost:8080",
    )

    assert event.event_type == "Microsoft.HealthcareApis.DicomImageDeleted"
    assert event.subject == "/dicom/studies/1.2.3/series/4.5.6/instances/7.8.9"
    assert event.data["imageStudyInstanceUid"] == "1.2.3"
    assert event.data["sequenceNumber"] == 42
    UUID(event.id)
