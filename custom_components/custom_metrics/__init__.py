"""
Custom Metrics Recorder.

A Home Assistant integration for recording user-defined metrics (blood
pressure, fuel costs, doorbell snapshots, etc.) via a service call, exposed to
custom Lovelace cards through a small WebSocket API.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_time_interval

from .const import CONF_RECORD_TYPES, DEFAULT_WARN_AT, DOMAIN, LOGGER
from .models import RecordType
from .runtime_data import CustomMetricsRuntimeData
from .services import async_setup_services
from .store import RecordStorage
from .websocket_api import async_setup_websocket_api

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.typing import ConfigType

    from .runtime_data import CustomMetricsConfigEntry

PURGE_INTERVAL = timedelta(hours=24)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:  # noqa: ARG001
    """Set up global (hass-wide) services and WebSocket commands."""
    async_setup_services(hass)
    async_setup_websocket_api(hass)
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: CustomMetricsConfigEntry
) -> bool:
    """Set up this integration's config entry."""
    record_types = {
        rt.id: rt
        for rt in (
            RecordType.from_dict(raw)
            for raw in entry.options.get(CONF_RECORD_TYPES, [])
        )
    }

    storage = RecordStorage(hass, entry.entry_id)
    await storage.async_load(record_types.keys())

    entry.runtime_data = CustomMetricsRuntimeData(
        storage=storage, record_types=record_types
    )

    async def _async_purge_job(_now: object) -> None:
        await _async_run_purge(hass, entry)

    entry.runtime_data.unsub_purge_interval = async_track_time_interval(
        hass, _async_purge_job, PURGE_INTERVAL
    )

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def async_unload_entry(
    _hass: HomeAssistant, entry: CustomMetricsConfigEntry
) -> bool:
    """
    Unload a config entry: cancel listeners, flush pending saves.

    Must NOT delete any stored data - that only happens in async_remove_entry.
    """
    if entry.runtime_data.unsub_purge_interval is not None:
        entry.runtime_data.unsub_purge_interval()
    await entry.runtime_data.storage.async_flush()
    return True


async def async_remove_entry(
    hass: HomeAssistant, entry: CustomMetricsConfigEntry
) -> None:
    """
    Delete all stored records when the user removes the integration.

    entry.runtime_data may already be gone by the time this runs (HA clears it
    once async_unload_entry has completed), so the storage is reconstructed
    from the entry's persisted options rather than relying on runtime_data.
    """
    record_type_ids = [rt["id"] for rt in entry.options.get(CONF_RECORD_TYPES, [])]
    storage = RecordStorage(hass, entry.entry_id)
    await storage.async_load(record_type_ids)
    await storage.async_remove()


async def _async_update_listener(
    hass: HomeAssistant, entry: CustomMetricsConfigEntry
) -> None:
    """Reload the entry when its options (record types) change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_run_purge(
    hass: HomeAssistant, entry: CustomMetricsConfigEntry
) -> None:
    """Purge expired records, enforce max_records, and manage Repairs warnings."""
    runtime_data = entry.runtime_data
    record_types = runtime_data.record_types

    await runtime_data.storage.async_purge_expired(
        {rt_id: rt.retention_days for rt_id, rt in record_types.items()}
    )
    await runtime_data.storage.async_enforce_max_records(
        {rt_id: rt.max_records for rt_id, rt in record_types.items()}
    )

    for rt_id, record_type in record_types.items():
        warn_at = record_type.warn_at or DEFAULT_WARN_AT
        count = runtime_data.storage.record_count(rt_id)
        issue_id = f"record_count_{rt_id}"
        if count >= warn_at:
            ir.async_create_issue(
                hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="record_count_high",
                translation_placeholders={
                    "name": record_type.name,
                    "count": str(count),
                },
            )
        else:
            ir.async_delete_issue(hass, DOMAIN, issue_id)

    LOGGER.debug("Purge job completed for entry %s", entry.entry_id)
