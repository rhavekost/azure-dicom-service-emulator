"""Unit tests for the streaming multipart/related parser (STOW-RS).

These tests exercise :func:`iter_multipart_related`, which is what STOW-RS
uses on the request path so the server can begin processing the first
DICOM instance before the last one has finished arriving on the wire.
"""

from typing import AsyncIterator

import pytest
from fastapi import HTTPException

from app.services.multipart import iter_multipart_related, parse_multipart_related

pytestmark = pytest.mark.unit


async def _stream_chunks(*chunks: bytes) -> AsyncIterator[bytes]:
    """Tiny async iterator helper that yields the given chunks verbatim.

    Used in place of Starlette's ``Request.stream()`` so we can inject
    arbitrary chunk boundaries (split inside a part body, split inside
    a boundary line, etc.) and verify the parser stitches them back
    together correctly.
    """
    for chunk in chunks:
        yield chunk


def _build_body(boundary: str, parts: list[bytes]) -> bytes:
    """Build a multipart/related body with the given DICOM parts."""
    body = b""
    for data in parts:
        body += f"--{boundary}\r\nContent-Type: application/dicom\r\n\r\n".encode()
        body += data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return body


# ── Basic correctness ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_iter_yields_single_part_in_one_chunk():
    boundary = "bnd"
    body = _build_body(boundary, [b"DICMpayload"])

    stream = _stream_chunks(body)
    content_type = f"multipart/related; boundary={boundary}"

    results = [p async for p in iter_multipart_related(stream, content_type)]

    assert len(results) == 1
    assert results[0].content_type == "application/dicom"
    assert results[0].data == b"DICMpayload"


@pytest.mark.asyncio
async def test_iter_yields_multiple_parts():
    boundary = "bnd"
    body = _build_body(boundary, [b"first", b"second", b"third"])

    stream = _stream_chunks(body)
    content_type = f"multipart/related; boundary={boundary}"

    results = [p async for p in iter_multipart_related(stream, content_type)]

    assert [p.data for p in results] == [b"first", b"second", b"third"]


@pytest.mark.asyncio
async def test_iter_skips_non_dicom_parts():
    boundary = "bnd"
    body = (
        f"--{boundary}\r\nContent-Type: text/plain\r\n\r\nignore me\r\n"
        f"--{boundary}\r\nContent-Type: application/dicom\r\n\r\n".encode()
    )
    body += b"keepme"
    body += f"\r\n--{boundary}--\r\n".encode()

    stream = _stream_chunks(body)
    content_type = f"multipart/related; boundary={boundary}"

    results = [p async for p in iter_multipart_related(stream, content_type)]

    assert len(results) == 1
    assert results[0].data == b"keepme"


# ── Chunk-boundary stitching ──────────────────────────────────────
#
# These are the tests that prove the parser handles a network stream
# where chunk boundaries can fall anywhere — inside a part body, inside
# a boundary line, between headers, etc.


@pytest.mark.asyncio
async def test_iter_handles_split_inside_part_body():
    boundary = "bnd"
    body = _build_body(boundary, [b"AAAABBBBCCCC"])
    # Find a split point that lands in the middle of "AAAABBBBCCCC"
    split = body.index(b"AAAA") + 6
    chunks = [body[:split], body[split:]]

    stream = _stream_chunks(*chunks)
    content_type = f"multipart/related; boundary={boundary}"
    results = [p async for p in iter_multipart_related(stream, content_type)]

    assert len(results) == 1
    assert results[0].data == b"AAAABBBBCCCC"


@pytest.mark.asyncio
async def test_iter_handles_split_inside_boundary_line():
    boundary = "longboundarystring"
    body = _build_body(boundary, [b"first", b"second"])
    # Split inside the second boundary occurrence so the parser must
    # buffer across chunks before recognizing it.
    second_boundary = body.index(b"--longboundarystring", 10) + 4
    chunks = [body[:second_boundary], body[second_boundary:]]

    stream = _stream_chunks(*chunks)
    content_type = f"multipart/related; boundary={boundary}"
    results = [p async for p in iter_multipart_related(stream, content_type)]

    assert [p.data for p in results] == [b"first", b"second"]


@pytest.mark.asyncio
async def test_iter_handles_byte_at_a_time_streaming():
    """Worst-case: one byte per chunk.  The parser must still produce
    identical output to the eager parser."""
    boundary = "bnd"
    body = _build_body(boundary, [b"hello", b"world"])

    stream = _stream_chunks(*[bytes([b]) for b in body])
    content_type = f"multipart/related; boundary={boundary}"
    results = [p async for p in iter_multipart_related(stream, content_type)]

    assert [p.data for p in results] == [b"hello", b"world"]


