# Changelog

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
