"""Test data factories for creating DICOM instances."""

import uuid
from datetime import datetime
from io import BytesIO

from pydicom.dataset import Dataset, FileDataset
from pydicom.uid import UID, ExplicitVRLittleEndian, generate_uid


class DicomFactory:
    """Factory for creating test DICOM files."""

    @staticmethod
    def create_ct_image(
        patient_id: str = "TEST-001",
        patient_name: str = "Test^Patient",
        study_uid: str | None = None,
        series_uid: str | None = None,
        sop_uid: str | None = None,
        accession_number: str | None = None,
        study_date: str | None = None,
        modality: str = "CT",
        with_pixel_data: bool = False,
        transfer_syntax: str | UID = ExplicitVRLittleEndian,
    ) -> bytes:
        """
        Create a minimal CT DICOM instance.

        Args:
            patient_id: Patient identifier
            patient_name: Patient name in DICOM format (Last^First)
            study_uid: Study Instance UID (generated if None)
            series_uid: Series Instance UID (generated if None)
            sop_uid: SOP Instance UID (generated if None)
            accession_number: Accession number (generated if None)
            study_date: Study date YYYYMMDD (today if None)
            modality: DICOM modality
            with_pixel_data: Include dummy pixel data
            transfer_syntax: Transfer syntax UID

        Returns:
            DICOM file as bytes
        """
        study_uid = study_uid or generate_uid()
        series_uid = series_uid or generate_uid()
        sop_uid = sop_uid or generate_uid()
        accession_number = accession_number or f"ACC-{uuid.uuid4().hex[:8]}"
        study_date = study_date or datetime.now().strftime("%Y%m%d")

        # File meta information
        file_meta = Dataset()
        file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"  # CT Image
        file_meta.MediaStorageSOPInstanceUID = sop_uid
        file_meta.TransferSyntaxUID = transfer_syntax
        file_meta.ImplementationClassUID = "1.2.826.0.1.3680043.8.498.1"

        # Create dataset
        ds = FileDataset(
            filename_or_obj=BytesIO(),
            dataset=Dataset(),
            file_meta=file_meta,
            preamble=b"\x00" * 128,
        )

        # Required DICOM attributes
        ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
        ds.SOPInstanceUID = sop_uid
        ds.StudyInstanceUID = study_uid
        ds.SeriesInstanceUID = series_uid
        ds.PatientID = patient_id
        ds.PatientName = patient_name
        ds.StudyDate = study_date
        ds.StudyTime = "120000"
        ds.Modality = modality
        ds.AccessionNumber = accession_number
        ds.StudyDescription = "Test Study"
        ds.SeriesDescription = "Test Series"
        ds.SeriesNumber = 1
        ds.InstanceNumber = 1

        # Add pixel data if requested
        if with_pixel_data:
            import numpy as np

            ds.Rows = 512
            ds.Columns = 512
            ds.BitsAllocated = 16
            ds.BitsStored = 16
            ds.HighBit = 15
            ds.SamplesPerPixel = 1
            ds.PhotometricInterpretation = "MONOCHROME2"
            ds.PixelRepresentation = 0
            ds.PixelData = np.zeros((512, 512), dtype=np.uint16).tobytes()

        # Save to bytes
        buffer = BytesIO()
        ds.save_as(buffer, write_like_original=False)
        return buffer.getvalue()

    @staticmethod
    def create_mri_image(
        patient_id: str = "TEST-002",
        patient_name: str = "MRI^Patient",
        study_uid: str | None = None,
        series_uid: str | None = None,
        sop_uid: str | None = None,
        modality: str = "MR",
        with_pixel_data: bool = False,
    ) -> bytes:
        """
        Create a minimal MRI DICOM instance.

        Args:
            patient_id: Patient identifier
            patient_name: Patient name in DICOM format (Last^First)
            study_uid: Study Instance UID (generated if None)
            series_uid: Series Instance UID (generated if None)
            sop_uid: SOP Instance UID (generated if None)
            modality: DICOM modality (default MR)
            with_pixel_data: Include dummy pixel data (256x256)

        Returns:
            DICOM file as bytes
        """
        study_uid = study_uid or generate_uid()
        series_uid = series_uid or generate_uid()
        sop_uid = sop_uid or generate_uid()

        file_meta = Dataset()
        file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.4"  # MR Image
        file_meta.MediaStorageSOPInstanceUID = sop_uid
        file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
        file_meta.ImplementationClassUID = "1.2.826.0.1.3680043.8.498.1"

        ds = FileDataset(
            filename_or_obj=BytesIO(),
            dataset=Dataset(),
            file_meta=file_meta,
            preamble=b"\x00" * 128,
        )

        ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.4"
        ds.SOPInstanceUID = sop_uid
        ds.StudyInstanceUID = study_uid
        ds.SeriesInstanceUID = series_uid
        ds.PatientID = patient_id
        ds.PatientName = patient_name
        ds.StudyDate = datetime.now().strftime("%Y%m%d")
        ds.StudyTime = "140000"
        ds.Modality = modality
        ds.AccessionNumber = f"ACC-{uuid.uuid4().hex[:8]}"
        ds.StudyDescription = "MRI Test Study"
        ds.SeriesDescription = "T1 Weighted"
        ds.SeriesNumber = 1
        ds.InstanceNumber = 1

        if with_pixel_data:
            import numpy as np

            ds.Rows = 256
            ds.Columns = 256
            ds.BitsAllocated = 16
            ds.BitsStored = 16
            ds.HighBit = 15
            ds.SamplesPerPixel = 1
            ds.PhotometricInterpretation = "MONOCHROME2"
            ds.PixelRepresentation = 0
            ds.PixelData = np.zeros((256, 256), dtype=np.uint16).tobytes()

        buffer = BytesIO()
        ds.save_as(buffer, write_like_original=False)
        return buffer.getvalue()

    @staticmethod
    def create_multiframe_ct(
        patient_id: str = "TEST-003",
        num_frames: int = 10,
        with_pixel_data: bool = True,
    ) -> bytes:
        """
        Create a multiframe CT DICOM instance.

        Args:
            patient_id: Patient identifier
            num_frames: Number of frames in the multiframe image
            with_pixel_data: Include dummy pixel data for all frames (128x128 per frame)

        Returns:
            DICOM file as bytes
        """
        study_uid = generate_uid()
        series_uid = generate_uid()
        sop_uid = generate_uid()

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
        ds.PatientName = "Multiframe^Patient"
        ds.StudyDate = datetime.now().strftime("%Y%m%d")
        ds.StudyTime = "150000"
        ds.Modality = "CT"
        ds.AccessionNumber = f"ACC-{uuid.uuid4().hex[:8]}"
        ds.StudyDescription = "Multiframe CT Study"
        ds.SeriesDescription = "Multiframe Series"
        ds.SeriesNumber = 1
        ds.InstanceNumber = 1
        ds.NumberOfFrames = num_frames

        if with_pixel_data:
            import numpy as np

            ds.Rows = 128
            ds.Columns = 128
            ds.BitsAllocated = 16
            ds.BitsStored = 16
            ds.HighBit = 15
            ds.SamplesPerPixel = 1
            ds.PhotometricInterpretation = "MONOCHROME2"
            ds.PixelRepresentation = 0
            # Create pixel data for all frames
            pixel_array = np.zeros((num_frames, 128, 128), dtype=np.uint16)
            ds.PixelData = pixel_array.tobytes()

        buffer = BytesIO()
        ds.save_as(buffer, write_like_original=False)
        return buffer.getvalue()

    @staticmethod
    def create_invalid_dicom(
        missing_tags: list[str] | None = None,
    ) -> bytes:
        """
        Create an invalid DICOM instance for testing error handling.

        Args:
            missing_tags: List of required tag names to omit

        Returns:
            Invalid DICOM file as bytes
        """
        missing_tags = missing_tags or []

        file_meta = Dataset()
        file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
        file_meta.MediaStorageSOPInstanceUID = generate_uid()
        file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
        file_meta.ImplementationClassUID = "1.2.826.0.1.3680043.8.498.1"

        ds = FileDataset(
            filename_or_obj=BytesIO(),
            dataset=Dataset(),
            file_meta=file_meta,
            preamble=b"\x00" * 128,
        )

        # Add required tags unless explicitly excluded
        if "SOPClassUID" not in missing_tags:
            ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
        if "SOPInstanceUID" not in missing_tags:
            ds.SOPInstanceUID = generate_uid()
        if "StudyInstanceUID" not in missing_tags:
            ds.StudyInstanceUID = generate_uid()
        if "SeriesInstanceUID" not in missing_tags:
            ds.SeriesInstanceUID = generate_uid()
        if "PatientID" not in missing_tags:
            ds.PatientID = "TEST-999"
        if "Modality" not in missing_tags:
            ds.Modality = "CT"

        buffer = BytesIO()
        ds.save_as(buffer, write_like_original=False)
        return buffer.getvalue()
