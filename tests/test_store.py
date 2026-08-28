"""Tests for custom_metrics.store.RecordStorage."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.custom_metrics.store import RecordStorage


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
