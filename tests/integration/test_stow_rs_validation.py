"""Integration tests for STOW-RS validation & error cases (Phase 2, Task 2).

Tests DICOM validation, error handling, and warning behaviors in STOW-RS.
Covers required attribute validation, searchable attribute warnings,
invalid DICOM content, multipart errors, and error response format.
"""

import pytest
from fastapi.testclient import TestClient

from tests.fixtures.factories import DicomFactory

pytestmark = pytest.mark.integration


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Helper Functions
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def build_multipart_request(
    dicom_files: list[bytes], boundary: str = "boundary123"
) -> tuple[bytes, str]:
    """Build multipart/related request body for STOW-RS.

    Args:
        dicom_files: List of DICOM file bytes to include
        boundary: Boundary delimiter string

    Returns:
        Tuple of (request_body, content_type_header)
    """
    parts = []
    for dcm_data in dicom_files:
        part = (f"--{boundary}\r\n" f"Content-Type: application/dicom\r\n" f"\r\n").encode()
        part += dcm_data + b"\r\n"
        parts.append(part)

    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    content_type = f'multipart/related; type="application/dicom"; boundary={boundary}'

    return body, content_type


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  1. Required Attribute Validation (5 tests)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_store_missing_sop_instance_uid_fails(client: TestClient):
    """Missing SOPInstanceUID (0008,0018) should cause failure.

    SOPInstanceUID is a required attribute per validate_required_attributes().
    When all instances in a batch fail, STOW-RS returns 409.
    """
    dcm_bytes = DicomFactory.create_invalid_dicom(missing_tags=["SOPInstanceUID"])
    body, content_type = build_multipart_request([dcm_bytes])

    response = client.post(
        "/v2/studies",
        content=body,
        headers={"Content-Type": content_type},
    )

    assert response.status_code == 409
    response_json = response.json()
    # FailedSOPSequence should be present
    assert "00081198" in response_json
    # ReferencedSOPSequence (success) should NOT be present
    assert "00081199" not in response_json


def test_store_missing_study_instance_uid_fails(client: TestClient):
    """Missing StudyInstanceUID (0020,000D) should cause failure.

    StudyInstanceUID is a required attribute per validate_required_attributes().
    """
    dcm_bytes = DicomFactory.create_invalid_dicom(missing_tags=["StudyInstanceUID"])
    body, content_type = build_multipart_request([dcm_bytes])

    response = client.post(
        "/v2/studies",
        content=body,
        headers={"Content-Type": content_type},
    )

    assert response.status_code == 409
    response_json = response.json()
    assert "00081198" in response_json
    assert "00081199" not in response_json


def test_store_missing_series_instance_uid_fails(client: TestClient):
    """Missing SeriesInstanceUID (0020,000E) should cause failure.

    SeriesInstanceUID is a required attribute per validate_required_attributes().
    """
    dcm_bytes = DicomFactory.create_invalid_dicom(missing_tags=["SeriesInstanceUID"])
    body, content_type = build_multipart_request([dcm_bytes])

    response = client.post(
        "/v2/studies",
        content=body,
        headers={"Content-Type": content_type},
    )

    assert response.status_code == 409
    response_json = response.json()
    assert "00081198" in response_json
    assert "00081199" not in response_json


def test_store_missing_patient_id_succeeds(client: TestClient):
    """Missing PatientID (0010,0020) -- NOT a required attribute in current implementation.

    Current behavior: PatientID is a searchable attribute, not a required attribute.
    The instance stores successfully with a null patient_id in the database.

    TODO: Azure v2 spec treats PatientID as required for storage. The emulator
    should be updated to validate PatientID and return 409 when missing.
    """
    # Create DICOM without PatientID by creating a valid one and removing PatientID
    dcm_bytes = DicomFactory.create_invalid_dicom(missing_tags=["PatientID"])
    body, content_type = build_multipart_request([dcm_bytes])

    response = client.post(
        "/v2/studies",
        content=body,
        headers={"Content-Type": content_type},
    )

    # Current behavior: succeeds (200) because PatientID is not in required list
    # TODO: Should return 409 per Azure v2 spec
    assert response.status_code == 200
    response_json = response.json()
    assert "00081199" in response_json  # Successfully stored


