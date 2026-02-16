from pathlib import Path

import numpy as np
import pydicom
import pytest
from pydicom.dataset import Dataset, FileDataset
from pydicom.uid import ExplicitVRLittleEndian

from app.services.frame_extraction import extract_frames

pytestmark = pytest.mark.unit


def create_synthetic_dicom(path: Path, rows: int = 64, columns: int = 64, frames: int = 1) -> None:
    """
    Create a synthetic DICOM file with pixel data.

    Args:
        path: Path to save DICOM file
        rows: Number of rows in image
        columns: Number of columns in image
        frames: Number of frames (1 for single frame, >1 for multi-frame)
    """
    # Create minimal DICOM dataset
    file_meta = Dataset()
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"  # CT Image Storage
    file_meta.MediaStorageSOPInstanceUID = pydicom.uid.generate_uid()
    file_meta.ImplementationClassUID = pydicom.uid.generate_uid()

    ds = FileDataset(str(path), {}, file_meta=file_meta, preamble=b"\0" * 128)

    # Required DICOM attributes
    ds.PatientName = "Test^Patient"
    ds.PatientID = "TEST001"
    ds.StudyInstanceUID = pydicom.uid.generate_uid()
    ds.SeriesInstanceUID = pydicom.uid.generate_uid()
    ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
    ds.SOPClassUID = file_meta.MediaStorageSOPClassUID
    ds.Modality = "CT"

    # Image attributes
    ds.Rows = rows
    ds.Columns = columns
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 0

    # Generate pixel data
    if frames > 1:
        ds.NumberOfFrames = frames
        pixel_data = np.random.randint(0, 1000, (frames, rows, columns), dtype=np.uint16)
    else:
        pixel_data = np.random.randint(0, 1000, (rows, columns), dtype=np.uint16)

    ds.PixelData = pixel_data.tobytes()

    # Save file
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    ds.save_as(path, write_like_original=False)


def test_extract_single_frame(tmp_path):
    """Extract single frame from DICOM instance."""
    dcm_path = tmp_path / "test_single.dcm"
    create_synthetic_dicom(dcm_path, rows=64, columns=64, frames=1)

    frames = extract_frames(dcm_path, tmp_path / "frames")

    assert len(frames) == 1
    assert frames[0].exists()
    assert frames[0].stat().st_size > 0
    # Verify frame data size (64 * 64 * 2 bytes = 8192)
    assert frames[0].stat().st_size == 8192


def test_extract_multi_frame(tmp_path):
    """Extract multiple frames from DICOM instance."""
    dcm_path = tmp_path / "test_multi.dcm"
    create_synthetic_dicom(dcm_path, rows=64, columns=64, frames=5)

    frames = extract_frames(dcm_path, tmp_path / "frames")

    assert len(frames) == 5
    for i, frame_path in enumerate(frames, start=1):
        assert frame_path.exists()
        assert frame_path.name == f"{i}.raw"
        assert frame_path.stat().st_size == 8192  # 64 * 64 * 2 bytes


def test_extract_frames_no_pixel_data(tmp_path):
    """Raise error if instance has no pixel data."""
    dcm_path = tmp_path / "no_pixels.dcm"

    # Create DICOM without pixel data
    file_meta = Dataset()
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    file_meta.MediaStorageSOPInstanceUID = pydicom.uid.generate_uid()
    file_meta.ImplementationClassUID = pydicom.uid.generate_uid()

    ds = FileDataset(str(dcm_path), {}, file_meta=file_meta, preamble=b"\0" * 128)
    ds.PatientName = "Test^Patient"
    ds.PatientID = "TEST001"
    ds.StudyInstanceUID = pydicom.uid.generate_uid()
    ds.SeriesInstanceUID = pydicom.uid.generate_uid()
    ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
    ds.SOPClassUID = file_meta.MediaStorageSOPClassUID
    ds.Modality = "SR"  # Structured Report - no pixel data

    ds.is_little_endian = True
    ds.is_implicit_VR = False
    ds.save_as(dcm_path, write_like_original=False)

    with pytest.raises(ValueError, match="decodable pixel data"):
        extract_frames(dcm_path, tmp_path / "frames")


def test_extract_frames_invalid_file(tmp_path):
    """Raise error if file is not valid DICOM."""
    invalid_path = tmp_path / "invalid.dcm"
    invalid_path.write_text("Not a DICOM file")

    with pytest.raises(ValueError, match="Invalid DICOM file"):
        extract_frames(invalid_path, tmp_path / "frames")


def test_extract_frames_missing_file(tmp_path):
    """Raise error if file does not exist."""
    missing_path = tmp_path / "missing.dcm"

    with pytest.raises(ValueError, match="Cannot read DICOM file"):
        extract_frames(missing_path, tmp_path / "frames")
