"""Constants for custom_metrics."""

from __future__ import annotations

from enum import StrEnum
from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "custom_metrics"
STORAGE_VERSION = 1
STORAGE_KEY_TEMPLATE = f"{DOMAIN}_{{entry_id}}_{{record_type_id}}"

# Delay (seconds) used for the debounced/coalesced Store save pattern.
SAVE_DELAY = 10

# Services
SERVICE_ADD_RECORD = "add_record"

# Service/record field names (public shape)
ATTR_RECORD_TYPE = "record_type"
ATTR_FIELDS = "fields"
ATTR_TIMESTAMP = "timestamp"
ATTR_RECORD_ID = "id"

# custom_metrics/list_records WebSocket command: optional page-size param, and
# a hard server-side cap applied regardless of what a caller (e.g. the card)
# requests, to keep response payload size bounded as a record type grows.
ATTR_LIMIT = "limit"
MAX_LIST_RECORDS_LIMIT = 500

# Reserved/compact keys used in the on-disk record envelope. These are internal
# only and never exposed to the user for renaming - kept short since they
# appear on every single stored record.
ENVELOPE_ID = "id"
ENVELOPE_TIMESTAMP = "t"
ENVELOPE_DATA = "d"

# Reserved words that cannot be used as user-defined field keys, since the
# public API flattens the envelope + field data into a single dict and a
# field named one of these would silently shadow the envelope's own value.
RESERVED_FIELD_KEYS = {"id", "timestamp", "record_type"}

# Options flow storage keys (config entry options) - legacy, pre-subentries
# storage format. Still read once at setup for a one-time migration into
# ConfigSubentry-based storage (see __init__.py); no longer written to.
CONF_RECORD_TYPES = "record_types"

# Each configured record type is stored as a ConfigSubentry of this type, so
# it appears as a visible, individually manageable sub-item directly on the
# integration's card in Settings > Devices & Services.
SUBENTRY_TYPE_RECORD_TYPE = "record_type"

# Default Repairs warning threshold (record count) per record type.
DEFAULT_WARN_AT = 5000

# Fired on hass.bus whenever a record type's data OR its own definition
# changes (add/delete/purge/max_records-eviction, or a reload triggered by a
# config subentry add/update/remove) - lets already-open Lovelace cards
# refetch instead of silently going stale. Payload is deliberately minimal
# (ids only, never field values) since bus events are broadcast to every
# authenticated listener and record data can be personal/sensitive.
EVENT_RECORDS_UPDATED = f"{DOMAIN}_updated"
ATTR_ENTRY_ID = "entry_id"


class FieldType(StrEnum):
    """Supported record field data types."""

    NUMBER = "number"
    TEXT = "text"
    LONG_TEXT = "long_text"
    BOOLEAN = "boolean"
    DATETIME = "datetime"
    SINGLE_SELECT = "single_select"
    MULTI_SELECT = "multi_select"
    IMAGE = "image"


# Field types that require a user-defined list of options.
SELECT_FIELD_TYPES = {FieldType.SINGLE_SELECT, FieldType.MULTI_SELECT}
