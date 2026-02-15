# Azure DICOM Service 1-to-1 Parity Features - Design Document

**Date:** 2026-02-15
**Status:** Approved
**Goal:** Implement complete functional parity with Azure Health Data Services DICOM Service v2 API

## Overview

This design covers four major feature sets to achieve 1-to-1 parity with Azure DICOM Service:

1. **STOW-RS Enhancements** - PUT upsert, expiry headers, improved validation
2. **WADO-RS Additions** - Frame retrieval, rendered images, transcoding
3. **QIDO-RS Enhancements** - Fuzzy matching, wildcards, extended query tags
4. **UPS-RS Worklist Service** - Complete workitem lifecycle management
5. **Event Propagation System** - Azure Event Grid compatible events with provider pattern

## References

- [Azure DICOM Conformance Statement v2](https://learn.microsoft.com/en-us/azure/healthcare-apis/dicom/dicom-services-conformance-statement-v2)
- [Azure Event Grid DICOM Schema](https://learn.microsoft.com/en-us/azure/event-grid/event-schema-azure-health-data-services)
- [DICOMweb Standard PS3.18](https://dicom.nema.org/medical/dicom/current/output/html/part18.html)

---

## 1. Event Provider System Architecture

### 1.1 Overview

The event system publishes DICOM operation events using Azure Event Grid message format. A provider pattern allows multiple event destinations (Azure Storage Queues, webhooks, files) to operate simultaneously.

### 1.2 Provider Interface

```python
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

class EventProvider(ABC):
    """Base class for event providers."""

    @abstractmethod
    async def publish(self, event: DicomEvent) -> None:
        """Publish a single event."""

    @abstractmethod
    async def publish_batch(self, events: list[DicomEvent]) -> None:
        """Publish multiple events atomically."""

    async def health_check(self) -> bool:
        """Check if provider is healthy and reachable."""
        return True

    async def close(self) -> None:
        """Clean up resources."""
        pass
```

### 1.3 Event Schema (Azure Event Grid Compatible)

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Any

@dataclass
class DicomEvent:
    """Azure Event Grid compatible DICOM event."""

    id: str  # UUID v4
    event_type: str  # e.g., "Microsoft.HealthcareApis.DicomImageCreated"
    subject: str  # Resource path: "/dicom/studies/{study}/series/{series}/instances/{instance}"
    event_time: datetime  # ISO 8601 UTC
    data_version: str  # "1.0"
    metadata_version: str  # "1"
    data: dict[str, Any]  # Event-specific payload
    topic: str  # Service URL
```

### 1.4 Built-in Providers

**Azure Storage Queue Provider:**
```python
class AzureStorageQueueProvider(EventProvider):
    """Publishes events to Azure Storage Queue (Azurite compatible)."""

    def __init__(self, connection_string: str, queue_name: str):
        self.queue_client = QueueClient.from_connection_string(
            connection_string, queue_name
        )
```

**Webhook Provider:**
```python
class WebhookProvider(EventProvider):
    """Publishes events via HTTP POST with retry logic."""

    def __init__(self, url: str, retry_attempts: int = 3):
        self.url = url
        self.retry_attempts = retry_attempts
```

**File Provider:**
```python
class FileProvider(EventProvider):
    """Appends events to JSON Lines file for debugging."""

    def __init__(self, file_path: str):
        self.file_path = file_path
```

**In-Memory Provider:**
```python
class InMemoryProvider(EventProvider):
    """Stores events in memory for testing. Queryable via /debug/events."""

    def __init__(self):
        self.events: list[DicomEvent] = []
```

### 1.5 Configuration

Providers load from environment variable `EVENT_PROVIDERS` (JSON array):

```json
[
  {
    "type": "azure_storage_queue",
    "connection_string": "UseDevelopmentStorage=true",
    "queue_name": "dicom-events",
    "enabled": true
  },
  {
    "type": "webhook",
    "url": "https://webhook.site/xxx-yyy-zzz",
    "retry_attempts": 3,
    "enabled": false
  },
  {
    "type": "file",
    "path": "/data/events.jsonl",
    "enabled": true
  }
]
```

Or via YAML config file:

```yaml
event_providers:
  - type: azure_storage_queue
    connection_string: "UseDevelopmentStorage=true"
    queue_name: dicom-events
    enabled: true
  - type: webhook
    url: https://webhook.site/xxx
    retry_attempts: 3
    enabled: false
```

### 1.6 Event Manager

```python
class EventManager:
    """Manages event publishing to multiple providers."""

    def __init__(self, providers: list[EventProvider]):
        self.providers = providers

    async def publish(self, event: DicomEvent) -> None:
        """Publish to all enabled providers (best-effort, non-blocking)."""
        failed_providers = []

        for provider in self.providers:
            try:
                await asyncio.wait_for(
                    provider.publish(event),
                    timeout=5.0
                )
            except Exception as e:
                logger.error(f"Provider {provider.__class__.__name__} failed: {e}")
                failed_providers.append(provider.__class__.__name__)

        if failed_providers:
            logger.warning(f"Event publish failed for providers: {failed_providers}")
            # Don't fail the DICOM operation
```

---

## 2. DICOM Event Types & Payloads

### 2.1 DicomImageCreated

**Triggered:** After successful STOW-RS instance storage
**Event Type:** `Microsoft.HealthcareApis.DicomImageCreated`

```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "eventType": "Microsoft.HealthcareApis.DicomImageCreated",
  "subject": "/dicom/studies/1.2.3.4/series/5.6.7.8/instances/9.10.11.12",
  "eventTime": "2026-02-15T10:30:00.000Z",
  "dataVersion": "1.0",
  "metadataVersion": "1",
  "topic": "http://localhost:8080",
  "data": {
    "partitionName": "Microsoft.Default",
    "imageStudyInstanceUid": "1.2.3.4",
    "imageSeriesInstanceUid": "5.6.7.8",
    "imageSopInstanceUid": "9.10.11.12",
    "serviceHostName": "localhost:8080",
    "sequenceNumber": 1234
  }
}
```

### 2.2 DicomImageDeleted

**Triggered:** After DELETE operation commits
**Event Type:** `Microsoft.HealthcareApis.DicomImageDeleted`

```json
{
  "eventType": "Microsoft.HealthcareApis.DicomImageDeleted",
  "data": {
    "partitionName": "Microsoft.Default",
    "imageStudyInstanceUid": "1.2.3.4",
    "imageSeriesInstanceUid": "5.6.7.8",
    "imageSopInstanceUid": "9.10.11.12",
    "serviceHostName": "localhost:8080",
    "sequenceNumber": 1235
  }
}
```

### 2.3 DicomImageUpdated

**Triggered:** After bulk update metadata operation
**Event Type:** `Microsoft.HealthcareApis.DicomImageUpdated`

```json
{
  "eventType": "Microsoft.HealthcareApis.DicomImageUpdated",
  "subject": "/dicom/studies/1.2.3.4",
  "data": {
    "partitionName": "Microsoft.Default",
    "imageStudyInstanceUid": "1.2.3.4",
    "updatedAttributes": ["PatientName", "StudyDescription"],
    "sequenceNumber": 1236
  }
}
```

### 2.4 Event Correlation with Change Feed

Events include `sequenceNumber` that matches the change feed sequence. This allows consumers to:
- Correlate real-time events with change feed entries
- Use change feed for catch-up if events are missed
- Verify event delivery completeness

---

## 3. WADO-RS Frame Retrieval

### 3.1 New Endpoints

```
GET /v2/studies/{study}/series/{series}/instances/{instance}/frames/{frames}
```

**Supported Frame Specifications:**
- Single frame: `/frames/1`
- Multiple frames: `/frames/1,3,5,7`
- Frame indexing starts at 1 (per DICOM standard)

### 3.2 Accept Headers

```
multipart/related; type="application/octet-stream"; transfer-syntax=*
multipart/related; type="application/octet-stream"; transfer-syntax=1.2.840.10008.1.2.1
multipart/related; type="image/jp2"; transfer-syntax=1.2.840.10008.1.2.4.90
application/octet-stream; transfer-syntax=*  (single frame only)
```

### 3.3 Frame Storage Strategy

**Lazy Extraction:**
- Frames are NOT extracted during STOW-RS
- Extract on first retrieval request
- Cache extracted frames for subsequent requests

**Storage Layout:**
```
{DICOM_STORAGE_DIR}/
  {study_uid}/
    {series_uid}/
      {instance_uid}/
        instance.dcm          # Original DICOM file
        frames/
          1.raw               # Frame 1 pixel data
          2.raw               # Frame 2 pixel data
          metadata.json       # Frame count, dimensions, VR
```

**Extraction Logic:**
```python
def extract_frames(dcm_path: Path) -> list[bytes]:
    """Extract all frames from DICOM instance."""
    ds = pydicom.dcmread(dcm_path)

    if not hasattr(ds, 'pixel_array'):
        raise ValueError("No pixel data")

    pixel_array = ds.pixel_array

    # Multi-frame image
    if len(pixel_array.shape) == 3:
        frames = []
        for i in range(pixel_array.shape[0]):
            frames.append(pixel_array[i].tobytes())
        return frames

    # Single frame
    return [pixel_array.tobytes()]
```

### 3.4 Transfer Syntax Transcoding

**Supported Source Transfer Syntaxes:**
- 1.2.840.10008.1.2 (Little Endian Implicit)
- 1.2.840.10008.1.2.1 (Little Endian Explicit)
- 1.2.840.10008.1.2.2 (Explicit VR Big Endian)
- 1.2.840.10008.1.2.4.50 (JPEG Baseline)
- 1.2.840.10008.1.2.4.57 (JPEG Lossless)
- 1.2.840.10008.1.2.4.70 (JPEG Lossless SV1)
- 1.2.840.10008.1.2.4.90 (JPEG 2000 Lossless)
- 1.2.840.10008.1.2.4.91 (JPEG 2000)
- 1.2.840.10008.1.2.5 (RLE Lossless)

**Transcoding Strategy:**
- If requested transfer syntax matches stored → return as-is
- If different → decode to pixel array → re-encode
- Use pydicom + pillow-jpls for JPEG-LS support
- Return 406 Not Acceptable if transcoding fails

---

## 4. WADO-RS Rendered Images

### 4.1 New Endpoints

```
GET /v2/studies/{study}/series/{series}/instances/{instance}/rendered
GET /v2/studies/{study}/series/{series}/instances/{instance}/frames/{frame}/rendered
```

### 4.2 Accept Headers & Defaults

```
Accept: image/jpeg  (default)
Accept: image/png
```

If no `Accept` header specified, render as JPEG.

### 4.3 Query Parameters

```
quality=1-100  (JPEG only, default: 100)
```

Quality parameter ignored for PNG rendering.

### 4.4 Rendering Pipeline

```python
async def render_frame(
    instance_path: Path,
    frame_number: int,
    format: str,
    quality: int = 100
) -> bytes:
    """Render DICOM frame to image format."""

    # Load DICOM
    ds = pydicom.dcmread(instance_path)
    pixel_array = ds.pixel_array

    # Extract specific frame if multi-frame
    if len(pixel_array.shape) == 3:
        if frame_number > pixel_array.shape[0]:
            raise ValueError(f"Frame {frame_number} out of range")
        frame_data = pixel_array[frame_number - 1]
    else:
        if frame_number != 1:
            raise ValueError("Single-frame instance")
        frame_data = pixel_array

    # Convert to PIL Image
    image = Image.fromarray(frame_data)

    # Apply windowing if present
    if hasattr(ds, 'WindowCenter') and hasattr(ds, 'WindowWidth'):
        image = apply_windowing(image, ds.WindowCenter, ds.WindowWidth)

    # Render to bytes
    buffer = io.BytesIO()
    if format == 'jpeg':
        image.save(buffer, format='JPEG', quality=quality)
    else:
        image.save(buffer, format='PNG')

    return buffer.getvalue()
```

### 4.5 Windowing Support

Apply DICOM windowing (Window Center/Width) for proper grayscale display:

```python
def apply_windowing(image: Image, center: float, width: float) -> Image:
    """Apply DICOM windowing to image."""
    arr = np.array(image, dtype=np.float32)

    lower = center - width / 2
    upper = center + width / 2

    arr = np.clip(arr, lower, upper)
    arr = ((arr - lower) / width * 255).astype(np.uint8)

    return Image.fromarray(arr)
```

---

## 5. STOW-RS Enhancements

### 5.1 PUT Method (Upsert)

**New Endpoints:**
```
PUT /v2/studies
PUT /v2/studies/{study}
```

**Behavior:**
- If instance exists (same StudyInstanceUID, SeriesInstanceUID, SOPInstanceUID) → replace it
- If instance doesn't exist → create it
- No 45070 warning code (duplicate instance) on PUT
- Replace entire instance, not merge attributes

**Implementation:**
```python
async def upsert_instance(study_uid: str, series_uid: str, sop_uid: str, data: bytes):
    """Upsert DICOM instance (PUT behavior)."""

    # Check if instance exists
    existing = await db.execute(
        select(DicomInstance).where(
            DicomInstance.study_instance_uid == study_uid,
            DicomInstance.series_instance_uid == series_uid,
            DicomInstance.sop_instance_uid == sop_uid
        )
    )

    if existing.scalar_one_or_none():
        # Delete old instance and files
        await delete_instance(study_uid, series_uid, sop_uid)

    # Store new instance
    await store_instance(study_uid, series_uid, sop_uid, data)
```

### 5.2 Expiry Headers

**Headers:**
```
msdicom-expiry-time-milliseconds: 86400000  # 24 hours in ms
msdicom-expiry-option: RelativeToNow        # Currently only supported option
msdicom-expiry-level: Study                 # Currently only supported level
```

**Behavior:**
- Calculate expiry time: `now() + expiry_milliseconds`
- Store in `DicomStudy.expires_at` column
- Background task checks every hour for expired studies
- Delete expired studies automatically
- If multiple instances stored with different expiry → last STOW wins

**Database Schema Addition:**
```python
class DicomStudy(Base):
    # ... existing fields ...
    expires_at: datetime | None  # NULL if no expiry set
```

**Background Task:**
```python
async def delete_expired_studies():
    """Scheduled task: delete expired studies."""
    while True:
        await asyncio.sleep(3600)  # Run every hour

        expired = await db.execute(
            select(DicomStudy).where(
                DicomStudy.expires_at.isnot(None),
                DicomStudy.expires_at <= datetime.now(timezone.utc)
            )
        )

        for study in expired.scalars().all():
            logger.info(f"Deleting expired study: {study.study_instance_uid}")
            await delete_study(study.study_instance_uid)
```

---

## 6. QIDO-RS Search Enhancements

### 6.1 Fuzzy Matching (Person Names)

**Query Parameter:**
```
GET /v2/studies?PatientName=Jon&fuzzymatching=true
```

**Algorithm:**
- Prefix word matching on any name component (family name, given name, etc.)
- DICOM PN format: `FamilyName^GivenName^MiddleName^Prefix^Suffix`

**Examples:**
- "John^Doe" matches: "joh", "do", "jo do", "Doe", "John Doe"
- "John^Doe" does NOT match: "ohn" (not a prefix)

**SQL Implementation:**
```sql
-- For search term "jon do"
WHERE
    patient_name ILIKE 'jon%'
    OR patient_name ILIKE '%^jon%'
    OR patient_name ILIKE 'do%'
    OR patient_name ILIKE '%^do%'
```

**Python Logic:**
```python
def build_fuzzy_name_filter(name_value: str, column: Column) -> BooleanClauseList:
    """Build SQL filter for fuzzy person name matching."""
    terms = name_value.lower().split()

    conditions = []
    for term in terms:
        conditions.extend([
            column.ilike(f"{term}%"),           # Starts with term
            column.ilike(f"%^{term}%"),         # Term after component separator
        ])

    return or_(*conditions)
```

**Applies to:** `PatientName`, `ReferringPhysicianName`

### 6.2 Wildcard Matching

**Wildcard Characters:**
- `*` → Zero or more characters
- `?` → Exactly one character

**Examples:**
```
PatientID=PAT*           → PAT123, PATIENT, PAT
StudyDescription=CT?Head → CT-Head, CT_Head, CT1Head
AccessionNumber=2024*    → 2024-001, 20241231-99
```

**SQL Translation:**
```python
def translate_wildcards(value: str) -> str:
    """Translate DICOM wildcards to SQL wildcards."""
    # Escape existing SQL wildcards
    value = value.replace('%', r'\%').replace('_', r'\_')

    # Translate DICOM wildcards
    value = value.replace('*', '%')
    value = value.replace('?', '_')

    return value
```

**SQL Query:**
```sql
WHERE patient_id LIKE 'PAT%'
WHERE study_description LIKE 'CT_Head'
```

### 6.3 UID List Matching

**Comma or Backslash Separated:**
```
GET /v2/studies?StudyInstanceUID=1.2.3,4.5.6,7.8.9
GET /v2/studies?StudyInstanceUID=1.2.3\4.5.6\7.8.9
```

**Parsing Logic:**
```python
def parse_uid_list(uid_param: str) -> list[str]:
    """Parse comma or backslash separated UID list."""
    # Replace backslashes with commas
    normalized = uid_param.replace('\\', ',')

    # Split and strip
    uids = [uid.strip() for uid in normalized.split(',')]

    return uids
```

**SQL Query:**
```sql
WHERE study_instance_uid IN ('1.2.3', '4.5.6', '7.8.9')
```

### 6.4 Extended Query Tag Support

**Query on Custom Tags:**
```
GET /v2/studies?00101010=025Y  # PatientAge (if registered as extended tag)
```

**Database Schema:**
```python
class ExtendedQueryTagValue(Base):
    """Values for extended query tags."""
    tag_id: int  # FK to ExtendedQueryTag
    study_instance_uid: str
    series_instance_uid: str | None
    sop_instance_uid: str | None
    tag_value: str

    __table_args__ = (
        Index('ix_tag_value', 'tag_id', 'tag_value'),
    )
```

**Query Join:**
```python
# For query: ?00101010=025Y
tag = await get_extended_query_tag("00101010")

query = (
    select(DicomInstance)
    .join(ExtendedQueryTagValue,
          ExtendedQueryTagValue.sop_instance_uid == DicomInstance.sop_instance_uid)
    .where(
        ExtendedQueryTagValue.tag_id == tag.id,
        ExtendedQueryTagValue.tag_value == "025Y"
    )
)
```

### 6.5 Aggregated Attributes

**Study Level:**
```
GET /v2/studies?includefield=NumberOfStudyRelatedInstances
```

**SQL:**
```sql
SELECT
    studies.*,
    COUNT(instances.sop_instance_uid) as NumberOfStudyRelatedInstances
FROM studies
LEFT JOIN instances ON instances.study_instance_uid = studies.study_instance_uid
GROUP BY studies.study_instance_uid
```

**Series Level:**
```
GET /v2/studies/{study}/series?includefield=NumberOfSeriesRelatedInstances
```

**Implementation:**
```python
if "NumberOfStudyRelatedInstances" in include_fields:
    query = query.add_columns(
        func.count(DicomInstance.sop_instance_uid).label('instance_count')
    ).group_by(DicomStudy.study_instance_uid)
```

---

## 7. UPS-RS Worklist Service

### 7.1 Database Schema

```python
class Workitem(Base):
    """UPS Workitem."""
    __tablename__ = 'workitems'

    # Primary identifier
    sop_instance_uid: Mapped[str] = mapped_column(String(64), primary_key=True)

    # Ownership and state
    transaction_uid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    procedure_step_state: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="SCHEDULED"
    )  # SCHEDULED, IN PROGRESS, COMPLETED, CANCELED

    # Patient information (for search)
    patient_name: Mapped[str | None] = mapped_column(String(255))
    patient_id: Mapped[str | None] = mapped_column(String(64))

    # Study reference
    study_instance_uid: Mapped[str | None] = mapped_column(String(64))

    # Scheduling
    scheduled_procedure_step_start_datetime: Mapped[datetime | None]

    # Request references (for search)
    accession_number: Mapped[str | None] = mapped_column(String(64))
    requested_procedure_id: Mapped[str | None] = mapped_column(String(64))

    # Station codes (for search)
    scheduled_station_name_code: Mapped[str | None] = mapped_column(String(64))
    scheduled_station_class_code: Mapped[str | None] = mapped_column(String(64))
    scheduled_station_geo_code: Mapped[str | None] = mapped_column(String(64))

    # Full DICOM dataset
    dicom_dataset: Mapped[dict] = mapped_column(JSON, nullable=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index('ix_workitem_patient_id', 'patient_id'),
        Index('ix_workitem_study_uid', 'study_instance_uid'),
        Index('ix_workitem_state', 'procedure_step_state'),
        Index('ix_workitem_scheduled_date', 'scheduled_procedure_step_start_datetime'),
    )
```

### 7.2 State Machine

```
┌───────────┐
│ SCHEDULED │
└─────┬─────┘
      │ claim (set Transaction UID)
      ▼
┌─────────────┐
│ IN PROGRESS │
└──┬────────┬─┘
   │        │
   │        │ cancel (with Transaction UID)
   │        ▼
   │   ┌───────────┐
   │   │ CANCELED  │
   │   └───────────┘
   │
   │ complete (with Transaction UID)
   ▼
┌───────────┐
│ COMPLETED │
└───────────┘

Cancel from SCHEDULED (no Transaction UID):
┌───────────┐
│ SCHEDULED │───────────────────────────┐
└───────────┘  request_cancel           │
                                         ▼
                                    ┌───────────┐
                                    │ CANCELED  │
                                    └───────────┘
```

**State Transition Rules:**

| From State | To State | Required | Validation |
|------------|----------|----------|------------|
| SCHEDULED | IN PROGRESS | No Transaction UID | Set new Transaction UID |
| IN PROGRESS | COMPLETED | Correct Transaction UID | Must match stored |
| IN PROGRESS | CANCELED | Correct Transaction UID | Must match stored |
| SCHEDULED | CANCELED | No Transaction UID | Via cancel request only |

### 7.3 API Endpoints

#### Create Workitem

```
POST /v2/workitems
POST /v2/workitems?{workitem_uid}
```

**Request:**
```json
{
  "00080005": {"vr": "CS", "Value": ["ISO_IR 100"]},
  "00080018": {"vr": "UI", "Value": ["1.2.3.4.5"]},
  "00100010": {"vr": "PN", "Value": [{"Alphabetic": "Doe^John"}]},
  "00100020": {"vr": "LO", "Value": ["PAT123"]},
  "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
  "00404005": {"vr": "DT", "Value": ["20260215103000"]},
  "0020000D": {"vr": "UI", "Value": ["1.2.3.4"]}
}
```

**Response:**
- 201 Created
- Location header: `/v2/workitems/{workitem_uid}`

**Validation:**
- `SOPInstanceUID` (0008,0018) required (in dataset or query param)
- `ProcedureStepState` (0074,1000) must be "SCHEDULED"
- `TransactionUID` (0008,1195) must NOT be present

#### Retrieve Workitem

```
GET /v2/workitems/{workitem_uid}
```

**Response:**
```json
{
  "00080018": {"vr": "UI", "Value": ["1.2.3.4.5"]},
  "00100010": {"vr": "PN", "Value": [{"Alphabetic": "Doe^John"}]},
  "00741000": {"vr": "CS", "Value": ["SCHEDULED"]}
  // ... (Transaction UID omitted)
}
```

**Note:** Response NEVER includes Transaction UID (0008,1195) - preserves security.

#### Update Workitem

```
POST /v2/workitems/{workitem_uid}?{transaction_uid}
```

**For SCHEDULED workitems:**
- No `transaction_uid` query param required
- Any user can update

**For IN PROGRESS workitems:**
- `transaction_uid` query param REQUIRED
- Must match stored Transaction UID
- Returns 400 if missing or incorrect

**Request Body:**
```json
{
  "00100010": {"vr": "PN", "Value": [{"Alphabetic": "Smith^Jane"}]},
  "00081030": {"vr": "LO", "Value": ["Updated Study Description"]}
}
```

**Validation:**
- Cannot update `ProcedureStepState` (use Change State endpoint)
- Cannot update if state is COMPLETED or CANCELED
- When updating sequence, must include ALL items (not just changed)

#### Change Workitem State

```
PUT /v2/workitems/{workitem_uid}/state
```

**Request Body:**
```json
{
  "00081195": {"vr": "UI", "Value": ["1.2.3.4.567"]},
  "00741000": {"vr": "CS", "Value": ["IN PROGRESS"]}
}
```

**State Transitions:**

**SCHEDULED → IN PROGRESS (Claim):**
```json
{
  "00081195": {"vr": "UI", "Value": ["<new-transaction-uid>"]},
  "00741000": {"vr": "CS", "Value": ["IN PROGRESS"]}
}
```
- Store Transaction UID
- Return 200 OK

**IN PROGRESS → COMPLETED:**
```json
{
  "00081195": {"vr": "UI", "Value": ["<existing-transaction-uid>"]},
  "00741000": {"vr": "CS", "Value": ["COMPLETED"]}
}
```
- Validate Transaction UID matches
- Return 400 if mismatch
- Return 200 OK if success

**IN PROGRESS → CANCELED:**
```json
{
  "00081195": {"vr": "UI", "Value": ["<existing-transaction-uid>"]},
  "00741000": {"vr": "CS", "Value": ["CANCELED"]}
}
```
- Validate Transaction UID matches
- Return 200 OK

#### Request Cancellation

```
POST /v2/workitems/{workitem_uid}/cancelrequest
```

**Only for SCHEDULED workitems.**

**Request Body (optional):**
```json
{
  "00404025": {"vr": "SQ", "Value": [{
    "00080104": {"vr": "LO", "Value": ["User requested cancellation"]}
  }]}
}
```

**Behavior:**
- If state is SCHEDULED → set to CANCELED, return 202 Accepted
- If state is IN PROGRESS → return 409 Conflict (owner must cancel)
- If state is COMPLETED/CANCELED → return 409 Conflict with warning

**Warning Header:**
```
299: The UPS is already in the requested state of CANCELED.
```

#### Search Workitems

```
GET /v2/workitems?{query_params}
```

**Searchable Attributes:**

| Attribute | Tag | Search Types |
|-----------|-----|--------------|
| PatientName | 0010,0010 | Exact, Fuzzy, Wildcard |
| PatientID | 0010,0020 | Exact, Wildcard |
| StudyInstanceUID | 0020,000D | Exact, UID List |
| ProcedureStepState | 0074,1000 | Exact, Wildcard |
| ScheduledProcedureStepStartDateTime | 0040,4005 | Exact, Range |
| AccessionNumber | 0040,0009 (in ReferencedRequestSequence) | Exact, Wildcard |
| RequestedProcedureID | 0040,1001 (in ReferencedRequestSequence) | Exact, Wildcard |
| ScheduledStationNameCode | 0040,4025 (CodeValue in sequence) | Exact, Wildcard |
| ScheduledStationClassCode | 0040,4026 (CodeValue in sequence) | Exact, Wildcard |
| ScheduledStationGeoCode | 0040,4027 (CodeValue in sequence) | Exact, Wildcard |

**Query Parameters:**
- `limit` (default: 100, max: 4000)
- `offset` (default: 0)
- `fuzzymatching` (default: false)
- `includefield=all` or specific tags

**Example:**
```
GET /v2/workitems?PatientID=PAT*&ProcedureStepState=SCHEDULED&limit=50
```

**Response:**
```json
[
  {
    "00080018": {"vr": "UI", "Value": ["1.2.3.4.5"]},
    "00100010": {"vr": "PN", "Value": [{"Alphabetic": "Doe^John"}]},
    "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
    "00404005": {"vr": "DT", "Value": ["20260215103000"]}
  }
]
```

### 7.4 Subscription Management (Future)

**Note:** Azure supports UPS-RS subscriptions for real-time workitem notifications. We'll implement the endpoints but return "Not Implemented" for MVP:

```
POST   /v2/workitems/{workitem}/subscribers/{aetitle}
DELETE /v2/workitems/{workitem}/subscribers/{aetitle}
GET    /v2/subscribers/{aetitle}
```

All return: `501 Not Implemented` with message directing users to polling or change feed.

---

## 8. Error Handling & Reliability

### 8.1 Event Publishing Failures

**Failure Modes:**
- Azure Storage Queue unavailable (Azurite down)
- Webhook endpoint unreachable
- Network timeout
- Serialization error

**Strategy:**
```python
class EventManager:
    async def publish(self, event: DicomEvent) -> None:
        """Best-effort, non-blocking event publishing."""
        failed_providers = []

        for provider in self.providers:
            try:
                await asyncio.wait_for(
                    provider.publish(event),
                    timeout=5.0
                )
            except asyncio.TimeoutError:
                logger.error(f"Provider {provider.name} timed out")
                failed_providers.append(provider.name)
            except Exception as e:
                logger.error(f"Provider {provider.name} failed: {e}")
                failed_providers.append(provider.name)

        # Log failures but don't fail DICOM operation
        if failed_providers:
            logger.warning(f"Event publish failed: {failed_providers}")
```

**Key Principle:** DICOM operations succeed even if event publishing fails. Events are best-effort.

### 8.2 Webhook Retry Logic

```python
from tenacity import retry, stop_after_attempt, wait_exponential

class WebhookProvider(EventProvider):
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, asyncio.TimeoutError))
    )
    async def _send_webhook(self, event: DicomEvent) -> None:
        """Send webhook with exponential backoff retry."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.url,
                json=event.to_dict(),
                timeout=5.0
            )
            response.raise_for_status()
```

**Retry Schedule:**
- Attempt 1: Immediate
- Attempt 2: Wait 1s
- Attempt 3: Wait 2s
- Attempt 4: Wait 4s (if max_attempts increased)

### 8.3 Frame Extraction Errors

**Error Cases:**

| Scenario | HTTP Code | Message |
|----------|-----------|---------|
| No pixel data in instance | 404 Not Found | "Instance does not contain pixel data" |
| Frame number out of range | 404 Not Found | "Frame 5 out of range (instance has 3 frames)" |
| Unsupported transfer syntax | 406 Not Acceptable | "Transfer syntax 1.2.3.4 not supported for transcoding" |
| Corrupt pixel data | 500 Internal Server Error | "Failed to decode pixel data" |

**Caching Failures:**
```python
# Store failed extraction attempts to avoid retries
class FrameExtractionCache:
    def __init__(self):
        self.failures: dict[str, datetime] = {}

    def mark_failed(self, instance_uid: str):
        """Mark instance as having failed extraction."""
        self.failures[instance_uid] = datetime.now(timezone.utc)

    def is_failed(self, instance_uid: str) -> bool:
        """Check if instance failed extraction in last hour."""
        if instance_uid not in self.failures:
            return False

        failed_at = self.failures[instance_uid]
        age = datetime.now(timezone.utc) - failed_at

        return age.total_seconds() < 3600
```

### 8.4 UPS-RS State Conflicts

**Error Scenarios:**

| Error | HTTP Code | When |
|-------|-----------|------|
| Missing Transaction UID for IN PROGRESS | 400 Bad Request | Update/State change without `?transaction_uid` |
| Wrong Transaction UID | 400 Bad Request | Transaction UID doesn't match stored |
| Invalid state transition | 409 Conflict | e.g., COMPLETED → IN PROGRESS |
| Workitem already in target state | 409 Conflict | e.g., cancel already-canceled |

**Response Examples:**

**400 Bad Request (Missing Transaction UID):**
```json
{
  "error": "BadRequest",
  "message": "Transaction UID required for workitem in IN PROGRESS state"
}
```

**400 Bad Request (Wrong Transaction UID):**
```json
{
  "error": "BadRequest",
  "message": "Transaction UID does not match. Workitem is owned by another process."
}
```

**409 Conflict (Invalid Transition):**
```json
{
  "error": "Conflict",
  "message": "Cannot transition from COMPLETED to IN PROGRESS"
}
```

---

## 9. Testing Strategy

### 9.1 Unit Tests (pytest)

**Event System Tests:**
```python
async def test_event_manager_publishes_to_all_providers():
    """Event manager calls publish on all providers."""
    mock_provider_1 = AsyncMock(spec=EventProvider)
    mock_provider_2 = AsyncMock(spec=EventProvider)

    manager = EventManager([mock_provider_1, mock_provider_2])
    event = DicomEvent(...)

    await manager.publish(event)

    mock_provider_1.publish.assert_awaited_once_with(event)
    mock_provider_2.publish.assert_awaited_once_with(event)

async def test_event_manager_continues_on_provider_failure():
    """Event manager doesn't fail if one provider fails."""
    failing_provider = AsyncMock(spec=EventProvider)
    failing_provider.publish.side_effect = Exception("Network error")

    working_provider = AsyncMock(spec=EventProvider)

    manager = EventManager([failing_provider, working_provider])
    event = DicomEvent(...)

    # Should not raise
    await manager.publish(event)

    # Working provider still called
    working_provider.publish.assert_awaited_once()
