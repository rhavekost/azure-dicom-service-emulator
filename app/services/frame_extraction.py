"""Frame extraction service for WADO-RS frame retrieval."""

import logging
from pathlib import Path

import pydicom

logger = logging.getLogger(__name__)


def extract_frames(dcm_path: Path, cache_dir: Path) -> list[Path]:
    """
    Extract all frames from DICOM instance to cache directory.

    Args:
        dcm_path: Path to DICOM file
        cache_dir: Directory to store extracted frames

    Returns:
        List of paths to extracted frame files (1.raw, 2.raw, etc.)

    Raises:
        ValueError: If instance has no pixel data or file I/O errors occur
    """
    logger.info(f"Extracting frames from {dcm_path.name}")

    # C1: Add error handling for file I/O operations
    try:
        ds = pydicom.dcmread(dcm_path)
    except (FileNotFoundError, PermissionError) as e:
        raise ValueError(f"Cannot read DICOM file {dcm_path}: {e}") from e
    except Exception as e:  # pydicom.errors.InvalidDicomError
        raise ValueError(f"Invalid DICOM file {dcm_path}: {e}") from e

    # C3: Fix pixel data validation - actually access the property
    try:
        # TODO: For large multi-frame images, consider using defer_size parameter
        # or processing frames iteratively to reduce memory footprint (C2)
        pixel_array = ds.pixel_array
    except (AttributeError, NotImplementedError) as e:
        raise ValueError("Instance does not contain decodable pixel data") from e

    # Create cache directory
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ValueError(f"Cannot create cache directory {cache_dir}: {e}") from e

    frame_paths = []

    # Multi-frame image (3D array)
    if len(pixel_array.shape) == 3:
        num_frames = pixel_array.shape[0]
        for i in range(num_frames):
            frame_path = cache_dir / f"{i + 1}.raw"
            try:
                frame_path.write_bytes(pixel_array[i].tobytes())
            except OSError as e:
                raise ValueError(f"Failed to write frame {i + 1}: {e}") from e
            frame_paths.append(frame_path)
        logger.debug(f"Extracted {num_frames} frames to {cache_dir}")
    else:
        # Single frame (2D array)
        frame_path = cache_dir / "1.raw"
        try:
            frame_path.write_bytes(pixel_array.tobytes())
        except OSError as e:
            raise ValueError(f"Failed to write frame 1: {e}") from e
        frame_paths.append(frame_path)
        logger.debug(f"Extracted 1 frame to {cache_dir}")

    return frame_paths
