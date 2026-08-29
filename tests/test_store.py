"""Tests for custom_metrics.store.RecordStorage."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.custom_metrics.const import (
    ATTR_ENTRY_ID,
    ATTR_RECORD_TYPE,
    EVENT_RECORDS_UPDATED,
)
from custom_components.custom_metrics.csv_transfer import ImportRow
from custom_components.custom_metrics.store import RecordStorage


def _capture_updated_events(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Return a list that accumulates EVENT_RECORDS_UPDATED payloads as they fire."""
    captured: list[dict[str, Any]] = []
    hass.bus.async_listen(
        EVENT_RECORDS_UPDATED, lambda event: captured.append(event.data)
    )
    return captured


async def test_add_list_delete_record(hass: HomeAssistant) -> None:
    """Records can be added, listed, and deleted by id."""
    storage = RecordStorage(hass, "entry1")
    await storage.async_load(["bp"])

    record = await storage.async_add_record("bp", {"systolic": 120})
    assert storage.record_count("bp") == 1
    assert storage.async_list_records("bp") == [record]

    assert await storage.async_delete_record("bp", record["id"]) is True
    assert storage.record_count("bp") == 0
    assert await storage.async_delete_record("bp", "unknown-id") is False


async def test_list_records_limit_sorts_desc_and_truncates(hass: HomeAssistant) -> None:
    """Limit sorts newest-first (by timestamp) and truncates to at most `limit`."""
    storage = RecordStorage(hass, "entry1")
    await storage.async_load(["bp"])
    now = dt_util.utcnow()
    for i in range(5):
        await storage.async_add_record(
            "bp", {"i": i}, timestamp=now + timedelta(seconds=i)
        )

    limited = storage.async_list_records("bp", limit=2)
    assert [r["d"]["i"] for r in limited] == [4, 3]

    unlimited = storage.async_list_records("bp")
    assert len(unlimited) == 5


async def test_list_records_predicate_filters_and_combines_with_limit(
    hass: HomeAssistant,
) -> None:
    """A `predicate` keeps only matching records, folded into the same pass as limit."""
    storage = RecordStorage(hass, "entry1")
    await storage.async_load(["bp"])
    now = dt_util.utcnow()
    for i in range(5):
        await storage.async_add_record(
            "bp", {"i": i}, timestamp=now + timedelta(seconds=i)
        )

    def predicate(data: dict[str, Any]) -> bool:
        return data["i"] % 2 == 0

    filtered = storage.async_list_records("bp", predicate=predicate)
    assert [r["d"]["i"] for r in filtered] == [0, 2, 4]

    filtered_limited = storage.async_list_records("bp", predicate=predicate, limit=1)
    assert [r["d"]["i"] for r in filtered_limited] == [4]


async def test_records_persist_across_instances(hass: HomeAssistant) -> None:
    """Flushed records are loadable by a fresh RecordStorage for the same entry."""
    storage = RecordStorage(hass, "entry1")
    await storage.async_load(["bp"])
    await storage.async_add_record("bp", {"systolic": 120})
    await storage.async_flush()

    reloaded = RecordStorage(hass, "entry1")
    await reloaded.async_load(["bp"])
    assert reloaded.record_count("bp") == 1


async def test_retention_none_keeps_forever(hass: HomeAssistant) -> None:
    """retention_days=None means old records are never purged."""
    storage = RecordStorage(hass, "entry1")
    await storage.async_load(["bp"])
    await storage.async_add_record(
        "bp", {"systolic": 120}, timestamp=dt_util.utcnow() - timedelta(days=1000)
    )

    removed = await storage.async_purge_expired({"bp": None})
    assert removed == {"bp": 0}
    assert storage.record_count("bp") == 1


async def test_purge_expired_removes_old_records(hass: HomeAssistant) -> None:
    """Records older than retention_days are removed; newer ones are kept."""
    storage = RecordStorage(hass, "entry1")
    await storage.async_load(["bp"])
    old_ts = dt_util.utcnow() - timedelta(days=10)
    new_ts = dt_util.utcnow()
    await storage.async_add_record("bp", {"systolic": 1}, timestamp=old_ts)
    await storage.async_add_record("bp", {"systolic": 2}, timestamp=new_ts)

    removed = await storage.async_purge_expired({"bp": 5})
    assert removed == {"bp": 1}
    remaining = storage.async_list_records("bp")
    assert len(remaining) == 1
    assert remaining[0]["d"]["systolic"] == 2


async def test_max_records_none_is_unlimited(hass: HomeAssistant) -> None:
    """max_records=None means no count-based cap is enforced."""
    storage = RecordStorage(hass, "entry1")
    await storage.async_load(["bp"])
    for i in range(3):
        await storage.async_add_record("bp", {"i": i})

    removed = await storage.async_enforce_max_records({"bp": None})
    assert removed == {"bp": 0}
    assert storage.record_count("bp") == 3


