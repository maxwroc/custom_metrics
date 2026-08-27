"""
Image file storage for IMAGE-type record fields.

Files are stored under
<config>/.storage/custom_metrics/<entry_id>/media/<record_type_id>/, decoupled
from the record's own id (a random filename is generated per stored image) so
an image can be stored before its owning record exists.

The service caller is treated as trusted: only basic sanity checks are made
(the source path exists and has an allowed image extension) - no deep
content/decompression-bomb validation, per the project's agreed scope.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from homeassistant.components.http import StaticPathConfig

from .const import DOMAIN, ENVELOPE_DATA, FieldType

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .models import RecordType
    from .store import RecordStorage

ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}

# Key used inside the stored image reference object, e.g. {"f": "<filename>"}.
IMAGE_REF_FILENAME_KEY = "f"

# URL prefix under which each entry's media directory is served. NOTE: this is
# served as a plain unauthenticated static path (same trust level as the
# card's JS bundle in frontend.py), NOT via HA's authenticated local media
# flow - reusing that flow would require the media to live under a
# `media_dirs`-configured folder and/or a signed-URL scheme, which was flagged
# in the project plan as needing a dedicated spike. This is a deliberate,
# documented simplification for v1.
MEDIA_URL_PREFIX = f"/{DOMAIN}_media"
_MEDIA_STATIC_PATH_REGISTERED_KEY = f"{DOMAIN}_media_static_path_registered"


def _remove_unreferenced_files(target_dir: Path, referenced: set[str]) -> int:
    """Delete files in target_dir whose name isn't in referenced. Runs in executor."""
    if not target_dir.is_dir():
        return 0
    removed = 0
    for path in target_dir.iterdir():
        if path.is_file() and path.name not in referenced:
            path.unlink()
            removed += 1
    return removed


async def async_register_media_static_path(hass: HomeAssistant, entry_id: str) -> None:
    """
    Serve this entry's media directory so media_source.py can build URLs.

    Guarded to register only once per entry_id, hass-wide.
    """
    key = f"{_MEDIA_STATIC_PATH_REGISTERED_KEY}_{entry_id}"
    if hass.data.get(key):
        return
    base_dir = Path(hass.config.path(".storage", DOMAIN, entry_id, "media"))

    def _ensure_dir() -> None:
        base_dir.mkdir(parents=True, exist_ok=True)

    await hass.async_add_executor_job(_ensure_dir)
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                f"{MEDIA_URL_PREFIX}/{entry_id}", str(base_dir), cache_headers=False
            )
        ]
    )
    hass.data[key] = True


class MediaStore:
    """Manages image files for IMAGE-type record fields, one dir per entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the media store for a config entry."""
        self.hass = hass
        self.entry_id = entry_id
        self._base_dir = Path(hass.config.path(".storage", DOMAIN, entry_id, "media"))

    def _dir_for_type(self, record_type_id: str) -> Path:
        return self._base_dir / record_type_id

    async def async_store_image(self, record_type_id: str, source_path: str) -> str:
        """Copy source_path into the managed media dir; return the stored filename."""

        def _copy() -> str:
            source = Path(source_path)
            if not source.is_file():
                msg = f"Image source path is not a file: {source}"
                raise ValueError(msg)
            ext = source.suffix.lower()
            if ext not in ALLOWED_IMAGE_EXTENSIONS:
                msg = f"Unsupported image extension '{ext}'"
                raise ValueError(msg)
            target_dir = self._dir_for_type(record_type_id)
            target_dir.mkdir(parents=True, exist_ok=True)
            filename = f"{uuid4()}{ext}"
            shutil.copyfile(source, target_dir / filename)
            return filename

        return await self.hass.async_add_executor_job(_copy)

    async def async_resolve_image_path(
        self, record_type_id: str, filename: str
    ) -> Path:
        """Return the absolute path to a stored image file."""
        return self._dir_for_type(record_type_id) / filename

    async def async_delete_image(self, record_type_id: str, filename: str) -> None:
        """Delete a single stored image file, ignoring if already missing."""

        def _delete() -> None:
            (self._dir_for_type(record_type_id) / filename).unlink(missing_ok=True)

        await self.hass.async_add_executor_job(_delete)

    async def async_cleanup_orphaned_media(
        self,
        record_storage: RecordStorage,
        record_types: dict[str, RecordType],
    ) -> dict[str, int]:
        """Delete stored image files no longer referenced by any record."""
        removed_counts: dict[str, int] = {}
        for record_type_id, record_type in record_types.items():
            image_field_keys = [
                f.key for f in record_type.fields if f.type is FieldType.IMAGE
            ]
            if not image_field_keys:
                continue

            referenced = _referenced_filenames(
                record_storage.async_list_records(record_type_id), image_field_keys
            )
            target_dir = self._dir_for_type(record_type_id)
            removed_counts[record_type_id] = await self.hass.async_add_executor_job(
                _remove_unreferenced_files, target_dir, referenced
            )
        return removed_counts

    async def async_remove_all(self) -> None:
        """Delete the entire media directory tree for this entry (uninstall)."""

        def _remove() -> None:
            shutil.rmtree(self._base_dir, ignore_errors=True)

        await self.hass.async_add_executor_job(_remove)


def _referenced_filenames(
    records: list[dict[str, Any]], image_field_keys: list[str]
) -> set[str]:
    """Collect all image filenames referenced by the given records."""
    referenced: set[str] = set()
    for record in records:
        data = record.get(ENVELOPE_DATA, {})
        for field_key in image_field_keys:
            value = data.get(field_key)
            if isinstance(value, dict) and value.get(IMAGE_REF_FILENAME_KEY):
                referenced.add(value[IMAGE_REF_FILENAME_KEY])
    return referenced


async def async_resolve_image_fields(
    media_store: MediaStore,
    record_type: RecordType,
    fields: dict[str, Any],
) -> dict[str, Any]:
    """
    Store any IMAGE-type field values (filesystem paths) and replace them.

    Shared between services.py and websocket_api.py so both write paths apply
    the same handling.
    """
    image_field_keys = {f.key for f in record_type.fields if f.type is FieldType.IMAGE}
    if not image_field_keys:
        return fields

    resolved = dict(fields)
    for key in image_field_keys:
        source_path = resolved.get(key)
        if not source_path:
            continue
        filename = await media_store.async_store_image(record_type.id, source_path)
        resolved[key] = {IMAGE_REF_FILENAME_KEY: filename}
    return resolved
