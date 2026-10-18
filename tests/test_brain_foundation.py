import json
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from zeus_pc.brain import (
    DEFAULT_MODEL_REGISTRY,
    BrainError,
    ModelRegistry,
    ModelSpec,
    ModelTask,
    build_default_model_registry,
    invoke,
    route_task,
)
from zeus_pc.brain.registry import MODEL_CATALOG_VERSION, MODEL_ID_RE
from zeus_pc.brain.runtime import (
    assert_loopback,
    chat_complete,
    models_dir,
    probe_server,
    runtime_status,
)
from zeus_pc.doctor import build_doctor_report
from zeus_pc.persistence import ZeusDatabase


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
    }
    base.update(overrides)
    return ModelSpec(**base)


def registry_with(specs):
    registry = ModelRegistry()
    for spec in specs:
        registry.register(spec)
    return registry


class StubCompletionHandler(BaseHTTPRequestHandler):
    """Minimal OpenAI-compatible llama-server stand-in."""

    def log_message(self, fmt, *args):  # silence test output
        return

    def do_GET(self):
        if self.path == "/health":
            body = json.dumps({"status": "ok"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

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
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), StubCompletionHandler
        )
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


class ModelRegistryTests(unittest.TestCase):
    def test_default_lineup_is_deterministic_and_complete(self):
        first = build_default_model_registry().to_dict()
        second = build_default_model_registry().to_dict()

        self.assertEqual(first, second)
        self.assertEqual(first["version"], MODEL_CATALOG_VERSION)
        ids = [item["model_id"] for item in first["models"]]
        self.assertEqual(ids, sorted(ids))
        for expected in (
            "LLM_JP_1_8B",
            "ELYZA_JP_8B",
            "SWALLOW_7B",
            "STABLELM_JP_7B",
            "RINNA_3_6B",
            "SARASHINA2_7B",
            "QWEN25_7B",
        ):
            self.assertIn(expected, ids)
        self.assertEqual(first["count"], len(ids))

    def test_specs_declare_license_language_and_memory(self):
        for spec in DEFAULT_MODEL_REGISTRY.ids():
            payload = DEFAULT_MODEL_REGISTRY.get(spec).to_dict()
            self.assertTrue(payload["license_name"])
            self.assertTrue(payload["languages"])
            self.assertGreater(payload["min_ram_gb"], 0)
            self.assertGreater(payload["size_gb"], 0)
            self.assertTrue(payload["filename"].endswith(".gguf"))
            self.assertTrue(
                MODEL_ID_RE.fullmatch(payload["model_id"])
            )

    def test_multilingual_reference_covers_portuguese(self):
        payloads = DEFAULT_MODEL_REGISTRY.to_dict()["models"]
        with_pt = [
            item for item in payloads if "pt" in item["languages"]
        ]
        self.assertEqual(len(with_pt), 1)
        self.assertEqual(with_pt[0]["model_id"], "QWEN25_7B")

    def test_duplicate_model_id_is_rejected(self):
        registry = registry_with([make_spec()])
        with self.assertRaises(BrainError):
            registry.register(make_spec())

    def test_malformed_specs_are_rejected(self):
        cases = [
            make_spec(model_id="bad id"),
            make_spec(filename="../../evil.gguf"),
            make_spec(filename="no-extension"),
            make_spec(quantization="FAST"),
            make_spec(size_gb=0),
            make_spec(min_ram_gb=-1),
            make_spec(license_name=""),
            make_spec(languages=()),
            make_spec(strengths=()),
            make_spec(quality_rank=0),
        ]
        for spec in cases:
            with self.assertRaises(BrainError):
                ModelRegistry().register(spec)

    def test_for_task_orders_by_quality_rank(self):
        registry = registry_with(
            [
                make_spec(model_id="SLOW_ONE", quality_rank=9),
                make_spec(
                    model_id="FAST_ONE",
                    filename="fast.Q4_K_M.gguf",
                    quality_rank=1,
                ),
            ]
        )
        ordered = [item.model_id for item in registry.for_task("CHAT")]
        self.assertEqual(ordered, ["FAST_ONE", "SLOW_ONE"])