```

**Frame Extraction Tests:**
```python
def test_extract_frames_single_frame():
    """Extract single frame from instance."""
    dcm_path = Path("tests/data/single_frame.dcm")
    frames = extract_frames(dcm_path)

    assert len(frames) == 1
    assert isinstance(frames[0], bytes)

def test_extract_frames_multi_frame():
    """Extract multiple frames from instance."""
    dcm_path = Path("tests/data/multi_frame.dcm")
    frames = extract_frames(dcm_path)

    assert len(frames) == 5
    for frame in frames:
        assert isinstance(frame, bytes)

def test_extract_frames_no_pixel_data():
    """Raise error if instance has no pixel data."""
    dcm_path = Path("tests/data/no_pixels.dcm")

    with pytest.raises(ValueError, match="No pixel data"):
        extract_frames(dcm_path)
```

**Search Enhancement Tests:**
```python
def test_fuzzy_matching_person_name():
    """Fuzzy match finds names by prefix."""
    # Setup: Create patients "John^Doe", "Jane^Smith"

    # Search "joh" should match "John^Doe"
    results = await search_studies({"PatientName": "joh", "fuzzymatching": "true"})
    assert len(results) == 1
    assert "John" in results[0]["00100010"]["Value"][0]["Alphabetic"]

    # Search "smi" should match "Jane^Smith"
    results = await search_studies({"PatientName": "smi", "fuzzymatching": "true"})
    assert len(results) == 1
    assert "Smith" in results[0]["00100010"]["Value"][0]["Alphabetic"]

