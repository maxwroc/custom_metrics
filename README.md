# Custom Metrics Recorder

A Home Assistant custom integration for recording your own user-defined
metrics — things Home Assistant doesn't track out of the box, like blood
pressure readings, fuel fill-up costs, or a photo from a doorbell press —
and exposing them for automations and a built-in dashboard card, without ever
touching YAML.

Example use cases:

- **Blood Pressure**: log `systolic` / `diastolic` / `pulse` after each
  reading.
- **Fuel Cost**: log `liters`, `price_per_liter`, `paid` every time you fill
  up.
- **Doorbell snapshot**: an automation saves a photo via `camera.snapshot`,
  then logs it here together with a "number of people" field.

## What it does

- Define your own **record types** (e.g. "Blood Pressure") and their fields
  (e.g. `systolic`, a required number) entirely through the UI.
- Save records via the `custom_metrics.add_record` service — call it by hand
  from Developer Tools, or from any automation.
- Records are stored forever by default; you can optionally cap them by age
  or count per record type.
- A bundled Lovelace card (`custom:custom-metrics-card`) lists and adds
  records for a record type — it registers itself automatically, no need to
  add a dashboard "Resource".
- Supports an `image` field type: automations (or the card's own form) can
  log a saved photo's file path, which is validated to exist and copied into
  managed storage, then shown in the card and in Home Assistant's Media
  browser.

## Installation

### HACS (recommended)

1. In HACS, add this repository as a custom repository (category:
   Integration): `https://github.com/maxwroc/custom_metrics`.
2. Install "Custom Metrics Recorder" and restart Home Assistant.

### Manual

Copy `custom_components/custom_metrics` into your Home Assistant
`config/custom_components/` directory and restart Home Assistant.

## Adding the integration

Settings → Devices & Services → **Add Integration** → search for
"Custom Metrics Recorder" → confirm. That's it — no YAML required at any
point, and only one instance is needed (it manages all of your record
types).

## Defining your first record type

On the Custom Metrics Recorder card (Settings → Devices & Services), click
**Add record type**.

Example — "Blood Pressure":

1. Name: `Blood Pressure`.
2. Add a field: key `systolic`, type `number`, required.
3. Choose "Add another field" and repeat for `diastolic` and `pulse`.
4. Finish — the record type is available immediately, no restart needed.

Supported field types: `number`, `text`, `long_text`, `boolean`, `datetime`,
`single_select`, `multi_select`, `image`. Select-type fields let you type a
comma-separated list of options.

Each record type shows up as its own row directly on the integration's card,
with its own **Configure**/**Rename**/**Delete** actions - no separate
"Options" dialog to dig through. Use the row's own **Rename** action to
rename the record type (its key stays the same); use **Configure** to manage
its fields (add fields, edit a field's label, delete a field), set its
retention period / maximum record count, export/import its data as CSV (see
[Backing up & restoring data](#backing-up--restoring-data-exportimport)
below), or (advanced, with a confirmation step) change the record type's or a
field's underlying key - useful if you want to see or clean up the key used
in automations, but be aware this can break existing automations/dashboards
that reference the old key.

## Adding records

### Manually, via Developer Tools

Developer Tools → Actions → call `custom_metrics.add_record`:

```yaml
action: custom_metrics.add_record
data:
  record_type: blood_pressure
  fields:
    systolic: 120
    diastolic: 80
    pulse: 65
```

Enable "Response data" to see the stored record (its generated `id` and
`timestamp` plus your fields).

### From automations

A smart-scale sensor changes state → log a weigh-in (useful if the scale is
shared, so a `name` field records who stepped on it):

```yaml
triggers:
  - trigger: state
    entity_id: sensor.smart_scale_weight
actions:
  - action: custom_metrics.add_record
    data:
      record_type: weight
      fields:
        name: Alex
        weight_kg: "{{ trigger.to_state.state }}"
```

A doorbell button press saves a snapshot, then logs it against an `image`
field (the field's value is a **filesystem path** to an already-saved image —
this integration copies it into its own managed storage, it does not decode
or validate the image contents). The path must resolve to somewhere inside
your Home Assistant config directory (e.g. under `/config`) — paths outside
of it are rejected:

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.doorbell_button
    to: "on"
actions:
  - action: camera.snapshot
    target:
      entity_id: camera.doorbell
    data:
      filename: /config/www/doorbell_snapshot.jpg
  - action: custom_metrics.add_record
    data:
      record_type: doorbell_visits
      fields:
        photo: /config/www/doorbell_snapshot.jpg
        people_count: 1
```

## Retention & growth

Records are **kept forever by default**. If you expect to log a record type
frequently via automations, you can optionally set, per record type:

- a **retention period** (in days), and/or
- a **maximum record count**.

Both are off ("unlimited"/"forever") unless you configure them. If a record
type's stored count grows past a configurable warning threshold (5,000 by
default), Home Assistant will raise a **Repairs** entry suggesting you
configure one of the above — it clears automatically once the count drops
back down.

## Backing up & restoring data (export/import)

Each record type's **Configure** menu has **Export data** and **Import
data** actions:

- **Export data** builds a download link for a CSV file of that record
  type's records. Tick **Include internal record ID** for a full backup
  (safe to re-import later — rows with an ID that already exists are
  skipped, not duplicated); untick it for a "data only" export (drops the
  ID, keeps the timestamp) suited to viewing/analyzing the data elsewhere,
  such as a spreadsheet.
- **Import data** uploads a CSV file (in the same format) and adds its rows
  to the record type. Rows whose ID already exists are skipped, as are rows
  with no ID whose timestamp and data exactly match an existing record (so
  re-importing a "data only" export doesn't create duplicates); malformed
  rows are reported and skipped without blocking the rest of the file.

The same operations are available as services for automation-driven
backups:

```yaml
action: custom_metrics.export_records
data:
  record_type: blood_pressure
  path: /config/www/blood_pressure_backup.csv
  include_id: true # default; set false to drop the ID column
```

Omit `path` (and enable "Response data") to get the CSV text back directly
instead of writing a file.

```yaml
action: custom_metrics.import_records
data:
  record_type: blood_pressure
  path: /config/www/blood_pressure_backup.csv
```

Provide exactly one of `path` or `content` (raw CSV text). Both services
require `path` to resolve inside your Home Assistant config directory.

## Viewing your data

Add a card to any dashboard with:

```yaml
type: custom:custom-metrics-card
record_type: blood_pressure
title: Blood Pressure
```

The card lists existing records for that record type and includes a small
form to add new ones. It's auto-registered by the integration, so you never
need to add anything under Settings → Dashboards → Resources.

All card config options:

| Option | Default | Description |
| --- | --- | --- |
| `record_type` | *(required)* | The record type id to show/add records for. |
| `title` | the record type's name | Card header text. |
| `last` | `20` | How many records to show, or how far back. Either a plain count (`last: 20`) or a duration - `30m`, `12h`, `3d`, `2w` (minutes/hours/days/weeks) - meaning "everything from that far back". Either way, at most 500 records are ever fetched. |
| `filter` | *(none)* | Only show/count records matching every condition - see "Filtering" below. |
| `show_form` | `true` | Show the "add record" form. |
| `show_list` | `true` | Show the table of existing records. |
| `show_delete` | `true` | Show a Delete button on each row. |

`show_form` and `show_list` can't both be `false` (there'd be nothing to
show). For example, a card meant only for quick data entry on a wall-mounted
tablet, showing just the last week and hiding delete buttons:

```yaml
type: custom:custom-metrics-card
record_type: blood_pressure
show_list: false
show_delete: false
last: 1w
```

### Filtering

Scope a card down to only the records you care about with `filter`: a list
of single-key `field: value` conditions, ALL of which must match (records
failing any condition are hidden). Filtering happens on the server, so it
also reduces what's fetched, not just what's displayed.

```yaml
type: custom:custom-metrics-card
record_type: weight
title: Max's Weight
filter:
  - name: Max
```

A plain value (e.g. `name: Max`) means "equals". Prefix the value with an
operator for anything else:

| Operator | Meaning | Works on |
| --- | --- | --- |
| `==` | equals *(default if no operator given)* | all field types |
| `!=` | not equals | all field types |
| `>`, `>=`, `<`, `<=` | greater/less than (or equal) | number, datetime |

For a multi-select field, `==`/`!=` check whether the value is (or isn't) one
of the record's selected options, e.g. `tags: "!= running"` matches records
where `running` is NOT among the selected tags.

```yaml
filter:
  - systolic: "> 130"
  - category: morning
```

Note: a text value that happens to start with an operator-like symbol (e.g.
`"> 100 degrees"`) will be misread as an operator - avoid starting a text
filter value with `==`, `!=`, `>`, `>=`, `<`, or `<`.

## Developer/automation reference

The service and the card both talk to the same backend, which is also
exposed over the WebSocket API for anyone building their own card:

- `custom_metrics/list_record_types` — returns the configured record types
  and their field definitions.
- `custom_metrics/list_records` — params: `record_type` (required), optional
  `start`/`end` ISO datetimes, optional `limit` (max rows, newest first; a
  server-side cap of 500 always applies even if you omit it or ask for more),
  optional `filter` (see "Filtering" above).
  Returns records as `{"id": ..., "timestamp": ..., ...your fields}`.
- `custom_metrics/add_record` — params: `record_type`, `fields`, optional
  `timestamp`. Same validation as the `add_record` service.
- `custom_metrics/delete_record` — params: `record_type`, `record_id`.

An `image`-type field's value in `list_records`/`add_record` responses is a
small object (e.g. `{"f": "<generated-filename>"}`), not the raw file. To
resolve it to something displayable, browse
`media-source://custom_metrics/<record_type>/<record_id>/<field_key>`.

## Uninstalling

Remove the integration from **Settings → Devices & Services** (not just by
deleting files via HACS) so its stored records and image files are cleaned up
from disk. A plain reload/restart never deletes your data — only an explicit
removal does.
