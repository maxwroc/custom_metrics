"""Fixtures and shared helpers for custom_metrics tests."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.custom_metrics.const import CONF_RECORD_TYPES, DOMAIN

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> Generator[None]:
    """Enable loading this custom integration for every test in this suite."""
    yield


# A minimal "Blood Pressure" record type used across multiple test modules.
BP_RECORD_TYPE: dict[str, Any] = {
    "id": "bp",
    "name": "Blood Pressure",
    "fields": [
        {
            "key": "systolic",
            "label": "Systolic",
            "type": "number",
            "required": True,
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


async def async_setup_entry_with_types(
    hass: HomeAssistant, record_types: list[dict[str, Any]] | None = None
) -> MockConfigEntry:
    """Create and set up a config entry, optionally pre-loaded with record types."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={CONF_RECORD_TYPES: record_types or []},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