def test_wildcard_matching():
    """Wildcard search matches patterns."""
    # Setup: Create patients PAT123, PAT456, OTHER789

    results = await search_studies({"PatientID": "PAT*"})
    assert len(results) == 2

    results = await search_studies({"PatientID": "PAT???"})
    assert len(results) == 2

    results = await search_studies({"PatientID": "PAT?23"})
    assert len(results) == 1

def test_uid_list_matching():
    """UID list search finds multiple studies."""
    # Setup: Create studies 1.2.3, 4.5.6, 7.8.9

    results = await search_studies({
        "StudyInstanceUID": "1.2.3,4.5.6"
    })
    assert len(results) == 2

    # Backslash separator
    results = await search_studies({
        "StudyInstanceUID": "1.2.3\\4.5.6"
    })
    assert len(results) == 2
```

**UPS-RS State Machine Tests:**
```python
async def test_workitem_claim():
    """Claiming workitem sets transaction UID and state."""
    workitem = await create_workitem(state="SCHEDULED")

    txn_uid = str(uuid.uuid4())
    await change_state(workitem.uid, txn_uid, "IN PROGRESS")

    updated = await get_workitem(workitem.uid)
    assert updated.procedure_step_state == "IN PROGRESS"
    assert updated.transaction_uid == txn_uid

