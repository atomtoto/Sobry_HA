"""Tests for the Sobry planning helpers.

The planner has no Home Assistant dependency, so it is loaded directly from
its path and can be exercised with ``python3 -m unittest``.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
import importlib.util
from pathlib import Path
import sys
import unittest
from zoneinfo import ZoneInfo

_MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "custom_components" / "sobry" / "planner.py"
)
_SPEC = importlib.util.spec_from_file_location("sobry_planner", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
planner = importlib.util.module_from_spec(_SPEC)
sys.modules["sobry_planner"] = planner
_SPEC.loader.exec_module(planner)

PARIS = ZoneInfo("Europe/Paris")


def _series(day: str, prices: list[float], step_minutes: int = 60) -> list[dict]:
    """Build raw API entries starting at local midnight of ``day``."""
    start = datetime.fromisoformat(f"{day}T00:00:00").replace(tzinfo=PARIS)
    return [
        {
            "timestamp": (start + timedelta(minutes=step_minutes * index))
            .astimezone(UTC)
            .isoformat(),
            "price": price,
        }
        for index, price in enumerate(prices)
    ]


def _local(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=PARIS)


def _hours_of(result) -> list[int]:
    return sorted(slot.start.astimezone(PARIS).hour for slot in result.selected)


class TestSlots(unittest.TestCase):
    def test_build_slots_infers_step(self) -> None:
        slots = planner.build_slots(_series("2026-01-15", [1.0] * 96, step_minutes=15))
        self.assertEqual(len(slots), 96)
        self.assertEqual(slots[0].duration, timedelta(minutes=15))
        self.assertEqual(slots[-1].end - slots[0].start, timedelta(days=1))

    def test_build_slots_handles_holes_and_duplicates(self) -> None:
        raw = _series("2026-01-15", [1.0, 2.0, 3.0])
        raw.append(dict(raw[1]))  # duplicated timestamp
        raw.append(
            {
                "timestamp": _local("2026-01-15T06:00:00").astimezone(UTC).isoformat(),
                "price": 4.0,
            }
        )
        slots = planner.build_slots(raw)
        self.assertEqual(len(slots), 4)
        self.assertTrue(all(slot.duration == timedelta(hours=1) for slot in slots))

    def test_build_slots_skips_unpublished_and_forecast_prices(self) -> None:
        raw = _series("2026-01-15", [1.0, 2.0, 3.0, 4.0])
        raw[1]["price"] = None  # placeholder of an hour not published yet
        raw[2]["estimated"] = True  # forecast beyond the day-ahead
        slots = planner.build_slots(raw)
        self.assertEqual([slot.price for slot in slots], [1.0, 4.0])

    def test_merge_price_entries_keeps_the_evening_before(self) -> None:
        yesterday = _series("2026-01-15", [0.30] * 24)
        today = _series("2026-01-16", [0.20] * 24)
        updated = _series("2026-01-16", [0.10] * 24)
        merged = planner.merge_price_entries(
            yesterday + today, updated, _local("2026-01-15T12:00:00")
        )
        self.assertEqual(len(merged), 36)
        self.assertEqual(
            planner.entry_start(merged[0]), _local("2026-01-15T12:00:00")
        )
        # The latest prices win over the ones they replace.
        self.assertEqual({item["price"] for item in merged[12:]}, {0.10})

    def test_extract_price_fallbacks(self) -> None:
        self.assertEqual(planner.extract_price({"price": "0.12"}), 0.12)
        self.assertEqual(planner.extract_price({"price_ttc_eur_kwh": 0.2}), 0.2)
        self.assertAlmostEqual(planner.extract_price({"spot_price": 80}), 0.08)
        self.assertIsNone(planner.extract_price({"nope": 1}))


class TestPeriodBounds(unittest.TestCase):
    def test_full_day(self) -> None:
        start, end = planner.period_bounds(
            _local("2026-01-15T10:00:00"), time(0, 0), time(0, 0), PARIS
        )
        self.assertEqual(start, _local("2026-01-15T00:00:00"))
        self.assertEqual(end, _local("2026-01-16T00:00:00"))

    def test_day_starting_at_six(self) -> None:
        start, _end = planner.period_bounds(
            _local("2026-01-15T02:00:00"), time(6, 0), time(6, 0), PARIS
        )
        self.assertEqual(start, _local("2026-01-14T06:00:00"))

    def test_daytime_window_rolls_to_tomorrow(self) -> None:
        start, end = planner.period_bounds(
            _local("2026-01-15T13:00:00"), time(8, 0), time(12, 0), PARIS
        )
        self.assertEqual(start, _local("2026-01-16T08:00:00"))
        self.assertEqual(end, _local("2026-01-16T12:00:00"))

    def test_overnight_window(self) -> None:
        start, end = planner.period_bounds(
            _local("2026-01-15T23:00:00"), time(22, 0), time(6, 0), PARIS
        )
        self.assertEqual(start, _local("2026-01-15T22:00:00"))
        self.assertEqual(end, _local("2026-01-16T06:00:00"))

        start, end = planner.period_bounds(
            _local("2026-01-15T03:00:00"), time(22, 0), time(6, 0), PARIS
        )
        self.assertEqual(start, _local("2026-01-14T22:00:00"))
        self.assertEqual(end, _local("2026-01-15T06:00:00"))


class TestPlanning(unittest.TestCase):
    def setUp(self) -> None:
        # Cheapest hours of the day: 0h to 5h (0.05) then 13h to 15h (0.06).
        prices = [0.05] * 6 + [0.30] * 7 + [0.06] * 3 + [0.40] * 8
        self.slots = planner.build_slots(_series("2026-01-15", prices))

    def test_cheapest_slots_picks_eight_cheapest_hours(self) -> None:
        result = planner.build_plan(
            self.slots,
            _local("2026-01-15T09:00:00"),
            PARIS,
            mode=planner.MODE_CHEAPEST_SLOTS,
            hours=8,
        )
        self.assertTrue(result.data_complete)
        self.assertEqual(_hours_of(result), [0, 1, 2, 3, 4, 5, 13, 14])
        self.assertEqual(result.selected_hours, 8)
        self.assertEqual(len(result.windows), 2)
        self.assertFalse(result.is_active(_local("2026-01-15T09:00:00")))
        self.assertTrue(result.is_active(_local("2026-01-15T13:30:00")))
        self.assertEqual(
            result.next_transition(_local("2026-01-15T09:00:00")),
            _local("2026-01-15T13:00:00"),
        )
        self.assertEqual(
            result.next_transition(_local("2026-01-15T13:30:00")),
            _local("2026-01-15T15:00:00"),
        )

    def test_cheapest_block_is_contiguous(self) -> None:
        result = planner.build_plan(
            self.slots,
            _local("2026-01-15T09:00:00"),
            PARIS,
            mode=planner.MODE_CHEAPEST_BLOCK,
            hours=8,
        )
        self.assertEqual(len(result.windows), 1)
        self.assertEqual(result.windows[0].start, _local("2026-01-15T00:00:00"))
        self.assertEqual(result.windows[0].hours, 8)

    def test_max_price_caps_the_selection(self) -> None:
        result = planner.build_plan(
            self.slots,
            _local("2026-01-15T09:00:00"),
            PARIS,
            mode=planner.MODE_CHEAPEST_SLOTS,
            hours=10,
            max_price=0.10,
        )
        self.assertEqual(_hours_of(result), [0, 1, 2, 3, 4, 5, 13, 14, 15])
        self.assertEqual(result.selected_hours, 9)

    def test_threshold_mode(self) -> None:
        result = planner.build_plan(
            self.slots,
            _local("2026-01-15T09:00:00"),
            PARIS,
            mode=planner.MODE_THRESHOLD,
            threshold_price=0.055,
        )
        self.assertEqual(_hours_of(result), [0, 1, 2, 3, 4, 5])

    def test_incomplete_data_holds_off(self) -> None:
        partial = planner.build_slots(_series("2026-01-15", [0.05] * 4))
        result = planner.build_plan(
            partial,
            _local("2026-01-15T01:00:00"),
            PARIS,
            hours=2,
        )
        self.assertFalse(result.data_complete)
        self.assertEqual(result.selected, [])
        self.assertEqual(result.reason, "waiting_for_prices")

        allowed = planner.build_plan(
            partial,
            _local("2026-01-15T01:00:00"),
            PARIS,
            hours=2,
            require_complete_data=False,
        )
        self.assertEqual(allowed.selected_hours, 2)

    def test_overnight_window_uses_tomorrow_prices(self) -> None:
        prices = [0.30] * 22 + [0.20, 0.20]
        slots = planner.build_slots(
            _series("2026-01-15", prices) + _series("2026-01-16", [0.01] * 24)
        )
        result = planner.build_plan(
            slots,
            _local("2026-01-15T21:00:00"),
            PARIS,
            hours=4,
            window_start=time(22, 0),
            window_end=time(6, 0),
        )
        self.assertTrue(result.data_complete)
        self.assertEqual(_hours_of(result), [0, 1, 2, 3])
        self.assertEqual(result.windows[0].start, _local("2026-01-16T00:00:00"))

    def test_overnight_window_survives_midnight(self) -> None:
        evening = _series("2026-01-15", [0.30] * 24)
        night = _series("2026-01-16", [0.30] * 2 + [0.01] * 2 + [0.30] * 20)
        before = planner.build_plan(
            planner.build_slots(evening + night),
            _local("2026-01-15T23:50:00"),
            PARIS,
            hours=2,
            window_start=time(22, 0),
            window_end=time(6, 0),
        )
        # After midnight the API only serves the new day: the evening before
        # is kept from the previous refresh, and the night is still planned.
        kept = planner.merge_price_entries(
            evening + night, night, _local("2026-01-15T00:00:00")
        )
        after = planner.build_plan(
            planner.build_slots(kept),
            _local("2026-01-16T00:20:00"),
            PARIS,
            hours=2,
            window_start=time(22, 0),
            window_end=time(6, 0),
        )
        for result in (before, after):
            self.assertTrue(result.data_complete)
            self.assertEqual(_hours_of(result), [2, 3])

    def test_short_day_is_complete(self) -> None:
        # 28 March 2027: the clocks go forward, the day lasts 23 hours.
        start = _local("2027-03-28T00:00:00").astimezone(UTC)
        raw = [
            {"timestamp": (start + timedelta(hours=index)).isoformat(), "price": 0.1}
            for index in range(23)
        ]
        result = planner.build_plan(
            planner.build_slots(raw), _local("2027-03-28T10:00:00"), PARIS, hours=4
        )
        self.assertTrue(result.data_complete)
        self.assertEqual(result.selected_hours, 4)

    def test_long_day_is_complete(self) -> None:
        # 25 October 2026: the clocks go back, the day lasts 25 hours. It is
        # complete with them all, and without the repeated hour as well.
        start = _local("2026-10-25T00:00:00").astimezone(UTC)
        for count, skipped in ((25, None), (24, 2)):
            raw = [
                {"timestamp": (start + timedelta(hours=index)).isoformat(), "price": 0.1}
                for index in range(count + (skipped is not None))
                if index != skipped
            ]
            result = planner.build_plan(
                planner.build_slots(raw), _local("2026-10-25T10:00:00"), PARIS, hours=4
            )
            self.assertTrue(result.data_complete, count)

    def test_overnight_window_across_the_clock_change(self) -> None:
        # 22:00 -> 06:00 on the night the clocks go forward lasts 7 hours.
        start = _local("2027-03-27T22:00:00").astimezone(UTC)
        raw = [
            {"timestamp": (start + timedelta(hours=index)).isoformat(), "price": 0.1}
            for index in range(7)
        ]
        result = planner.build_plan(
            planner.build_slots(raw),
            _local("2027-03-27T21:00:00"),
            PARIS,
            hours=2,
            window_start=time(22, 0),
            window_end=time(6, 0),
        )
        self.assertTrue(result.data_complete)
        window = planner.PlanWindow(result.period_start, result.period_end, 0.0)
        self.assertEqual(window.hours, 7)

    def test_next_period_is_planned_when_available(self) -> None:
        slots = planner.build_slots(
            _series("2026-01-15", [0.10] * 24) + _series("2026-01-16", [0.20] * 24)
        )
        result = planner.build_plan(
            slots,
            _local("2026-01-15T23:30:00"),
            PARIS,
            hours=2,
        )
        # Ties are resolved on the earliest slot, so today runs at 00:00 and
        # tomorrow's occurrence is planned too.
        self.assertEqual(
            result.next_transition(_local("2026-01-15T23:30:00")),
            _local("2026-01-16T00:00:00"),
        )
        self.assertEqual(result.selected_hours, 4)

    def test_extra_hours_extend_the_last_window(self) -> None:
        result = planner.build_plan(
            self.slots,
            _local("2026-01-15T09:00:00"),
            PARIS,
            hours=6,
            extra_hours=2,
        )
        # 6 cheapest hours are 00:00 -> 06:00, extended to 08:00.
        self.assertEqual(len(result.windows), 1)
        self.assertEqual(result.windows[0].start, _local("2026-01-15T00:00:00"))
        self.assertEqual(result.windows[0].end, _local("2026-01-15T08:00:00"))
        self.assertEqual(result.scheduled_hours, 8)
        self.assertTrue(result.is_active(_local("2026-01-15T07:30:00")))
        # The expensive extension is taken into account in the average price.
        self.assertGreater(result.average_price, 0.05)

    def test_extra_hours_extend_past_the_price_series(self) -> None:
        prices = [0.30] * 23 + [0.01]
        slots = planner.build_slots(_series("2026-01-15", prices))
        result = planner.build_plan(
            slots,
            _local("2026-01-15T09:00:00"),
            PARIS,
            hours=1,
            extra_hours=2,
        )
        self.assertEqual(result.windows[0].start, _local("2026-01-15T23:00:00"))
        self.assertEqual(result.windows[0].end, _local("2026-01-16T02:00:00"))
        self.assertTrue(result.is_active(_local("2026-01-16T01:00:00")))

    def test_extra_hours_merge_with_the_next_period(self) -> None:
        slots = planner.build_slots(
            _series("2026-01-15", [0.30] * 23 + [0.01])
            + _series("2026-01-16", [0.01] + [0.30] * 23)
        )
        result = planner.build_plan(
            slots,
            _local("2026-01-15T09:00:00"),
            PARIS,
            hours=1,
            extra_hours=1,
        )
        # Today runs 23:00 -> 00:00 plus one extra hour, tomorrow runs
        # 00:00 -> 01:00 plus its own extra hour: one single window.
        self.assertEqual(len(result.windows), 1)
        self.assertEqual(result.windows[0].start, _local("2026-01-15T23:00:00"))
        self.assertEqual(result.windows[0].end, _local("2026-01-16T02:00:00"))

    def test_force_price_runs_on_top_of_the_plan(self) -> None:
        result = planner.build_plan(
            self.slots,
            _local("2026-01-15T09:00:00"),
            PARIS,
            hours=2,
            max_price=0.01,
            force_price=0.06,
        )
        # Nothing is under the maximum price, yet every slot of the period at
        # or under the force price still runs.
        self.assertEqual(result.reason, "above_max_price")
        self.assertEqual(result.planned_windows, [])
        self.assertEqual(_hours_of(result), [0, 1, 2, 3, 4, 5, 13, 14, 15])
        self.assertEqual(result.forced, result.selected)
        self.assertTrue(result.is_active(_local("2026-01-15T13:30:00")))
        self.assertTrue(result.is_forced(_local("2026-01-15T13:30:00")))
        self.assertIsNone(result.planned_window(_local("2026-01-15T13:30:00")))

    def test_force_price_adds_to_the_selection(self) -> None:
        result = planner.build_plan(
            self.slots,
            _local("2026-01-15T00:30:00"),
            PARIS,
            hours=2,
            extra_hours=1,
            force_price=0.06,
        )
        # The plan picks 00:00 -> 02:00, extended to 03:00; the force price
        # adds the rest of the cheap night and 13:00 -> 16:00, unextended.
        self.assertEqual(
            [(window.start, window.end) for window in result.planned_windows],
            [(_local("2026-01-15T00:00:00"), _local("2026-01-15T03:00:00"))],
        )
        self.assertEqual(
            [(window.start, window.end) for window in result.windows],
            [
                (_local("2026-01-15T00:00:00"), _local("2026-01-15T06:00:00")),
                (_local("2026-01-15T13:00:00"), _local("2026-01-15T16:00:00")),
            ],
        )
        self.assertEqual(
            sorted(slot.start.astimezone(PARIS).hour for slot in result.forced),
            [3, 4, 5, 13, 14, 15],
        )
        self.assertFalse(result.is_forced(_local("2026-01-15T02:30:00")))
        self.assertTrue(result.is_forced(_local("2026-01-15T03:30:00")))

    def test_force_price_ignores_the_window_and_missing_prices(self) -> None:
        # Tonight's window waits for tomorrow's prices, and the runtime is
        # zero: the cheap afternoon still runs, the morning is over.
        result = planner.build_plan(
            self.slots,
            _local("2026-01-15T09:00:00"),
            PARIS,
            hours=0,
            window_start=time(22, 0),
            window_end=time(6, 0),
            force_price=0.06,
        )
        self.assertEqual(result.reason, "waiting_for_prices")
        self.assertEqual(result.planned_windows, [])
        self.assertEqual(_hours_of(result), [13, 14, 15])

    def test_without_force_price_every_window_is_planned(self) -> None:
        result = planner.build_plan(
            self.slots,
            _local("2026-01-15T09:00:00"),
            PARIS,
            hours=8,
        )
        self.assertEqual(result.forced, [])
        self.assertEqual(result.planned_windows, result.windows)

    def _negative_day_slots(self) -> list:
        # The 15th holds one negative hour, the 16th none.
        today = [0.05] * 6 + [0.30] * 6 + [-0.02] + [0.30] * 3 + [0.06] * 3 + [0.40] * 5
        tomorrow = [0.05] * 6 + [0.30] * 18
        return planner.build_slots(
            _series("2026-01-15", today) + _series("2026-01-16", tomorrow)
        )

    def test_force_price_still_runs_on_a_day_with_a_negative_price(self) -> None:
        result = planner.build_plan(
            self._negative_day_slots(),
            _local("2026-01-15T09:00:00"),
            PARIS,
            hours=2,
            force_price=0.06,
        )
        days = {slot.start.astimezone(PARIS).day for slot in result.forced}
        self.assertEqual(days, {15, 16})

    def test_force_price_is_skipped_on_a_day_with_a_negative_price(self) -> None:
        result = planner.build_plan(
            self._negative_day_slots(),
            _local("2026-01-15T09:00:00"),
            PARIS,
            hours=2,
            force_price=0.06,
            force_skip_negative=True,
        )
        # The 15th has a negative price: only the plan runs. The 16th has
        # none, so its cheap night runs on top of the two hours it planned.
        forced = [slot.start.astimezone(PARIS) for slot in result.forced]
        self.assertEqual({start.day for start in forced}, {16})
        self.assertEqual(sorted(start.hour for start in forced), [2, 3, 4, 5])
        today = [
            slot.start.astimezone(PARIS).hour
            for slot in result.selected
            if slot.start.astimezone(PARIS).day == 15
        ]
        self.assertEqual(today, [0, 12])
        self.assertFalse(result.is_forced(_local("2026-01-15T14:00:00")))
        self.assertFalse(result.is_active(_local("2026-01-15T14:00:00")))
        self.assertTrue(result.is_active(_local("2026-01-15T12:30:00")))

    def test_skip_negative_days_ignores_a_zero_price(self) -> None:
        prices = [0.0] + [0.05] * 5 + [0.30] * 18
        result = planner.build_plan(
            planner.build_slots(_series("2026-01-15", prices)),
            _local("2026-01-15T09:00:00"),
            PARIS,
            hours=1,
            force_price=0.06,
            force_skip_negative=True,
        )
        self.assertEqual(len(result.forced), 5)

    def test_last_window_closes_the_period(self) -> None:
        result = planner.build_plan(
            self.slots,
            _local("2026-01-15T09:00:00"),
            PARIS,
            hours=8,
        )
        first, last = result.windows
        self.assertEqual(result.last_end, _local("2026-01-15T15:00:00"))
        self.assertFalse(result.closes_period(first))
        self.assertTrue(result.closes_period(last))

    def test_last_window_includes_the_extra_runtime(self) -> None:
        result = planner.build_plan(
            self.slots,
            _local("2026-01-15T09:00:00"),
            PARIS,
            hours=8,
            extra_hours=1,
        )
        self.assertEqual(result.last_end, _local("2026-01-15T16:00:00"))
        self.assertFalse(result.closes_period(result.windows[0]))
        self.assertTrue(result.closes_period(result.windows[-1]))

    def test_forced_slots_do_not_close_the_period(self) -> None:
        result = planner.build_plan(
            self.slots,
            _local("2026-01-15T09:00:00"),
            PARIS,
            hours=2,
            force_price=0.06,
        )
        # The plan ends at 02:00, the forced afternoon runs after it.
        self.assertEqual(result.last_end, _local("2026-01-15T02:00:00"))
        (planned,) = result.planned_windows
        self.assertTrue(result.closes_period(planned))

    def test_merged_next_period_keeps_the_window_closing_today(self) -> None:
        slots = planner.build_slots(
            _series("2026-01-15", [0.30] * 23 + [0.01])
            + _series("2026-01-16", [0.01] + [0.30] * 23)
        )
        result = planner.build_plan(
            slots,
            _local("2026-01-15T09:00:00"),
            PARIS,
            hours=1,
            extra_hours=1,
        )
        (window,) = result.windows
        self.assertEqual(result.last_end, _local("2026-01-16T01:00:00"))
        self.assertTrue(result.closes_period(window))

    def test_no_window_closes_nothing(self) -> None:
        result = planner.build_plan(
            self.slots,
            _local("2026-01-15T09:00:00"),
            PARIS,
            hours=0,
        )
        self.assertIsNone(result.last_end)

    def test_quarter_hourly_series(self) -> None:
        prices = [0.4] * 96
        for index in range(8, 16):  # 02:00 -> 04:00
            prices[index] = 0.01
        slots = planner.build_slots(_series("2026-01-15", prices, step_minutes=15))
        result = planner.build_plan(
            slots,
            _local("2026-01-15T00:30:00"),
            PARIS,
            hours=2,
        )
        self.assertEqual(result.selected_hours, 2)
        self.assertEqual(result.windows[0].start, _local("2026-01-15T02:00:00"))
        self.assertEqual(result.windows[0].end, _local("2026-01-15T04:00:00"))


if __name__ == "__main__":
    unittest.main()
