"""Exercise port changes with an isolated app copy and database."""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
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
        self.env.pop('KANBAN_PORT', None)
        self.addCleanup(self.stop_all)

    def free_port(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        self.ports.append(port)
        return port

    def command(self, port, action):
        args = [str(self.root / 'bin/kanban'), action]
        if port is not None:
            args.append(f'KANBAN_PORT={port}')
        result = subprocess.run(args,
                                env=self.env, capture_output=True, timeout=22)
        return result.returncode, result.stdout.decode('utf-8', 'replace'), result.stderr.decode('utf-8', 'replace')

    def healthy(self, port):
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health', timeout=2) as response:
            return json.load(response)['app'] == 'Kanban'

    def stop_all(self):
        self.command(None, 'stop')

    def launch_direct(self, port, root=None):
        process = subprocess.Popen([sys.executable, '-m', 'kanban', 'serve', '--port', str(port)],
                                   cwd=root or self.root, env=self.env, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        def stop_process():
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        self.addCleanup(stop_process)
        for _ in range(50):
            try:
                if self.healthy(port):
                    return process
            except OSError:
                pass
            time.sleep(0.1)
        self.fail(f'Direct server did not start on {port}')

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

    def test_start_replaces_direct_server_from_this_project(self):
        first, target = self.free_port(), self.free_port()
        process = self.launch_direct(first)
        code, output, error = self.command(target, 'start')
        self.assertEqual(code, 0, output + error)
        process.wait(timeout=5)
        self.assertTrue(self.healthy(target))

    def test_second_direct_server_cannot_start_on_another_port(self):
        first, second = self.free_port(), self.free_port()
        self.launch_direct(first)
        duplicate = subprocess.run([sys.executable, '-m', 'kanban', 'serve', '--port', str(second)],
                                   cwd=self.root, env=self.env, capture_output=True, timeout=8)
        self.assertNotEqual(duplicate.returncode, 0)
        self.assertIn('Kanban이 이미 실행 중입니다', duplicate.stderr.decode())
        self.assertTrue(self.healthy(first))

    def test_start_replaces_server_from_another_installation(self):
        other = self.root / 'other-install'
        other.mkdir()
        shutil.copytree(self.root / 'bin', other / 'bin')
        shutil.copytree(self.root / 'kanban', other / 'kanban')
        shutil.copytree(self.root / 'web', other / 'web')
        previous, target = self.free_port(), self.free_port()
        process = self.launch_direct(previous, other)
        code, output, error = self.command(target, 'start')
        self.assertEqual(code, 0, output + error)
        process.wait(timeout=5)
        self.assertTrue(self.healthy(target))

    def test_start_takes_over_same_port_from_another_installation(self):
        other = self.root / 'other-install'
        other.mkdir()
        shutil.copytree(self.root / 'bin', other / 'bin')
        shutil.copytree(self.root / 'kanban', other / 'kanban')
        shutil.copytree(self.root / 'web', other / 'web')
        port = self.free_port()
        process = self.launch_direct(port, other)
        code, output, error = self.command(port, 'start')
        self.assertEqual(code, 0, output + error)
        process.wait(timeout=5)
        self.assertTrue(self.healthy(port))

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
        self.assertEqual((self.root / '.local/port').read_text().strip(), str(first))

    def test_saved_port_is_used_by_later_commands(self):
        port = self.free_port()
        code, output, error = self.command(port, 'start')
        self.assertEqual(code, 0, output + error)
        self.assertEqual((self.root / '.local/port').read_text().strip(), str(port))
        code, output, error = self.command(None, 'status')
        self.assertEqual(code, 0, output + error)
        self.assertIn(f'http://127.0.0.1:{port}', output)
        code, output, error = self.command(None, 'stop')
        self.assertEqual(code, 0, output + error)
        with socket.socket() as probe:
            self.assertNotEqual(probe.connect_ex(('127.0.0.1', port)), 0)
        code, output, error = self.command(None, 'start')
        self.assertEqual(code, 0, output + error)
        self.assertTrue(self.healthy(port))

    def test_doctor_uses_saved_port_without_changing_it(self):
        port = self.free_port()
        code, output, error = self.command(port, 'start')
        self.assertEqual(code, 0, output + error)
        code, output, error = self.command(None, 'doctor')
        self.assertEqual(code, 0, output + error)
        self.assertIn(f'http://127.0.0.1:{port}', output)
        self.assertEqual((self.root / '.local/port').read_text().strip(), str(port))

    def test_only_start_and_restart_accept_port_argument(self):
        for action in ('doctor', 'status', 'stop', 'open', 'install-login'):
            code, output, error = self.command(12192, action)
            self.assertEqual(code, 2, action + output + error)
            self.assertIn('포트 지정은 start와 restart에서만', error)

    def test_restart_switches_running_server_to_new_port(self):
        first, second = self.free_port(), self.free_port()
        code, output, error = self.command(first, 'start')
        self.assertEqual(code, 0, output + error)
        code, output, error = self.command(second, 'restart')
        self.assertEqual(code, 0, output + error)
        with socket.socket() as probe:
            self.assertNotEqual(probe.connect_ex(('127.0.0.1', first)), 0)
        self.assertTrue(self.healthy(second))
        self.assertEqual((self.root / '.local/port').read_text().strip(), str(second))
        code, output, error = self.command(None, 'status')
        self.assertEqual(code, 0, output + error)
        self.assertIn(f'http://127.0.0.1:{second}', output)

    def test_restart_replaces_direct_server_on_same_port(self):
        port = self.free_port()
        process = self.launch_direct(port)
        code, output, error = self.command(port, 'restart')
        self.assertEqual(code, 0, output + error)
        process.wait(timeout=5)
        self.assertTrue(self.healthy(port))
        self.assertEqual((self.root / '.local/port').read_text().strip(), str(port))

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
        self.assertEqual((self.root / '.local/port').read_text().strip(), str(first))

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