async def test_workitem_update_requires_transaction_uid():
    """Updating IN PROGRESS workitem requires transaction UID."""
    workitem = await create_workitem(state="IN PROGRESS", txn_uid="1.2.3")

    # Without transaction UID → 400
    with pytest.raises(HTTPException) as exc:
        await update_workitem(workitem.uid, {"PatientName": "New"}, None)
    assert exc.value.status_code == 400

    # With wrong transaction UID → 400
    with pytest.raises(HTTPException) as exc:
        await update_workitem(workitem.uid, {"PatientName": "New"}, "wrong-uid")
    assert exc.value.status_code == 400

    # With correct transaction UID → success
    await update_workitem(workitem.uid, {"PatientName": "New"}, "1.2.3")

async def test_workitem_cancel_from_scheduled():
    """Cancel request works on SCHEDULED workitem."""
    workitem = await create_workitem(state="SCHEDULED")

    response = await request_cancel(workitem.uid)
    assert response.status_code == 202

    updated = await get_workitem(workitem.uid)
    assert updated.procedure_step_state == "CANCELED"

async def test_workitem_cancel_from_in_progress_fails():
    """Cancel request fails on IN PROGRESS workitem."""
    workitem = await create_workitem(state="IN PROGRESS", txn_uid="1.2.3")

    response = await request_cancel(workitem.uid)
    assert response.status_code == 409
