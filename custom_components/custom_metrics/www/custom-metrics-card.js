/**
 * Custom Metrics Recorder - Lovelace card.
 *
 * A lightweight custom card (no build step, no external dependencies) that
 * lists and adds records for a single configured record type via the
 * custom_metrics WebSocket API.
 *
 * Card config:
 *   type: custom:custom-metrics-card
 *   record_type: blood_pressure   # required - the record type id
 *   title: Blood Pressure         # optional - defaults to the record type's name
 *   last: 20                      # optional - a count (max rows, default 20) OR a duration like
 *                                  # '30m', '12h', '3d', '2w' (minutes/hours/days/weeks - show
 *                                  # everything from that far back, still capped server-side)
 *   show_form: true               # optional - show the "add record" form, default true
 *   show_list: true               # optional - show the records table, default true
 *   show_delete: true             # optional - show per-row delete buttons, default true
 *   columns:                      # optional - table-only column allow-list + order (add-record
 *     - systolic                  # form is unaffected and always shows every field); omit for
 *     - diastolic                 # today's default behavior (every field, record type's order)
 *
 * A visual editor (CustomMetricsCardEditor, below) is also registered via
 * getConfigElement(), so all of the above can be configured through the
 * dashboard's "Visual editor" instead of raw YAML.
 */

const DEFAULT_LAST_COUNT = 20;
const LAST_DURATION_RE = /^(\d+)(m|h|d|w)$/i;
const DURATION_UNIT_MS = { m: 60_000, h: 3_600_000, d: 86_400_000, w: 604_800_000 };

// Fired on hass.bus (see const.py's EVENT_RECORDS_UPDATED) whenever a record
// type's data or definition changes, from ANY source (this card, another
// card/tab, an automation's service call, the purge job, etc.) - lets an
// already-open card refetch instead of silently going stale.
const EVENT_RECORDS_UPDATED = "custom_metrics_updated";
// Coalesces bursts of update events (e.g. many rows added in quick
// succession) into a single refetch.
const UPDATE_DEBOUNCE_MS = 300;

/**
 * Parse the card's `last` config value into either a row count or a
 * duration (in ms), defaulting to DEFAULT_LAST_COUNT rows when unset.
 * Returns null if the value is neither a positive number nor a recognized
 * duration string (e.g. '2w'), so callers can reject it as a config error.
 */
function parseLast(value) {
    if (value === undefined) {
        return { type: "count", value: DEFAULT_LAST_COUNT };
    }
    if (typeof value === "number") {
        return Number.isFinite(value) && value >= 1 ? { type: "count", value } : null;
    }
    if (typeof value === "string") {
        const match = LAST_DURATION_RE.exec(value.trim());
        if (!match) {
            return null;
        }
        return { type: "duration", ms: Number(match[1]) * DURATION_UNIT_MS[match[2].toLowerCase()] };
    }
    return null;
}

function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (ch) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
    })[ch]);
}

class CustomMetricsCard extends HTMLElement {
    constructor() {
        super();
        this.attachShadow({ mode: "open" });
        this._config = null;
        this._hass = null;
        this._recordType = null;
        this._records = [];
        this._formValues = {};
        this._loading = false;
        this._error = null;
        this._imageUrls = {};
        this._unsubscribeUpdates = null;
        this._subscribingToUpdates = false;
        this._updateDebounceTimer = null;
        // Tri-state: null = not yet validated against the record type's real
        // fields (unknown `record_type` / unknown `columns` keys), true =
        // validated and valid, false = validated and invalid. Reset to null
        // in setConfig() so _validateConfig() runs exactly once per config -
        // see that method for why validation lives there, not in _loadData().
        this._configValid = null;
    }

