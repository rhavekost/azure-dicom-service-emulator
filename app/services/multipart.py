"""Multipart/related parser for DICOM uploads (STOW-RS).

This module provides two entry points:

* :func:`iter_multipart_related` — async generator that consumes an HTTP
  request stream and yields parts as they complete.  This keeps peak
  memory at roughly the size of one DICOM instance instead of the entire
  request body.  Use this on the STOW-RS request path so the server can
  begin processing the first instance before the last one has even
  arrived on the wire — critical for STOW requests carrying thousands
  of images.

* :func:`parse_multipart_related` — eager parser that takes the full
  body as ``bytes`` and returns a list of :class:`MultipartPart`.  Kept
  for backwards compatibility with non-streaming callers (tests, the
  bulk-update path) and for ``build_multipart_response`` round-trips.

Both parsers share the same scanner core (:func:`_scan_buffer`) so they
agree on edge cases (CRLF vs LF line endings, missing preamble,
nested-but-non-DICOM parts, etc.).
"""

from __future__ import annotations

import re
from typing import AsyncIterator, NamedTuple, Optional, Protocol

from fastapi import HTTPException


class MultipartPart(NamedTuple):
    """Single part from a multipart/related message."""

    content_type: str
    data: bytes


class _ByteStream(Protocol):
    """Minimal protocol for an async byte-chunk source.

    Anything that yields ``bytes`` chunks from ``async for chunk in src``
    works here — including Starlette's ``Request.stream()``.
    """

    def __aiter__(self) -> AsyncIterator[bytes]: ...


# Sentinel for the closing ``--boundary--`` marker. ``_scan_buffer``
# returns this in place of a real :class:`MultipartPart` so callers
# know to stop iterating.
_CLOSING_BOUNDARY = object()


# ── Boundary extraction ────────────────────────────────────────────


def _extract_boundary(content_type: str) -> str:
    """Extract and validate the ``boundary`` parameter from a Content-Type header.

    Raises:
        HTTPException: 400 if the boundary is missing or empty.
    """
    boundary_match = re.search(r"boundary=([^\s;]+)", content_type)
    if not boundary_match:
        raise HTTPException(
            status_code=400, detail="Missing boundary parameter in Content-Type header"
        )

    boundary = boundary_match.group(1).strip('"')
    if not boundary:
        raise HTTPException(
            status_code=400, detail="Invalid boundary parameter in Content-Type header"
        )
    return boundary


# ── Shared scanner ─────────────────────────────────────────────────
#
# Both the eager and streaming parsers feed bytes into a buffer and
# call :func:`_scan_buffer` to extract whatever complete parts are
# currently available.  Returning the number of bytes consumed lets the
# streaming parser trim its rolling buffer so peak memory stays at
# roughly one DICOM part.


def _decode_part(raw: bytes) -> Optional[MultipartPart]:
    """Decode a single raw part body (between two boundaries) into a MultipartPart.

    Mirrors the historical eager-parser behavior:
        * empty parts and the closing ``--`` marker are skipped (return None)
        * parts without a header/body separator are skipped (return None)
        * non-DICOM content types are skipped (return None)
        * malformed UTF-8 in headers is treated as "skip this part"
    """
    if not raw or raw == b"--\r\n" or raw == b"--":
        return None

    if b"\r\n\r\n" in raw:
        headers, data = raw.split(b"\r\n\r\n", 1)
    elif b"\n\n" in raw:
        headers, data = raw.split(b"\n\n", 1)
    else:
        return None

    data = data.rstrip(b"\r\n")

    try:
        headers_str = headers.decode("utf-8", errors="ignore")
    except (ValueError, UnicodeDecodeError):
        return None

    part_content_type = "application/dicom"
    ct_match = re.search(r"Content-Type:\s*([^\r\n]+)", headers_str, re.IGNORECASE)
    if ct_match:
        part_content_type = ct_match.group(1).strip()

    if not data or "application/dicom" not in part_content_type.lower():
        return None

    return MultipartPart(part_content_type, data)


def _is_closing_marker(prefix: bytes) -> bool:
    """Return True if the bytes immediately after a boundary indicate the closing marker."""
    return prefix.startswith(b"--")


