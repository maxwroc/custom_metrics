# Plan: Custom Metrics Recorder (HA Integration)

## Context
- Project: HomeAssistant custom integration to record user-defined metrics (blood pressure, fuel costs, etc.)
- Source spec: specs.txt (P0 and P1 requirements)
- Suggested name: "Custom Metrics Recorder" (domain: `custom_metrics`)

## Key Decision: Integration vs Addon
- DECIDED: Build as an HA **integration**, not an addon.
- Rationale:
  - All P0 requirements achievable via integration APIs: Store helper/SQLite for storage,
    services (hass.services.async_register) for "save record", WebSocket API /
    REST HomeAssistantView for data exposure to custom Lovelace cards, async_track_time_interval
    for retention/purge jobs, config/options flow for user-defined schemas.
  - Integrations work on all install types (Core/Container/Supervised/HA OS); addons require Supervisor.
  - Data volume is small/personal-scale — doesn't justify separate container/DB engine.
  - Simpler distribution via HACS, no Docker/addon repo maintenance.
  - P1 "custom page" can be done via integration-registered panel (async_register_built_in_panel +
    static frontend files), so addon not required even for that.
  - Caveat: if a fully standalone web app decoupled from the HA UI shell is ever wanted,
    addon+ingress would be the alternative — otherwise integration is recommended default.

## Finding: Auto-registering the custom Lovelace card
- Integration can auto-register bundled custom card with NO manual user step:
  - Serve JS via hass.http.register_static_path (or StaticPathConfig / async_register_static_paths in newer HA)
  - Call homeassistant.components.frontend.add_extra_js_url(hass, url) in async_setup/async_setup_entry
    to inject the module globally (no Lovelace "resources" entry needed).
- Addon CANNOT do this directly (no hass access in container). Would need to call HA WebSocket/REST API
  with a long-lived token to create a `lovelace/resources/create` entry, which only works for
  storage-mode dashboards (not YAML mode) — fragile, not recommended.
- This further reinforces choosing the Integration approach over Addon.

## Alignment answers (confirmed with user)
- Scope: P0 only for this plan. P1 (field relations, custom browse/edit page) deferred to a
  follow-up plan; only leave extension points (panel registration hook) in place.
- Field data types for v1 record schema: number, text, long_text (multiline/notes), boolean,
  datetime, single_select (enum/dropdown), multi_select. Select-type fields carry a
  user-defined list of options set when the field is created. (Image field added later — see Phase I.)
- Dev/test environment: VS Code Dev Container (Docker), matching the standard
  ludeeus/integration_blueprint workflow (devcontainer + scripts/develop + config/ test instance).
  Use a disposable/dedicated test HA instance, NOT the user's main/production HA instance.
- Plan document: saved as `plan.md` at the project root (this file).

## Research findings (HA developer docs, current as of 2026-08-27)
- Config flow: custom_components with config_flow must set "config_flow": true in manifest.json
  and define ConfigFlow subclass in config_flow.py (domain=DOMAIN). Since this integration has no
  external device/account to connect to, async_step_user should just create the entry immediately
  (title "Custom Metrics Recorder"). manifest.json should set "single_config_entry": true (one
  instance manages all record types/data).
- Record-type/field schema definition must NOT use YAML — implement via OptionsFlow (menu-based
  multi-step wizard): add/edit/remove record type, then loop to add fields (name, data type,
  required, unit, default, options-for-select-types), "add another field?" loop pattern is a
  standard data_entry_flow technique.
- Services: register in async_setup (module-level, not async_setup_entry) per HA guidance so
  automations can reference/validate the service even before any config entry loads. Use
  hass.services.async_register(DOMAIN, "add_record", handler, schema=..., supports_response=
  SupportsResponse.OPTIONAL) to optionally return the created record with generated id.
- Storage: homeassistant.helpers.storage.Store (JSON) is sufficient for personal-scale data
  (thousands of records/health+finance logs) — no need for SQLite/external DB. One Store keyed by
  config entry, versioned STORAGE_VERSION for future migrations.
- Retention: async_track_time_interval (or async_track_time_change once/day) to purge expired
  records per record-type retention_days setting (None = forever).
- Data exposure/custom API: register WebSocket API commands via
  websocket_api.async_register_command (list_record_types, list_records, add_record,
  delete_record) — this is the "custom API" satisfying P0's exposure requirement, used by the
  custom Lovelace card.
- Auto-registering the custom card (previously researched, reconfirmed): serve the bundled JS via
  hass.http.async_register_static_paths([StaticPathConfig(url_path, file_path, cache_headers=False)])
  and call homeassistant.components.frontend.add_extra_js_url(hass, url) during async_setup/
  async_setup_entry. No manual Lovelace "resource" step needed for the user.
- Dev workflow reference: ludeeus/integration_blueprint GitHub template is the standard scaffold
  for custom integrations — ships .devcontainer.json, config/ (local HA test config dir),
  scripts/setup + scripts/develop (launches `hass -c config` in foreground), hacs.json,
  requirements_dev.txt. Recommends pytest-homeassistant-custom-component for tests.
- Iteration loop: after code changes, reload the config entry (UI "Reload" action or
  hass.config_entries.async_reload) to re-run async_setup_entry/async_unload_entry — no full HA
  restart needed, PROVIDED async_unload_entry correctly tears down services/listeners/ws commands
  registered per-entry. Full restart only needed for manifest.json changes (e.g. first time adding
  config_flow/requirements) or www/ JS changes are picked up via browser refresh only (no reload
  needed, since served as static files with cache disabled in dev).