    setConfig(config) {
        if (!config || !config.record_type) {
            throw new Error("custom-metrics-card: 'record_type' is required in the card config");
        }
        if (config.last !== undefined && !parseLast(config.last)) {
            throw new Error(
                "custom-metrics-card: 'last' must be a positive number (e.g. 20) or a duration like '30m', '12h', '3d', '2w'",
            );
        }
        if (config.show_form === false && config.show_list === false) {
            throw new Error(
                "custom-metrics-card: 'show_form' and 'show_list' cannot both be false - the card would render nothing",
            );
        }
        if (config.filter !== undefined && !Array.isArray(config.filter)) {
            throw new Error(
                "custom-metrics-card: 'filter' must be a list of single-key field maps, e.g. [{name: Max}]",
            );
        }
        if (
            config.columns !== undefined &&
            (!Array.isArray(config.columns) || !config.columns.every((c) => typeof c === "string"))
        ) {
            throw new Error(
                "custom-metrics-card: 'columns' must be a list of field key strings, e.g. [systolic, diastolic]",
            );
        }
        this._config = config;
        this._recordType = null;
        this._records = [];
        this._configValid = null;
        this._render();
        // No-op if `hass` isn't connected yet (validated instead from the
        // `hass` setter below, once it is) - see _validateConfig().
        this._validateConfig();
    }

    set hass(hass) {
        const firstRun = !this._hass;
        this._hass = hass;
        if (firstRun) {
            // setConfig() always runs before this setter (framework
            // guarantee), so this is the earliest point `hass` is available
            // for a freshly-mounted card - validate now if setConfig()'s own
            // call above couldn't (because `hass` wasn't connected yet).
            this._validateConfig();
        }
        this._subscribeToUpdates();
    }

    connectedCallback() {
        // Re-establish the subscription if the card is re-attached to the DOM
        // (e.g. after a dashboard view switch) - disconnectedCallback tears it
        // down below to avoid leaking it while detached.
        this._subscribeToUpdates();
    }

    disconnectedCallback() {
        if (this._updateDebounceTimer) {
            clearTimeout(this._updateDebounceTimer);
            this._updateDebounceTimer = null;
        }
        if (this._unsubscribeUpdates) {
            this._unsubscribeUpdates();
            this._unsubscribeUpdates = null;
        }
    }

    async _subscribeToUpdates() {
        if (!this._hass || this._unsubscribeUpdates || this._subscribingToUpdates) {
            return;
        }
        // Set synchronously, before the `await` below, so a `hass` setter call
        // that re-enters this method while the first call is still pending
        // (hass is reassigned to every card on nearly every state change, so
        // this is a real, frequent race - not just theoretical) is blocked
        // immediately rather than racing to also call subscribeEvents().
        this._subscribingToUpdates = true;
        try {
            const unsubscribe = await this._hass.connection.subscribeEvents(
                (event) => this._onRecordsUpdated(event),
                EVENT_RECORDS_UPDATED,
            );
            if (this.isConnected) {
                this._unsubscribeUpdates = unsubscribe;
            } else {
                // Card was removed from the DOM while the subscribe call was
                // still in flight - don't leak the subscription.
                unsubscribe();
            }
        } catch {
            // Live refresh is a best-effort enhancement - if subscribing fails
            // (e.g. the connection isn't ready yet), the card still works, just
            // without live updates until the next manual reload.
        } finally {
            this._subscribingToUpdates = false;
        }
    }

    _onRecordsUpdated(event) {
        if (!this._config || event.data?.record_type !== this._config.record_type) {
            return;
        }
        if (this._updateDebounceTimer) {
            clearTimeout(this._updateDebounceTimer);
        }
        this._updateDebounceTimer = setTimeout(() => {
            this._updateDebounceTimer = null;
            this._loadData();
        }, UPDATE_DEBOUNCE_MS);
    }

    getCardSize() {
        return 3 + Math.ceil((this._records || []).length / 2);
    }

    /**
     * Validates the config against the record type's real fields (unknown
     * `record_type` / unknown `columns` keys) - needs live server data (the
     * record type's field list), so it's necessarily async, but it's driven
     * by config/hass lifecycle events (setConfig(), the hass setter's first
     * run), NOT by _loadData()'s per-refresh hot path, so it only ever runs
     * once per config (guarded by `_configValid`, reset to null in
     * setConfig()). `_loadData()` just checks the resulting `_configValid`
     * and stops immediately (waiting for the next config/hass change) if
     * it's anything other than `true`.
     */
    async _validateConfig() {
        if (!this._hass || !this._config || this._configValid !== null) {
            return;
        }
        try {
            const typesResponse = await this._hass.callWS({
                type: "custom_metrics/list_record_types",
            });
            const recordType = (typesResponse.record_types || []).find(
                (rt) => rt.id === this._config.record_type,
            );
            if (!recordType) {
                throw new Error(`Unknown record_type '${this._config.record_type}'`);
            }
            if (this._config.columns) {
                const validKeys = new Set(recordType.fields.map((f) => f.key));
                const unknownColumn = this._config.columns.find((key) => !validKeys.has(key));
                if (unknownColumn) {
                    throw new Error(`Unknown column field '${unknownColumn}'`);
                }
            }
            this._configValid = true;
            await this._loadData();
        } catch (err) {
            this._configValid = false;
            this._error = err.message || String(err);
            this._render();
        }
    }

