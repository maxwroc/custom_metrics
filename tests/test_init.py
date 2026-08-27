"""Tests for the integration's setup/unload/remove lifecycle."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.custom_metrics.store import RecordStorage

from .conftest import BP_RECORD_TYPE, async_setup_entry_with_types, make_source_image

IMAGE_RECORD_TYPE = {
    "id": "pets",
    "name": "Pets",
    "fields": [
        {
            "key": "photo",
            "label": "Photo",
            "type": "image",
            "required": False,
            "unit": None,
            "default": None,
            "options": None,
        },
    ],
    "timestamp_field": "timestamp",
    "retention_days": None,
    "max_records": None,
    "warn_at": None,
}


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


async def test_removing_record_type_purges_its_storage_and_media(
    hass: HomeAssistant,
) -> None:
    """Removing a record type from options purges its Store file and media dir."""
    entry = await async_setup_entry_with_types(
        hass, [BP_RECORD_TYPE, IMAGE_RECORD_TYPE]
    )
    await entry.runtime_data.storage.async_add_record("bp", {"systolic": 120})
    source = make_source_image(hass, name="cat.jpg")
    filename = await entry.runtime_data.media_store.async_store_image(
        "pets", str(source)
    )
    await entry.runtime_data.storage.async_add_record(
        "pets", {"photo": {"f": filename}}
    )
    await entry.runtime_data.storage.async_flush()

    pets_media_path = await entry.runtime_data.media_store.async_resolve_image_path(
        "pets", filename
    )
    assert pets_media_path.is_file()

    # Remove the "pets" record type, keeping "bp".
    hass.config_entries.async_update_entry(
        entry, options={"record_types": [BP_RECORD_TYPE]}
    )
    await hass.async_block_till_done()

    assert "pets" not in entry.runtime_data.record_types
    assert not pets_media_path.is_file()

    reloaded = RecordStorage(hass, entry.entry_id)
    await reloaded.async_load(["bp", "pets"])
    assert reloaded.record_count("bp") == 1
    assert reloaded.record_count("pets") == 0
