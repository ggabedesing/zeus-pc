import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zeus_pc.brain import BrainError
from zeus_pc.persistence import ZeusDatabase
from zeus_pc.skeleton import run_skeleton, step_transaction
from zeus_pc.transaction import (
    ActionDefinition,
    ActionRegistry,
    TransactionEngine,
    TransactionError,
)

MODEL_ID = "LLM_JP_1_8B"
FILENAME = "llm-jp-3.1-1.8b-instruct4.Q4_K_M.gguf"
MODEL_BYTES = b"GGUF-REAL"
GUID_A = "11111111-1111-1111-1111-111111111111"
GUID_B = "22222222-2222-2222-2222-222222222222"
PLANS = [
    {"guid": GUID_A, "name": "Equilibrado", "active": True},
    {"guid": GUID_B, "name": "Economia", "active": False},
]


class FakeProc:
    def __init__(self, exit_code=None):
        self.exit_code = exit_code
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.exit_code

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return self.exit_code or 0


def seed_model(db, models_dir: Path) -> str:
    models_dir.mkdir(parents=True, exist_ok=True)
    (models_dir / FILENAME).write_bytes(MODEL_BYTES)
    sha = hashlib.sha256(MODEL_BYTES).hexdigest()
    db.record_model_install(
        model_id=MODEL_ID,
        filename=FILENAME,
        license_name="Apache-2.0",
        license_accepted_at="2026-01-01T00:00:00Z",
        sha256=sha,
        size_bytes=len(MODEL_BYTES),
        source="import",
    )
    return sha


def guid_engine(db, *, switch_ok=True, restore_ok=True):
    """Real TransactionEngine over a power-plan-like test action."""
    calls: list[str] = []
    state = {"active": GUID_A, "known": {GUID_A, GUID_B}}
    registry = ActionRegistry()

    def validate(args):
        if not isinstance(args, dict) or set(args) != {"guid"}:
            raise TransactionError("guid unico aceito")
        guid = str(args.get("guid") or "").lower()
        if guid not in state["known"]:
            raise TransactionError("plano inexistente")
        return {"guid": guid}

    def apply(args):
        calls.append(args["guid"])
        if len(calls) == 1 and not switch_ok:
            return {"ok": False, "message": "powercfg recusou a troca."}
        if len(calls) == 2 and not restore_ok:
            return {"ok": False, "message": "powercfg recusou a restauracao."}
        state["active"] = args["guid"]
        return {"ok": True, "message": "plano ativo."}

    def verify(args):
        return {"ok": args["guid"] == state["active"], "actual": state["active"]}

    def rollback(before_state, plan):
        state["active"] = before_state["guid"]
        return {"ok": True, "message": "restaurado"}

    registry.register(
        ActionDefinition(
            action_id="SET_ACTIVE_POWER_PLAN",
            resource_key="POWER_SCHEME",
            required_privilege="USER",
            reversible=True,
            validate_args=validate,
            capture_before=lambda args: {"guid": state["active"]},
            verify=verify,
            rollback_preview=lambda before: {
                "action_id": "SET_ACTIVE_POWER_PLAN",
                "args": {"guid": before["guid"]},
            },
            apply=apply,
            rollback=rollback,
        )
    )
    return TransactionEngine(db, registry=registry), state, calls


def statuses(report):
    return {step["name"]: step["status"] for step in report["steps"]}


class SkeletonRunnerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.db = ZeusDatabase(self.root / "zeus.db")
        self.db.initialize()
        self.models = self.root / "models"
        patcher = patch.dict(
            os.environ, {"ZEUS_MODELS_DIR": str(self.models)}
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_with(self, **overrides):
        defaults = dict(
            probe=lambda url, timeout=0: {"reachable": False},
            launcher=lambda cmd, log: FakeProc(),
            invoker=lambda task, prompt, **kw: {
                "model_id": kw["model_id"],
                "task": task,
                "content": "zeus respondeu",
                "usage": {"total_tokens": 5},
                "elapsed_ms": 12.5,
            },
            plan_source=lambda: PLANS,
            detect_binary=lambda: "/fake/llama-server",
            sleeper=lambda seconds: None,
            serve_timeout=5.0,
            log_path=self.root / "serve.log",
        )
        defaults.update(overrides)
        return run_skeleton(self.db, **defaults)

    def test_happy_path_runs_the_full_real_cycle(self):
        seed_model(self.db, self.models)
        probes = iter([{"reachable": False}, {"reachable": True}])
        engine, state, calls = guid_engine(self.db)
        procs = []

        def tracked_launcher(cmd, log):
            proc = FakeProc()
            procs.append({"cmd": list(cmd), "proc": proc})
            return proc

        report = self.run_with(
            probe=lambda url, timeout=0: next(probes),
            launcher=tracked_launcher,
            engine=engine,
        )

        self.assertTrue(report["ok"], report)
        self.assertEqual(
            statuses(report),
            {
                "doctor": "ok",
                "model": "ok",
                "serve": "ok",
                "invoke": "ok",
                "transaction": "ok",
            },
        )
        # subprocesso subiu com argv puro e foi encerrado ao final
        self.assertEqual(
            procs[0]["cmd"],
            [sys.executable, "-m", "zeus_pc.brain.serve", "--model-id", MODEL_ID],
        )
        self.assertTrue(procs[0]["proc"].terminated)
        # transacao real: trocou e restaurou via consentimento
        self.assertEqual(calls, [GUID_B, GUID_A])
        self.assertEqual(state["active"], GUID_A)
        states = [row["state"] for row in self.db.recent_transactions()]
        self.assertEqual(states, ["VERIFIED", "VERIFIED"])
        # hash do modelo conferido no caminho "model"
        model_step = next(s for s in report["steps"] if s["name"] == "model")
        self.assertEqual(model_step["sha256"], hashlib.sha256(MODEL_BYTES).hexdigest())

    def test_missing_everything_skips_without_failures(self):
        launched = []

        def launcher(cmd, log):
            launched.append(cmd)
            return FakeProc()

        report = self.run_with(
            launcher=launcher,
            plan_source=lambda: [],
        )

        self.assertTrue(report["ok"], report)
        self.assertEqual(
            statuses(report),
            {
                "doctor": "ok",
                "model": "pulado",
                "serve": "pulado",
                "invoke": "pulado",
                "transaction": "pulado",
            },
        )
        self.assertEqual(launched, [])
        model_step = next(s for s in report["steps"] if s["name"] == "model")
        self.assertIn("--download-url", model_step["detail"])
        self.assertEqual(report["counts"]["falha"], 0)

    def test_download_without_license_acceptance_is_a_failure(self):
        report = self.run_with(
            download_url="https://huggingface.co/x/resolve/main/y.gguf",
            accept_license=False,
        )
        self.assertFalse(report["ok"])
        model_step = next(s for s in report["steps"] if s["name"] == "model")
        self.assertEqual(model_step["status"], "falha")
        self.assertIn("--accept-license", model_step["detail"])
        # nada foi instalado
        self.assertFalse((self.models / FILENAME).exists())

    def test_serve_timeout_is_a_failure_and_process_is_stopped(self):
        seed_model(self.db, self.models)
        procs = []

        def launcher(cmd, log):
            proc = FakeProc()
            procs.append(proc)
            return proc

        report = self.run_with(
            launcher=launcher,
            probe=lambda url, timeout=0: {"reachable": False},
            serve_timeout=3.0,
        )

        self.assertFalse(report["ok"])
        by_name = {step["name"]: step for step in report["steps"]}
        self.assertEqual(by_name["serve"]["status"], "falha")
        self.assertIn("nao respondeu", by_name["serve"]["detail"])
        self.assertEqual(by_name["invoke"]["status"], "pulado")
        self.assertTrue(procs[0].terminated)

    def test_early_server_exit_reports_log(self):
        seed_model(self.db, self.models)
        (self.root / "serve.log").write_text(
            "erro: gguf corrompido", encoding="utf-8"
        )

        report = self.run_with(
            launcher=lambda cmd, log: FakeProc(exit_code=1),
            probe=lambda url, timeout=0: {"reachable": False},
            serve_timeout=5.0,
            log_path=self.root / "serve.log",
        )

        by_name = {step["name"]: step for step in report["steps"]}
        self.assertEqual(by_name["serve"]["status"], "falha")
        self.assertIn("codigo 1", by_name["serve"]["detail"])
        self.assertIn("gguf corrompido", by_name["serve"]["detail"])

    def test_invoke_failure_is_reported(self):
        seed_model(self.db, self.models)

        def broken_invoker(task, prompt, **kwargs):
            raise BrainError("runtime indisponivel: teste")

        report = self.run_with(
            probe=lambda url, timeout=0: {"reachable": True},
            invoker=broken_invoker,
        )

        self.assertFalse(report["ok"])
        by_name = {step["name"]: step for step in report["steps"]}
        self.assertEqual(by_name["invoke"]["status"], "falha")
        self.assertIn("runtime indisponivel", by_name["invoke"]["detail"])
        self.assertEqual(by_name["serve"]["status"], "ok")

    def test_missing_llama_server_skips_serving(self):
        seed_model(self.db, self.models)
        report = self.run_with(detect_binary=lambda: None)
        by_name = {step["name"]: step for step in report["steps"]}
        self.assertEqual(by_name["serve"]["status"], "pulado")
        self.assertIn("llama-server", by_name["serve"]["detail"])
        self.assertEqual(by_name["invoke"]["status"], "pulado")

    def test_broken_database_is_reported_not_raised(self):
        broken_path = self.root / "broken"
        broken_path.mkdir()  # um diretorio no lugar do arquivo do banco
        broken = ZeusDatabase(broken_path)
        # arquivo presente: o verify toca o banco quebrado e deve reportar
        self.models.mkdir(parents=True, exist_ok=True)
        (self.models / FILENAME).write_bytes(MODEL_BYTES)

        report = run_skeleton(
            broken,
            probe=lambda url, timeout=0: {"reachable": False},
            launcher=lambda cmd, log: FakeProc(),
            plan_source=lambda: [],
            detect_binary=lambda: None,
            sleeper=lambda seconds: None,
            log_path=self.root / "serve.log",
        )

        self.assertFalse(report["ok"])
        by_name = {step["name"]: step for step in report["steps"]}
        self.assertEqual(by_name["doctor"]["status"], "falha")
        self.assertEqual(by_name["model"]["status"], "falha")
        self.assertEqual(by_name["serve"]["status"], "pulado")


class TransactionStepTests(unittest.TestCase):
    def test_switch_and_restore_both_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            db.initialize()
            engine, state, calls = guid_engine(db)

            step = step_transaction(engine, PLANS)

            self.assertEqual(step["status"], "ok")
            self.assertEqual(calls, [GUID_B, GUID_A])
            self.assertEqual(state["active"], GUID_A)
            self.assertEqual(step["switch_state"], "VERIFIED")
            self.assertEqual(step["restore_state"], "VERIFIED")

    def test_skips_without_alternative_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            db.initialize()
            engine, _, _ = guid_engine(db)

            only_active = [PLANS[0]]
            self.assertEqual(
                step_transaction(engine, only_active)["status"], "pulado"
            )
            self.assertEqual(
                step_transaction(engine, [])["status"], "pulado"
            )

    def test_failed_switch_is_failure_without_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            db.initialize()
            engine, state, calls = guid_engine(db, switch_ok=False)

            step = step_transaction(engine, PLANS)

            self.assertEqual(step["status"], "falha")
            self.assertIn("troca falhou", step["detail"])
            self.assertEqual(calls, [GUID_B])  # restauracao nem tentada
            self.assertEqual(state["active"], GUID_A)

    def test_failed_restore_is_a_loud_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            db.initialize()
            engine, state, calls = guid_engine(db, restore_ok=False)

            step = step_transaction(engine, PLANS)

            self.assertEqual(step["status"], "falha")
            self.assertIn("FAILA CRITICA", step["detail"])
            self.assertIn("NAO restaurado", step["detail"])
            self.assertEqual(calls, [GUID_B, GUID_A])

    def test_unknown_plan_prepares_but_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            db.initialize()
            engine, _, _ = guid_engine(db)
            ghost_plans = [
                {"guid": GUID_A, "name": "Ativo", "active": True},
                {"guid": "99999999-9999-9999-9999-999999999999",
                 "name": "Fantasma", "active": False},
            ]

            step = step_transaction(engine, ghost_plans)

            self.assertEqual(step["status"], "falha")
            self.assertIn("preparacao da troca falhou", step["detail"])
            self.assertEqual(db.recent_transactions(), [])


if __name__ == "__main__":
    unittest.main()