    async _loadData() {
        if (!this._hass || !this._config || !this._configValid) {
            return;
        }
        this._loading = true;
        this._error = null;
        this._render();
        try {
            const typesResponse = await this._hass.callWS({
                type: "custom_metrics/list_record_types",
            });
            const recordType = (typesResponse.record_types || []).find(
                (rt) => rt.id === this._config.record_type,
            );
            this._recordType = recordType;

            const last = parseLast(this._config.last);
            const recordsResponse = await this._hass.callWS({
                type: "custom_metrics/list_records",
                record_type: this._config.record_type,
                ...(last.type === "count"
                    ? { limit: last.value }
                    : { start: new Date(Date.now() - last.ms).toISOString() }),
                ...(this._config.filter ? { filter: this._config.filter } : {}),
            });
            this._records = (recordsResponse.records || []).sort(
                (a, b) => new Date(b.timestamp) - new Date(a.timestamp),
            );
            this._imageUrls = {};
        } catch (err) {
            this._error = err.message || String(err);
        } finally {
            this._loading = false;
            this._render();
        }

        // Resolve image fields to signed, displayable URLs in the background
        // (via HA's media_source, which handles authentication) and
        // re-render once they're available, without blocking the initial
        // (text/number/etc.) render above.
        await this._resolveImages();
    }

    async _resolveImages() {
        const imageFieldKeys = (this._recordType?.fields || [])
            .filter((f) => f.type === "image")
            .map((f) => f.key);
        if (!imageFieldKeys.length || !this._records.length) {
            return;
        }

        let anyResolved = false;
        await Promise.all(
            this._records.flatMap((record) =>
                imageFieldKeys.map(async (fieldKey) => {
                    const value = record[fieldKey];
                    const cacheKey = `${record.id}/${fieldKey}`;
                    if (!value || !value.f || this._imageUrls[cacheKey] !== undefined) {
                        return;
                    }
                    try {
                        const resolved = await this._hass.callWS({
                            type: "media_source/resolve_media",
                            media_content_id: `media-source://custom_metrics/${this._config.record_type}/${record.id}/${fieldKey}`,
                        });
                        this._imageUrls[cacheKey] = resolved.url;
                    } catch {
                        this._imageUrls[cacheKey] = null;
                    }
                    anyResolved = true;
                }),
            ),
        );
        if (anyResolved) {
            this._render();
        }
    }

    async _handleSubmit(event) {
        event.preventDefault();
        if (!this._recordType || !this._hass) {
            return;
        }
        const fields = {};
        for (const field of this._recordType.fields) {
            const value = this._formValues[field.key];
            if (value === undefined || value === "") {
                continue;
            }
            fields[field.key] = field.type === "number" ? Number(value) : value;
        }

        this._error = null;

        for (const field of this._recordType.fields) {
            if (field.type !== "image") {
                continue;
            }
            const path = fields[field.key];
            if (!path) {
                continue;
            }
            try {
                const result = await this._hass.callWS({
                    type: "custom_metrics/validate_image_path",
                    path,
                });
                if (!result.valid) {
                    this._error = `${field.label}: ${result.error}`;
                    this._render();
                    return;
                }
            } catch (err) {
                this._error = err.message || String(err);
                this._render();
                return;
            }
        }

        try {
            await this._hass.callWS({
                type: "custom_metrics/add_record",
                record_type: this._config.record_type,
                fields,
            });
            this._formValues = {};
            await this._loadData();
        } catch (err) {
            this._error = err.message || String(err);
            this._render();
        }
    }