class RouterTests(unittest.TestCase):
    def test_routes_to_present_file_over_missing_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            models = Path(tmp)
            registry = registry_with(
                [
                    make_spec(model_id="RANK_1", quality_rank=1),
                    make_spec(
                        model_id="RANK_2",
                        filename="other.Q4_K_M.gguf",
                        quality_rank=2,
                    ),
                ]
            )
            (models / "other.Q4_K_M.gguf").write_bytes(b"gguf")

            decision = route_task(
                "CHAT",
                registry=registry,
                models_dir=models,
            )

            self.assertTrue(decision.ok)
            self.assertEqual(decision.model_id, "RANK_2")
            statuses = {
                item.model_id: item.status
                for item in decision.considered
            }
            self.assertEqual(statuses["RANK_1"], "sem_arquivo")
            self.assertEqual(statuses["RANK_2"], "ok")

    def test_memory_guard_skips_models_that_do_not_fit(self):
        with tempfile.TemporaryDirectory() as tmp:
            models = Path(tmp)
            registry = registry_with(
                [
                    make_spec(
                        model_id="HEAVY",
                        filename="heavy.Q4_K_M.gguf",
                        min_ram_gb=32,
                    ),
                    make_spec(
                        model_id="LIGHT",
                        filename="light.Q4_K_M.gguf",
                        min_ram_gb=2,
                        quality_rank=5,
                    ),
                ]
            )
            (models / "heavy.Q4_K_M.gguf").write_bytes(b"")
            (models / "light.Q4_K_M.gguf").write_bytes(b"")

            decision = route_task(
                "CHAT",
                registry=registry,
                models_dir=models,
                available_ram_gb=8,
            )

            self.assertEqual(decision.model_id, "LIGHT")
            statuses = {
                item.model_id: item.status
                for item in decision.considered
            }
            self.assertEqual(statuses["HEAVY"], "memoria")

    def test_decision_explains_when_nothing_is_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            decision = route_task(
                "PLAN",
                registry=DEFAULT_MODEL_REGISTRY,
                models_dir=Path(tmp),
            )
            self.assertFalse(decision.ok)
            self.assertIsNone(decision.model_id)
            self.assertIn("nenhum modelo disponivel", decision.reason)
            statuses = {
                item.model_id: item.status
                for item in decision.considered
            }
            # Candidatos de PLAN nao tem arquivo; os demais nem servem PLAN.
            self.assertEqual(statuses["ELYZA_JP_8B"], "sem_arquivo")
            self.assertEqual(statuses["SWALLOW_7B"], "sem_arquivo")
            self.assertEqual(statuses["LLM_JP_1_8B"], "sem_tarefa")

    def test_unknown_task_or_model_raise_brain_error(self):
        with self.assertRaises(BrainError):
            route_task("TELEPORT")
        with self.assertRaises(BrainError):
            route_task("CHAT", model_id="NOT_REGISTERED")

    def test_explicit_choice_is_strict_about_file_and_ram(self):
        with tempfile.TemporaryDirectory() as tmp:
            models = Path(tmp)
            with self.assertRaises(BrainError):
                route_task(
                    "CHAT",
                    registry=DEFAULT_MODEL_REGISTRY,
                    models_dir=models,
                    model_id="LLM_JP_1_8B",
                )
            (models / "llm-jp-3.1-1.8b-instruct4.Q4_K_M.gguf").write_bytes(b"")
            with self.assertRaises(BrainError):
                route_task(
                    "CHAT",
                    registry=DEFAULT_MODEL_REGISTRY,
                    models_dir=models,
                    model_id="LLM_JP_1_8B",
                    available_ram_gb=1.0,
                )

    def test_routing_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            models = Path(tmp)
            (models / "demo.Q4_K_M.gguf").write_bytes(b"")
            registry = registry_with([make_spec()])
            first = route_task("CHAT", registry=registry, models_dir=models)
            second = route_task("CHAT", registry=registry, models_dir=models)
            self.assertEqual(first.to_dict(), second.to_dict())


