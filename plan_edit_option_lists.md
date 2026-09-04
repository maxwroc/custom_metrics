# Editable single/multi-select option lists

Status: **implemented and validated on Unix** (see "Validation status" below).
The original hand-off note below was written on Windows, where the Home
Assistant pytest suite couldn't run (missing `fcntl`, Unix-only, imported by
HA's test plugin at load time) - that gap has since been closed.

## Goal

Field modifications are intentionally unsupported. The only prior exception was
`append_select_option`, which let a user *append* new values to a
single/multi-select field. This change makes the accepted-values list of a
`single_select`/`multi_select` field **fully editable**: add, remove, rename,
and reorder options.

## Confirmed behavior (decided with the user)

- Options stay **plain strings** — the stored value *is* the string. No
  value/label object model was introduced.
- **Remove**: allowed even if existing records still use the value. Those rows
  are left as-is; their stored value becomes an "orphaned" value (no longer in
  the accepted list). No DB rewrite, no blocking.
- **Rename**: implemented as editing the option string in place in the config
  list. Existing records keep the old string (orphaned). No DB rewrite.
- **Reorder**: falls out for free from editing the full list; only affects the
  add-record form's option order.
- Net effect: the accepted-values list is freely editable. Only forward writes
  are validated against the current list; historical rows are never touched or
  re-validated on read.
- **Default pruning**: if a field's configured `default` references a value that
  is no longer present after the edit, it is auto-pruned (there is no separate
  default-edit step): single-select default is cleared to `None`; multi-select
  default keeps only still-valid items.

## Why this is safe

Options are only consumed for **write-time** validation (`schema.py`
`vol.In(options)`) and for rendering the add-record form. Reads return raw
stored values and never validate against `options`, so orphaned values render
and export fine. Multi-select orphans remain valid JSON arrays. This was
verified by grepping every `.options` usage across the component.

## Files changed

### `custom_components/custom_metrics/config_flow.py`
- `field_actions` menu now offers `edit_select_options` (was
  `append_select_option`) for `SELECT_FIELD_TYPES` fields.
- New `async_step_edit_select_options`: prefills a comma-separated list of the
  current options; the user edits it to add/remove/rename/reorder. Validation:
  - trims whitespace on each item and drops empty items,
  - rejects an empty resulting list -> error key `options_required`,
  - rejects duplicates -> error key `duplicate_option`,
  - preserves order.
  On success it rebuilds the `fields` list (keeping every other attribute
  including the immutable `sql_column`) and persists via
  `async_update_and_abort`.
- New module-level helper `_prune_default(field, options)`: returns the pruned
  default (single-select cleared if missing; multi-select filtered to present
  items; other types untouched).
- Old `async_step_append_select_option` was removed.

### `custom_components/custom_metrics/strings.json` and `translations/en.json`
- `field_actions.menu_options`: `append_select_option` -> `edit_select_options`
  ("Edit options").
- New `edit_select_options` step block: title, description (shows current
  options `{options}` for field `{key}` and explains that removed/renamed values
  stay on old records), and data label `options` ("Options (comma-separated)").
- `error.duplicate_option` message generalized to "Options must be unique -
  remove the duplicate values." (`options_required` reused as-is.)

### `custom_components/custom_metrics/models.py`
- No behavioral change (removal was already permitted — `__post_init__` only
  enforces a non-empty options list). Confirmed there were no append-only
  docstrings to update in the Python source.

### `README.md`
- Added a compact, user-facing note (in "Create a record type"): fields can't
  be changed after creation, but a record type can be reconfigured to edit a
  field's label and to manage a select field's accepted values
  (add/remove/rename/reorder); existing records keep their stored value.

### `plan_sql.md`
- Status note + Phase 1 pt.6 updated: options are now fully editable via
  `edit_select_options` with orphaned-value semantics and default auto-pruning;
  marked done. (Removed the "options may only be appended" wording.)

### `plan_lit.md`
- Updated a stale example reference from `append_select_option` to
  `edit_select_options`.

