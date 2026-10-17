import unittest

from zeus_pc.observer import ObserverRunner, ObserverStore, Sample
from zeus_pc.process_observer import (
    ProcessObserverStore,
    ProcessPoint,
    ProcessSnapshot,
    _process_cpu_pct,
    _top_points,
)


class ProcessObserverTests(unittest.TestCase):
    def test_process_cpu_delta_math(self):
        previous = (100, 10_000_000)
        current = (100, 30_000_000)

        self.assertEqual(
            _process_cpu_pct(previous, current, 2.0, 2),
            50.0,
        )
        self.assertIsNone(
            _process_cpu_pct(None, current, 2.0, 2)
        )

    def test_pid_reuse_resets_cpu_baseline(self):
        previous = (100, 10_000_000)
        reused_pid = (200, 30_000_000)

        self.assertIsNone(
            _process_cpu_pct(previous, reused_pid, 2.0, 2)
        )

    def test_top_process_ordering_ignores_unknown_values(self):
        points = [
            ProcessPoint(1, "low.exe", 5.0, 100),
            ProcessPoint(2, "high.exe", 50.0, 50),
            ProcessPoint(3, "unknown.exe", None, None),
            ProcessPoint(4, "memory.exe", 10.0, 900),
        ]

        top_cpu = _top_points(points, "cpu_pct", 2)
        top_memory = _top_points(points, "working_set_bytes", 2)

        self.assertEqual(
            [item.name for item in top_cpu],
            ["high.exe", "memory.exe"],
        )
        self.assertEqual(
            [item.name for item in top_memory],
            ["memory.exe", "low.exe"],
        )

    def test_process_history_is_bounded_and_session_filterable(self):
        store = ProcessObserverStore(max_snapshots=5)
        for index in range(7):
            store.record(
                ProcessSnapshot(
                    timestamp=float(index),
                    total_processes=10,
                    sampled_processes=9,
                    inaccessible_count=1,
                    top_cpu=[],
                    top_memory=[],
                    session_id="A" if index % 2 == 0 else "B",
                )
            )

        recent = store.recent(10)
        session_a = store.recent(10, session_id="A")

        self.assertEqual(len(recent), 5)
        self.assertEqual(recent[0].timestamp, 2.0)
        self.assertTrue(
            all(item.session_id == "A" for item in session_a)
        )

    def test_runner_tags_process_snapshot_with_active_session(self):
        sample_store = ObserverStore(max_samples=10)
        process_store = ProcessObserverStore(max_snapshots=5)

        def process_provider():
            return ProcessSnapshot(
                timestamp=20.0,
                total_processes=3,
                sampled_processes=2,
                inaccessible_count=1,
                top_cpu=[],
                top_memory=[],
            )

        runner = ObserverRunner(
            sample_store,
            provider=lambda: Sample(
                10.0,
                50.0,
                40.0,
                None,
                None,
            ),
            process_provider=process_provider,
            process_store=process_store,
            process_interval_seconds=5.0,
        )
        session, _ = runner.start_session(
            "Gaming session",
            "gaming",
        )

        runner.sample_once()
        process_snapshot = process_store.latest()

        self.assertIsNotNone(process_snapshot)
        self.assertEqual(
            process_snapshot.session_id,
            session.id,
        )


if __name__ == "__main__":
    unittest.main()