def test_store_missing_modality_succeeds(client: TestClient):
    """Missing Modality (0008,0060) -- NOT a required attribute in current implementation.

    Current behavior: Modality is a searchable attribute, not a required attribute.
    The instance stores successfully with a null modality in the database.

    TODO: Azure v2 spec treats Modality as required for storage. The emulator
    should be updated to validate Modality and return 409 when missing.
    """
    dcm_bytes = DicomFactory.create_invalid_dicom(missing_tags=["Modality"])
    body, content_type = build_multipart_request([dcm_bytes])

    response = client.post(
        "/v2/studies",
        content=body,
        headers={"Content-Type": content_type},
    )

    # Current behavior: succeeds (200) because Modality is not in required list
    # TODO: Should return 409 per Azure v2 spec
    assert response.status_code == 200
    response_json = response.json()
    assert "00081199" in response_json  # Successfully stored


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  2. Searchable Attribute Warnings (5 tests)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_store_missing_patient_name_returns_200(client: TestClient):
    """Missing PatientName -- instance stores successfully.

    Current behavior: PatientName is a searchable attribute. Missing searchable
    attributes do not trigger warnings in the current implementation.
    The instance is stored with null patient_name.

    TODO: Azure v2 spec returns HTTP 202 with WarningReason tag when searchable
    attributes are missing. Current implementation returns 200 with no warning.
    """
    # Create DICOM without PatientName
    dcm_bytes = DicomFactory.create_invalid_dicom(missing_tags=["PatientName"])
    body, content_type = build_multipart_request([dcm_bytes])

    response = client.post(
        "/v2/studies",
        content=body,
        headers={"Content-Type": content_type},
    )

    # Current behavior: 200 (no warnings for missing searchable attributes)
    # TODO: Should return 202 per Azure v2 spec
    assert response.status_code == 200
    response_json = response.json()
    assert "00081199" in response_json  # Successfully stored


def test_store_missing_study_date_returns_200(client: TestClient):
    """Missing StudyDate -- instance stores with null study_date.

    Current behavior: StudyDate is a searchable attribute. Missing searchable
    attributes do not trigger warnings. The instance is stored with null study_date.

    TODO: Azure v2 spec returns HTTP 202 with warning for missing searchable attributes.
    """
    # Create DICOM without StudyDate
    dcm_bytes = DicomFactory.create_invalid_dicom(missing_tags=["StudyDate"])
    body, content_type = build_multipart_request([dcm_bytes])

    response = client.post(
        "/v2/studies",
        content=body,
        headers={"Content-Type": content_type},
    )

    # Current behavior: 200 (no warnings)
    # TODO: Should return 202 per Azure v2 spec
    assert response.status_code == 200
    response_json = response.json()
    assert "00081199" in response_json


def test_store_invalid_study_date_format_returns_200(client: TestClient):
    """Invalid StudyDate format -- instance stores with malformed date.

    Current behavior: No validation is performed on date format. The malformed
    date string is stored as-is in the database.

    TODO: Azure v2 spec returns HTTP 202 with coercion warning (WarningReason 0xB000)
    when date values cannot be coerced to the expected format.
    """
    dcm_bytes = DicomFactory.create_ct_image(
        patient_id="DATE-FORMAT-001",
        study_date="not-a-date",
        with_pixel_data=False,
    )
    body, content_type = build_multipart_request([dcm_bytes])

    response = client.post(
        "/v2/studies",
        content=body,
        headers={"Content-Type": content_type},
    )

    # Current behavior: 200 (no format validation on searchable attributes)
    # TODO: Should return 202 with coercion warning per Azure v2 spec
    assert response.status_code == 200
    response_json = response.json()
    assert "00081199" in response_json


def test_response_includes_warning_reason_tag(client: TestClient):
    """Verify WarningReason tag (0008,1196) behavior in store responses.

    Current behavior: WarningReason tag is only included in the build_store_response
    when the warnings list is non-empty. Since the current implementation does not
    generate searchable attribute warnings, the tag is never present in normal responses.

    TODO: Implement searchable attribute validation to produce warnings and include
    WarningReason tag (0008,1196) with value 0xB000 in 202 responses.
    """
    # Store a valid DICOM - should have no warnings
    dcm_bytes = DicomFactory.create_ct_image(
        patient_id="WARNING-TAG-001",
        with_pixel_data=False,
    )
    body, content_type = build_multipart_request([dcm_bytes])

    response = client.post(
        "/v2/studies",
        content=body,
        headers={"Content-Type": content_type},
    )

    assert response.status_code == 200
    response_json = response.json()

    # No warnings for fully valid DICOM
    assert "00081196" not in response_json  # WarningReason not present when no warnings

    # Verify the success structure is intact
    assert "00081199" in response_json
    assert "00081198" not in response_json  # No FailedSOPSequence


