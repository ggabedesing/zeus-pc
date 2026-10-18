import hashlib
import io
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from zeus_pc.brain import (
    BrainError,
    ModelManager,
    ModelRegistry,
    ModelSpec,
    ModelTask,
    language_fidelity,
    run_benchmark,
    score_turn,
    sha256_file,
)
from zeus_pc.brain.benchmark import BENCHMARK_PROMPTS, LANGUAGE_SET
from zeus_pc.brain.manager import DEFAULT_DISK_BUDGET_GB
from zeus_pc.brain.router import route_task
from zeus_pc.doctor import build_doctor_report
from zeus_pc.persistence import SCHEMA_VERSION, ZeusDatabase

GGUF_BYTES = b"GGUF-STUB-" + bytes(range(256)) * 8
GGUF_SHA256 = hashlib.sha256(GGUF_BYTES).hexdigest()


def make_spec(**overrides):
    base = {
        "model_id": "DEMO_MODEL",
        "title": "Demo Model",
        "family": "Demo",
        "filename": "demo.Q4_K_M.gguf",
        "quantization": "Q4_K_M",
        "size_gb": 1.0,
        "context_length": 2048,
        "min_ram_gb": 1.0,
        "license_name": "Apache-2.0",
        "languages": ("pt", "en"),
        "strengths": (ModelTask.CHAT,),
        "quality_rank": 1,
        "source_url": "https://huggingface.co/demo/demo-GGUF",
    }
    base.update(overrides)
    return ModelSpec(**base)


def registry_with(specs):
    registry = ModelRegistry()
    for spec in specs:
        registry.register(spec)
    return registry


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def opener_returning(payload: bytes):
    def _open(url: str):
        return FakeResponse(payload)

    return _open


def opener_failing(message: str):
    def _open(url: str):
        raise OSError(message)

    return _open


class ManagerTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.models = self.root / "models"
        self.db = ZeusDatabase(self.root / "zeus.db")
        self.db.initialize()
        self.registry = registry_with([make_spec()])

    def manager(self, **kwargs):
        kwargs.setdefault("registry", self.registry)
        kwargs.setdefault("directory", self.models)
        return ModelManager(self.db, **kwargs)


