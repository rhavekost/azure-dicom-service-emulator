"""Event data models for Azure Event Grid compatible events."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass
class DicomEvent:
    """Azure Event Grid compatible DICOM event."""

    id: str
    event_type: str
    subject: str
    event_time: datetime
    data_version: str
    metadata_version: str
    topic: str
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Convert to Azure Event Grid JSON format."""
        return {
            "id": self.id,
            "eventType": self.event_type,
            "subject": self.subject,
            "eventTime": self.event_time.isoformat(),
            "dataVersion": self.data_version,
            "metadataVersion": self.metadata_version,
            "topic": self.topic,
            "data": self.data,
        }

    @classmethod
    def from_instance_created(
        cls,
        study_uid: str,
        series_uid: str,
        instance_uid: str,
        sequence_number: int,
        service_url: str,
    ) -> "DicomEvent":
        """Create DicomImageCreated event from instance data."""
        return cls(
            id=str(uuid.uuid4()),
            event_type="Microsoft.HealthcareApis.DicomImageCreated",
            subject=f"/dicom/studies/{study_uid}/series/{series_uid}/instances/{instance_uid}",
            event_time=datetime.now(timezone.utc),
            data_version="1.0",
            metadata_version="1",
            topic=service_url,
            data={
                "partitionName": "Microsoft.Default",
                "imageStudyInstanceUid": study_uid,
                "imageSeriesInstanceUid": series_uid,
                "imageSopInstanceUid": instance_uid,
                "serviceHostName": service_url.replace("http://", "").replace("https://", ""),
                "sequenceNumber": sequence_number,
            },
        )

    @classmethod
    def from_instance_deleted(
        cls,
        study_uid: str,
        series_uid: str,
        instance_uid: str,
        sequence_number: int,
        service_url: str,
    ) -> "DicomEvent":
        """Create DicomImageDeleted event from instance data."""
        return cls(
            id=str(uuid.uuid4()),
            event_type="Microsoft.HealthcareApis.DicomImageDeleted",
            subject=f"/dicom/studies/{study_uid}/series/{series_uid}/instances/{instance_uid}",
            event_time=datetime.now(timezone.utc),
            data_version="1.0",
            metadata_version="1",
            topic=service_url,
            data={
                "partitionName": "Microsoft.Default",
                "imageStudyInstanceUid": study_uid,
                "imageSeriesInstanceUid": series_uid,
                "imageSopInstanceUid": instance_uid,
                "serviceHostName": service_url.replace("http://", "").replace("https://", ""),
                "sequenceNumber": sequence_number,
            },
        )