    _handleDelete(recordId) {
        return async () => {
            try {
                await this._hass.callWS({
                    type: "custom_metrics/delete_record",
                    record_type: this._config.record_type,
                    record_id: recordId,
                });
                await this._loadData();
            } catch (err) {
                this._error = err.message || String(err);
                this._render();
            }
        };
    }

    _handleInputChange(key, isCheckbox, isMultiSelect) {
        return (event) => {
            if (isCheckbox) {
                this._formValues[key] = event.target.checked;
            } else if (isMultiSelect) {
                this._formValues[key] = Array.from(event.target.selectedOptions).map(
                    (option) => option.value,
                );
            } else {
                this._formValues[key] = event.target.value;
            }
        };
    }

    _renderFieldInput(field) {
        const label = escapeHtml(field.label);
        const inputId = `field-${field.key}`;
        if (field.type === "image") {
            return `<label for="${inputId}">${label}</label><input id="${inputId}" type="text" data-key="${field.key}" placeholder="Full path to an existing image file under /config, e.g. /config/www/photo.jpg" />`;
        }
        if (field.type === "long_text") {
            return `<label for="${inputId}">${label}</label><textarea id="${inputId}" data-key="${field.key}"></textarea>`;
        }
        if (field.type === "boolean") {
            return `<label><input type="checkbox" data-key="${field.key}" /> ${label}</label>`;
        }
        if (field.type === "datetime") {
            return `<label for="${inputId}">${label}</label><input id="${inputId}" type="datetime-local" data-key="${field.key}" />`;
        }
        if (field.type === "single_select" || field.type === "multi_select") {
            const options = (field.options || [])
                .map((option) => `<option value="${escapeHtml(option)}">${escapeHtml(option)}</option>`)
                .join("");
            const multiple = field.type === "multi_select" ? "multiple" : "";
            return `<label for="${inputId}">${label}</label><select id="${inputId}" data-key="${field.key}" ${multiple}><option value=""></option>${options}</select>`;
        }
        const inputType = field.type === "number" ? "number" : "text";
        const step = field.type === "number" ? ` step="any"` : "";
        return `<label for="${inputId}">${label}</label><input id="${inputId}" type="${inputType}" data-key="${field.key}"${step} />`;
    }

    _formatValue(value, field) {
        if (value === undefined || value === null) {
            return "";
        }
        if (field.type === "boolean") {
            return value ? "Yes" : "No";
        }
        if (Array.isArray(value)) {
            return escapeHtml(value.join(", "));
        }
        return escapeHtml(String(value));
    }

    _renderCell(record, field) {
        if (field.type !== "image") {
            return this._formatValue(record[field.key], field);
        }
        const value = record[field.key];
        if (!value || !value.f) {
            return "";
        }
        const url = this._imageUrls[`${record.id}/${field.key}`];
        if (url === null) {
            return `<em>Image unavailable</em>`;
        }
        if (!url) {
            return "Loading image...";
        }
        return `<img class="record-image" src="${url}" alt="${escapeHtml(field.label)}" />`;
    }

    /**
     * Fields to show as TABLE columns, honoring the `columns` config's
     * allow-list + order when present (validated against real field keys in
     * _loadData()). The add-record form always uses the full, unfiltered
     * `this._recordType.fields` instead - it is deliberately unaffected by
     * this config, per the "table only" scope decision.
     */
    _visibleFields() {
        if (!this._recordType) {
            return [];
        }
        if (!this._config.columns) {
            return this._recordType.fields;
        }
        return this._config.columns
            .map((key) => this._recordType.fields.find((f) => f.key === key))
            .filter(Boolean);
    }

