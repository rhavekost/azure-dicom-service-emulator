"""Integration tests for WADO-RS frame retrieval and rendered endpoints (Phase 2, Task 4).

Tests frame-level retrieval (GET .../frames/{frames}) and image rendering
(GET .../rendered). Covers single/multi-frame retrieval, multipart response
format, out-of-range errors, JPEG/PNG rendering, quality parameters, and
windowing.

20 tests total:
- Frame Retrieval: 8 tests
- Rendered Endpoints: 12 tests
"""

import io
import re
import shutil
from io import BytesIO
from pathlib import Path

import numpy as np
import pydicom
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydicom.dataset import Dataset, FileDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

pytestmark = pytest.mark.integration


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Helper Functions
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def build_multipart_request(
    dicom_files: list[bytes], boundary: str = "boundary123"
) -> tuple[bytes, str]:
    """Build multipart/related request body for STOW-RS."""
    parts = []
    for dcm_data in dicom_files:
        part = (f"--{boundary}\r\n" f"Content-Type: application/dicom\r\n" f"\r\n").encode()
        part += dcm_data + b"\r\n"
        parts.append(part)

    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    content_type = f'multipart/related; type="application/dicom"; boundary={boundary}'

    return body, content_type


def store_dicom(client: TestClient, dicom_bytes: bytes) -> dict:
    """Store a DICOM instance and return the STOW-RS response JSON."""
    body, content_type = build_multipart_request([dicom_bytes])
    response = client.post(
        "/v2/studies",
        content=body,
        headers={"Content-Type": content_type},
    )
    assert response.status_code == 200, f"Store failed: {response.status_code} {response.text}"
    return response.json()


def store_dicom_for_frames(
    client: TestClient, dicom_bytes: bytes, storage_dir: Path
) -> tuple[str, str, str]:
    """Store a DICOM instance and create the file layout expected by frame_cache.

    The dicom_engine stores files as: {storage}/{study}/{series}/{sop}.dcm
    The frame_cache and rendered endpoints expect: {storage}/{study}/{series}/{sop}/instance.dcm

    This helper stores via STOW-RS then copies the file to the expected path.

    Returns:
        Tuple of (study_uid, series_uid, sop_uid)
    """
    store_response = store_dicom(client, dicom_bytes)
    study_uid, series_uid, sop_uid = extract_uids_from_store_response(store_response)

    # Copy file from dicom_engine layout to frame_cache layout
    src = storage_dir / study_uid / series_uid / f"{sop_uid}.dcm"
    dst_dir = storage_dir / study_uid / series_uid / sop_uid
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / "instance.dcm"
    shutil.copy2(str(src), str(dst))

    return study_uid, series_uid, sop_uid


def extract_uids_from_store_response(store_response: dict) -> tuple[str, str, str]:
    """Extract study, series, and instance UIDs from a STOW-RS response.

    Returns:
        Tuple of (study_uid, series_uid, sop_uid)
    """
    retrieve_url = store_response["00081199"]["Value"][0]["00081190"]["Value"][0]
    # URL format: /v2/studies/{study}/series/{series}/instances/{instance}
    url_parts = retrieve_url.split("/")
    study_uid = url_parts[3]
    series_uid = url_parts[5]
    sop_uid = url_parts[7]
    return study_uid, series_uid, sop_uid


