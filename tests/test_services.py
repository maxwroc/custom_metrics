"""Tests for the custom_metrics.add_record service."""

from __future__ import annotations

from pathlib import Path

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.setup import async_setup_component

from custom_components.custom_metrics.const import DOMAIN, SERVICE_ADD_RECORD

from .conftest import BP_RECORD_TYPE, async_setup_entry_with_types

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


async def test_service_registered_even_without_entry(hass: HomeAssistant) -> None:
    """The service is registered at component setup, independent of any entry."""
    assert await async_setup_component(hass, DOMAIN, {})
    assert hass.services.has_service(DOMAIN, SERVICE_ADD_RECORD)


async def test_add_record_happy_path(hass: HomeAssistant) -> None:
    """A valid add_record call stores the record and returns its public shape."""
    await async_setup_entry_with_types(hass, [BP_RECORD_TYPE])

    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_ADD_RECORD,
        {"record_type": "bp", "fields": {"systolic": 120}},
        blocking=True,
        return_response=True,
    )
    assert response["systolic"] == 120.0
    assert "id" in response
    assert "timestamp" in response


async def test_add_record_unknown_record_type(hass: HomeAssistant) -> None:
    """An unknown record_type raises ServiceValidationError."""
    await async_setup_entry_with_types(hass, [BP_RECORD_TYPE])

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_ADD_RECORD,
            {"record_type": "unknown", "fields": {}},
            blocking=True,
        )


async def test_add_record_missing_required_field(hass: HomeAssistant) -> None:
    """A missing required field raises ServiceValidationError."""
    await async_setup_entry_with_types(hass, [BP_RECORD_TYPE])

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_ADD_RECORD,
            {"record_type": "bp", "fields": {}},
            blocking=True,
        )


async def test_add_record_not_set_up(hass: HomeAssistant) -> None:
    """Calling the service with no loaded entry raises ServiceValidationError."""
    assert await async_setup_component(hass, DOMAIN, {})

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_ADD_RECORD,
            {"record_type": "bp", "fields": {}},
            blocking=True,
        )


async def test_add_record_stores_image_reference_not_raw_path(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """An IMAGE field's filesystem path is replaced with a stored reference."""
    await async_setup_entry_with_types(hass, [IMAGE_RECORD_TYPE])
    source_file = tmp_path / "cat.jpg"
    source_file.write_bytes(b"fake-image-bytes")

    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_ADD_RECORD,
        {"record_type": "pets", "fields": {"photo": str(source_file)}},
        blocking=True,
        return_response=True,
    )

    assert isinstance(response["photo"], dict)
    assert response["photo"]["f"].endswith(".jpg")
