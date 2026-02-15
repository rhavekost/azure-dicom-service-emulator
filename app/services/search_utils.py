"""Search utilities for QIDO-RS enhancements."""

import logging
from sqlalchemy import Column
from sqlalchemy.sql.expression import or_, BooleanClauseList

logger = logging.getLogger(__name__)


def build_fuzzy_name_filter(name_value: str, column: Column) -> BooleanClauseList:
    """
    Build SQL filter for fuzzy person name matching.

    Implements prefix word matching on any name component.
    DICOM PN format: FamilyName^GivenName^MiddleName^Prefix^Suffix

    Examples:
    - "joh" matches "John^Doe" (starts with "John")
    - "do" matches "John^Doe" (starts with "Doe")
    - "joh do" matches "John^Doe" (matches both components)

    Args:
        name_value: Search term (space-separated words)
        column: SQLAlchemy column to filter

    Returns:
        OR clause combining all prefix matches
    """
    terms = name_value.lower().split()

    conditions = []
    for term in terms:
        # Match at start of name
        conditions.append(column.ilike(f"{term}%"))

        # Match after component separator (^)
        conditions.append(column.ilike(f"%^{term}%"))

    return or_(*conditions)


def translate_wildcards(value: str) -> str:
    """
    Translate DICOM wildcards to SQL wildcards.

    DICOM wildcards:
    - * → zero or more characters (SQL %)
    - ? → exactly one character (SQL _)

    Args:
        value: DICOM search value with wildcards

    Returns:
        SQL LIKE pattern
    """
    # Escape existing SQL wildcards
    value = value.replace("%", r"\%")
    value = value.replace("_", r"\_")

    # Translate DICOM wildcards
    value = value.replace("*", "%")
    value = value.replace("?", "_")

    return value


def parse_uid_list(uid_param: str) -> list[str]:
    """
    Parse comma or backslash separated UID list.

    Examples:
    - "1.2.3,4.5.6" → ["1.2.3", "4.5.6"]
    - "1.2.3\\4.5.6" → ["1.2.3", "4.5.6"]

    Args:
        uid_param: UID parameter from query string

    Returns:
        List of UIDs
    """
    # Normalize backslashes to commas
    normalized = uid_param.replace("\\", ",")

    # Split and strip whitespace
    uids = [uid.strip() for uid in normalized.split(",") if uid.strip()]

    return uids