def create_ct_with_pixel_data(
    patient_id: str = "FRAME-TEST",
    rows: int = 64,
    cols: int = 64,
    study_uid: str | None = None,
    series_uid: str | None = None,
    sop_uid: str | None = None,
    pixel_value: int = 100,
) -> bytes:
    """Create a CT DICOM with specific pixel data for verification.

    Args:
        patient_id: Patient ID
        rows: Image rows
        cols: Image columns
        study_uid: Study UID (generated if None)
        series_uid: Series UID (generated if None)
        sop_uid: SOP UID (generated if None)
        pixel_value: Fill value for pixel data

    Returns:
        DICOM file as bytes
    """
    study_uid = study_uid or generate_uid()
    series_uid = series_uid or generate_uid()
    sop_uid = sop_uid or generate_uid()

    file_meta = Dataset()
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    file_meta.MediaStorageSOPInstanceUID = sop_uid
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    file_meta.ImplementationClassUID = "1.2.826.0.1.3680043.8.498.1"

    ds = FileDataset(
        filename_or_obj=BytesIO(),
        dataset=Dataset(),
        file_meta=file_meta,
        preamble=b"\x00" * 128,
    )

    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    ds.SOPInstanceUID = sop_uid
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = series_uid
    ds.PatientID = patient_id
    ds.PatientName = "Frame^Test"
    ds.StudyDate = "20240101"
    ds.StudyTime = "120000"
    ds.Modality = "CT"
    ds.AccessionNumber = "ACC-FRAME-001"
    ds.SeriesNumber = 1
    ds.InstanceNumber = 1

    ds.Rows = rows
    ds.Columns = cols
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.PixelRepresentation = 0
    ds.PixelData = np.full((rows, cols), pixel_value, dtype=np.uint16).tobytes()

    buffer = BytesIO()
    ds.save_as(buffer, write_like_original=False)
    return buffer.getvalue()


def create_multiframe_ct(
    patient_id: str = "MULTIFRAME-TEST",
    num_frames: int = 5,
    rows: int = 64,
    cols: int = 64,
    study_uid: str | None = None,
    series_uid: str | None = None,
    sop_uid: str | None = None,
) -> bytes:
    """Create a multiframe CT DICOM with distinct pixel data per frame.

    Each frame has a unique fill value (frame_index * 10) to allow
    verifying that individual frames are correctly extracted.

    Returns:
        DICOM file as bytes
    """
    study_uid = study_uid or generate_uid()
    series_uid = series_uid or generate_uid()
    sop_uid = sop_uid or generate_uid()

    file_meta = Dataset()
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    file_meta.MediaStorageSOPInstanceUID = sop_uid
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    file_meta.ImplementationClassUID = "1.2.826.0.1.3680043.8.498.1"

    ds = FileDataset(
        filename_or_obj=BytesIO(),
        dataset=Dataset(),
        file_meta=file_meta,
        preamble=b"\x00" * 128,
    )

    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    ds.SOPInstanceUID = sop_uid
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = series_uid
    ds.PatientID = patient_id
    ds.PatientName = "Multiframe^Test"
    ds.StudyDate = "20240101"
    ds.StudyTime = "120000"
    ds.Modality = "CT"
    ds.AccessionNumber = "ACC-MULTI-001"
    ds.SeriesNumber = 1
    ds.InstanceNumber = 1
    ds.NumberOfFrames = num_frames

    ds.Rows = rows
    ds.Columns = cols
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.PixelRepresentation = 0

    # Each frame gets a distinct fill value
    pixel_array = np.zeros((num_frames, rows, cols), dtype=np.uint16)
    for i in range(num_frames):
        pixel_array[i] = (i + 1) * 10
    ds.PixelData = pixel_array.tobytes()

    buffer = BytesIO()
    ds.save_as(buffer, write_like_original=False)
    return buffer.getvalue()


