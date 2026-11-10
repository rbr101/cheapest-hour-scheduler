# Changelog

## [Unreleased]

### Added
- Per device record an optional **ready by** time. The record gets its own cheapest
  window (run time, or the block length) that ends before that time, e.g. 07:00.
- Sensor per device record with its planned start (`sensor.<name>_start_<entity>`).
- `waiting` attribute explaining why a device has no plan yet (e.g. waiting for prices).

### Changed
- Planning is done per device record; the shared block is still used by records
  without a ready-by time. Stored state format changed (old stored plans are ignored).

## [0.1.0] - 2026-11-10

### Added
- Initial integration: cheapest consecutive block from the Zonneplan price sensor,
  device records with start/end values, run time and offset, `auto` seconds-until-start
  for Home Connect `start_in_relative` numbers, sensors, binary sensor, switch and button.
