"""Tests for RateLimiter."""
import sys, os, time, threading
from pathlib import Path
from unittest import TestCase, main

_APP = Path(__file__).resolve().parent.parent / "app"
sys.path.insert(0, str(_APP))

from rate_limit import RateLimiter


class TestRateLimiter(TestCase):
    def setUp(self):
        self.rl = RateLimiter(max_per_minute=5, window_seconds=3)

    def test_allows_under_limit(self):
        for i in range(5):
            self.assertTrue(self.rl.check(f"ip_{i}"), f"request {i} should be allowed")

    def test_denies_over_limit(self):
        key = "deny_test"
        for _ in range(5):
            self.rl.check(key)
        # 6th request should be denied
        self.assertFalse(self.rl.check(key))

    def test_sliding_window_resets(self):
        key = "sliding_test"
        self.rl.check(key)
        self.rl.check(key)
        # Wait for window to expire
        time.sleep(3.1)
        # Should be allowed again
        self.assertTrue(self.rl.check(key))

    def test_per_ip_isolation(self):
        for i in range(5):
            self.rl.check("ip_a")
        # ip_a is at limit, but ip_b should still be allowed
        self.assertTrue(self.rl.check("ip_b"))

    def test_reset_one_key(self):
        key = "reset_one"
        for _ in range(5):
            self.rl.check(key)
        self.assertFalse(self.rl.check(key))
        self.rl.reset(key)
        self.assertTrue(self.rl.check(key))

    def test_reset_all(self):
        self.rl.check("ip_a")
        self.rl.check("ip_b")
        self.rl.reset()
        self.assertTrue(self.rl.check("ip_a"))
        self.assertTrue(self.rl.check("ip_b"))

    def test_thread_safety(self):
        """Concurrent checks should not corrupt internal state."""
        errors = []

        def burst(ip_prefix, count):
            for i in range(count):
                if not self.rl.check(f"{ip_prefix}_{i % 10}"):
                    errors.append(f"denied too early: {ip_prefix}_{i}")

        threads = [threading.Thread(target=burst, args=(f"t{i}", 20)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])

    def test_zero_max_per_minute(self):
        rl = RateLimiter(max_per_minute=0, window_seconds=1)
        self.assertFalse(rl.check("any_ip"))
        rl2 = RateLimiter(max_per_minute=1, window_seconds=1)
        self.assertTrue(rl2.check("ip"))
        self.assertFalse(rl2.check("ip"))


if __name__ == "__main__":
    main()