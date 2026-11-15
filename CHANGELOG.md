# Changelog

## [0.7.6] - 2026-11-15

### Fixed
- Action data that still had its `data:` line above the fields (without an `action:`
  line) was sent as one field called `data` and rejected ("not a valid option at 'data'").
  The fields under `data:` are sent now.

## [0.7.5] - 2026-11-15

### Fixed
- Editing a record that was already on (or whose window had begun) put the new start at
  the beginning of the running quarter, so **in the past**, and ran the action at once.
  The start is now always 5 minutes from now; a device that is on stays on meanwhile.
- **Action data pasted from the developer tools** (with `action:` and `data:` lines) made
  the action fail, because the wrapper was sent as data. Only what is under `data:` (and
  `target:`) is sent now.
- Card: long "Last sent" texts were cut off; they wrap now.

## [0.7.4] - 2026-11-15

### Added
- Card: **click a record's name** to show its properties: entity or action and data,
  values, ready by, run time, planned start and end, average price, status, and what was
  **last sent** to the device, including the error when that failed. With a link to the
  record's device page.
- Card: wide bars spell out the start time ("Start 10:10 – 14:10"); times follow the
  12/24-hour setting of your Home Assistant profile.
- The last thing sent per record (start, stop, test) and its result is stored.

### Changed
- A start that would fall in the past is now planned **5 minutes** from now (was 2 to 3).

## [0.7.3] - 2026-11-15

### Fixed
- A "start in X seconds" record (`auto` / `{seconds_until_start}`) that an older version
  had planned **in the past** stayed there: it showed as running, but its start signal
  was never sent, and neither editing it nor **Recalculate** planned it again. Such a
  record is now planned again from the current time and gets its start.
- **Saving a record in the edit form always replans it**, also the first time after an
  update and also when nothing was changed.

## [0.7.2] - 2026-11-15

### Fixed
- **A start is never planned in the past.** When the cheapest window has already begun
  (after adding a device, changing its ready-by time or run time, switching it on, or
  when the cheapest hours are right now), the start is set 2 to 3 minutes from now and
  the run time is kept. Before, the start was put at the beginning of the running
  quarter, and records using `auto` or `{seconds_until_start}` were then never started.
  A device that is already on when you edit it simply stays on.
- The **Recalculate** button gave an error (`unexpected keyword argument 'force'`).
- **Editing a record now always replans it**, also while it is running or already
  done. Before, a running record kept its old window until that ended.
- With *ready by*, the run for the next ready-by moment could be planned directly after
  the previous run in the same cheap hours. A run now belongs to the 24 hours before
  its ready-by moment.

## [0.7.1] - 2026-11-14

### Fixed
- The device form is **one screen again** with all fields (entity, value at start/end,
  action, action data, ready by, run time, offset). In 0.7.0 it was split in two steps
  and fields were hidden, so the on/off values, ready-by time and run time seemed gone.
  Stored records were not changed; they show their values again.

### Changed
- Example data for an action: choose an action, leave the data empty and submit; the
  form comes back once with the example data filled in. Your own data is never replaced.

## [0.7.0] - 2026-11-14

### Changed
- **Device records are now managed on the integration page** (config subentries):
  an **Add device** button, and every record has its own row with a settings icon to
  edit it and a menu to delete it. *Configure* now only holds the global settings
  (price sensor, block length).
- Adding/editing a record takes two steps: what to switch (description, entity and/or
  action), then the details. Fields that don't apply are no longer shown.
- Existing records are migrated automatically. Record ids, entities, devices, history
  and stored plans are kept.
- Requires Home Assistant 2025.6 or newer.

### Added
- When you pick an action, the action data is **pre-filled with the action's example
  data**, like *Fill example data* in the developer tools (all fields with an example,
  also in collapsed sections). Required fields without an example are added as a
  commented hint. Editing a record with the same action keeps your own data.

## [0.6.4] - 2026-11-14

