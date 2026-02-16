"""Tests for study expiry schema (Phase 3, Task 1)."""

from datetime import datetime, timedelta, timezone

from app.models.dicom import DicomStudy


def test_dicom_study_has_expires_at():
    """DicomStudy model has expires_at field."""
    study = DicomStudy(
        study_instance_uid="1.2.3", expires_at=datetime.now(timezone.utc) + timedelta(days=1)
    )

    assert study.expires_at is not None
    assert isinstance(study.expires_at, datetime)


def test_dicom_study_expires_at_nullable():
    """expires_at can be None (no expiry)."""
    study = DicomStudy(study_instance_uid="1.2.3", expires_at=None)

    assert study.expires_at is None
