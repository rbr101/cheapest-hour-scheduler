# Cheapest Hour Scheduler

Home Assistant custom integration that finds the **cheapest consecutive block of hours** in a dynamic energy price sensor and switches devices on (and off again) during that block: battery chargers, boilers, heat-pump boost, washing machines, dishwashers…

Built for the Zonneplan sensor `sensor.zonneplan_current_tariff_rounded` (15-minute prices in the `prices_today` / `prices_tomorrow` attributes), but any sensor with attributes in this format works (hourly data too):

```yaml
prices_today:
  - time: '2026-11-10 00:00:00+01:00'
    price: 0.42
  - time: '2026-11-10 00:15:00+01:00'
    price: 0.42
prices_tomorrow:
  - ...
```

## Installation

### HACS (custom repository)
1. HACS → ⋮ → *Custom repositories* → add `https://github.com/rbr101/cheapest-hour-scheduler`, type **Integration**.
2. Install **Cheapest Hour Scheduler** and restart Home Assistant.

### Manual
Copy `custom_components/cheapest_hour_scheduler` to `<config>/custom_components/` and restart.

## Configuration

*Settings → Devices & services → Add integration → Cheapest Hour Scheduler*

| Field | Meaning |
|---|---|
| Price sensor | e.g. `sensor.zonneplan_current_tariff_rounded` |
| Block length (hours) | Size of the cheapest consecutive block. Quarters allowed (`2.5`). |

You can add the integration several times, e.g. a 1-hour block for the dishwasher and a 4-hour block for the battery.

### Device records
Open the integration → **Configure** → *Add device*. Each record:

| Field | Example | Meaning |
|---|---|---|
| Entity | `switch.battery_charger` | What to control |
| Value at start | `on` | Value set at block start (+ offset) |
| Value at end | `off` | Optional. Set after the run time. Empty = do nothing. |
| Run time (h) | `2` | Optional. Empty = until the end of the block. |
| Start offset (min) | `30` | Optional. Start N minutes after the block starts. |

How the value is applied depends on the entity domain:

| Domain | Value | Service |
|---|---|---|
| `switch`, `light`, `input_boolean`, `fan`, … | `on` / `off` | `turn_on` / `turn_off` |
| `number`, `input_number` | `16` | `set_value` |
| `number`, `input_number` | `auto` | `set_value` with **seconds until the start** |
| `select`, `input_select` | `eco` | `select_option` |
| `button`, `input_button` | anything | `press` |
| `script` | `on` | `turn_on` |
| `climate` | `on`/`off` or an HVAC mode | `turn_on`/`turn_off`/`set_hvac_mode` |

#### Dishwasher example (Home Connect)
`number.keuken_dishwasher_start_in_relative` expects the number of seconds until the program starts.

* Entity: `number.keuken_dishwasher_start_in_relative`
* Value at start: `auto` (or `{seconds_until_start}` if you need it inside a template-like value)
* Value at end: *(empty)*

As soon as a block is planned the integration writes the seconds from *now* until the block start (rounded down to whole minutes). If the plan moves (e.g. when tomorrow's prices arrive) the new value is sent again. After the start moment the value is not touched anymore, the appliance runs its own program. You still need to have the dishwasher in remote-start mode / start the program as usual.

## How planning works

* The cheapest block is the consecutive window (of the configured length) with the **lowest average price**, starting no earlier than the slot that is running now. On a tie the earliest window wins.
* Replanning happens when the price sensor changes, every 5 minutes, and with the *Recalculate* button.
* A block that **has started is locked**; it won't move even if new prices arrive.
* After a block ends, the next block is searched from the end of the previous one (so it only runs once per available period of price data; typically one block per day once tomorrow's prices are published).
* The plan and which actions were already executed are stored, so a restart doesn't switch things twice. If HA was down at the start moment but the run time has not passed yet, the start action is applied as soon as HA is back.

## Entities

| Entity | Description |
|---|---|
| `sensor.<name>_block_start` | Start of the planned block (attribute `planned_actions` lists every device action) |
| `sensor.<name>_block_end` | End of the planned block |
| `sensor.<name>_block_average_price` | Average price of the block, with per-slot prices as attributes |
| `binary_sensor.<name>_cheapest_block_active` | On during the block — handy for your own automations |
| `switch.<name>_scheduler_enabled` | Master switch; off = no device actions |
| `button.<name>_recalculate` | Force a recalculation |

## Development

```bash
pip install pytest pyyaml
pytest tests/test_planner.py
```