### Added
- **Action** per record: call a service at the start instead of setting an entity,
  e.g. `home_connect.start_selected_program`. Pick it from a searchable list of all
  actions in your Home Assistant, or type it.
- **Action data** (YAML) per record, sent with the action. Supports
  `{seconds_until_start}` and `{minutes_until_start}`; with a placeholder the action
  is called ahead of time (like `auto`), without one at the start moment. Empty data
  calls the action without data.
- Form validation: entity or action required, action must exist, data must be valid YAML.

### Changed
- The entity is optional for records with an action. Test start runs the action.
- README: installation via the **Marketplace** (Settings → Marketplace → Add from link),
  which replaces HACS since Home Assistant 2026.11. HACS steps kept for older versions.

## [0.6.3] - 2026-11-14

### Added
- **Test start** and **Test stop** buttons on every record device. They run the
  scheduled start/end action right now so you can check the device reacts. The plan
  is not changed. Failures are shown as an error in the UI.

### Changed
- Scheduled actions on a non-existing entity, or with an unknown value, are now
  logged as errors (and shown in `last_error`) instead of a warning.

## [0.6.2] - 2026-11-10

### Added
- MIT license.

### Changed
- CI: HACS validation now also checks the brand icon (no checks ignored), as required
  for inclusion in the HACS default store.

## [0.6.1] - 2026-11-10

### Added
- **Description** per device record, shown everywhere instead of the entity name
  (devices, entities, card, attributes, options lists).
- Each device record is now **its own device** under the scheduler, with:
  - `switch.<description>_enabled` to turn the record on/off (stored across restarts;
    turning it off while running switches the device back right away),
  - `sensor.<description>_start` and `sensor.<description>_end`,
  - `binary_sensor.<description>_active`.
- Devices of deleted records are removed automatically.
- Card shows disabled records struck through.

### Changed
- The per-record start sensor moved to the record's device and is now called
  "Start" (entity id based on the description). Without a description and friendly
  name, the entity's object id is used ("Boiler" instead of "input_boolean.boiler").

## [0.2.2] - 2026-11-10

### Added
- Integration icon and logo (light and dark, 1x and 2x) in `brand/`, shown by Home
  Assistant 2026.3+. SVG sources and render script in `docs/brand/`.

## [0.2.1] - 2026-11-10

### Fixed
- The shared block could run again right after it ended (or twice a day) because the
  next block was searched directly from the end of the previous one. It now runs at
  most once a day: the next block starts at least 20 hours after the previous start.

### Added
- Home Assistant integration tests now run (config flow, switching, ready-by, stored
  prices, websocket) and are part of CI.

## [0.2.0] - 2026-11-10

### Added
- Lovelace card `custom:cheapest-hour-card`, shipped with and auto-registered by the
  integration: prices over time plus a bar per device for when it is on, ready-by
  flags, shared block and a "now" line. Screenshot in the README.
- Websocket command `cheapest_hour_scheduler/plan` providing prices and plans to the card.
- Per device record an optional **ready by** time. The record gets its own cheapest
  window (run time, or the block length) that ends before that time, e.g. 07:00.
- Sensor per device record with its planned start (`sensor.<name>_start_<entity>`).
- `waiting` attribute explaining why a device has no plan yet (e.g. waiting for prices).
- Price data is persisted. When the price sensor is unavailable (or after a restart while
  it is), planning and switching continue with the stored prices. New attributes
  `price_source` and `prices_known_until` on the block-start sensor.

### Changed
- Planning is done per device record; the shared block is still used by records
  without a ready-by time. Stored state format changed (old stored plans are ignored).

## [0.1.0] - 2026-11-10

### Added
- Initial integration: cheapest consecutive block from the Zonneplan price sensor,
  device records with start/end values, run time and offset, `auto` seconds-until-start
  for Home Connect `start_in_relative` numbers, sensors, binary sensor, switch and button.
