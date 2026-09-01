# Plan: Custom Metrics Recorder (HA Integration)

## Implementation status (read this first)
- **Phases A-K below are DONE and shipped** (v1, implemented and committed through `4be2bac` on
  `main`) — this includes the full integration (storage, config/options flow, services, WebSocket
  API, the auto-registered Lovelace card, media/image support, Repairs warnings, README). Everything
  from "## Context" through "## Suggested GitHub repo description" is kept purely as a **historical
  record** (why decisions were made, what was verified via spikes, approved deviations from the
  original idea like the vanilla-JS card instead of Lit+TypeScript) — treat it as background, not
  as a to-do list. Two small items from that original scope (a brand icon, cutting the first GitHub
  release) were considered and explicitly **dropped as not needed** (decision 2026-08-27, see
  Phase L, P0-1) — not outstanding work, don't revisit unless priorities change.
- **Only "Phase L: Requested Enhancements — Investigation" (at the very end of this file) reflects
  current/future work.** It's investigation-only so far except where noted below — that's the
  section to act on next, not anything above it. **Implemented so far: P0-2 (card refactoring,
  2026-08-28: HTML-escaping fix, server-capped/configurable `last` row limit (count or duration
  like `2w`), `show_form`/`show_list`/`show_delete` config switches, CSS Grid form layout), P0-3
  (rename record type/field name+label freely; advanced key/id rename with data migration + a
  required confirmation checkbox; the key is now shown throughout the flow, 2026-08-28), and P0-4
  (record types are now Config Subentries, so each shows up as its own manageable row directly on
  the integration's card, with a one-time migration from the old options-based storage,
  2026-08-28), P1-2 (card visual config editor — `ha-form`-based, was already implemented in
  code but this doc had gone stale marking it "deferred"; corrected 2026-08-28, not outstanding
  work), P0-7 (live card refresh via a `custom_metrics_updated` HA bus event fired on every
  record/record-type mutation, so an already-open card updates without a manual reload -
  implemented and verified live 2026-08-28), and P0-6 (CSV export/import per record type via the
  Configure menu's "Export data"/"Import data" actions plus `export_records`/`import_records`
  services, 2026-08-29 - see Phase L's P0-6 section for the full design, including the
  full-backup-vs-data-only `include_id` choice added on top of the original plan), and P0-9
  (default record filter via a card `filter` config - a YAML list of single-key `{field: value}`
  conditions, AND-combined, server-side in the WS API/store; went through several design
  revisions - see Phase L's P0-9 section - ended up simpler than first planned: no add-record
  pre-fill, no natural-text grammar, just `==`/`!=`/`>`/`>=`/`<`/`<=` operators, 2026-08-29), and
  P0-10 (card `columns` config - a YAML allow-list + order of field keys restricting/reordering
  the table's columns only, add-record form unaffected; the visual config editor got a custom
  hand-rolled "Visible columns"/"Available fields" picker with up/down/add/remove controls rather
  than a plain text field or an unverified `ha-form` reorderable multi-select, per explicit user
  request, 2026-09-01).**
  P0-1 (brand icon/release) was dropped. **P0-5 (aggregation API — sum/avg/min/max/count of a
  numeric field grouped into day/week/month buckets, plus a SQLite-migration investigation) and
  P0-8 (move the "add record" form into an `<ha-dialog>` popup, triggered by a new "+ Add record"
  button, with `show_form` renamed to `show_add_record`) are
  PLANNED but NOT YET IMPLEMENTED (2026-08-28)** — see Phase L below for the full plans; those are
  the next things to implement. Only the P1 items remain purely
  investigation-only.

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

---

# Phase L: Requested Enhancements — Investigation (2026-08-27)

**Status: investigation only — NONE of the items below have been implemented.** Each item lists
the current state (grounded in the actual code as of commit `4be2bac`), the implementation options
considered, and a recommendation, so a future session can pick this up and start implementing
directly instead of re-researching. Ordered as given by the user: P0 items first, P1 (deferred)
after.

## P0-1: Brand icon and first GitHub release — DROPPED
- Decision (2026-08-27): not pursuing either of these. Both were investigated (brand icon would
  have required a PR against the external `home-assistant/brands` repo to show up in the actual HA
  UI; the GitHub release was needed only for HACS version resolution) but judged not worth it right
  now. Not tracked as outstanding work — revisit only if that changes.

## P0-2: Card refactoring — IMPLEMENTED (2026-08-28)
All sub-items live in `www/custom-metrics-card.js` (no build step, still vanilla JS/CSS — none of
the options below required introducing a build pipeline or `frontend_src/`). Implemented per the
recommendations below: `escapeHtml()` applied at every interpolation site (2.1); `list_records`
WS command now accepts an optional `limit` and always applies a server-side
`MAX_LIST_RECORDS_LIMIT` (500) cap; the card's own config key is `last` (not a plain `limit` — a
follow-up refinement, see 2.2), accepting either a count (default 20) or a duration shorthand like
`2w`/`3d`/`12h`/`30m` (2.2); `show_form`/`show_list`/`show_delete` card config switches, with a
`setConfig()`-time error if both `show_form` and `show_list` are `false` (2.3/2.4/2.5); form
switched to a two-column CSS Grid layout with the submit button right-aligned in its own
full-width row (2.6). Covered by new tests in `tests/test_store.py` and
`tests/test_websocket_api.py` (backend `limit`/`start` params, exercised together); verified live
(grid alignment, and the `last`/`show_form`/`show_delete` switches, incl. both count and duration
forms of `last`) via browser automation. The default for `show_delete` was kept as `true`
(backward compatible) since the open product question in this section wasn't resolved either way.

### P0-2.1: Unescaped text (XSS risk)
- Current state: `_render()` builds markup via template-literal strings assigned to
  `shadowRoot.innerHTML`, interpolating `title`, every `field.label` (table headers + form labels),
  `_formatValue()`'s return value (raw record data), `this._error`, and the image `alt` attribute —
  **none of it HTML-escaped**.
- Why this matters here specifically: field **values** (`text`/`long_text` fields, and error
  messages that can echo back submitted values) are controlled by anyone who can call
  `custom_metrics.add_record` — which includes non-admin automations and, in principle, a
  compromised/misbehaving automation or voice-assistant intent, not just the dashboard-editing
  admin who defines field labels. The card runs in the authenticated viewer's own HA frontend
  session, so an unescaped `<img src=x onerror=...>` in a stored text value is a genuine stored-XSS
  vector against whoever views that dashboard, not just a cosmetic glitch.
- Options:
  1. Add one small `escapeHtml(value)` helper (standard `& < > " '` replace chain) and apply it at
     every interpolation site listed above. Smallest diff, keeps the existing innerHTML-string
     rendering architecture, fully closes the gap if applied consistently.
  2. Rewrite rendering to build DOM nodes via `createElement`/`.textContent` instead of innerHTML
     strings — eliminates the bug class structurally but is a much larger diff touching every
     render path.
  3. A small tagged-template `html` helper that auto-escapes interpolated values (lit-html-style) —
     a middle ground, but amounts to hand-rolling templating machinery for a card whose whole
     premise is "no dependencies."
- Recommendation: (1) — minimal, safe, on-brand for the project's simplicity goal. Needs to be
  applied to: card `title`, every `field.label` usage, `_formatValue()`'s output, `this._error`,
  and the image `alt` text.

### P0-2.2: Default row limit, configurable via card config — IMPLEMENTED (2026-08-28)
- Current state: `custom_metrics/list_records` (websocket_api.py) only accepts optional
  `start`/`end` datetime filters — no `limit`/sort param. The card fetches *every* record for the
  configured type, sorts client-side by timestamp descending, and renders all of them.
- Options:
  1. Client-side only: card config gets a `limit` (default e.g. 20), card slices `this._records`
     after sorting. Zero backend change, but doesn't reduce WS payload size or backend work as a
     type's record count grows — doesn't help the storage-scalability concern already documented
     above.
  2. Server-side: add `vol.Optional("limit"): int` to the `list_records` WS schema; do the
     sort-desc + slice in `store.py`'s `async_list_records` (or the WS handler) before the response
     is serialized, so only the requested page is ever sent. Card passes its configured limit.
  3. Combine both: server enforces a hard sanity cap (e.g. 500) regardless of what's requested,
     while the card's own (smaller, UI-focused) `limit` config is what's actually sent.
- Naming: don't reuse `max_records` (that's the existing hard *storage* cap concept from Phase H) —
  use a distinct name for this *display/query* concept, e.g. card config `limit` and WS param
  `limit`.
- Recommendation: (3) — most scalable, keeps payload/response size bounded regardless of how large
  a record type grows over time. True pagination/"load more" is a separate, bigger feature —
  explicitly out of scope here, worth flagging as a natural future follow-up.
- **Follow-up refinement (implemented instead of a plain `limit`):** the card config key is `last`,
  not `limit`, and accepts either a plain count (e.g. `last: 20`, same behavior as the `limit`
  design above) **or** a duration shorthand string — `30m`/`12h`/`3d`/`2w` (minutes/hours/days/
  weeks) — meaning "show everything from that far back." Implemented entirely client-side with no
  further backend changes needed: a duration value is converted to a `start` timestamp
  (`new Date(Date.now() - ms).toISOString()`) and sent via the *already-existing* `start` WS param,
  while a count value is sent via `limit` exactly as before; the server's unconditional
  `MAX_LIST_RECORDS_LIMIT` (500) default still applies as a safety net in the duration case too,
  since the WS handler always falls back to that cap when no explicit `limit` is given. Backend
  (`websocket_api.py`/`store.py`) required zero changes for this — `start` and `limit` already
  composed correctly together.

