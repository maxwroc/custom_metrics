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
 */

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
  }

  setConfig(config) {
    if (!config || !config.record_type) {
      throw new Error("custom-metrics-card: 'record_type' is required in the card config");
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

      const recordsResponse = await this._hass.callWS({
        type: "custom_metrics/list_records",
        record_type: this._config.record_type,
      });
      this._records = (recordsResponse.records || []).sort(
        (a, b) => new Date(b.timestamp) - new Date(a.timestamp),
      );
    } catch (err) {
      this._error = err.message || String(err);
    } finally {
      this._loading = false;
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
      if (field.type === "image") {
        continue; // not supported by this card yet
      }
      const value = this._formValues[field.key];
      if (value === undefined || value === "") {
        continue;
      }
      fields[field.key] = field.type === "number" ? Number(value) : value;
    }

    this._error = null;
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
    if (field.type === "image") {
      return `<em>${field.label} (image fields are not yet supported by this card)</em>`;
    }
    if (field.type === "long_text") {
      return `<label>${field.label}<textarea data-key="${field.key}"></textarea></label>`;
    }
    if (field.type === "boolean") {
      return `<label><input type="checkbox" data-key="${field.key}" /> ${field.label}</label>`;
    }
    if (field.type === "datetime") {
      return `<label>${field.label}<input type="datetime-local" data-key="${field.key}" /></label>`;
    }
    if (field.type === "single_select" || field.type === "multi_select") {
      const options = (field.options || [])
        .map((option) => `<option value="${option}">${option}</option>`)
        .join("");
      const multiple = field.type === "multi_select" ? "multiple" : "";
      return `<label>${field.label}<select data-key="${field.key}" ${multiple}><option value=""></option>${options}</select></label>`;
    }
    const inputType = field.type === "number" ? "number" : "text";
    return `<label>${field.label}<input type="${inputType}" data-key="${field.key}" /></label>`;
  }

  _formatValue(value, field) {
    if (value === undefined || value === null) {
      return "";
    }
    if (field.type === "boolean") {
      return value ? "Yes" : "No";
    }
    if (Array.isArray(value)) {
      return value.join(", ");
    }
    return String(value);
  }

  _render() {
    if (!this.shadowRoot) {
      return;
    }
    if (!this._config) {
      this.shadowRoot.innerHTML = "";
      return;
    }

    const title =
      this._config.title || (this._recordType ? this._recordType.name : this._config.record_type);

    let bodyHtml;
    if (this._loading && !this._recordType) {
      bodyHtml = "<p>Loading...</p>";
    } else if (this._error) {
      bodyHtml = `<p class="error">${this._error}</p>`;
    } else if (!this._recordType) {
      bodyHtml = "<p>No data.</p>";
    } else {
      const fields = this._recordType.fields;
      const headerCells = fields.map((f) => `<th>${f.label}</th>`).join("");
      const rows = this._records
        .map((record) => {
          const cells = fields
            .map((f) => `<td>${this._formatValue(record[f.key], f)}</td>`)
            .join("");
          return `<tr>
            <td>${new Date(record.timestamp).toLocaleString()}</td>
            ${cells}
            <td><button class="delete-btn" data-id="${record.id}">Delete</button></td>
          </tr>`;
        })
        .join("");

      const formFields = fields
        .map((f) => `<div class="field">${this._renderFieldInput(f)}</div>`)
        .join("");

      bodyHtml = `
        <table>
          <thead><tr><th>Timestamp</th>${headerCells}<th></th></tr></thead>
          <tbody>${rows || `<tr><td colspan="${fields.length + 2}">No records yet.</td></tr>`}</tbody>
        </table>
        <form id="add-form">
          ${formFields}
          <button type="submit">Add record</button>
        </form>
      `;
    }

    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        table { width: 100%; border-collapse: collapse; margin-bottom: 12px; }
        th, td { text-align: left; padding: 4px 8px; border-bottom: 1px solid var(--divider-color, #e0e0e0); }
        form { display: flex; flex-wrap: wrap; gap: 8px; align-items: end; }
        .field { display: flex; flex-direction: column; }
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

customElements.define("custom-metrics-card", CustomMetricsCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "custom-metrics-card",
  name: "Custom Metrics Recorder",
  description: "List and add records for a Custom Metrics Recorder record type.",
});
