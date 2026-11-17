"""Offline tests; no Codex login, network access, or GUI launch."""

import unittest

from codex_usage import UsageError, parse_rate_limits, _wait_for_id
from widget import (
    clamp_position,
    format_countdown,
    format_renewal,
    quota_color,
    recovery_delay,
)


class UsageParsingTests(unittest.TestCase):
    def test_used_to_remaining_for_both_windows(self):
        snapshot = parse_rate_limits({
            "rateLimitsByLimitId": {
                "codex": {
                    "planType": "plus",
                    "primary": {"usedPercent": 19, "windowDurationMins": 300,
                                "resetsAt": 1794889800},
                    "secondary": {"usedPercent": 32, "windowDurationMins": 10080,
                                  "resetsAt": 1795209600},
                }
            }
        })
        self.assertEqual(snapshot.primary.remaining_percent, 81)
        self.assertEqual(snapshot.secondary.remaining_percent, 68)
        self.assertEqual(snapshot.primary.resets_at, 1794889800)
        self.assertEqual(snapshot.secondary.resets_at, 1795209600)

    def test_clamps_out_of_range_used_values(self):
        snapshot = parse_rate_limits({
            "rateLimits": {
                "primary": {"usedPercent": 120},
                "secondary": {"usedPercent": -8},
            }
        })
        self.assertEqual(snapshot.primary.remaining_percent, 0)
        self.assertEqual(snapshot.secondary.remaining_percent, 100)

    def test_missing_rate_limits_fail_closed(self):
        with self.assertRaises(UsageError):
            parse_rate_limits({})

    def test_future_codex_bucket_is_parsed(self):
        snapshot = parse_rate_limits({
            "rateLimitsByLimitId": {"codex-next": {
                "primary": {"usedPercent": "12.8"},
            }}
        })
        self.assertEqual(snapshot.primary.remaining_percent, 88)

    def test_non_object_jsonrpc_response_does_not_crash(self):
        class FakeReader:
            def __init__(self):
                self.messages = iter(["[]", "null", '17', '{"id":4,"result":{"ok":true}}'])
            def get(self, timeout):
                return next(self.messages)
        from time import monotonic
        self.assertEqual(_wait_for_id(FakeReader(), 4, monotonic()+3), {"ok": True})


class RenewalTests(unittest.TestCase):
    def test_exact_four_hours(self):
        self.assertEqual(format_countdown(14400, now=0), "in 4h 00m")

    def test_weekly_countdown(self):
        self.assertEqual(format_countdown(3*86400+14*3600, now=0), "in 3d 14h")

    def test_small_positive_interval_rounds_up(self):
        self.assertEqual(format_countdown(121, now=0), "in 3m")
        self.assertEqual(format_countdown(1, now=0), "in 1m")

    def test_due_and_missing(self):
        self.assertEqual(format_countdown(100, now=100), "Renewal due")
        self.assertEqual(format_countdown(None, now=100), "")
        self.assertEqual(format_renewal(None), "Resets: unavailable")

    def test_datetime_display_uses_local_timezone(self):
        self.assertRegex(format_renewal(1794889800), r"^Resets: \d{2} \w{3} \d{4}, \d{2}:\d{2} [AP]M$")

    def test_invalid_timestamp_is_not_invented(self):
        self.assertEqual(format_renewal(10**30), "Resets: unavailable")


class DisplayHelpersTests(unittest.TestCase):
    def test_quota_colors_track_remaining(self):
        self.assertEqual(quota_color(9), "#F49A9A")
        self.assertEqual(quota_color(25), "#F7DB83")
        self.assertEqual(quota_color(81), "#8FE3B2")

    def test_widget_position_fits_monitor(self):
        self.assertEqual(clamp_position(1700, 1030, 340, 125,
                                        (0, 0, 1920, 1040)), (1580, 915))

    def test_recovery_delay_bounded(self):
        self.assertEqual([recovery_delay(n) for n in range(1, 7)],
                         [15, 30, 60, 120, 300, 300])


if __name__ == "__main__":
    unittest.main()