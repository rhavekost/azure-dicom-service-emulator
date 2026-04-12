"""
QIDO-RS Router — Query/search for DICOM studies, series, and instances.

Endpoints:
- GET /v2/studies                                          (search all studies)
- GET /v2/studies/{study_uid}/series                      (search series in a study)
- GET /v2/studies/{study_uid}/series/{series_uid}/instances (search instances in a series)
- GET /v2/series                                           (root-level series search)
- GET /v2/instances                                        (root-level instance search)
- GET /v2/studies/{study_uid}/instances                   (study-scoped instance search)
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import and_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.dicom import DicomInstance
from app.routers._shared import _json_dumps
from app.schemas.qido import (
    DicomJsonObject,  # noqa: F401 — imported for OpenAPI schema registration
)
from app.services.search_utils import build_fuzzy_name_filter, parse_uid_list, translate_wildcards

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Shared OpenAPI response documentation for QIDO-RS endpoints ────
_QIDO_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Array of DICOM JSON objects matching the query",
        "content": {
            "application/dicom+json": {
                "schema": {
                    "type": "array",
                    "items": {"$ref": "#/components/schemas/DicomJsonObject"},
                }
            }
        },
    }
}

# ── Tag mapping for QIDO-RS query parameters ───────────────────────
QIDO_TAG_MAP = {
    "PatientID": "patient_id",
    "00100020": "patient_id",
    "PatientName": "patient_name",
    "00100010": "patient_name",
    "StudyDate": "study_date",
    "00080020": "study_date",
    "AccessionNumber": "accession_number",
    "00080050": "accession_number",
    "StudyDescription": "study_description",
    "00081030": "study_description",
    "Modality": "modality",
    "00080060": "modality",
    "StudyInstanceUID": "study_instance_uid",
    "0020000D": "study_instance_uid",
    "SeriesInstanceUID": "series_instance_uid",
    "0020000E": "series_instance_uid",
    "SOPInstanceUID": "sop_instance_uid",
    "00080018": "sop_instance_uid",
    "ReferringPhysicianName": "referring_physician_name",
    "00080090": "referring_physician_name",
}

# ── QIDO-RS Study UID parameter keys ──────────────────────────────
# Used to strip StudyInstanceUID from query params before delegating to
# _parse_qido_params in study-scoped endpoints, preventing duplicate conditions.
_STUDY_UID_KEYS: frozenset[str] = frozenset({"StudyInstanceUID", "0020000D"})


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Helper functions
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def parse_date_range(value: str):
    """Parse DICOM date range query value into SQL filter conditions.

    Supports four date formats per DICOM standard:
    - "20260115": exact match
    - "20260110-20260120": range (inclusive on both ends)
    - "-20260120": before or equal
    - "20260110-": after or equal

    Args:
        value: Date query string from QIDO-RS parameter

    Returns:
        Tuple of (filter_type, start_date, end_date) where:
        - filter_type: "exact" | "range" | "before" | "after"
        - start_date: Start date string (None for before queries)
        - end_date: End date string (None for after queries)

    Notes:
        - Does not validate date format (DICOM is lenient)
        - Assumes YYYYMMDD format but accepts any string
        - Lexicographic comparison works correctly for YYYYMMDD
    """
    if "-" not in value:
        # Exact match: "20260115"
        return ("exact", value, None)

    if value.startswith("-"):
        # Before or equal: "-20260120"
        end_date = value[1:]  # Strip leading hyphen
        return ("before", None, end_date)

    if value.endswith("-"):
        # After or equal: "20260110-"
        start_date = value[:-1]  # Strip trailing hyphen
        return ("after", start_date, None)

    # Range: "20260110-20260120"
    parts = value.split("-", 1)  # Split on first hyphen only
    if len(parts) == 2:
        start_date, end_date = parts
        return ("range", start_date, end_date)

    # Fallback: treat as exact match if parsing fails
    return ("exact", value, None)


def _parse_qido_params(params, fuzzymatching: bool = False) -> list:
    """Convert QIDO-RS query parameters to SQLAlchemy filter conditions.

    Supports:
    - UID list matching: comma or backslash separated UIDs (for StudyInstanceUID, SeriesInstanceUID, SOPInstanceUID)
    - Date range matching: DICOM date range syntax (for StudyDate)
    - Wildcard matching: * (zero or more chars), ? (exactly one char)
    - Fuzzy matching: prefix word matching for person names (when fuzzymatching=true)
    - Exact matching: when no wildcards and fuzzymatching=false
    """
    filters = []
    for key, value in params.items():
        # Per DICOM PS3.18, parameters with no value are includefield hints,
        # not filters — skip them to avoid matching only rows with empty columns.
        if not value:
            continue
        if key in ("limit", "offset", "fuzzymatching", "includefield"):
            continue

        db_column = QIDO_TAG_MAP.get(key)
        if db_column:
            column = getattr(DicomInstance, db_column, None)
            if column is not None:
                # UID list matching takes precedence (for UID fields only)
                if db_column in ("study_instance_uid", "series_instance_uid", "sop_instance_uid"):
                    if "," in value or "\\" in value:
                        # UID list search
                        uids = parse_uid_list(value)
                        filters.append(column.in_(uids))
                    else:
                        # Single UID - exact match
                        filters.append(column == value)
                # Date range matching (for date fields)
                elif db_column == "study_date":
                    filter_type, start_date, end_date = parse_date_range(value)

                    if filter_type == "exact":
                        # Exact date match
                        filters.append(column == start_date)
                    elif filter_type == "range":
                        # Date range (inclusive)
                        filters.append(and_(column >= start_date, column <= end_date))
                    elif filter_type == "before":
                        # Before or equal
                        filters.append(column <= end_date)
                    elif filter_type == "after":
                        # After or equal
                        filters.append(column >= start_date)
                # Wildcard matching takes precedence (for non-UID, non-date fields)
                elif "*" in value or "?" in value:
                    # Translate DICOM wildcards to SQL LIKE pattern
                    pattern = translate_wildcards(value)
                    filters.append(column.like(pattern))
                elif fuzzymatching and db_column in ("patient_name", "referring_physician_name"):
                    # Use prefix word matching for person names
                    filters.append(build_fuzzy_name_filter(value, column))
                else:
                    # Exact match
                    filters.append(column == value)

    return filters


def filter_dicom_json_by_includefield(
    dicom_json: dict, includefield: str | list[str] | None, level: str = "study"
) -> dict:
    """Filter DICOM JSON based on includefield parameter.

    Args:
        dicom_json: Full DICOM JSON dictionary
        includefield: Comma-separated list of DICOM tags, list of tags, "all", or None
        level: Query level ("study", "series", or "instance")

    Returns:
        Filtered DICOM JSON dictionary

    Behavior:
        - includefield=all: return everything
        - includefield=<tag>: return only requested tags + required return tags
        - includefield=None: return as-is (default set already built)

    Required return tags (always included):
        - StudyInstanceUID (0020000D)
        - SeriesInstanceUID (0020000E)
        - SOPInstanceUID (00080018)
        - SOPClassUID (00080016)
    """
    # Required tags per DICOM PS3.18 Table 6.7.1-2a
    REQUIRED_TAGS = {
        "0020000D",  # StudyInstanceUID
        "0020000E",  # SeriesInstanceUID
        "00080018",  # SOPInstanceUID
        "00080016",  # SOPClassUID
    }

    # If no includefield specified, return as-is (default set)
    if not includefield:
        return dicom_json

    # Handle list of values (multiple query params)
    if isinstance(includefield, list):
        # Check if any value is "all"
        if "all" in includefield:
            return dicom_json
        # Combine all tags from the list (filter out empty strings)
        requested_tags = {
            tag.strip() for value in includefield for tag in value.split(",") if tag.strip()
        }
        # If no valid tags after filtering, return default
        if not requested_tags:
            return dicom_json
    else:
        # If includefield=all, return everything
        if includefield == "all":
            return dicom_json
        # Parse requested tags (comma-separated)
        requested_tags = {tag.strip() for tag in includefield.split(",") if tag.strip()}
        # If empty string or no valid tags, return default
        if not requested_tags:
            return dicom_json

    # Combine requested tags with required tags
    tags_to_include = requested_tags | REQUIRED_TAGS

    # Filter the DICOM JSON to only include requested + required tags
    filtered = {tag: value for tag, value in dicom_json.items() if tag in tags_to_include}

    return filtered


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  QIDO-RS Endpoints
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@router.get("/studies", response_class=Response, responses=_QIDO_RESPONSES)
async def qido_rs_studies(
    request: Request,
    limit: int = Query(100, alias="limit"),
    offset: int = Query(0, alias="offset"),
    fuzzymatching: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """QIDO-RS: Search for studies.

    Query parameters:
    - PatientName: Patient name (supports exact match and wildcard)
    - fuzzymatching: Enable fuzzy prefix matching on person names (default: false)
    - limit: Maximum number of results (default: 100)
    - offset: Offset for pagination (default: 0)

    When fuzzymatching=true, person name queries use prefix word matching:
    - "joh" matches "John^Doe" (starts with "John")
    - "do" matches "Doe^John" (starts with "Doe")
    - "joh do" matches "John^Doe" (matches both components)
    """
    filters = _parse_qido_params(request.query_params, fuzzymatching)

    # Build query for distinct studies
    query = select(DicomInstance).where(and_(*filters)) if filters else select(DicomInstance)

    # Get distinct studies with aggregation
    result = await db.execute(
        query.order_by(DicomInstance.study_date.desc()).offset(offset).limit(limit)
    )
    instances = result.scalars().all()

    # Group by study for study-level response
    studies: dict[str, list] = {}
    for inst in instances:
        if inst.study_instance_uid not in studies:
            studies[inst.study_instance_uid] = []
        studies[inst.study_instance_uid].append(inst)

    # Get includefield parameter (may be multiple values)
    includefield_list = request.query_params.getlist("includefield")
    includefield = includefield_list if includefield_list else None

    response = []
    for study_uid, study_instances in studies.items():
        first = study_instances[0]
        modalities = list({i.modality for i in study_instances if i.modality})
        series_uids = list({i.series_instance_uid for i in study_instances})

        study_json = {
            "0020000D": {"vr": "UI", "Value": [study_uid]},
            "00100020": {"vr": "LO", "Value": [first.patient_id] if first.patient_id else {}},
            "00100010": {
                "vr": "PN",
                "Value": [{"Alphabetic": first.patient_name}] if first.patient_name else {},
            },
            "00080020": {"vr": "DA", "Value": [first.study_date] if first.study_date else {}},
            "00080050": {
                "vr": "SH",
                "Value": [first.accession_number] if first.accession_number else {},
            },
            "00081030": {
                "vr": "LO",
                "Value": [first.study_description] if first.study_description else {},
            },
            "00080061": {"vr": "CS", "Value": modalities},  # ModalitiesInStudy
            "00201206": {"vr": "IS", "Value": [len(series_uids)]},  # NumberOfStudyRelatedSeries
            "00201208": {
                "vr": "IS",
                "Value": [len(study_instances)],
            },  # NumberOfStudyRelatedInstances
        }

        # Apply includefield filtering
        filtered_study_json = filter_dicom_json_by_includefield(study_json, includefield, "study")
        response.append(filtered_study_json)

    return Response(
        content=_json_dumps(response),
        media_type="application/dicom+json",
    )


@router.get("/studies/{study_uid}/series", response_class=Response, responses=_QIDO_RESPONSES)
async def qido_rs_series(
    study_uid: str,
    request: Request,
    limit: int = Query(100, alias="limit"),
    offset: int = Query(0, alias="offset"),
    SeriesInstanceUID: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """QIDO-RS: Search for series within a study.

    Query parameters:
    - SeriesInstanceUID: Series UID (supports comma or backslash separated list)
    - limit: Maximum number of results (default: 100)
    - offset: Offset for pagination (default: 0)
    """
    # Start with study filter
    conditions = [DicomInstance.study_instance_uid == study_uid]

    # Add SeriesInstanceUID filter with list support
    if SeriesInstanceUID:
        if "," in SeriesInstanceUID or "\\" in SeriesInstanceUID:
            # UID list search
            uids = parse_uid_list(SeriesInstanceUID)
            conditions.append(DicomInstance.series_instance_uid.in_(uids))
        else:
            # Single UID - exact match
            conditions.append(DicomInstance.series_instance_uid == SeriesInstanceUID)

    query = select(DicomInstance).where(and_(*conditions)).offset(offset).limit(limit)

    result = await db.execute(query)
    instances = result.scalars().all()

    series: dict[str, list] = {}
    for inst in instances:
        if inst.series_instance_uid not in series:
            series[inst.series_instance_uid] = []
        series[inst.series_instance_uid].append(inst)

    # Get includefield parameter (may be multiple values)
    includefield_list = request.query_params.getlist("includefield")
    includefield = includefield_list if includefield_list else None

    response = []
    for series_uid, series_instances in series.items():
        first = series_instances[0]
        series_json = {
            "0020000D": {"vr": "UI", "Value": [study_uid]},
            "0020000E": {"vr": "UI", "Value": [series_uid]},
            "00080060": {"vr": "CS", "Value": [first.modality] if first.modality else {}},
            "0008103E": {
                "vr": "LO",
                "Value": [first.series_description] if first.series_description else {},
            },
            "00200011": {"vr": "IS", "Value": [first.series_number] if first.series_number else {}},
            "00201209": {
                "vr": "IS",
                "Value": [len(series_instances)],
            },  # NumberOfSeriesRelatedInstances
        }

        # Apply includefield filtering
        filtered_series_json = filter_dicom_json_by_includefield(
            series_json, includefield, "series"
        )
        response.append(filtered_series_json)

    return Response(
        content=_json_dumps(response),
        media_type="application/dicom+json",
    )


@router.get(
    "/studies/{study_uid}/series/{series_uid}/instances",
    response_class=Response,
    responses=_QIDO_RESPONSES,
)
async def qido_rs_instances(
    study_uid: str,
    series_uid: str,
    request: Request,
    limit: int = Query(100, alias="limit"),
    offset: int = Query(0, alias="offset"),
    SOPInstanceUID: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """QIDO-RS: Search for instances within a series.

    Query parameters:
    - SOPInstanceUID: SOP Instance UID (supports comma or backslash separated list)
    - limit: Maximum number of results (default: 100)
    - offset: Offset for pagination (default: 0)
    """
    # Start with study and series filters
    conditions = [
        DicomInstance.study_instance_uid == study_uid,
        DicomInstance.series_instance_uid == series_uid,
    ]

    # Add SOPInstanceUID filter with list support
    if SOPInstanceUID:
        if "," in SOPInstanceUID or "\\" in SOPInstanceUID:
            # UID list search
            uids = parse_uid_list(SOPInstanceUID)
            conditions.append(DicomInstance.sop_instance_uid.in_(uids))
        else:
            # Single UID - exact match
            conditions.append(DicomInstance.sop_instance_uid == SOPInstanceUID)

    query = select(DicomInstance).where(and_(*conditions)).offset(offset).limit(limit)

    result = await db.execute(query)
    instances = result.scalars().all()

    # Get includefield parameter (may be multiple values) and apply filtering
    includefield_list = request.query_params.getlist("includefield")
    includefield = includefield_list if includefield_list else None
    response = [
        filter_dicom_json_by_includefield(inst.dicom_json, includefield, "instance")
        for inst in instances
    ]

    return Response(
        content=_json_dumps(response),
        media_type="application/dicom+json",
    )


@router.get("/series", response_class=Response, responses=_QIDO_RESPONSES)
async def qido_rs_all_series(
    request: Request,
    limit: int = Query(100, alias="limit"),
    offset: int = Query(0, alias="offset"),
    fuzzymatching: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """QIDO-RS: Search for all series across all studies.

    Root-level series search — equivalent to the Azure DICOM Service v2
    ``GET /series`` endpoint. Supports the same QIDO-RS query parameters as
    the study-scoped series search.

    Query parameters:
    - PatientID, PatientName, StudyDate, AccessionNumber, Modality,
      StudyInstanceUID, SeriesInstanceUID, fuzzymatching, limit, offset,
      includefield
    """
    filters = _parse_qido_params(request.query_params, fuzzymatching)

    # Step 1: Get paginated DISTINCT series UIDs ordered consistently.
    # Applying limit/offset here operates at the series level, not the instance
    # level, so pagination is semantically correct.
    # ORDER BY series_instance_uid ensures stable, deterministic pagination across
    # pages (alphabetical ordering of UIDs is arbitrary but consistent).
    base_filter = and_(*filters) if filters else true()
    distinct_series_query = (
        select(DicomInstance.series_instance_uid)
        .where(base_filter)
        .distinct()
        .order_by(DicomInstance.series_instance_uid)
        .offset(offset)
        .limit(limit)
    )
    series_uid_result = await db.execute(distinct_series_query)
    series_uids = [row[0] for row in series_uid_result.all()]

    if not series_uids:
        return Response(content=_json_dumps([]), media_type="application/dicom+json")

    # Step 2: Fetch one representative instance per series UID from step 1.
    # Also re-apply the original filters so attributes like Modality are
    # consistent with what was requested.
    # ORDER BY study_date.desc() picks the most-recent instance as the
    # representative for each series (used for the series date attribute),
    # not to control the series ordering in the response — that is determined
    # by series_uids (Step 1) which is already sorted by series_instance_uid.
    rep_query = (
        select(DicomInstance)
        .where(and_(base_filter, DicomInstance.series_instance_uid.in_(series_uids)))
        .order_by(DicomInstance.study_date.desc(), DicomInstance.series_instance_uid)
    )
    rep_result = await db.execute(rep_query)
    all_matching = rep_result.scalars().all()

    # Pick one representative instance per series and count matching instances.
    # Note: This count reflects only instances matching the current query filters,
    # which deviates from PS3.18's definition of NumberOfSeriesRelatedInstances as
    # the total count of all instances in the series regardless of query filters.
    # Emulator context: a separate unfiltered COUNT query per series is omitted
    # for simplicity.
    seen_series: dict[str, DicomInstance] = {}
    instance_count: dict[str, int] = {}
    for inst in all_matching:
        uid = inst.series_instance_uid
        instance_count[uid] = instance_count.get(uid, 0) + 1
        if uid not in seen_series:
            seen_series[uid] = inst

    includefield_list = request.query_params.getlist("includefield")
    includefield = includefield_list if includefield_list else None

    response = []
    for series_uid in series_uids:
        if series_uid not in seen_series:
            continue
        first = seen_series[series_uid]
        series_json = {
            "0020000D": {"vr": "UI", "Value": [first.study_instance_uid]},
            "0020000E": {"vr": "UI", "Value": [series_uid]},
            "00080060": {"vr": "CS", "Value": [first.modality] if first.modality else {}},
            "0008103E": {
                "vr": "LO",
                "Value": [first.series_description] if first.series_description else {},
            },
            "00200011": {"vr": "IS", "Value": [first.series_number] if first.series_number else {}},
            "00201209": {
                "vr": "IS",
                "Value": [instance_count.get(series_uid, 1)],
            },  # NumberOfSeriesRelatedInstances
        }
        filtered_series_json = filter_dicom_json_by_includefield(
            series_json, includefield, "series"
        )
        response.append(filtered_series_json)

    return Response(
        content=_json_dumps(response),
        media_type="application/dicom+json",
    )


@router.get("/instances", response_class=Response, responses=_QIDO_RESPONSES)
async def qido_rs_all_instances(
    request: Request,
    limit: int = Query(100, alias="limit"),
    offset: int = Query(0, alias="offset"),
    fuzzymatching: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """QIDO-RS: Search for all instances across all studies and series.

    Root-level instance search — equivalent to the Azure DICOM Service v2
    ``GET /instances`` endpoint. Supports the same QIDO-RS query parameters as
    the series-scoped instance search.

    Query parameters:
    - PatientID, PatientName, StudyDate, AccessionNumber, Modality,
      StudyInstanceUID, SeriesInstanceUID, SOPInstanceUID, fuzzymatching,
      limit, offset, includefield
    """
    filters = _parse_qido_params(request.query_params, fuzzymatching)

    query = select(DicomInstance).where(and_(*filters)) if filters else select(DicomInstance)
    query = query.order_by(DicomInstance.study_date.desc()).offset(offset).limit(limit)

    result = await db.execute(query)
    instances = result.scalars().all()

    includefield_list = request.query_params.getlist("includefield")
    includefield = includefield_list if includefield_list else None

    response = [
        filter_dicom_json_by_includefield(inst.dicom_json, includefield, "instance")
        for inst in instances
    ]

    return Response(
        content=_json_dumps(response),
        media_type="application/dicom+json",
    )


@router.get("/studies/{study_uid}/instances", response_class=Response, responses=_QIDO_RESPONSES)
async def qido_rs_study_instances(
    study_uid: str,
    request: Request,
    limit: int = Query(100, alias="limit"),
    offset: int = Query(0, alias="offset"),
    fuzzymatching: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """QIDO-RS: Search for instances within a study across all series.

    Study-scoped instance search (without series scope) — equivalent to the
    Azure DICOM Service v2 ``GET /studies/{study}/instances`` endpoint.

    Query parameters:
    - SeriesInstanceUID, SOPInstanceUID, PatientID, PatientName, Modality,
      fuzzymatching, limit, offset, includefield
    """
    # Always scope to the requested study (path param takes precedence).
    base_conditions = [DicomInstance.study_instance_uid == study_uid]

    # Strip StudyInstanceUID from query params so _parse_qido_params cannot add
    # a conflicting second condition if the caller also passes ?StudyInstanceUID=.
    # Only single-value params are passed here; includefield is handled separately
    # via getlist() below.
    filtered_params = {k: v for k, v in request.query_params.items() if k not in _STUDY_UID_KEYS}

    # Parse additional QIDO filters from the scrubbed query params
    extra_filters = _parse_qido_params(filtered_params, fuzzymatching)

    conditions = base_conditions + extra_filters
    query = (
        select(DicomInstance)
        .where(and_(*conditions))
        .order_by(DicomInstance.study_date.desc())
        .offset(offset)
        .limit(limit)
    )

    result = await db.execute(query)
    instances = result.scalars().all()

    includefield_list = request.query_params.getlist("includefield")
    includefield = includefield_list if includefield_list else None

    response = [
        filter_dicom_json_by_includefield(inst.dicom_json, includefield, "instance")
        for inst in instances
    ]

    return Response(
        content=_json_dumps(response),
        media_type="application/dicom+json",
    )
