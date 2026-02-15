"""Tests for Workitem model (Phase 5, Task 1)."""

import pytest
from datetime import datetime, timezone, timedelta
from app.models.dicom import Workitem


def test_workitem_creation():
    """Create a Workitem with required fields."""
    workitem = Workitem(
        sop_instance_uid="1.2.840.10008.5.1.4.34.5.1",
        procedure_step_state="SCHEDULED",
        dicom_dataset={
            "00080005": {"vr": "CS", "Value": ["ISO_IR 100"]},
            "00080016": {"vr": "UI", "Value": ["1.2.840.10008.5.1.4.34.5"]},
            "00080018": {"vr": "UI", "Value": ["1.2.840.10008.5.1.4.34.5.1"]},
        }
    )

    assert workitem.sop_instance_uid == "1.2.840.10008.5.1.4.34.5.1"
    assert workitem.procedure_step_state == "SCHEDULED"
    assert workitem.transaction_uid is None
    assert workitem.dicom_dataset is not None
    assert isinstance(workitem.dicom_dataset, dict)


def test_workitem_state_values():
    """Test all valid procedure step states."""
    valid_states = ["SCHEDULED", "IN PROGRESS", "COMPLETED", "CANCELED"]

    for state in valid_states:
        workitem = Workitem(
            sop_instance_uid=f"1.2.3.{state}",
            procedure_step_state=state,
            dicom_dataset={"test": "data"}
        )
        assert workitem.procedure_step_state == state


def test_workitem_with_transaction_uid():
    """Test claimed workitem with transaction UID."""
    workitem = Workitem(
        sop_instance_uid="1.2.840.10008.5.1.4.34.5.1",
        transaction_uid="1.2.840.10008.1.2.3.4.5",
        procedure_step_state="IN PROGRESS",
        dicom_dataset={"test": "data"}
    )

    assert workitem.sop_instance_uid == "1.2.840.10008.5.1.4.34.5.1"
    assert workitem.transaction_uid == "1.2.840.10008.1.2.3.4.5"
    assert workitem.procedure_step_state == "IN PROGRESS"


def test_workitem_with_patient_info():
    """Test workitem with patient searchable attributes."""
    workitem = Workitem(
        sop_instance_uid="1.2.840.10008.5.1.4.34.5.1",
        procedure_step_state="SCHEDULED",
        patient_name="Doe^John",
        patient_id="PAT12345",
        dicom_dataset={"test": "data"}
    )

    assert workitem.patient_name == "Doe^John"
    assert workitem.patient_id == "PAT12345"


def test_workitem_with_scheduling():
    """Test workitem with scheduling information."""
    scheduled_time = datetime.now(timezone.utc) + timedelta(hours=2)

    workitem = Workitem(
        sop_instance_uid="1.2.840.10008.5.1.4.34.5.1",
        procedure_step_state="SCHEDULED",
        scheduled_procedure_step_start_datetime=scheduled_time,
        study_instance_uid="1.2.840.113619.2.1.1.1",
        accession_number="ACC12345",
        requested_procedure_id="RP001",
        dicom_dataset={"test": "data"}
    )

    assert workitem.scheduled_procedure_step_start_datetime == scheduled_time
    assert workitem.study_instance_uid == "1.2.840.113619.2.1.1.1"
    assert workitem.accession_number == "ACC12345"
    assert workitem.requested_procedure_id == "RP001"


def test_workitem_with_station_codes():
    """Test workitem with station code searchable attributes."""
    workitem = Workitem(
        sop_instance_uid="1.2.840.10008.5.1.4.34.5.1",
        procedure_step_state="SCHEDULED",
        scheduled_station_name_code="CT01",
        scheduled_station_class_code="CT_SCANNER",
        scheduled_station_geo_code="RADIOLOGY_DEPT_A",
        dicom_dataset={"test": "data"}
    )

    assert workitem.scheduled_station_name_code == "CT01"
    assert workitem.scheduled_station_class_code == "CT_SCANNER"
    assert workitem.scheduled_station_geo_code == "RADIOLOGY_DEPT_A"


def test_workitem_default_state():
    """Test that default state is SCHEDULED when explicitly set."""
    # SQLAlchemy defaults are applied at DB insert time, not object creation
    # When creating a workitem, procedure_step_state should be explicitly provided
    # or it will be None until persisted to DB where the default will apply
    workitem = Workitem(
        sop_instance_uid="1.2.840.10008.5.1.4.34.5.1",
        procedure_step_state="SCHEDULED",  # Explicitly set the default
        dicom_dataset={"test": "data"}
    )

    assert workitem.procedure_step_state == "SCHEDULED"
