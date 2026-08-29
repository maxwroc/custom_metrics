"""Config and record-type subentry flows for Custom Metrics Recorder."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.util import slugify

from .const import (
    DEFAULT_WARN_AT,
    DOMAIN,
    EXPORT_URL_PREFIX,
    RESERVED_FIELD_KEYS,
    SELECT_FIELD_TYPES,
    SUBENTRY_TYPE_RECORD_TYPE,
    FieldType,
)
from .csv_transfer import parse_import_csv
from .models import FieldDefinition, RecordType

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .store import ImportSummary


def _optional_int(value: Any) -> int | None:
    """Coerce a form value to int, treating blank/None as 'unset'."""
    if value in (None, ""):
        return None
    return int(value)


def _require_str(value: str | None) -> str:
    """
    Narrow an Optional[str] known to be non-None at this point in the flow.

    Used for values sourced from ConfigSubentry.unique_id (typed str | None
    generically, though our own record_type subentries always set it) and
    from this flow's own transient state (always set by an earlier step
    before these accessors are reached). Raises rather than using a bare
    `assert` (stripped under `python -O`, and flagged by ruff's S101).
    """
    if value is None:
        msg = "Expected a value to be set at this point in the flow"
        raise ValueError(msg)
    return value


def _require_import_summary(value: ImportSummary | None) -> ImportSummary:
    """Narrow the transient _import_summary, set by async_step_import_data."""
    if value is None:
        msg = "Expected an import summary to be set at this point in the flow"
        raise ValueError(msg)
    return value


def _field_schema() -> vol.Schema:
    """Build the "add a field" form schema, shared by the create/reconfigure flows."""
    return vol.Schema(
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
    )


def _field_selector(fields: list[FieldDefinition]) -> selector.SelectSelector:
    """Build a SelectSelector listing fields as 'Label (key)', so the key is visible."""
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[
                selector.SelectOptionDict(value=f.key, label=f"{f.label} ({f.key})")
                for f in fields
            ]
        )
    )


class CustomMetricsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """
    Config flow for Custom Metrics Recorder.

    There is nothing to configure upfront - this integration has no external
    device/account to connect to, so the flow simply creates the single entry.
    Record types are configured afterwards as config subentries (see
    RecordTypeSubentryFlow below), so each one shows up as its own visible,
    individually manageable row directly on the integration's card in
    Settings > Devices & Services, with built-in add/reconfigure/delete
    actions - no separate "Configure" options dialog needed.
    """

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial (and only) step."""
        if user_input is not None:
            return self.async_create_entry(title="Custom Metrics Recorder", data={})
        return self.async_show_form(step_id="user")

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls,
        config_entry: ConfigEntry,  # noqa: ARG003
    ) -> dict[str, type[config_entries.ConfigSubentryFlow]]:
        """Each record type is configured as a 'record_type' subentry."""
        return {SUBENTRY_TYPE_RECORD_TYPE: RecordTypeSubentryFlow}


