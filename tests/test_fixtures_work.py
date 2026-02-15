"""Verify test fixtures are working."""

import pytest


def test_sample_ct_dicom_fixture(sample_ct_dicom):
    """Sample CT fixture should return bytes."""
    assert isinstance(sample_ct_dicom, bytes)
    assert len(sample_ct_dicom) > 0


def test_valid_dicom_files_fixture(valid_dicom_files):
    """Valid DICOM files fixture should return list of files."""
    assert len(valid_dicom_files) >= 7
    assert all(f.suffix == ".dcm" for f in valid_dicom_files)


@pytest.mark.asyncio
async def test_client_fixture(client):
    """Client fixture should provide working test client."""
    response = client.get("/health")
    assert response.status_code == 200
