"""WebSocket API commands used by the custom Lovelace card."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.components.websocket_api.decorators import (
    async_response,
    websocket_command,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_FIELDS,
    ATTR_LIMIT,
    ATTR_RECORD_TYPE,
    ATTR_TIMESTAMP,
    DOMAIN,
    MAX_LIST_RECORDS_LIMIT,
)
from .media_store import async_resolve_image_fields, async_validate_image_path
from .record_view import to_public_record
from .schema import validate_record_data

if TYPE_CHECKING:
    from homeassistant.components.websocket_api.connection import ActiveConnection
    from homeassistant.core import HomeAssistant

    from .runtime_data import CustomMetricsRuntimeData

_WS_REGISTERED_KEY = f"{DOMAIN}_ws_registered"


def _get_runtime_data(hass: HomeAssistant) -> CustomMetricsRuntimeData | None:
    """Return the runtime data for the (single) loaded config entry, if any."""
    entries = hass.config_entries.async_entries(DOMAIN)
    loaded = [entry for entry in entries if entry.state is ConfigEntryState.LOADED]
    return loaded[0].runtime_data if loaded else None


@websocket_command({vol.Required("type"): "custom_metrics/list_record_types"})
@async_response
async def handle_list_record_types(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return all configured record types."""
    runtime_data = _get_runtime_data(hass)
    if runtime_data is None:
        connection.send_error(
            msg["id"], "not_setup", "Custom Metrics Recorder is not set up"
        )
        return
    connection.send_result(
        msg["id"],
        {"record_types": [rt.to_dict() for rt in runtime_data.record_types.values()]},
    )


@websocket_command(
    {
        vol.Required("type"): "custom_metrics/list_records",
        vol.Required(ATTR_RECORD_TYPE): str,
        vol.Optional("start"): str,
        vol.Optional("end"): str,
        vol.Optional(ATTR_LIMIT): vol.All(int, vol.Range(min=1)),
    }
)
@async_response
async def handle_list_records(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return records for a record type, optionally filtered by time range."""
    runtime_data = _get_runtime_data(hass)
    if runtime_data is None:
        connection.send_error(
            msg["id"], "not_setup", "Custom Metrics Recorder is not set up"
        )
        return
    record_type_id = msg[ATTR_RECORD_TYPE]
    if record_type_id not in runtime_data.record_types:
        connection.send_error(
            msg["id"], "unknown_record_type", f"Unknown record_type '{record_type_id}'"
        )
        return
    start = dt_util.parse_datetime(msg["start"]) if "start" in msg else None
    end = dt_util.parse_datetime(msg["end"]) if "end" in msg else None
    # Always apply a server-side cap, regardless of what the caller requests,
    # so response payload size stays bounded as a record type grows.
    if ATTR_LIMIT in msg:
        limit = min(msg[ATTR_LIMIT], MAX_LIST_RECORDS_LIMIT)
    else:
        limit = MAX_LIST_RECORDS_LIMIT
    records = runtime_data.storage.async_list_records(
        record_type_id, start=start, end=end, limit=limit
    )
    connection.send_result(
        msg["id"], {"records": [to_public_record(r) for r in records]}
    )


@websocket_command(
    {
        vol.Required("type"): "custom_metrics/add_record",
        vol.Required(ATTR_RECORD_TYPE): str,
        vol.Required(ATTR_FIELDS): dict,
        vol.Optional(ATTR_TIMESTAMP): str,
    }
)
@async_response
async def handle_add_record(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Add a record - a thin wrapper sharing the service's validation logic."""
    runtime_data = _get_runtime_data(hass)
    if runtime_data is None:
        connection.send_error(
            msg["id"], "not_setup", "Custom Metrics Recorder is not set up"
        )
        return
    record_type_id = msg[ATTR_RECORD_TYPE]
    record_type = runtime_data.record_types.get(record_type_id)
    if record_type is None:
        connection.send_error(
            msg["id"], "unknown_record_type", f"Unknown record_type '{record_type_id}'"
        )
        return
    try:
        validated_fields = validate_record_data(record_type, msg[ATTR_FIELDS])
    except vol.Invalid as err:
        connection.send_error(msg["id"], "invalid_fields", str(err))
        return
    try:
        validated_fields = await async_resolve_image_fields(
            runtime_data.media_store, record_type, validated_fields
        )
    except ValueError as err:
        connection.send_error(msg["id"], "invalid_image", str(err))
        return
    timestamp = (
        dt_util.parse_datetime(msg[ATTR_TIMESTAMP]) if ATTR_TIMESTAMP in msg else None
    )
    record = await runtime_data.storage.async_add_record(
        record_type_id, validated_fields, timestamp
    )
    connection.send_result(msg["id"], {"record": to_public_record(record)})


@websocket_command(
    {
        vol.Required("type"): "custom_metrics/delete_record",
        vol.Required(ATTR_RECORD_TYPE): str,
        vol.Required("record_id"): str,
    }
)
@async_response
async def handle_delete_record(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Delete a record by id."""
    runtime_data = _get_runtime_data(hass)
    if runtime_data is None:
        connection.send_error(
            msg["id"], "not_setup", "Custom Metrics Recorder is not set up"
        )
        return
    record_type_id = msg[ATTR_RECORD_TYPE]
    if record_type_id not in runtime_data.record_types:
        connection.send_error(
            msg["id"], "unknown_record_type", f"Unknown record_type '{record_type_id}'"
        )
        return
    deleted = await runtime_data.storage.async_delete_record(
        record_type_id, msg["record_id"]
    )
    if not deleted:
        connection.send_error(msg["id"], "not_found", "Record not found")
        return
    await runtime_data.media_store.async_cleanup_orphaned_media(
        runtime_data.storage, runtime_data.record_types
    )
    connection.send_result(msg["id"], {"deleted": True})


@websocket_command(
    {
        vol.Required("type"): "custom_metrics/validate_image_path",
        vol.Required("path"): str,
    }
)
@async_response
async def handle_validate_image_path(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Check whether a filesystem path is a valid, existing image file."""
    error = await async_validate_image_path(hass, msg["path"])
    connection.send_result(msg["id"], {"valid": error is None, "error": error})


def async_setup_websocket_api(hass: HomeAssistant) -> None:
    """Register the custom_metrics WebSocket commands once, hass-wide."""
    if hass.data.get(_WS_REGISTERED_KEY):
        return
    websocket_api.async_register_command(hass, handle_list_record_types)
    websocket_api.async_register_command(hass, handle_list_records)
    websocket_api.async_register_command(hass, handle_add_record)
    websocket_api.async_register_command(hass, handle_delete_record)
    websocket_api.async_register_command(hass, handle_validate_image_path)
    hass.data[_WS_REGISTERED_KEY] = True