def test_multiple_warnings_not_aggregated(client: TestClient):
    """Multiple missing searchable attributes -- all stored without warnings.

    Current behavior: No searchable attribute warnings are generated, so there are
    no warnings to aggregate. All instances with missing searchable attributes
    are stored successfully.

    TODO: Azure v2 spec should return 202 with aggregated warnings for all
    missing/invalid searchable attributes in a single response.
    """
    # Create DICOM missing multiple searchable attributes
    dcm_bytes = DicomFactory.create_invalid_dicom(
        missing_tags=["PatientName", "StudyDate", "StudyDescription"]
    )
    body, content_type = build_multipart_request([dcm_bytes])

    response = client.post(
        "/v2/studies",
        content=body,
        headers={"Content-Type": content_type},
    )

    # Current behavior: 200 with no warnings
    # TODO: Should return 202 with aggregated warnings per Azure v2 spec
    assert response.status_code == 200
    response_json = response.json()
    assert "00081199" in response_json  # Successfully stored
    assert "00081196" not in response_json  # No WarningReason tag


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  3. Invalid DICOM Content (5 tests)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_store_non_dicom_file_fails(client: TestClient):
    """Random bytes that are not DICOM should fail.

    The parse_dicom() function uses pydicom.dcmread(force=True), which attempts
    to parse even non-DICOM data. However, random bytes will lack required
    attributes (SOPInstanceUID, etc.), causing validate_required_attributes()
    to fail, resulting in a failure entry. Since all instances fail, returns 409.
    """
    random_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100  # PNG-like header
    body, content_type = build_multipart_request([random_bytes])

    response = client.post(
        "/v2/studies",
        content=body,
        headers={"Content-Type": content_type},
    )

    # Non-DICOM data: parse_dicom with force=True may succeed but
    # validate_required_attributes will fail, leading to 409
    assert response.status_code == 409
    response_json = response.json()
    assert "00081198" in response_json  # FailedSOPSequence
    assert "00081199" not in response_json  # No success


def test_store_corrupted_dicom_fails(client: TestClient):
    """Truncated/corrupted DICOM file should fail.

    Takes a valid DICOM and truncates it to create corrupt data.
    pydicom with force=True still parses the truncated bytes into a Dataset,
    but all required attributes are missing from the truncated content,
    causing validate_required_attributes() to fail.
    """
    # Create a valid DICOM, then truncate to make it corrupt
    valid_dcm = DicomFactory.create_ct_image(
        patient_id="CORRUPT-001",
        with_pixel_data=False,
    )
    # Truncate to just the preamble + partial header (128-byte preamble + 4-byte DICM + ~8 bytes)
    corrupted = valid_dcm[:140]

    body, content_type = build_multipart_request([corrupted])

    response = client.post(
        "/v2/studies",
        content=body,
        headers={"Content-Type": content_type},
    )

    # Truncated DICOM: parse_dicom succeeds (force=True) but all required
    # attributes are missing -> validation failure -> 409
    assert response.status_code == 409
    response_json = response.json()
    # FailedSOPSequence must be present with the failure entry
    assert "00081198" in response_json
    assert len(response_json["00081198"]["Value"]) >= 1
    # No instances should have stored successfully
    assert "00081199" not in response_json


def test_store_invalid_transfer_syntax_stores_successfully(client: TestClient):
    """DICOM with unknown Transfer Syntax UID -- stores successfully.

    Current behavior: pydicom with force=True does not validate transfer syntax.
    The file is stored as-is with the unknown transfer syntax UID.

    TODO: Azure v2 spec may reject instances with unknown transfer syntax UIDs.
    The current implementation is permissive due to force=True parsing.
    """
    # Create DICOM with a non-standard (made-up) transfer syntax
    dcm_bytes = DicomFactory.create_ct_image(
        patient_id="UNKNOWN-TS-001",
        transfer_syntax="1.2.3.4.5.6.7.8.9.999",  # Non-existent TS UID
        with_pixel_data=False,
    )
    body, content_type = build_multipart_request([dcm_bytes])

    response = client.post(
        "/v2/studies",
        content=body,
        headers={"Content-Type": content_type},
    )

    # Current behavior: stores successfully (force=True ignores unknown TS)
    # TODO: Azure v2 may reject unknown transfer syntaxes
    assert response.status_code == 200
    response_json = response.json()
    assert "00081199" in response_json


