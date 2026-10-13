import subprocess
import unittest
from unittest.mock import patch

from zeus_pc import powershell
from zeus_pc.powershell import CONSOLE_ENCODING_PREFIX, run, run_json

EXE = r"C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe"


def completed(returncode=0, stdout=b"", stderr=b""):
    return subprocess.CompletedProcess(args=[EXE], returncode=returncode, stdout=stdout, stderr=stderr)


class PowerShellRunTests(unittest.TestCase):
    @patch("zeus_pc.powershell.subprocess.run")
    @patch("zeus_pc.powershell.shutil.which", return_value=EXE)
    def test_run_decodes_utf8_accented_output(self, which, run_mock):
        run_mock.return_value = completed(stdout="operação".encode("utf-8"))

        code, out, err = run("Write-Output operacao")

        self.assertEqual(code, 0)
        self.assertEqual(out, "operação")
        self.assertEqual(err, "")

    @patch("zeus_pc.powershell.subprocess.run")
    @patch("zeus_pc.powershell.shutil.which", return_value=EXE)
    def test_run_uses_console_encoding_prefix_and_bytes_mode(self, which, run_mock):
        run_mock.return_value = completed(stdout=b"ok")

        run("Get-Foo")

        command = run_mock.call_args.args[0]
        self.assertEqual(command[0], EXE)
        self.assertTrue(command[-1].startswith(CONSOLE_ENCODING_PREFIX))
        self.assertTrue(command[-1].endswith("Get-Foo"))
        self.assertNotIn("text", run_mock.call_args.kwargs)

    @patch("zeus_pc.powershell.subprocess.run")
    @patch("zeus_pc.powershell.shutil.which", return_value=EXE)
    def test_run_json_appends_convertto_json_after_prefix(self, which, run_mock):
        run_mock.return_value = completed(stdout=b'{"Pending":0}')

        data = run_json("Get-Foo")

        command = run_mock.call_args.args[0]
        self.assertTrue(command[-1].startswith(CONSOLE_ENCODING_PREFIX))
        self.assertIn("ConvertTo-Json", command[-1])
        self.assertEqual(data, {"Pending": 0})

    @patch("zeus_pc.powershell.subprocess.run")
    @patch("zeus_pc.powershell.shutil.which", return_value=EXE)
    def test_timeout_returns_clean_message_without_command_line(self, which, run_mock):
        blob = [EXE, "-Command", "Get-WindowsOptionalFeature -Online | Select-Object FeatureName"]
        run_mock.side_effect = subprocess.TimeoutExpired(cmd=blob, timeout=1)

        code, out, err = run("Get-WindowsOptionalFeature -Online")

        self.assertEqual(code, 124)
        self.assertEqual(out, "")
        self.assertTrue(err)
        self.assertNotIn("powershell", err.lower())
        self.assertNotIn("Select-Object", err)

    @patch("zeus_pc.powershell.subprocess.run")
    @patch("zeus_pc.powershell.shutil.which", return_value=EXE)
    def test_timeout_maps_to_clean_message_in_run_json(self, which, run_mock):
        blob = [EXE, "-Command", "Get-WindowsUpdateSecret"]
        run_mock.side_effect = subprocess.TimeoutExpired(cmd=blob, timeout=1)

        with self.assertRaises(RuntimeError) as ctx:
            run_json("Get-WindowsUpdateSecret")

        self.assertNotIn("powershell", str(ctx.exception).lower())
        self.assertNotIn("Get-WindowsUpdateSecret", str(ctx.exception))

    @patch("zeus_pc.powershell.subprocess.run")
    @patch("zeus_pc.powershell.shutil.which", return_value=EXE)
    def test_non_zero_strips_script_echo_keeps_elevation_reason(self, which, run_mock):
        stderr = (
            "Get-WindowsOptionalFeature : A opera\xc3\xa7\xc3\xa3o solicitada requer eleva\xc3\xa7\xc3\xa3o.\r\n"
            "No linha:1 caractere:1\r\n"
            "+ Get-WindowsOptionalFeature -Online | Select-Object FeatureName\r\n"
            "+ ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~\r\n"
            "    + CategoryInfo          : NotSpecified\r\n"
        ).encode("utf-8")
        run_mock.return_value = completed(returncode=1, stderr=stderr)

        code, out, err = run("Get-WindowsOptionalFeature -Online | Select-Object FeatureName")

        self.assertEqual(code, 1)
        self.assertIn("requer eleva", err)
        self.assertNotIn("Select-Object", err)
        self.assertNotIn("CategoryInfo", err)

    @patch("zeus_pc.powershell.subprocess.run")
    @patch("zeus_pc.powershell.shutil.which", return_value=EXE)
    def test_os_error_returns_clean_message(self, which, run_mock):
        run_mock.side_effect = OSError("cannot execute")

        code, out, err = run("Get-Foo")

        self.assertEqual(code, 127)
        self.assertNotIn("powershell", err.lower())
        self.assertTrue(err)


if __name__ == "__main__":
    unittest.main()
