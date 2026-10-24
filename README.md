# Sobry_HA

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=atomtoto&repository=Sobry_HA&category=integration)

Sobry Energy integration for Home Assistant Community Store (HACS), based on the Sobry V2 API.

## Features

- Pulls electricity prices from `https://api.sobry.co/api/prices/raw`
- Supports Sobry pricing options (`segment`, `turpe`, `profil`, `display`)
- Supports Sobry API key mode for personalized prices (`/v2/user/daily-prices`)
- Creates Home Assistant sensors for:
  - Current price, which changes on every slot boundary, not only when the
    API is polled
  - Next price
  - Minimum, maximum and average price of the current day
- Exposes today's prices, and tomorrow's once published, in the `all_prices`
  attribute of the current price sensor (kept out of the recorder history:
  that list is too large for it)
- Drives appliances directly from the market price, without writing an automation
  (see [Appliance control](#appliance-control)), with a runtime that can
  [vary over the year](#a-runtime-that-varies), a run that can
  [finish its cycle](#finishing-the-cycle) on the appliance's own consumption,
  a price under which it [always runs](#always-run-below-a-price), a switch to
  [force it on](#forcing-the-appliance-on) by hand, and a fixed-schedule
  [safety net](#safety-net) when prices cannot be fetched

## Installation

### With the button (recommended)

Click the badge above ("Open your Home Assistant instance and open a repository
inside the Home Assistant Community Store"), then **Download**, and restart
Home Assistant.

### Manually (HACS custom repository)

1. In Home Assistant, open **HACS** → **Integrations** → **Custom repositories**
2. Add this repository URL with category **Integration**
3. Install **Sobry Energy**
4. Restart Home Assistant
5. Add **Sobry Energy** from **Settings** → **Devices & Services**


## Appliance control

The integration can decide *when* an appliance should run, and switch it on and
off itself. A water heater set to the 8 cheapest hours of the day no longer
needs a helper entity and an automation.

### Add an appliance

**Settings** → **Devices & Services** → **Sobry Energy** → **Configure** →
**Add an appliance**, then fill in:

| Field | Meaning |
| --- | --- |
| Name | Names the device and its entities, for example `Water heater` |
| Selection mode | How the slots are picked, see below |
| Entity to control | Optional. `switch`, `input_boolean`, `light`, `climate`, `water_heater`, `humidifier` or `fan` |
| Runtime | Hours to schedule per window, for example `8` |
| Entity giving the runtime | Optional. An `input_number`, `number` or sensor holding the hours to schedule. See [A runtime that varies](#a-runtime-that-varies) |
| Extra runtime after the cheap slots | Optional. Keeps running that long after the last selected slot, whatever the price |
| Keep running while this entity draws power | Optional. A power sensor of the appliance. See [Finishing the cycle](#finishing-the-cycle) |
| Power above which the appliance keeps running | Consumption under which the appliance is considered done, in watts |
| Maximum hold | Caps that extension, whatever the consumption reads |
| Window start / end | Daily window the slots are picked in. Same start and end means a full day |
| Maximum price | Optional cap: a slot above it is never selected, even if hours are missing |
| Price threshold | Used by the threshold mode |
| Always run below this price | Optional. Every slot at or under it runs on top of the plan, whatever the mode, window, runtime or maximum price. See [Always run below a price](#always-run-below-a-price) |
| Not on days with a negative price | Skips the price above on any day holding a negative price |
| Fallback start / end | Optional fixed schedule, used whenever the plan cannot decide. See [Safety net](#safety-net) |
| Wait for the whole window to be priced | Keeps the appliance off until every price of the window is published |

Each appliance becomes its own device. Add as many as you need, and edit or
remove them from the same menu.

### Selection modes

- **Cheapest slots of the window** — the N cheapest slots, consecutive or not.
  This is the classic "8 cheapest hours" behaviour.
- **Cheapest continuous block** — the cheapest single run of N hours, for a
  dishwasher or an EV charge that should not be interrupted.
- **Below a price threshold** — every slot under a price you set.

*Extra runtime* extends the last planned slot by a fixed duration, at whatever
the price is then. It is meant for an appliance whose state you cannot measure:
a water heater with no temperature reading gets its 8 cheap hours plus, say, one
more hour, to be sure it finished heating. The extension applies once per
window, after the last selected slot, and holds even when the price series
stops before its end.

### Always run below a price

Whatever the plan decided, some prices are too good to miss: a free or negative
price on a sunny afternoon is worth heating the water for, even when the plan
already scheduled its 8 hours tonight. Set *Always run below this price* — `0`
to catch every free and negative price — and the appliance also runs during
every slot priced at or under it.

- It comes on top of every other rule: whatever the selection mode, the daily
  window, the runtime (even `0`, or a month set to `0`), the maximum price, or
  a window still waiting for its prices.
- It also runs on top of the [safety net](#safety-net): a forced slot never
  stops the fixed schedule, and never hides that the plan is blind.
- A forced run is a bonus, not the run the appliance needs: it is neither
  extended by the *extra runtime* nor held on by the consumption.
- Turn on *Not on days with a negative price* to leave it out on any day
  (midnight to midnight) with at least one negative price: only the slots the
  plan selected run that day. A price of exactly `0` is not negative.
- Leave it empty to disable it. The *Automatic control* switch still wins:
  when it is off, the appliance is left alone.

While a slot runs for its price alone, the binary sensor carries
`force_active: true`, next to `force_price`. Forced slots are listed with the
others in `slots` and `windows`, and count in the planned hours and the
planned average price.

### Finishing the cycle

A water heater that still draws power has not finished heating, and how long it
needs changes with the incoming water temperature. Point *Keep running while
this entity draws power* at its power sensor: at the end of the last planned
window of the day the appliance keeps running while that sensor reads above the
threshold, and stops once it settles below.

- The reading has to stay below the threshold for two minutes before the
  appliance stops, so a single dip never cuts a heating cycle short. A power
  entity that is missing, unavailable or not numeric counts as below.
- *Maximum hold* caps the extension whatever the consumption says — a sensor
  stuck at 2 kW cannot run the appliance all day. Two hours by default, counted
  from the end of the planned window.
- Watts and kilowatts are both understood, from the entity's own unit.
- The hold applies only at the end of the last planned window of the day (of
  the window you configured, for an overnight one). An earlier window ends as
  planned, whatever the appliance still draws. It comes after any fixed *extra
  runtime*, and after a fallback window too.
- Consumption never *starts* anything: the hold only extends a run the plan
  started, and a Home Assistant restart ends an ongoing hold.

While the hold is on, the binary sensor stays `on` and carries
`power_hold_active`, `power_now` and `power_hold_until` in its attributes.

### A runtime that varies

A water heater needs fewer hours in July than in January. Two ways to follow
that, both optional:

**Per month** — **Configure** → **Runtime per month**, pick the appliance, and
fill in the months that differ. A month left empty uses the runtime configured
on the appliance, so filling in November to March is enough. A month set to `0`
keeps the appliance off for that month — a pool pump over winter, for instance.

**From an entity** — point *Entity giving the runtime* at an `input_number` you
set from a dashboard, or at any `number` or sensor whose value is a number of
hours. That covers everything a month cannot: a template sensor on the outdoor
temperature, on `sensor.season`, on the day of the week, on who is home. The
plan follows the entity as it changes, without waiting for the next price
refresh, and clamps the value to 0–24 h.

The runtime is resolved in that order: the entity if it holds a usable number,
then the month, then the runtime configured on the appliance. An entity that is
missing, unavailable or not numeric is logged once and the next source takes
over, so a broken template never leaves the appliance without a schedule.

While the runtime comes from a month or an entity, the tunable
`number.<name>_runtime` is replaced by the read-only `sensor.<name>_runtime`:
the value has a single source of truth, and the `hours_source` attribute of the
binary sensor says which one is in charge (`entity`, `month`, `default`, or
`manual` when you set it by hand).

### Entities created per appliance

| Entity | Role |
| --- | --- |
| `binary_sensor.<name>` | `on` while the appliance is scheduled to run. Carries the planned slots, the window, the average planned price and the period in its attributes |
| `sensor.<name>_next_start` | Start of the next planned run |
| `sensor.<name>_next_end` | End of the running window, or of the next one. While the appliance is held by its consumption, the latest moment it can stop |
| `sensor.<name>_planned_average_price` | Average price of the selected slots |
| `number.<name>_runtime` | Hours to schedule, tunable from a dashboard. Replaced by `sensor.<name>_runtime` when the runtime comes from a month or an entity |
| `number.<name>_price_threshold` | Threshold, for the threshold mode |
| `switch.<name>_automatic_control` | Created when an entity is controlled. Turn it off to take over manually; the appliance is left as it is, and the plan resumes when you turn it back on |
| `switch.<name>_force_on` | Turn it on to run the appliance now, whatever the plan says. See [Forcing the appliance on](#forcing-the-appliance-on) |
| `binary_sensor.<name>_fixed_schedule_fallback` | Created when a fallback schedule is set. `on` while the fixed schedule is in charge |

The schedule is re-evaluated every minute and on every price refresh, so slots
are honoured to the minute. On restart, the appliance is put back in the state
the plan asks for.

The binary sensor works on its own: leave *Entity to control* empty to keep
driving your appliance from your own automations.

```yaml
automation:
  - alias: Water heater
    triggers:
      - trigger: state
        entity_id: binary_sensor.water_heater
    actions:
      - action: homeassistant.turn_{{ 'on' if trigger.to_state.state == 'on' else 'off' }}
        target:
          entity_id: switch.water_heater_relay
```

### Forcing the appliance on

`switch.<name>_force_on` runs the appliance right away, on top of everything:
the plan, the prices, a missing API. It stays on until you turn it off, then
the plan takes over again at once — off outside the planned slots. It replaces
the `input_boolean` of a hand-made "force" automation.

- It works without an entity to control: the binary sensor turns `on`, and
  carries `boost_active: true`.
- *Automatic control* still wins: while it is off, the appliance is left alone.
- It survives a Home Assistant restart. It never arms the
  [consumption hold](#finishing-the-cycle).

### Safety net

An appliance that never runs because a price could not be fetched is worse than
one running at a bad hour. Set a **fallback start** and **fallback end** — your
old off-peak hours, for instance — and the plan switches to that fixed schedule
whenever it cannot decide:

- the Sobry API is unreachable, or answers with an error;
- the prices stop before the current moment (nothing fetched since yesterday);
- the window could not be filled: no price published for it yet, nothing under
  the threshold, or everything above the maximum price.

While the fallback is in charge, `binary_sensor.<name>_fixed_schedule_fallback`
is `on` and the main binary sensor carries `fallback_active: true` along with
the `reason` that caused it — alert on either to know you are flying blind. The
plan takes back over by itself on the next successful refresh.

The safety net also covers a Home Assistant restart while the API is down: as
soon as an appliance is configured, the entities are created even when the first
fetch fails, so the fixed schedule can drive the appliance. A plan with a
runtime of `0` hours is an explicit "do not run" and never triggers the
fallback, and an appliance whose automatic control switch is off is still left
alone.

### `sobry.get_cheapest_slots` service

Returns the cheapest slots of a window without creating an appliance, for
templates, scripts and one-off automations.

```yaml
action: sobry.get_cheapest_slots
data:
  hours: 3
  extra_hours: 1
  window_start: "22:00:00"
  window_end: "06:00:00"
response_variable: cheapest
```

The response holds `slots`, `windows`, `period_start`, `period_end`,
`planned_hours`, `average_price`, `next_start`, `active` and `data_complete`.
Pass `force_price` to add every slot at or under that price, whatever the
window: they are listed in `slots` and `windows`, and again in `forced_slots`.
Add `force_skip_negative: true` to leave it out on the days holding a negative
price.

### Notes on the price horizon

A plan only selects slots the API has already published: tomorrow's prices are
fetched as soon as they are, early in the afternoon, and the forecasts the
API-key endpoint returns before that are never planned on. *Wait for the whole
window to be priced* keeps the appliance off until the whole window is known,
so the cheapest slots are never missed by planning on half a day; the
`data_complete` and `reason` attributes of the binary sensor tell you when the
plan is waiting.

An overnight window such as `22:00` → `06:00` is therefore planned in the
afternoon, and stays planned after midnight: the prices of the evening before
are kept. Without an API key they are fetched again on every refresh; with
one they are kept in memory, so a restart of Home Assistant in the middle of
the night leaves the rest of that night waiting — set a fallback schedule to
cover it.

Days on which the clocks change are handled: a 23-hour day is complete with
23 hours of prices, a 25-hour day with 24 or 25.

## Notes

- Without API key, the integration keeps using the existing public endpoint mode.
- Default update interval is 15 minutes. Each refresh asks for yesterday,
  today and tomorrow.

## Sobry API key (personalized prices)

You can set an optional `api_key` in the integration configuration/options flow.

- When `api_key` is set, the integration uses:
  - `GET https://api.sobry.co/v2/user/daily-prices`
  - Header `Authorization: Bearer <api_key>`
  - No `contractId` query parameter
  - The V2 `date` and `time` response fields (Europe/Paris) to select the
    current and next prices; on the night the clocks go back, the second
    occurrence of a time is the repeated hour
  - `days=1`, for today and tomorrow
- When `api_key` is not set, the integration uses the previous endpoint/behavior.

The key is entered in a masked field. To remove it, clear the field in
**Configure** → **Pricing settings**: the integration goes back to the public
prices. When Sobry refuses the key — revoked or expired — Home Assistant asks
for a new one from **Settings** → **Devices & Services**; leave it empty there
to go back to the public prices.

The integration also supports v2 parameters internally:
- `granularity` mapped to `15m` or `1h`
- `taxMode` (`ttc`/`ht`)

## Releasing

Releases are published by the `Release` workflow, in one of two ways:

- push a tag: `git tag v0.6.0 && git push origin v0.6.0`;
- or **Actions** → **Release** → *Run workflow*, with the tag as input — the
  workflow creates the tag on the chosen branch itself.

It runs the planner tests first, refuses to publish when the tag does not match
the `version` of `custom_components/sobry/manifest.json` (so HACS and Home
Assistant never disagree on the version), and takes the release body from
`.github/release-notes/<tag>.md` when that file exists, falling back to
GitHub's generated notes. A markdown heading on the first line of that file
becomes the release title.

## Links
HACS Validation : https://github.com/atomtoto/Sobry_HACS/actions/runs/34109321499/job/101701623660
hassfest Validation : https://github.com/atomtoto/Sobry_HACS/actions/runs/34109321473/job/101701623516
