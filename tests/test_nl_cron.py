"""Tests for ``core/nl_cron.py`` -- natural-language → cron parser used
by the ``/schedule`` wizard (W3 of CC parity plan).

Each pattern in the plan's spec gets a positive case; specific recovery
messages from ``NlCronParseError`` are checked so the wizard's surfaced
error stays useful.
"""

from __future__ import annotations

import pytest

from core.nl_cron import NlCronParseError, parse


class TestWeekdayWeekend:
    def test_every_weekday_at_9am(self):
        r = parse("every weekday at 9am")
        assert r.cron == "0 9 * * 1-5"
        assert "weekday" in r.human and "09:00" in r.human

    def test_every_weekday_at_24h(self):
        r = parse("every weekday at 09:30")
        assert r.cron == "30 9 * * 1-5"

    def test_every_weekend_default_time(self):
        r = parse("every weekend")
        assert r.cron == "0 9 * * 0,6"

    def test_every_weekend_pm_time(self):
        r = parse("every weekend at 6pm")
        assert r.cron == "0 18 * * 0,6"


class TestNamedWeekdays:
    @pytest.mark.parametrize("day,dow", [
        ("monday", 1), ("Tuesday", 2), ("WEDNESDAY", 3),
        ("thursday", 4), ("friday", 5), ("saturday", 6), ("sunday", 0),
    ])
    def test_every_weekday_at_time(self, day, dow):
        r = parse(f"every {day} at 8:30")
        assert r.cron == f"30 8 * * {dow}"

    def test_morning_default(self):
        r = parse("every monday morning")
        assert r.cron == "0 9 * * 1"
        assert "Monday" in r.human

    def test_evening_default(self):
        r = parse("every friday evening")
        assert r.cron == "0 18 * * 5"

    def test_no_time_defaults_to_9am(self):
        r = parse("every Tuesday")
        assert r.cron == "0 9 * * 2"


class TestDaily:
    def test_daily_at_time(self):
        r = parse("daily at 18:30")
        assert r.cron == "30 18 * * *"

    def test_every_day_at_time(self):
        r = parse("every day at 7am")
        assert r.cron == "0 7 * * *"

    def test_daily_default(self):
        r = parse("daily")
        assert r.cron == "0 9 * * *"

    def test_daily_at_noon(self):
        r = parse("daily at noon")
        assert r.cron == "0 12 * * *"

    def test_daily_at_midnight(self):
        r = parse("daily at midnight")
        assert r.cron == "0 0 * * *"


class TestEveryN:
    def test_every_n_hours(self):
        r = parse("every 2 hours")
        assert r.cron == "0 */2 * * *"

    def test_every_n_minutes(self):
        r = parse("every 30 minutes")
        assert r.cron == "*/30 * * * *"

    def test_hourly(self):
        r = parse("hourly")
        assert r.cron == "0 * * * *"

    def test_every_hour(self):
        r = parse("every hour")
        assert r.cron == "0 * * * *"

    def test_every_n_minutes_out_of_range_rejected(self):
        with pytest.raises(NlCronParseError, match="between 1 and 59"):
            parse("every 90 minutes")

    def test_every_n_hours_out_of_range_rejected(self):
        with pytest.raises(NlCronParseError, match="between 1 and 23"):
            parse("every 25 hours")


class TestMonthly:
    def test_first_of_month_default_time(self):
        r = parse("first of every month")
        assert r.cron == "0 9 1 * *"

    def test_first_of_month_with_time(self):
        r = parse("first of every month at 8am")
        assert r.cron == "0 8 1 * *"

    def test_1st_alias(self):
        r = parse("1st of the month at 6:30")
        assert r.cron == "30 6 1 * *"


class TestRawCronPassthrough:
    def test_valid_cron_returned_unchanged(self):
        r = parse("0 9 * * 1-5")
        assert r.cron == "0 9 * * 1-5"

    def test_invalid_cron_falls_through_to_nl_then_errors(self):
        # 6 fields -> not detected as cron, not an NL pattern -> error
        with pytest.raises(NlCronParseError):
            parse("0 9 * * 1-5 extra")

    def test_humanizes_known_shape(self):
        r = parse("0 */6 * * *")
        assert "every 6 hours" in r.human


class TestErrors:
    def test_empty_input_specific_error(self):
        with pytest.raises(NlCronParseError, match="describe when"):
            parse("")

    def test_unrecognised_input_lists_examples(self):
        with pytest.raises(NlCronParseError) as exc_info:
            parse("maybe Tuesdays?")
        msg = str(exc_info.value)
        assert "every weekday at 9am" in msg
        assert "5-field cron" in msg

    def test_unparseable_time_named_specifically(self):
        with pytest.raises(NlCronParseError, match="Couldn't parse the time"):
            parse("every weekday at quarter-past")


class TestHumanLabel:
    def test_weekday_label_uses_real_name(self):
        r = parse("every Wednesday at 7:00")
        assert "Wednesday" in r.human

    def test_passthrough_humanises_familiar_shape(self):
        r = parse("0 9 * * 1-5")
        assert "weekday" in r.human

    def test_passthrough_unfamiliar_shape_falls_back_to_raw(self):
        # croniter accepts this; humanizer doesn't have a friendly form.
        r = parse("17 4 5 * *")
        assert r.cron == "17 4 5 * *"
        # Either humanises something or falls back to "cron `...`"
        assert "17 4 5" in r.human or "cron" in r.human