```

### 9.2 Integration Tests

**Event Publishing to Azurite:**
```python
async def test_events_published_to_azurite_queue():
    """STOW-RS publishes DicomImageCreated to Azurite queue."""
    # Setup Azurite connection
    queue_client = QueueClient.from_connection_string(
        "UseDevelopmentStorage=true",
        "dicom-events"
    )
    queue_client.create_queue()

    # Store DICOM instance
    response = await client.post(
        "/v2/studies",
        files={"file": open("tests/data/instance.dcm", "rb")},
        headers={"Content-Type": "multipart/related; type=application/dicom"}
    )
    assert response.status_code == 200

    # Wait for async event publishing
    await asyncio.sleep(1)

    # Verify event in queue
    messages = queue_client.receive_messages(max_messages=1)
    assert len(messages) == 1

    event_data = json.loads(messages[0].content)
    assert event_data["eventType"] == "Microsoft.HealthcareApis.DicomImageCreated"
    assert "imageStudyInstanceUid" in event_data["data"]
```

**Frame Retrieval Integration:**
```python
async def test_frame_retrieval_workflow():
    """Store multi-frame instance, retrieve individual frames."""
    # Store multi-frame instance
    await client.post("/v2/studies", files={"file": multi_frame_dcm})

    # Retrieve specific frame
    response = await client.get(
        f"/v2/studies/{study}/series/{series}/instances/{instance}/frames/2",
        headers={"Accept": "application/octet-stream"}
    )

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/octet-stream"
    assert len(response.content) > 0

