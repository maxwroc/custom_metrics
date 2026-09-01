"""Tests for the config flow and the record_type subentry flow."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, call

from aiohttp import FormData
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component

from custom_components.custom_metrics.const import DOMAIN, SUBENTRY_TYPE_RECORD_TYPE

from .conftest import BP_RECORD_TYPE, async_setup_entry_with_types

if TYPE_CHECKING:
    from pytest_homeassistant_custom_component.typing import ClientSessionGenerator


def _subentry_id(entry: config_entries.ConfigEntry, unique_id: str) -> str:
    """Look up a subentry's internal subentry_id by its unique_id (record type id)."""
    return next(
        se.subentry_id for se in entry.subentries.values() if se.unique_id == unique_id
    )


async def _init_add_flow(
    hass: HomeAssistant, entry: config_entries.ConfigEntry
) -> config_entries.SubentryFlowResult:
    """Start the 'add a new record type' subentry flow."""
    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_RECORD_TYPE),
        context={"source": config_entries.SOURCE_USER, "entry_id": entry.entry_id},
    )


async def _init_reconfigure_flow(
    hass: HomeAssistant, entry: config_entries.ConfigEntry, unique_id: str
) -> config_entries.SubentryFlowResult:
    """Start the 'reconfigure an existing record type' subentry flow."""
    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_RECORD_TYPE),
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
            "subentry_id": _subentry_id(entry, unique_id),
        },
    )


async def test_user_flow_creates_single_entry(hass: HomeAssistant) -> None:
    """The user step has nothing to configure and just creates the entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Custom Metrics Recorder"


async def test_add_record_type_and_field(hass: HomeAssistant) -> None:
    """Adding a record type then a field creates a subentry with that data."""
    entry = await async_setup_entry_with_types(hass)

    result = await _init_add_flow(hass, entry)
    assert result["step_id"] == "user"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Blood Pressure"}
    )
    assert result["step_id"] == "add_field"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"key": "systolic", "type": "number", "required": True, "add_another": False},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["unique_id"] == "blood_pressure"
    assert result["data"]["fields"][0]["key"] == "systolic"

    await hass.async_block_till_done()
    assert "blood_pressure" in entry.runtime_data.record_types


async def test_add_field_rejects_reserved_key(hass: HomeAssistant) -> None:
    """Reserved field keys (id/timestamp/record_type) are rejected with an error."""
    entry = await async_setup_entry_with_types(hass)
    result = await _init_add_flow(hass, entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Test"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"key": "timestamp", "type": "text", "required": False, "add_another": False},
    )
    assert result["step_id"] == "add_field"
    assert result["errors"] == {"key": "reserved_key"}


async def test_add_field_requires_options_for_select_types(hass: HomeAssistant) -> None:
    """single_select/multi_select fields require at least one option."""
    entry = await async_setup_entry_with_types(hass)
    result = await _init_add_flow(hass, entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Mood"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "key": "mood",
            "type": "single_select",
            "required": False,
            "add_another": False,
        },
    )
    assert result["errors"] == {"options": "options_required"}


async def test_add_record_type_name_collision(hass: HomeAssistant) -> None:
    """A name that slugifies to an existing record type id is rejected."""
    entry = await async_setup_entry_with_types(hass)

    result = await _init_add_flow(hass, entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Blood Pressure"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"key": "systolic", "type": "number", "required": True, "add_another": False},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    result = await _init_add_flow(hass, entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Blood Pressure"}
    )
    assert result["step_id"] == "user"
    assert result["errors"] == {"name": "already_exists"}


async def test_reconfigure_menu(hass: HomeAssistant) -> None:
    """Reconfiguring a record type shows the expected management menu."""
    entry = await async_setup_entry_with_types(hass, [BP_RECORD_TYPE])
    result = await _init_reconfigure_flow(hass, entry, "bp")
    assert result["type"] is FlowResultType.MENU
    assert set(result["menu_options"]) == {
        "manage_fields",
        "reconfigure_add_field",
        "set_retention",
        "change_type_key",
        "export_data",
        "import_data",
    }


async def test_reconfigure_add_field(hass: HomeAssistant) -> None:
    """Adding a field via reconfigure appends to the existing field list."""
    entry = await async_setup_entry_with_types(hass, [BP_RECORD_TYPE])
    result = await _init_reconfigure_flow(hass, entry, "bp")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "reconfigure_add_field"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"key": "diastolic", "type": "number", "required": True, "add_another": False},
    )
    assert result["type"] is FlowResultType.ABORT
    await hass.async_block_till_done()

    record_type = entry.runtime_data.record_types["bp"]
    assert {f.key for f in record_type.fields} == {"systolic", "diastolic"}


async def test_edit_field_label(hass: HomeAssistant) -> None:
    """Editing a field's label leaves its key (and stored data) untouched."""
    entry = await async_setup_entry_with_types(hass, [BP_RECORD_TYPE])
    result = await _init_reconfigure_flow(hass, entry, "bp")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "manage_fields"}
    )
    assert result["step_id"] == "manage_fields"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"field_key": "systolic"}
    )
    assert result["type"] is FlowResultType.MENU
    assert set(result["menu_options"]) == {
        "edit_field_label",
        "change_field_key",
        "delete_field",
    }
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "edit_field_label"}
    )
    assert result["step_id"] == "edit_field_label"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"label": "Systolic (mmHg)"}
    )
    assert result["type"] is FlowResultType.ABORT
    await hass.async_block_till_done()

    record_type = entry.runtime_data.record_types["bp"]
    field = record_type.get_field("systolic")
    assert field.label == "Systolic (mmHg)"


