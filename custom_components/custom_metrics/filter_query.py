"""
Compile a card's `filter` config into a server-side record predicate (P0-9).

Config shape: a list of single-key {field_key: value} maps, AND-combined
(every item must match), e.g.:

    filter:
      - name: "!= Max"
      - age: "> 30"

Each item's value is either a native YAML/JSON scalar (int/float/bool - used
directly with an implied "==") or a string optionally prefixed with one of
"==", "!=", ">=", "<=", ">", "<" (checked longest-token-first so ">="/"<="
aren't mis-split into ">"/"<" plus a leftover "="). No operator prefix means
"==". No quoting is parsed inside string values - YAML's own quoting already
delivers a plain Python string, so there's nothing further to strip.
"""

from __future__ import annotations

import operator as _operator
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.util import dt as dt_util

from .const import FieldType
from .schema import validate_filter_value

if TYPE_CHECKING:
    from collections.abc import Callable

    from .models import RecordType

# Operator tokens, longest first so ">="/"<=" aren't mis-split into ">"/"<".
_OPERATOR_TOKENS = ("==", "!=", ">=", "<=", ">", "<")

# Which operators are valid for each field type. MULTI_SELECT only supports
# ==/!= with membership semantics (does the stored list contain this value),
# not full-list equality. IMAGE fields are never filterable (rejected before
# this table is even consulted - see _compile_condition).
_ALLOWED_OPERATORS: dict[FieldType, frozenset[str]] = {
    FieldType.NUMBER: frozenset({"==", "!=", ">", ">=", "<", "<="}),
    FieldType.TEXT: frozenset({"==", "!="}),
    FieldType.LONG_TEXT: frozenset({"==", "!="}),
    FieldType.BOOLEAN: frozenset({"==", "!="}),
    FieldType.DATETIME: frozenset({"==", "!=", ">", ">=", "<", "<="}),
    FieldType.SINGLE_SELECT: frozenset({"==", "!="}),
    FieldType.MULTI_SELECT: frozenset({"==", "!="}),
}

_COMPARATORS: dict[str, Callable[[Any, Any], bool]] = {
    "==": _operator.eq,
    "!=": _operator.ne,
    ">": _operator.gt,
    ">=": _operator.ge,
    "<": _operator.lt,
    "<=": _operator.le,
}


# Machine-readable FilterError codes (also asserted on directly in tests).
ERR_UNKNOWN_FIELD = "unknown_filter_field"
ERR_UNSUPPORTED_FIELD = "unsupported_filter_field"
ERR_UNSUPPORTED_OPERATOR = "unsupported_filter_operator"
ERR_INVALID_VALUE = "invalid_filter_value"
ERR_INVALID_ITEM = "invalid_filter_item"


class FilterError(Exception):
    """Raised when a `filter` config value can't be compiled into a predicate."""

    def __init__(self, code: str, message: str) -> None:
        """Store a machine-readable error code alongside the message."""
        super().__init__(message)
        self.code = code
        self.message = message


def _split_operator(raw_value: Any) -> tuple[str, Any]:
    """Return (operator, raw literal) for one filter item's value."""
    if not isinstance(raw_value, str):
        # Native YAML/JSON scalar (int/float/bool) - implied "==", no parsing.
        return "==", raw_value
    for token in _OPERATOR_TOKENS:
        if raw_value.startswith(token):
            return token, raw_value[len(token) :].strip()
    return "==", raw_value.strip()


def _normalize_datetime(value: Any) -> Any:
    """
    Parse a stored DATETIME field value that may be a str or a datetime.

    A user-defined `datetime`-type field's stored value can be a Python
    datetime object (freshly added, never round-tripped through storage) or
    an ISO string (after a save/reload) - schema.py coerces on write via
    cv.datetime but nothing normalizes it back for storage afterwards.
    Comparing a str to a datetime raises TypeError, so both sides are
    normalized through this before any comparison.
    """
    if isinstance(value, str):
        return dt_util.parse_datetime(value)
    return value


def _compile_condition(
    record_type: RecordType, field_key: str, raw_value: Any
) -> Callable[[dict[str, Any]], bool]:
    """Compile one {field_key: raw_value} filter item into a predicate."""
    field_def = record_type.get_field(field_key)
    if field_def is None:
        msg = f"Unknown filter field '{field_key}'"
        raise FilterError(ERR_UNKNOWN_FIELD, msg)
    if field_def.type is FieldType.IMAGE:
        msg = f"Field '{field_key}' (image) cannot be filtered on"
        raise FilterError(ERR_UNSUPPORTED_FIELD, msg)

    op, literal = _split_operator(raw_value)
    if op not in _ALLOWED_OPERATORS[field_def.type]:
        msg = (
            f"Operator '{op}' is not supported for field '{field_key}' "
            f"({field_def.type.value})"
        )
        raise FilterError(ERR_UNSUPPORTED_OPERATOR, msg)

    try:
        coerced = validate_filter_value(field_def, literal)
    except vol.Invalid as err:
        msg = f"Invalid filter value for field '{field_key}': {err}"
        raise FilterError(ERR_INVALID_VALUE, msg) from err

    if field_def.type is FieldType.MULTI_SELECT:

        def predicate(data: dict[str, Any]) -> bool:
            if field_key not in data:
                return False
            is_member = coerced in (data[field_key] or [])
            return is_member if op == "==" else not is_member

        return predicate

    if field_def.type is FieldType.DATETIME:
        compare = _COMPARATORS[op]

        def predicate(data: dict[str, Any]) -> bool:
            if field_key not in data:
                return False
            stored = _normalize_datetime(data[field_key])
            if stored is None:
                return False
            return compare(stored, coerced)

        return predicate

    compare = _COMPARATORS[op]

    def predicate(data: dict[str, Any]) -> bool:
        if field_key not in data:
            return False
        return compare(data[field_key], coerced)

    return predicate


def compile_record_filter(
    record_type: RecordType, filter_list: Any
) -> Callable[[dict[str, Any]], bool] | None:
    """
    Compile a card's `filter` config into a single record predicate.

    `filter_list` must be a list of single-key {field_key: value} maps,
    AND-combined (every item must match). Returns None if filter_list is
    falsy (no filtering configured). Raises FilterError - never a bare
    exception - on any problem, so callers can map it straight to a
    websocket `send_error`.
    """
    if not filter_list:
        return None
    if not isinstance(filter_list, list):
        msg = "'filter' must be a list of single-key field maps"
        raise FilterError(ERR_INVALID_ITEM, msg)

    try:
        conditions: list[Callable[[dict[str, Any]], bool]] = []
        for item in filter_list:
            if not isinstance(item, dict) or len(item) != 1:
                msg = (
                    "Each 'filter' item must be a single-key map, "
                    "e.g. {field_key: value}"
                )
                raise FilterError(ERR_INVALID_ITEM, msg)  # noqa: TRY301
            ((field_key, raw_value),) = item.items()
            if not isinstance(field_key, str):
                msg = "Filter field keys must be strings"
                raise FilterError(ERR_INVALID_ITEM, msg)  # noqa: TRY301
            conditions.append(_compile_condition(record_type, field_key, raw_value))
    except FilterError:
        raise
    except Exception as err:
        msg = f"Invalid filter: {err}"
        raise FilterError(ERR_INVALID_ITEM, msg) from err

    def combined(data: dict[str, Any]) -> bool:
        return all(condition(data) for condition in conditions)

    return combined