def _scan_buffer(buf: bytearray, boundary_bytes: bytes, eof: bool):
    """Extract all complete parts currently in ``buf`` for the given boundary.

    Buffer-trimming contract:
        After each successful part extraction, the next boundary line
        (which starts the *next* part) is **left in the buffer** so that
        the next call can re-anchor on it.  Concretely, if this call
        yields N parts, ``consumed`` advances past the opening boundary
        and the next N-1 boundary occurrences, but not past the boundary
        that begins the still-incomplete part N+1.

    Args:
        buf: Rolling byte buffer.  Not mutated by this function;
            callers trim the consumed prefix themselves.
        boundary_bytes: Boundary line including the ``--`` prefix (e.g.
            ``b"--mybnd"``).
        eof: ``True`` when no more bytes will arrive.

    Returns:
        Tuple of ``(parts, consumed)`` where:

        * ``parts`` is a list whose items are either :class:`MultipartPart`
          or the sentinel ``_CLOSING_BOUNDARY`` (indicating the stream
          should end after consuming this much).
        * ``consumed`` is the number of bytes the caller should remove
          from the front of ``buf``.
    """
    parts: list = []
    buf_len = len(buf)

    # Anchor on the first boundary; anything before it is MIME preamble
    # and gets dropped.
    first = buf.find(boundary_bytes, 0)
    if first < 0:
        if eof:
            return parts, buf_len
        return parts, 0

    # ``pos`` always points just past a boundary we have already
    # recognized as starting a part.
    pos = first + len(boundary_bytes)

    # ``consumed`` is the position we'll tell the caller to trim to.
    # Initially we throw away everything before the first boundary, but
    # KEEP the boundary itself in the buffer until we've actually
    # extracted the part it precedes.  That way we never desync on
    # tricky chunk-boundary cases.
    consumed = first

    while True:
        # Need at least 2 bytes after the boundary to tell whether this
        # is the closing ``--boundary--`` marker.
        if pos + 2 > buf_len:
            break

        if _is_closing_marker(buf[pos : pos + 2]):
            parts.append(_CLOSING_BOUNDARY)
            consumed = pos + 2
            return parts, consumed

        # Locate the boundary that closes the current part.
        next_boundary = buf.find(boundary_bytes, pos)
        if next_boundary < 0:
            # The body of this part isn't complete yet — wait for more
            # bytes.  Leave ``consumed`` at the position of the boundary
            # that *opened* this part so the next call can re-anchor.
            break

        raw = bytes(buf[pos:next_boundary])
        decoded = _decode_part(raw)
        if decoded is not None:
            parts.append(decoded)

        # Advance past the just-closed part.  Critical: ``consumed`` is
        # set to ``next_boundary`` (the *position of* the next boundary,
        # not past it) so the next iteration / call re-anchors on it.
        consumed = next_boundary
        pos = next_boundary + len(boundary_bytes)

    if eof and not parts:
        # Stream ended without any complete parts.  Drop the buffer so
        # the caller doesn't loop on dead bytes.
        consumed = buf_len

    return parts, consumed


# ── Streaming parser (request-side) ────────────────────────────────


async def iter_multipart_related(
    stream: _ByteStream, content_type: str
) -> AsyncIterator[MultipartPart]:
    """Yield parts from a multipart/related byte stream as each one completes.

    Args:
        stream: Anything async-iterable over byte chunks (typically
            Starlette's ``Request.stream()``).
        content_type: The request's Content-Type header value.

    Yields:
        :class:`MultipartPart` for each ``application/dicom`` part in
        the body.  Non-DICOM parts are silently skipped, mirroring the
        eager parser's behavior.

    Raises:
        HTTPException: 400 if the boundary is missing or invalid.
    """
    boundary = _extract_boundary(content_type)
    boundary_bytes = f"--{boundary}".encode()

    buf = bytearray()
    eof = False

    try:
        async for chunk in stream:
            if chunk:
                buf.extend(chunk)

            parts, consumed = _scan_buffer(buf, boundary_bytes, eof=False)
            if consumed:
                del buf[:consumed]

            for part in parts:
                if part is _CLOSING_BOUNDARY:
                    return
                yield part
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"Failed to parse multipart request: {str(e)}"
        )

    # End of stream — drain whatever's left.
    eof = True
    parts, consumed = _scan_buffer(buf, boundary_bytes, eof=True)
    if consumed:
        del buf[:consumed]
    for part in parts:
        if part is _CLOSING_BOUNDARY:
            return
        yield part


# ── Eager parser (back-compat) ─────────────────────────────────────


def parse_multipart_related(body: bytes, content_type: str) -> list[MultipartPart]:
    """Parse a fully-buffered multipart/related body and return all DICOM parts.

    Kept for backwards compatibility with tests and callers that don't
    have an async stream available (e.g. ``bulk_update_studies``).  New
    request-handling code should prefer :func:`iter_multipart_related`.

    Args:
        body: Complete request body bytes.
        content_type: Content-Type header value (must include the
            ``boundary=`` parameter).

    Returns:
        List of :class:`MultipartPart` for each ``application/dicom``
        part present in the body.

    Raises:
        HTTPException: 400 if the Content-Type is malformed or the
            boundary is missing.
    """
    try:
        boundary = _extract_boundary(content_type)
        boundary_bytes = f"--{boundary}".encode()

        # Preserve historical fast path: if the caller didn't include a
        # leading CRLF in front of the very first boundary (most clients
        # don't), prepend one to keep the scanner's "boundaries are
        # preceded by CRLF" mental model correct.
        # _scan_buffer already handles "no preamble" naturally.

        buf = bytearray(body)
        result: list[MultipartPart] = []
        parts, _ = _scan_buffer(buf, boundary_bytes, eof=True)
        for part in parts:
            if part is _CLOSING_BOUNDARY:
                break
            result.append(part)
        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse multipart request: {str(e)}")


# ── Response helper (unchanged) ────────────────────────────────────


def build_multipart_response(parts: list[tuple[str, bytes]], boundary: str) -> bytes:
    """Build a multipart/related response body for WADO-RS.

    Args:
        parts: List of ``(content_type, data)`` tuples.
        boundary: Boundary string to use.

    Returns:
        Complete multipart/related message as bytes.
    """
    body_parts: list[bytes] = []

    for content_type, data in parts:
        part = (f"--{boundary}\r\nContent-Type: {content_type}\r\n\r\n").encode()
        part += data + b"\r\n"
        body_parts.append(part)

    body_parts.append(f"--{boundary}--\r\n".encode())

    return b"".join(body_parts)
