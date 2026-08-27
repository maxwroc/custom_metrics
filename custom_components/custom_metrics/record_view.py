"""
Shared helpers for the internal record envelope <-> public shape conversion.

Used by both services.py and websocket_api.py so the flattening logic lives in
one place.
"""

from __future__ import annotations

from typing import Any

from .const import (
    ATTR_RECORD_ID,
    ATTR_TIMESTAMP,
    ENVELOPE_DATA,
    ENVELOPE_ID,
    ENVELOPE_TIMESTAMP,
)


def to_public_record(record: dict[str, Any]) -> dict[str, Any]:
    """Flatten the internal envelope into the public {id, timestamp, ...} shape."""
    return {
        ATTR_RECORD_ID: record[ENVELOPE_ID],
        ATTR_TIMESTAMP: record[ENVELOPE_TIMESTAMP],
        **record[ENVELOPE_DATA],
    }
