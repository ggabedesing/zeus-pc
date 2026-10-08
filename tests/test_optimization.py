import unittest

from zeus_pc.models import Metric, ProbeResult, Status
from zeus_pc.optimization import (
    OptimizationStatus,
    build_optimization_plan,
    snapshot_from_payload,
)
from zeus_pc.process_observer import ProcessPoint, ProcessSnapshot


def result(domain, metrics=None, status=Status.OK, source="test"):
    return ProbeResult(
        domain,
        status,
        metrics or [],
        source=source,
    )


def base_snapshot():
    return [
        result("Windows"),
        result("CPU"),
        result(
            "RAM",
            [
                Metric("usable_gb", 32.0, source="test"),
                Metric("available_gb", 16.0, source="test"),
            ],
        ),
        result(
            "Storage",
            [
                Metric(
                    "volumes",
                    [{"root": "C:\\", "free_pct": 50.0}],
                    source="test",
                )
            ],
        ),
        result(
            "Power",
            [Metric("active_scheme", "Balanced", source="test")],
        ),
        result(
            "Startup",
            [Metric("items", [], source="test")],
        ),
        result(
            "Windows Update",
            [Metric("pending_count", 0, source="test")],
        ),
    ]


class OptimizationEngineTests(unittest.TestCase):
    def test_no_action_is_preserved_when_core_coverage_is_healthy(self):
        plan = build_optimization_plan(base_snapshot(), "general")

        self.assertFalse(plan.execution_ready)
        self.assertEqual(plan.mode, "DRY_RUN")
        self.assertEqual(plan.profile, "GENERAL")
        self.assertEqual(len(plan.findings), 1)
        self.assertEqual(
            plan.findings[0].status,
            OptimizationStatus.ALREADY_CORRECT,
        )

    def test_recommended_rule_maps_to_apply_recommended_but_not_automation_ready(self):
        snapshot = base_snapshot()
        update = next(
            item for item in snapshot if item.domain == "Windows Update"
        )
        update.metrics = [
            Metric("pending_count", 3, source="Windows Update Agent")
        ]

        plan = build_optimization_plan(snapshot)
        finding = next(
            item
            for item in plan.findings
            if item.id == "windows.pending-updates.v1"
        )

        self.assertEqual(
            finding.status,
            OptimizationStatus.APPLY_RECOMMENDED,
        )
        self.assertFalse(finding.automation_eligible)
        self.assertIn("NO_ALLOWLISTED_ACTION", finding.blockers)
        self.assertFalse(plan.execution_ready)

    def test_incomplete_core_coverage_blocks_false_no_action_conclusion(self):
        snapshot = base_snapshot()
        windows = next(
            item for item in snapshot if item.domain == "Windows"
        )
        windows.status = Status.FAILED

        plan = build_optimization_plan(snapshot)

        self.assertEqual(len(plan.findings), 1)
        self.assertEqual(
            plan.findings[0].id,
            "system.coverage-limited.v1",
        )
        self.assertEqual(
            plan.findings[0].status,
            OptimizationStatus.BLOCKED,
        )

    def test_observer_requires_minimum_sample_coverage(self):
        baseline = {
            "metrics": {
                "cpu_pct": {
                    "count": 2,
                    "min": 98.0,
                    "max": 100.0,
                    "avg": 99.0,
                }
            }
        }

        plan = build_optimization_plan(
            base_snapshot(),
            observer_baseline=baseline,
        )

        self.assertFalse(
            any(
                item.id == "observer.cpu-sustained-pressure.v1"
                for item in plan.findings
            )
        )

    def test_cpu_and_memory_pressure_attach_process_evidence(self):
        baseline = {
            "metrics": {
                "cpu_pct": {
                    "count": 10,
                    "min": 86.0,
                    "max": 99.0,
                    "avg": 91.0,
                },
                "ram_used_pct": {
                    "count": 10,
                    "min": 91.0,
                    "max": 97.0,
                    "avg": 94.0,
                },
            }
        }
        process = ProcessSnapshot(
            timestamp=1.0,
            total_processes=2,
            sampled_processes=2,
            inaccessible_count=0,
            top_cpu=[
                ProcessPoint(
                    10,
                    "game.exe",
                    45.0,
                    1_000_000,
                )
            ],
            top_memory=[
                ProcessPoint(
                    20,
                    "obs64.exe",
                    20.0,
                    2_000_000,
                )
            ],
        )

        plan = build_optimization_plan(
            base_snapshot(),
            observer_baseline=baseline,
            process_snapshot=process,
        )

        cpu = next(
            item
            for item in plan.findings
            if item.id == "observer.cpu-sustained-pressure.v1"
        )
        memory = next(
            item
            for item in plan.findings
            if item.id == "observer.memory-sustained-pressure.v1"
        )

        self.assertEqual(
            cpu.status,
            OptimizationStatus.MANUAL_REVIEW,
        )
        self.assertEqual(cpu.confidence.value, "HIGH")
        self.assertTrue(
            any(
                item.domain == "Observer Processes"
                and item.value["name"] == "game.exe"
                for item in cpu.evidence
            )
        )
        self.assertTrue(
            any(
                item.domain == "Observer Processes"
                and item.value["name"] == "obs64.exe"
                for item in memory.evidence
            )
        )

    def test_high_gpu_in_gaming_is_explanatory_no_action(self):
        baseline = {
            "metrics": {
                "gpu_pct": {
                    "count": 5,
                    "min": 95.0,
                    "max": 100.0,
                    "avg": 98.0,
                }
            }
        }

        plan = build_optimization_plan(
            base_snapshot(),
            "gaming",
            observer_baseline=baseline,
        )
        finding = next(
            item
            for item in plan.findings
            if item.id == "observer.gpu-bound-explanation.v1"
        )

        self.assertEqual(
            finding.status,
            OptimizationStatus.NO_ACTION,
        )
        self.assertIn(
            "MEASURE_FRAMETIME_BEFORE_ACTION",
            finding.blockers,
        )

    def test_snapshot_payload_round_trip_preserves_status_and_metric(self):
        original = base_snapshot()
        restored = snapshot_from_payload(
            [item.to_dict() for item in original]
        )

        self.assertEqual(restored[0].status, Status.OK)
        storage = next(
            item for item in restored if item.domain == "Storage"
        )
        self.assertEqual(
            storage.metrics[0].value[0]["free_pct"],
            50.0,
        )


if __name__ == "__main__":
    unittest.main()
