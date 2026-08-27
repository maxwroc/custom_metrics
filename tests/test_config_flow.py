"""Tests for the config flow and options flow."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.custom_metrics.const import CONF_RECORD_TYPES, DOMAIN

from .conftest import BP_RECORD_TYPE, async_setup_entry_with_types


async def test_user_flow_creates_single_entry(hass: HomeAssistant) -> None:
    """The user step has nothing to configure and just creates the entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Custom Metrics Recorder"


async def test_options_menu_grows_with_record_types(hass: HomeAssistant) -> None:
    """The menu only offers edit/remove/retention once a record type exists."""
    entry = await async_setup_entry_with_types(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["add_record_type"]

    entry_with_type = await async_setup_entry_with_types(hass, [BP_RECORD_TYPE])
    result = await hass.config_entries.options.async_init(entry_with_type.entry_id)
    assert set(result["menu_options"]) == {
        "add_record_type",
        "edit_record_type",
        "remove_record_type",
        "set_retention",
    }


async def test_add_record_type_and_field(hass: HomeAssistant) -> None:
    """Adding a record type then a field persists a new RecordType in options."""
    entry = await async_setup_entry_with_types(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_record_type"}
    )
    assert result["step_id"] == "add_record_type"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "Blood Pressure"}
    )
    assert result["step_id"] == "add_field"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"key": "systolic", "type": "number", "required": True, "add_another": False},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY

    record_types = result["data"][CONF_RECORD_TYPES]
    assert len(record_types) == 1
    assert record_types[0]["id"] == "blood_pressure"
    assert record_types[0]["fields"][0]["key"] == "systolic"


async def test_add_field_rejects_reserved_key(hass: HomeAssistant) -> None:
    """Reserved field keys (id/timestamp/record_type) are rejected with an error."""
    entry = await async_setup_entry_with_types(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_record_type"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "Test"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"key": "timestamp", "type": "text", "required": False, "add_another": False},
    )
    assert result["step_id"] == "add_field"
    assert result["errors"] == {"key": "reserved_key"}


async def test_add_field_requires_options_for_select_types(hass: HomeAssistant) -> None:
    """single_select/multi_select fields require at least one option."""
    entry = await async_setup_entry_with_types(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_record_type"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "Mood"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "key": "mood",
            "type": "single_select",
            "required": False,
            "add_another": False,
        },
    )
    assert result["errors"] == {"options": "options_required"}


async def test_remove_record_type(hass: HomeAssistant) -> None:
    """Removing a record type drops it from the stored options."""
    entry = await async_setup_entry_with_types(hass, [BP_RECORD_TYPE])
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "remove_record_type"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"record_type": "bp"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_RECORD_TYPES] == []


async def test_set_retention_values(hass: HomeAssistant) -> None:
    """Retention/max_records/warn_at can be set for an existing record type."""
    entry = await async_setup_entry_with_types(hass, [BP_RECORD_TYPE])
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "set_retention"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"record_type": "bp"}
    )
    assert result["step_id"] == "set_retention_values"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"retention_days": 30, "max_records": 1000, "warn_at": 500}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    record_type = result["data"][CONF_RECORD_TYPES][0]
    assert record_type["retention_days"] == 30
    assert record_type["max_records"] == 1000
    assert record_type["warn_at"] == 500
