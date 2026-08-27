"""Tests for the custom_metrics WebSocket API commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant

from .conftest import BP_RECORD_TYPE, async_setup_entry_with_types

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_homeassistant_custom_component.typing import WebSocketGenerator


async def test_list_record_types(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """list_record_types returns the configured record types."""
    await async_setup_entry_with_types(hass, [BP_RECORD_TYPE])
    client = await hass_ws_client(hass)

    await client.send_json({"id": 1, "type": "custom_metrics/list_record_types"})
    response = await client.receive_json()

    assert response["success"]
    assert response["result"]["record_types"][0]["id"] == "bp"


async def test_add_list_delete_record(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """add_record/list_records/delete_record work end-to-end."""
    await async_setup_entry_with_types(hass, [BP_RECORD_TYPE])
    client = await hass_ws_client(hass)

    await client.send_json(
        {
            "id": 1,
            "type": "custom_metrics/add_record",
            "record_type": "bp",
            "fields": {"systolic": 120},
        }
    )
    response = await client.receive_json()
    assert response["success"]
    record_id = response["result"]["record"]["id"]

    await client.send_json(
        {"id": 2, "type": "custom_metrics/list_records", "record_type": "bp"}
    )
    response = await client.receive_json()
    assert len(response["result"]["records"]) == 1

    await client.send_json(
        {
            "id": 3,
            "type": "custom_metrics/delete_record",
            "record_type": "bp",
            "record_id": record_id,
        }
    )
    response = await client.receive_json()
    assert response["result"]["deleted"] is True


async def test_unknown_record_type_error(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """Unknown record_type ids return an error, not a crash."""
    await async_setup_entry_with_types(hass, [BP_RECORD_TYPE])
    client = await hass_ws_client(hass)

    await client.send_json(
        {"id": 1, "type": "custom_metrics/list_records", "record_type": "nope"}
    )
    response = await client.receive_json()

    assert response["success"] is False
    assert response["error"]["code"] == "unknown_record_type"


async def test_delete_missing_record_returns_not_found(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """Deleting a non-existent record id returns a not_found error."""
    await async_setup_entry_with_types(hass, [BP_RECORD_TYPE])
    client = await hass_ws_client(hass)

    await client.send_json(
        {
            "id": 1,
            "type": "custom_metrics/delete_record",
            "record_type": "bp",
            "record_id": "missing",
        }
    )
    response = await client.receive_json()

    assert response["success"] is False
    assert response["error"]["code"] == "not_found"


async def test_validate_image_path_for_existing_file(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, tmp_path: Path
) -> None:
    """An existing, allowed-extension image path validates as valid."""
    await async_setup_entry_with_types(hass, [BP_RECORD_TYPE])
    source_file = tmp_path / "cat.jpg"
    source_file.write_bytes(b"fake-image-bytes")
    client = await hass_ws_client(hass)

    await client.send_json(
        {
            "id": 1,
            "type": "custom_metrics/validate_image_path",
            "path": str(source_file),
        }
    )
    response = await client.receive_json()

    assert response["success"]
    assert response["result"] == {"valid": True, "error": None}


async def test_validate_image_path_for_missing_file(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, tmp_path: Path
) -> None:
    """A non-existent path validates as invalid, with an explanatory error."""
    await async_setup_entry_with_types(hass, [BP_RECORD_TYPE])
    client = await hass_ws_client(hass)

    await client.send_json(
        {
            "id": 1,
            "type": "custom_metrics/validate_image_path",
            "path": str(tmp_path / "missing.jpg"),
        }
    )
    response = await client.receive_json()

    assert response["success"]
    assert response["result"]["valid"] is False
    assert response["result"]["error"]


async def test_add_record_invalid_image_path_returns_error(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, tmp_path: Path
) -> None:
    """add_record with a non-existent image path returns an invalid_image error."""
    image_record_type = {
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
    await async_setup_entry_with_types(hass, [image_record_type])
    client = await hass_ws_client(hass)

    await client.send_json(
        {
            "id": 1,
            "type": "custom_metrics/add_record",
            "record_type": "pets",
            "fields": {"photo": str(tmp_path / "missing.jpg")},
        }
    )
    response = await client.receive_json()

    assert response["success"] is False
    assert response["error"]["code"] == "invalid_image"