async def test_rendered_image_jpeg():
    """Render DICOM frame as JPEG."""
    await client.post("/v2/studies", files={"file": instance_dcm})

    response = await client.get(
        f"/v2/studies/{study}/series/{series}/instances/{instance}/rendered",
        headers={"Accept": "image/jpeg"}
    )

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "image/jpeg"

    # Verify valid JPEG
    image = Image.open(io.BytesIO(response.content))
    assert image.format == "JPEG"
```

**UPS-RS Workflow Integration:**
```python
async def test_ups_workflow_complete_cycle():
    """Create → Claim → Update → Complete workflow."""
    # Create workitem
    response = await client.post(
        "/v2/workitems",
        json=create_workitem_payload()
    )
    assert response.status_code == 201
    workitem_uid = response.headers["Location"].split("/")[-1]

    # Claim workitem (SCHEDULED → IN PROGRESS)
    txn_uid = str(uuid.uuid4())
    await client.put(
        f"/v2/workitems/{workitem_uid}/state",
        json={
            "00081195": {"vr": "UI", "Value": [txn_uid]},
            "00741000": {"vr": "CS", "Value": ["IN PROGRESS"]}
        }
    )

    # Update workitem
    await client.post(
        f"/v2/workitems/{workitem_uid}?{txn_uid}",
        json={
            "00081030": {"vr": "LO", "Value": ["Updated description"]}
        }
    )

    # Complete workitem
    response = await client.put(
        f"/v2/workitems/{workitem_uid}/state",
        json={
            "00081195": {"vr": "UI", "Value": [txn_uid]},
            "00741000": {"vr": "CS", "Value": ["COMPLETED"]}
        }
    )
    assert response.status_code == 200

    # Verify final state
    workitem = await client.get(f"/v2/workitems/{workitem_uid}")
    assert workitem.json()["00741000"]["Value"][0] == "COMPLETED"
```

### 9.3 Smoke Test Updates

**Add to smoke_test.py:**

```python
def test_frame_retrieval():
    """Test frame retrieval endpoint."""
    print("[9/12] Frame Retrieval...")

    # Upload multi-frame instance
    upload_multi_frame_instance()

    # Retrieve frame 1
    response = requests.get(
        f"{base_url}/v2/studies/{study}/series/{series}/instances/{instance}/frames/1",
        headers={"Accept": "application/octet-stream"}
    )
    assert response.status_code == 200, "Frame retrieval failed"
    print("  [PASS] Frame 1 retrieved")

    # Retrieve frames 1,3,5
    response = requests.get(
        f"{base_url}/v2/studies/{study}/series/{series}/instances/{instance}/frames/1,3,5",
        headers={"Accept": "multipart/related; type=application/octet-stream"}
    )
    assert response.status_code == 200, "Multi-frame retrieval failed"
    print("  [PASS] Frames 1,3,5 retrieved")

