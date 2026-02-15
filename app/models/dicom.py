"""SQLAlchemy models for the DICOM emulator."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, String, Integer, DateTime, Text, JSON, Boolean,
    BigInteger, Index, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class DicomInstance(Base):
    """Stored DICOM instance with extracted metadata for QIDO-RS queries."""

    __tablename__ = "dicom_instances"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    study_instance_uid = Column(String(128), nullable=False, index=True)
    series_instance_uid = Column(String(128), nullable=False, index=True)
    sop_instance_uid = Column(String(128), nullable=False, unique=True)
    sop_class_uid = Column(String(128))
    transfer_syntax_uid = Column(String(128))

    # Common searchable DICOM tags
    patient_id = Column(String(64), index=True)
    patient_name = Column(String(256))
    study_date = Column(String(8))  # YYYYMMDD
    study_time = Column(String(14))
    accession_number = Column(String(64), index=True)
    study_description = Column(String(256))
    modality = Column(String(16), index=True)
    series_description = Column(String(256))
    series_number = Column(Integer)
    instance_number = Column(Integer)
    referring_physician_name = Column(String(256))

    # Full DICOM JSON metadata (for /metadata endpoint)
    dicom_json = Column(JSON, nullable=False)

    # Storage path for the .dcm file on disk
    file_path = Column(String(512), nullable=False)
    file_size = Column(BigInteger)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_study_series", "study_instance_uid", "series_instance_uid"),
        Index("ix_patient_study", "patient_id", "study_date"),
    )


class ChangeFeedEntry(Base):
    """Change feed tracking — mirrors Azure DICOM Service change feed."""

    __tablename__ = "change_feed"

    sequence = Column(BigInteger, primary_key=True, autoincrement=True)
    study_instance_uid = Column(String(128), nullable=False)
    series_instance_uid = Column(String(128), nullable=False)
    sop_instance_uid = Column(String(128), nullable=False)
    action = Column(String(16), nullable=False)  # create, update, delete
    state = Column(String(16), nullable=False, default="current")  # current, replaced, deleted
    timestamp = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    dicom_metadata = Column(JSON, default=dict)

    __table_args__ = (
        Index("ix_changefeed_timestamp", "timestamp"),
    )


class ExtendedQueryTag(Base):
    """Extended query tags — custom DICOM tags registered for QIDO-RS search."""

    __tablename__ = "extended_query_tags"

    id = Column(Integer, primary_key=True, autoincrement=True)
    path = Column(String(64), nullable=False, unique=True)  # e.g. "00100020"
    vr = Column(String(4), nullable=False)  # DICOM VR (CS, LO, DA, etc.)
    private_creator = Column(String(64))
    level = Column(String(16), nullable=False)  # Study, Series, Instance
    status = Column(String(16), nullable=False, default="Adding")  # Adding, Ready, Deleting
    query_status = Column(String(16), default="Enabled")  # Enabled, Disabled
    errors_count = Column(Integer, default=0)
    operation_id = Column(UUID(as_uuid=True))

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ExtendedQueryTagValue(Base):
    """Indexed values for extended query tags."""

    __tablename__ = "extended_query_tag_values"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    tag_id = Column(Integer, nullable=False, index=True)
    resource_uid = Column(String(128), nullable=False)  # study/series/instance UID
    resource_level = Column(String(16), nullable=False)
    value = Column(String(512))

    __table_args__ = (
        Index("ix_eqt_tag_value", "tag_id", "value"),
        UniqueConstraint("tag_id", "resource_uid", name="uq_eqt_resource"),
    )


class Operation(Base):
    """Async operation tracking — mirrors Azure's operation status pattern."""

    __tablename__ = "operations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    type = Column(String(64), nullable=False)  # add-extended-query-tag, reindex, bulk-update
    status = Column(String(16), nullable=False, default="running")  # running, succeeded, failed
    percent_complete = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))
    results = Column(JSON, default=dict)
    errors = Column(JSON, default=list)
