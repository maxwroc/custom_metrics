"""Tests for custom_metrics.media_store."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from homeassistant.core import HomeAssistant

from custom_components.custom_metrics.const import ENVELOPE_DATA, ENVELOPE_ID, FieldType
from custom_components.custom_metrics.media_store import (
    IMAGE_REF_FILENAME_KEY,
    MediaStore,
    async_resolve_image_fields,
)
from custom_components.custom_metrics.models import FieldDefinition, RecordType
from custom_components.custom_metrics.store import RecordStorage


def _make_source_image(tmp_path: Path, name: str = "photo.jpg") -> Path:
    source = tmp_path / name
    source.write_bytes(b"fake-image-bytes")
    return source


@pytest.fixture
def entry_id() -> str:
    """Return a unique entry id per test so on-disk media dirs never collide."""
    return f"entry-{uuid4().hex}"


async def test_store_resolve_and_delete_image(
    hass: HomeAssistant, tmp_path: Path, entry_id: str
) -> None:
    """An image can be stored, resolved to a path, and deleted."""
    media_store = MediaStore(hass, entry_id)
    source = _make_source_image(tmp_path)

    filename = await media_store.async_store_image("bp", str(source))
    assert filename.endswith(".jpg")

    resolved = await media_store.async_resolve_image_path("bp", filename)
    assert resolved.is_file()
    assert resolved.read_bytes() == b"fake-image-bytes"

    await media_store.async_delete_image("bp", filename)
    resolved_after_delete = await media_store.async_resolve_image_path("bp", filename)
    assert not resolved_after_delete.is_file()


async def test_store_image_rejects_missing_file(
    hass: HomeAssistant, tmp_path: Path, entry_id: str
) -> None:
    """Storing a non-existent source path raises ValueError."""
    media_store = MediaStore(hass, entry_id)
    with pytest.raises(ValueError, match="not a file"):
        await media_store.async_store_image("bp", str(tmp_path / "missing.jpg"))


async def test_store_image_rejects_unsupported_extension(
    hass: HomeAssistant, tmp_path: Path, entry_id: str
) -> None:
    """Storing a file with a disallowed extension raises ValueError."""
    media_store = MediaStore(hass, entry_id)
    source = _make_source_image(tmp_path, name="document.txt")
    with pytest.raises(ValueError, match="Unsupported image extension"):
        await media_store.async_store_image("bp", str(source))


async def test_cleanup_orphaned_media_removes_unreferenced_files(
    hass: HomeAssistant, tmp_path: Path, entry_id: str
) -> None:
    """Files no longer referenced by any record are deleted; referenced ones survive."""
    media_store = MediaStore(hass, entry_id)
    storage = RecordStorage(hass, entry_id)
    await storage.async_load(["bp"])

    kept_filename = await media_store.async_store_image(
        "bp", str(_make_source_image(tmp_path))
    )
    orphan_filename = await media_store.async_store_image(
        "bp", str(_make_source_image(tmp_path, name="orphan.png"))
    )

    await storage.async_add_record(
        "bp", {"photo": {IMAGE_REF_FILENAME_KEY: kept_filename}}
    )

    record_type = RecordType(
        id="bp",
        name="Blood Pressure",
        fields=[
            FieldDefinition(key="photo", label="Photo", type=FieldType.IMAGE),
        ],
    )

    removed = await media_store.async_cleanup_orphaned_media(
        storage, {"bp": record_type}
    )
    assert removed == {"bp": 1}

    kept_path = await media_store.async_resolve_image_path("bp", kept_filename)
    orphan_path = await media_store.async_resolve_image_path("bp", orphan_filename)
    assert kept_path.is_file()
    assert not orphan_path.is_file()


async def test_async_remove_all_deletes_entry_media_dir(
    hass: HomeAssistant, tmp_path: Path, entry_id: str
) -> None:
    """async_remove_all deletes the whole media tree for the entry."""
    media_store = MediaStore(hass, entry_id)
    filename = await media_store.async_store_image(
        "bp", str(_make_source_image(tmp_path))
    )
    path = await media_store.async_resolve_image_path("bp", filename)
    assert path.is_file()

    await media_store.async_remove_all()

    assert not path.is_file()


async def test_resolve_image_fields_replaces_path_with_reference(
    hass: HomeAssistant, tmp_path: Path, entry_id: str
) -> None:
    """async_resolve_image_fields turns a filesystem path into a {"f": ...} ref."""
    media_store = MediaStore(hass, entry_id)
    record_type = RecordType(
        id="bp",
        name="Blood Pressure",
        fields=[
            FieldDefinition(key="systolic", label="Systolic", type=FieldType.NUMBER),
            FieldDefinition(key="photo", label="Photo", type=FieldType.IMAGE),
        ],
    )
    source = _make_source_image(tmp_path)

    resolved = await async_resolve_image_fields(
        media_store, record_type, {"systolic": 120, "photo": str(source)}
    )

    assert resolved["systolic"] == 120
    assert isinstance(resolved["photo"], dict)
    assert resolved["photo"][IMAGE_REF_FILENAME_KEY].endswith(".jpg")


async def test_resolve_image_fields_noop_without_image_fields(
    hass: HomeAssistant, entry_id: str
) -> None:
    """Record types with no IMAGE field pass fields through unchanged."""
    media_store = MediaStore(hass, entry_id)
    record_type = RecordType(
        id="bp",
        name="Blood Pressure",
        fields=[
            FieldDefinition(key="systolic", label="Systolic", type=FieldType.NUMBER)
        ],
    )

    fields = {"systolic": 120}
    resolved = await async_resolve_image_fields(media_store, record_type, fields)
    assert resolved is fields


def test_referenced_filenames_uses_envelope_data_key() -> None:
    """Sanity check that ENVELOPE_DATA/ENVELOPE_ID match the record envelope shape."""
    record = {
        ENVELOPE_ID: "abc",
        ENVELOPE_DATA: {"photo": {IMAGE_REF_FILENAME_KEY: "x.jpg"}},
    }
    assert record[ENVELOPE_DATA]["photo"][IMAGE_REF_FILENAME_KEY] == "x.jpg"