class InstallTests(ManagerTestCase):
    def test_license_acceptance_is_mandatory(self):
        manager = self.manager(open_url=opener_returning(GGUF_BYTES))
        with self.assertRaises(BrainError) as ctx:
            manager.install(
                "DEMO_MODEL",
                url="https://huggingface.co/demo/resolve/main/demo.gguf",
            )
        self.assertIn("licenca nao aceita", str(ctx.exception))
        self.assertIn("Apache-2.0", str(ctx.exception))
        self.assertEqual(self.db.list_model_installs(), [])

    def test_unknown_model_and_double_origin_are_rejected(self):
        manager = self.manager(open_url=opener_returning(GGUF_BYTES))
        with self.assertRaises(BrainError):
            manager.install("NOT_REGISTERED", accept_license=True, url="https://huggingface.co/x")
        with self.assertRaises(BrainError):
            manager.install(
                "DEMO_MODEL",
                accept_license=True,
                url="https://huggingface.co/x",
                source_path="/tmp/other.gguf",
            )
        with self.assertRaises(BrainError):
            manager.install("DEMO_MODEL", accept_license=True)

    def test_download_streams_hashes_and_records_install(self):
        manager = self.manager(open_url=opener_returning(GGUF_BYTES))
        result = manager.install(
            "DEMO_MODEL",
            accept_license=True,
            url="https://huggingface.co/demo/resolve/main/demo.gguf",
            expected_sha256=GGUF_SHA256,
        )

        target = self.models / "demo.Q4_K_M.gguf"
        self.assertTrue(target.is_file())
        self.assertEqual(target.read_bytes(), GGUF_BYTES)
        self.assertEqual(result["sha256"], GGUF_SHA256)
        self.assertEqual(result["size_bytes"], len(GGUF_BYTES))
        self.assertEqual(result["source"], "download")
        self.assertEqual(result["license_name"], "Apache-2.0")

        install = self.db.get_model_install("DEMO_MODEL")
        self.assertEqual(install["sha256"], GGUF_SHA256)
        self.assertTrue(install["license_accepted_at"])
        self.assertIsNone(install["last_used_at"])
        # nada temporario sobra
        self.assertEqual(
            [path.name for path in self.models.iterdir()],
            ["demo.Q4_K_M.gguf"],
        )

    def test_hash_mismatch_discards_everything(self):
        manager = self.manager(open_url=opener_returning(b"corrompido"))
        with self.assertRaises(BrainError) as ctx:
            manager.install(
                "DEMO_MODEL",
                accept_license=True,
                url="https://huggingface.co/demo/resolve/main/demo.gguf",
                expected_sha256=GGUF_SHA256,
            )
        self.assertIn("hash", str(ctx.exception))
        self.assertFalse((self.models / "demo.Q4_K_M.gguf").exists())
        self.assertEqual(list(self.models.glob("*.part")), [])
        self.assertIsNone(self.db.get_model_install("DEMO_MODEL"))

    def test_download_failure_leaves_nothing_behind(self):
        manager = self.manager(open_url=opener_failing("rede fora"))
        with self.assertRaises(BrainError) as ctx:
            manager.install(
                "DEMO_MODEL",
                accept_license=True,
                url="https://huggingface.co/demo/resolve/main/demo.gguf",
            )
        self.assertIn("falha no download", str(ctx.exception))
        self.assertEqual(list(self.models.iterdir()), [])

    def test_url_guard_rejects_http_and_unknown_hosts(self):
        manager = self.manager(open_url=opener_returning(GGUF_BYTES))
        with self.assertRaises(BrainError) as ctx:
            manager.install(
                "DEMO_MODEL",
                accept_license=True,
                url="http://huggingface.co/demo/x.gguf",
            )
        self.assertIn("HTTPS", str(ctx.exception))

        with self.assertRaises(BrainError) as ctx:
            manager.install(
                "DEMO_MODEL",
                accept_license=True,
                url="https://evil.example.com/x.gguf",
            )
        self.assertIn("Host de download negado", str(ctx.exception))
        self.assertEqual(list(self.models.iterdir()), [])

    def test_bad_expected_hash_format_is_rejected(self):
        manager = self.manager(open_url=opener_returning(GGUF_BYTES))
        with self.assertRaises(BrainError):
            manager.install(
                "DEMO_MODEL",
                accept_license=True,
                url="https://huggingface.co/demo/x.gguf",
                expected_sha256="abc123",
            )

    def test_import_copies_local_file_and_verifies(self):
        source = self.root / "baixado.gguf"
        source.write_bytes(GGUF_BYTES)
        manager = self.manager()

        result = manager.install(
            "DEMO_MODEL",
            accept_license=True,
            source_path=str(source),
            expected_sha256=GGUF_SHA256,
        )
        self.assertEqual(result["source"], "import")
        self.assertEqual(result["sha256"], GGUF_SHA256)
        # o arquivo do usuario nunca e apagado
        self.assertTrue(source.is_file())

        verification = manager.verify("DEMO_MODEL")
        self.assertTrue(verification["ok"])
        self.assertEqual(verification["status"], "ok")
        self.assertEqual(verification["recorded_sha256"], GGUF_SHA256)

    def test_import_hash_mismatch_discards_the_copy(self):
        source = self.root / "baixado.gguf"
        source.write_bytes(b"errado")
        manager = self.manager()
        with self.assertRaises(BrainError):
            manager.install(
                "DEMO_MODEL",
                accept_license=True,
                source_path=str(source),
                expected_sha256=GGUF_SHA256,
            )
        self.assertFalse((self.models / "demo.Q4_K_M.gguf").exists())
        self.assertTrue(source.is_file())

    def test_missing_source_and_existing_target_are_rejected(self):
        manager = self.manager()
        with self.assertRaises(BrainError):
            manager.install(
                "DEMO_MODEL",
                accept_license=True,
                source_path=str(self.root / "nao-existe.gguf"),
            )

        target = self.models / "demo.Q4_K_M.gguf"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"pre-existente")
        with self.assertRaises(BrainError) as ctx:
            manager.install(
                "DEMO_MODEL",
                accept_license=True,
                source_path="x",
            )
        self.assertIn("ja existe", str(ctx.exception))

    def test_sha256_file_helper_matches_hashlib(self):
        target = self.root / "qualquer.bin"
        target.write_bytes(GGUF_BYTES)
        digest, size = sha256_file(target)
        self.assertEqual(digest, GGUF_SHA256)
        self.assertEqual(size, len(GGUF_BYTES))


