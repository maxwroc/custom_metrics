# Editable single/multi-select option lists

Hand-off note for continuing **test validation on a Unix machine**. All code is
implemented and lint-clean on Windows; the Home Assistant pytest suite could not
be run here because this host lacks `fcntl` (Unix-only), which HA's test plugin
imports at load time.

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

## Validation status

- `ruff check custom_components/custom_metrics/config_flow.py
  tests/test_config_flow.py` -> passes.
- `strings.json` and `translations/en.json` parse as valid JSON.
- **Pending (run on Unix):**
  ```
  pip install -r requirements_dev.txt
  pytest tests/test_config_flow.py -q
  ```
  Then, if desired, the full suite: `pytest -q`.

## Notes / possible follow-ups

- There is no dedicated "edit default" step; default pruning is the only way an
  edit can change a default. If a UI to edit defaults is wanted later, that is a
  separate change.
- `except sqlite3.Error, SchemaError:` appears twice in `config_flow.py`
  (lines ~307 and ~386). It is pre-existing and out of scope for this task, but
  worth a look — it reads like Python-2 `except A, B:` syntax. It compiled/parsed
  under Python 3.14 here; verify it does the intended thing on your runtime.