class RuntimeTests(unittest.TestCase):
    def test_loopback_only_urls(self):
        self.assertEqual(
            assert_loopback("http://127.0.0.1:8081"),
            "http://127.0.0.1:8081",
        )
        for bad in (
            "http://evil.example.com/v1",
            "http://192.168.0.10:8081",
            "ftp://127.0.0.1",
            "sem-esquema",
        ):
            with self.assertRaises(BrainError):
                assert_loopback(bad)

    def test_probe_reports_unreachable_without_raising(self):
        result = probe_server("http://127.0.0.1:1", timeout=0.3)
        self.assertFalse(result["reachable"])
        self.assertIn("sem resposta", result["detail"])

    def test_probe_and_chat_complete_against_stub_server(self):
        with StubServer() as stub:
            probe = probe_server(stub.base_url, timeout=2)
            self.assertTrue(probe["reachable"])
            self.assertEqual(probe["detail"], "ok")

            result = chat_complete(
                "ola zeus",
                model="DEMO_MODEL",
                base_url=stub.base_url,
            )
            self.assertEqual(result["content"], "eco:ola zeus")
            # 2 tokens de prompt + 2 de resposta, conforme o stub.
            self.assertEqual(result["usage"]["total_tokens"], 4)

    def test_chat_complete_rejects_empty_and_oversized_prompts(self):
        with StubServer() as stub:
            with self.assertRaises(BrainError):
                chat_complete(
                    "   ",
                    model="X",
                    base_url=stub.base_url,
                )
            with self.assertRaises(BrainError):
                chat_complete(
                    "x" * 12001,
                    model="X",
                    base_url=stub.base_url,
                )

    def test_default_models_dir_is_repo_data_not_src_data(self):
        import zeus_pc

        repo_root = Path(zeus_pc.__file__).resolve().parents[2]
        self.assertEqual(
            models_dir(), repo_root / "data" / "models",
        )
        self.assertNotIn(
            "/src/data/models", str(models_dir()),
        )

    def test_runtime_status_shape(self):
        status = runtime_status("http://127.0.0.1:1")
        payload = status.to_dict()
        self.assertIn("server_available", payload)
        self.assertIn("models_dir", payload)
        self.assertIn("total_ram_gb", payload)
        self.assertFalse(payload["reachable"])


class InvokeTests(unittest.TestCase):
    def test_invoke_routes_and_completes_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            models = Path(tmp)
            (models / "demo.Q4_K_M.gguf").write_bytes(b"")
            registry = registry_with([make_spec()])
            with StubServer() as stub:
                result = invoke(
                    "chat",
                    "ping",
                    registry=registry,
                    models_dir_path=models,
                    base_url=stub.base_url,
                )

        self.assertEqual(result["model_id"], "DEMO_MODEL")
        self.assertEqual(result["task"], "CHAT")
        self.assertEqual(result["content"], "eco:ping")
        self.assertTrue(result["routing"]["ok"])
        self.assertGreaterEqual(result["elapsed_ms"], 0)

    def test_invoke_fails_closed_without_runtime_or_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            models = Path(tmp)
            registry = registry_with([make_spec()])
            with self.assertRaises(BrainError) as ctx:
                invoke(
                    "CHAT",
                    "ping",
                    registry=registry,
                    models_dir_path=models,
                    base_url="http://127.0.0.1:1",
                )
            # Runtime down but file missing: routing fails first.
            self.assertTrue(
                "nenhum modelo" in str(ctx.exception)
                or "Runtime indisponivel" in str(ctx.exception)
            )

            (models / "demo.Q4_K_M.gguf").write_bytes(b"")
            with self.assertRaises(BrainError) as ctx:
                invoke(
                    "CHAT",
                    "ping",
                    registry=registry,
                    models_dir_path=models,
                    base_url="http://127.0.0.1:1",
                )
            self.assertIn("Runtime indisponivel", str(ctx.exception))


class DoctorTests(unittest.TestCase):
    def test_report_lists_every_model_and_named_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            db.initialize()  # mesmo bootstrap do main do servidor
            report = build_doctor_report(db)

        names = [check["name"] for check in report["checks"]]
        self.assertIn("persistence", names)
        self.assertIn("powercfg", names)
        self.assertIn("brain_server", names)
        self.assertIn("models_dir", names)
        self.assertEqual(
            len(report["models"]), len(DEFAULT_MODEL_REGISTRY.ids())
        )
        for state in report["models"]:
            self.assertIn("present", state)
            self.assertIn("fits_ram", state)
        persistence = next(
            check for check in report["checks"] if check["name"] == "persistence"
        )
        self.assertTrue(persistence["ok"])
        self.assertEqual(
            report["registered_models"],
            len(DEFAULT_MODEL_REGISTRY.ids()),
        )
        self.assertEqual(
            report["registered_models"], len(report["models"]),
        )

    def test_report_is_json_serializable(self):
        report = build_doctor_report()
        encoded = json.dumps(report, ensure_ascii=False)
        self.assertIn("models", encoded)


if __name__ == "__main__":
    unittest.main()
