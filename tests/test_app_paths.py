"""Testes do app_paths — caminhos dev e frozen (Increment 023).

Em desenvolvimento os caminhos devem apontar para a raiz do repositório
(web/, data/) exatamente como antes. Simulando sys.frozen + sys._MEIPASS
(PyInstaller), os assets vêm do bundle e o dado persistente vai para
%LOCALAPPDATA%\\ZeusPC\\data.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from zeus_pc import app_paths, server
from zeus_pc.brain import runtime


class AppPathsDevTests(unittest.TestCase):
    def test_dev_paths_match_repo_layout(self) -> None:
        repo = Path(app_paths.__file__).resolve().parents[2]
        self.assertFalse(app_paths.is_frozen())
        self.assertEqual(app_paths.bundle_dir(), repo)
        self.assertEqual(app_paths.web_dir(), repo / "web")
        self.assertEqual(app_paths.data_dir(), repo / "data")

    def test_server_still_points_at_repo_web_and_data(self) -> None:
        self.assertEqual(server.WEB, app_paths.web_dir())
        self.assertEqual(server.DATA, app_paths.data_dir())
        self.assertTrue(str(server.DB.path).startswith(str(app_paths.data_dir())))

    def test_models_dir_matches_repo_data_models(self) -> None:
        repo = Path(app_paths.__file__).resolve().parents[2]
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZEUS_MODELS_DIR", None)
            os.environ.pop("ZEUS_DATA_DIR", None)
            self.assertEqual(runtime.models_dir(), repo / "data" / "models")

    def test_data_dir_env_override_wins(self) -> None:
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"ZEUS_DATA_DIR": tmp}):
                self.assertEqual(app_paths.data_dir(), Path(tmp))


class AppPathsFrozenTests(unittest.TestCase):
    def test_frozen_web_from_bundle_and_data_from_localappdata(self) -> None:
        with TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "meipass"
            local = Path(tmp) / "localappdata"
            bundle.mkdir()
            with patch.dict(os.environ, {"LOCALAPPDATA": str(local)}):
                os.environ.pop("ZEUS_DATA_DIR", None)
                with (
                    patch.object(sys, "frozen", True, create=True),
                    patch.object(sys, "_MEIPASS", str(bundle), create=True),
                ):
                    self.assertTrue(app_paths.is_frozen())
                    self.assertEqual(app_paths.bundle_dir(), bundle)
                    self.assertEqual(app_paths.web_dir(), bundle / "web")
                    self.assertEqual(app_paths.data_dir(), local / "ZeusPC" / "data")
                    self.assertEqual(
                        runtime.models_dir(),
                        local / "ZeusPC" / "data" / "models",
                    )

    def test_frozen_data_env_override_wins(self) -> None:
        with TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "meipass"
            data = Path(tmp) / "portable-data"
            bundle.mkdir()
            with patch.dict(os.environ, {"ZEUS_DATA_DIR": str(data)}):
                with (
                    patch.object(sys, "frozen", True, create=True),
                    patch.object(sys, "_MEIPASS", str(bundle), create=True),
                ):
                    self.assertEqual(app_paths.data_dir(), data)

    def test_frozen_without_meipass_falls_back_to_executable_dir(self) -> None:
        with TemporaryDirectory() as tmp:
            exe = Path(tmp) / "ZeusPC.exe"
            exe.write_bytes(b"MZ")
            with patch.dict(os.environ, {"LOCALAPPDATA": str(Path(tmp) / "la")}):
                os.environ.pop("ZEUS_DATA_DIR", None)
                with (
                    patch.object(sys, "frozen", True, create=True),
                    patch.object(sys, "_MEIPASS", None, create=True),
                    patch.object(sys, "executable", str(exe)),
                ):
                    # _MEIPASS None (modo onedir sem extração) -> pasta do EXE.
                    # resolve() nos dois lados: o runner do CI expõe o temp
                    # como nome 8.3 (RUNNER~1) e resolve() expande para o nome
                    # longo — a comparação é pela pasta, não pela grafia.
                    base = Path(tmp).resolve()
                    self.assertEqual(app_paths.bundle_dir(), base)
                    self.assertEqual(app_paths.web_dir(), base / "web")

    def test_is_frozen_false_in_normal_runs(self) -> None:
        self.assertFalse(app_paths.is_frozen())


if __name__ == "__main__":
    unittest.main()
