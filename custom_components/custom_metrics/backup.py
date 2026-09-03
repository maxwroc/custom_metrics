"""Home Assistant backup coordination for the Custom Metrics database."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN, LOGGER

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .runtime_data import CustomMetricsConfigEntry


async def async_pre_backup(hass: HomeAssistant) -> None:
    """Drain writes and close each loaded database before backup files are copied."""
    entries: list[CustomMetricsConfigEntry] = hass.config_entries.async_loaded_entries(
        DOMAIN
    )
    prepared: list[CustomMetricsConfigEntry] = []
    try:
        for entry in entries:
            await entry.runtime_data.storage.async_prepare_backup()
            prepared.append(entry)
    except BaseException as err:
        for entry in reversed(prepared):
            try:
                await entry.runtime_data.storage.async_finish_backup()
            except BaseException:  # noqa: BLE001 - backup cleanup must survive cancellation
                LOGGER.exception("Failed to resume Custom Metrics after backup error")
        msg = "Could not prepare the Custom Metrics database for backup"
        raise HomeAssistantError(msg) from err


async def async_post_backup(hass: HomeAssistant) -> None:
    """Reopen and validate each database after backup finishes."""
    entries: list[CustomMetricsConfigEntry] = hass.config_entries.async_loaded_entries(
        DOMAIN
    )
    errors: list[BaseException] = []
    for entry in entries:
        try:
            await entry.runtime_data.storage.async_finish_backup()
        except BaseException as err:  # noqa: BLE001 - resume every loaded entry
            LOGGER.exception("Failed to reopen Custom Metrics after backup")
            errors.append(err)
    if errors:
        msg = "Could not reopen the Custom Metrics database after backup"
        raise HomeAssistantError(msg) from errors[0]
