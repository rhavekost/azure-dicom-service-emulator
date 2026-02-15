"""Generate sample DICOM files for testing."""

from pathlib import Path
from .factories import DicomFactory


def create_fixture_files():
    """Create all DICOM fixture files."""
    fixtures_dir = Path(__file__).parent / "dicom"

    # Valid DICOM files
    valid_dir = fixtures_dir / "valid"
    valid_dir.mkdir(parents=True, exist_ok=True)

    # CT minimal (no pixel data)
    ct_minimal = DicomFactory.create_ct_image(
        patient_id="VALID-001",
        patient_name="Valid^CT^Minimal",
        with_pixel_data=False,
    )
    (valid_dir / "ct_minimal.dcm").write_bytes(ct_minimal)

    # CT with pixels
    ct_with_pixels = DicomFactory.create_ct_image(
        patient_id="VALID-002",
        patient_name="Valid^CT^Pixels",
        with_pixel_data=True,
    )
    (valid_dir / "ct_with_pixels.dcm").write_bytes(ct_with_pixels)

    # MRI T1
    mri_t1 = DicomFactory.create_mri_image(
        patient_id="VALID-003",
        patient_name="Valid^MRI^T1",
        modality="MR",
        with_pixel_data=True,
    )
    (valid_dir / "mri_t1.dcm").write_bytes(mri_t1)

    # MRI T2
    mri_t2 = DicomFactory.create_mri_image(
        patient_id="VALID-004",
        patient_name="Valid^MRI^T2",
        modality="MR",
        with_pixel_data=True,
    )
    (valid_dir / "mri_t2.dcm").write_bytes(mri_t2)

    # Multiframe CT
    multiframe = DicomFactory.create_multiframe_ct(
        patient_id="VALID-005",
        num_frames=10,
        with_pixel_data=True,
    )
    (valid_dir / "multiframe_ct.dcm").write_bytes(multiframe)

    # US (Ultrasound)
    us = DicomFactory.create_ct_image(
        patient_id="VALID-006",
        patient_name="Valid^US",
        modality="US",
        with_pixel_data=True,
    )
    (valid_dir / "us.dcm").write_bytes(us)

    # XA (X-Ray Angiography)
    xa = DicomFactory.create_ct_image(
        patient_id="VALID-007",
        patient_name="Valid^XA",
        modality="XA",
        with_pixel_data=True,
    )
    (valid_dir / "xa.dcm").write_bytes(xa)

    # Edge cases
    edge_dir = fixtures_dir / "edge_cases"
    edge_dir.mkdir(parents=True, exist_ok=True)

    # Empty patient name
    empty_name = DicomFactory.create_ct_image(
        patient_id="EDGE-001",
        patient_name="",
        with_pixel_data=False,
    )
    (edge_dir / "empty_patient_name.dcm").write_bytes(empty_name)

    # Special characters in patient name
    special_chars = DicomFactory.create_ct_image(
        patient_id="EDGE-002",
        patient_name="O'Brien^John=Mary^Dr.^Jr.",
        with_pixel_data=False,
    )
    (edge_dir / "special_chars_name.dcm").write_bytes(special_chars)

    # Very long study description
    long_desc = DicomFactory.create_ct_image(
        patient_id="EDGE-003",
        patient_name="Edge^LongDesc",
        with_pixel_data=False,
    )
    # Would need to modify factory to support this - placeholder for now
    (edge_dir / "long_description.dcm").write_bytes(long_desc)

    # Implicit VR Little Endian
    from pydicom.uid import ImplicitVRLittleEndian
    implicit_vr = DicomFactory.create_ct_image(
        patient_id="EDGE-004",
        patient_name="Edge^ImplicitVR",
        transfer_syntax=ImplicitVRLittleEndian,
        with_pixel_data=False,
    )
    (edge_dir / "implicit_vr.dcm").write_bytes(implicit_vr)

    # Invalid DICOM files
    invalid_dir = fixtures_dir / "invalid"
    invalid_dir.mkdir(parents=True, exist_ok=True)

    # Missing SOPInstanceUID
    missing_sop = DicomFactory.create_invalid_dicom(
        missing_tags=["SOPInstanceUID"]
    )
    (invalid_dir / "missing_sop_uid.dcm").write_bytes(missing_sop)

    # Missing StudyInstanceUID
    missing_study = DicomFactory.create_invalid_dicom(
        missing_tags=["StudyInstanceUID"]
    )
    (invalid_dir / "missing_study_uid.dcm").write_bytes(missing_study)

    # Missing PatientID
    missing_patient = DicomFactory.create_invalid_dicom(
        missing_tags=["PatientID"]
    )
    (invalid_dir / "missing_patient_id.dcm").write_bytes(missing_patient)

    print(f"✅ Created {len(list(valid_dir.glob('*.dcm')))} valid DICOM files")
    print(f"✅ Created {len(list(edge_dir.glob('*.dcm')))} edge case DICOM files")
    print(f"✅ Created {len(list(invalid_dir.glob('*.dcm')))} invalid DICOM files")


if __name__ == "__main__":
    create_fixture_files()