async def test_change_field_key_requires_confirmation(hass: HomeAssistant) -> None:
    """Changing a field's key without ticking the confirmation box is rejected."""
    entry = await async_setup_entry_with_types(hass, [BP_RECORD_TYPE])
    result = await _init_reconfigure_flow(hass, entry, "bp")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "manage_fields"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"field_key": "systolic"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "change_field_key"}
    )
    assert result["step_id"] == "change_field_key"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"new_key": "sys", "confirm": False}
    )
    assert result["step_id"] == "change_field_key"
    assert result["errors"] == {"confirm": "confirmation_required"}


async def test_change_field_key_migrates_stored_records(hass: HomeAssistant) -> None:
    """Confirmed field-key changes rename the key in every stored record too."""
    entry = await async_setup_entry_with_types(hass, [BP_RECORD_TYPE])
    await entry.runtime_data.storage.async_add_record("bp", {"systolic": 120})

    result = await _init_reconfigure_flow(hass, entry, "bp")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "manage_fields"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"field_key": "systolic"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "change_field_key"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"new_key": "sys", "confirm": True}
    )
    assert result["type"] is FlowResultType.ABORT
    await hass.async_block_till_done()

    record_type = entry.runtime_data.record_types["bp"]
    assert record_type.get_field("sys") is not None
    assert record_type.get_field("systolic") is None
    records = entry.runtime_data.storage.async_list_records("bp")
    assert records[0]["d"] == {"sys": 120}


async def test_delete_field_requires_confirmation(hass: HomeAssistant) -> None:
    """Deleting a field without ticking the confirmation box is rejected."""
    entry = await async_setup_entry_with_types(hass, [BP_RECORD_TYPE])
    result = await _init_reconfigure_flow(hass, entry, "bp")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "manage_fields"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"field_key": "systolic"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "delete_field"}
    )
    assert result["step_id"] == "delete_field"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"confirm": False}
    )
    assert result["step_id"] == "delete_field"
    assert result["errors"] == {"confirm": "confirmation_required"}


async def test_delete_field_removes_it(hass: HomeAssistant) -> None:
    """Confirmed field deletion removes it from the record type's field list."""
    entry = await async_setup_entry_with_types(hass, [BP_RECORD_TYPE])
    result = await _init_reconfigure_flow(hass, entry, "bp")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "manage_fields"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"field_key": "systolic"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "delete_field"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"confirm": True}
    )
    assert result["type"] is FlowResultType.ABORT
    await hass.async_block_till_done()

    record_type = entry.runtime_data.record_types["bp"]
    assert record_type.get_field("systolic") is None


async def test_change_type_key_migrates_storage(hass: HomeAssistant) -> None:
    """Confirmed record-type key changes rename the underlying Store file too."""
    entry = await async_setup_entry_with_types(hass, [BP_RECORD_TYPE])
    await entry.runtime_data.storage.async_add_record("bp", {"systolic": 120})
    await entry.runtime_data.storage.async_flush()

    result = await _init_reconfigure_flow(hass, entry, "bp")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "change_type_key"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"new_key": "blood_pressure", "confirm": True}
    )
    assert result["type"] is FlowResultType.ABORT
    await hass.async_block_till_done()

    assert "bp" not in entry.runtime_data.record_types
    assert "blood_pressure" in entry.runtime_data.record_types
    records = entry.runtime_data.storage.async_list_records("blood_pressure")
    assert len(records) == 1
    assert records[0]["d"] == {"systolic": 120}


async def test_change_type_key_rejects_unsafe_ids(hass: HomeAssistant) -> None:
    """Record type ids used in media paths must remain slug-safe."""
    entry = await async_setup_entry_with_types(hass, [BP_RECORD_TYPE])

    for unsafe_id in ("/config", "..", "../outside", "nested/type"):
        result = await _init_reconfigure_flow(hass, entry, "bp")
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"next_step_id": "change_type_key"}
        )
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"new_key": unsafe_id, "confirm": True}
        )

        assert result["step_id"] == "change_type_key"
        assert result["errors"] == {"new_key": "invalid_key"}


async def test_change_type_key_rolls_back_media_on_storage_failure(
    hass: HomeAssistant,
) -> None:
    """A storage rename failure restores media and leaves the subentry unchanged."""
    entry = await async_setup_entry_with_types(hass, [BP_RECORD_TYPE])
    media_rename = AsyncMock()
    entry.runtime_data.media_store.async_rename_record_type = media_rename
    entry.runtime_data.storage.async_rename_record_type = AsyncMock(
        side_effect=OSError("storage unavailable")
    )

    result = await _init_reconfigure_flow(hass, entry, "bp")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "change_type_key"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"new_key": "blood_pressure", "confirm": True}
    )

    assert result["step_id"] == "change_type_key"
    assert result["errors"] == {"base": "rename_failed"}
    assert media_rename.await_args_list == [
        call("bp", "blood_pressure"),
        call("blood_pressure", "bp"),
    ]
    assert next(iter(entry.subentries.values())).unique_id == "bp"


