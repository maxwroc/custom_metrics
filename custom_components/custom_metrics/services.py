"""The custom_metrics.add_record service."""

from __future__ import annotations

from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import (
    ATTR_FIELDS,
    ATTR_RECORD_TYPE,
    ATTR_TIMESTAMP,
    DOMAIN,
    SERVICE_ADD_RECORD,
)
from .media_store import async_resolve_image_fields
from .record_view import to_public_record
from .schema import validate_record_data

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse

    from .runtime_data import CustomMetricsRuntimeData

SERVICE_ADD_RECORD_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_RECORD_TYPE): cv.string,
        vol.Required(ATTR_FIELDS): dict,
        vol.Optional(ATTR_TIMESTAMP): cv.datetime,
    }
)


def _get_runtime_data(hass: HomeAssistant) -> CustomMetricsRuntimeData:
    """Return the runtime data for the (single) loaded config entry."""
    entries = hass.config_entries.async_entries(DOMAIN)
    loaded = [entry for entry in entries if entry.state is ConfigEntryState.LOADED]
    if not loaded:
        msg = "Custom Metrics Recorder is not set up"
        raise ServiceValidationError(msg)
    return loaded[0].runtime_data


async def _async_add_record(call: ServiceCall) -> ServiceResponse:
    """Handle the custom_metrics.add_record service call."""
    runtime_data = _get_runtime_data(call.hass)
    record_type_id = call.data[ATTR_RECORD_TYPE]
    record_type = runtime_data.record_types.get(record_type_id)
    if record_type is None:
        msg = f"Unknown record_type '{record_type_id}'"
        raise ServiceValidationError(msg)

    try:
        validated_fields = validate_record_data(record_type, call.data[ATTR_FIELDS])
    except vol.Invalid as err:
        raise ServiceValidationError(str(err)) from err

    try:
        validated_fields = await async_resolve_image_fields(
            runtime_data.media_store, record_type, validated_fields
        )
    except ValueError as err:
        raise ServiceValidationError(str(err)) from err

    record = await runtime_data.storage.async_add_record(
        record_type_id,
        validated_fields,
        timestamp=call.data.get(ATTR_TIMESTAMP),
    )
    return to_public_record(record)


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the custom_metrics services (module-level, hass-wide)."""
    if hass.services.has_service(DOMAIN, SERVICE_ADD_RECORD):
        return
    hass.services.async_register(
        DOMAIN,
        SERVICE_ADD_RECORD,
        _async_add_record,
        schema=SERVICE_ADD_RECORD_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