def test_rendered_images():
    """Test rendered image endpoints."""
    print("[10/12] Rendered Images...")

    # Render as JPEG
    response = requests.get(
        f"{base_url}/v2/studies/{study}/series/{series}/instances/{instance}/rendered",
        headers={"Accept": "image/jpeg"}
    )
    assert response.status_code == 200, "JPEG render failed"
    assert response.headers["Content-Type"] == "image/jpeg"
    print("  [PASS] JPEG render")

    # Render as PNG with quality
    response = requests.get(
        f"{base_url}/v2/studies/{study}/series/{series}/instances/{instance}/frames/1/rendered?quality=85",
        headers={"Accept": "image/png"}
    )
    assert response.status_code == 200, "PNG render failed"
    print("  [PASS] PNG render")

def test_fuzzy_search():
    """Test fuzzy matching search."""
    print("[11/12] Fuzzy Search...")

    # Search with fuzzy matching
    response = requests.get(
        f"{base_url}/v2/studies",
        params={"PatientName": "joh", "fuzzymatching": "true"}
    )
    assert response.status_code == 200
    results = response.json()
    assert len(results) > 0, "Fuzzy search found no results"
    print(f"  [PASS] Fuzzy search (found {len(results)} studies)")

def test_ups_workflow():
    """Test UPS-RS workitem workflow."""
    print("[12/12] UPS-RS Workflow...")

    # Create workitem
    workitem_uid = str(uuid.uuid4())
    response = requests.post(
        f"{base_url}/v2/workitems?{workitem_uid}",
        json=create_workitem_json(),
        headers={"Content-Type": "application/dicom+json"}
    )
    assert response.status_code == 201, "Workitem create failed"
    print("  [PASS] Workitem created")

    # Claim workitem
    txn_uid = str(uuid.uuid4())
    response = requests.put(
        f"{base_url}/v2/workitems/{workitem_uid}/state",
        json={
            "00081195": {"vr": "UI", "Value": [txn_uid]},
            "00741000": {"vr": "CS", "Value": ["IN PROGRESS"]}
        }
    )
    assert response.status_code == 200, "Workitem claim failed"
    print("  [PASS] Workitem claimed")

    # Complete workitem
    response = requests.put(
        f"{base_url}/v2/workitems/{workitem_uid}/state",
        json={
            "00081195": {"vr": "UI", "Value": [txn_uid]},
            "00741000": {"vr": "CS", "Value": ["COMPLETED"]}
        }
    )
    assert response.status_code == 200, "Workitem complete failed"
    print("  [PASS] Workitem completed")
```

### 9.4 Performance Tests

**Frame Extraction Performance:**
```python
async def test_frame_extraction_performance():
    """Extract 1000 frames in < 30 seconds."""
    instances = await setup_multi_frame_instances(count=100, frames_each=10)

    start = time.time()

    for instance in instances:
        for frame in range(1, 11):
            await client.get(f"/v2/.../instances/{instance}/frames/{frame}")

    elapsed = time.time() - start
    assert elapsed < 30, f"Frame extraction too slow: {elapsed}s"
```

**Search Performance:**
```python
async def test_wildcard_search_performance():
    """Wildcard search on 10k studies < 500ms."""
    await setup_studies(count=10000)

    start = time.time()
    results = await search_studies({"PatientID": "PAT*"})
    elapsed = time.time() - start

    assert elapsed < 0.5, f"Wildcard search too slow: {elapsed}s"
```

**Event Publishing Overhead:**
```python
async def test_event_publishing_overhead():
    """Event publishing adds < 100ms per STOW."""
    # Measure STOW without events
    disable_event_providers()
    start = time.time()
    await stow_instance()
    baseline = time.time() - start

    # Measure STOW with events
    enable_event_providers()
    start = time.time()
    await stow_instance()
    with_events = time.time() - start

    overhead = with_events - baseline
    assert overhead < 0.1, f"Event overhead too high: {overhead}s"
```

---

## 10. Implementation Roadmap

### Phase 1: Event System Foundation (Week 1)
- Event provider base class and manager
- Azure Storage Queue provider (Azurite compatible)
- Webhook provider with retry logic
- File and in-memory providers
- Configuration loading
- Event publishing integration into STOW/DELETE
- Unit tests for event system
- Integration test with Azurite

### Phase 2: WADO-RS Enhancements (Week 2)
- Frame extraction service
- Frame storage and caching
- Frame retrieval endpoints (`/frames/{frames}`)
- Rendered image endpoints (`/rendered`)
- JPEG/PNG rendering with PIL
- Transfer syntax transcoding support
- Unit tests for frame extraction and rendering
- Integration tests for frame retrieval

### Phase 3: STOW-RS Enhancements (Week 2)
- PUT method support (upsert logic)
- Expiry header parsing and storage
- Background task for expired study deletion
- Enhanced warning codes (45063, 1)
- Unit tests for upsert and expiry
- Integration tests for expiry workflow

### Phase 4: QIDO-RS Enhancements (Week 3)
- Fuzzy matching implementation
- Wildcard matching (`*`, `?`)
- UID list parsing and querying
- Extended query tag value storage and indexing
- Extended query tag search support
- Aggregated attributes (NumberOfStudyRelatedInstances)
- Unit tests for all search types
- Performance tests for search

### Phase 5: UPS-RS Worklist Service (Week 4-5)
- Database schema for workitems
- Create workitem endpoint
- Retrieve workitem endpoint
- Update workitem endpoint (with transaction UID validation)
- Change state endpoint (state machine implementation)
- Request cancellation endpoint
- Search workitems endpoint
- Unit tests for state machine
- Integration tests for full workflow
- Smoke tests for UPS-RS

### Phase 6: Testing & Documentation (Week 6)
- Comprehensive smoke test suite update
- Performance test suite
- Integration test with Azurite in CI
- Update README with new features
- API documentation updates
- Docker compose with Azurite

---

## 11. Dependencies

### New Python Packages

```toml
[project]
dependencies = [
    # Existing
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "sqlalchemy[asyncio]>=2.0.36",
    "asyncpg>=0.30.0",
    "pydicom>=3.0.1",
    "httpx>=0.28.0",

    # New for this design
    "azure-storage-queue>=12.11.0",  # Azure Storage Queue client
    "pillow>=11.0.0",                # Image rendering (JPEG/PNG)
    "pillow-jpls>=1.0.0",            # JPEG-LS support
    "tenacity>=9.0.0",               # Retry logic for webhooks
    "pyyaml>=6.0.2",                 # YAML config support
]
```

### Docker Compose Additions

```yaml
# docker-compose.yml
services:
  emulator:
    # ... existing config ...
    environment:
      EVENT_PROVIDERS: |
        [
          {
            "type": "azure_storage_queue",
            "connection_string": "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://azurite:10000/devstoreaccount1;QueueEndpoint=http://azurite:10001/devstoreaccount1;",
            "queue_name": "dicom-events",
            "enabled": true
          }
        ]
    depends_on:
      - postgres
      - azurite

  azurite:
    image: mcr.microsoft.com/azure-storage/azurite:latest
    ports:
      - "10000:10000"  # Blob
      - "10001:10001"  # Queue
      - "10002:10002"  # Table
    command: "azurite --blobHost 0.0.0.0 --queueHost 0.0.0.0"
