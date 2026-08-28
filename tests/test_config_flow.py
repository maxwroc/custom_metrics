"""Tests for the config flow and the record_type subentry flow."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.custom_metrics.const import DOMAIN, SUBENTRY_TYPE_RECORD_TYPE

from .conftest import BP_RECORD_TYPE, async_setup_entry_with_types


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