def create_rgb_dicom(
    patient_id: str = "RGB-TEST",
    rows: int = 64,
    cols: int = 64,
    study_uid: str | None = None,
    series_uid: str | None = None,
    sop_uid: str | None = None,
) -> bytes:
    """Create an RGB DICOM image (3 samples per pixel).

    Creates a simple color image with red, green, and blue channels.

    Returns:
        DICOM file as bytes
    """
    study_uid = study_uid or generate_uid()
    series_uid = series_uid or generate_uid()
    sop_uid = sop_uid or generate_uid()

    file_meta = Dataset()
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.7"  # Secondary Capture
    file_meta.MediaStorageSOPInstanceUID = sop_uid
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    file_meta.ImplementationClassUID = "1.2.826.0.1.3680043.8.498.1"

    ds = FileDataset(
        filename_or_obj=BytesIO(),
        dataset=Dataset(),
        file_meta=file_meta,
        preamble=b"\x00" * 128,
    )

    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.7"
    ds.SOPInstanceUID = sop_uid
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = series_uid
    ds.PatientID = patient_id
    ds.PatientName = "RGB^Test"
    ds.StudyDate = "20240101"
    ds.StudyTime = "120000"
    ds.Modality = "OT"
    ds.AccessionNumber = "ACC-RGB-001"
    ds.SeriesNumber = 1
    ds.InstanceNumber = 1

    ds.Rows = rows
    ds.Columns = cols
    ds.BitsAllocated = 8
    ds.BitsStored = 8
    ds.HighBit = 7
    ds.SamplesPerPixel = 3
    ds.PhotometricInterpretation = "RGB"
    ds.PixelRepresentation = 0
    ds.PlanarConfiguration = 0  # Color-by-pixel (R1G1B1 R2G2B2 ...)

    # Create a simple color pattern
    pixel_array = np.zeros((rows, cols, 3), dtype=np.uint8)
    pixel_array[:, : rows // 3, 0] = 255  # Red band
    pixel_array[:, rows // 3 : 2 * rows // 3, 1] = 255  # Green band
    pixel_array[:, 2 * rows // 3 :, 2] = 255  # Blue band
    ds.PixelData = pixel_array.tobytes()

    buffer = BytesIO()
    ds.save_as(buffer, write_like_original=False)
    return buffer.getvalue()


def create_ct_with_windowing(
    patient_id: str = "WINDOW-TEST",
    rows: int = 64,
    cols: int = 64,
    window_center: float = 40.0,
    window_width: float = 400.0,
    study_uid: str | None = None,
    series_uid: str | None = None,
    sop_uid: str | None = None,
) -> bytes:
    """Create a CT DICOM with windowing attributes.

    Returns:
        DICOM file as bytes
    """
    study_uid = study_uid or generate_uid()
    series_uid = series_uid or generate_uid()
    sop_uid = sop_uid or generate_uid()

    file_meta = Dataset()
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    file_meta.MediaStorageSOPInstanceUID = sop_uid
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    file_meta.ImplementationClassUID = "1.2.826.0.1.3680043.8.498.1"

    ds = FileDataset(
        filename_or_obj=BytesIO(),
        dataset=Dataset(),
        file_meta=file_meta,
        preamble=b"\x00" * 128,
    )

    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    ds.SOPInstanceUID = sop_uid
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = series_uid
    ds.PatientID = patient_id
    ds.PatientName = "Window^Test"
    ds.StudyDate = "20240101"
    ds.StudyTime = "120000"
    ds.Modality = "CT"
    ds.AccessionNumber = "ACC-WINDOW-001"
    ds.SeriesNumber = 1
    ds.InstanceNumber = 1

    ds.Rows = rows
    ds.Columns = cols
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.PixelRepresentation = 0
    ds.WindowCenter = window_center
    ds.WindowWidth = window_width

    # Create a gradient to test windowing
    pixel_array = np.linspace(0, 500, rows * cols, dtype=np.uint16).reshape(rows, cols)
    ds.PixelData = pixel_array.tobytes()

    buffer = BytesIO()
    ds.save_as(buffer, write_like_original=False)
    return buffer.getvalue()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Fixtures
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.fixture
def patch_dicomweb_storage(client, monkeypatch, tmp_path):
    """Patch dicomweb module's DICOM_STORAGE_DIR and frame_cache to use test storage.

    The dicomweb module reads DICOM_STORAGE_DIR at import time, so the
    module-level constant and frame_cache need to be monkeypatched to
    point at the test storage directory.

    Returns:
        Tuple of (client, storage_dir_path)
    """
    import app.routers.dicomweb as dicomweb_module
    from app.services.frame_cache import FrameCache

    # The test client fixture already sets up storage in tmp_path/dicom_storage
    # We need to find that directory
    storage_dir = tmp_path / "dicom_storage"
    storage_path = dicomweb_module.Path(str(storage_dir))

    monkeypatch.setattr(dicomweb_module, "DICOM_STORAGE_DIR", storage_path)
    monkeypatch.setattr(dicomweb_module, "frame_cache", FrameCache(storage_path))

    return client, storage_path


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  1. Frame Retrieval (8 tests)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_retrieve_single_frame(patch_dicomweb_storage):
    """GET .../frames/1 retrieves the first frame from a single-frame instance."""
    client, storage_dir = patch_dicomweb_storage

    dcm = create_ct_with_pixel_data(patient_id="FRAME-SINGLE-001", pixel_value=42)
    study_uid, series_uid, sop_uid = store_dicom_for_frames(client, dcm, storage_dir)

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/frames/1",
        headers={"Accept": "application/octet-stream"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert len(response.content) > 0


def test_retrieve_multiple_frames_comma_separated(patch_dicomweb_storage):
    """GET .../frames/1,3,5 retrieves multiple frames from a multiframe instance."""
    client, storage_dir = patch_dicomweb_storage

    dcm = create_multiframe_ct(patient_id="FRAME-MULTI-001", num_frames=10)
    study_uid, series_uid, sop_uid = store_dicom_for_frames(client, dcm, storage_dir)

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/frames/1,3,5",
        headers={"Accept": "multipart/related; type=application/octet-stream"},
    )

    assert response.status_code == 200
    # Multiple frames should come back as multipart/related
    assert "multipart/related" in response.headers["content-type"]

    # Verify the response contains the boundary and frame data
    ct = response.headers["content-type"]
    assert "application/octet-stream" in ct


def test_retrieve_all_frames_from_multiframe(patch_dicomweb_storage):
    """Retrieve all frames from a multiframe instance individually."""
    client, storage_dir = patch_dicomweb_storage

    num_frames = 5
    dcm = create_multiframe_ct(patient_id="FRAME-ALL-001", num_frames=num_frames)
    study_uid, series_uid, sop_uid = store_dicom_for_frames(client, dcm, storage_dir)

    # Retrieve each frame individually
    for frame_num in range(1, num_frames + 1):
        response = client.get(
            f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/frames/{frame_num}",
            headers={"Accept": "application/octet-stream"},
        )
        assert response.status_code == 200, f"Frame {frame_num} retrieval failed"
        assert len(response.content) > 0, f"Frame {frame_num} is empty"


def test_frame_response_multipart_related(patch_dicomweb_storage):
    """Multi-frame request returns correct multipart/related content-type."""
    client, storage_dir = patch_dicomweb_storage

    dcm = create_multiframe_ct(patient_id="FRAME-MULTIPART-001", num_frames=5)
    study_uid, series_uid, sop_uid = store_dicom_for_frames(client, dcm, storage_dir)

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/frames/1,2",
        headers={"Accept": "multipart/related; type=application/octet-stream"},
    )

    assert response.status_code == 200

    ct = response.headers["content-type"]
    assert "multipart/related" in ct
    assert "application/octet-stream" in ct
    assert "boundary=" in ct

    # Verify boundary markers exist in body
    boundary_match = re.search(r"boundary=([^\s;]+)", ct)
    assert boundary_match is not None
    boundary = boundary_match.group(1)
    assert f"--{boundary}".encode() in response.content
    assert f"--{boundary}--".encode() in response.content


def test_frame_boundary_parsing(patch_dicomweb_storage):
    """Parse individual frames from multipart/related response."""
    client, storage_dir = patch_dicomweb_storage

    dcm = create_multiframe_ct(patient_id="FRAME-PARSE-001", num_frames=5, rows=32, cols=32)
    study_uid, series_uid, sop_uid = store_dicom_for_frames(client, dcm, storage_dir)

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/frames/1,2,3",
        headers={"Accept": "multipart/related; type=application/octet-stream"},
    )

    assert response.status_code == 200

    # Extract boundary
    ct = response.headers["content-type"]
    boundary_match = re.search(r"boundary=([^\s;]+)", ct)
    assert boundary_match is not None
    boundary = boundary_match.group(1)

    # Split by boundary and count parts with data
    boundary_bytes = f"--{boundary}".encode()
    parts = response.content.split(boundary_bytes)

    # Filter out empty parts and the closing delimiter
    data_parts = []
    for part in parts:
        if not part or part.strip() == b"" or part.strip() == b"--":
            continue
        # Each part has headers separated by \r\n\r\n
        if b"\r\n\r\n" in part:
            _, data = part.split(b"\r\n\r\n", 1)
            data = data.rstrip(b"\r\n")
            if data:
                data_parts.append(data)

    # We requested 3 frames, so should get 3 parts
    assert len(data_parts) == 3


def test_frame_out_of_range_returns_error(patch_dicomweb_storage):
    """Frame 999 on a 10-frame instance returns an error.

    Note: The implementation returns 404 (not 400) for out-of-range frames.
    This is because the frame cache raises ValueError with "out of range"
    which the endpoint maps to 404.
    TODO: Azure DICOM service returns 400 for out-of-range frames.
    """
    client, storage_dir = patch_dicomweb_storage

    dcm = create_multiframe_ct(patient_id="FRAME-OOR-001", num_frames=10)
    study_uid, series_uid, sop_uid = store_dicom_for_frames(client, dcm, storage_dir)

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/frames/999",
        headers={"Accept": "application/octet-stream"},
    )

    # Implementation returns 404 for out-of-range (see dicomweb.py line 606-607)
    # TODO: Azure DICOM Service returns 400 for out-of-range frame numbers
    assert response.status_code == 404
    assert "out of range" in response.json()["detail"].lower()