class RecordTypeSubentryFlow(config_entries.ConfigSubentryFlow):
    """
    Add or reconfigure a single record type (name, fields, retention).

    One subentry per record type. Adding/removing is handled by Home
    Assistant's own built-in subentry UI (a "+" button on the integration's
    card, and a per-row Delete action) - only the add wizard (this flow's
    `user` step) and the reconfigure menu (`reconfigure` step) need to be
    implemented here.
    """

    def __init__(self) -> None:
        """Initialize transient wizard state."""
        self._name: str | None = None
        self._type_id: str | None = None
        self._fields: list[FieldDefinition] = []
        self._field_buffer: list[FieldDefinition] = []
        self._editing_field_key: str | None = None
        self._import_summary: ImportSummary | None = None
        self._import_errors: list[dict[str, Any]] = []

    # -- shared helpers ---------------------------------------------------

    def _existing_type_ids(self) -> set[str]:
        return {
            subentry.unique_id
            for subentry in self._get_entry().subentries.values()
            if subentry.subentry_type == SUBENTRY_TYPE_RECORD_TYPE
            and subentry.unique_id is not None
        }

    def _existing_keys(self) -> set[str]:
        return {f.key for f in (*self._fields, *self._field_buffer)}

    def _current_record_type(self) -> RecordType:
        subentry = self._get_reconfigure_subentry()
        return RecordType.from_subentry(
            _require_str(subentry.unique_id), subentry.title, dict(subentry.data)
        )

    def _parse_field_input(
        self, user_input: dict[str, Any]
    ) -> tuple[FieldDefinition | None, dict[str, str]]:
        """Validate an add-field form submission; return (field_or_None, errors)."""
        errors: dict[str, str] = {}
        key = user_input["key"].strip()
        if not key:
            errors["key"] = "key_required"
        elif key in RESERVED_FIELD_KEYS:
            errors["key"] = "reserved_key"
        elif key in self._existing_keys():
            errors["key"] = "duplicate_key"

        options: list[str] | None = None
        field_type = FieldType(user_input["type"])
        if not errors and field_type in SELECT_FIELD_TYPES:
            raw_options = user_input.get("options", "")
            options = [o.strip() for o in raw_options.split(",") if o.strip()]
            if not options:
                errors["options"] = "options_required"

        if errors:
            return None, errors
        return (
            FieldDefinition(
                key=key,
                label=user_input.get("label") or key,
                type=field_type,
                required=user_input.get("required", False),
                unit=user_input.get("unit") or None,
                options=options,
            ),
            errors,
        )

    # -- create a new record type -----------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.SubentryFlowResult:
        """Ask for the new record type's name."""
        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input["name"].strip()
            type_id = slugify(name, separator="_")
            if not name:
                errors["name"] = "name_required"
            elif type_id in self._existing_type_ids():
                errors["name"] = "already_exists"
            else:
                self._name = name
                self._type_id = type_id
                self._fields = []
                self._field_buffer = []
                return await self.async_step_add_field()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required("name"): str}),
            errors=errors,
        )

    async def async_step_add_field(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.SubentryFlowResult:
        """Add a field to the record type currently being created."""
        errors: dict[str, str] = {}
        if user_input is not None:
            field, errors = self._parse_field_input(user_input)
            if field is not None:
                self._field_buffer.append(field)
                if user_input.get("add_another"):
                    return await self.async_step_add_field()
                record_type = RecordType(
                    id=_require_str(self._type_id),
                    name=_require_str(self._name),
                    fields=[*self._fields, *self._field_buffer],
                )
                return self.async_create_entry(
                    title=record_type.name,
                    data=record_type.to_subentry_data(),
                    unique_id=record_type.id,
                )

        return self.async_show_form(
            step_id="add_field",
            data_schema=_field_schema(),
            errors=errors,
            description_placeholders={"count": str(len(self._field_buffer))},
        )

    # -- reconfigure an existing record type -------------------------------

    async def async_step_reconfigure(
        self,
        user_input: dict[str, Any] | None = None,  # noqa: ARG002
    ) -> config_entries.SubentryFlowResult:
        """Show the menu of things that can be changed about this record type."""
        subentry = self._get_reconfigure_subentry()
        self._type_id = _require_str(subentry.unique_id)
        self._name = subentry.title
        self._fields = self._current_record_type().fields
        self._field_buffer = []
        return self.async_show_menu(
            step_id="reconfigure",
            menu_options=[
                "manage_fields",
                "reconfigure_add_field",
                "set_retention",
                "change_type_key",
                "export_data",
                "import_data",
            ],
            description_placeholders={"name": self._name, "key": self._type_id},
        )

    async def async_step_reconfigure_add_field(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.SubentryFlowResult:
        """Add one or more new fields to an existing record type."""
        errors: dict[str, str] = {}
        if user_input is not None:
            field, errors = self._parse_field_input(user_input)
            if field is not None:
                self._field_buffer.append(field)
                if user_input.get("add_another"):
                    return await self.async_step_reconfigure_add_field()
                all_fields = [*self._fields, *self._field_buffer]
                return self.async_update_and_abort(
                    self._get_entry(),
                    self._get_reconfigure_subentry(),
                    data_updates={"fields": [f.to_dict() for f in all_fields]},
                )

        return self.async_show_form(
            step_id="reconfigure_add_field",
            data_schema=_field_schema(),
            errors=errors,
            description_placeholders={"count": str(len(self._field_buffer))},
        )

    async def async_step_manage_fields(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.SubentryFlowResult:
        """Pick which field to manage (edit label, change key, or delete)."""
        if user_input is not None:
            self._editing_field_key = user_input["field_key"]
            return await self.async_step_field_actions()

        return self.async_show_form(
            step_id="manage_fields",
            data_schema=vol.Schema(
                {vol.Required("field_key"): _field_selector(self._fields)}
            ),
        )

    async def async_step_field_actions(
        self,
        user_input: dict[str, Any] | None = None,  # noqa: ARG002
    ) -> config_entries.SubentryFlowResult:
        """Show what can be done with the field picked in manage_fields."""
        field = next(f for f in self._fields if f.key == self._editing_field_key)
        return self.async_show_menu(
            step_id="field_actions",
            menu_options=["edit_field_label", "change_field_key", "delete_field"],
            description_placeholders={"label": field.label, "key": field.key},
        )

    async def async_step_edit_field_label(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.SubentryFlowResult:
        """Change only a field's display label (its key is untouched)."""
        field = next(f for f in self._fields if f.key == self._editing_field_key)
        errors: dict[str, str] = {}
        if user_input is not None:
            new_label = user_input["label"].strip()
            if not new_label:
                errors["label"] = "label_required"
            else:
                updated_fields = [
                    FieldDefinition(
                        key=f.key,
                        label=new_label if f.key == field.key else f.label,
                        type=f.type,
                        required=f.required,
                        unit=f.unit,
                        default=f.default,
                        options=f.options,
                    )
                    for f in self._fields
                ]
                return self.async_update_and_abort(
                    self._get_entry(),
                    self._get_reconfigure_subentry(),
                    data_updates={"fields": [f.to_dict() for f in updated_fields]},
                )

        return self.async_show_form(
            step_id="edit_field_label",
            data_schema=vol.Schema({vol.Required("label", default=field.label): str}),
            errors=errors,
            description_placeholders={"key": field.key},
        )

    async def async_step_change_field_key(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.SubentryFlowResult:
        """
        Change a field's key, migrating every stored record to match.

        Requires explicit confirmation: any automation or dashboard card that
        references the old key by name (e.g. `fields: {<old_key>: ...}`) will
        silently stop working, and we have no way to detect or fix that for
        the user - only the record data itself can be migrated safely.
        """
        old_key = _require_str(self._editing_field_key)
        errors: dict[str, str] = {}
        if user_input is not None:
            new_key = user_input["new_key"].strip()
            if not new_key:
                errors["new_key"] = "key_required"
            elif new_key in RESERVED_FIELD_KEYS:
                errors["new_key"] = "reserved_key"
            elif new_key != old_key and any(f.key == new_key for f in self._fields):
                errors["new_key"] = "duplicate_key"
            elif not user_input.get("confirm"):
                errors["confirm"] = "confirmation_required"
            else:
                entry = self._get_entry()
                if new_key != old_key:
                    await entry.runtime_data.storage.async_rename_field_key(
                        _require_str(self._type_id), old_key, new_key
                    )
                updated_fields = [
                    FieldDefinition(
                        key=new_key if f.key == old_key else f.key,
                        label=f.label,
                        type=f.type,
                        required=f.required,
                        unit=f.unit,
                        default=f.default,
                        options=f.options,
                    )
                    for f in self._fields
                ]
                return self.async_update_and_abort(
                    entry,
                    self._get_reconfigure_subentry(),
                    data_updates={"fields": [f.to_dict() for f in updated_fields]},
                )

        return self.async_show_form(
            step_id="change_field_key",
            data_schema=vol.Schema(
                {
                    vol.Required("new_key", default=old_key): str,
                    vol.Required("confirm", default=False): bool,
                }
            ),
            errors=errors,
            description_placeholders={"key": old_key},
        )

    async def async_step_delete_field(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.SubentryFlowResult:
        """
        Remove a field from this record type, after explicit confirmation.

        Existing stored records keep their data under this key (nothing is
        deleted from disk), but any automation or dashboard card that still
        submits this field will start failing validation - we can't detect
        or fix that for the user, hence the confirmation requirement.
        """
        field = next(f for f in self._fields if f.key == self._editing_field_key)
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get("confirm"):
                errors["confirm"] = "confirmation_required"
            else:
                remaining_fields = [f for f in self._fields if f.key != field.key]
                return self.async_update_and_abort(
                    self._get_entry(),
                    self._get_reconfigure_subentry(),
                    data_updates={"fields": [f.to_dict() for f in remaining_fields]},
                )

        return self.async_show_form(
            step_id="delete_field",
            data_schema=vol.Schema({vol.Required("confirm", default=False): bool}),
            errors=errors,
            description_placeholders={"key": field.key, "label": field.label},
        )

    async def async_step_change_type_key(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.SubentryFlowResult:
        """
        Change the record type's own id/key, migrating its stored data.

        Requires explicit confirmation for the same reason as
        change_field_key_value - a rename here also changes the value
        automations must pass for `record_type`, and any dashboard card's
        `record_type:` config, which we can't detect or fix automatically.
        """
        old_id = _require_str(self._type_id)
        errors: dict[str, str] = {}
        if user_input is not None:
            new_id = user_input["new_key"].strip()
            if not new_id:
                errors["new_key"] = "key_required"
            elif new_id != old_id and new_id in self._existing_type_ids():
                errors["new_key"] = "already_exists"
            elif not user_input.get("confirm"):
                errors["confirm"] = "confirmation_required"
            else:
                entry = self._get_entry()
                if new_id != old_id:
                    await entry.runtime_data.storage.async_rename_record_type(
                        old_id, new_id
                    )
                    await entry.runtime_data.media_store.async_rename_record_type(
                        old_id, new_id
                    )
                return self.async_update_and_abort(
                    entry, self._get_reconfigure_subentry(), unique_id=new_id
                )

        return self.async_show_form(
            step_id="change_type_key",
            data_schema=vol.Schema(
                {
                    vol.Required("new_key", default=old_id): str,
                    vol.Required("confirm", default=False): bool,
                }
            ),
            errors=errors,
            description_placeholders={"key": old_id, "name": _require_str(self._name)},
        )

    async def async_step_set_retention(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.SubentryFlowResult:
        """Edit retention_days / max_records / warn_at for this record type."""
        record_type = self._current_record_type()
        if user_input is not None:
            return self.async_update_and_abort(
                self._get_entry(),
                self._get_reconfigure_subentry(),
                data_updates={
                    "retention_days": _optional_int(user_input.get("retention_days")),
                    "max_records": _optional_int(user_input.get("max_records")),
                    "warn_at": _optional_int(user_input.get("warn_at")),
                },
            )

        return self.async_show_form(
            step_id="set_retention",
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

    async def async_step_export_data(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.SubentryFlowResult:
        """
        Build a short-lived signed download link for this record type's CSV.

        `include_id` (checked by default) selects "full backup" (id +
        timestamp + fields, safe for idempotent re-import) vs. "data only"
        (drops the internal id, keeps timestamp since it's meaningful data -
        re-importing always creates new records with the original
        timestamps preserved).
        """
        if user_input is not None:
            entry = self._get_entry()
            type_id = _require_str(self._type_id)
            include_id = user_input["include_id"]
            url = (
                f"{EXPORT_URL_PREFIX}/{entry.entry_id}/{type_id}"
                f"?include_id={'true' if include_id else 'false'}"
            )
            signed_url = async_sign_path(self.hass, url, timedelta(minutes=5))
            return self.async_abort(
                reason="export_ready",
                description_placeholders={"download_url": signed_url},
            )

        return self.async_show_form(
            step_id="export_data",
            data_schema=vol.Schema({vol.Required("include_id", default=True): bool}),
        )

    async def async_step_import_data(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.SubentryFlowResult:
        """Upload a CSV file and import its rows into this record type."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                with process_uploaded_file(self.hass, user_input["file"]) as path:
                    csv_text = path.read_text(encoding="utf-8")
            except ValueError:
                errors["file"] = "file_not_found"
            else:
                record_type = self._current_record_type()
                parse_result = parse_import_csv(record_type, csv_text)
                storage = self._get_entry().runtime_data.storage
                self._import_summary = await storage.async_import_records(
                    record_type.id, parse_result.rows
                )
                self._import_errors = parse_result.errors
                return await self.async_step_import_result()

        return self.async_show_form(
            step_id="import_data",
            data_schema=vol.Schema(
                {
                    vol.Required("file"): selector.FileSelector(
                        selector.FileSelectorConfig(accept=".csv")
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_import_result(
        self,
        user_input: dict[str, Any] | None = None,  # noqa: ARG002
    ) -> config_entries.SubentryFlowResult:
        """Show a one-shot summary of the import that just completed."""
        summary = _require_import_summary(self._import_summary)
        errors_text = "; ".join(
            f"row {error['row']}: {error['message']}"
            for error in self._import_errors[:5]
        )
        return self.async_abort(
            reason="import_complete",
            description_placeholders={
                "imported": str(summary.imported),
                "skipped": str(summary.skipped_duplicate),
                "error_count": str(len(self._import_errors)),
                "errors": errors_text or "(none)",
            },
        )