# ── Equivalence with eager parser ─────────────────────────────────


@pytest.mark.asyncio
async def test_iter_matches_parse_multipart_related():
    """For the same body, streaming and eager parsers must agree."""
    boundary = "bnd"
    parts = [b"alpha", b"beta", b"gamma"]
    body = _build_body(boundary, parts)
    content_type = f"multipart/related; boundary={boundary}"

    eager = parse_multipart_related(body, content_type)
    stream = _stream_chunks(body)
    streaming = [p async for p in iter_multipart_related(stream, content_type)]

    assert [(p.content_type, p.data) for p in eager] == [
        (p.content_type, p.data) for p in streaming
    ]


# ── Closing-boundary handling ─────────────────────────────────────


@pytest.mark.asyncio
async def test_iter_stops_at_closing_boundary_marker():
    """Anything after the closing ``--boundary--`` is ignored."""
    boundary = "bnd"
    body = _build_body(boundary, [b"only"])
    # Append trailing bytes that look almost like another part but are
    # past the closing marker.
    body += b"GARBAGE_AFTER_CLOSING_MARKER\r\n"

    stream = _stream_chunks(body)
    content_type = f"multipart/related; boundary={boundary}"
    results = [p async for p in iter_multipart_related(stream, content_type)]

    assert len(results) == 1
    assert results[0].data == b"only"


@pytest.mark.asyncio
async def test_iter_handles_no_closing_marker():
    """Truncated stream without a closing marker still yields completed parts."""
    boundary = "bnd"
    # Build a body with one complete part followed by a truncated next part
    body = (
        f"--{boundary}\r\nContent-Type: application/dicom\r\n\r\n".encode()
        + b"firstpart\r\n"
        + f"--{boundary}\r\nContent-Type: application/dicom\r\n\r\nincomp".encode()
        # No closing boundary ever arrives
    )
    stream = _stream_chunks(body)
    content_type = f"multipart/related; boundary={boundary}"
    results = [p async for p in iter_multipart_related(stream, content_type)]

    # First part completed; second part is incomplete so it's discarded.
    assert len(results) == 1
    assert results[0].data == b"firstpart"


# ── Error paths ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_iter_raises_on_missing_boundary():
    stream = _stream_chunks(b"anything")
    with pytest.raises(HTTPException) as exc_info:
        async for _ in iter_multipart_related(stream, "multipart/related; type=application/dicom"):
            pass
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_iter_handles_empty_stream():
    stream = _stream_chunks()
    results = [
        p
        async for p in iter_multipart_related(stream, "multipart/related; boundary=bnd")
    ]
    assert results == []


# ── Memory / streaming contract ───────────────────────────────────


@pytest.mark.asyncio
async def test_iter_yields_first_part_before_consuming_full_stream():
    """The parser must yield part 1 before chunk 2 is even requested.

    This is the property the production code relies on: while we're
    parsing part N in a process pool, we want chunks for part N+1 to
    still be arriving from the socket.
    """
    boundary = "bnd"
    part1_complete = (
        f"--{boundary}\r\nContent-Type: application/dicom\r\n\r\n".encode()
        + b"firstcomplete\r\n"
    )
    part2_open = f"--{boundary}\r\nContent-Type: application/dicom\r\n\r\n".encode() + b"secon"

    chunks_emitted = 0

    async def slow_stream() -> AsyncIterator[bytes]:
        nonlocal chunks_emitted
        # Chunk 1: contains ALL of part 1 plus the start of part 2's
        # boundary line, so the scanner can recognize part 1 is done.
        chunks_emitted += 1
        yield part1_complete + part2_open[:30]
        # Chunk 2: rest of part 2 + closing marker
        chunks_emitted += 1
        yield part2_open[30:] + b"dpart\r\n" + f"--{boundary}--\r\n".encode()

    content_type = f"multipart/related; boundary={boundary}"

    yielded_first = False
    chunks_at_first_yield = -1
    parts: list[bytes] = []
    async for part in iter_multipart_related(slow_stream(), content_type):
        if not yielded_first:
            chunks_at_first_yield = chunks_emitted
            yielded_first = True
        parts.append(part.data)

    assert parts == [b"firstcomplete", b"secondpart"]
    # The first part must have been yielded after only the first chunk
    # was emitted.  If the parser were buffering the whole body, this
    # would be 2.
    assert chunks_at_first_yield == 1
