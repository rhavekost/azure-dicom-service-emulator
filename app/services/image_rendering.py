"""Image rendering service for WADO-RS rendered endpoints."""

import logging
import io
from pathlib import Path
import pydicom
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


def render_frame(
    dcm_path: Path,
    frame_number: int = 1,
    format: str = "jpeg",
    quality: int = 100
) -> bytes:
    """
    Render DICOM frame to image format.

    Args:
        dcm_path: Path to DICOM file
        frame_number: Frame number (1-indexed)
        format: Output format ("jpeg" or "png")
        quality: JPEG quality (1-100, ignored for PNG)

    Returns:
        Rendered image as bytes

    Raises:
        ValueError: If frame number out of range or no pixel data
    """
    logger.info(
        f"Rendering frame {frame_number} from {dcm_path} as {format} (quality={quality})"
    )

    # Validate quality parameter for JPEG
    if format.lower() == "jpeg" and not (1 <= quality <= 100):
        raise ValueError(f"JPEG quality must be 1-100, got {quality}")

    # Read DICOM file with error handling
    try:
        ds = pydicom.dcmread(dcm_path)
    except (FileNotFoundError, PermissionError) as e:
        raise ValueError(f"Cannot read DICOM file {dcm_path}: {e}") from e
    except Exception as e:
        raise ValueError(f"Invalid DICOM file {dcm_path}: {e}") from e

    # Access pixel_array with proper error handling
    try:
        pixel_array = ds.pixel_array
    except (AttributeError, NotImplementedError) as e:
        raise ValueError("Instance does not contain decodable pixel data") from e

    # Check for multi-frame using NumberOfFrames attribute
    num_frames = getattr(ds, 'NumberOfFrames', 1)

    if num_frames > 1:
        # Multi-frame image
        if frame_number < 1 or frame_number > num_frames:
            raise ValueError(
                f"Frame {frame_number} out of range (instance has {num_frames} frames)"
            )
        frame_data = pixel_array[frame_number - 1]
    else:
        # Single frame (could be grayscale or RGB)
        if frame_number != 1:
            raise ValueError(f"Cannot access frame {frame_number} of single-frame instance")
        frame_data = pixel_array

    # Apply windowing if present
    if hasattr(ds, 'WindowCenter') and hasattr(ds, 'WindowWidth'):
        logger.debug(
            f"Applying windowing: center={ds.WindowCenter}, width={ds.WindowWidth}"
        )
        frame_data = apply_windowing(
            frame_data,
            ds.WindowCenter,
            ds.WindowWidth
        )

    # Normalize to 8-bit if needed
    if frame_data.dtype != np.uint8:
        logger.debug(f"Normalizing {frame_data.dtype} to uint8")
        frame_data = normalize_to_uint8(frame_data)

    # Convert to PIL Image
    image = Image.fromarray(frame_data)

    # Render to bytes
    buffer = io.BytesIO()
    if format.lower() == "jpeg":
        image.save(buffer, format="JPEG", quality=quality)
    elif format.lower() == "png":
        image.save(buffer, format="PNG")
    else:
        raise ValueError(f"Unsupported format: {format}")

    image_bytes = buffer.getvalue()
    logger.info(f"Rendered {len(image_bytes)} bytes as {format}")
    return image_bytes


def apply_windowing(
    pixel_array: np.ndarray,
    center: float | list[float],
    width: float | list[float]
) -> np.ndarray:
    """
    Apply DICOM windowing to pixel array.

    Args:
        pixel_array: Input pixel array
        center: Window center (or list for multi-window)
        width: Window width (or list for multi-window)

    Returns:
        Windowed pixel array (uint8)
    """
    # Handle multi-window case (use first window)
    # Handle both list and pydicom.MultiValue types
    if not isinstance(center, (int, float)):
        center = float(center[0])
    if not isinstance(width, (int, float)):
        width = float(width[0])

    arr = pixel_array.astype(np.float32)

    lower = center - width / 2
    upper = center + width / 2

    # Apply windowing
    arr = np.clip(arr, lower, upper)
    arr = ((arr - lower) / width * 255).astype(np.uint8)

    return arr


def normalize_to_uint8(pixel_array: np.ndarray) -> np.ndarray:
    """
    Normalize pixel array to uint8 range.

    Args:
        pixel_array: Input pixel array

    Returns:
        Normalized uint8 array
    """
    arr = pixel_array.astype(np.float32)

    # Normalize to 0-255
    arr_min = arr.min()
    arr_max = arr.max()

    if arr_max > arr_min:
        arr = ((arr - arr_min) / (arr_max - arr_min) * 255).astype(np.uint8)
    else:
        arr = np.zeros_like(arr, dtype=np.uint8)

    return arr
