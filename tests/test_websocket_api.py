"""Tests for the custom_metrics WebSocket API commands."""

# pyright: reportOptionalMemberAccess=false

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.custom_metrics.const import MAX_LIST_RECORDS_LIMIT

from .conftest import BP_RECORD_TYPE, async_setup_entry_with_types, make_source_image

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


async def test_add_record_rejects_invalid_timestamp(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """An explicit malformed timestamp is not silently replaced with now."""
    await async_setup_entry_with_types(hass, [BP_RECORD_TYPE])
    client = await hass_ws_client(hass)

    await client.send_json(
        {
            "id": 1,
            "type": "custom_metrics/add_record",
            "record_type": "bp",
            "fields": {"systolic": 120},
            "timestamp": "not-a-date",
        }
    )
    response = await client.receive_json()

    assert response["success"] is False
    assert response["error"]["code"] == "invalid_datetime"


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
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """An existing, allowed-extension image path validates as valid."""
    await async_setup_entry_with_types(hass, [BP_RECORD_TYPE])
    source_file = make_source_image(hass, name="cat.jpg")
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


async def test_list_records_limit_sorts_newest_first(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """An explicit limit caps the result and orders it newest-first."""
    entry = await async_setup_entry_with_types(hass, [BP_RECORD_TYPE])
    storage = entry.runtime_data.storage
    now = dt_util.utcnow()
    for i in range(5):
        await storage.async_add_record(
            "bp", {"systolic": i}, timestamp=now + timedelta(seconds=i)
        )

    client = await hass_ws_client(hass)
    await client.send_json(
        {
            "id": 1,
            "type": "custom_metrics/list_records",
            "record_type": "bp",
            "limit": 2,
        }
    )
    response = await client.receive_json()

    assert response["success"]
    records = response["result"]["records"]
    assert [r["systolic"] for r in records] == [4, 3]


async def test_list_records_rejects_invalid_datetime(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """Malformed time filters return an error instead of disabling filtering."""
    await async_setup_entry_with_types(hass, [BP_RECORD_TYPE])
    client = await hass_ws_client(hass)

    await client.send_json(
        {
            "id": 1,
            "type": "custom_metrics/list_records",
            "record_type": "bp",
            "start": "not-a-date",
        }
    )
    response = await client.receive_json()

    assert response["success"] is False
    assert response["error"]["code"] == "invalid_datetime"


async def test_list_records_rejects_reversed_time_range(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """A start after end is rejected instead of returning a misleading empty list."""
    await async_setup_entry_with_types(hass, [BP_RECORD_TYPE])
    client = await hass_ws_client(hass)

    await client.send_json(
        {
            "id": 1,
            "type": "custom_metrics/list_records",
            "record_type": "bp",
            "start": "2026-02-01T00:00:00+00:00",
            "end": "2026-01-01T00:00:00+00:00",
        }
    )
    response = await client.receive_json()

    assert response["success"] is False
    assert response["error"]["code"] == "invalid_time_range"


async def test_list_records_without_limit_is_still_capped(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """Even with no explicit limit, results are capped at MAX_LIST_RECORDS_LIMIT."""
    entry = await async_setup_entry_with_types(hass, [BP_RECORD_TYPE])
    storage = entry.runtime_data.storage
    for i in range(MAX_LIST_RECORDS_LIMIT + 5):
        await storage.async_add_record("bp", {"systolic": i})

    client = await hass_ws_client(hass)
    await client.send_json(
        {"id": 1, "type": "custom_metrics/list_records", "record_type": "bp"}
    )
    response = await client.receive_json()

    assert response["success"]
    assert len(response["result"]["records"]) == MAX_LIST_RECORDS_LIMIT


async def test_list_records_limit_above_cap_is_clamped(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """A requested limit above the server cap is clamped, not rejected."""
    entry = await async_setup_entry_with_types(hass, [BP_RECORD_TYPE])
    storage = entry.runtime_data.storage
    for i in range(MAX_LIST_RECORDS_LIMIT + 5):
        await storage.async_add_record("bp", {"systolic": i})

    client = await hass_ws_client(hass)
    await client.send_json(
        {
            "id": 1,
            "type": "custom_metrics/list_records",
            "record_type": "bp",
            "limit": MAX_LIST_RECORDS_LIMIT * 10,
        }
    )
    response = await client.receive_json()

    assert response["success"]
    assert len(response["result"]["records"]) == MAX_LIST_RECORDS_LIMIT


# Record type used by the `filter` tests below - a mix of field types so each
# operator-compatibility rejection can be exercised (label=text, photo=image).
_FILTER_RECORD_TYPE: dict[str, Any] = {
    "id": "widgets",
    "name": "Widgets",
    "fields": [
        {
            "key": "count",
            "label": "Count",
            "type": "number",
            "required": False,
            "unit": None,
            "default": None,
            "options": None,
        },
        {
            "key": "label",
            "label": "Label",
            "type": "text",
            "required": False,
            "unit": None,
            "default": None,
            "options": None,
        },
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


async def test_list_records_filter_happy_path(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """A `filter` list only returns records matching every item (AND-combined)."""
    entry = await async_setup_entry_with_types(hass, [_FILTER_RECORD_TYPE])
    storage = entry.runtime_data.storage
    for count in (50, 100, 150):
        await storage.async_add_record("widgets", {"count": count, "label": "a"})

    client = await hass_ws_client(hass)
    await client.send_json(
        {
            "id": 1,
            "type": "custom_metrics/list_records",
            "record_type": "widgets",
            "filter": [{"count": "> 75"}],
        }
    )
    response = await client.receive_json()

    assert response["success"]
    assert sorted(r["count"] for r in response["result"]["records"]) == [100, 150]


async def test_list_records_filter_unknown_field_error(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """Filtering on a field that doesn't exist on the record type is an error."""
    await async_setup_entry_with_types(hass, [_FILTER_RECORD_TYPE])
    client = await hass_ws_client(hass)

    await client.send_json(
        {
            "id": 1,
            "type": "custom_metrics/list_records",
            "record_type": "widgets",
            "filter": [{"nope": 1}],
        }
    )
    response = await client.receive_json()

    assert response["success"] is False
    assert response["error"]["code"] == "unknown_filter_field"


async def test_list_records_filter_image_field_error(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """Filtering on an IMAGE-type field is rejected - it's an internal object."""
    await async_setup_entry_with_types(hass, [_FILTER_RECORD_TYPE])
    client = await hass_ws_client(hass)

    await client.send_json(
        {
            "id": 1,
            "type": "custom_metrics/list_records",
            "record_type": "widgets",
            "filter": [{"photo": "x"}],
        }
    )
    response = await client.receive_json()

    assert response["success"] is False
    assert response["error"]["code"] == "unsupported_filter_field"


async def test_list_records_filter_unsupported_operator_error(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """A comparison operator not valid for the field's type is a clear error."""
    await async_setup_entry_with_types(hass, [_FILTER_RECORD_TYPE])
    client = await hass_ws_client(hass)

    await client.send_json(
        {
            "id": 1,
            "type": "custom_metrics/list_records",
            "record_type": "widgets",
            "filter": [{"label": "> a"}],
        }
    )
    response = await client.receive_json()

    assert response["success"] is False
    assert response["error"]["code"] == "unsupported_filter_operator"


async def test_list_records_filter_invalid_value_error(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """A filter value that doesn't coerce to the field's type is a clear error."""
    await async_setup_entry_with_types(hass, [_FILTER_RECORD_TYPE])
    client = await hass_ws_client(hass)

    await client.send_json(
        {
            "id": 1,
            "type": "custom_metrics/list_records",
            "record_type": "widgets",
            "filter": [{"count": "> notanumber"}],
        }
    )
    response = await client.receive_json()

    assert response["success"] is False
    assert response["error"]["code"] == "invalid_filter_value"


async def test_list_records_filter_invalid_item_error(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """A `filter` item that isn't a single-key map is rejected as a whole."""
    await async_setup_entry_with_types(hass, [_FILTER_RECORD_TYPE])
    client = await hass_ws_client(hass)

    await client.send_json(
        {
            "id": 1,
            "type": "custom_metrics/list_records",
            "record_type": "widgets",
            "filter": [{"count": 1, "label": "a"}],
        }
    )
    response = await client.receive_json()

    assert response["success"] is False
    assert response["error"]["code"] == "invalid_filter_item"


# -- aggregate_records (plan_sql.md Phase 4) ---------------------------------


async def test_aggregate_records_sum_by_day(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """sum/day buckets records by UTC calendar day, ascending, sparse."""
    entry = await async_setup_entry_with_types(hass, [_FILTER_RECORD_TYPE])
    storage = entry.runtime_data.storage
    day1 = dt_util.parse_datetime("2026-01-01T10:00:00+00:00")
    day3 = dt_util.parse_datetime("2026-01-03T10:00:00+00:00")
    await storage.async_add_record("widgets", {"count": 10}, timestamp=day1)
    await storage.async_add_record("widgets", {"count": 20}, timestamp=day1)
    await storage.async_add_record("widgets", {"count": 5}, timestamp=day3)

    client = await hass_ws_client(hass)
    await client.send_json(
        {
            "id": 1,
            "type": "custom_metrics/aggregate_records",
            "record_type": "widgets",
            "op": "sum",
            "bucket": "day",
            "field": "count",
        }
    )
    response = await client.receive_json()

    assert response["success"]
    buckets = response["result"]["buckets"]
    assert [b["value"] for b in buckets] == [30.0, 5.0]
    assert [b["count"] for b in buckets] == [2, 1]
    assert buckets[0]["start"] == "2026-01-01T00:00:00+00:00"
    assert buckets[1]["start"] == "2026-01-03T00:00:00+00:00"


async def test_aggregate_records_count_forbids_field(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """`op: count` must not accept a `field` parameter."""
    await async_setup_entry_with_types(hass, [_FILTER_RECORD_TYPE])
    client = await hass_ws_client(hass)

    await client.send_json(
        {
            "id": 1,
            "type": "custom_metrics/aggregate_records",
            "record_type": "widgets",
            "op": "count",
            "bucket": "day",
            "field": "count",
        }
    )
    response = await client.receive_json()

    assert response["success"] is False
    assert response["error"]["code"] == "field_forbidden"


async def test_aggregate_records_numeric_op_requires_field(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """`op: sum` (etc.) requires a `field` parameter."""
    await async_setup_entry_with_types(hass, [_FILTER_RECORD_TYPE])
    client = await hass_ws_client(hass)

    await client.send_json(
        {
            "id": 1,
            "type": "custom_metrics/aggregate_records",
            "record_type": "widgets",
            "op": "sum",
            "bucket": "day",
        }
    )
    response = await client.receive_json()

    assert response["success"] is False
    assert response["error"]["code"] == "field_required"


async def test_aggregate_records_count_op(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """`op: count` counts records per bucket, regardless of field values."""
    entry = await async_setup_entry_with_types(hass, [_FILTER_RECORD_TYPE])
    storage = entry.runtime_data.storage
    day1 = dt_util.parse_datetime("2026-01-01T10:00:00+00:00")
    for _ in range(3):
        await storage.async_add_record("widgets", {}, timestamp=day1)

    client = await hass_ws_client(hass)
    await client.send_json(
        {
            "id": 1,
            "type": "custom_metrics/aggregate_records",
            "record_type": "widgets",
            "op": "count",
            "bucket": "day",
        }
    )
    response = await client.receive_json()

    assert response["success"]
    buckets = response["result"]["buckets"]
    assert buckets == [{"start": "2026-01-01T00:00:00+00:00", "value": 3, "count": 3}]


async def test_aggregate_records_with_filter(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """`filter` narrows aggregation to matching rows, same as list_records."""
    entry = await async_setup_entry_with_types(hass, [_FILTER_RECORD_TYPE])
    storage = entry.runtime_data.storage
    day1 = dt_util.parse_datetime("2026-01-01T10:00:00+00:00")
    await storage.async_add_record(
        "widgets", {"count": 10, "label": "a"}, timestamp=day1
    )
    await storage.async_add_record(
        "widgets", {"count": 999, "label": "b"}, timestamp=day1
    )

    client = await hass_ws_client(hass)
    await client.send_json(
        {
            "id": 1,
            "type": "custom_metrics/aggregate_records",
            "record_type": "widgets",
            "op": "sum",
            "bucket": "day",
            "field": "count",
            "filter": [{"label": "a"}],
        }
    )
    response = await client.receive_json()

    assert response["success"]
    assert response["result"]["buckets"] == [
        {"start": "2026-01-01T00:00:00+00:00", "value": 10.0, "count": 1}
    ]


async def test_aggregate_records_apexcharts_format(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """`format: apexcharts` returns an ApexCharts-ready series."""
    entry = await async_setup_entry_with_types(hass, [_FILTER_RECORD_TYPE])
    storage = entry.runtime_data.storage
    day1 = dt_util.parse_datetime("2026-01-01T00:00:00+00:00")
    await storage.async_add_record("widgets", {"count": 42}, timestamp=day1)

    client = await hass_ws_client(hass)
    await client.send_json(
        {
            "id": 1,
            "type": "custom_metrics/aggregate_records",
            "record_type": "widgets",
            "op": "sum",
            "bucket": "day",
            "field": "count",
            "format": "apexcharts",
        }
    )
    response = await client.receive_json()

    assert response["success"]
    series = response["result"]["series"]
    assert series[0]["name"] == "Count"
    assert series[0]["data"] == [{"x": int(day1.timestamp() * 1000), "y": 42.0}]


async def test_aggregate_records_unknown_field_error(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """An unknown numeric field for sum/avg/min/max is a clear error."""
    await async_setup_entry_with_types(hass, [_FILTER_RECORD_TYPE])
    client = await hass_ws_client(hass)

    await client.send_json(
        {
            "id": 1,
            "type": "custom_metrics/aggregate_records",
            "record_type": "widgets",
            "op": "sum",
            "bucket": "day",
            "field": "nope",
        }
    )
    response = await client.receive_json()

    assert response["success"] is False
    assert response["error"]["code"] == "unknown_field"
