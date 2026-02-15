"""Multipart/related parser for DICOM uploads (STOW-RS)."""

import re
from typing import NamedTuple


class MultipartPart(NamedTuple):
    """Single part from multipart/related message."""
    content_type: str
    data: bytes


def parse_multipart_related(body: bytes, content_type: str) -> list[MultipartPart]:
    """
    Parse multipart/related message and extract DICOM parts.

    Args:
        body: Raw multipart message body
        content_type: Content-Type header value (contains boundary)

    Returns:
        List of MultipartPart with content_type and binary data
    """
    # Extract boundary from Content-Type header
    boundary_match = re.search(r'boundary=([^\s;]+)', content_type)
    if not boundary_match:
        raise ValueError("No boundary found in Content-Type header")

    boundary = boundary_match.group(1).strip('"')
    boundary_bytes = f"--{boundary}".encode()

    # Split body by boundary
    parts = body.split(boundary_bytes)

    result = []
    for part in parts:
        if not part or part == b'--\r\n' or part == b'--':
            continue

        # Split headers from body
        if b'\r\n\r\n' in part:
            headers, data = part.split(b'\r\n\r\n', 1)
        elif b'\n\n' in part:
            headers, data = part.split(b'\n\n', 1)
        else:
            continue

        # Remove trailing CRLF
        data = data.rstrip(b'\r\n')

        # Extract Content-Type
        part_content_type = "application/dicom"  # default
        headers_str = headers.decode('utf-8', errors='ignore')
        ct_match = re.search(r'Content-Type:\s*([^\r\n]+)', headers_str, re.IGNORECASE)
        if ct_match:
            part_content_type = ct_match.group(1).strip()

        if data:
            result.append(MultipartPart(part_content_type, data))

    return result


def build_multipart_response(parts: list[tuple[str, bytes]], boundary: str) -> bytes:
    """
    Build multipart/related response for WADO-RS.

    Args:
        parts: List of (content_type, data) tuples
        boundary: Boundary string to use

    Returns:
        Complete multipart/related message as bytes
    """
    body_parts = []

    for content_type, data in parts:
        part = (
            f"--{boundary}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"\r\n"
        ).encode()
        part += data + b"\r\n"
        body_parts.append(part)

    body_parts.append(f"--{boundary}--\r\n".encode())

    return b"".join(body_parts)
