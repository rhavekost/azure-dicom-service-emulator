"""
Unit tests for ETag / If-None-Match support on WADO-RS metadata endpoints.

Covers:
- ETag header is present in all three metadata responses
- ETag is a quoted string (RFC 7232)
- If-None-Match with matching ETag → 304 Not Modified (no body)
- If-None-Match with non-matching ETag → 200 with full body
- If-None-Match: * (wildcard) → 304 Not Modified when resource exists
- If-None-Match with multi-value list containing a match → 304
- If-None-Match with multi-value list containing no match → 200
- If-None-Match: * on non-existent resource → 404 (RFC 7232: wildcard only
  matches when resource exists)
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


# ── Multi-value If-None-Match (RFC 7232 § 3.2) ───────────────────────


class TestMultiValueIfNoneMatch:
    """If-None-Match may carry a comma-separated list of ETags."""

    def test_multi_value_with_matching_token_returns_304(self, client, stored_instance):
        """One of the tokens matches the current ETag → 304."""
        first = client.get(_study_metadata_url(stored_instance))
        etag = first.headers["etag"]

        response = client.get(
            _study_metadata_url(stored_instance),
            headers={"If-None-Match": f'"deadbeef0000000000000000000000000000", {etag}'},
        )
        assert response.status_code == 304
        assert response.content == b""
        assert "etag" in response.headers

    def test_multi_value_with_no_matching_token_returns_200(self, client, stored_instance):
        """None of the tokens matches the current ETag → 200 with body."""
        response = client.get(
            _study_metadata_url(stored_instance),
            headers={
                "If-None-Match": (
                    '"deadbeef0000000000000000000000000000", '
                    '"cafebabe0000000000000000000000000000"'
                )
            },
        )
        assert response.status_code == 200
        assert len(response.content) > 0


# ── ETag invalidation after bulk update ──────────────────────────────


class TestETagInvalidatedAfterBulkUpdate:
    """ETag must change after a bulk update modifies the study's metadata."""

    def test_etag_changes_after_bulk_update(self, client, stored_instance):
        """Bulk-updating PatientID must produce a new ETag and invalidate the old one."""
        url = _study_metadata_url(stored_instance)

        # 1. Capture the ETag before the update
        before = client.get(url)
        assert before.status_code == 200
        etag_before = before.headers["etag"]

        # 2. Perform a bulk update that changes PatientID
        update_response = client.post(
            "/v2/studies/$bulkUpdate",
            json={
                "studyInstanceUids": [stored_instance["study_uid"]],
                "changeDataset": {"00100020": {"vr": "LO", "Value": ["UPDATED-PATIENT-ID"]}},
            },
        )
        assert update_response.status_code == 202

        # 3. Fetch the ETag after the update
        after = client.get(url)
        assert after.status_code == 200
        etag_after = after.headers["etag"]

        # 4. The ETag must have changed
        assert etag_before != etag_after, "ETag was not invalidated after bulk update"

        # 5. The old ETag must now return 200 (not 304) — it is stale
        stale_response = client.get(url, headers={"If-None-Match": etag_before})
        assert (
            stale_response.status_code == 200
        ), "Old ETag still returns 304 after bulk update — caching semantics are wrong"


# ── Wildcard on non-existent resource ────────────────────────────────


class TestWildcardOnNonExistentResource:
    """RFC 7232: wildcard * only matches when the resource exists.

    The server raises 404 before inspecting If-None-Match, so the
    wildcard should never produce a 304 for an unknown resource.
    """

    def test_wildcard_on_nonexistent_study_returns_404(self, client):
        """GET /v2/studies/<unknown>/metadata with If-None-Match: * → 404."""
        response = client.get(
            "/v2/studies/1.2.3.nonexistent/metadata",
            headers={"If-None-Match": "*"},
        )
        assert response.status_code == 404
