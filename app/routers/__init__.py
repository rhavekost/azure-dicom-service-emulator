"""Router modules for API endpoints."""

from app.routers import (
    changefeed,
    debug,
    delete,
    dicomweb,
    extended_query_tags,
    operations,
    qido,
    stow,
    wado,
)

__all__ = [
    "stow",
    "wado",
    "qido",
    "delete",
    "dicomweb",  # backwards-compatible shim
    "changefeed",
    "extended_query_tags",
    "operations",
    "debug",
]