class VerifyTests(ManagerTestCase):
    def test_tampering_is_detected(self):
        source = self.root / "baixado.gguf"
        source.write_bytes(GGUF_BYTES)
        manager = self.manager()
        manager.install(
            "DEMO_MODEL",
            accept_license=True,
            source_path=str(source),
        )

        target = self.models / "demo.Q4_K_M.gguf"
        target.write_bytes(GGUF_BYTES + b"MAIS-BYTES")

        verification = manager.verify("DEMO_MODEL")
        self.assertFalse(verification["ok"])
        self.assertEqual(verification["status"], "alterado")
        self.assertNotEqual(
            verification["recorded_sha256"],
            verification["current_sha256"],
        )

    def test_untracked_and_absent_files_are_reported(self):
        manager = self.manager()
        # nunca instalado
        self.assertEqual(manager.verify("DEMO_MODEL")["status"], "nao_rastreado")

        # arquivo solto (nao gerenciado pelo Zeus) tambem e nao rastreado
        self.models.mkdir(parents=True, exist_ok=True)
        (self.models / "demo.Q4_K_M.gguf").write_bytes(b"soltinho")
        result = manager.verify("DEMO_MODEL")
        self.assertEqual(result["status"], "nao_rastreado")
        self.assertFalse(result["ok"])

        # rastreado mas apagado
        self.db.record_model_install(
            model_id="DEMO_MODEL",
            filename="demo.Q4_K_M.gguf",
            license_name="Apache-2.0",
            license_accepted_at="2026-01-01T00:00:00Z",
            sha256=GGUF_SHA256,
            size_bytes=len(GGUF_BYTES),
            source="import",
        )
        (self.models / "demo.Q4_K_M.gguf").unlink()
        self.assertEqual(manager.verify("DEMO_MODEL")["status"], "ausente")

    def test_unknown_model_raises(self):
        manager = self.manager()
        with self.assertRaises(BrainError):
            manager.verify("NOT_REGISTERED")


class UsageAndRemovalTests(ManagerTestCase):
    def _install(self, manager):
        source = self.root / "baixado.gguf"
        source.write_bytes(GGUF_BYTES)
        return manager.install(
            "DEMO_MODEL", accept_license=True, source_path=str(source)
        )

    def test_touch_marks_last_use(self):
        manager = self.manager()
        self._install(manager)
        self.assertTrue(manager.touch("DEMO_MODEL"))
        install = self.db.get_model_install("DEMO_MODEL")
        self.assertTrue(install["last_used_at"])
        self.assertFalse(manager.touch("NOT_REGISTERED"))

    def test_remove_requires_tracked_and_confirmation(self):
        manager = self.manager()
        with self.assertRaises(BrainError):
            manager.remove("DEMO_MODEL", confirm=True)

        self._install(manager)
        with self.assertRaises(BrainError) as ctx:
            manager.remove("DEMO_MODEL")
        self.assertIn("confirmacao", str(ctx.exception))

        result = manager.remove("DEMO_MODEL", confirm=True)
        self.assertTrue(result["removed"])
        self.assertEqual(result["freed_bytes"], len(GGUF_BYTES))
        self.assertFalse((self.models / "demo.Q4_K_M.gguf").exists())
        self.assertIsNone(self.db.get_model_install("DEMO_MODEL"))