### `tests/test_config_flow.py`
Replaced the two `append_select_option` tests with five `edit_select_options`
tests:
- `test_edit_select_options_add_remove_rename_reorder` — asserts the menu shows
  `{edit_field_label, edit_select_options}`, then edits `["happy","sad"]` into
  `["excited","glad"]` (drop, rename, add, reorder) and checks `sql_column` is
  preserved.
- `test_edit_select_options_rejects_empty` — `"  ,  "` -> error
  `{"options": "options_required"}`.
- `test_edit_select_options_rejects_duplicate` — `"happy, happy"` -> error
  `{"options": "duplicate_option"}`.
- `test_edit_select_options_prunes_single_default` — default `"sad"`; editing to
  `"happy, glad"` clears the default to `None`.
- `test_edit_select_options_prunes_multi_default` — multi-select default
  `["a","b"]`, options `["a","b","c"]`; editing to `"a"` yields options `["a"]`
  and default `["a"]`.

## Validation status (updated after real Unix validation)

- `ruff check custom_components/custom_metrics tests` -> passes.
- `ruff format --check custom_components/custom_metrics tests` -> passes (after
  auto-formatting the two files touched by the fixes below).
- `strings.json` and `translations/en.json` parse as valid JSON.
- `pytest tests/ -q` -> **180 passed** (full suite, run on this Unix devcontainer).
- No Pylance errors on any changed file.

### Bugs found and fixed during Unix validation

1. **Blocking syntax bug (pre-existing, unrelated to this feature):**
   `config_flow.py` had `except sqlite3.Error, SchemaError:` at two spots
   (`async_step_add_field`, `async_step_reconfigure_add_field`) - Python-2
   syntax, a `SyntaxError` in Python 3 ("multiple exception types must be
   parenthesized"). This meant `config_flow.py` could not be imported at all,
   so every test importing it (not just the new `edit_select_options` tests)
   would have failed at collection. The earlier "it compiled/parsed under
   Python 3.14" claim in this doc was not actually verified - don't trust
   claims like that without a real run. Fixed: both changed to
   `except (sqlite3.Error, SchemaError):`.
2. **CSV import/export incorrectly enforced `options` (user-requested fix):**
   `schema.py`'s `_validator_for_field` used `vol.In(options)` for
   `single_select`/`multi_select`, which `build_import_field_validators`
   (used by `csv_transfer.py`'s CSV import, shared by the config flow's
   "Import data" step and the `import_records` service) reused unchanged.
   That meant importing a CSV referencing a value no longer in the field's
   current `options` (e.g. a backup taken before an edit) was wrongly
   rejected as a row error. `options` is only a UI convenience to reduce
   typos in the add-record form/service, not a hard data constraint on
   import - it never was one for CSV export either (`build_export_csv`
   already didn't validate, needed no change).
   Fix: `_validator_for_field` gained an `enforce_options: bool = True`
   parameter; `build_import_field_validators` now passes
   `enforce_options=False` (select fields import as plain string /
   list-of-strings, no `options` check). `build_fields_schema` (used by
   `validate_record_data`, i.e. `add_record` via service/automation/
   WebSocket API) and `validate_filter_value` are unchanged - they still
   enforce `options`, per explicit user confirmation.
   New tests in `tests/test_csv_transfer.py`:
   `test_import_multi_select_value_not_in_options_is_accepted`,
   `test_import_single_select_value_not_in_options_is_accepted`.

## Notes / possible follow-ups

- There is no dedicated "edit default" step; default pruning is the only way an
  edit can change a default. If a UI to edit defaults is wanted later, that is a
  separate change.
- ~~`except sqlite3.Error, SchemaError:` appears twice in `config_flow.py`~~ -
  fixed during Unix validation, see "Validation status" above.
- Possible future addition (not implemented, not requested yet): an end-to-end
  test confirming a record written *before* an options edit still reads/exports
  correctly with its now-orphaned value afterward. Currently only verified by
  code review (every `.options` usage grepped) and by the config-flow-level
  `field.options`/`field.default` assertions, not by an integration-level test
  that round-trips an actual stored record through an edit.
