"""Config and options flow for Custom Metrics Recorder."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from slugify import slugify

from .const import (
    CONF_RECORD_TYPES,
    DEFAULT_WARN_AT,
    DOMAIN,
    RESERVED_FIELD_KEYS,
    SELECT_FIELD_TYPES,
    FieldType,
)
from .models import FieldDefinition, RecordType


def _optional_int(value: Any) -> int | None:
    """Coerce a form value to int, treating blank/None as 'unset'."""
    if value in (None, ""):
        return None
    return int(value)


class CustomMetricsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """
    Config flow for Custom Metrics Recorder.

    There is nothing to configure upfront - this integration has no external
    device/account to connect to, so the flow simply creates the single entry.
    """

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial (and only) step."""
        if user_input is not None:
            return self.async_create_entry(title="Custom Metrics Recorder", data={})
        return self.async_show_form(step_id="user")

    @staticmethod
    @callback
    def async_get_options_flow(
        _config_entry: config_entries.ConfigEntry,
    ) -> CustomMetricsOptionsFlow:
        """Get the options flow for this handler."""
        return CustomMetricsOptionsFlow()


class CustomMetricsOptionsFlow(config_entries.OptionsFlow):
    """Options flow: menu-driven wizard to manage record types and fields."""

    def __init__(self) -> None:
        """Initialize transient wizard state."""
        self._record_types: list[RecordType] = []
        self._new_type_id: str | None = None
        self._new_type_name: str | None = None
        self._editing_type_id: str | None = None
        self._field_buffer: list[FieldDefinition] = []

    def _load_record_types(self) -> None:
        stored = self.config_entry.options.get(CONF_RECORD_TYPES, [])
        self._record_types = [RecordType.from_dict(rt) for rt in stored]

    def _save_record_types(self) -> dict[str, Any]:
        return {CONF_RECORD_TYPES: [rt.to_dict() for rt in self._record_types]}

    def _existing_keys(self) -> set[str]:
        """Return field keys already used by the type being added to/edited."""
        keys = {f.key for f in self._field_buffer}
        if self._editing_type_id is not None:
            existing = next(
                (rt for rt in self._record_types if rt.id == self._editing_type_id),
                None,
            )
            if existing is not None:
                keys |= {f.key for f in existing.fields}
        return keys

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,  # noqa: ARG002
    ) -> config_entries.ConfigFlowResult:
        """Show the main menu."""
        self._load_record_types()
        menu_options = ["add_record_type"]
        if self._record_types:
            menu_options += ["edit_record_type", "remove_record_type", "set_retention"]
        return self.async_show_menu(step_id="init", menu_options=menu_options)

    async def async_step_add_record_type(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Ask for the new record type's name."""
        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input["name"].strip()
            type_id = slugify(name, separator="_")
            if not name:
                errors["name"] = "name_required"
            elif any(rt.id == type_id for rt in self._record_types):
                errors["name"] = "already_exists"
            else:
                self._new_type_name = name
                self._new_type_id = type_id
                self._editing_type_id = None
                self._field_buffer = []
                return await self.async_step_add_field()

        return self.async_show_form(
            step_id="add_record_type",
            data_schema=vol.Schema({vol.Required("name"): str}),
            errors=errors,
        )

    async def async_step_edit_record_type(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Pick an existing record type to add more fields to."""
        if user_input is not None:
            self._editing_type_id = user_input["record_type"]
            self._field_buffer = []
            return await self.async_step_add_field()

        return self.async_show_form(
            step_id="edit_record_type",
            data_schema=vol.Schema(
                {vol.Required("record_type"): _record_type_selector(self._record_types)}
            ),
        )

    async def async_step_add_field(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Add a field to the record type currently being created/edited."""
        errors: dict[str, str] = {}
        if user_input is not None:
            key = user_input["key"].strip()
            if not key:
                errors["key"] = "key_required"
            elif key in RESERVED_FIELD_KEYS:
                errors["key"] = "reserved_key"
            elif key in self._existing_keys():
                errors["key"] = "duplicate_key"
            else:
                field_type = FieldType(user_input["type"])
                options: list[str] | None = None
                if field_type in SELECT_FIELD_TYPES:
                    raw_options = user_input.get("options", "")
                    options = [o.strip() for o in raw_options.split(",") if o.strip()]
                    if not options:
                        errors["options"] = "options_required"

                if not errors:
                    self._field_buffer.append(
                        FieldDefinition(
                            key=key,
                            label=user_input.get("label") or key,
                            type=field_type,
                            required=user_input.get("required", False),
                            unit=user_input.get("unit") or None,
                            options=options,
                        )
                    )
                    if user_input.get("add_another"):
                        return await self.async_step_add_field()
                    return self._finish_field_wizard()

        return self.async_show_form(
            step_id="add_field",
            data_schema=vol.Schema(
                {
                    vol.Required("key"): str,
                    vol.Optional("label"): str,
                    vol.Required(
                        "type", default=FieldType.NUMBER.value
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[t.value for t in FieldType],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Optional("required", default=False): bool,
                    vol.Optional("unit"): str,
                    vol.Optional("options"): str,
                    vol.Optional("add_another", default=False): bool,
                },
            ),
            errors=errors,
            description_placeholders={"count": str(len(self._field_buffer))},
        )

    def _finish_field_wizard(self) -> config_entries.ConfigFlowResult:
        """Persist the accumulated field buffer into a new or existing type."""
        if self._editing_type_id is not None:
            index = next(
                i
                for i, rt in enumerate(self._record_types)
                if rt.id == self._editing_type_id
            )
            existing = self._record_types[index]
            self._record_types[index] = RecordType(
                id=existing.id,
                name=existing.name,
                fields=[*existing.fields, *self._field_buffer],
                timestamp_field=existing.timestamp_field,
                retention_days=existing.retention_days,
                max_records=existing.max_records,
                warn_at=existing.warn_at,
            )
        else:
            self._record_types.append(
                RecordType(
                    id=self._new_type_id,
                    name=self._new_type_name,
                    fields=self._field_buffer,
                )
            )
        return self.async_create_entry(title="", data=self._save_record_types())

    async def async_step_remove_record_type(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Remove an existing record type."""
        if user_input is not None:
            type_id = user_input["record_type"]
            self._record_types = [rt for rt in self._record_types if rt.id != type_id]
            return self.async_create_entry(title="", data=self._save_record_types())

        return self.async_show_form(
            step_id="remove_record_type",
            data_schema=vol.Schema(
                {vol.Required("record_type"): _record_type_selector(self._record_types)}
            ),
        )

    async def async_step_set_retention(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Pick which record type's retention settings to edit."""
        if user_input is not None:
            self._editing_type_id = user_input["record_type"]
            return await self.async_step_set_retention_values()

        return self.async_show_form(
            step_id="set_retention",
            data_schema=vol.Schema(
                {vol.Required("record_type"): _record_type_selector(self._record_types)}
            ),
        )

    async def async_step_set_retention_values(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Edit retention_days / max_records / warn_at for the chosen record type."""
        index = next(
            i
            for i, rt in enumerate(self._record_types)
            if rt.id == self._editing_type_id
        )
        record_type = self._record_types[index]

        if user_input is not None:
            self._record_types[index] = RecordType(
                id=record_type.id,
                name=record_type.name,
                fields=record_type.fields,
                timestamp_field=record_type.timestamp_field,
                retention_days=_optional_int(user_input.get("retention_days")),
                max_records=_optional_int(user_input.get("max_records")),
                warn_at=_optional_int(user_input.get("warn_at")),
            )
            return self.async_create_entry(title="", data=self._save_record_types())

        return self.async_show_form(
            step_id="set_retention_values",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "retention_days",
                        description={"suggested_value": record_type.retention_days},
                    ): vol.Any(None, vol.Coerce(int)),
                    vol.Optional(
                        "max_records",
                        description={"suggested_value": record_type.max_records},
                    ): vol.Any(None, vol.Coerce(int)),
                    vol.Optional(
                        "warn_at",
                        description={
                            "suggested_value": record_type.warn_at or DEFAULT_WARN_AT
                        },
                    ): vol.Any(None, vol.Coerce(int)),
                }
            ),
            description_placeholders={"name": record_type.name},
        )


def _record_type_selector(record_types: list[RecordType]) -> selector.SelectSelector:
    """Build a SelectSelector listing the given record types by name."""
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[
                selector.SelectOptionDict(value=rt.id, label=rt.name)
                for rt in record_types
            ]
        )
    )