class StorageLruTests(ManagerTestCase):
    def _three_models(self):
        specs = [
            # ALPHA e o unico que exige mais RAM que o resto
            make_spec(model_id="ALPHA", filename="alpha.gguf", min_ram_gb=2.0),
            make_spec(model_id="BETA", filename="beta.gguf", min_ram_gb=1.0),
            make_spec(model_id="GAMMA", filename="gamma.gguf", min_ram_gb=1.0),
        ]
        registry = registry_with(specs)
        self.registry = registry
        manager = ModelManager(self.db, registry=registry, directory=self.models)
        self.models.mkdir(parents=True, exist_ok=True)
        for spec, used_at in (
            (specs[0], "2026-01-01T00:00:00Z"),  # mais antigo
            (specs[1], "2026-03-01T00:00:00Z"),  # meio
            (specs[2], "2026-02-01T00:00:00Z"),  # meio
        ):
            (self.models / spec.filename).write_bytes(GGUF_BYTES)
            self.db.record_model_install(
                model_id=spec.model_id,
                filename=spec.filename,
                license_name="Apache-2.0",
                license_accepted_at="2026-01-01T00:00:00Z",
                sha256=GGUF_SHA256,
                size_bytes=len(GGUF_BYTES),
                source="import",
            )
            self.db.touch_model_used(spec.model_id, used_at=used_at)
        return manager, specs

    def test_plan_keeps_recent_and_evicts_lru_first(self):
        manager, _ = self._three_models()
        # cabem 2 arquivos: BETA (mais recente) e GAMMA ficam; ALPHA (LRU) sai.
        budget = (len(GGUF_BYTES) * 2) / (1024**3)
        status = manager.storage_status(budget_gb=budget, available_ram=64.0)

        self.assertEqual(status["plan"]["keep"], ["BETA", "GAMMA"])
        self.assertEqual(len(status["plan"]["evict"]), 1)
        self.assertEqual(status["plan"]["evict"][0]["model_id"], "ALPHA")
        self.assertEqual(status["plan"]["evict"][0]["reason"], "lru")
        self.assertFalse(status["plan"]["fits"])
        self.assertEqual(status["tracked_count"], 3)

    def test_ram_guard_demotes_models_that_do_not_fit(self):
        manager, _ = self._three_models()
        budget = (len(GGUF_BYTES) * 2) / (1024**3)
        # RAM apertada: ALPHA exige 2 GB mas ha so 1.5 GB -> sai antes,
        # mesmo sendo o mais usado recentemente.
        self.db.touch_model_used("ALPHA", used_at="2026-06-01T00:00:00Z")
        status = manager.storage_status(budget_gb=budget, available_ram=1.5)

        evicted = [item["model_id"] for item in status["plan"]["evict"]]
        self.assertIn("ALPHA", evicted)
        alpha = next(
            item for item in status["installs"] if item["model_id"] == "ALPHA"
        )
        self.assertFalse(alpha["fits_ram"])
        self.assertEqual(status["plan"]["evict"][0]["reason"], "memoria")

    def test_untracked_files_are_never_planned(self):
        manager, _ = self._three_models()
        (self.models / "soltinho.gguf").write_bytes(b"usuario")
        budget = (len(GGUF_BYTES) * 2) / (1024**3)
        status = manager.storage_status(budget_gb=budget, available_ram=64.0)
        planned = status["plan"]["keep"] + [
            item["model_id"] for item in status["plan"]["evict"]
        ]
        self.assertNotIn("soltinho.gguf", planned)
        self.assertEqual(status["tracked_count"], 3)

    def test_prune_requires_confirmation_and_frees_space(self):
        manager, _ = self._three_models()
        budget = (len(GGUF_BYTES) * 2) / (1024**3)

        with self.assertRaises(BrainError):
            manager.prune(budget_gb=budget, confirm=False)

        result = manager.prune(budget_gb=budget, confirm=True, available_ram=64.0)
        self.assertEqual(len(result["removed"]), 1)
        self.assertEqual(result["removed"][0]["model_id"], "ALPHA")
        self.assertEqual(result["kept"], ["BETA", "GAMMA"])
        # stubs tem 2 KB; o byte exato e o que importa (freed_gb = 0.0 aqui)
        self.assertEqual(
            result["removed"][0]["freed_bytes"], len(GGUF_BYTES)
        )
        self.assertGreaterEqual(result["freed_gb"], 0)
        self.assertFalse((self.models / "alpha.gguf").exists())
        self.assertIsNone(self.db.get_model_install("ALPHA"))
        self.assertIsNotNone(self.db.get_model_install("BETA"))

    def test_storage_status_rejects_bad_budget(self):
        manager, _ = self._three_models()
        with self.assertRaises(BrainError):
            manager.storage_status(budget_gb=0)

    def test_default_budget_is_positive(self):
        self.assertGreater(DEFAULT_DISK_BUDGET_GB, 0)


class StubEchoHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        body = json.dumps({"status": "ok"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            request = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            request = {}
        received = request.get("messages") or [{}]
        content = str(received[-1].get("content", ""))
        payload = {
            "choices": [
                {"message": {"role": "assistant", "content": f"eco:{content}"}}
            ],
            "usage": {
                "prompt_tokens": len(content.split()),
                "completion_tokens": 2,
                "total_tokens": len(content.split()) + 2,
            },
        }
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class StubServer:
    def __enter__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), StubEchoHandler)
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.base_url = f"http://{host}:{port}"
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        return False


class FidelityTests(unittest.TestCase):
    def test_language_fidelity_detects_real_languages(self):
        self.assertEqual(language_fidelity("ja", "このPCは速いです。"), 1.0)
        self.assertEqual(language_fidelity("ja", "This is fast."), 0.0)
        self.assertEqual(
            language_fidelity("en", "Your computer is running slowly."), 1.0
        )
        self.assertEqual(language_fidelity("en", "Seu PC está lento."), 0.0)
        self.assertEqual(
            language_fidelity("pt", "Seu computador está lento."), 1.0
        )
        self.assertEqual(
            language_fidelity("pt", "Your computer is slow."), 0.0
        )

    def test_unknown_language_raises(self):
        with self.assertRaises(BrainError):
            language_fidelity("kl", "qualquer coisa")

    def test_score_saturates_at_cpu_reference(self):
        # fidelidade perfeita + velocidade na referencia = 100
        self.assertEqual(score_turn(1.0, 20.0), 100.0)
        # acima da referencia nao soma nota extra
        self.assertEqual(score_turn(1.0, 500.0), 100.0)
        # sem fidelidade, so a velocidade vale (metade = 20)
        self.assertEqual(score_turn(0.0, 10.0), 20.0)

    def test_suite_is_closed_and_deterministic(self):
        self.assertEqual(LANGUAGE_SET, ("ja", "en", "pt"))
        languages = {item.language for item in BENCHMARK_PROMPTS}
        self.assertEqual(languages, set(LANGUAGE_SET))
        self.assertEqual(BENCHMARK_PROMPTS, BENCHMARK_PROMPTS)
        # 2 prompts por idioma, tarefas do ModelTask
        for language in LANGUAGE_SET:
            count = sum(
                1 for item in BENCHMARK_PROMPTS if item.language == language
            )
            self.assertEqual(count, 2)
        for item in BENCHMARK_PROMPTS:
            self.assertIsInstance(item.task, ModelTask)


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.models = self.root / "models"
        self.models.mkdir()
        (self.models / "demo.Q4_K_M.gguf").write_bytes(GGUF_BYTES)
        self.db = ZeusDatabase(self.root / "zeus.db")
        self.db.initialize()
        self.registry = registry_with([make_spec()])
        patcher = patch.dict(
            os.environ, {"ZEUS_MODELS_DIR": str(self.models)}
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_benchmark_measures_scores_and_journals_them(self):
        with StubServer() as stub:
            result = run_benchmark(
                "DEMO_MODEL",
                registry=self.registry,
                database=self.db,
                base_url=stub.base_url,
            )

        self.assertEqual(result["model_id"], "DEMO_MODEL")
        self.assertEqual(result["prompt_count"], len(BENCHMARK_PROMPTS))
        self.assertEqual(set(result["languages"]), set(LANGUAGE_SET))
        for language in LANGUAGE_SET:
            block = result["languages"][language]
            self.assertEqual(block["prompts"], 2)
            # o eco do stub repete o prompt no idioma pedido
            self.assertEqual(block["mean_fidelity"], 1.0)
            self.assertGreater(block["score"], 0)
        self.assertGreater(result["score"], 0)
        for row in result["results"]:
            self.assertTrue(row["ok"])
            self.assertEqual(row["fidelity"], 1.0)

        # tudo journalizado e agregado para o roteador
        stored = self.db.latest_benchmarks("DEMO_MODEL")
        self.assertEqual(len(stored), len(BENCHMARK_PROMPTS))
        scores = self.db.benchmark_scores()
        self.assertIn("DEMO_MODEL", scores)
        self.assertAlmostEqual(
            scores["DEMO_MODEL"], result["score"], places=2
        )

    def test_benchmark_language_subset_is_respected(self):
        with StubServer() as stub:
            result = run_benchmark(
                "DEMO_MODEL",
                registry=self.registry,
                database=self.db,
                base_url=stub.base_url,
                languages=("pt",),
            )
        self.assertEqual(result["prompt_count"], 2)
        self.assertEqual(set(result["languages"]), {"pt"})

    def test_benchmark_fails_closed_without_runtime_or_model(self):
        with self.assertRaises(BrainError) as ctx:
            run_benchmark(
                "DEMO_MODEL",
                registry=self.registry,
                database=self.db,
                base_url="http://127.0.0.1:1",
            )
        self.assertIn("Runtime indisponivel", str(ctx.exception))

        (self.models / "demo.Q4_K_M.gguf").unlink()
        with StubServer() as stub:
            with self.assertRaises(BrainError) as ctx:
                run_benchmark(
                    "DEMO_MODEL",
                    registry=self.registry,
                    base_url=stub.base_url,
                )
        self.assertIn("GGUF ausente", str(ctx.exception))

        with self.assertRaises(BrainError):
            run_benchmark("NOT_REGISTERED", registry=self.registry)
        with self.assertRaises(BrainError):
            run_benchmark(
                "DEMO_MODEL",
                registry=self.registry,
                languages=("pt", "kl"),
            )
        with self.assertRaises(BrainError):
            run_benchmark(
                "DEMO_MODEL", registry=self.registry, languages=()
            )


class RouterScoreTests(unittest.TestCase):
    def test_benchmark_score_beats_static_rank(self):
        with tempfile.TemporaryDirectory() as tmp:
            models = Path(tmp)
            registry = registry_with(
                [
                    make_spec(model_id="TOP_RANK", quality_rank=1),
                    make_spec(
                        model_id="MEASURED",
                        filename="measured.gguf",
                        quality_rank=9,
                    ),
                ]
            )
            (models / "demo.Q4_K_M.gguf").write_bytes(b"")
            (models / "measured.gguf").write_bytes(b"")

            # sem notas: o rank estatico vence
            decision = route_task(
                "CHAT", registry=registry, models_dir=models
            )
            self.assertEqual(decision.model_id, "TOP_RANK")

            # com nota medida: a medicao vence o rank declarado
            decision = route_task(
                "CHAT",
                registry=registry,
                models_dir=models,
                scores={"MEASURED": 88.5},
            )
            self.assertEqual(decision.model_id, "MEASURED")
            self.assertIn("nota de benchmark 88.5", decision.reason)


class PersistenceV4Tests(unittest.TestCase):
    def test_schema_version_and_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            health = db.initialize()
            self.assertEqual(health.schema_version, SCHEMA_VERSION)
            self.assertGreaterEqual(SCHEMA_VERSION, 4)
            with db.connect() as connection:
                names = {
                    row["name"]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
            self.assertIn("model_installs", names)
            self.assertIn("model_benchmarks", names)
            # idempotente
            db.initialize()
            db.initialize()

    def test_benchmark_scores_use_latest_row_per_language(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            db.initialize()
            common = {
                "model_id": "DEMO_MODEL",
                "elapsed_ms": 100.0,
                "prompt_tokens": 5,
                "completion_tokens": 10,
                "tokens_per_s": 100.0,
                "fidelity": 1.0,
            }
            db.record_benchmark(language="pt", task="CHAT", score=40.0, **common)
            db.record_benchmark(language="pt", task="CHAT", score=80.0, **common)
            db.record_benchmark(language="en", task="CHAT", score=60.0, **common)

            scores = db.benchmark_scores()
            # pt usa a linha mais recente (80), en usa 60 -> media 70
            self.assertEqual(scores, {"DEMO_MODEL": 70.0})


class DoctorV4Tests(unittest.TestCase):
    def test_doctor_reports_storage_and_benchmarks(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            db.initialize()
            report = build_doctor_report(db)

        names = [check["name"] for check in report["checks"]]
        self.assertIn("model_storage", names)
        self.assertIn("brain_benchmarks", names)
        self.assertIn("storage", report)
        self.assertIn("benchmark_scores", report)
        self.assertEqual(report["benchmark_scores"], {})
        self.assertEqual(report["storage"]["tracked_count"], 0)
        storage_check = next(
            check for check in report["checks"] if check["name"] == "model_storage"
        )
        self.assertTrue(storage_check["ok"])
        json.dumps(report, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
