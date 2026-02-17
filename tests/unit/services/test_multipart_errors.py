"""Test multipart parser error handling."""

import pytest
from fastapi import HTTPException

from app.services.multipart import parse_multipart_related

pytestmark = pytest.mark.unit


def test_parse_multipart_missing_boundary():
    """Should raise HTTPException for missing boundary."""
    content = b"--test\r\nContent-Type: application/dicom\r\n\r\nDATA"
    content_type = "multipart/related; type=application/dicom"  # No boundary!

    with pytest.raises(HTTPException) as exc_info:
        parse_multipart_related(content, content_type)

    assert exc_info.value.status_code == 400
    assert "boundary" in exc_info.value.detail.lower()


def test_parse_multipart_invalid_boundary_format():
    """Should raise HTTPException for malformed boundary."""
    content = b"--test\r\nContent-Type: application/dicom\r\n\r\nDATA"
    content_type = 'multipart/related; type=application/dicom; boundary="'  # Unclosed quote

    with pytest.raises(HTTPException) as exc_info:
        parse_multipart_related(content, content_type)

    assert exc_info.value.status_code == 400


def test_parse_multipart_empty_body():
    """Should handle empty multipart body gracefully."""
    content = b""
    content_type = "multipart/related; type=application/dicom; boundary=test"

    parts = parse_multipart_related(content, content_type)
    assert len(parts) == 0  # No parts, not an error


def test_parse_multipart_no_dicom_parts():
    """Should skip non-DICOM parts without error."""
    content = b"--test\r\n" b"Content-Type: text/plain\r\n\r\n" b"Not DICOM\r\n" b"--test--"
    content_type = "multipart/related; type=application/dicom; boundary=test"

    parts = parse_multipart_related(content, content_type)
    assert len(parts) == 0  # Non-DICOM parts skipped


def test_parse_multipart_malformed_part_headers():
    """Should handle parts with malformed headers gracefully."""
    # Part with no header/body separator
    content = (
        b"--test\r\n"
        b"Content-Type: application/dicom"  # No \r\n\r\n separator
        b"InvalidData\r\n"
        b"--test--"
    )
    content_type = "multipart/related; type=application/dicom; boundary=test"

    # Should skip malformed parts without raising
    parts = parse_multipart_related(content, content_type)
    # The part is skipped because there's no \r\n\r\n separator
    assert len(parts) == 0
