import unittest
from datetime import datetime, timedelta, timezone

from operations.trends import Sample, forecast


class TrendTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime.now(timezone.utc)
        self.samples = [Sample(self.start + timedelta(minutes=index), index * 100) for index in range(6)]

    def test_real_baseline_growth_estimate(self):
        result = forecast(self.samples, capacity_bytes=1000)
        self.assertAlmostEqual(result.bytes_per_second, 100 / 60)
        self.assertAlmostEqual(result.seconds_to_threshold, 180)
        self.assertEqual(result.sample_count, 6)

    def test_sparse_gapped_reordered_and_reset_samples_are_rejected(self):
        variants = [
            self.samples[:2],
            [Sample(self.start + timedelta(seconds=index), index * 100) for index in range(6)],
            list(reversed(self.samples)),
            [*self.samples[:5], Sample(self.start + timedelta(minutes=15), 600)],
            [*self.samples[:5], Sample(self.start + timedelta(minutes=5), 200)],
        ]
        for samples in variants:
            with self.subTest(samples=samples), self.assertRaises(ValueError):
                forecast(samples, capacity_bytes=1000)

    def test_stable_series_does_not_invent_a_breach_time(self):
        samples = [Sample(sample.observed_at, 100) for sample in self.samples]
        self.assertIsNone(forecast(samples, capacity_bytes=1000).seconds_to_threshold)


if __name__ == "__main__":
    unittest.main()
