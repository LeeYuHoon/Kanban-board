"""Exercise port changes with an isolated app copy and database."""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path


@unittest.skipUnless(sys.platform == 'darwin', 'The launcher uses macOS launchctl')
class LauncherPortTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='kanban-port-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = Path(__file__).resolve().parents[1]
        (self.root / 'bin').mkdir()
        (self.root / 'home').mkdir()
        shutil.copy2(source / 'bin/kanban', self.root / 'bin/kanban')
        shutil.copytree(source / 'kanban', self.root / 'kanban', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(source / 'web', self.root / 'web')
        self.ports = []
        self.env = dict(os.environ, HOME=str(self.root / 'home'),
                        KANBAN_DB=str(self.root / 'test.sqlite3'),
                        KANBAN_SESSION_ROOTS=json.dumps({'codex': [], 'claude': []}),
                        KANBAN_LABEL='com.kanban.port-switch-test')
        self.addCleanup(self.stop_all)

    def free_port(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        self.ports.append(port)
        return port

    def command(self, port, action):
        result = subprocess.run([str(self.root / 'bin/kanban'), action, f'KANBAN_PORT={port}'],
                                env=self.env, capture_output=True, timeout=22)
        return result.returncode, result.stdout.decode('utf-8', 'replace'), result.stderr.decode('utf-8', 'replace')

    def healthy(self, port):
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health', timeout=2) as response:
            return json.load(response)['app'] == 'Kanban'

    def stop_all(self):
        for port in self.ports:
            self.command(port, 'stop')

    def test_start_switches_running_server_to_new_port(self):
        first, second = self.free_port(), self.free_port()
        for port in (first, second):
            code, output, error = self.command(port, 'start')
            self.assertEqual(code, 0, output + error)
            self.assertTrue(self.healthy(port))
        with socket.socket() as probe:
            self.assertNotEqual(probe.connect_ex(('127.0.0.1', first)), 0)
        self.assertFalse((self.root / '.local' / f'kanban-{first}.pid').exists())
        self.assertTrue((self.root / '.local' / f'kanban-{second}.pid').exists())

    def test_occupied_new_port_leaves_old_server_running(self):
        first, second = self.free_port(), self.free_port()
        code, output, error = self.command(first, 'start')
        self.assertEqual(code, 0, output + error)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', second))
            listener.listen()
            code, output, error = self.command(second, 'start')
        self.assertNotEqual(code, 0, output + error)
        self.assertIn('다른 프로그램이 쓰고 있습니다', error)
        self.assertTrue(self.healthy(first))

    def test_restart_switches_running_server_to_new_port(self):
        first, second = self.free_port(), self.free_port()
        code, output, error = self.command(first, 'start')
        self.assertEqual(code, 0, output + error)
        code, output, error = self.command(second, 'restart')
        self.assertEqual(code, 0, output + error)
        with socket.socket() as probe:
            self.assertNotEqual(probe.connect_ex(('127.0.0.1', first)), 0)
        self.assertTrue(self.healthy(second))

    def test_restart_collision_reports_pid_and_preserves_old_server(self):
        first, second = self.free_port(), self.free_port()
        code, output, error = self.command(first, 'start')
        self.assertEqual(code, 0, output + error)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', second))
            listener.listen()
            code, output, error = self.command(second, 'restart')
        self.assertNotEqual(code, 0, output + error)
        self.assertIn(f'포트 {second}를 다른 프로그램이 쓰고 있습니다(PID ', error)
        self.assertIn("./kanban restart KANBAN_PORT='포트번호'", error)
        self.assertTrue(self.healthy(first))

    def test_old_pid_file_is_migrated_on_start(self):
        first, second = self.free_port(), self.free_port()
        code, output, error = self.command(first, 'start')
        self.assertEqual(code, 0, output + error)
        state = self.root / '.local'
        (state / f'kanban-{first}.pid').rename(state / f'kankan-{first}.pid')
        code, output, error = self.command(second, 'start')
        self.assertEqual(code, 0, output + error)
        self.assertFalse((state / f'kankan-{first}.pid').exists())
        self.assertTrue(self.healthy(second))


if __name__ == '__main__':
    unittest.main()