async def test_max_records_enforced_drops_oldest(hass: HomeAssistant) -> None:
    """When over the cap, the oldest records are dropped first."""
    storage = RecordStorage(hass, "entry1")
    await storage.async_load(["bp"])
    now = dt_util.utcnow()
    for i in range(3):
        await storage.async_add_record(
            "bp", {"i": i}, timestamp=now + timedelta(seconds=i)
        )

    removed = await storage.async_enforce_max_records({"bp": 2})
    assert removed == {"bp": 1}
    remaining = storage.async_list_records("bp")
    assert [r["d"]["i"] for r in remaining] == [1, 2]


async def test_async_remove_deletes_all_store_files(hass: HomeAssistant) -> None:
    """async_remove() deletes the on-disk Store file(s) for every loaded type."""
    storage = RecordStorage(hass, "entry1")
    await storage.async_load(["bp"])
    await storage.async_add_record("bp", {"i": 1})
    await storage.async_flush()

    await storage.async_remove()

    reloaded = RecordStorage(hass, "entry1")
    await reloaded.async_load(["bp"])
    assert reloaded.record_count("bp") == 0


async def test_add_record_fires_updated_event(hass: HomeAssistant) -> None:
    """Adding a record fires EVENT_RECORDS_UPDATED with the entry/type ids."""
    storage = RecordStorage(hass, "entry1")
    await storage.async_load(["bp"])
    captured = _capture_updated_events(hass)

    await storage.async_add_record("bp", {"systolic": 120})
    await hass.async_block_till_done()

    assert captured == [{ATTR_ENTRY_ID: "entry1", ATTR_RECORD_TYPE: "bp"}]


async def test_delete_record_fires_updated_event_only_when_removed(
    hass: HomeAssistant,
) -> None:
    """Deleting fires the event only when a record was actually removed."""
    storage = RecordStorage(hass, "entry1")
    await storage.async_load(["bp"])
    record = await storage.async_add_record("bp", {"systolic": 120})
    captured = _capture_updated_events(hass)

    assert await storage.async_delete_record("bp", "unknown-id") is False
    await hass.async_block_till_done()
    assert captured == []

    assert await storage.async_delete_record("bp", record["id"]) is True
    await hass.async_block_till_done()
    assert captured == [{ATTR_ENTRY_ID: "entry1", ATTR_RECORD_TYPE: "bp"}]


async def test_purge_expired_fires_updated_event_only_when_removed(
    hass: HomeAssistant,
) -> None:
    """Purging fires the event only for types that actually lost records."""
    storage = RecordStorage(hass, "entry1")
    await storage.async_load(["bp", "weight"])
    await storage.async_add_record(
        "bp", {"systolic": 1}, timestamp=dt_util.utcnow() - timedelta(days=10)
    )
    await storage.async_add_record("weight", {"kg": 70})
    captured = _capture_updated_events(hass)

    removed = await storage.async_purge_expired({"bp": 5, "weight": None})
    await hass.async_block_till_done()

    assert removed == {"bp": 1, "weight": 0}
    assert captured == [{ATTR_ENTRY_ID: "entry1", ATTR_RECORD_TYPE: "bp"}]


async def test_max_records_enforced_fires_updated_event_only_when_removed(
    hass: HomeAssistant,
) -> None:
    """max_records eviction fires the event only for types that lost records."""
    storage = RecordStorage(hass, "entry1")
    await storage.async_load(["bp", "weight"])
    now = dt_util.utcnow()
    for i in range(3):
        await storage.async_add_record(
            "bp", {"i": i}, timestamp=now + timedelta(seconds=i)
        )
    await storage.async_add_record("weight", {"kg": 70})
    captured = _capture_updated_events(hass)

    removed = await storage.async_enforce_max_records({"bp": 2, "weight": None})
    await hass.async_block_till_done()

    assert removed == {"bp": 1, "weight": 0}
    assert captured == [{ATTR_ENTRY_ID: "entry1", ATTR_RECORD_TYPE: "bp"}]


async def test_import_records_appends_new_rows(hass: HomeAssistant) -> None:
    """Rows with a fresh id are appended, generating uuid/timestamp when unset."""
    storage = RecordStorage(hass, "entry1")
    await storage.async_load(["bp"])

    summary = await storage.async_import_records(
        "bp",
        [
            ImportRow(id="row-1", timestamp=dt_util.utcnow(), fields={"i": 1}),
            ImportRow(id=None, timestamp=None, fields={"i": 2}),
        ],
    )

    assert summary.imported == 2
    assert summary.skipped_duplicate == 0
    assert storage.record_count("bp") == 2
    ids = {r["id"] for r in storage.async_list_records("bp")}
    assert "row-1" in ids


async def test_import_records_skips_duplicate_ids(hass: HomeAssistant) -> None:
    """A row whose id already exists in the store is skipped, not overwritten."""
    storage = RecordStorage(hass, "entry1")
    await storage.async_load(["bp"])
    existing = await storage.async_add_record("bp", {"i": 0})

    summary = await storage.async_import_records(
        "bp",
        [
            ImportRow(id=existing["id"], timestamp=dt_util.utcnow(), fields={"i": 99}),
            ImportRow(id="new-row", timestamp=dt_util.utcnow(), fields={"i": 1}),
        ],
    )

    assert summary.imported == 1
    assert summary.skipped_duplicate == 1
    assert storage.record_count("bp") == 2
    # The original record's data is untouched (not overwritten).
    assert storage.async_list_records("bp")[0]["d"]["i"] == 0


