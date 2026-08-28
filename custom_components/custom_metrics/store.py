"""Storage layer for custom_metrics: one Store file per record type."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    ENVELOPE_DATA,
    ENVELOPE_ID,
    ENVELOPE_TIMESTAMP,
    SAVE_DELAY,
    STORAGE_KEY_TEMPLATE,
    STORAGE_VERSION,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from homeassistant.core import HomeAssistant


class RecordStorage:
    """Manages persistence of records, one Store file per record type."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the storage manager."""
        self.hass = hass
        self.entry_id = entry_id
        self._stores: dict[str, Store] = {}
        self._records: dict[str, list[dict[str, Any]]] = {}

    def _get_store(self, record_type_id: str) -> Store:
        """Return (creating if needed) the Store for a record type."""
        if record_type_id not in self._stores:
            key = STORAGE_KEY_TEMPLATE.format(
                entry_id=self.entry_id, record_type_id=record_type_id
            )
            self._stores[record_type_id] = Store(self.hass, STORAGE_VERSION, key)
        return self._stores[record_type_id]

    async def async_load(self, record_type_ids: Iterable[str]) -> None:
        """Load records for the given record type ids from disk."""
        for record_type_id in record_type_ids:
            store = self._get_store(record_type_id)
            data = await store.async_load()
            self._records[record_type_id] = (data or {}).get("records", [])

    def record_count(self, record_type_id: str) -> int:
        """Return the current in-memory record count for a record type."""
        return len(self._records.get(record_type_id, []))

    async def async_add_record(
        self,
        record_type_id: str,
        data: dict[str, Any],
        timestamp: datetime | None = None,
    ) -> dict[str, Any]:
        """Add a new record and schedule a debounced save."""
        record = {
            ENVELOPE_ID: str(uuid4()),
            ENVELOPE_TIMESTAMP: (timestamp or dt_util.utcnow()).isoformat(),
            ENVELOPE_DATA: data,
        }
        self._records.setdefault(record_type_id, []).append(record)
        self._async_schedule_save(record_type_id)
        return record

    def async_list_records(
        self,
        record_type_id: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Return records for a record type, optionally filtered by time range.

        If limit is given, results are sorted by timestamp descending (most
        recent first) and truncated to at most `limit` records - callers
        (the WebSocket API) are responsible for capping `limit` to a sane
        server-side maximum before calling this.
        """
        records = self._records.get(record_type_id, [])
        if start is None and end is None:
            result = list(records)
        else:
            result = []
            for record in records:
                ts = dt_util.parse_datetime(record[ENVELOPE_TIMESTAMP])
                if ts is None:
                    continue
                if start is not None and ts < start:
                    continue
                if end is not None and ts > end:
                    continue
                result.append(record)

        if limit is not None:
            result = sorted(result, key=lambda r: r[ENVELOPE_TIMESTAMP], reverse=True)[
                :limit
            ]
        return result

    async def async_delete_record(self, record_type_id: str, record_id: str) -> bool:
        """Delete a single record by id. Returns True if a record was removed."""
        records = self._records.get(record_type_id, [])
        for index, record in enumerate(records):
            if record[ENVELOPE_ID] == record_id:
                records.pop(index)
                self._async_schedule_save(record_type_id)
                return True
        return False

    async def async_purge_expired(
        self, retention_by_type: dict[str, int | None]
    ) -> dict[str, int]:
        """
        Remove records older than retention_days per record type.

        A retention_days of None means "keep forever" (no purge for that type).
        Returns a dict of record_type_id -> number of records removed.
        """
        now = dt_util.utcnow()
        removed_counts: dict[str, int] = {}
        for record_type_id, retention_days in retention_by_type.items():
            if retention_days is None:
                removed_counts[record_type_id] = 0
                continue
            cutoff = now - timedelta(days=retention_days)
            records = self._records.get(record_type_id, [])
            kept = []
            removed = 0
            for record in records:
                ts = dt_util.parse_datetime(record[ENVELOPE_TIMESTAMP])
                if ts is not None and ts < cutoff:
                    removed += 1
                else:
                    kept.append(record)
            if removed:
                self._records[record_type_id] = kept
                self._async_schedule_save(record_type_id)
            removed_counts[record_type_id] = removed
        return removed_counts

    async def async_enforce_max_records(
        self, max_records_by_type: dict[str, int | None]
    ) -> dict[str, int]:
        """
        Drop oldest records beyond an optional per-type count cap.

        A max_records of None means "unlimited" (opt-in feature, off by default).
        Returns a dict of record_type_id -> number of records removed.
        """
        removed_counts: dict[str, int] = {}
        for record_type_id, max_records in max_records_by_type.items():
            if max_records is None:
                removed_counts[record_type_id] = 0
                continue
            records = self._records.get(record_type_id, [])
            if len(records) <= max_records:
                removed_counts[record_type_id] = 0
                continue
            records.sort(key=lambda r: r[ENVELOPE_TIMESTAMP])
            removed = len(records) - max_records
            self._records[record_type_id] = records[-max_records:]
            self._async_schedule_save(record_type_id)
            removed_counts[record_type_id] = removed
        return removed_counts

    def _async_schedule_save(self, record_type_id: str) -> None:
        """Schedule a debounced/coalesced save for a record type's Store."""
        store = self._get_store(record_type_id)

        def _data_to_save() -> dict[str, Any]:
            return {"records": self._records.get(record_type_id, [])}

        store.async_delay_save(_data_to_save, SAVE_DELAY)

    async def async_flush(self) -> None:
        """Force an immediate save of all loaded record types (e.g. on unload)."""
        for record_type_id, store in self._stores.items():
            await store.async_save({"records": self._records.get(record_type_id, [])})

    async def async_remove_record_type(self, record_type_id: str) -> None:
        """Delete the Store file for a single record type (e.g. type removed)."""
        store = self._get_store(record_type_id)
        await store.async_remove()
        self._stores.pop(record_type_id, None)
        self._records.pop(record_type_id, None)

    async def async_remove(self) -> None:
        """Delete ALL of this entry's per-record-type Store files (uninstall)."""
        for record_type_id in list(self._stores):
            await self.async_remove_record_type(record_type_id)
