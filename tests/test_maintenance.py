import tempfile
import unittest
from pathlib import Path
from zeus_pc.maintenance import MaintenanceJobs


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.jobs = MaintenanceJobs(Path(self.temp.name), launcher=lambda *args: None)

    def test_job_runs_closed_stages_and_persists_output(self):
        job = self.jobs.start('system_scan')
        executed = []
        def runner(command, emit):
            executed.append(command)
            emit('test output\n')
            return 0
        finished = self.jobs.run(job['id'], runner=runner, admin=True)
        self.assertEqual(finished['state'], 'succeeded')
        self.assertEqual(len(executed), 2)
        self.assertIn('/ScanHealth', executed[0])
        self.assertIn('/verifyonly', executed[1])
        self.assertIn('test output', self.jobs.list()[0]['output'])

    def test_failure_stops_the_repair_sequence(self):
        job = self.jobs.start('system_repair')
        commands = []
        def runner(command, emit):
            commands.append(command)
            return 5
        result = self.jobs.run(job['id'], runner=runner, admin=True)
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(len(commands), 1)

    def test_unknown_operation_and_path_traversal_are_rejected(self):
        for operation in ('powershell', '../test', 'install_lively'):
            with self.assertRaises(ValueError):
                self.jobs.start(operation)
        with self.assertRaises(ValueError):
            self.jobs.run('../file', admin=True)

    def test_elevated_operations_never_run_as_a_standard_user(self):
        job = self.jobs.start('system_repair')
        commands = []
        result = self.jobs.run(job['id'], runner=lambda argv, emit: commands.append(argv), admin=False)
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(commands, [])


if __name__ == '__main__':
    unittest.main()