### P0-2.3: Config switch to disable "add record" (the form)
- New boolean card config, e.g. `show_form` (default `true`). When `false`, skip building/
  appending the `<form id="add-form">` block in `_render()`. Small, isolated change.

### P0-2.4: Config switch to disable the records list/table
- Same pattern, e.g. `show_list` (default `true`) — skip the `<table>` block when `false`.
- If both `show_form` and `show_list` are `false`, the card would render an empty `<ha-card>` shell
  with nothing useful in it — worth a `setConfig()`-time validation warning/error for that specific
  combination rather than silently rendering nothing.

### P0-2.5: Config switch for delete buttons
- New boolean card config, e.g. `show_delete` (default `true`, to preserve current behavior).
  Wraps the `<button class="delete-btn">` cell in a conditional.
- Open product question (not resolved here, no strong preference given): should the default stay
  `true` (backward compatible) or default to `false` (safer for dashboards visible to non-admin
  household members who could otherwise accidentally delete history)? Flagging for a decision
  before implementation. Also interacts with the P1 multi-user item below (see P1-1) — if per-
  record ownership is ever added, this could become "show delete only for records I own" rather
  than a single global on/off.

### P0-2.6: Add-record form layout (label/input alignment, submit button placement)
- Current state: `form { display: flex; flex-wrap: wrap; gap: 8px; align-items: end; }` with each
  `.field` an independent flex column whose `<label>` wraps both the label text and the input
  together — this is why labels/inputs don't currently line up into neat columns (each field's
  size depends on its own label-text length, and fields wrap unpredictably across rows depending on
  count/label length).
- Options:
  1. **CSS Grid, two columns**: `form { display: grid; grid-template-columns: auto 1fr; column-gap:
     8px; row-gap: 8px; align-items: center; }`, with each field's label text and its input as
     separate grid cells (label associated to input via `for`/`id`) — gives clean, consistent
     column alignment regardless of label length/localized text length. The submit button spans
     both columns on its own row, right-aligned (`grid-column: 1 / -1; justify-self: end;`).
  2. Keep flex, but fix a minimum label width (e.g. `label { min-width: 120px; }`) with one field
     per row — simpler CSS but can look awkward with very short or very long label text, less
     flexible for localization.
  3. `<table>`-based form layout — generally discouraged for form semantics today, and visually
     confusing alongside the card's own real data table.
- Recommendation: (1), CSS Grid — cleanest, handles varying label lengths gracefully, stays
  dependency-free, and naturally supports "submit button pinned bottom-right." User noted openness
  to alternative suggestions with no strong preference — this is a starting proposal, not a final
  decision, to sanity-check before implementing.

## P0-3: Editable record type name/id and field label/key, with the "key" visible — IMPLEMENTED (2026-08-28)
Implemented as designed below: (1) the key/id is now shown throughout the subentry flow (the
reconfigure menu's title is `{name} ({key})`, and field pickers show `Label (key)`); (2)
`edit_field_label` changes only the display text, zero cascading impact (record type *renaming*
ended up NOT needing a dedicated step at all - see the UX revision note below, it's handled by the
subentry list's own built-in Rename action instead); (3) `change_type_key`/`change_field_key`
(option B - allowed, with migration) are implemented: `store.py` gained
`async_rename_field_key`/`async_rename_record_type`, `media_store.py` gained
`async_rename_record_type` (moves the media directory), and both advanced steps require a
`confirm` checkbox before proceeding, with a description explaining the automation/
dashboard-breakage risk that can't be mitigated automatically. Covered by new tests in
`tests/test_config_flow.py` (including a test that verifies stored record data/`d` dict keys are
actually migrated, and that the Store file moves to the new id). See P0-4 below - these steps now
live in a `ConfigSubentryFlow` rather than the old `OptionsFlow`, since P0-4 was implemented
first/underneath this.

### UX revision (2026-08-28, user feedback after initial implementation)
The first pass put every action (including a custom "rename record type" step) into one flat
reconfigure menu, and had no way to delete a field. Feedback + a look at how HA's subentry UI
actually behaves led to this restructure:
- **Confirmed infeasible**: adding custom entries to a subentry's built-in "⋮" menu (Rename/
  Delete) - that menu is generic HA frontend UI shared by every integration using subentries, with
  no per-integration extension point (verified by reading `homeassistant/components/config/
  config_entries.py`'s `config_subentry_update`/`config_subentry_delete` WS commands - fully
  generic, keyed only by `subentry_id`, nothing integration-specific). Only the "Configure" gear
  button is customizable (it launches our own flow).
- **Dropped the custom "rename" step entirely** - the subentry list's built-in Rename action
  already edits `subentry.title` directly (`config_entries/subentries/update`), which is exactly
  the same data our own `rename` step touched - fully redundant.
- **Field editing consolidated**: reconfigure menu is now `manage_fields` / `reconfigure_add_field`
  / `set_retention` / `change_type_key` (4 items, was 6). `manage_fields` is a field picker
  (dropdown, "Label (key)") leading to a per-field menu: `edit_field_label` / `change_field_key` /
  `delete_field` (new). HA's menu step has no support for section headers/grouping (confirmed - no
  such feature in the framework), so this two-level "pick field, then act on it" structure is how
  the flat-menu limitation was worked around, rather than one long undifferentiated list.
- **Field deletion added** (`async_step_delete_field`): same `confirm` checkbox pattern as the key
  changes, since removing a field's definition means any automation/card still submitting that key
  will start failing validation (voluptuous's default `PREVENT_EXTRA` rejects unknown keys) - the
  same unmitigable-by-us risk class as a key rename. Existing stored data isn't touched/deleted.
- Verified live via direct REST calls to `/api/config/config_entries/subentries/flow` (the same
  endpoint the frontend itself uses): reconfigure menu correctly omits "rename", field picker
  correctly shows keys, per-field menu shows all 3 actions, and a live field deletion round-tripped
  correctly (verified removed from `custom_metrics/list_record_types`).
- Current state (confirmed in `config_flow.py`): a record type's `id` is derived from its `name`
  via `slugify(name, separator="_")` **only once, at creation time**
  (`async_step_add_record_type`); there is currently **no rename capability at all** — the
  existing "edit_record_type" step actually means "go add more fields to this type," not "rename
  it." Field `key` is likewise fixed at field-creation time (checked only for duplicates/reserved
  words). Once past the initial creation form, the user only ever sees the friendly `name`/`label`
  in the options-flow UI — the underlying `id`/`key` (what actually appears in `add_record` calls
  and automations) is invisible, matching the user's complaint.
- Sub-items:
  1. **Surface the key** (read-only) alongside the editable `name`/`label` wherever shown in the
     options flow — e.g. render record-type/field picker list entries as "Blood Pressure
     (`blood_pressure`)", and show the key as a disabled/read-only field on a future "edit" form.
     Trivial UI-only addition, no data-model change.
  2. **Allow editing `name`/`label` without touching `id`/`key`** — safe, zero cascading impact:
     `id`/`key` is what's actually stored in every record's `d` dict and referenced by automations;
     `name`/`label` is purely cosmetic display text living only in the `RecordType`/
     `FieldDefinition` config (`entry.options`). Needs new options-flow steps (e.g.
     `async_step_rename_record_type`, an analogous "edit field label" step). Low risk — recommend
     doing this regardless of the decision on (3).
  3. **Allow changing the `id`/`key` itself** — the harder item, and the user explicitly flagged
     uncertainty about whether it's even needed. Consequences if allowed:
     - Every existing record's compact envelope `d.<old_key>` must be renamed to `d.<new_key>`
       across *all* stored records for that type — a real data migration (load, rewrite, re-save
       the type's Store file), not just an options update.
     - If the *record type* `id` changes: the per-type Store file's key
       (`f"{DOMAIN}_{entry_id}_{record_type_id}"`) would need renaming too, and so would the image
       media directory (`.../media/<record_type_id>/...`) for any image fields.
     - **Automations referencing the old key by name are invisible to us and cannot be migrated
       automatically** — a user's existing `fields: {<old_key>: ...}` YAML (or a dashboard's
       `record_type: <old_id>` card config) would silently break after a rename. This is the one
       genuinely un-mitigable risk, entirely outside the integration's visibility/control.
     - Options: (A) disallow key/id renaming entirely (name/label-only, per item 2) — simplest,
       matches how most HA integrations treat internal identifiers (immutable once created); (B)
       allow it, perform the record-data + Store-file + media-directory migration ourselves (fully
       within our control to do correctly), and require an explicit "I understand this may break
       existing automations/dashboards referencing the old key" confirmation before proceeding.
  - Recommendation: implement (2) unconditionally (no real downside). For (3), lean towards (B) —
    the user did ask for it, and the migration itself is tractable; the confirmation step is there
    specifically to cover the one risk we can't fix for the user (their own automation/dashboard
    configs).

## P0-4: Show configured record types on the integration's own page (not just inside "Configure") — IMPLEMENTED (2026-08-28)
Implemented using Config Subentries exactly as designed below. Each record type is now a
`ConfigSubentry` (`subentry_type="record_type"`, `unique_id`=our record type id/key,
`title`=display name, `data`=the rest of `RecordType.to_subentry_data()`). `config_flow.py`'s
`CustomMetricsOptionsFlow` was removed entirely and replaced by `RecordTypeSubentryFlow`
(`async_step_user` for adding, `async_step_reconfigure` menu for everything else - rename, add
fields, edit a field's label, the P0-3 advanced key-change steps, and retention). A one-time
migration (`_async_migrate_legacy_options` in `__init__.py`) converts any pre-existing
options-based record types into subentries on first setup after upgrade, then clears them from
options - verified live against this repo's own real dev config entry (4 pre-existing record
types migrated correctly with zero data loss, confirmed via `.storage/core.config_entries`
inspection) and confirmed visually that all 4 now show as individual rows with their own
Configure/Rename/Delete actions on the integration's card in Settings → Devices & Services, with
no separate "Configure" dialog needed for the entry itself. `store.py`'s per-record-type Store
files were NOT touched by the migration (only the record type *definition* moved) - matches the
original design note.
- Confirmed via Home Assistant developer docs (`config_entries_config_flow_handler`,
  "Subentry flows" section): **Config Subentries** (`ConfigSubentry`,
  `ConfigSubentryFlow`/`async_get_supported_subentry_types`) is the current, supported HA mechanism
  for exactly this. The docs' own worked example — a weather integration storing each configured
  "location" as a subentry of one hub config entry — is structurally identical to "each record type
  is a sub-item of one Custom Metrics Recorder config entry." Subentries appear as visible sub-rows
  directly on the integration's card in Settings → Devices & Services, without opening "Configure,"
  which is exactly what was asked for.
