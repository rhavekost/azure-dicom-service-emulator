"""Accept header validation for WADO-RS endpoints.

Implements HTTP 406 Not Acceptable enforcement per DICOMweb specification.
"""


def validate_accept_for_retrieve(accept_header: str | None) -> bool:
    """Validate Accept header for WADO-RS retrieve endpoints.

    Supported media types for retrieve:
    - multipart/related (with or without type parameter)
    - application/dicom
    - */* (wildcard, accepts all)
    - Empty/None (default to multipart/related)

    Args:
        accept_header: Accept header value from request

    Returns:
        True if acceptable, False if should return 406

    Examples:
        >>> validate_accept_for_retrieve("multipart/related")
        True
        >>> validate_accept_for_retrieve("application/dicom")
        True
        >>> validate_accept_for_retrieve("*/*")
        True
        >>> validate_accept_for_retrieve(None)
        True
        >>> validate_accept_for_retrieve("text/plain")
        False
        >>> validate_accept_for_retrieve("image/jpeg")
        False
    """
    # No Accept header or empty - default behavior (accept)
    if not accept_header or not accept_header.strip():
        return True

    # Normalize to lowercase for case-insensitive comparison
    accept_lower = accept_header.lower()

    # Wildcard accepts everything
    if "*/*" in accept_lower:
        return True

    # Supported WADO-RS retrieve media types
    if "multipart/related" in accept_lower:
        return True
    if "application/dicom" in accept_lower:
        return True

    # Unsupported media type
    return False


def validate_accept_for_rendered(accept_header: str | None) -> bool:
    """Validate Accept header for WADO-RS rendered endpoints.

    Supported media types for rendered:
    - image/jpeg
    - image/png
    - */* (wildcard, accepts all)
    - Empty/None (default to image/jpeg)

    Args:
        accept_header: Accept header value from request

    Returns:
        True if acceptable, False if should return 406

    Examples:
        >>> validate_accept_for_rendered("image/jpeg")
        True
        >>> validate_accept_for_rendered("image/png")
        True
        >>> validate_accept_for_rendered("*/*")
        True
        >>> validate_accept_for_rendered(None)
        True
        >>> validate_accept_for_rendered("application/dicom")
        False
        >>> validate_accept_for_rendered("text/plain")
        False
    """
    # No Accept header or empty - default behavior (accept)
    if not accept_header or not accept_header.strip():
        return True

    # Normalize to lowercase for case-insensitive comparison
    accept_lower = accept_header.lower()

    # Wildcard accepts everything
    if "*/*" in accept_lower:
        return True

    # Supported rendered media types
    if "image/jpeg" in accept_lower:
        return True
    if "image/png" in accept_lower:
        return True

    # Unsupported media type
    return False
