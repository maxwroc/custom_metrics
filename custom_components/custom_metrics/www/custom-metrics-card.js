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
 */

const DEFAULT_LAST_COUNT = 20;
const LAST_DURATION_RE = /^(\d+)(m|h|d|w)$/i;
const DURATION_UNIT_MS = { m: 60_000, h: 3_600_000, d: 86_400_000, w: 604_800_000 };

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
        this._config = config;
        this._recordType = null;
        this._records = [];
        this._render();
    }

    set hass(hass) {
        const firstRun = !this._hass;
        this._hass = hass;
        if (firstRun) {
            this._loadData();
        }
    }

    getCardSize() {
        return 3 + Math.ceil((this._records || []).length / 2);
    }

    async _loadData() {
        if (!this._hass || !this._config) {
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
            if (!recordType) {
                throw new Error(`Unknown record_type '${this._config.record_type}'`);
            }
            this._recordType = recordType;

            const last = parseLast(this._config.last);
            const recordsResponse = await this._hass.callWS({
                type: "custom_metrics/list_records",
                record_type: this._config.record_type,
                ...(last.type === "count"
                    ? { limit: last.value }
                    : { start: new Date(Date.now() - last.ms).toISOString() }),
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
            const fields = this._recordType.fields;
            let tableHtml = "";
            if (showList) {
                const headerCells = fields.map((f) => `<th>${escapeHtml(f.label)}</th>`).join("");
                const deleteHeader = showDelete ? "<th></th>" : "";
                const rows = this._records
                    .map((record) => {
                        const cells = fields
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
                const colspan = fields.length + 1 + (showDelete ? 1 : 0);

                tableHtml = `
        <table>
          <thead><tr><th>Timestamp</th>${headerCells}${deleteHeader}</tr></thead>
          <tbody>${rows || `<tr><td colspan="${colspan}">No records yet.</td></tr>`}</tbody>
        </table>
      `;
            }

            let formHtml = "";
            if (showForm) {
                const formFields = fields
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
