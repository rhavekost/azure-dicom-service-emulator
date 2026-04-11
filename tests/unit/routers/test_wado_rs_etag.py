"""
Unit tests for ETag / If-None-Match support on WADO-RS metadata endpoints.

Covers:
- ETag header is present in all three metadata responses
- ETag is a quoted string (RFC 7232)
- If-None-Match with matching ETag → 304 Not Modified (no body)
- If-None-Match with non-matching ETag → 200 with full body
- If-None-Match: * (wildcard) → 304 Not Modified when resource exists
"""

import pytest

pytestmark = pytest.mark.unit

# ── Helpers ─────────────────────────────────────────────────────────


def _study_metadata_url(uids):
    return f"/v2/studies/{uids['study_uid']}/metadata"


def _series_metadata_url(uids):
    return f"/v2/studies/{uids['study_uid']}/series/{uids['series_uid']}/metadata"


def _instance_metadata_url(uids):
    study = uids["study_uid"]
    series = uids["series_uid"]
    sop = uids["sop_uid"]
    return f"/v2/studies/{study}/series/{series}/instances/{sop}/metadata"


# ── Study-level metadata ─────────────────────────────────────────────


class TestStudyMetadataETag:
    def test_etag_header_present(self, client, stored_instance):
        response = client.get(_study_metadata_url(stored_instance))
        assert response.status_code == 200
        assert "etag" in response.headers

    def test_etag_is_quoted_string(self, client, stored_instance):
        response = client.get(_study_metadata_url(stored_instance))
        etag = response.headers["etag"]
        assert etag.startswith('"') and etag.endswith('"')

    def test_if_none_match_matching_returns_304(self, client, stored_instance):
        first = client.get(_study_metadata_url(stored_instance))
        etag = first.headers["etag"]

        second = client.get(
            _study_metadata_url(stored_instance),
            headers={"If-None-Match": etag},
        )
        assert second.status_code == 304
        assert second.content == b""
        assert "etag" in second.headers

    def test_if_none_match_mismatch_returns_200(self, client, stored_instance):
        response = client.get(
            _study_metadata_url(stored_instance),
            headers={"If-None-Match": '"deadbeef00000000000000000000000000000000"'},
        )
        assert response.status_code == 200

    def test_if_none_match_wildcard_returns_304(self, client, stored_instance):
        response = client.get(
            _study_metadata_url(stored_instance),
            headers={"If-None-Match": "*"},
        )
        assert response.status_code == 304
        assert "etag" in response.headers


# ── Series-level metadata ────────────────────────────────────────────


class TestSeriesMetadataETag:
    def test_etag_header_present(self, client, stored_instance):
        response = client.get(_series_metadata_url(stored_instance))
        assert response.status_code == 200
        assert "etag" in response.headers

    def test_etag_is_quoted_string(self, client, stored_instance):
        response = client.get(_series_metadata_url(stored_instance))
        etag = response.headers["etag"]
        assert etag.startswith('"') and etag.endswith('"')

    def test_if_none_match_matching_returns_304(self, client, stored_instance):
        first = client.get(_series_metadata_url(stored_instance))
        etag = first.headers["etag"]

        second = client.get(
            _series_metadata_url(stored_instance),
            headers={"If-None-Match": etag},
        )
        assert second.status_code == 304
        assert second.content == b""
        assert "etag" in second.headers

    def test_if_none_match_mismatch_returns_200(self, client, stored_instance):
        response = client.get(
            _series_metadata_url(stored_instance),
            headers={"If-None-Match": '"deadbeef00000000000000000000000000000000"'},
        )
        assert response.status_code == 200

    def test_if_none_match_wildcard_returns_304(self, client, stored_instance):
        response = client.get(
            _series_metadata_url(stored_instance),
            headers={"If-None-Match": "*"},
        )
        assert response.status_code == 304
        assert "etag" in response.headers


# ── Instance-level metadata ──────────────────────────────────────────


class TestInstanceMetadataETag:
    def test_etag_header_present(self, client, stored_instance):
        response = client.get(_instance_metadata_url(stored_instance))
        assert response.status_code == 200
        assert "etag" in response.headers

    def test_etag_is_quoted_string(self, client, stored_instance):
        response = client.get(_instance_metadata_url(stored_instance))
        etag = response.headers["etag"]
        assert etag.startswith('"') and etag.endswith('"')

    def test_if_none_match_matching_returns_304(self, client, stored_instance):
        first = client.get(_instance_metadata_url(stored_instance))
        etag = first.headers["etag"]

        second = client.get(
            _instance_metadata_url(stored_instance),
            headers={"If-None-Match": etag},
        )
        assert second.status_code == 304
        assert second.content == b""
        assert "etag" in second.headers

    def test_if_none_match_mismatch_returns_200(self, client, stored_instance):
        response = client.get(
            _instance_metadata_url(stored_instance),
            headers={"If-None-Match": '"deadbeef00000000000000000000000000000000"'},
        )
        assert response.status_code == 200

    def test_if_none_match_wildcard_returns_304(self, client, stored_instance):
        response = client.get(
            _instance_metadata_url(stored_instance),
            headers={"If-None-Match": "*"},
        )
        assert response.status_code == 304
        assert "etag" in response.headers


# ── ETag stability ────────────────────────────────────────────────────


class TestETagStability:
    """Same content → same ETag on repeated requests."""

    def test_study_etag_stable_across_requests(self, client, stored_instance):
        r1 = client.get(_study_metadata_url(stored_instance))
        r2 = client.get(_study_metadata_url(stored_instance))
        assert r1.headers["etag"] == r2.headers["etag"]

    def test_series_etag_stable_across_requests(self, client, stored_instance):
        r1 = client.get(_series_metadata_url(stored_instance))
        r2 = client.get(_series_metadata_url(stored_instance))
        assert r1.headers["etag"] == r2.headers["etag"]

    def test_instance_etag_stable_across_requests(self, client, stored_instance):
        r1 = client.get(_instance_metadata_url(stored_instance))
        r2 = client.get(_instance_metadata_url(stored_instance))
        assert r1.headers["etag"] == r2.headers["etag"]
