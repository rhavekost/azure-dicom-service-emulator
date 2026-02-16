"""Tests for search utilities for QIDO-RS enhancements."""

import pytest
from sqlalchemy import Column, String
from sqlalchemy.sql.expression import BooleanClauseList, ColumnElement

from app.services.search_utils import (

    build_fuzzy_name_filter,
    parse_uid_list,
    translate_wildcards,
)

pytestmark = pytest.mark.unit


def test_build_fuzzy_name_filter_single_word():
    """Test fuzzy name matching with single search term."""
    # Create a mock column
    column = Column("patient_name", String)

    # Test single word matching
    result = build_fuzzy_name_filter("joh", column)

    # Verify result is a BooleanClauseList (OR clause)
    assert isinstance(result, BooleanClauseList)

    # Convert to string to verify SQL logic
    sql_str = str(result.compile(compile_kwargs={"literal_binds": True}))

    # Should match at start: "joh%"
    assert "joh%" in sql_str.lower()

    # Should match after separator: "%^joh%"
    assert "%^joh%" in sql_str.lower() or r"%\^joh%" in sql_str.lower()

    # Should use case-insensitive matching (ILIKE or LIKE with lower())
    assert "like" in sql_str.lower()


def test_build_fuzzy_name_filter_multiple_words():
    """Test fuzzy name matching with multiple search terms."""
    # Create a mock column
    column = Column("patient_name", String)

    # Test multiple word matching
    result = build_fuzzy_name_filter("joh do", column)

    # Verify result is a BooleanClauseList (OR clause)
    assert isinstance(result, BooleanClauseList)

    # Convert to string to verify SQL logic
    sql_str = str(result.compile(compile_kwargs={"literal_binds": True}))

    # Should match "joh" at start and after separator
    assert "joh%" in sql_str.lower()
    assert "%^joh%" in sql_str.lower() or r"%\^joh%" in sql_str.lower()

    # Should match "do" at start and after separator
    assert "do%" in sql_str.lower()
    assert "%^do%" in sql_str.lower() or r"%\^do%" in sql_str.lower()


def test_build_fuzzy_name_filter_empty_input():
    """Test fuzzy name matching with empty input."""
    # Create a mock column
    column = Column("patient_name", String)

    # Test empty string
    result = build_fuzzy_name_filter("", column)

    # Verify result is a ColumnElement
    assert isinstance(result, ColumnElement)

    # Convert to string to verify SQL logic
    sql_str = str(result.compile(compile_kwargs={"literal_binds": True}))

    # Should return false() for empty input
    assert "false" in sql_str.lower() or "0" in sql_str

    # Test whitespace-only string
    result = build_fuzzy_name_filter("   ", column)
    assert isinstance(result, ColumnElement)
    sql_str = str(result.compile(compile_kwargs={"literal_binds": True}))
    assert "false" in sql_str.lower() or "0" in sql_str


def test_translate_wildcards():
    """Test DICOM wildcard to SQL LIKE pattern translation."""
    # Test asterisk (zero or more characters)
    assert translate_wildcards("PAT*") == "PAT%"

    # Test question mark (exactly one character)
    assert translate_wildcards("PAT???") == "PAT___"

    # Test combination
    assert translate_wildcards("PAT*123?") == "PAT%123_"

    # Test escaping existing SQL wildcards
    assert translate_wildcards("PAT_%*") == r"PAT\_\%%"

    # Test no wildcards
    assert translate_wildcards("PATIENT") == "PATIENT"


def test_parse_uid_list_comma_separated():
    """Test parsing comma-separated UID list."""
    result = parse_uid_list("1.2.3,4.5.6,7.8.9")
    assert result == ["1.2.3", "4.5.6", "7.8.9"]


def test_parse_uid_list_backslash_separated():
    """Test parsing backslash-separated UID list."""
    result = parse_uid_list("1.2.3\\4.5.6\\7.8.9")
    assert result == ["1.2.3", "4.5.6", "7.8.9"]


def test_parse_uid_list_with_whitespace():
    """Test parsing UID list with whitespace."""
    # Whitespace around commas
    result = parse_uid_list("1.2.3 , 4.5.6 , 7.8.9")
    assert result == ["1.2.3", "4.5.6", "7.8.9"]

    # Whitespace around backslashes
    result = parse_uid_list("1.2.3 \\ 4.5.6 \\ 7.8.9")
    assert result == ["1.2.3", "4.5.6", "7.8.9"]

    # Mixed separators with whitespace
    result = parse_uid_list("1.2.3 , 4.5.6\\7.8.9")
    assert result == ["1.2.3", "4.5.6", "7.8.9"]