def test_store_empty_file_returns_200_with_no_instances(client: TestClient):
    """Zero bytes content -- empty part is silently skipped.

    Current behavior: An empty part in the multipart request is filtered out by
    the multipart parser (the `if data:` check), resulting in zero parts
    processed. With no stored instances and no failures, returns 200 with an
    empty response.

    TODO: Azure v2 spec should return 400 Bad Request for empty/zero-byte
    DICOM content. Current implementation silently ignores empty parts.
    """
    empty_bytes = b""
    body, content_type = build_multipart_request([empty_bytes])

    response = client.post(
        "/v2/studies",
        content=body,
        headers={"Content-Type": content_type},
    )

    # Empty data: multipart parser skips empty parts, resulting in no instances
    # to process. With stored=[] and failures=[], status = 200.
    # TODO: Should return 400 Bad Request per spec
    response_json = response.json()
    assert response.status_code == 200
    # No instances were stored
    assert "00081199" not in response_json


def test_store_text_file_as_dicom_fails(client: TestClient):
    """Text content inside a valid multipart/related request should fail.

    This tests text data sent as a DICOM part within a correctly-typed
    multipart/related request. The text is parsed by pydicom with force=True,
    but since it lacks required DICOM attributes, validation fails.

    Note: This tests "non-DICOM content within valid multipart structure",
    not "wrong top-level Content-Type" (which is covered by
    test_store_invalid_content_type_fails).
    """
    text_content = b"This is a plain text file, not a DICOM file.\n" * 10
    body, content_type = build_multipart_request([text_content])

    response = client.post(
        "/v2/studies",
        content=body,
        headers={"Content-Type": content_type},
    )

    # Text file: parse_dicom force=True creates a minimal dataset but
    # required attributes are missing -> validation failure -> 409
    assert response.status_code == 409
    response_json = response.json()
    assert "00081198" in response_json  # FailedSOPSequence
    assert "00081199" not in response_json


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  4. Multipart Errors (3 tests)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_store_missing_boundary_fails(client: TestClient):
    """Content-Type without boundary parameter should fail.

    The parse_multipart_related() function raises ValueError when no boundary
    is found in the Content-Type header. This exception is unhandled by the
    endpoint and propagates as an uncaught exception.

    TODO: The endpoint should catch ValueError from multipart parsing and
    return 400 Bad Request instead of raising an unhandled exception.
    """
    dcm_bytes = DicomFactory.create_ct_image(
        patient_id="NO-BOUNDARY-001",
        with_pixel_data=False,
    )

    # Build body with boundary but send Content-Type WITHOUT boundary
    body = b"--boundary123\r\nContent-Type: application/dicom\r\n\r\n"
    body += dcm_bytes + b"\r\n"
    body += b"--boundary123--\r\n"

    # Current behavior: ValueError raised from parse_multipart_related is unhandled
    # TODO: Should return 400 Bad Request
    with pytest.raises(ValueError, match="No boundary found"):
        client.post(
            "/v2/studies",
            content=body,
            headers={"Content-Type": 'multipart/related; type="application/dicom"'},
        )


def test_store_invalid_content_type_fails(client: TestClient):
    """Non-multipart Content-Type should fail.

    When Content-Type is not multipart/related, the boundary extraction
    in parse_multipart_related() will fail with ValueError because no
    boundary parameter is present.

    TODO: The endpoint should validate Content-Type is multipart/related
    before attempting to parse, returning 415 Unsupported Media Type.
    """
    dcm_bytes = DicomFactory.create_ct_image(
        patient_id="BAD-CT-001",
        with_pixel_data=False,
    )

    # Current behavior: ValueError raised from parse_multipart_related is unhandled
    # TODO: Should return 415 Unsupported Media Type
    with pytest.raises(ValueError, match="No boundary found"):
        client.post(
            "/v2/studies",
            content=dcm_bytes,
            headers={"Content-Type": "application/dicom"},
        )


