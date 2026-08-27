"""Tests for the integration's setup/unload/remove lifecycle."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.custom_metrics.store import RecordStorage

from .conftest import BP_RECORD_TYPE, async_setup_entry_with_types


async def test_setup_and_unload_entry(hass: HomeAssistant) -> None:
    """The entry loads, then unloads cleanly without leaking listeners."""
    entry = await async_setup_entry_with_types(hass)
    assert entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_unload_does_not_delete_data(hass: HomeAssistant) -> None:
    """A plain unload must not delete stored records."""
    entry = await async_setup_entry_with_types(hass, [BP_RECORD_TYPE])
    await entry.runtime_data.storage.async_add_record("bp", {"systolic": 120})
    await entry.runtime_data.storage.async_flush()

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    reloaded = RecordStorage(hass, entry.entry_id)
    await reloaded.async_load(["bp"])
    assert reloaded.record_count("bp") == 1


async def test_remove_entry_deletes_storage(hass: HomeAssistant) -> None:
    """Removing the entry (uninstall) deletes all of its stored records."""
    entry = await async_setup_entry_with_types(hass, [BP_RECORD_TYPE])
    await entry.runtime_data.storage.async_add_record("bp", {"systolic": 120})
    await entry.runtime_data.storage.async_flush()

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    reloaded = RecordStorage(hass, entry.entry_id)
    await reloaded.async_load(["bp"])
    assert reloaded.record_count("bp") == 0


async def test_reload_picks_up_new_options(hass: HomeAssistant) -> None:
    """Updating options (adding a record type) triggers a reload that picks it up."""
    entry = await async_setup_entry_with_types(hass)
    assert entry.runtime_data.record_types == {}

    hass.config_entries.async_update_entry(
        entry, options={"record_types": [BP_RECORD_TYPE]}
    )
    await hass.async_block_till_done()

    assert "bp" in entry.runtime_data.record_types
