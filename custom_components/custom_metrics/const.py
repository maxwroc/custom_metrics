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

# Options flow storage keys (config entry options)
CONF_RECORD_TYPES = "record_types"

# Default Repairs warning threshold (record count) per record type.
DEFAULT_WARN_AT = 5000


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
