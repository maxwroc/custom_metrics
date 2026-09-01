"""Storage layer for custom_metrics: one Store file per record type."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_ENTRY_ID,
    ATTR_RECORD_TYPE,
    ENVELOPE_DATA,
    ENVELOPE_ID,
    ENVELOPE_TIMESTAMP,
    EVENT_RECORDS_UPDATED,
    LOGGER,
    SAVE_DELAY,
    STORAGE_KEY_TEMPLATE,
    STORAGE_VERSION,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from homeassistant.core import HomeAssistant

    from .csv_transfer import ImportRow


@dataclass
class ImportSummary:
    """Result of a bulk CSV import (store.py's async_import_records)."""

    imported: int
    skipped_duplicate: int


def _freeze(value: Any) -> Any:
    """
    Recursively convert a field-data dict/list into a hashable form.

    Used to build a set of (timestamp, data) "signatures" for content-based
    duplicate detection in async_import_records - a plain dict/list isn't
    hashable, so nested containers are converted into tuples (dict items
    sorted by key, so key order never affects equality/hashing).
    """
    if isinstance(value, dict):
        return tuple(sorted((k, _freeze(v)) for k, v in value.items()))
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


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

    def _fire_updated(self, record_type_id: str) -> None:
        """Notify listeners (e.g. an open Lovelace card) that data changed."""
        self.hass.bus.async_fire(
            EVENT_RECORDS_UPDATED,
            {ATTR_ENTRY_ID: self.entry_id, ATTR_RECORD_TYPE: record_type_id},
        )

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
        self._fire_updated(record_type_id)
        return record

    async def async_import_records(
        self, record_type_id: str, rows: list[ImportRow]
    ) -> ImportSummary:
        """
        Bulk-import parsed CSV rows (csv_transfer.py), skipping duplicates.

        A row whose `id` matches an already-stored record is skipped (counted
        as `skipped_duplicate`, NOT overwritten) - this makes re-importing an
        exported backup idempotent/safe.

        Independent of `id`, a row is ALSO skipped as a duplicate if its
        timestamp AND field data are IDENTICAL to another record - either
        already stored, or already accepted earlier in this same import call.
        This catches re-imports of "data only" exports (which have no `id`
        column, so would otherwise always be re-added as brand new records)
        as well as accidental exact-duplicate rows within one CSV file. A row
        with no timestamp at all (blank `id` and blank `timestamp`) skips
        this check - there's no meaningful timestamp to compare against, so
        it's always appended as a new record with the current time.

        Schedules a single debounced save and fires EVENT_RECORDS_UPDATED at
        most once for the whole call (not per-row), mirroring
        async_add_record's pattern.
        """
        existing = self._records.get(record_type_id, [])
        existing_ids = {record[ENVELOPE_ID] for record in existing}
        seen_signatures = {
            (record[ENVELOPE_TIMESTAMP], _freeze(record[ENVELOPE_DATA]))
            for record in existing
        }

        imported = 0
        skipped_duplicate = 0
        new_records: list[dict[str, Any]] = []
        for row in rows:
            if row.id is not None and row.id in existing_ids:
                skipped_duplicate += 1
                continue

            timestamp = row.timestamp or dt_util.utcnow()
            timestamp_iso = timestamp.isoformat()
            if row.timestamp is not None:
                signature = (timestamp_iso, _freeze(row.fields))
                if signature in seen_signatures:
                    skipped_duplicate += 1
                    continue
                seen_signatures.add(signature)

            record_id = row.id or str(uuid4())
            new_records.append(
                {
                    ENVELOPE_ID: record_id,
                    ENVELOPE_TIMESTAMP: timestamp_iso,
                    ENVELOPE_DATA: row.fields,
                }
            )
            existing_ids.add(record_id)
            imported += 1

        if new_records:
            self._records.setdefault(record_type_id, []).extend(new_records)
            self._async_schedule_save(record_type_id)
            self._fire_updated(record_type_id)
        return ImportSummary(imported=imported, skipped_duplicate=skipped_duplicate)

    async def async_rename_field_key(
        self, record_type_id: str, old_key: str, new_key: str
    ) -> None:
        """Rename `d.<old_key>` -> `d.<new_key>` in every stored record of this type."""
        for record in self._records.get(record_type_id, []):
            data = record[ENVELOPE_DATA]
            if old_key in data:
                data[new_key] = data.pop(old_key)
        self._async_schedule_save(record_type_id)

    async def async_rename_record_type(self, old_id: str, new_id: str) -> None:
        """
        Rename a record type's id: move its Store file to the new id.

        Must be called against the entry's live, already-loaded RecordStorage
        (not a fresh instance) so in-memory records aren't lost. The old
        Store file is deleted and a new one scheduled under new_id; callers
        are responsible for flushing (e.g. via the reload that naturally
        follows a config subentry update) so the new file actually exists on
        disk before anything reads it back under the new id.
        """
        records = self._records.get(old_id, [])
        old_store = self._stores.get(old_id)
        if old_store is not None:
            await old_store.async_remove()
        self._records.pop(old_id, None)
        self._stores.pop(old_id, None)
        self._records[new_id] = records
        self._async_schedule_save(new_id)

    def async_list_records(
        self,
        record_type_id: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Return records for a record type, optionally filtered by time range.

        `predicate`, if given, is a compiled field-value filter (see
        filter_query.py) tested against each record's ENVELOPE_DATA dict -
        folded into the SAME pass as the start/end check below, to avoid a
        second full iteration over the record list.

        If limit is given, results are sorted by timestamp descending (most
        recent first) and truncated to at most `limit` records - callers
        (the WebSocket API) are responsible for capping `limit` to a sane
        server-side maximum before calling this.
        """
        records = self._records.get(record_type_id, [])
        if start is None and end is None and predicate is None:
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
                if predicate is not None and not predicate(record[ENVELOPE_DATA]):
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
                self._fire_updated(record_type_id)
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
            if retention_days < 1:
                LOGGER.warning(
                    "Ignoring invalid retention_days %s for record type %s",
                    retention_days,
                    record_type_id,
                )
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
                self._fire_updated(record_type_id)
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
            if max_records < 1:
                LOGGER.warning(
                    "Ignoring invalid max_records %s for record type %s",
                    max_records,
                    record_type_id,
                )
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
            self._fire_updated(record_type_id)
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
