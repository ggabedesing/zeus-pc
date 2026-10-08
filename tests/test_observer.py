import unittest

from zeus_pc.observer import ObserverStore, Sample, _cpu_usage_from_times


class ObserverTests(unittest.TestCase):
    def test_cpu_usage_delta_math(self):
        previous = (100, 300, 200)
        current = (150, 400, 300)

        self.assertEqual(_cpu_usage_from_times(previous, current), 75.0)
        self.assertIsNone(_cpu_usage_from_times(None, current))

    def test_ring_buffer_keeps_newest_samples(self):
        store = ObserverStore(max_samples=10)
        for index in range(12):
            store.record(Sample(float(index), index, 50.0, None, None))

        recent = store.recent(20)

        self.assertEqual(len(recent), 10)
        self.assertEqual(recent[0].timestamp, 2.0)
        self.assertEqual(recent[-1].timestamp, 11.0)

    def test_baseline_skips_missing_values(self):
        store = ObserverStore(max_samples=10)
        store.record(Sample(1.0, 10.0, 40.0, None, 50.0))
        store.record(Sample(2.0, None, 60.0, 80.0, None))
        store.record(Sample(3.0, 30.0, 50.0, 20.0, 70.0))

        baseline = store.baseline(10)

        self.assertEqual(baseline["sample_count"], 3)
        self.assertEqual(baseline["duration_seconds"], 2.0)
        self.assertEqual(baseline["metrics"]["cpu_pct"]["count"], 2)
        self.assertEqual(baseline["metrics"]["cpu_pct"]["avg"], 20.0)
        self.assertEqual(baseline["metrics"]["ram_used_pct"]["avg"], 50.0)
        self.assertEqual(baseline["metrics"]["gpu_pct"]["avg"], 50.0)
        self.assertEqual(baseline["metrics"]["gpu_temp_c"]["avg"], 60.0)

    def test_record_uses_injected_provider(self):
        calls = []

        def provider():
            calls.append(True)
            return Sample(5.0, 25.0, 35.0, None, None)

        store = ObserverStore(max_samples=10, provider=provider)
        sample = store.record()

        self.assertEqual(len(calls), 1)
        self.assertEqual(sample.cpu_pct, 25.0)
        self.assertEqual(store.recent(1)[0].timestamp, 5.0)


if __name__ == "__main__":
    unittest.main()
