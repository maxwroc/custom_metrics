# Custom Metrics Recorder

A Home Assistant custom integration for recording your own user-defined
metrics — things Home Assistant doesn't track out of the box, like blood
pressure readings, fuel fill-up costs, or a photo from a doorbell press — and
exposing them to automations and a built-in dashboard card, without ever
touching YAML.

You define your own **record types** (e.g. "Blood Pressure") and their typed
**fields** (e.g. `systolic`, a required number) entirely through the UI, then
log timestamped **records** against them — by hand, from the card, or from any
automation.

Example use cases:

- **Blood Pressure** — log `systolic` / `diastolic` / `pulse` after each reading.
- **Fuel Cost** — log `liters`, `price_per_liter`, `paid` every fill-up.
- **Doorbell snapshot** — an automation saves a photo, then logs it here with a
  "number of people" field.

## Why not just use native Home Assistant entities?

Native helpers and sensors work well for live automation, but fall short when
you want to keep and manage a personal log of readings. This integration was
built to solve exactly that:

- **Keep the exact values forever.** Home Assistant's full-resolution history
  is purged after a while (10 days by default), and its *long-term statistics*
  only retain **hourly aggregates** (min/max/mean) — your individual atomic
  readings are gone. Custom Metrics stores every record you add, unchanged,
  with no downsampling, for as long as you want.
- **Log readings with a custom timestamp.** Record a measurement *as of when it
  actually happened* — backfilling an old reading — not just "now" like a
  state change.
- **Per-record-type retention rules.** Choose how long (or how many) records to
  keep for each record type independently, instead of one global recorder
  purge for the whole instance.
- **Easy CSV export** of a record type's data for backup or analysis.
- **Easy CSV import** to restore or bulk-load records.
- **One structured record instead of scattered entities.** A reading with
  several fields (e.g. systolic + diastolic + pulse) is a single record, not a
  handful of unrelated helpers that are hard to keep together and manage.

This integration gives you a proper **multi-field, append-only log per record
type** — real timestamped rows with typed fields (numbers, text, booleans,
dates, selects, even images) stored in an integration-owned SQLite database,
all configured from the UI.

## Installation

### HACS (recommended)

1. In HACS, add this repository as a custom repository (category:
   **Integration**): `https://github.com/maxwroc/custom_metrics`.
2. Install "Custom Metrics Recorder" and restart Home Assistant.

### Manual

Copy `custom_components/custom_metrics` into your Home Assistant
`config/custom_components/` directory and restart Home Assistant.

### Add the integration

Settings → Devices & Services → **Add Integration** → search for "Custom
Metrics Recorder" → confirm. No YAML required, and only one instance is needed
(it manages all of your record types).

## Basic usage

### 1. Create a record type

On the Custom Metrics Recorder card (Settings → Devices & Services), click **Add
record type**, give it a name (e.g. `Blood Pressure`), and add fields (name,
type, required?). Supported field types: `number`, `text`, `long_text`,
`boolean`, `datetime`, `single_select`, `multi_select`, `image`. It's available
immediately — no restart.

### 2. Add a record

Add records from the dashboard card's **Add record** button, by hand from
Developer Tools → Actions, or from an automation — all via the
`custom_metrics.add_record` action:

```yaml
action: custom_metrics.add_record
data:
  record_type: blood_pressure
  fields:
    systolic: 120
    diastolic: 80
    pulse: 65
```

## The dashboard card

Add the bundled card to any dashboard — it auto-registers, so you don't need to
add a dashboard "Resource":

```yaml
type: custom:custom-metrics-card
record_type: blood_pressure
title: Blood Pressure
```

It lists existing records and has an **Add record** button that opens a form.
See the [wiki](https://github.com/maxwroc/custom_metrics/wiki/Dashboard-Card)
for all card options (filtering, columns, read-only mode, etc.).

<!-- TODO: add a screenshot of the card at docs/images/card-example.png and reference it here, e.g.:
![Custom Metrics card](docs/images/card-example.png)
-->

## Documentation

Full documentation lives in the
**[project wiki](https://github.com/maxwroc/custom_metrics/wiki)**:

- [Installation](https://github.com/maxwroc/custom_metrics/wiki/Installation)
- [Creating a record type](https://github.com/maxwroc/custom_metrics/wiki/Creating-a-Record-Type)
  (field types, retention, limits)
- [Creating records from automations](https://github.com/maxwroc/custom_metrics/wiki/Automations-Creating-Records)
- [Backing up records to CSV](https://github.com/maxwroc/custom_metrics/wiki/Automations-CSV-Backups)
- [The dashboard card](https://github.com/maxwroc/custom_metrics/wiki/Dashboard-Card)
  (all config options)
- [Development](https://github.com/maxwroc/custom_metrics/wiki/Development),
  [custom card development](https://github.com/maxwroc/custom_metrics/wiki/Custom-Card-Development),
  and the
  [WebSocket API reference](https://github.com/maxwroc/custom_metrics/wiki/WebSocket-API-Reference)

## Uninstalling

Remove the integration from **Settings → Devices & Services** (not just by
deleting files via HACS) so its stored records and image files are cleaned up
from disk. A plain reload/restart never deletes your data — only an explicit
removal does.