- HACS integration requirements (hacs.xyz/docs/publish/integration): single integration dir under
  custom_components/<domain>/, hacs.json at repo root, manifest.json needs domain, name,
  documentation, issue_tracker, codeowners, version; brand assets (icon) recommended; GitHub
  releases (tags) preferred for version resolution; repo needs description + topics + README
  (manual GitHub settings, not files).
- Storage size/compression (verified against HA core source, homeassistant/helpers/storage.py):
  `Store` does NOT compress data — it writes via `write_utf8_file`/`write_utf8_file_atomic`, plain
  UTF-8 JSON text, using orjson only for fast serialization (not compression). So on-disk file
  size is exactly the JSON text size; shorter keys directly/linearly reduce file size and I/O
  bytes on every save/load — a real win given the earlier storage-scalability concerns.
  Orjson's own docs confirm it maintains a **process-wide cache of dict keys during deserialization
  ("must be at most 64 bytes to be cached, 2048 entries stored")**, de-duplicating repeated key
  strings across all loaded records — so shortening keys has a much smaller effect on in-memory
  RAM footprint than on disk size (the key string itself is shared across all records regardless
  of length, as long as under 64 bytes, which all our keys are).

## Storage key-naming decisions (follow-up question)
- Reserved/internal envelope keys (fully in our control, zero UX cost, appear on every record —
  highest leverage for size savings): compact record envelope `{"id": <uuid>, "t": <iso datetime>,
  "d": {<user field key>: value, ...}}` — `id`/`t`/`d` instead of longer names; nested `"d"` wrapper
  chosen over a flat structure specifically to avoid any collision risk with user-chosen field
  keys (flat would require reserving "id"/"t" as forbidden field key names — added validation
  complexity for a ~6-byte-per-record saving, not worth it).
- Image reference object (Phase I): `{"filename": ...}` → `{"f": ...}` as originally suggested;
  future thumbnail key would similarly be short (e.g. `"th"`).
- User-defined field keys (e.g. "systolic"): confirmed with user — kept EXACTLY as typed in both
  storage and the public `add_record` service/WebSocket API (no internal compact-key translation
  layer). Rationale: users already fully control this (can choose short keys themselves), and a
  translation layer would add real complexity (renaming/reordering edge cases) for a comparatively
  smaller saving than the reserved-key changes above. Instead, add a lightweight UI hint in the
  Options flow's "add field" step suggesting short keys for record types expected to be logged
  frequently by automations.

## Field-key collision analysis (follow-up question)
- Raw internal storage: NO collision possible. The nested envelope `{"id", "t", "d": {fields}}`
  means a user field named "t" lives at `d.t`, a different JSON path from the top-level "t" —
  this is precisely why nesting was chosen over a flat internal structure.
