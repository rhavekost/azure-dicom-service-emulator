"""Unit tests for Accept header validation."""

import pytest

from app.services.accept_validation import (
    validate_accept_for_rendered,
    validate_accept_for_retrieve,
)

pytestmark = pytest.mark.unit


# --- validate_accept_for_retrieve ---


def test_retrieve_accepts_none():
    assert validate_accept_for_retrieve(None) is True


def test_retrieve_accepts_empty_string():
    assert validate_accept_for_retrieve("") is True


def test_retrieve_accepts_multipart_related():
    assert validate_accept_for_retrieve("multipart/related") is True


def test_retrieve_accepts_multipart_related_with_type():
    assert validate_accept_for_retrieve("multipart/related; type=application/dicom") is True


def test_retrieve_accepts_application_dicom():
    assert validate_accept_for_retrieve("application/dicom") is True


def test_retrieve_accepts_wildcard():
    assert validate_accept_for_retrieve("*/*") is True


def test_retrieve_accepts_case_insensitive():
    assert validate_accept_for_retrieve("MULTIPART/RELATED") is True


def test_retrieve_rejects_text_plain():
    assert validate_accept_for_retrieve("text/plain") is False


def test_retrieve_rejects_image_jpeg():
    assert validate_accept_for_retrieve("image/jpeg") is False


def test_retrieve_rejects_text_html():
    assert validate_accept_for_retrieve("text/html") is False


# --- validate_accept_for_rendered ---


def test_rendered_accepts_none():
    assert validate_accept_for_rendered(None) is True


def test_rendered_accepts_empty_string():
    assert validate_accept_for_rendered("") is True


def test_rendered_accepts_jpeg():
    assert validate_accept_for_rendered("image/jpeg") is True


def test_rendered_accepts_png():
    assert validate_accept_for_rendered("image/png") is True


def test_rendered_accepts_wildcard():
    assert validate_accept_for_rendered("*/*") is True


def test_rendered_accepts_case_insensitive():
    assert validate_accept_for_rendered("IMAGE/JPEG") is True


def test_rendered_rejects_application_dicom():
    assert validate_accept_for_rendered("application/dicom") is False


def test_rendered_rejects_text_plain():
    assert validate_accept_for_rendered("text/plain") is False


def test_rendered_rejects_multipart():
    assert validate_accept_for_rendered("multipart/related") is False
