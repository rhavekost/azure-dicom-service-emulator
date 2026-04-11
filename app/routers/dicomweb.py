"""
DICOMweb compatibility shim.

The DICOMweb implementation has been split across four focused modules:
  - stow.py    — STOW-RS (store)
  - wado.py    — WADO-RS (retrieve)
  - qido.py    — QIDO-RS (search)
  - delete.py  — DELETE

This module is kept as a backwards-compatible shim so that existing code and
tests that do ``from app.routers import dicomweb; app.include_router(dicomweb.router)``
continue to work without modification.

Shared helpers are in ``app.routers._shared``.
"""

from fastapi import APIRouter

from app.routers import delete as _delete
from app.routers import qido as _qido
from app.routers import stow as _stow
from app.routers import wado as _wado

# Re-export public symbols used directly by test files.
from app.routers._shared import DICOM_STORAGE_DIR, _compute_etag, _json_dumps  # noqa: F401
from app.routers.delete import _delete_instances  # noqa: F401
from app.routers.qido import (  # noqa: F401
    _STUDY_UID_KEYS,
    QIDO_TAG_MAP,
    _parse_qido_params,
    filter_dicom_json_by_includefield,
    parse_date_range,
)
from app.routers.stow import (  # noqa: F401
    FAILURE_REASON_INSTANCE_ALREADY_EXISTS,
    FAILURE_REASON_UID_MISMATCH,
    FAILURE_REASON_UNABLE_TO_PROCESS,
    BulkUpdateRequest,
    _extract_scalar_value,
    bulk_update_studies,
    stow_rs,
    stow_rs_put,
)
from app.routers.wado import _retrieve_instances, _retrieve_metadata  # noqa: F401

# Combined router that includes all four sub-routers.
# This matches the original single-router layout used throughout the test suite.
router = APIRouter()
router.include_router(_stow.router)
router.include_router(_wado.router)
router.include_router(_qido.router)
router.include_router(_delete.router)
