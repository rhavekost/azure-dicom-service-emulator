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
        ValueError: If instance has no pixel data
    """
    ds = pydicom.dcmread(dcm_path)

    if not hasattr(ds, 'pixel_array'):
        raise ValueError("Instance does not contain pixel data")

    pixel_array = ds.pixel_array

    # Create cache directory
    cache_dir.mkdir(parents=True, exist_ok=True)

    frame_paths = []

    # Multi-frame image (3D array)
    if len(pixel_array.shape) == 3:
        for i in range(pixel_array.shape[0]):
            frame_path = cache_dir / f"{i + 1}.raw"
            frame_path.write_bytes(pixel_array[i].tobytes())
            frame_paths.append(frame_path)
    else:
        # Single frame (2D array)
        frame_path = cache_dir / "1.raw"
        frame_path.write_bytes(pixel_array.tobytes())
        frame_paths.append(frame_path)

    return frame_paths
