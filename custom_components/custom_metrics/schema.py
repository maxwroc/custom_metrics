"""Build dynamic voluptuous schemas for validating record field data."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.helpers import config_validation as cv

from .const import FieldType

if TYPE_CHECKING:
    from .models import FieldDefinition, RecordType


def _validator_for_field(field_def: FieldDefinition) -> Any:
    """Return the voluptuous validator for a single field definition."""
    if field_def.type is FieldType.SINGLE_SELECT:
        return vol.In(field_def.options or [])
    if field_def.type is FieldType.MULTI_SELECT:
        return [vol.In(field_def.options or [])]

    # IMAGE: input value is a filesystem path handed off to media_store.py,
    # not the stored reference object - it shares the plain-string validator.
    simple_validators: dict[FieldType, Any] = {
        FieldType.NUMBER: vol.Coerce(float),
        FieldType.BOOLEAN: cv.boolean,
        FieldType.DATETIME: cv.datetime,
        FieldType.TEXT: cv.string,
        FieldType.LONG_TEXT: cv.string,
        FieldType.IMAGE: cv.string,
    }
    return simple_validators[field_def.type]


def build_fields_schema(record_type: RecordType) -> vol.Schema:
    """Build a voluptuous Schema validating the 'fields' dict of a record type."""
    schema_dict: dict[Any, Any] = {}
    for field_def in record_type.fields:
        validator = _validator_for_field(field_def)
        key: Any
        if field_def.required:
            key = vol.Required(field_def.key)
        elif field_def.default is not None:
            key = vol.Optional(field_def.key, default=field_def.default)
        else:
            key = vol.Optional(field_def.key)
        schema_dict[key] = validator
    return vol.Schema(schema_dict)


def build_import_field_validators(record_type: RecordType) -> dict[str, Any]:
    """
    Build a per-field-key validator map for CSV import (csv_transfer.py).

    Returns one callable validator per non-IMAGE field (IMAGE fields are
    already-finalized reference objects on import, not a source path to
    validate - see csv_transfer.py). Each validator is wrapped in
    `vol.Schema(...)` so it's directly callable on a bare value (e.g.
    multi_select's `_validator_for_field` returns a bare list `[vol.In(...)]`,
    which is only usable as a dict-schema value, not callable on its own,
    unless it's itself wrapped by a `vol.Schema`).
    """
    return {
        field_def.key: vol.Schema(_validator_for_field(field_def))
        for field_def in record_type.fields
        if field_def.type is not FieldType.IMAGE
    }


def validate_record_data(
    record_type: RecordType, data: dict[str, Any]
) -> dict[str, Any]:
    """
    Validate and coerce a record's field data against its record type.

    Raises vol.Invalid on failure.
    """
    schema = build_fields_schema(record_type)
    return schema(data)
