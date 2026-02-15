"""Event data models for Azure Event Grid compatible events."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse


@dataclass(frozen=True)
class DicomEvent:
    """Azure Event Grid compatible DICOM event."""

    EVENT_TYPE_CREATED = "Microsoft.HealthcareApis.DicomImageCreated"
    EVENT_TYPE_DELETED = "Microsoft.HealthcareApis.DicomImageDeleted"
    DATA_VERSION = "1.0"
    METADATA_VERSION = "1"
    DEFAULT_PARTITION = "Microsoft.Default"

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
    def _from_instance(
        cls,
        event_type: str,
        study_uid: str,
        series_uid: str,
        instance_uid: str,
        sequence_number: int,
        service_url: str,
    ) -> "DicomEvent":
        """Create event from instance data."""
        parsed_url = urlparse(service_url)
        service_hostname = parsed_url.netloc or parsed_url.path

        return cls(
            id=str(uuid.uuid4()),
            event_type=event_type,
            subject=f"/dicom/studies/{study_uid}/series/{series_uid}/instances/{instance_uid}",
            event_time=datetime.now(timezone.utc),
            data_version=cls.DATA_VERSION,
            metadata_version=cls.METADATA_VERSION,
            topic=service_url,
            data={
                "partitionName": cls.DEFAULT_PARTITION,
                "imageStudyInstanceUid": study_uid,
                "imageSeriesInstanceUid": series_uid,
                "imageSopInstanceUid": instance_uid,
                "serviceHostName": service_hostname,
                "sequenceNumber": sequence_number,
            },
        )

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
        return cls._from_instance(
            cls.EVENT_TYPE_CREATED,
            study_uid,
            series_uid,
            instance_uid,
            sequence_number,
            service_url,
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
        return cls._from_instance(
            cls.EVENT_TYPE_DELETED,
            study_uid,
            series_uid,
            instance_uid,
            sequence_number,
            service_url,
        )