- What adopting this would actually require (a real refactor, not a small tweak):
  1. Move `RecordType` storage from today's single blob in `entry.options["record_types"]` to one
     `ConfigSubentry` per record type (via `hass.config_entries.async_add_subentry` /
     `async_update_subentry` / `async_remove_subentry`), each subentry's own `data` holding that
     type's field defs + retention settings.
  2. Replace/restructure the current single `OptionsFlowHandler` wizard with a
     `ConfigSubentryFlow` (`async_get_supported_subentry_types` returning e.g.
     `{"record_type": RecordTypeSubentryFlowHandler}`), following the add/reconfigure/remove
     pattern subentries use instead of the current menu-based options flow.
  3. **Data migration** (the highest-risk part): every existing install has its record types in
     `entry.options` today. A one-time migration — likely inside `async_setup_entry`, or via a
     config-entry version bump + `async_migrate_entry` — would need to convert each existing
     options-based `RecordType` into an equivalent subentry exactly once, before the new
     subentry-based read/write path takes over.
  4. `store.py`'s per-record-type Store file keys (already keyed by `record_type_id`) would *not*
     need to change — only the record type *definition's* storage location moves, not the actual
     record data files.
  5. Translations: subentry flows use a separate `config_subentries` key in
     `strings.json`/`translations/en.json` (distinct from the existing `config`/`options` keys) —
     needs its own new set of strings.
- Recommendation: feasible, and subentries are confirmed to be the right/current-supported
  mechanism for the request — but this is a substantial architectural change (a new flow type +
  a real one-time data migration for every existing installation), not a small UI tweak. Should be
  scoped as its own dedicated sub-phase with its own migration test plan, rather than bundled in
  casually alongside the smaller card/icon items above.

## P0-5: Aggregation API (backend, no SQLite) — PLANNED, not yet implemented (2026-08-28)

