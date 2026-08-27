"""Data models for custom_metrics record types and field definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .const import SELECT_FIELD_TYPES, FieldType


@dataclass(frozen=True, slots=True)
class FieldDefinition:
    """Definition of a single field within a record type."""

    key: str
    label: str
    type: FieldType
    required: bool = False
    unit: str | None = None
    default: Any = None
    options: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {
            "key": self.key,
            "label": self.label,
            "type": self.type.value,
            "required": self.required,
            "unit": self.unit,
            "default": self.default,
            "options": self.options,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FieldDefinition:
        """Deserialize from a JSON-compatible dict."""
        return cls(
            key=data["key"],
            label=data["label"],
            type=FieldType(data["type"]),
            required=data.get("required", False),
            unit=data.get("unit"),
            default=data.get("default"),
            options=data.get("options"),
        )

    def __post_init__(self) -> None:
        """Validate select-type fields carry an options list."""
        if self.type in SELECT_FIELD_TYPES and not self.options:
            msg = f"Field '{self.key}' ({self.type}) requires a non-empty options list"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class RecordType:
    """A user-defined record type (e.g. 'Blood Pressure')."""

    id: str
    name: str
    fields: list[FieldDefinition] = field(default_factory=list)
    timestamp_field: str = "timestamp"
    retention_days: int | None = None
    max_records: int | None = None
    warn_at: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {
            "id": self.id,
            "name": self.name,
            "fields": [f.to_dict() for f in self.fields],
            "timestamp_field": self.timestamp_field,
            "retention_days": self.retention_days,
            "max_records": self.max_records,
            "warn_at": self.warn_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RecordType:
        """Deserialize from a JSON-compatible dict."""
        return cls(
            id=data["id"],
            name=data["name"],
            fields=[FieldDefinition.from_dict(f) for f in data.get("fields", [])],
            timestamp_field=data.get("timestamp_field", "timestamp"),
            retention_days=data.get("retention_days"),
            max_records=data.get("max_records"),
            warn_at=data.get("warn_at"),
        )

    def get_field(self, key: str) -> FieldDefinition | None:
        """Return the field definition matching key, if any."""
        return next((f for f in self.fields if f.key == key), None)