async def test_set_retention_values(hass: HomeAssistant) -> None:
    """Retention/max_records/warn_at can be set for an existing record type."""
    entry = await async_setup_entry_with_types(hass, [BP_RECORD_TYPE])
    result = await _init_reconfigure_flow(hass, entry, "bp")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "set_retention"}
    )
    assert result["step_id"] == "set_retention"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"retention_days": 30, "max_records": 1000, "warn_at": 500}
    )
    assert result["type"] is FlowResultType.ABORT
    await hass.async_block_till_done()

    record_type = entry.runtime_data.record_types["bp"]
    assert record_type.retention_days == 30
    assert record_type.max_records == 1000
    assert record_type.warn_at == 500


async def test_set_retention_rejects_non_positive_values(
    hass: HomeAssistant,
) -> None:
    """Retention settings must be positive when configured."""
    entry = await async_setup_entry_with_types(hass, [BP_RECORD_TYPE])
    result = await _init_reconfigure_flow(hass, entry, "bp")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "set_retention"}
    )

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"retention_days": 0, "max_records": -1, "warn_at": 0}
    )

    assert result["step_id"] == "set_retention"
    assert result["errors"] == {
        "retention_days": "positive_integer",
        "max_records": "positive_integer",
        "warn_at": "positive_integer",
    }


async def test_export_data_returns_signed_download_url(hass: HomeAssistant) -> None:
    """export_data aborts with a signed download link reflecting include_id."""
    assert await async_setup_component(hass, "http", {})
    entry = await async_setup_entry_with_types(hass, [BP_RECORD_TYPE])
    result = await _init_reconfigure_flow(hass, entry, "bp")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "export_data"}
    )
    assert result["step_id"] == "export_data"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"include_id": True}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "export_ready"
    download_url = result["description_placeholders"]["download_url"]
    assert f"/{DOMAIN}_export/{entry.entry_id}/bp" in download_url
    assert "include_id=true" in download_url
    assert "authSig=" in download_url


async def test_export_data_include_id_false_reflected_in_url(
    hass: HomeAssistant,
) -> None:
    """Unchecking include_id is reflected in the signed download link."""
    assert await async_setup_component(hass, "http", {})
    entry = await async_setup_entry_with_types(hass, [BP_RECORD_TYPE])
    result = await _init_reconfigure_flow(hass, entry, "bp")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "export_data"}
    )

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"include_id": False}
    )

    assert "include_id=false" in result["description_placeholders"]["download_url"]


async def _upload_csv(client: ClientSessionGenerator, csv_text: str) -> str:
    """Upload a CSV file via the standard /api/file_upload endpoint; return file_id."""
    form = FormData()
    form.add_field(
        "file", csv_text.encode(), filename="import.csv", content_type="text/csv"
    )
    resp = await client.post("/api/file_upload", data=form)
    assert resp.status == 200
    return (await resp.json())["file_id"]


async def test_import_data_happy_path(
    hass: HomeAssistant, hass_client: ClientSessionGenerator
) -> None:
    """Uploading a CSV imports its rows and shows a summary abort."""
    assert await async_setup_component(hass, "http", {})
    assert await async_setup_component(hass, "file_upload", {})
    entry = await async_setup_entry_with_types(hass, [BP_RECORD_TYPE])

    client = await hass_client()
    file_id = await _upload_csv(
        client, "id,timestamp,systolic\n,2026-01-01T10:00:00+00:00,120\n"
    )

    result = await _init_reconfigure_flow(hass, entry, "bp")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "import_data"}
    )
    assert result["step_id"] == "import_data"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"file": file_id}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "import_complete"
    assert result["description_placeholders"]["imported"] == "1"
    assert result["description_placeholders"]["skipped"] == "0"
    assert entry.runtime_data.storage.record_count("bp") == 1


async def test_import_data_skips_duplicate_id_on_reimport(
    hass: HomeAssistant, hass_client: ClientSessionGenerator
) -> None:
    """Re-importing the same file (same id) is idempotent - skipped, not duplicated."""
    assert await async_setup_component(hass, "http", {})
    assert await async_setup_component(hass, "file_upload", {})
    entry = await async_setup_entry_with_types(hass, [BP_RECORD_TYPE])
    csv_text = "id,timestamp,systolic\nfixed-id,2026-01-01T10:00:00+00:00,120\n"
    client = await hass_client()

    for _ in range(2):
        file_id = await _upload_csv(client, csv_text)
        result = await _init_reconfigure_flow(hass, entry, "bp")
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"next_step_id": "import_data"}
        )
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"file": file_id}
        )

    assert result["description_placeholders"]["imported"] == "0"
    assert result["description_placeholders"]["skipped"] == "1"
    assert entry.runtime_data.storage.record_count("bp") == 1