def test_frame_from_single_frame_image(patch_dicomweb_storage):
    """frames/1 works on a non-multiframe (single-frame) DICOM instance."""
    client, storage_dir = patch_dicomweb_storage

    dcm = create_ct_with_pixel_data(
        patient_id="FRAME-SINGLE-002", rows=64, cols=64, pixel_value=200
    )
    study_uid, series_uid, sop_uid = store_dicom_for_frames(client, dcm, storage_dir)

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/frames/1",
        headers={"Accept": "application/octet-stream"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"

    # Single frame of 64x64 uint16 = 8192 bytes
    expected_size = 64 * 64 * 2
    assert len(response.content) == expected_size


def test_retrieve_frames_binary_data_intact(patch_dicomweb_storage):
    """Pixel data retrieved via frames endpoint is uncorrupted."""
    client, storage_dir = patch_dicomweb_storage

    rows, cols = 32, 32
    pixel_value = 12345

    dcm = create_ct_with_pixel_data(
        patient_id="FRAME-INTEGRITY-001", rows=rows, cols=cols, pixel_value=pixel_value
    )
    study_uid, series_uid, sop_uid = store_dicom_for_frames(client, dcm, storage_dir)

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/frames/1",
        headers={"Accept": "application/octet-stream"},
    )

    assert response.status_code == 200

    # Reconstruct numpy array from the raw frame bytes
    frame_array = np.frombuffer(response.content, dtype=np.uint16).reshape(rows, cols)

    # All values should match the fill value
    assert np.all(frame_array == pixel_value), (
        f"Expected all pixels to be {pixel_value}, "
        f"got min={frame_array.min()}, max={frame_array.max()}"
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  2. Rendered Endpoints (12 tests)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_render_instance_as_jpeg(patch_dicomweb_storage):
    """GET .../rendered with Accept: image/jpeg returns a JPEG image."""
    client, storage_dir = patch_dicomweb_storage

    dcm = create_ct_with_pixel_data(patient_id="RENDER-JPEG-001")
    study_uid, series_uid, sop_uid = store_dicom_for_frames(client, dcm, storage_dir)

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/rendered",
        headers={"Accept": "image/jpeg"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"

    # Verify it's valid JPEG (starts with FF D8)
    assert response.content[:2] == b"\xff\xd8"

    # Verify it can be opened by PIL
    img = Image.open(io.BytesIO(response.content))
    assert img.format == "JPEG"


def test_render_instance_as_png(patch_dicomweb_storage):
    """GET .../rendered with Accept: image/png returns a PNG image."""
    client, storage_dir = patch_dicomweb_storage

    dcm = create_ct_with_pixel_data(patient_id="RENDER-PNG-001")
    study_uid, series_uid, sop_uid = store_dicom_for_frames(client, dcm, storage_dir)

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/rendered",
        headers={"Accept": "image/png"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"

    # Verify PNG signature
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"

    # Verify it can be opened by PIL
    img = Image.open(io.BytesIO(response.content))
    assert img.format == "PNG"


def test_render_frame_as_jpeg(patch_dicomweb_storage):
    """GET .../frames/2/rendered returns a rendered JPEG of a specific frame."""
    client, storage_dir = patch_dicomweb_storage

    dcm = create_multiframe_ct(patient_id="RENDER-FRAME-JPEG-001", num_frames=3)
    study_uid, series_uid, sop_uid = store_dicom_for_frames(client, dcm, storage_dir)

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/frames/2/rendered",
        headers={"Accept": "image/jpeg"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content[:2] == b"\xff\xd8"


def test_render_frame_as_png(patch_dicomweb_storage):
    """GET .../frames/1/rendered with Accept: image/png returns PNG."""
    client, storage_dir = patch_dicomweb_storage

    dcm = create_multiframe_ct(patient_id="RENDER-FRAME-PNG-001", num_frames=3)
    study_uid, series_uid, sop_uid = store_dicom_for_frames(client, dcm, storage_dir)

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/frames/1/rendered",
        headers={"Accept": "image/png"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_with_quality_parameter(patch_dicomweb_storage):
    """GET .../rendered?quality=85 respects the quality parameter."""
    client, storage_dir = patch_dicomweb_storage

    dcm = create_ct_with_pixel_data(
        patient_id="RENDER-QUALITY-001", rows=128, cols=128, pixel_value=150
    )
    study_uid, series_uid, sop_uid = store_dicom_for_frames(client, dcm, storage_dir)

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/rendered?quality=85",
        headers={"Accept": "image/jpeg"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"

    # Verify it's a valid JPEG
    img = Image.open(io.BytesIO(response.content))
    assert img.format == "JPEG"


def test_render_with_window_center_width(patch_dicomweb_storage):
    """GET .../rendered on an instance with WindowCenter/WindowWidth in DICOM tags.

    Note: The current implementation reads windowing from DICOM attributes
    embedded in the file, not from query parameters. Query-parameter-based
    windowing (?WindowCenter=40&WindowWidth=400) is not yet supported.
    TODO: Add support for windowing query parameters per Azure DICOM spec.
    """
    client, storage_dir = patch_dicomweb_storage

    # Create DICOM with windowing attributes embedded
    dcm = create_ct_with_windowing(
        patient_id="RENDER-WINDOW-001", window_center=250.0, window_width=200.0
    )
    study_uid, series_uid, sop_uid = store_dicom_for_frames(client, dcm, storage_dir)

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/rendered",
        headers={"Accept": "image/jpeg"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"

    # Verify it renders successfully (windowing is applied from DICOM tags)
    img = Image.open(io.BytesIO(response.content))
    assert img.format == "JPEG"

    # With windowing, the image should have a range of values (not all black)
    img_array = np.array(img)
    assert img_array.max() > 0, "Image appears all-black; windowing may not be applied"


def test_render_rgb_dicom_preserves_color(patch_dicomweb_storage):
    """Rendering an RGB DICOM produces a color image."""
    client, storage_dir = patch_dicomweb_storage

    dcm = create_rgb_dicom(patient_id="RENDER-RGB-001", rows=64, cols=64)
    study_uid, series_uid, sop_uid = store_dicom_for_frames(client, dcm, storage_dir)

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/rendered",
        headers={"Accept": "image/png"},
    )

    assert response.status_code == 200

    # Open as image and verify it has color (RGB mode)
    img = Image.open(io.BytesIO(response.content))
    assert img.mode == "RGB", f"Expected RGB mode, got {img.mode}"

    # Verify the image has actual color content (not all grayscale)
    img_array = np.array(img)
    # Check that R, G, B channels are not identical (color image)
    # At least one channel should differ from another
    r_channel = img_array[:, :, 0]
    g_channel = img_array[:, :, 1]
    b_channel = img_array[:, :, 2]

    channels_differ = not np.array_equal(r_channel, g_channel) or not np.array_equal(
        g_channel, b_channel
    )
    assert channels_differ, "RGB image rendered as grayscale (all channels identical)"


def test_render_multiframe_first_frame_default(patch_dicomweb_storage):
    """GET .../rendered on a multiframe instance renders frame 1 by default."""
    client, storage_dir = patch_dicomweb_storage

    dcm = create_multiframe_ct(patient_id="RENDER-DEFAULT-FRAME-001", num_frames=5)
    study_uid, series_uid, sop_uid = store_dicom_for_frames(client, dcm, storage_dir)

    # Get rendered instance (no frame specified - should default to frame 1)
    response_default = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/rendered",
        headers={"Accept": "image/png"},
    )

    assert response_default.status_code == 200

    # Get explicitly rendered frame 1
    response_frame1 = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/frames/1/rendered",
        headers={"Accept": "image/png"},
    )

    assert response_frame1.status_code == 200

    # Both should produce identical images
    img_default = Image.open(io.BytesIO(response_default.content))
    img_frame1 = Image.open(io.BytesIO(response_frame1.content))

    assert np.array_equal(
        np.array(img_default), np.array(img_frame1)
    ), "Default rendered image differs from explicitly requested frame 1"


def test_render_invalid_quality_returns_422(patch_dicomweb_storage):
    """quality=101 returns a validation error.

    Note: FastAPI's Query(ge=1, le=100) validation returns 422, not 400.
    The quality parameter is validated at the FastAPI level before reaching
    the endpoint handler.
    TODO: Azure DICOM Service would return 400 for invalid quality.
    """
    client, storage_dir = patch_dicomweb_storage

    dcm = create_ct_with_pixel_data(patient_id="RENDER-BAD-QUALITY-001")
    store_dicom(client, dcm)

    # Use UIDs from the factory (we don't need the frame layout for this test
    # since the request will be rejected at validation before file access)
    ds = pydicom.dcmread(BytesIO(dcm), force=True)
    study_uid = str(ds.StudyInstanceUID)
    series_uid = str(ds.SeriesInstanceUID)
    sop_uid = str(ds.SOPInstanceUID)

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/rendered?quality=101",
        headers={"Accept": "image/jpeg"},
    )

    # FastAPI validates quality with ge=1, le=100 and returns 422 for violations
    # TODO: Azure DICOM Service returns 400 for invalid quality parameter
    assert response.status_code == 422


def test_render_unsupported_format_returns_406(patch_dicomweb_storage):
    """Accept: image/gif returns 406 Not Acceptable per DICOMweb spec.

    Per DICOMweb specification, rendered endpoints must validate Accept headers
    and return 406 for unsupported image formats.
    """
    client, storage_dir = patch_dicomweb_storage

    dcm = create_ct_with_pixel_data(patient_id="RENDER-UNSUPPORTED-001")
    study_uid, series_uid, sop_uid = store_dicom_for_frames(client, dcm, storage_dir)

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/rendered",
        headers={"Accept": "image/gif"},
    )

    # Should return 406 Not Acceptable per DICOMweb spec
    assert response.status_code == 406


def test_rendered_image_dimensions_match(patch_dicomweb_storage):
    """Rendered image has correct width/height matching DICOM Rows/Columns."""
    client, storage_dir = patch_dicomweb_storage

    rows, cols = 48, 96

    dcm = create_ct_with_pixel_data(patient_id="RENDER-DIMS-001", rows=rows, cols=cols)
    study_uid, series_uid, sop_uid = store_dicom_for_frames(client, dcm, storage_dir)

    response = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/rendered",
        headers={"Accept": "image/png"},
    )

    assert response.status_code == 200

    img = Image.open(io.BytesIO(response.content))
    # PIL: size is (width, height) = (columns, rows)
    assert img.size == (cols, rows), f"Expected ({cols}, {rows}), got {img.size}"


def test_rendered_jpeg_quality_affects_size(patch_dicomweb_storage):
    """Higher JPEG quality produces a larger file (for non-trivial images)."""
    client, storage_dir = patch_dicomweb_storage

    # Use a larger image with varied content for quality to make a visible difference
    dcm = create_ct_with_windowing(
        patient_id="RENDER-QUALITY-SIZE-001",
        rows=128,
        cols=128,
        window_center=250.0,
        window_width=500.0,
    )
    study_uid, series_uid, sop_uid = store_dicom_for_frames(client, dcm, storage_dir)

    # Get low quality
    response_low = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/rendered?quality=10",
        headers={"Accept": "image/jpeg"},
    )
    assert response_low.status_code == 200

    # Get high quality
    response_high = client.get(
        f"/v2/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/rendered?quality=100",
        headers={"Accept": "image/jpeg"},
    )
    assert response_high.status_code == 200

    # Higher quality should produce a larger file
    assert len(response_high.content) > len(response_low.content), (
        f"High quality ({len(response_high.content)} bytes) should be larger "
        f"than low quality ({len(response_low.content)} bytes)"
    )
