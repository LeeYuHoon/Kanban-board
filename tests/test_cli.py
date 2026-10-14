"""Subprocess coverage without installed agents, credentials, or model calls."""
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from kanban.store import ROOT, Store


class CliTests(unittest.TestCase):
    def test_sigint_stops_cli_and_persists_interruption(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            store = Store(directory / 'test.sqlite3')
            board = store.create_board('QA', str(directory))
            card = store.create_card(board['id'], 'Cancel test', 'QA', 'codex')
            fake = directory / 'codex'
            fake.write_text('#!' + sys.executable + '\n' + '''import os, pathlib, time
pathlib.Path('cli-ready').write_text(str(os.getpid()))
while True:
    time.sleep(1)
''')
            fake.chmod(0o700)
            env = dict(os.environ, KANBAN_DB=str(store.path), PATH=str(directory) + os.pathsep + os.environ['PATH'])
            process = subprocess.Popen([sys.executable, '-m', 'kanban', 'start', card['id']],
                                       cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 8
                while not (directory / 'cli-ready').exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue((directory / 'cli-ready').exists(), 'Fake CLI did not start')
                self.assertEqual(store.card(card['id'])['status'], 'running')
                process.send_signal(signal.SIGINT)
                process.communicate(timeout=8)
                self.assertEqual(process.returncode, 130)
                saved = Store(store.path).card(card['id'])
                self.assertEqual(saved['status'], 'interrupted')
                self.assertTrue(saved['finished_at'])
                child_pid = int((directory / 'cli-ready').read_text())
                with self.assertRaises(ProcessLookupError):
                    os.kill(child_pid, 0)
                rerun = subprocess.run([sys.executable, '-m', 'kanban', 'start', card['id']],
                                       cwd=ROOT, env=env, capture_output=True, timeout=8)
                self.assertEqual(rerun.returncode, 1)
                self.assertEqual(store.card(card['id'])['status'], 'interrupted')
            finally:
                if process.poll() is None:
                    process.send_signal(signal.SIGINT)
                    process.communicate(timeout=8)


if __name__ == '__main__':
    unittest.main()