### SQLite migration investigation (requested alongside the aggregation feature)
- User asked whether migrating storage to SQLite makes sense, specifically to support new
  aggregation-style API functions (e.g. "give card developers the sum of a field over time,
  weekly sums, etc.").
- Current state (re-confirmed by reading the actual code): `store.py`'s `RecordStorage` keeps one
  `homeassistant.helpers.storage.Store` (JSON) file per record type, fully loaded into an
  in-memory `dict[record_type_id, list[dict]]` on setup. All reads/filters/purges
  (`async_list_records`, `async_purge_expired`, etc.) are synchronous Python loops over that
  in-memory list, not I/O-bound at read time despite the `async_` naming convention. Writes are
  debounced (`SAVE_DELAY` = 10s) and rewrite the whole per-type file. Scale is already bounded by
  `retention_days`, `max_records`, and the `warn_at` Repairs-warning default of 5,000
  records/type (see the original "Storage scalability analysis" section above, which already
  considered and rejected SQLite once, for the same personal-scale reasons).
- Recommendation: **do not migrate to SQLite.** Reasons:
  1. No performance problem exists to solve at this record-count scale (personal metrics, not
     recorder-style high-frequency sensor logging) — Python-side bucketing/aggregation over a few
     thousand records is sub-millisecond.
  2. Record fields are fully dynamic (users add/rename/remove fields at runtime via the P0-3
     subentry reconfigure flow) — a real relational schema per record type would need live
     `ALTER TABLE` migrations on every field change. The only way to avoid that is a JSON-blob
     column + `json_extract()`, which gives up most of SQL's actual benefit while still adding all
     of SQLite's operational complexity (executor-thread offloading since `sqlite3` is
     synchronous, connection/locking handling, a new migration path from existing `Store` files,
     and care needed to keep the new file covered by HA's config backup, e.g. under `.storage/`).
  3. It would be a wide, risky refactor: every current in-memory-list consumer (`record_view.py`,
     `media_source.py`, `services.py`, `websocket_api.py`, the retention/purge/max_records logic in
     `store.py` itself) would need touching, plus rewriting ~9 test files that assume
     list-of-dicts, for a benefit (query performance/flexibility) not actually needed here.
  4. Aggregation itself does not need SQLite: since all of a type's records are already resident
     in memory, bucketing by day/week/month + sum/avg/min/max/count is a simple groupby-style
     Python loop, no different in complexity or real-world performance from doing it in SQL at
     this scale.
  5. Would only reconsider SQLite if the record-count caps were removed entirely to support
     genuinely large datasets (e.g. importing years of granular data) — a separate, larger
     discussion from the aggregation feature, not needed to ship it.
- User agreed: keep current storage; add aggregation in Python on top of it.

### Aggregation API design (confirmed with user via `vscode_askQuestions`)
- Functions: `sum`, `avg`, `min`, `max`, `count`.
- Buckets: `day`, `week`, `month` (explicitly **not** `hour`, per user's free-text refinement of
  the options).
- Surface: a **new WebSocket command only** (`custom_metrics/aggregate_records`) — no matching
  service, so automations/templates don't get this, only the frontend/cards via
  `hass.connection.sendMessagePromise`.
- Field scope: `sum`/`avg`/`min`/`max` require a numeric (`FieldType.NUMBER`) field; `count` is
  field-less (counts all records in a bucket regardless of any field).
- Card charting (actually rendering a graph from this new endpoint in
  `custom-metrics-card.js`) is explicitly **out of scope for now** — backend API only, left to
  card developers / a future follow-up task.

### Implementation steps
1. `const.py` — add `ATTR_OP = "op"`, `ATTR_BUCKET = "bucket"`, `ATTR_FIELD = "field"` (singular,
   distinct from the existing plural `ATTR_FIELDS` used by `add_record`'s fields dict). Add
   `AGGREGATE_OPERATIONS = ("sum", "avg", "min", "max", "count")` and
   `AGGREGATE_BUCKETS = ("day", "week", "month")` tuples for reuse in the WS schema and tests.
2. `store.py` (*depends on 1*) — add:
   - A module-level pure helper `_bucket_start(ts: datetime, bucket: str) -> datetime`: converts
     `ts` to local time via `homeassistant.util.dt.as_local`, then truncates to the bucket's
     calendar boundary in local time (day: local midnight; week: local midnight of the Monday of
     that week, via `date() - timedelta(days=weekday())`; month: local midnight on the 1st).
     Returns a local-tz-aware `datetime` (so DST-correct) — callers `.isoformat()` it for the
     response. Kept standalone (not a method) so it's directly unit-testable without a
     `RecordStorage` instance.
   - `RecordStorage.aggregate_records(self, record_type_id, field, op, bucket, start=None,
     end=None) -> list[dict]`: reuses the existing time-range filter logic (factor the `start`/
     `end` filtering loop already inside `async_list_records` into a small shared private helper,
     `_filter_by_range`, called from both methods, to avoid duplicating it) to get the candidate
     records, groups them by `_bucket_start(...)`, then per bucket:
     - `op == "count"`: value = number of records in the bucket (ignores `field` entirely).
     - `op in (sum, avg, min, max)`: only records where `field` is present in `d` AND is a
       `float`/`int` are included; value computed over those; if zero qualifying records exist in
       an otherwise-nonempty bucket, `value` is `None` (bucket is still included since it did have
       records, relevant for `count`).
     Buckets are emitted **sparse** (only buckets containing at least one record in range at all)
     — no synthetic zero-filled buckets for gaps. This is a default choice, **not yet explicitly
     confirmed** with the user (see Decisions below) — easy to revisit before/at implementation.
     Result sorted ascending by bucket start:
     `[{"start": <local ISO8601 str>, "value": <float | int | None>, "count": <int>}, ...]`.
3. `websocket_api.py` (*depends on 1, 2*) — add `handle_aggregate_records`:
   - Schema: `type: "custom_metrics/aggregate_records"`, `record_type` (required str), `field`
     (optional str), `op` (required, `vol.In(AGGREGATE_OPERATIONS)`), `bucket` (required,
     `vol.In(AGGREGATE_BUCKETS)`), optional `start`/`end` (str, same pattern as `list_records`).
   - Validation done in the handler (can't be expressed as a static schema since it depends on the
     specific record type's field defs):
     - `record_type` must exist -> reuse the existing `"unknown_record_type"` error.
     - if `op != "count"`: `field` is required, must exist on the record type, and must be of
       `FieldType.NUMBER` -> new `"invalid_field"` error with a clear message per failure mode
       (missing/unknown field/wrong type).
     - if `op == "count"`: `field`, if provided, is simply ignored (no error).
   - Calls `runtime_data.storage.aggregate_records(...)`, returns `{"buckets": [...]}`.
   - Register in `async_setup_websocket_api` alongside the existing commands.
4. Tests (*depends on 2, 3*, can run in parallel with each other):
   - `tests/test_store.py`: unit tests for `_bucket_start` (day/week/month boundaries, including a
     DST-transition date to confirm local-time correctness) and `aggregate_records` (each op,
     mixed numeric/missing/non-numeric field values skipped correctly for sum/avg/min/max, `count`
     ignores field presence, empty-range returns `[]`, ascending sort order, `start`/`end`
     filtering reused correctly).
   - `tests/test_websocket_api.py`: WS command happy path per op (incl. `count` with no `field`),
     error cases (unknown `record_type`, missing `field` for a non-count op, unknown field name,
     field that isn't `FieldType.NUMBER`), and that response `buckets` are ordered ascending.
5. Optional (flag to whoever implements, not required): update README's WebSocket API section to
   document `custom_metrics/aggregate_records` for card developers, consistent with how
   `list_records` etc. are already documented there.

### Relevant files
- `custom_components/custom_metrics/const.py` — new `ATTR_OP`/`ATTR_BUCKET`/`ATTR_FIELD` (distinct
  from existing `ATTR_FIELDS`), `AGGREGATE_OPERATIONS`, `AGGREGATE_BUCKETS`.
- `custom_components/custom_metrics/store.py` — `_bucket_start()` helper, `_filter_by_range()`
  extraction (refactor of existing logic inside `async_list_records`), new
  `RecordStorage.aggregate_records()`.
- `custom_components/custom_metrics/websocket_api.py` — new `handle_aggregate_records`, registered
  in `async_setup_websocket_api`.
- `custom_components/custom_metrics/models.py` — reuse `RecordType.get_field()` /
  `FieldDefinition.type` for the field-is-numeric validation; no changes needed here.
- `tests/test_store.py`, `tests/test_websocket_api.py` — new test coverage per Step 4.
- `README.md` — optional WS API doc addition (Step 5).

### Verification (once implemented)
1. `python3 -m pytest tests/ -q` — all existing + new tests pass.
2. `python3 -m ruff check custom_components/custom_metrics tests` and
   `python3 -m ruff format --check custom_components/custom_metrics tests` — clean.
3. Manual smoke check against the live dev instance (`scripts/develop`): call
   `custom_metrics/aggregate_records` via the browser console
   (`await hass.connection.sendMessagePromise({type: "custom_metrics/aggregate_records",
   record_type: "<id>", field: "<numeric field>", op: "sum", bucket: "week"})`) against real
   recorded data and confirm bucket boundaries/values look correct, including for `op: "count"`
   with no `field`.

### Decisions
- No SQLite migration — current JSON `Store`-per-type + in-memory list stays as-is.
- WebSocket-only surface, no new service (per user answer).
- Sparse bucket output (no zero-filled gap buckets) chosen as the simpler default — NOT explicitly
  confirmed with the user, worth a quick sanity check before/at implementation start since it
  affects how a future card would need to render gaps.
- Buckets limited to day/week/month (no hour) per user's explicit refinement.
- Local-timezone-based bucket boundaries (via `dt_util.as_local`) chosen so "weekly"/"monthly"
  buckets match the user's actual calendar, not UTC.

### Further considerations (not yet actioned)
1. Sparse vs. dense (zero-filled) bucket output for gaps — recommend sparse (simpler, described
   above) but flag to user before/at implementation start since it's not yet explicitly confirmed.
2. Card charting (rendering a graph from this new endpoint in `custom-metrics-card.js`) was
   explicitly deferred — worth its own follow-up plan once this backend API ships and stabilizes.

## P0-6: CSV Export/Import (backend + config-flow UI) — IMPLEMENTED (2026-08-29)

Implemented per the design below, plus one refinement added at implementation time (confirmed via
`vscode_askQuestions`): export offers a choice between a **full backup** (`include_id=true`,
default — includes the internal `id` column, safe for idempotent re-import) and **data only**
(`include_id=false` — drops only `id`, keeps `timestamp` since it's meaningful data, not an
internal detail). The choice is a single checkbox in the config-flow "Export data" step, and an
`include_id` field (default `true`) on the new `custom_metrics.export_records` service.

New files: `csv_transfer.py` (pure `build_export_csv`/`parse_import_csv` logic, no I/O),
`export_view.py` (`CustomMetricsExportView`, mirrors `media_store.py`'s `CustomMetricsMediaView`
pattern, served at `/custom_metrics_export/<entry_id>/<record_type_id>`, `include_id` via query
param). `store.py` gained `async_import_records` (skips duplicate ids, single debounced save +
single `EVENT_RECORDS_UPDATED` fire per call, not per-row). `config_flow.py`'s reconfigure menu
gained `export_data` (builds a 5-minute signed download link via `async_sign_path`) and
`import_data` (HA's `FileSelector` + `homeassistant.components.file_upload.process_uploaded_file`,
then an `import_result` summary abort). `services.py` gained `export_records`/`import_records`
(the latter takes exactly one of `path`/`content`). `media_store.py`'s previously-private
path-allow-list helpers (`_allowed_source_roots`/`_validate_source_path`) were generalized into
public, reusable ones (`allowed_source_roots`, `validate_source_path` with a `kind`/
`allowed_extensions` param, plus a new `validate_write_target_path` for export's write-target case)
so the same OWASP path-traversal protection covers the new `path` service params, not just IMAGE
fields. `manifest.json` gained a `file_upload` dependency. Covered by new
`tests/test_csv_transfer.py` and `tests/test_export_view.py`, plus additions to `test_store.py`
(import/duplicate-skip/event-once), `test_config_flow.py` (export/import steps, incl. a real
`/api/file_upload` round-trip), and `test_services.py` (both services, path allow-list rejection,
path/content XOR validation). Full suite: 115 tests passing, ruff clean.

### Confirmed decisions (via `vscode_askQuestions`)
- UI placement: **per record type**, inside the existing `RecordTypeSubentryFlow` reconfigure menu
  (`config_flow.py`) — two new menu options `export_data`/`import_data` alongside the existing
  `manage_fields`/`reconfigure_add_field`/`set_retention`/`change_type_key`. NOT an entry-level
  bulk/all-types export — the CSV schema is inherently per-type (columns = that type's fields), so
  this is where it fits naturally in the existing UX (the same gear-icon "Configure" menu).
- Import identity: if the CSV has a non-empty `id` column, reuse it as the record's id; if a
  record with that id already exists in the type's store, SKIP the row (counted as
  `skipped_duplicate`) rather than overwrite — makes re-importing an exported backup idempotent/
  safe. Empty/missing `id` -> generate a new uuid4 (pure append).
- `multi_select` CSV encoding: join values with `;` in one cell (e.g. `red;blue`).
- `image` field CSV value: just the stored reference filename string (e.g. `a1b2c3.jpg` — i.e.
  the `"f"` key's value from the internal `{"f": ...}` ref object, per `IMAGE_REF_FILENAME_KEY` in
  `media_store.py`), NOT the full JSON object.
- Import does NOT validate that an image filename actually exists on disk — trusted-caller model,
  consistent with the rest of the media handling in this project.
- ALSO expose as services (`custom_metrics.export_records` / `custom_metrics.import_records`), for
  automation-driven scheduled backups, IN ADDITION to the config-flow UI (not UI-only).

### Design

#### Shared core: new `csv_transfer.py` module
- `build_export_csv(record_type: RecordType, records: list[dict]) -> str`: builds CSV text via
  stdlib `csv.writer` over `io.StringIO` (handles quoting of embedded commas/newlines/quotes
  automatically — no manual escaping, avoids injection-style bugs). Header row:
  `id, timestamp, <field.key for field in record_type.fields>` (record_type field order). Per-row
  value formatting: `multi_select` -> `";".join(value)`; `image` -> `value.get(IMAGE_REF_FILENAME_KEY,
  "")`; `boolean` -> `"true"`/`"false"` (lowercase, for portability); everything else -> `str(value)`
  if not None else `""`.
- `parse_import_csv(record_type: RecordType, csv_text: str) -> ImportParseResult`: parses via
  `csv.DictReader`, per row:
  - `id` column: if non-empty, reuse as-is; else generate a new uuid4 at insert time (leave as
    `None` here, `store.py`'s import method decides).
  - `timestamp` column: if non-empty, `dt_util.parse_datetime`; unparsable -> row error. If empty,
    use "now" at insert time.
  - Each remaining CSV column matched against `record_type.fields` by key: `multi_select` ->
    `split(";")` (drop empty strings); `image` -> build `{IMAGE_REF_FILENAME_KEY: value}` directly
    if non-empty (bypasses the normal IMAGE validator in `schema.py`, which expects a *source file
    path* to hand off to `media_store` — that's the `add_record` semantics, not import's); all
    other field types -> pass the raw string through the SAME per-field validators
    `schema.py._validator_for_field` already builds (reuse, don't reinvent — coerces
    `"true"/"false"` -> bool, numeric strings -> float, checks `single_select`/`multi_select`
    against `options`, etc.), building a schema that excludes IMAGE-typed fields (those are
    already-finalized objects, not passed through validation).
  - Unknown/extra CSV columns (not `id`/`timestamp`/any current field key) are silently ignored
    (forward-compatible with CSVs exported before a field was removed).
  - Missing a `required` field's value, or a validator raising `vol.Invalid`, -> that ROW is
    recorded as an error (row number + message) and skipped; the rest of the file still processes
    (a bad row shouldn't block restoring an otherwise-good backup).
  - Returns something like `ImportParseResult(rows: list[tuple[id: str | None, timestamp: datetime
    | None, fields: dict]], errors: list[dict])`.

#### `store.py` — bulk import support
- Add `RecordStorage.async_import_records(record_type_id, rows) -> ImportSummary` (`imported`,
  `skipped_duplicate` counts): builds a set of existing ids for the type once (O(1) membership
  checks), appends new envelopes for non-duplicate rows (generating a uuid4 for rows with no id),
  single `_async_schedule_save` call at the end (not per-row) — mirrors the existing debounced-save
  pattern already used by `async_add_record` etc.

#### Export delivery: new `CustomMetricsExportView` (mirrors `media_store.py`'s
`CustomMetricsMediaView` pattern exactly)
- New `HomeAssistantView` (`requires_auth=True` default) at
  `/{DOMAIN}_export/{entry_id}/{record_type_id}.csv`, generates the CSV on the fly per GET request
  via `csv_transfer.build_export_csv` (no temp file needed) and returns it as
  `web.Response(text=csv_text, content_type="text/csv", headers={"Content-Disposition":
  'attachment; filename="..."'})`.
- Registered once hass-wide (same `_registered` hass.data guard pattern as
  `async_register_media_view`).
- `config_flow.py`'s new `async_step_export_data` computes a short-lived signed URL via
  `homeassistant.components.http.auth.async_sign_path(hass, url, timedelta(minutes=5))` (same
  signing mechanism already relied on for images, verify exact sync/async calling convention
  against HA source at implementation time) and returns `self.async_abort(reason="export_ready",
  description_placeholders={"download_url": signed_url})` — a one-shot info screen with a
  clickable markdown link that the browser downloads directly (no further flow steps needed).

#### Import UI: `config_flow.py`'s new `async_step_import_data`
- Form with `vol.Required("file"): selector.FileSelector(selector.FileSelectorConfig(accept=
  ".csv"))` (HA's standard file-upload selector, used by many integrations for exactly this).
- On submit: `homeassistant.helpers.file_upload.process_uploaded_file(hass, file_id)` to get the
  uploaded content server-side, parse via `csv_transfer.parse_import_csv`, call
  `entry.runtime_data.storage.async_import_records(...)`, then show an `async_step_import_result`
  info step (or another `async_abort` with counts in `description_placeholders`: imported/
  skipped_duplicate/error count + first few error messages).

#### Services (`services.py`) — for automation-driven backups
- `custom_metrics.export_records`: fields `record_type` (required), `path` (optional string). If
  `path` given: write the CSV to that path (MUST validate/allow-list the root the same way
  `media_store.py` already restricts source paths — reuse/refactor its existing allow-listed-root
  check rather than duplicating a second ad-hoc path-traversal guard, since writing to an arbitrary
  filesystem path is a real OWASP path-traversal/arbitrary-file-write risk) and return `{"path":
  <resolved path>}`. If `path` omitted: return `{"csv": <text>}` directly via
  `SupportsResponse.OPTIONAL` (e.g. for piping into a notify action).
- `custom_metrics.import_records`: fields `record_type` (required), exactly one of `path` (read an
  existing CSV file, same allow-listed-root check) or `content` (raw CSV text inline) required —
  validated manually in the handler (`ServiceValidationError` if both or neither given, since
  voluptuous alone doesn't express XOR cleanly). Returns the same imported/skipped_duplicate/
  errors summary shape as the config-flow import step.
- Both registered in `async_setup` (module-level, alongside the existing `add_record`), with
  `services.yaml`/`icons.json`/`strings.json` entries matching the existing pattern.

### Relevant files
- NEW `custom_components/custom_metrics/csv_transfer.py` — `build_export_csv`, `parse_import_csv`,
  shared by config_flow.py, services.py, and the export view.
- NEW export view (either a new `export_view.py` mirroring `media_store.py`'s view pattern, or
  added to `media_store.py` directly — implementer's call): `CustomMetricsExportView`.
- `custom_components/custom_metrics/store.py` — new `async_import_records`.
- `custom_components/custom_metrics/config_flow.py` — new `async_step_export_data`,
  `async_step_import_data`, `async_step_import_result`; add `"export_data"`/`"import_data"` to the
  `reconfigure` menu's `menu_options`.
- `custom_components/custom_metrics/services.py` + `services.yaml` + `icons.json` — new
  `export_records`/`import_records` services.
- `custom_components/custom_metrics/media_store.py` — reuse/expose its existing allow-listed-root
  path-safety helper for the new services' `path` params (avoid a second implementation).
- `custom_components/custom_metrics/strings.json` + `translations/en.json` — new subentry-flow
  menu items/steps/abort reasons, new service strings.
- Tests: `tests/test_config_flow.py` (export_data signed-link shape, import_data happy path +
  duplicate-id skip + malformed-row error reporting — need to check PHACC's helper for testing
  `FileSelector` uploads in a flow, e.g. via `homeassistant.helpers.file_upload`'s test utilities),
  new `tests/test_csv_transfer.py` (export formatting per field type, round-trip export->import
  equality, malformed CSV row handling), `tests/test_services.py` (both new services, incl. path
  allow-list rejection), possibly `tests/test_export_view.py` (auth required, signed-URL access,
  content-type/disposition headers).

### Verification
1. `python3 -m pytest tests/ -q` and ruff clean, as usual.
2. Manual: export a record type via the integration's Configure -> "Export data" menu item in a
   live `scripts/develop` instance, confirm the CSV downloads with correct headers/values incl. a
   multi_select and an image field; re-import the same file and confirm all rows are skipped as
   duplicates (idempotent restore); modify a row's id/timestamp cell to simulate a merge scenario.
3. Manual: call `custom_metrics.export_records`/`import_records` from Developer Tools -> Actions
   with and without `path`, confirm path traversal outside the allow-listed root is rejected.

### Open items / not yet decided
- Exact reuse mechanism for the path allow-list check currently private to `media_store.py` (may
  need a small refactor to a shared/importable helper rather than copy-pasting the logic).
- Whether `async_sign_path`'s calling convention is actually async (verify against HA source before
  implementing, same caveat noted in the earlier media_store design notes).

## P0-7: Live card refresh on record changes — IMPLEMENTED (2026-08-28)

### Problem
`custom-metrics-card.js` only re-fetches (`_loadData()`) on first load (`hass` setter's
`firstRun`) and right after ITS OWN add/delete actions. There is no signal at all when data
changes via any OTHER path (an automation calling `custom_metrics.add_record`, another open card/
browser tab, the daily purge job, a future CSV import (P0-6), or a record-type definition edit via
the config subentry flow) — the card silently goes stale until the user manually reloads the
dashboard/tab.

### Confirmed decisions (via `vscode_askQuestions`)
- Mechanism: a HA event-bus event (`hass.bus.async_fire`), NOT a new dedicated WS "subscribe"
  command and NOT plain polling. The card subscribes via the standard
  `hass.connection.subscribeEvents(callback, event_type)` (the same pattern HA's own registries
  use, e.g. `EVENT_ENTITY_REGISTRY_UPDATED`) — simplest, no new WS command/connection bookkeeping,
  idiomatic for "something changed, go refetch" signals.
- Scope: fire on every mutation — `add_record`, `delete_record`, purge (retention), `max_records`
  eviction, and (once built) CSV import (P0-6) and record-type definition changes (field add/
  remove/rename, retention change, type key rename via the config subentry flow, P0-3/P0-4).
- Card debounces bursts (~300ms coalescing window) before refetching, so e.g. a CSV import of many
  rows (P0-6, already fires one event per store-mutating call, not per-row) doesn't cause a
  stampede of `list_records` calls.

### Design

#### Single event, fired from one place per concern
- `const.py`: `EVENT_RECORDS_UPDATED = f"{DOMAIN}_updated"`. Payload: `{"entry_id": <str>,
  "record_type_id": <str>}` — deliberately minimal (ids only, never field values/record content,
  since bus events are broadcast to every authenticated subscriber — a health-data privacy
  consideration, not just size). The SAME event/payload shape is used for both "records changed"
  and "record type definition changed", since the card's existing `_loadData()` already refetches
  *both* `list_record_types` and `list_records` together — no need for two separate event kinds or
  two separate card-side handlers.
- `store.py` (`RecordStorage`, already holds `self.hass`) — fire
  `self.hass.bus.async_fire(EVENT_RECORDS_UPDATED, {"entry_id": self.entry_id, "record_type_id":
  record_type_id})` at the end of: `async_add_record` (always), `async_delete_record` (only if a
  record was actually removed), `async_purge_expired` (once per record type that had >0 removed,
  inside its existing per-type loop), `async_enforce_max_records` (same, only when >0 evicted),
  and the future P0-6 `async_import_records` (once per call, not per imported row — already
  batches internally). Centralizing in `store.py` means every mutation path (WS command, the
  `add_record` service, the purge job, future CSV import) fires consistently without every call
  site needing to remember to do it.
- `__init__.py`'s `async_setup_entry` — after `entry.runtime_data` is set up, fire one
  `EVENT_RECORDS_UPDATED` per configured `record_type_id`. Since a config-subentry add/update/
  remove already triggers a full entry reload (existing P0-3/P0-4 behavior via
  `entry.add_update_listener`), this single call site transparently covers ALL record-type
  definition changes (field added/removed/renamed, retention changed, type key renamed, or a type
  added/removed) with no changes needed in `config_flow.py` itself. Firing unconditionally on
  every setup (including the very first one, when no card can be subscribed yet) is harmless
  (no-op) — chosen for simplicity over adding a "is this the first setup" guard.

#### Card-side (`custom-metrics-card.js`)
- Add `connectedCallback()`/`disconnectedCallback()` (standard custom-element lifecycle, not
  currently defined). On first `hass` assignment (existing `firstRun` check), additionally call
  `hass.connection.subscribeEvents(this._boundOnUpdated, EVENT_RECORDS_UPDATED_NAME)` (a JS
  constant matching the Python `EVENT_RECORDS_UPDATED` value, e.g. `"custom_metrics_updated"`),
  storing the returned unsubscribe function; guard with a flag so it only subscribes once even if
  the `hass` setter fires many times. Call the stored unsubscribe function from
  `disconnectedCallback()` (Lovelace destroys/recreates card elements on view switches/dashboard
  edits — must not leak subscriptions).
- Event handler: ignore events where `event.data.record_type_id !== this._config.record_type`;
  otherwise schedule a debounced (~300ms, `clearTimeout`/`setTimeout`) call to `_loadData()`,
  coalescing bursts into a single refetch.
- No change needed to `_loadData()` itself — it already fetches both `list_record_types` and
  `list_records` together, so a single call handles both records-changed and
  record-type-definition-changed cases uniformly.

### Relevant files
- `custom_components/custom_metrics/const.py` — new `EVENT_RECORDS_UPDATED`.
- `custom_components/custom_metrics/store.py` — fire the event at the end of
  `async_add_record`/`async_delete_record`/`async_purge_expired`/`async_enforce_max_records` (and
  the future P0-6 `async_import_records`).
- `custom_components/custom_metrics/__init__.py` — fire the event once per record type at the end
  of `async_setup_entry`.
- `custom_components/custom_metrics/www/custom-metrics-card.js` — `connectedCallback`/
  `disconnectedCallback`, event subscription + debounced refetch.
- Tests: `tests/test_store.py` (event fired on add/delete-that-removes/purge-that-removes/
  max_records-eviction, NOT fired on a no-op delete of an unknown id or a purge/eviction that
  removed nothing — use `hass.bus.async_listen`/`async_fire`-capturing in tests, standard HA test
  pattern via `hass.bus.async_listen(EVENT_RECORDS_UPDATED, capture)` + `await
  hass.async_block_till_done()`), `tests/test_init.py` (event fired once per record type after
  `async_setup_entry`/reload).

### Verification
1. `python3 -m pytest tests/ -q`, ruff clean.
2. Manual (`scripts/develop`): open the same record type's card in two browser tabs; add a record
   via Developer Tools → Actions (`custom_metrics.add_record`, i.e. NOT through either card) and
   confirm both tabs' tables update within ~300ms without a manual reload; repeat for a delete via
   one tab while the other is open; confirm a config subentry field-label edit is also reflected
   live in an already-open card without a page refresh.

### Implemented as designed above
- `const.py` gained `EVENT_RECORDS_UPDATED = f"{DOMAIN}_updated"` and `ATTR_ENTRY_ID = "entry_id"`
  (payload keys: `entry_id`, `record_type` — reusing the existing `ATTR_RECORD_TYPE` constant for
  the second key, instead of a separate `record_type_id` name, so the payload's key matches the
  same field name the card's own config already uses).
- `store.py`'s `RecordStorage` gained a `_fire_updated(record_type_id)` helper, called from
  `async_add_record` (always), `async_delete_record` (only when a record was actually removed),
  `async_purge_expired` and `async_enforce_max_records` (both only for record types that actually
  lost ≥1 record) — exactly as designed.
- `__init__.py`'s `async_setup_entry` fires the event once per configured record type after
  `entry.runtime_data` is set up (unconditionally, including on first setup) — covers every
  record-type-definition change (P0-3/P0-4 flows) via the reload they already trigger, with zero
  changes needed in `config_flow.py`.
- `custom-metrics-card.js` gained `connectedCallback()`/`disconnectedCallback()`,
  `_subscribeToUpdates()` (idempotent, called from both the `hass` setter and
  `connectedCallback()` so it re-establishes correctly if the card is detached/reattached),
  `_onRecordsUpdated()` (filters by `event.data.record_type === this._config.record_type`, then
  debounces via a 300ms `setTimeout` before calling `_loadData()`), and constants
  `EVENT_RECORDS_UPDATED = "custom_metrics_updated"` / `UPDATE_DEBOUNCE_MS = 300`.
- Tests added: `tests/test_store.py` (`test_add_record_fires_updated_event`,
  `test_delete_record_fires_updated_event_only_when_removed`,
  `test_purge_expired_fires_updated_event_only_when_removed`,
  `test_max_records_enforced_fires_updated_event_only_when_removed` — all via a
  `hass.bus.async_listen` capture helper) and `tests/test_init.py`
  (`test_setup_entry_fires_updated_event_per_record_type`). Full suite: 88 tests passing, ruff
  clean.
- Verified live end-to-end against the real dev HA instance (not just unit tests, via browser
  automation): a second `custom-metrics-card` element was injected directly, then a record was
  added purely via `custom_metrics/add_record` over WebSocket (bypassing that card entirely, to
  simulate "another tab/automation") — the injected card's table updated automatically (3 → 7 →
  cleaned back to 3 rows) with no manual reload, confirming the whole path (store event → bus →
  `hass.connection.subscribeEvents` → debounced `_loadData()`) works end-to-end. Also confirmed via
  a raw WS event subscription that the backend fires `custom_metrics_updated` with the expected
  `{entry_id, record_type}` payload.
- Dev-loop gotcha re-confirmed during this work: a config entry **reload** does NOT pick up edits
  to this integration's own `.py` files (Python only imports a module once; reload just re-runs
  the already-imported `async_setup_entry`/`async_unload_entry` functions) — a full `scripts/
  develop` process restart was required before the new `store.py`/`__init__.py` event-firing code
  took effect, even though the plan's own "Research findings" section above says reload is
  sufficient for iteration. That earlier note appears to have been wrong/incomplete for this class
  of change (or was only ever true for effects that don't depend on new code actually running,
  e.g. re-reading config); worth treating "restart, not just reload" as the safe default for
  backend `.py` changes going forward, reserving plain reload for config/data-only changes.

## P0-8: Move "Add record" form into a popup dialog — PLANNED, not yet implemented (2026-08-28)

### Confirmed decisions (via `vscode_askQuestions`)
- Dialog implementation: HA's `<ha-dialog>` (internal frontend component, matches native HA look),
  NOT a native `<dialog>` element — accepted risk of depending on an undocumented HA-internal
  custom element, per user's explicit preference.
- Trigger: a "+ Add record" button replacing the inline form area.
- Config key: `show_form` renamed to `show_add_record` (no backward-compat alias needed — this
  integration has not had an official HACS release/external distribution yet, per this file's own
  history showing "cut first GitHub release" was explicitly dropped in P0-1, so no known external
  configs depend on the old key).
- Scope: pure relocation of the existing form (same fields/validation/behavior) into the dialog —
  no redesign of the form's own layout.

### Design
- New `_openAddDialog()` method on `CustomMetricsCard`: creates
  `document.createElement("ha-dialog")`, builds the same field inputs as today (reusing
  `_renderFieldInput` per field) into its content, sets a heading (record type name), and —
  critically — appends the dialog element to `document.body`, NOT the card's own shadow root.
  This is the key non-obvious technical detail: Lovelace's masonry/grid dashboard layout can
  create a stacking/containment context that would visually clip a dialog rendered inline inside
  the card's own DOM subtree. HA's own internal dialog-manager always mounts real dialogs at the
  top level (a sibling of `home-assistant` itself) specifically to avoid this — this design
  mirrors that.
- `_render()`: the inline `formHtml` block is replaced by a single trigger button (shown when
  `this._config.show_add_record !== false`), wired to call `_openAddDialog()`.
- `_handleSubmit()`: on success, in addition to existing behavior (reset `_formValues`, call
  `_loadData()`), also closes/detaches `this._dialogEl`. On a validation error, the error message
  is updated IN PLACE inside the already-open dialog (mutate just the error text node) rather than
  re-rendering the whole dialog from scratch — re-rendering would wipe in-progress field values
  the user already typed into OTHER fields before the error occurred.
- `disconnectedCallback()` (new, or shared with P0-7 if implemented first/together): also closes/
  detaches `this._dialogEl` if the card itself is removed from the DOM while a dialog is open, to
  avoid an orphaned dialog element left on `document.body`.
- Escape key / backdrop click closing the dialog without submitting is native `<ha-dialog>`
  behavior (based on mwc-dialog) — no custom wiring needed beyond listening for its `closed` event
  to detach the element and clear `this._dialogEl`.
- Config rename: `show_form` -> `show_add_record` everywhere — `setConfig()`'s validation (both
  `show_add_record` and `show_list` false -> error), `_render()`'s read of the config, and the
  visual editor (`CustomMetricsCardEditor`)'s `_schema()`/`_displayData()` defaults/
  `EDITOR_FIELD_LABELS`, plus the top-of-file JSDoc config comment block.

### Relevant files
- `custom_components/custom_metrics/www/custom-metrics-card.js` ONLY — 100% frontend, no Python/
  backend changes needed at all.
- Confirmed via `tests/test_frontend.py` that existing automated tests only check static-path/URL
  registration, not JS runtime behavior — no automated JS test harness exists in this project, so
  verification here is manual/browser-based only.

### Verification (manual, via `scripts/develop`)
1. Click "+ Add record" -> dialog opens, NOT clipped by the dashboard's layout (test with the card
   inside a masonry/grid view, not just a single-card view, to actually exercise the containment
   risk).
2. Submit success -> dialog closes automatically, table refreshes with the new record.
3. Trigger a validation error (e.g. leave a required field blank, or an invalid image path) ->
   dialog stays open, shows the error, and other already-entered field values are still present
   (not wiped).
4. Escape key and backdrop click both dismiss the dialog without submitting anything.
5. `show_add_record: false` in card config -> no trigger button rendered.
6. Visual editor's boolean toggle (now labeled under `show_add_record`) still round-trips its
   value correctly into the card config.

### Decisions
- `<ha-dialog>` over native `<dialog>` — per explicit user preference, accepting the internal-API
  dependency risk (mitigated in practice: many other HACS custom cards already rely on the same
  element, so it has stayed compatible across HA releases historically).
- Dialog appended to `document.body`, not the card's shadow root — the main non-obvious technical
  risk/insight of this whole plan; without this, the popup could be visually clipped depending on
  the dashboard's layout.
- Breaking rename of `show_form` -> `show_add_record`, no deprecated alias — acceptable since this
  project has no known external users/released config yet.
- Pure relocation only, no form layout redesign, per confirmed scope.

### Further considerations
1. The dialog's slotted content lives in the page's light DOM (since it's appended to
   `document.body`, outside the card's own shadow root), so its `<style>` block isn't naturally
   CSS-encapsulated the way the rest of the card is — mitigated with specific/unlikely-to-clash
   class names rather than introducing a scoping mechanism, consistent with the project's
   dependency-free philosophy. Flagged as a minor, accepted limitation.
2. If P0-7 (live card refresh) and P0-8 (this popup) are both implemented, worth checking that an
   incoming `EVENT_RECORDS_UPDATED`-triggered refresh (P0-7) while the add-record dialog is
   currently open doesn't disrupt the open dialog's in-progress form state (it shouldn't, since
   `_loadData()` only touches the table/`_records`, not the separately-tracked dialog element — but
   worth a quick manual check once both exist).

## P0-9: Default record filter (card config + backend API) — IMPLEMENTED (2026-08-29)

### Problem
Right now a card always shows every record of its configured `record_type`. There's no way to
scope a card down to, e.g., only "Body weight" records where `name` is "Max" (useful for
per-person dashboards sharing one record type) — a user has to visually scan/ignore rows that
don't apply to them.

### Design history (went through 3 rounds of discussion before landing on this)
1. First draft (documented, never implemented): a flat `filter: {field: value}` map, exact-match
   only, WITH add-record pre-fill of matching fields.
2. Revised to a natural-text condition string (e.g. `filter: "name != 'Max' and age > 30"`) with a
   real tokenizer/parser for combining logic and quoted string values.
3. Final, simpler design (what's actually implemented): a YAML LIST of single-key
   `{field_key: value}` maps, AND-combined by list membership — eliminates the need for any
   field-name/AND-combining parser entirely (the list key IS the field name, the list IS the AND).

### Confirmed decisions
- Filter config: `filter: [{field_key: value}, ...]`, e.g.:
  ```yaml
  filter:
    - name: "!= Max"
    - age: "> 30"
  ```
- Combining: AND-only — every list item must match. No OR/parentheses.
- Operators: `==`, `!=`, `>`, `>=`, `<`, `<=` only. No operator prefix means `==` (e.g. `name: Max`
  is equivalent to `name: "== Max"`). `contains`/`in` (substring/list-membership keywords) were
  explicitly dropped — they're plain English words that would collide ambiguously with real text
  values (e.g. `status: "in progress"`).
- `multi_select` fields reinterpret `==`/`!=` as membership tests (does the stored list
  contain/not-contain this one value) instead of full-list equality — this covers the use case
  `contains` would have, without the ambiguity risk. `>`/`>=`/`<`/`<=` are not valid for
  `multi_select`.
- No quoting needed/parsed inside values — YAML's own quoting already delivers a plain string; a
  native YAML scalar (int/float/bool) is used directly with an implied `==`, no string parsing.
- No add-record pre-fill (dropped — filter is a pure display/query concern now).
- YAML-only — no `CustomMetricsCardEditor` visual-editor support (unlike P0-10).
- Filtering happens server-side (WS API + store), never client-side after fetching everything.
- `add_record` (service + WS) is NOT affected by a card's filter.
- A `filter` list item with more than one key is a hard validation error (`invalid_filter_item`) —
  almost certainly a typo, not intentional.
- Known accepted limitation: a `text`/`long_text` value that happens to start with an
  operator-like token (e.g. `"> 100 degrees"`) will be misparsed as an operator + remainder — no
  escape hatch exists once `contains` was dropped; documented in the README instead of "fixed".

### Operator ↔ field-type compatibility
| Field type | Allowed operators | Notes |
|---|---|---|
| `number` | `==` `!=` `>` `>=` `<` `<=` | |
| `text` / `long_text` | `==` `!=` | exact match only |
| `boolean` | `==` `!=` | |
| `datetime` | `==` `!=` `>` `>=` `<` `<=` | see normalization note below |
| `single_select` | `==` `!=` | |
| `multi_select` | `==` `!=` | membership semantics (see above) |
| `image` | none | always rejected (`unsupported_filter_field`) |

### Implementation
- NEW `custom_components/custom_metrics/filter_query.py`: `FilterError` (code + message, with
  named constants `ERR_UNKNOWN_FIELD`/`ERR_UNSUPPORTED_FIELD`/`ERR_UNSUPPORTED_OPERATOR`/
  `ERR_INVALID_VALUE`/`ERR_INVALID_ITEM` to satisfy ruff's EM101 "no bare string literal in
  raise" rule); `compile_record_filter(record_type, filter_list) -> Callable[[dict], bool] | None`
  — validates the whole list up front (fail fast, never a bare exception), returns a predicate
  tested against each record's `d` dict, `all(...)`-combined. No tokenizer/parser needed for
  combining or field-name resolution — just a small per-item "does this string start with an
  operator token?" check (longest-match-first so `>=`/`<=` aren't mis-split into `>`/`<`).
- `schema.py`: new `validate_filter_value(field_def, raw_value)` reusing the existing private
  `_validator_for_field` — SPECIAL-CASED for `MULTI_SELECT`: `_validator_for_field` returns a
  LIST-shaped validator (`[vol.In(options)]`, for validating the full stored list), but a filter
  literal is always a SINGLE value to check membership for, so `MULTI_SELECT` validates directly
  against `vol.In(field_def.options)` instead — this was a real bug caught during test-writing
  (calling the list validator on a bare string would iterate its characters).
- `store.py`: `async_list_records` gained a `predicate: Callable[[dict], bool] | None` param,
  folded into the same pass that already filters on `start`/`end` (the `start is None and end is
  None` fast-path now also requires `predicate is None`).
- `websocket_api.py`: `handle_list_records`'s schema gained `vol.Optional(ATTR_FILTER): list`;
  compiles the filter via `filter_query.compile_record_filter` (catching `FilterError` →
  `connection.send_error(msg["id"], err.code, err.message)`), passes the compiled predicate through
  to `storage.async_list_records`.
- `const.py`: `ATTR_FILTER = "filter"`.
- `www/custom-metrics-card.js`: minimal changes since all parsing/evaluation is server-side —
  `setConfig()` shallow-validates `filter` is an array if present; `_loadData()` forwards
  `this._config.filter` as the `filter` param on the `list_records` WS call. No pre-fill, no
  `_renderFieldInput`/table changes.
- DATETIME comparison note: a user-defined `datetime`-type field's stored value can be a Python
  `datetime` object (freshly added, never round-tripped) or an ISO string (after a save/reload) —
  nothing in the codebase normalizes it back to one shape. `filter_query.py` normalizes both sides
  defensively (`dt_util.parse_datetime(v) if isinstance(v, str) else v`) before comparing, rather
  than fixing the underlying inconsistency (out of scope here).
- A record missing an optional field always fails any condition on it, including `!=`.

### Tests
- NEW `tests/test_filter_query.py` — native scalars, every operator/field-type combination incl.
  rejections, `multi_select` membership both directions, `>=`/`<=` vs `>`/`<` longest-match
  parsing, unknown field, IMAGE-field rejection, invalid value coercion, non-list config,
  multi-key/non-dict item rejection, empty list, DATETIME str-vs-object normalization.
- `tests/test_store.py` — `async_list_records` with `predicate`, combined with `limit`.
- `tests/test_websocket_api.py` — `filter` happy path + `unknown_filter_field`/
  `unsupported_filter_field`/`unsupported_filter_operator`/`invalid_filter_value`/
  `invalid_filter_item`.
- `README.md` — new `filter` card config row + a "Filtering" subsection (operator table, examples,
  the text-operator-prefix limitation).

### Further considerations (not actioned, flagged for later)
1. `contains`/substring matching and `in`/list-membership for non-`multi_select` fields were
   dropped from v1 due to English-word ambiguity — could revisit with unambiguous symbol-based
   syntax (e.g. `~`) if ever requested.
2. No visual editor support for `filter` (YAML-only) — could mirror P0-10's editor work later.

## P0-10: Card config for table column visibility/order — IMPLEMENTED (2026-09-01)

Implemented as designed below, with one refinement to the editor UI decision: instead of a plain
comma-separated text field OR an unverified `ha-form` reorderable multi-select, the visual config
editor got a custom hand-rolled "Visible columns" (ordered, with ↑/↓/remove) + "Available fields"
(with add) picker widget, built directly in `CustomMetricsCardEditor` — per explicit user request
for a real select+reorder UI (2026-09-01). `setConfig()`/`_loadData()` validate `columns` (must be
an array of strings; unknown field keys surface as a visible card error, same pattern as an
unknown `record_type`). The add-record form is unaffected, `Timestamp`/`show_delete`'s Delete
column remain outside `columns`' control, exactly as scoped below.

### Problem
A card always shows every one of the record type's fields as a table column. For record types with
many fields, users may only care about a handful in the at-a-glance list view (e.g. show just
`systolic`/`diastolic` from a Blood Pressure type that also tracks `pulse`, `notes`, `medication`,
etc.) — there's no way to trim/reorder the table's columns today short of removing fields from the
record type entirely (which also removes them from the add-record form and existing data).

### Confirmed decisions (via `vscode_askQuestions`)
- Scope: **table columns only** — the add-record form is explicitly NOT affected and always shows
  every field of the record type, regardless of this config. (Keeps the two concerns independent:
  "what I want to glance at" vs. "what I need to fill in when adding a record".)
- Shape: **allow-list** (`columns: [<field key>, ...]`) — the list ALSO doubles as the column
  ORDER, so this single config covers both "which fields show" and "what order they show in" in
  one place (per user's explicit request) — no separate ordering config needed.
- Must be configurable from **both raw YAML and the card's visual config editor**
  (`CustomMetricsCardEditor`), not YAML-only.

### Design

#### Card config
- New optional card config `columns`: an array of field keys, in display order, e.g.:
  ```yaml
  type: custom:custom-metrics-card
  record_type: blood_pressure
  columns:
    - diastolic
    - systolic
  ```
- When present, the table shows ONLY the listed fields' columns, in the given order. The
  `Timestamp` column always stays first (not configurable via `columns` — it's the envelope's own
  built-in column, not one of the record type's user-defined fields) and the `Delete` column
  continues to be controlled independently by the existing `show_delete` config, unaffected by
  `columns`.
- When omitted (default, backward compatible): unchanged current behavior — every field shown, in
  the record type's own defined field order.
- `setConfig()` can only do shallow validation (`columns`, if present, must be an array of
  strings) since the record type's field definitions aren't known until `_loadData()` fetches
  `list_record_types` — same lazily-validated pattern already used for `record_type` itself and
  for P0-9's `filter`. Once loaded, any key in `columns` that doesn't match a real field on the
  record type surfaces as an error via the card's existing `this._error`/render path (e.g.
  `"Unknown column field 'xyz'"`), rather than silently ignored — a typo'd/renamed field key
  should be visible to the user, not silently produce a table missing a column they expected.

#### Rendering (`www/custom-metrics-card.js`)
- `_render()`'s table-building code (`fields.map(...)` for both the header row and each data row)
  switches from always iterating `this._recordType.fields` to iterating a small computed
  `_visibleFields()` helper: returns `this._config.columns
  ? this._config.columns.map((key) => this._recordType.get_field-equivalent lookup).filter(Boolean)
  : this._recordType.fields` — i.e. maps configured keys to their `FieldDefinition`-shaped objects
  (preserving `columns`' order), falling back to every field when unset. The add-record form
  (`_renderFieldInput` loop) keeps iterating `this._recordType.fields` directly, UNCHANGED, per the
  "table only" decision.

#### Visual config editor (`CustomMetricsCardEditor`)
- Needs a control that lets the user pick a SUBSET of the record type's fields AND set their
  order, driven by "Label (key)" options like the other field pickers in this project (e.g.
  `config_flow.py`'s `_field_selector` pattern, mirrored here for the card editor's JS/`ha-form`
  context).
- Two implementation options, in order of preference — **needs verifying against the actual
  installed HA frontend version at implementation time**, since `<ha-form>`'s `select` selector's
  exact reordering support isn't confirmed from this repo alone:
  1. **Preferred**: an `ha-form` `select` selector schema entry with `multiple: true` and (if
     supported by this HA version) `reorder: true` — HA's frontend has a multi-select selector
     variant that renders chosen options as a reorderable/draggable chip list, which would let the
     `columns` array be edited directly as "pick fields, drag to reorder" in one control. If this
     works, it's clearly the best UX and needs no fallback.
  2. **Fallback** (if `reorder` isn't available/reliable in this HA version, or multi-select
     re-ordering doesn't survive `ha-form`'s round-trip): a plain **text field**, following the
     exact same pattern already used for `last` (comma-separated field keys typed by the user,
     e.g. `diastolic,systolic`), parsed/validated the same lazy way as raw YAML — simpler,
     guaranteed to work, but a slightly less friendly editing experience than a real chip picker.
- Either way, `_displayData()`/`_schema()`/`EDITOR_FIELD_LABELS` gain a `columns` entry alongside
  the existing `record_type`/`title`/`last`/`show_*` fields, and the editor's `record_type`
  selection needs to be loaded before `columns`' options can be populated (mirrors how
  `_loadRecordTypes()` already gates the `record_type` dropdown's options today).

### Relevant files
- `custom_components/custom_metrics/www/custom-metrics-card.js` — `columns` card config, new
  `_visibleFields()` helper consumed by the table header/row rendering (add-record form
  unaffected), `CustomMetricsCardEditor`'s new `columns` schema entry + `EDITOR_FIELD_LABELS` entry.
- `README.md` — document the new `columns` card config option with a worked example, and note it's
  table-only (doesn't affect the add-record form).
- No backend/Python changes at all — this is 100% a card-side (frontend) feature, same as P0-8.
- Tests: none automated (per `test_frontend.py`'s existing scope — no JS test harness in this
  project); manual/browser verification only, consistent with prior card-only work (e.g. P0-8).

### Verification (once implemented)
1. Manual (`scripts/develop`): configure a card with `columns: [diastolic, systolic]` on a record
   type that also has other fields (e.g. `pulse`, `notes`) — confirm the table shows ONLY those two
   columns, in that order, with `Timestamp` still first and `Delete` still governed by
   `show_delete`; confirm the add-record form still shows EVERY field regardless of `columns`;
   confirm omitting `columns` reproduces today's unchanged behavior (all fields, record-type
   order); confirm an unknown key in `columns` surfaces a clear error instead of being silently
   dropped; confirm the visual config editor can both pick a subset of fields AND reorder them
   (whichever of the two implementation options above ends up used), and that editing via the
   visual editor round-trips correctly back to raw YAML.

### Decisions
- Table columns only — add-record form is never affected by this config.
- Allow-list shape (`columns: [...]`), doubling as both visibility AND order in one config.
- Must be supported in both raw YAML and the visual config editor (not YAML-only).
- `Timestamp` column and the `show_delete`-controlled Delete column are outside `columns`' control.

### Further considerations (not yet actioned)
1. Exact feasibility of a reorderable multi-select in `ha-form` for this HA version is unverified
   from this repo alone — needs a quick check against the live frontend at implementation time
   before committing to the "preferred" editor design over the comma-separated-text fallback.
2. Whether `columns` should eventually also support the add-record form (a per-user request could
   change the "table only" decision above) is explicitly out of scope for this pass — the two
   concerns (view vs. add) were deliberately kept independent per the confirmed decision.

## P1-1 (deferred): Multi-user support — separate records, owner-only editing
- Current state: no per-user concept anywhere in the data model. Every record is globally visible
  and editable by anyone who can reach the card or call the service; `add_record` doesn't record
  who created a record.
- Identity is actually available at both entry points, so this is implementable when picked up:
  WS commands receive `connection.user` (a `User` object: `.id`, `.name`, `.is_admin`); service
  calls receive `call.context.user_id` (may be `None` when an automation/script runs without an
  explicit user context — needs an explicit decision on how such records are attributed/owned).
- Worth distinguishing two readings of "separate record files, only owner may edit them" before
  designing further:
  (a) separate record *types* per user (each user gets their own independent set of types/fields) —
      a bigger change, and doesn't obviously match the phrasing/existing example data (e.g. the
      live "Body weight" record type already has a `name` field distinguishing Max/Magdalena within
      *one shared* type).
  (b) per-*record* ownership within a shared record type (the more likely intended reading): add an
      `owner_id` to the stored record envelope, populated from `connection.user.id`/
      `call.context.user_id` at creation time; `delete_record` (and any future "edit record"
      command) checks the caller's id against `owner_id` before allowing the action (with an open
      question on whether `is_admin` users should retain an override).
- "Separate record files" taken literally (one Store file per user per record type) is possible but
  would fragment the "list all records for this type" read path (fan-out across every user's file,
  merge/sort in memory) for no obvious scalability benefit — the existing per-record-type Store
  split was chosen to isolate a *noisy record type's* I/O, not per-user I/O, so this doesn't
  obviously carry over. Tagging an `owner_id` field within the existing per-type Store looks like
  the simpler, equally effective option, pending further design.
- Recommendation: this changes the data model and the security/permission model meaningfully
  enough (plus the open automation-attribution and admin-override questions above) that it
  deserves its own dedicated planning pass when picked up, rather than a quick addition here —
  consistent with how the original plan explicitly deferred P1 rather than under-designing it.

## P1-2: Card visual configuration editor — IMPLEMENTED (already done, doc corrected 2026-08-28)
- Correction: this was originally written up as "deferred"/investigation-only, but the actual code
  in `www/custom-metrics-card.js` already has it fully implemented, exactly per the approach
  described below — this section was simply never updated after implementation. Not outstanding
  work.
- As implemented: `CustomMetricsCard.getConfigElement()` returns a `<custom-metrics-card-editor>`
  (the `CustomMetricsCardEditor` class, same file). It lazily creates and reuses a single
  `<ha-form>` child (`_ensureForm()` — created once, `.data`/`.schema` updated in place on
  subsequent renders so it doesn't steal focus mid-edit), whose schema covers `record_type` (a
  `select` populated from `custom_metrics/list_record_types`, loaded once per `hass` assignment via
  `_loadRecordTypes()`), `title` (text), `last` (text — accepts either a count or a duration
  shorthand like `2w`), and boolean rows for `show_form`/`show_list`/`show_delete` (labels supplied
  via `EDITOR_FIELD_LABELS`, computed via `_form.computeLabel`). Changes are reported back to the
  dashboard editor via a `config-changed` CustomEvent dispatched from the `ha-form`'s
  `value-changed` listener, per the standard HA Lovelace card-editor contract. `_displayData()`
  merges in the same effective defaults as the card itself purely for display, without writing
  them back into the config until the user actually changes something.
- Note for whoever implements P0-8 (popup add-record dialog): that work renames `show_form` to
  `show_add_record`, so this editor's schema/`EDITOR_FIELD_LABELS`/`_displayData()` default need to
  be updated in lockstep — already captured in P0-8's own plan above, just cross-referencing here.