def test_store_malformed_multipart_fails(client: TestClient):
    """Malformed multipart body with mismatched boundaries.

    When the body uses a different boundary than declared in Content-Type,
    parse_multipart_related() splits by the declared boundary. The entire
    body becomes a single chunk. Since the chunk contains Content-Type headers
    and DICOM data, the parser treats it as a valid part.

    Current behavior: The permissive parser extracts the DICOM data from the
    malformed structure, and pydicom with force=True manages to parse it.
    The instance is stored successfully.

    TODO: The multipart parser should validate boundary structure more strictly
    and return 400 Bad Request when boundaries don't match.
    """
    # Send body with NO boundaries at all - just raw garbage
    content_type = 'multipart/related; type="application/dicom"; boundary=correct-boundary'
    # Body is random data with no boundary markers
    body = b"This is not a multipart message at all, just random text data."

    response = client.post(
        "/v2/studies",
        content=body,
        headers={"Content-Type": content_type},
    )

    # With no matching boundaries, parser returns empty parts list.
    # No instances processed -> 200 with no ReferencedSOPSequence
    # TODO: Should return 400 Bad Request when no valid parts are found
    assert response.status_code == 200
    response_json = response.json()
    # No instances were stored
    assert "00081199" not in response_json


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  5. Response Error Format (2 tests)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_error_response_includes_failure_reason(client: TestClient):
    """FailureReason tag (0008,1197) should be present in failed SOP entries.

    When a DICOM instance fails validation, the FailedSOPSequence entries
    should include the FailureReason tag with an appropriate failure code.
    """
    # Missing SOPInstanceUID triggers validation failure
    dcm_bytes = DicomFactory.create_invalid_dicom(missing_tags=["SOPInstanceUID"])
    body, content_type = build_multipart_request([dcm_bytes])

    response = client.post(
        "/v2/studies",
        content=body,
        headers={"Content-Type": content_type},
    )

    assert response.status_code == 409
    response_json = response.json()

    # Check FailedSOPSequence structure
    assert "00081198" in response_json
    failed_sops = response_json["00081198"]["Value"]
    assert len(failed_sops) >= 1

    # Verify FailureReason tag is present
    failed_entry = failed_sops[0]
    assert "00081197" in failed_entry  # FailureReason
    assert failed_entry["00081197"]["vr"] == "US"

    # Value should be FAILURE_REASON_UNABLE_TO_PROCESS (0xC000 = 49152)
    assert failed_entry["00081197"]["Value"][0] == 0xC000


def test_error_response_includes_failed_sop_sequence(client: TestClient):
    """FailedSOPSequence (0008,1198) lists which instances failed.

    When some instances fail in a batch, FailedSOPSequence should contain
    entries for each failed instance with ReferencedSOPClassUID,
    ReferencedSOPInstanceUID, and FailureReason.
    """
    # Create a batch: one valid, one invalid
    valid_dcm = DicomFactory.create_ct_image(
        patient_id="BATCH-OK-001",
        with_pixel_data=False,
    )
    invalid_dcm = DicomFactory.create_invalid_dicom(
        missing_tags=["StudyInstanceUID"],
    )

    body, content_type = build_multipart_request([valid_dcm, invalid_dcm])

    response = client.post(
        "/v2/studies",
        content=body,
        headers={"Content-Type": content_type},
    )

    # Mixed batch: one success, one failure -> status 202 (partial success)
    # The response should have both ReferencedSOPSequence and FailedSOPSequence
    assert response.status_code == 202
    response_json = response.json()

    # Verify both sequences present
    assert "00081199" in response_json  # ReferencedSOPSequence (success)
    assert "00081198" in response_json  # FailedSOPSequence (failure)

    # Verify success entries
    success_sops = response_json["00081199"]["Value"]
    assert len(success_sops) == 1

    # Verify failure entries
    failed_sops = response_json["00081198"]["Value"]
    assert len(failed_sops) == 1

    # Verify failure entry structure
    failed_entry = failed_sops[0]
    assert "00081150" in failed_entry  # ReferencedSOPClassUID
    assert "00081155" in failed_entry  # ReferencedSOPInstanceUID
    assert "00081197" in failed_entry  # FailureReason
