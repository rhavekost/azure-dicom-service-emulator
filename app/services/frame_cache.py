"""Frame cache manager for WADO-RS frame retrieval."""

import logging
from pathlib import Path
from datetime import datetime, timezone
from .frame_extraction import extract_frames

logger = logging.getLogger(__name__)


class FrameCache:
    """
    Manages frame extraction and caching.

    Note: Failure tracking is in-memory per-process. In multi-worker
    FastAPI deployments, each worker process maintains its own failure cache.
    Consider using shared storage (Redis, database) for production environments
    requiring coordinated failure tracking across workers.
    """

    def __init__(self, storage_dir: Path | str, failure_ttl_seconds: int = 3600):
        """
        Initialize frame cache.

        Args:
            storage_dir: Root directory for DICOM storage
            failure_ttl_seconds: How long to remember failed extractions (default: 1 hour)
        """
        self.storage_dir = Path(storage_dir)
        self.failure_ttl_seconds = failure_ttl_seconds
        self._extraction_failures: dict[str, datetime] = {}

    def get_or_extract(
        self,
        study_uid: str,
        series_uid: str,
        instance_uid: str
    ) -> list[Path]:
        """
        Get frames from cache or extract if not cached.

        Args:
            study_uid: Study Instance UID
            series_uid: Series Instance UID
            instance_uid: SOP Instance UID

        Returns:
            List of paths to extracted frame files

        Raises:
            ValueError: If extraction fails or instance has no pixel data
        """
        # Check if previously failed
        if self._is_failed(instance_uid):
            raise ValueError(
                f"Instance {instance_uid} failed extraction in last hour"
            )

        # Build paths
        instance_dir = (
            self.storage_dir / study_uid / series_uid / instance_uid
        )
        dcm_path = instance_dir / "instance.dcm"
        frames_dir = instance_dir / "frames"

        if not dcm_path.exists():
            raise FileNotFoundError(f"Instance not found: {instance_uid}")

        # Check if frames already extracted
        # NOTE: There's a potential TOCTOU race here in multi-process deployments
        # where multiple workers could attempt extraction simultaneously.
        # This is acceptable because:
        # 1. extract_frames creates frames_dir with exist_ok=True
        # 2. Frame files are written atomically
        # 3. Worst case: duplicate extraction work, not corruption
        if frames_dir.exists():
            cached_frames = sorted(frames_dir.glob("*.raw"))
            if cached_frames:
                logger.debug(
                    f"Using cached frames for {instance_uid} "
                    f"({len(cached_frames)} frames)"
                )
                return cached_frames
            # Empty frames directory - likely from failed extraction.
            # Fall through to attempt extraction again.

        # Extract frames
        try:
            logger.info(f"Extracting frames for {instance_uid}")
            frames = extract_frames(dcm_path, frames_dir)
            return frames
        except Exception as e:
            self._mark_failed(instance_uid)
            raise ValueError(f"Failed to extract frames: {e}") from e

    def get_frame(
        self,
        study_uid: str,
        series_uid: str,
        instance_uid: str,
        frame_number: int
    ) -> Path:
        """
        Get specific frame by number.

        Args:
            study_uid: Study Instance UID
            series_uid: Series Instance UID
            instance_uid: SOP Instance UID
            frame_number: Frame number (1-indexed)

        Returns:
            Path to frame file

        Raises:
            ValueError: If frame number out of range
        """
        frames = self.get_or_extract(study_uid, series_uid, instance_uid)

        if frame_number < 1 or frame_number > len(frames):
            raise ValueError(
                f"Frame {frame_number} out of range "
                f"(instance has {len(frames)} frames)"
            )

        return frames[frame_number - 1]

    def _mark_failed(self, instance_uid: str) -> None:
        """Mark instance as having failed extraction."""
        self._extraction_failures[instance_uid] = datetime.now(timezone.utc)

    def _is_failed(self, instance_uid: str) -> bool:
        """
        Check if instance failed extraction recently.

        Also cleans up expired failure entries to prevent memory leak.
        """
        if instance_uid not in self._extraction_failures:
            return False

        failed_at = self._extraction_failures[instance_uid]
        age = datetime.now(timezone.utc) - failed_at

        # Clean up expired entry
        if age.total_seconds() >= self.failure_ttl_seconds:
            del self._extraction_failures[instance_uid]
            return False

        return True
