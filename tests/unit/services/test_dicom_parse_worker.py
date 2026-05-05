"""Unit tests for the picklable DICOM parse worker.

The worker function is what the STOW route hands off to a
:class:`concurrent.futures.ProcessPoolExecutor`.  We exercise it
in-process here (no subprocess) — :class:`ParsedPartRecord` carrying
the right shape across a process boundary is what tests in
``tests/unit/routers/test_dicomweb_stow.py`` assert at the integration
level.
"""

from __future__ import annotations

import pickle

import pytest

from app.services.dicom_parse_worker import ParsedPartRecord, parse_part_to_record
from tests.fixtures.factories import DicomFactory

pytestmark = pytest.mark.unit


def test_parses_valid_dicom_into_picklable_record():
    """Happy path: a valid CT instance round-trips through pickle."""
    dicom_bytes = DicomFactory.create_ct_image(
        patient_id="WORKER-001",
        patient_name="Worker^Test",
    )

    record = parse_part_to_record(dicom_bytes)

    assert record.ok is True
    assert record.parse_error is None
    assert record.study_uid
    assert record.series_uid
    assert record.sop_uid
    assert record.sop_class_uid
    assert record.transfer_syntax_uid is not None

    # PS3.18 F.2 tag format: 8 hex chars, dict with vr/Value
    assert isinstance(record.dicom_json, dict)
    assert "00100010" in record.dicom_json  # PatientName
    assert record.dicom_json["00100010"]["vr"] == "PN"

    # Searchable metadata is the flat columns dict the route layer needs.
    assert record.searchable["patient_id"] == "WORKER-001"

    # The whole record must round-trip through pickle (it's about to
    # cross a subprocess boundary in production).
    blob = pickle.dumps(record)
    revived = pickle.loads(blob)
    assert revived == record


def test_records_required_attribute_failures_without_raising():
    """Missing SOPInstanceUID → ok=False with required_errors populated.

    Worker must NOT raise on validation failure; raising would surface
    as a BrokenProcessPool and tear down the whole pool.
    """
    bad = DicomFactory.create_invalid_dicom(missing_tags=["SOPInstanceUID"])

    record = parse_part_to_record(bad)

    assert record.ok is False
    assert record.required_errors  # at least one error message
    # parse_error stays None; this is a validation failure, not a
    # parse-time exception.
    assert record.parse_error is None


def test_garbage_bytes_become_failure_record_without_raising():
    """Raw garbage produces an ``ok=False`` record without raising.

    Worker must catch every exception path so the pool stays alive.
    pydicom's ``dcmread`` is permissive — random bytes typically yield
    an empty dataset that fails required-attribute validation rather
    than a parse exception.  Either path is acceptable; what matters
    is that the worker NEVER lets an exception escape.
    """
    record = parse_part_to_record(b"this is not DICOM at all")

    assert record.ok is False
    # Either branch is fine; parse exceptions and validation failures
    # are both reported via record.ok=False with a populated detail.
    assert record.parse_error is not None or record.required_errors


def test_searchable_warnings_propagate_for_valid_dicom_missing_optional_attrs():
    """Missing PatientID is a v2 *warning*, not a failure.

    The worker reports the warning in ``searchable_warnings`` and still
    returns ``ok=True`` so the route layer can produce a 202 response.
    """
    dicom_bytes = DicomFactory.create_dicom_with_attrs(patient_id=None)

    record = parse_part_to_record(dicom_bytes)

    assert record.ok is True
    assert any("PatientID" in w for w in record.searchable_warnings)


def test_record_default_construction_is_pickle_safe():
    """The empty-record dataclass shape itself round-trips through pickle.

    Guards against a regression where someone adds a non-picklable
    default (e.g. a Dataset) to ParsedPartRecord and it only blows up
    in production with a BrokenProcessPool.
    """
    empty = ParsedPartRecord(ok=True)
    assert pickle.loads(pickle.dumps(empty)) == empty