async def test_import_records_fires_updated_event_once_not_per_row(
    hass: HomeAssistant,
) -> None:
    """Importing multiple rows fires EVENT_RECORDS_UPDATED once for the call."""
    storage = RecordStorage(hass, "entry1")
    await storage.async_load(["bp"])
    captured = _capture_updated_events(hass)

    await storage.async_import_records(
        "bp",
        [
            ImportRow(id=None, timestamp=None, fields={"i": 1}),
            ImportRow(id=None, timestamp=None, fields={"i": 2}),
            ImportRow(id=None, timestamp=None, fields={"i": 3}),
        ],
    )
    await hass.async_block_till_done()

    assert captured == [{ATTR_ENTRY_ID: "entry1", ATTR_RECORD_TYPE: "bp"}]


async def test_import_records_no_rows_does_not_fire_event(
    hass: HomeAssistant,
) -> None:
    """Importing zero new rows (e.g. all duplicates) does not fire the event."""
    storage = RecordStorage(hass, "entry1")
    await storage.async_load(["bp"])
    existing = await storage.async_add_record("bp", {"i": 0})
    captured = _capture_updated_events(hass)

    summary = await storage.async_import_records(
        "bp", [ImportRow(id=existing["id"], timestamp=None, fields={"i": 99})]
    )
    await hass.async_block_till_done()

    assert summary.imported == 0
    assert captured == []


async def test_import_records_skips_content_duplicate_without_id(
    hass: HomeAssistant,
) -> None:
    """A no-id row with the SAME timestamp+fields as an existing record is skipped."""
    storage = RecordStorage(hass, "entry1")
    await storage.async_load(["bp"])
    ts = dt_util.utcnow()
    await storage.async_add_record("bp", {"systolic": 120}, timestamp=ts)

    summary = await storage.async_import_records(
        "bp", [ImportRow(id=None, timestamp=ts, fields={"systolic": 120})]
    )

    assert summary.imported == 0
    assert summary.skipped_duplicate == 1
    assert storage.record_count("bp") == 1


async def test_import_records_same_timestamp_different_fields_is_imported(
    hass: HomeAssistant,
) -> None:
    """Same timestamp but different field data is NOT treated as a duplicate."""
    storage = RecordStorage(hass, "entry1")
    await storage.async_load(["bp"])
    ts = dt_util.utcnow()
    await storage.async_add_record("bp", {"systolic": 120}, timestamp=ts)

    summary = await storage.async_import_records(
        "bp", [ImportRow(id=None, timestamp=ts, fields={"systolic": 130})]
    )

    assert summary.imported == 1
    assert summary.skipped_duplicate == 0
    assert storage.record_count("bp") == 2


async def test_import_records_same_fields_different_timestamp_is_imported(
    hass: HomeAssistant,
) -> None:
    """Identical field data at a different timestamp is NOT treated as a duplicate."""
    storage = RecordStorage(hass, "entry1")
    await storage.async_load(["bp"])
    ts = dt_util.utcnow()
    await storage.async_add_record("bp", {"systolic": 120}, timestamp=ts)

    summary = await storage.async_import_records(
        "bp",
        [
            ImportRow(
                id=None, timestamp=ts + timedelta(seconds=1), fields={"systolic": 120}
            )
        ],
    )

    assert summary.imported == 1
    assert summary.skipped_duplicate == 0
    assert storage.record_count("bp") == 2


async def test_import_records_dedupes_content_duplicates_within_same_batch(
    hass: HomeAssistant,
) -> None:
    """Two no-id rows in the same import with identical timestamp+fields dedupe."""
    storage = RecordStorage(hass, "entry1")
    await storage.async_load(["bp"])
    ts = dt_util.utcnow()

    summary = await storage.async_import_records(
        "bp",
        [
            ImportRow(id=None, timestamp=ts, fields={"systolic": 120}),
            ImportRow(id=None, timestamp=ts, fields={"systolic": 120}),
        ],
    )

    assert summary.imported == 1
    assert summary.skipped_duplicate == 1
    assert storage.record_count("bp") == 1


async def test_import_records_without_timestamp_never_content_deduped(
    hass: HomeAssistant,
) -> None:
    """Rows with neither id nor timestamp are always appended (nothing to compare)."""
    storage = RecordStorage(hass, "entry1")
    await storage.async_load(["bp"])

    summary = await storage.async_import_records(
        "bp",
        [
            ImportRow(id=None, timestamp=None, fields={"systolic": 120}),
            ImportRow(id=None, timestamp=None, fields={"systolic": 120}),
        ],
    )

    assert summary.imported == 2
    assert summary.skipped_duplicate == 0
    assert storage.record_count("bp") == 2