```

---

## 12. Success Criteria

**Event System:**
- ✅ Events published to Azurite queue on STOW/DELETE
- ✅ Webhook provider sends POST with retry
- ✅ DICOM operations succeed even if events fail
- ✅ Event schema matches Azure Event Grid format

**WADO-RS:**
- ✅ Frame retrieval returns correct frames
- ✅ Multi-frame retrieval works with comma-separated list
- ✅ Rendered images return valid JPEG/PNG
- ✅ Transfer syntax transcoding works for supported formats

**STOW-RS:**
- ✅ PUT upsert replaces existing instances
- ✅ Expiry headers set study expiration
- ✅ Background task deletes expired studies

**QIDO-RS:**
- ✅ Fuzzy matching finds names by prefix
- ✅ Wildcard matching (`*`, `?`) works
- ✅ UID list search finds multiple studies
- ✅ Extended query tag search works

**UPS-RS:**
- ✅ State machine enforces valid transitions
- ✅ Transaction UID provides ownership
- ✅ Search finds workitems by all supported attributes
- ✅ Full workflow (create → claim → update → complete) works

**Testing:**
- ✅ All unit tests pass
- ✅ Integration tests with Azurite pass
- ✅ Smoke tests cover all new features
- ✅ Performance tests meet targets

---

## 13. Open Questions & Future Work

**Event Grid Subscriptions:**
- Azure supports Event Grid subscriptions with filtering
- Consider adding webhook subscription management API
- Allow users to register webhooks dynamically

**Bulk Update API:**
- Azure supports bulk update of DICOM attributes
- Not in scope for initial parity
- Consider for Phase 7

**External Metadata:**
- Azure supports storing/retrieving DICOM with external metadata
- Not in scope for initial parity
- Requires additional database schema

**UPS-RS Subscriptions:**
- Azure supports UPS-RS subscription endpoints
- Return 501 Not Implemented for MVP
- Consider implementing with WebSocket notifications

**Event Ordering:**
- Should events guarantee ordering?
- Azure Storage Queue provides FIFO within partition
- Consider sequence number enforcement

---

## Appendices

### A. Azure Event Grid Event Schema Reference

```json
{
  "id": "unique-event-id",
  "eventType": "Microsoft.HealthcareApis.DicomImageCreated",
  "subject": "/dicom/studies/{study}/series/{series}/instances/{instance}",
  "eventTime": "2026-02-15T10:30:00.000Z",
  "dataVersion": "1.0",
  "metadataVersion": "1",
  "topic": "https://workspace-dicom.dicom.azurehealthcareapis.com",
  "data": {
    "partitionName": "Microsoft.Default",
    "imageStudyInstanceUid": "1.2.826.0.1.3680043.8.498.123",
    "imageSeriesInstanceUid": "1.2.826.0.1.3680043.8.498.456",
    "imageSopInstanceUid": "1.2.826.0.1.3680043.8.498.789",
    "serviceHostName": "workspace-dicom.dicom.azurehealthcareapis.com",
    "sequenceNumber": 1
  }
}
```

### B. Transfer Syntax Support Matrix

| Transfer Syntax | UID | Read | Write | Transcode From | Transcode To |
|-----------------|-----|------|-------|----------------|--------------|
| Implicit VR Little Endian | 1.2.840.10008.1.2 | ✅ | ✅ | ✅ | ✅ |
| Explicit VR Little Endian | 1.2.840.10008.1.2.1 | ✅ | ✅ | ✅ | ✅ |
| Explicit VR Big Endian | 1.2.840.10008.1.2.2 | ✅ | ✅ | ✅ | ✅ |
| JPEG Baseline | 1.2.840.10008.1.2.4.50 | ✅ | ✅ | ✅ | ✅ |
| JPEG Lossless | 1.2.840.10008.1.2.4.57 | ✅ | ✅ | ✅ | ✅ |
| JPEG Lossless SV1 | 1.2.840.10008.1.2.4.70 | ✅ | ✅ | ✅ | ✅ |
| JPEG 2000 Lossless | 1.2.840.10008.1.2.4.90 | ✅ | ✅ | ✅ | ✅ |
| JPEG 2000 | 1.2.840.10008.1.2.4.91 | ✅ | ✅ | ✅ | ✅ |
| RLE Lossless | 1.2.840.10008.1.2.5 | ✅ | ✅ | ✅ | ⚠️ |
| Others | * | ❌ | ❌ | ❌ | ❌ |

### C. UPS-RS State Machine Detailed Transitions

```
State: SCHEDULED
  ├─ Allowed Actions:
  │   ├─ Update (no transaction UID required)
  │   ├─ Request Cancel → CANCELED
  │   └─ Change State to IN PROGRESS (sets transaction UID)
  └─ Forbidden Actions:
      ├─ Change State to COMPLETED (must go through IN PROGRESS)
      └─ Change State to CANCELED (use Request Cancel)

State: IN PROGRESS
  ├─ Allowed Actions:
  │   ├─ Update (requires correct transaction UID)
  │   ├─ Change State to COMPLETED (requires correct transaction UID)
  │   └─ Change State to CANCELED (requires correct transaction UID)
  └─ Forbidden Actions:
      ├─ Update without transaction UID → 400
      ├─ Update with wrong transaction UID → 400
      ├─ Request Cancel → 409 (use Change State instead)
      └─ Change State to SCHEDULED → 409

State: COMPLETED
  ├─ Allowed Actions:
  │   └─ Retrieve (read-only)
  └─ Forbidden Actions:
      ├─ Update → 400
      ├─ Change State → 409
      └─ Request Cancel → 409

State: CANCELED
  ├─ Allowed Actions:
  │   └─ Retrieve (read-only)
  └─ Forbidden Actions:
      ├─ Update → 400
      ├─ Change State → 409
      └─ Request Cancel → 409 (with warning header)
```

### D. Performance Benchmarks

| Operation | Target | Measurement |
|-----------|--------|-------------|
| Frame extraction (single) | < 50ms | First request extracts + caches |
| Frame extraction (cached) | < 5ms | Subsequent requests from cache |
| Frame extraction (1000 frames) | < 30s | Batch extraction |
| Rendered image (JPEG) | < 100ms | Includes windowing |
| Rendered image (PNG) | < 150ms | Lossless compression |
| Search with fuzzy match | < 200ms | 10k studies |
| Search with wildcard | < 500ms | 10k studies |
| Search with UID list (10 UIDs) | < 100ms | Direct IN clause |
| Event publish (Storage Queue) | < 50ms | Azurite local |
| Event publish (Webhook) | < 200ms | Network dependent |
| UPS create workitem | < 50ms | Database insert |
| UPS search workitems | < 200ms | 1k workitems |

---

**End of Design Document**