    _render() {
        if (!this.shadowRoot) {
            return;
        }
        if (!this._config) {
            this.shadowRoot.innerHTML = "";
            return;
        }

        const showList = this._config.show_list !== false;
        const showForm = this._config.show_form !== false;
        const showDelete = this._config.show_delete !== false;

        const title = escapeHtml(
            this._config.title || (this._recordType ? this._recordType.name : this._config.record_type),
        );

        let bodyHtml;
        if (this._loading && !this._recordType) {
            bodyHtml = "<p>Loading...</p>";
        } else if (this._error) {
            bodyHtml = `<p class="error">${escapeHtml(this._error)}</p>`;
        } else if (!this._recordType) {
            bodyHtml = "<p>No data.</p>";
        } else {
            const tableFields = this._visibleFields();
            let tableHtml = "";
            if (showList) {
                const headerCells = tableFields.map((f) => `<th>${escapeHtml(f.label)}</th>`).join("");
                const deleteHeader = showDelete ? "<th></th>" : "";
                const rows = this._records
                    .map((record) => {
                        const cells = tableFields
                            .map((f) => `<td>${this._renderCell(record, f)}</td>`)
                            .join("");
                        const deleteCell = showDelete
                            ? `<td class="delete-cell"><button class="delete-btn" data-id="${record.id}">Delete</button></td>`
                            : "";
                        return `<tr>
            <td>${new Date(record.timestamp).toLocaleString()}</td>
            ${cells}
            ${deleteCell}
          </tr>`;
                    })
                    .join("");
                const colspan = tableFields.length + 1 + (showDelete ? 1 : 0);

                tableHtml = `
        <table>
          <thead><tr><th>Timestamp</th>${headerCells}${deleteHeader}</tr></thead>
          <tbody>${rows || `<tr><td colspan="${colspan}">No records yet.</td></tr>`}</tbody>
        </table>
      `;
            }

            let formHtml = "";
            if (showForm) {
                const formFields = this._recordType.fields
                    .map((f) => {
                        const wrapperClass = f.type === "boolean" ? "field-boolean" : "field";
                        return `<div class="${wrapperClass}">${this._renderFieldInput(f)}</div>`;
                    })
                    .join("");
                formHtml = `
        <form id="add-form">
          ${formFields}
          <div class="form-actions"><button type="submit">Add record</button></div>
        </form>
      `;
            }

            bodyHtml = `${tableHtml}${formHtml}`;
        }

        this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        table { width: 100%; border-collapse: collapse; margin-bottom: 12px; }
        th, td { text-align: left; padding: 4px 8px; border-bottom: 1px solid var(--divider-color, #e0e0e0); }
        .delete-cell { text-align: right; }
        .record-image { max-width: 80px; max-height: 80px; border-radius: 4px; display: block; }
        form { display: grid; grid-template-columns: auto 1fr; column-gap: 8px; row-gap: 8px; align-items: center; }
        .field { display: contents; }
        .field-boolean { grid-column: 1 / -1; }
        .form-actions { grid-column: 1 / -1; justify-self: end; }
        .error { color: var(--error-color, red); }
        button { cursor: pointer; }
      </style>
      <ha-card header="${title}">
        <div class="card-content">${bodyHtml}</div>
      </ha-card>
    `;

        const form = this.shadowRoot.getElementById("add-form");
        if (form) {
            form.addEventListener("submit", (event) => this._handleSubmit(event));
            form.querySelectorAll("[data-key]").forEach((input) => {
                const key = input.dataset.key;
                const isCheckbox = input.type === "checkbox";
                const isMultiSelect = input.tagName === "SELECT" && input.multiple;
                input.addEventListener("change", this._handleInputChange(key, isCheckbox, isMultiSelect));
            });
        }
        this.shadowRoot.querySelectorAll(".delete-btn").forEach((button) => {
            button.addEventListener("click", this._handleDelete(button.dataset.id));
        });
    }

    static getStubConfig() {
        return { type: "custom:custom-metrics-card", record_type: "" };
    }

    static getConfigElement() {
        return document.createElement("custom-metrics-card-editor");
    }
}

const EDITOR_FIELD_LABELS = {
    record_type: "Record type",
    title: "Title",
    last: "Last N records (count or duration like 2w)",
    show_form: "Show add-record form",
    show_list: "Show records list",
    show_delete: "Show delete buttons",
};

/**
 * Visual editor for custom-metrics-card, using HA's built-in <ha-form>.
 *
 * Exposes every card config option (record_type, title, last, show_form,
 * show_list, show_delete) as a form field, and reports changes back to the
 * dashboard editor via the standard `config-changed` event. The `<ha-form>`
 * element is created once and reused across updates (its `.data`/`.schema`
 * are updated in place) instead of being recreated on every render, since
 * recreating it would steal input focus while the user is mid-edit (e.g.
 * typing in the `title` field).
 */
class CustomMetricsCardEditor extends HTMLElement {
    constructor() {
        super();
        this._config = {};
        this._hass = null;
        this._recordTypes = [];
        this._recordTypesLoaded = false;
        this._form = null;
        this._columnsSection = null;
    }

    setConfig(config) {
        this._config = config || {};
        this._updateForm();
    }

    set hass(hass) {
        this._hass = hass;
        this._loadRecordTypes();
        this._updateForm();
    }

    async _loadRecordTypes() {
        if (!this._hass || this._recordTypesLoaded) {
            return;
        }
        this._recordTypesLoaded = true;
        try {
            const response = await this._hass.callWS({ type: "custom_metrics/list_record_types" });
            this._recordTypes = response.record_types || [];
        } catch {
            this._recordTypes = [];
        }
        this._updateForm();
    }

    // Merge in explicit defaults purely for display, so boolean toggles/`last`
    // show their effective (card-default) value for a config that omits them,
    // WITHOUT writing those defaults back into the config until the user
    // actually changes something.
    _displayData() {
        return {
            last: DEFAULT_LAST_COUNT,
            show_form: true,
            show_list: true,
            show_delete: true,
            ...this._config,
        };
    }

    _schema() {
        return [
            {
                name: "record_type",
                required: true,
                selector: {
                    select: {
                        mode: "dropdown",
                        options: this._recordTypes.map((rt) => ({ value: rt.id, label: rt.name })),
                    },
                },
            },
            { name: "title", selector: { text: {} } },
            { name: "last", selector: { text: {} } },
            { name: "show_form", selector: { boolean: {} } },
            { name: "show_list", selector: { boolean: {} } },
            { name: "show_delete", selector: { boolean: {} } },
        ];
    }

    _ensureForm() {
        if (this._form) {
            return this._form;
        }
        this._form = document.createElement("ha-form");
        this._form.computeLabel = (schema) => EDITOR_FIELD_LABELS[schema.name] || schema.name;
        this._form.addEventListener("value-changed", (event) => {
            event.stopPropagation();
            this.dispatchEvent(
                new CustomEvent("config-changed", {
                    detail: { config: event.detail.value },
                    bubbles: true,
                    composed: true,
                }),
            );
        });
        this.appendChild(this._form);
        return this._form;
    }

    _updateForm() {
        if (!this._hass) {
            return;
        }
        const form = this._ensureForm();
        form.hass = this._hass;
        form.schema = this._schema();
        form.data = this._displayData();
        this._updateColumnsSection();
    }

    _ensureColumnsSection() {
        if (this._columnsSection) {
            return this._columnsSection;
        }
        this._columnsSection = document.createElement("div");
        this._columnsSection.className = "columns-picker";
        this.appendChild(this._columnsSection);
        return this._columnsSection;
    }

    _emitConfigChanged(config) {
        this.dispatchEvent(
            new CustomEvent("config-changed", {
                detail: { config },
                bubbles: true,
                composed: true,
            }),
        );
    }

    /**
     * Renders the `columns` picker: a "Visible columns" list (in configured
     * order, with up/down/remove controls) and an "Available fields" list
     * (with an add control) for the currently selected record type. Not a
     * plain text field and not backed by `<ha-form>` - built directly as
     * hand-rolled HTML/listeners (same style as CustomMetricsCard itself)
     * since `<ha-form>`'s reorderable multi-select support isn't guaranteed
     * across HA frontend versions, per P0-10's plan.
     */
    _updateColumnsSection() {
        const section = this._ensureColumnsSection();
        const recordType = this._recordTypes.find((rt) => rt.id === this._config.record_type);
        if (!recordType) {
            section.innerHTML = `<p class="columns-picker__hint">Select a record type to configure columns.</p>`;
            return;
        }

        const allFields = recordType.fields || [];
        const selectedKeys = this._config.columns || allFields.map((f) => f.key);
        const byKey = new Map(allFields.map((f) => [f.key, f]));
        const selectedFields = selectedKeys.map((key) => byKey.get(key)).filter(Boolean);
        const availableFields = allFields.filter((f) => !selectedKeys.includes(f.key));

        const selectedRows = selectedFields
            .map(
                (f, index) => `
          <li class="columns-picker__row" data-key="${f.key}">
            <span>${escapeHtml(f.label)}</span>
            <span class="columns-picker__actions">
              <button type="button" data-action="up" data-key="${f.key}" ${index === 0 ? "disabled" : ""}>&uarr;</button>
              <button type="button" data-action="down" data-key="${f.key}" ${index === selectedFields.length - 1 ? "disabled" : ""}>&darr;</button>
              <button type="button" data-action="remove" data-key="${f.key}">&times;</button>
            </span>
          </li>`,
            )
            .join("");

        const availableRows = availableFields
            .map(
                (f) => `
          <li class="columns-picker__row" data-key="${f.key}">
            <span>${escapeHtml(f.label)}</span>
            <span class="columns-picker__actions">
              <button type="button" data-action="add" data-key="${f.key}">+</button>
            </span>
          </li>`,
            )
            .join("");

        section.innerHTML = `
      <style>
        .columns-picker { margin-top: 8px; }
        .columns-picker__group { margin-top: 8px; }
        .columns-picker__group h4 { margin: 4px 0; font-size: 0.9em; color: var(--secondary-text-color, #666); }
        .columns-picker__list { margin: 0; padding: 0; }
        .columns-picker__row { display: flex; align-items: center; justify-content: space-between; padding: 2px 0; list-style: none; }
        .columns-picker__actions button { cursor: pointer; margin-left: 4px; }
        .columns-picker__hint { color: var(--secondary-text-color, #666); font-size: 0.9em; }
      </style>
      <div class="columns-picker__group">
        <h4>Visible columns</h4>
        <ul class="columns-picker__list">${selectedRows || "<li>(none)</li>"}</ul>
      </div>
      <div class="columns-picker__group">
        <h4>Available fields</h4>
        <ul class="columns-picker__list">${availableRows || "<li>(none)</li>"}</ul>
      </div>
    `;

        section.querySelectorAll("button[data-action]").forEach((button) => {
            button.addEventListener("click", () => {
                const action = button.dataset.action;
                const key = button.dataset.key;
                const newKeys = selectedFields.map((f) => f.key);
                if (action === "add") {
                    newKeys.push(key);
                } else if (action === "remove") {
                    const idx = newKeys.indexOf(key);
                    if (idx !== -1) {
                        newKeys.splice(idx, 1);
                    }
                } else if (action === "up" || action === "down") {
                    const idx = newKeys.indexOf(key);
                    const swapWith = action === "up" ? idx - 1 : idx + 1;
                    if (idx !== -1 && swapWith >= 0 && swapWith < newKeys.length) {
                        [newKeys[idx], newKeys[swapWith]] = [newKeys[swapWith], newKeys[idx]];
                    }
                }
                this._emitConfigChanged({ ...this._config, columns: newKeys });
            });
        });
    }
}

// Registering the custom element immediately at module evaluation time is
// racy: this module is loaded via a dynamic `import()` fired from HA's
// frontend bootstrap script, in parallel with HA's own core/app bundles.
// If our `customElements.define()` call happens to run before HA's frontend
// finishes setting up its custom element registry, our registration is
// silently lost (the class is never retrievable via `customElements.get()`
// afterwards, even though this module ran fine). Deferring registration
// until a core HA element (`home-assistant`, the app's root element) is
// defined ensures the registry is already in its final state.
function registerCustomMetricsCard() {
    if (customElements.get("custom-metrics-card")) {
        return;
    }
    customElements.define("custom-metrics-card", CustomMetricsCard);
    customElements.define("custom-metrics-card-editor", CustomMetricsCardEditor);

    window.customCards = window.customCards || [];
    window.customCards.push({
        type: "custom-metrics-card",
        name: "Custom Metrics Recorder",
        description: "List and add records for a Custom Metrics Recorder record type.",
    });
}

if (customElements.get("home-assistant")) {
    registerCustomMetricsCard();
} else {
    customElements.whenDefined("home-assistant").then(registerCustomMetricsCard);
}