- Public API shape IS flat (see Phase F/K): WebSocket `list_records`/`add_record` responses expose
  records as `{"id": ..., "timestamp": ..., ...user fields}` (friendly names, flattened for card/
  automation ergonomics — public-facing keys don't need to be as compact as the on-disk envelope).
  This flattening reintroduces real collision risk: a user field literally named "timestamp" or
  "id" would silently overwrite/shadow the envelope's own value in that merged public dict — a
  genuine data-integrity bug, not just cosmetic.
- Fix (added to Phase D step 10 above): validate user-chosen field keys against a reserved-word
  disallow-list (`id`, `timestamp`, `record_type`) in `async_step_add_field`, rejecting with a
  clear error if matched. Cheap, closes the gap completely, and only needs to live at the one
  place field keys are created (the Options flow), not scattered through the read/response paths.

## Storage scalability analysis (JSON Store capacity, follow-up question)
- `Store.async_load()` parses the whole file once (cached in memory after); `Store.async_save()`
  rewrites the ENTIRE file every time (no append/incremental write). Risk is not CPU (orjson is
  fast) but I/O frequency + file size, especially on SD-card-based Raspberry Pi hardware.
- Rough capacity guidance (estimate, to be confirmed via benchmark — see Verification):
  - Up to ~5,000 records/type (~1-2MB JSON): negligible impact, safe default zone.
  - ~5,000-50,000 records/type (~2-20MB): noticeable but tolerable; write latency starts to matter
    if writes are frequent (e.g. automation-driven logging).
  - 50,000+ records/type (20MB+): risk zone — full-file writes can take hundreds of ms to seconds,
    startup load time grows, SD wear accumulates faster.
- Risk driver: `add_record` service is public, so automations may call it far more frequently than
  manual entry (the original motivating use case). Must protect against unbounded automation-driven
  growth without violating the "store forever unless retention specified" P0 requirement.
- Mitigations (added to plan, see Phase C/D/H updates below):
  1. One `Store` file PER RECORD TYPE (not one combined file for the whole integration) — isolates
     blast radius so a noisy type doesn't slow down/bloat unrelated types.
  2. Debounced/delayed saves (HA's standard delayed-save pattern, as used by entity_registry etc.)
     instead of a synchronous full-file write on every `add_record` call — coalesces bursts of
     writes into one write every N seconds; auto-flushes on clean HA shutdown.
  3. New optional per-record-type `max_records` setting (count-based cap), alongside existing
     `retention_days` (time-based) — OFF by default, so "store forever" remains the default.
  4. User notification via HA Repairs (`homeassistant.helpers.issue_registry.async_create_issue`),
     NOT plain persistent_notification — surfaces an actionable warning in Settings > System >
     Repairs when a type's record count crosses a configurable `warn_at` threshold (default 5,000);
     auto-resolved (`async_delete_issue`) once count drops back below threshold.
  5. Long-term: if genuinely unbounded high-volume logging is needed, JSON Store is structurally
     the wrong tool (no partial reads/writes) — that's when the already-flagged "revisit SQLite"
     decision would actually be exercised. The cap/warning is a mitigation, not a permanent fix.

## Status
- Plan fully drafted and approved for implementation.

---

# FULL PLAN (Custom Metrics Recorder — P0 HA Integration)

## Phase A: Repository & Dev Environment Scaffold
1. Create root-level project files (based on ludeeus/integration_blueprint template), adapted for
   this project:
   - `.devcontainer.json` — devcontainer config for VS Code Dev Containers (Docker), installs
     Python + Home Assistant core deps.
   - `config/configuration.yaml` — minimal local test HA instance config (default_config:, plus
     `logger: logs: custom_components.custom_metrics: debug` for verbose dev logging). This
     config/ directory is a disposable local test instance, never the user's real HA config.
   - `scripts/setup` — creates venv, installs `homeassistant`, `pytest-homeassistant-custom-component`,
     colorlog, ruff, etc. (requirements_dev.txt).
   - `scripts/develop` — symlinks/copies `custom_components/custom_metrics` into `config/custom_components/`
     and runs `hass -c config --debug` in the foreground.
   - `requirements_dev.txt` — homeassistant, pytest-homeassistant-custom-component, pytest-asyncio,
     ruff, freezegun (for retention purge tests).
   - `.gitignore` — exclude `config/.storage`, `config/home-assistant.log`, venv, node_modules, dist.
   - `README.md`, `LICENSE` (MIT recommended) — required by HACS.
   - `hacs.json` — `{"name": "Custom Metrics Recorder"}` (add `homeassistant` min-version key once
     dev HA version is known).
2. *Depends on 1.* Verify devcontainer builds and `scripts/develop` boots a bare HA instance at
   http://localhost:8123 with no integration yet — sanity check before writing integration code.

## Phase B: Core Integration Skeleton (domain: `custom_metrics`)
*Depends on Phase A.*
3. `custom_components/custom_metrics/manifest.json` — domain, name "Custom Metrics Recorder",
   version "0.1.0", config_flow: true, single_config_entry: true, integration_type: "hub",
   iot_class: "local_push" (no external device; instant local state changes), codeowners,
   documentation/issue_tracker placeholders (real GitHub URLs once repo exists).
4. `const.py` — DOMAIN, STORAGE_VERSION, STORAGE_KEY template, service names
   (`SERVICE_ADD_RECORD`), config/options keys, `FieldType` enum: `NUMBER, TEXT, LONG_TEXT,
   BOOLEAN, DATETIME, SINGLE_SELECT, MULTI_SELECT, IMAGE` (IMAGE implemented in Phase I below).
   Also define the reserved/compact record envelope key constants: `ATTR_ID = "id"`,
   `ATTR_TIMESTAMP = "t"`, `ATTR_DATA = "d"` (see "Storage key-naming decisions" above) — these are
   internal-only, never exposed to the user for renaming, and chosen short since they appear on
   every single stored record.
5. `models.py` — frozen dataclasses: `FieldDefinition` (key, label, type: FieldType, required,
   unit, default, options: list[str] | None for select types), `RecordType` (id, name, fields:
   list[FieldDefinition], timestamp_field default "timestamp", retention_days: int | None).
   Include `to_dict`/`from_dict` for JSON (de)serialization to/from the Store and config entry
   options. For `IMAGE` fields, the stored record value is a small reference object (e.g.
   `{"f": "<record_id>.<ext>"}` — short key per the storage key-naming decision), deliberately an
   object rather than a bare string so a future `"th"` (thumbnail) key can be added later without
   a breaking data-shape change.
6. `schema.py` — builds a `voluptuous.Schema` dynamically from a `RecordType` for validating
   incoming record data (used by services.py and websocket_api.py); raises
   `vol.Invalid`/`ServiceValidationError` with clear messages per field. `IMAGE` fields are
   special-cased: the *input* schema expects a filesystem path string (handled by Phase I's
   `media_store.py`), while the *stored* value is the reference object described above.
   User-defined field keys (e.g. "systolic") are used exactly as typed by the user — no internal
   renaming/translation layer (confirmed decision, see above).

## Phase C: Storage Layer
*Depends on Phase B (models.py).* Can run in parallel with Phase D (config/options flow).
7. `store.py` — `RecordStorage` class wrapping ONE `homeassistant.helpers.storage.Store` PER
   RECORD TYPE (version=STORAGE_VERSION, key=f"{DOMAIN}_{entry_id}_{record_type_id}") — isolates
   a noisy/high-volume type's I/O from unrelated types. Uses HA's standard delayed/debounced save
   pattern (coalesce bursts of `add_record` calls into one write every few seconds; flushes
   automatically on clean HA shutdown) instead of a synchronous full-file write per call.
   Responsibilities:
   - `async_load()` — load on entry setup (one Store per configured record type), cache each
     type's records in memory (`dict[record_type_id, list[record]]`), where each record uses the
     compact envelope `{"id": <uuid>, "t": <iso datetime>, "d": {<user field key>: value, ...}}`.
   - `async_add_record(record_type_id, data, timestamp=None)` — generates uuid id, stores
     `{"id": ..., "t": ..., "d": {...fields}}`, schedules a delayed save (not an immediate full
     write).
   - `async_list_records(record_type_id, start=None, end=None)` — filtered read from cache
     (filters on the `"t"` envelope key).
   - `async_delete_record(record_type_id, record_id)`.
   - `async_purge_expired(retention_by_type: dict[str, int | None])` — removes records older than
     `now - retention_days` per type (compares against `"t"`); called by the daily retention job.
   - `async_enforce_max_records(max_records_by_type: dict[str, int | None])` — if a type has an
     optional count-based cap configured, drop oldest records beyond the cap (opt-in, off by
     default — does not change the "store forever" default behavior).
   - `async_remove()` — thin wrapper deleting ALL of this entry's per-type Store files from disk
     (`Store.async_remove()` per type); used only by `async_remove_entry` (Phase H), never by
     unload/reload.
   - Record-count tracking used by Phase H's Repairs warning (see step 25 below).
8. Unit tests for `store.py` (pure, using a fake/in-memory Store or `hass` fixture) — add/list/
   delete/purge/max_records-eviction logic, including "retention_days = None / max_records = None
   means keep forever", debounced-save coalescing behavior, and that `async_remove()` deletes all
   per-type files for the entry.

## Phase D: Config Flow & Options Flow (UI-only, no YAML)
*Depends on Phase B.* Can run in parallel with Phase C.
9. `config_flow.py` — `ConfigFlow` (domain=DOMAIN): `async_step_user` shows a simple confirmation
   form (or immediate `async_create_entry`) since there's nothing to configure upfront; enforces
   single instance via `single_config_entry` manifest flag (HA handles the "already configured"
   abort automatically).
10. `OptionsFlowHandler` (same file or `options_flow.py`) — menu-driven wizard:
    - `async_step_init` — menu: "Add record type" / "Edit record type" / "Remove record type" /
      "Set retention policy".
    - `async_step_add_record_type` — name + default timestamp field name (overridable).
    - `async_step_add_field` — key, label, `FieldType` (SelectSelector), required (bool),
      unit (optional text), default (optional), options (comma-separated text shown only when
      type is single/multi-select) — loops via an "add another field?" checkbox back to itself,
      accumulating into `self._field_buffer`, until finished then persists the new `RecordType`
      into config entry options. Includes a translated hint/description under the field-key input
      suggesting shorter keys (e.g. "sys" instead of "systolic_pressure") for record types the
      user expects to log frequently via automations, since field keys are stored verbatim in
      every record (see "Storage key-naming decisions"). Validates the field key against a
      reserved-word disallow-list (`id`, `timestamp`, `record_type`) and rejects with a clear
      error if matched — see "Field-key collision analysis" above for why this is needed even
      though the internal storage envelope is nesting-safe.
    - `async_step_set_retention` — per record-type optional retention (days), None = forever, AND
      optional `max_records` count-based cap (None = unlimited), AND optional `warn_at` threshold
      (default 5,000) used for the Repairs warning in Phase H.
    - Update `hass.config_entries.async_update_entry(entry, options=new_options)` and trigger the
      entry reload so `RecordStorage`/services pick up new record types immediately.
11. `strings.json` + `translations/en.json` — all step titles/descriptions/field labels/error and
    abort messages for the flows above (required for HA UI to render text; also unlocks running
    `python3 -m script.translations develop` during dev — optional but recommended).
12. Tests for config_flow.py and options flow — full coverage of every step incl. the add-field
    loop, validation errors, and the reserved-word field-key rejection (`id`/`timestamp`/
    `record_type`) (mirrors HA's own requirement of 100% config_flow coverage).

## Phase E: Services (the P0 "save a new record" service)
*Depends on Phase C (store.py) and Phase B (schema.py).*
13. `services.py` — register in `async_setup` (module-level, hass-wide, not per entry):
    - `custom_metrics.add_record` — fields: `record_type` (required, string — resolved against
      configured RecordTypes at call time), `fields` (object — the arbitrary per-type data),
      `timestamp` (optional datetime override). Validates `fields` against the RecordType's
      dynamic schema (`schema.py`), raises `ServiceValidationError` on mismatch/unknown type,
      calls `RecordStorage.async_add_record`, returns the created record via
      `SupportsResponse.OPTIONAL`.
14. `services.yaml` — describes `add_record` for the Developer Tools "Actions" UI (fields incl.
    example values); `icons.json` — service icon (e.g. `mdi:notebook-plus`).
15. Tests: service registration exists even with zero entries; add_record happy path; validation
    error paths (missing required field, wrong type, unknown record_type).

## Phase F: WebSocket API (data exposure for the custom card)
*Depends on Phase C.* Parallel with Phase E.
16. `websocket_api.py` — register (in `async_setup`, guarded so it only registers once):
    - `custom_metrics/list_record_types` — returns configured RecordType defs (for the card to
      render dynamic forms/columns).
    - `custom_metrics/list_records` — params: record_type, optional start/end — returns records
      flattened to a friendly public shape `{"id": ..., "timestamp": ..., ...user fields}`
      (translated from the compact internal envelope `{"id","t","d"}` at this API boundary) —
      collision with user field keys is prevented by the reserved-word validation in Phase D.
    - `custom_metrics/add_record` / `custom_metrics/delete_record` — thin wrappers so the card can
      write without going through the service-call frontend dialog (nicer UX for a dedicated
      card), delegating to the same `store.py`/`schema.py` validation as the service.
17. Tests using the `hass_ws_client` pytest fixture: call each command, assert JSON result shape
    and error cases (unknown record_type, malformed record).

## Phase G: Custom Lovelace Card + Auto-Registration
*Depends on Phase F (WS API) for data contract.*
18. `frontend_src/` — small Lit + TypeScript project (package.json, esbuild config) implementing
    `<custom-metrics-card>`: `setConfig()`/`hass` setter, calls `hass.callWS(...)` against the
    commands from Phase F to list types/records, and `hass.callService("custom_metrics",
    "add_record", …)` (or the WS add_record command) to submit new entries. Build script bundles
    to a single file.
19. `custom_components/custom_metrics/www/custom-metrics-card.js` — build output committed (or
    built via a `scripts/build_frontend` step run before packaging a release).
20. `frontend.py` — in `async_setup_entry` (or `async_setup`), call
    `hass.http.async_register_static_paths([StaticPathConfig("/custom_metrics/custom-metrics-card.js",
    <path to www file>, cache_headers=False)])` then
    `homeassistant.components.frontend.add_extra_js_url(hass, "/custom_metrics/custom-metrics-card.js")`
    — registers the card automatically, no manual Lovelace "Resources" step for the user.
21. Manual verification: add a dashboard card of type `custom:custom-metrics-card` and confirm it
    renders without ever visiting Settings → Dashboards → Resources.

## Phase H: Retention Job & Lifecycle Wiring
*Depends on Phases C, D.*
22. `__init__.py` — `async_setup` (register global services + websocket commands once);
    `async_setup_entry` (load `RecordStorage`, read `RecordType`/retention config from entry
    options, schedule `async_track_time_interval(hass, purge_job, timedelta(hours=24))`, register
    frontend static path + add_extra_js_url, store per-entry runtime objects in
    `entry.runtime_data`); `async_unload_entry` (cancel the interval listener, clean up — return
    True, MUST NOT delete stored data — runs on every reload/restart teardown); optionally
    `async_reload_entry` (or rely on HA's default reload behavior).
23. `async_remove_entry(hass, entry)` — implement this HA-provided hook (called only when the
    user clicks "Delete" on the integration in the UI, distinct from unload/reload) to call
    `RecordStorage.async_remove()` and fully delete all of the entry's per-record-type Store files
    so no data lingers after uninstall. Config entry `data`/`options` (record type defs) are
    cleaned up automatically by HA's own core config entry storage — no action needed for those.
    Document in README: users should delete the integration via Settings (not just remove files
    via HACS) to trigger this cleanup; otherwise the Store file(s) become a harmless orphan on disk.
24. Tests: entry setup/unload round-trip (no leaked listeners, data file still present after
    unload/reload), purge job actually removes expired records when time is advanced
    (`async_fire_time_changed`), reload picks up updated options (new record type visible
    immediately), and `async_remove_entry` deletes the underlying Store file(s) while a plain
    unload/reload does not.
25. Repairs warning: as part of the same daily job (or right after each `add_record`, cheaply
    using the in-memory count), check each record type's count against its configured `warn_at`
    threshold (default 5,000). If exceeded, call
    `homeassistant.helpers.issue_registry.async_create_issue(hass, DOMAIN, issue_id=
    f"record_count_{record_type_id}", is_fixable=False, severity=IssueSeverity.WARNING,
    translation_key="record_count_high", translation_placeholders={...})` to surface a Repairs
    entry suggesting the user configure `retention_days` or `max_records`. Call
    `async_delete_issue` once the count drops back below threshold (e.g. after purge/eviction).
    Add corresponding `issues.json`/translation strings. Tests: issue created above threshold,
    cleared below threshold, not created when `warn_at` is disabled.

## Phase I: Image Field Type & Media Support
*Depends on Phase B (models/const), Phase C (store.py patterns), Phase E (services), Phase G
(card). Scoped per agreed simplifications: `media_source.py` approach, file-path handoff only (no
inline base64), no thumbnails in v1 (but data shape designed for adding them later), the service
caller is treated as trusted (no deep content/security validation, only basic sanity checks),
orphaned-media cleanup runs at startup AND after purge/delete (not startup-only).*
26. Add `IMAGE` to `FieldType` enum (const.py, already reflected above) and extend
    `FieldDefinition`/schema (models.py, schema.py, already reflected above) so an image field's
    stored value is the small reference object `{"f": "<record_id>.<ext>"}` — structured as
    an object specifically so a future `"th"` (thumbnail) key can be added without a breaking data-
    shape change (only a minor `STORAGE_VERSION` bump to backfill would be needed later, not a
    redesign).
27. `media_store.py` — new module managing image files on disk under
    `<config>/.storage/custom_metrics/<entry_id>/media/<record_type_id>/`:
    - `async_store_image(record_type_id, record_id, source_path)` — validates the source path
      exists/is readable (executor job), copies it into the managed directory
      (`shutil.copyfile`), preserving the extension; returns the stored filename reference. Only
      basic sanity checks (file exists, extension in an allow-list of common image types) — no
      Pillow/content verification/decompression-bomb defense, per the agreed trusted-caller scope.
    - `async_resolve_image_path(record_type_id, filename)` — returns the absolute path for serving.
    - `async_delete_image(record_type_id, filename)` — deletes a single file.
    - `async_cleanup_orphaned_media()` — walks the media directory tree, diffs against the set of
      filenames actually referenced by records in `store.py`, deletes any unreferenced file. Call
      this at `async_setup_entry` (startup safety net) AND immediately after the daily
      retention/purge job AND after record deletion/`async_remove_entry` — not startup-only, so
      disk space from purged records is reclaimed promptly instead of only at the next HA restart.
    - Designed with a single internal seam (`async_store_image`) so a future thumbnail-generation
      step (e.g. a Pillow resize) can be inserted there later without touching callers.
28. `services.py` update: `add_record` handling for `IMAGE`-type fields — the value supplied in
    the service call is treated as a filesystem path (not literal field data); the handler calls
    `media_store.async_store_image(...)` and substitutes the returned reference object into the
    stored record instead of the raw path.
29. `media_source.py` — implement `async_get_media_source` + a `MediaSource` subclass:
    - `async_browse_media` — simple two-level hierarchy: root → record types that contain an image
      field → individual records with images as leaf items (title = timestamp or a configured
      display field).
    - `async_resolve_media` — maps `<record_type_id>/<record_id>` to the stored file via
      `media_store.async_resolve_image_path`, returns `PlayMedia(path=Path(...), mime_type=...)`
      — relies on HA's shared local-file media serving/auth mechanism (the same one the built-in
      local "Media" integration uses for `/media` folder browsing) so no custom authenticated
      `HomeAssistantView`/signed-URL code needs to be written. **Spike/verify this assumption
      first** (small throwaway prototype) before building the rest of this phase around it, since
      it's the key complexity-reducing assumption for this feature.
30. Card update (`frontend_src/`): when a displayed record has an image field, resolve and render
    a single `<img>` (no gallery/thumbnail grid in v1) using the media-source URL for that record.
31. Tests: `media_store.py` (store/resolve/delete/orphan-cleanup, incl. "referenced files survive,
    unreferenced files removed"), `add_record` image-field handling (path → stored reference),
    `media_source.py` browse/resolve (using HA's media_source test helpers).

## Phase J: HACS Packaging & CI
*Depends on all above (including Phase I) being functionally complete.*
32. Brand assets: add integration icon (`icon.png`) per HACS "brand assets" requirement (linked
    doc: creating_integration_file_structure#brand-images---brand).
33. `.github/workflows/validate.yml` — run `home-assistant/actions/hassfest` and `hacs/action`
    (integration category) on push/PR to catch manifest/structure issues before release.
34. `.github/workflows/lint.yml` (optional but recommended) — `ruff format --check` + `ruff check`.
35. Cut the first GitHub release (tag matching `manifest.json` "version", e.g. `v0.1.0`) so HACS
    can resolve a version; write repo description + topics (`home-assistant`, `hacs-integration`,
    `custom-component`) in GitHub repo settings (manual, not a file).
36. Confirm manual-install-via-HACS-custom-repository steps are documented in README (owned by
    Phase K below) before/alongside the first release.

## Phase K: User Documentation
*Depends on all functional phases (B through I) being feature-complete; can run in parallel with
Phase J (packaging). Written for regular/non-technical users — deliberately excludes internal
implementation details (e.g. exact purge-job scheduling, orphan-cleanup timing/triggers).*
37. `README.md` — primary user-facing doc:
    - What it does (short pitch + the blood pressure / fuel cost / doorbell-photo examples from
      the original spec).
    - Installation (via HACS, and a manual-copy fallback).
    - Adding the integration purely via UI (Settings → Devices & Services → Add Integration) — no
      YAML involved at any point.
    - Defining your first record type via the integration's "Configure" options, step-by-step,
      with a concrete example (Blood Pressure: systolic/diastolic/pulse).
    - Adding records manually (Developer Tools → Actions → `custom_metrics.add_record` example).
    - **Automating record creation** — concrete, non-technical examples:
      1. A smart-scale sensor's state changes → automation calls `custom_metrics.add_record`,
         passing a `name` field (since a scale may be used by multiple people) plus the weight
         value from the trigger.
      2. A doorbell button press → `camera.snapshot` saves a photo → automation calls
         `custom_metrics.add_record` referencing that saved photo for an image field, plus a
         "number of people" field.
    - Retention & growth, in user-relevant terms only ("records are kept forever by default; you
      can optionally set a retention period or a maximum record count per type if you plan to log
      frequently via automations") — no internal purge-job/orphan-cleanup mechanics.
    - Viewing your data — adding the auto-registered `custom:custom-metrics-card` to a dashboard.
    - Uninstalling — delete via Settings to remove stored data cleanly.
    - Links out to `docs/automations.md` and `docs/card-development.md` for deeper detail, keeping
      the README itself scannable.
38. `docs/automations.md` — an expanded, still user-facing automation cookbook (more examples
    beyond the two in the README, e.g. logging a fuel fill-up from a fuel-price sensor, or a
    generic "any sensor changes → log a record" pattern) — no internal architecture details.
39. `docs/card-development.md` — dedicated reference for people building their OWN custom Lovelace
    cards against this integration's data (distinct from just using the bundled card):
    - The WebSocket commands available (`list_record_types`, `list_records`, `add_record`,
      `delete_record`) with example request/response JSON for each.
    - How to add a record from a card — either `hass.callService("custom_metrics", "add_record",
      …)` or the WS command directly — with a note on when to prefer each.
    - The record data shape (`id`, `timestamp`, plus per-type fields) and the record-type
      definition shape, so a card author can render dynamic forms/columns.
    - How an image-type field's value resolves to a displayable URL (media-source id → resolved
      URL) — pitched at "here's what you need to call," not the internal serving implementation.
40. Verification: have someone unfamiliar with the project follow the README from a fresh dev
    instance — install, configure a record type, add a record via the UI, add the card, and set
    up one of the documented example automations — confirm no step requires undocumented
    information.

## Relevant files
- `custom_components/custom_metrics/manifest.json` — integration metadata, config_flow flag.
- `custom_components/custom_metrics/const.py` — DOMAIN, FieldType enum, storage keys.
- `custom_components/custom_metrics/models.py` — RecordType/FieldDefinition dataclasses.
- `custom_components/custom_metrics/schema.py` — dynamic voluptuous schema builder.
- `custom_components/custom_metrics/store.py` — RecordStorage (Store-backed persistence + purge).
- `custom_components/custom_metrics/config_flow.py` — ConfigFlow + OptionsFlow wizard.
- `custom_components/custom_metrics/services.py` / `services.yaml` — `add_record` service.
- `custom_components/custom_metrics/websocket_api.py` — custom WS commands for the card.
- `custom_components/custom_metrics/frontend.py` — static path + add_extra_js_url registration.
- `custom_components/custom_metrics/media_store.py` — image file storage/retrieval/orphan cleanup.
- `custom_components/custom_metrics/media_source.py` — browse/resolve images for the card/media
  browser (Phase I).
- `custom_components/custom_metrics/www/custom-metrics-card.js` — built card bundle.
- `custom_components/custom_metrics/__init__.py` — setup/unload/lifecycle wiring.
- `custom_components/custom_metrics/strings.json`, `translations/en.json`, `icons.json`,
  `issues.json` (Repairs warning translations for record-count threshold).
- `frontend_src/` — Lit/TypeScript source + build config for the card.
- `tests/components/custom_metrics/**` — pytest suite (config_flow, options_flow, store, services,
  websocket_api, init/lifecycle, media_store, media_source).
- `.devcontainer.json`, `config/configuration.yaml`, `scripts/setup`, `scripts/develop`,
  `requirements_dev.txt` — local dev/test HA instance tooling.
- `hacs.json`, `.github/workflows/validate.yml`, `README.md`, `LICENSE` — HACS publishing.
- `docs/automations.md`, `docs/card-development.md` — extended user/developer documentation
  (Phase K), linked from README.

## Verification
1. Devcontainer boots; `scripts/develop` starts HA at localhost:8123 pointed at the disposable
   `config/` instance (never the user's production HA).
2. Add the integration purely via UI: Settings → Devices & Services → Add Integration → search
   "Custom Metrics Recorder" → confirm — no `configuration.yaml` edits required at any point.
3. Via the integration's Options ("Configure"), add a "Blood Pressure" record type with fields
   systolic/diastolic/pulse (numbers) and a "Fuel Cost" type with liters/price_per_liter/paid
   (numbers) — confirm both appear without restart.
4. Developer Tools → Actions → call `custom_metrics.add_record` for each type; confirm response
   data (if requested) and that `config/.storage/custom_metrics_<entry_id>` contains the record.
5. Restart the dev HA instance; confirm records persisted (forever by default).
6. Set a short retention (e.g. 1 day) on a test record type, add an old-dated record via the
   service (`timestamp` override), advance time in a test or wait for the daily job, confirm
   purge removes it while other types are unaffected.
7. Add a Lovelace card `type: custom:custom-metrics-card` to a dashboard — confirm it renders
   without ever adding a Resource under Settings → Dashboards → Resources.
8. Reload the config entry (three-dot menu → Reload) after a Python code change — confirm new
   behavior takes effect without a full HA restart.
9. Run `pytest tests/ --cov=custom_components.custom_metrics --cov-report term-missing` — all
   tests green, coverage includes config_flow/options_flow at ~100%.
10. Run `ruff format --check .` and `ruff check .` — clean.
11. Run the `hacs/action` and `hassfest` validations locally or via the GitHub Actions workflow
    before tagging the first release.
12. Verify uninstall cleanup: delete the integration via Settings → confirm all of the entry's
    `.storage/custom_metrics_<entry_id>_<record_type_id>` files are removed from disk; separately,
    just reload/restart HA → confirm the same files are untouched (data survives normal lifecycle
    events).
13. Benchmark storage scalability assumptions: generate synthetic datasets of 1k / 10k / 50k /
    100k records for a single record type in the dev container and measure `async_load`/save
    timings and memory footprint, to confirm (or revise) the capacity guidance documented under
    "Storage scalability analysis" before shipping. Confirm the Repairs warning fires at the
    configured `warn_at` threshold and clears once resolved.
14. Add a record type with an `IMAGE` field, add a record via `add_record` pointing at a real file
    path; confirm the file is copied into the managed media directory, the JSON record only holds
    a reference (not bytes), the card renders the image, and deleting/purging that record removes
    the underlying file (verified via the orphan-cleanup routine, not just at restart).
15. Follow `README.md` end-to-end from a fresh dev instance (install → configure a record type →
    add a record via the UI → add the card → set up one documented example automation) — confirm
    no step requires information missing from the docs.

## Decisions
- Domain: `custom_metrics`. Name: "Custom Metrics Recorder". `single_config_entry: true` (one
  instance holds all record types/data — this is a personal data store, not a multi-device hub).
- Storage: `homeassistant.helpers.storage.Store` (JSON), not SQLite/external DB — sufficient for
  personal-scale data volumes; revisit only if usage patterns prove otherwise.
- Record-type/field schema defined exclusively through UI (Options flow wizard) — no YAML config
  ever required, per explicit user requirement and current HA direction.
- v1 field data types: number, text, long_text, boolean, datetime, single_select, multi_select
  (+ image, Phase I).
- `add_record` service is the primary P0 write path; WebSocket commands additionally expose the
  same capability for the card's own UI ergonomics (both funnel through the same store/schema
  validation, avoiding duplicated logic).
- Custom card is auto-registered via `add_extra_js_url` + static path registration — no manual
  Lovelace resource step for the end user.
- P1 (field relations, standalone browse/edit page) explicitly excluded from this plan; only a
  clean extension point (panel/static-file registration pattern already in place via
  `frontend.py`) is left for a future plan.
- Dev/test instance: isolated VS Code Dev Container running a disposable HA config — never the
  user's production instance — matching the `ludeeus/integration_blueprint` community-standard
  workflow.
- iot_class: `local_push` (no external device; state changes are immediate/local).
- Storage architecture: ONE `Store` file PER RECORD TYPE (not one combined file) to isolate a
  high-volume/automation-driven type's I/O impact from unrelated low-volume types. Writes are
  debounced/delayed (coalesced), not synchronous-per-call, to reduce disk I/O frequency on
  constrained hardware (e.g. Raspberry Pi with SD card storage).
- Added optional per-record-type `max_records` (count-based cap, off by default) alongside
  `retention_days` (time-based, off by default) — both opt-in so "store forever" remains the
  default per P0 spec. A configurable `warn_at` threshold (default 5,000 records) triggers a
  Home Assistant Repairs issue (via `issue_registry`) suggesting the user configure retention/cap;
  auto-clears once resolved. Rough soft-safe zone: <5,000 records/type; caution 5k-50k; risk 50k+
  (to be confirmed by the benchmarking verification step before release).
- Data is stored exclusively via our own `Store` file, never as HA entity states — therefore the
  `recorder` integration's purge policy (default `purge_keep_days: 10`) has zero effect on our
  records; retention is governed solely by our own per-record-type setting. Nothing is ever
  written to `recorder`'s database (`home-assistant_v2.db`), so there is no "main DB" cleanup
  concern by construction.
- On uninstall: implement `async_remove_entry` (Phase H, step 23) to delete our `Store` file via
  `Store.async_remove()` — this only fires on explicit "Delete" in the UI, never on unload/reload,
  so data survives normal restarts/reloads but is fully purged on deliberate removal. Config entry
  data/options are cleaned up automatically by HA itself. README documents that users should
  delete the integration via Settings (not just remove files via HACS) to trigger this cleanup.
- Image field type (v1, Phase I): serve via `media_source.py` returning `PlayMedia(path=...)`,
  relying on HA's shared local-file media serving/auth mechanism instead of a custom authenticated
  HTTP view (to be confirmed via an early spike). Populate via file-path handoff only (automation
  saves a file to disk first, e.g. via `camera.snapshot`, then our service copies it in) — no
  inline base64 support in v1. No thumbnails in v1; the record's image value is stored as an
  object (`{"f": ...}`) specifically so a `"th"` (thumbnail) key can be added later without a
  breaking data-shape change. The service caller is treated as trusted (only basic existence/
  extension sanity checks, no deep image-content validation or decompression-bomb defense).
  Orphaned media cleanup runs at HA startup AND right after the daily retention/purge job AND
  after record/entry deletion — not startup-only — so disk space is reclaimed promptly rather
  than only at the next restart.
- Documentation is a first-class deliverable (Phase K), not an afterthought: `README.md` covers
  installation/setup/manual usage/automation examples/retention in plain, non-technical language;
  `docs/automations.md` extends the automation cookbook; `docs/card-development.md` documents the
  WebSocket/service API contract specifically for third-party custom-card authors.
- JSON storage key naming (follow-up question): `Store` performs no compression (verified against
  HA core source) so shorter keys directly reduce on-disk file size; orjson's process-wide key
  cache (keys ≤64 bytes, 2048-entry cache) means shortening keys has little effect on in-memory
  RAM (key strings are already de-duplicated across records). Reserved envelope keys we control
  (`id`, `t` for timestamp, `d` for the field-data wrapper, `f` for the image reference) are made
  short since they appear on every record at zero UX cost. User-defined field keys are kept
  exactly as typed (no internal translation layer) — simplicity/automation-friendliness preferred
  over the smaller additional disk savings a translation layer would provide; a UI hint in the
  Options flow nudges users toward shorter keys for high-volume record types instead.
- Field-key collision (follow-up question): the internal nested envelope prevents any collision in
  the stored JSON, but the public API is deliberately flat/friendly (`{"id","timestamp",...fields}`)
  for card/automation ergonomics, which reintroduces collision risk. Fixed by reserving `id`,
  `timestamp`, and `record_type` as forbidden field-key names, validated once at field-creation
  time in the Options flow (Phase D step 10) rather than needing guards scattered through every
  read/response path.

## Repository setup note
- The plan's file/tooling conventions (Phase A) are modeled on the `ludeeus/integration_blueprint`
  GitHub template. Using GitHub's "Use this template" feature on that repo is optional — it gives
  a "generated from" lineage badge but is not required; the same files can simply be authored
  directly in this repository following the same conventions. Creating a repo from a template
  requires a GitHub-account-level action (web UI "Use this template" button, or
  `gh repo create --template ludeeus/integration_blueprint`) and cannot be performed automatically
  on the user's behalf from this environment.

## Suggested GitHub repo description
> Home Assistant custom integration for recording user-defined personal metrics (health readings,
> fuel costs, and more) — fully configurable via the UI, with a custom Lovelace card.
