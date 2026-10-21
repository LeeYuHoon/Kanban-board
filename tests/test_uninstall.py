"""Check that uninstall choices preserve or remove the intended data."""
import os
import pty
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(sys.platform == 'darwin', 'The launcher uses macOS commands')
class UninstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='kanban-uninstall-test-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'Kanban-board'
        self.home = self.base / 'home'
        self.vault = self.base / 'Vault'
        self.external_db = self.base / 'external.sqlite3'
        (self.root / 'bin').mkdir(parents=True)
        (self.root / 'kanban').mkdir()
        (self.root / 'kanban/__init__.py').touch()
        (self.root / '.local/backups').mkdir(parents=True)
        (self.root / '.local/backups/manual.sqlite3').write_bytes(b'backup')
        (self.root / '.local/server.log').write_text('server log')
        self.home.mkdir()
        (self.vault / 'Kanban').mkdir(parents=True)
        (self.vault / 'Kanban/note.md').write_text('edited note')
        shutil.copy2(Path(__file__).resolve().parents[1] / 'bin/kanban', self.root / 'bin/kanban')
        with sqlite3.connect(self.external_db) as db:
            db.execute('CREATE TABLE obsidian_projects (vault TEXT)')
            db.execute('INSERT INTO obsidian_projects VALUES (?)', (str(self.vault),))
        self.env = dict(os.environ, HOME=str(self.home), KANBAN_DB=str(self.external_db),
                        KANBAN_LABEL='com.kanban.uninstall-test')
        self.env.pop('KANBAN_PORT', None)

    def run_uninstall(self, *args):
        return subprocess.run([str(self.root / 'bin/kanban'), 'uninstall', *args],
                              env=self.env, capture_output=True, text=True, timeout=20)

    def test_app_only_preserves_data_and_obsidian(self):
        result = self.run_uninstall('--yes', '--keep-data')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.root.exists())
        saved = list(self.home.glob('Kanban-data-*'))
        self.assertEqual(len(saved), 1)
        self.assertEqual((saved[0] / '.local/server.log').read_text(), 'server log')
        self.assertTrue((saved[0] / '.local/backups/manual.sqlite3').exists())
        self.assertTrue(self.external_db.exists())
        self.assertTrue((self.vault / 'Kanban/note.md').exists())

    def test_app_only_preserves_custom_db_inside_app(self):
        custom = self.root / 'custom.sqlite3'
        shutil.copy2(self.external_db, custom)
        self.env['KANBAN_DB'] = str(custom)
        result = self.run_uninstall('--yes', '--keep-data')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        saved = list(self.home.glob('Kanban-data-*'))
        self.assertEqual(len(saved), 1)
        with sqlite3.connect(saved[0] / 'custom.sqlite3') as db:
            self.assertEqual(db.execute('SELECT vault FROM obsidian_projects').fetchone()[0], str(self.vault))
        self.assertTrue((self.vault / 'Kanban/note.md').exists())

    def test_app_and_data_removes_external_db_but_keeps_obsidian(self):
        result = self.run_uninstall('--yes', '--keep-obsidian')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.root.exists())
        self.assertFalse(self.external_db.exists())
        self.assertTrue((self.vault / 'Kanban/note.md').exists())

    def test_everything_removes_obsidian_notes_too(self):
        result = self.run_uninstall('--yes')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.root.exists())
        self.assertFalse(self.external_db.exists())
        self.assertFalse((self.vault / 'Kanban').exists())
        self.assertTrue(self.vault.exists())

    def test_interactive_cancel_keeps_everything(self):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        process = subprocess.Popen([str(self.root / 'bin/kanban'), 'uninstall'],
                                   env=self.env, stdin=slave, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        os.write(master, b'0\n')
        output, error = process.communicate(timeout=20)
        self.assertEqual(process.returncode, 0, output + error)
        self.assertIn('제거 방식을 선택하세요:', output)
        self.assertIn('외부 DB·Obsidian 노트 유지', output)
        self.assertIn('직접 편집한 노트 포함', output)
        self.assertTrue(self.root.exists())
        self.assertTrue(self.external_db.exists())
        self.assertTrue((self.vault / 'Kanban/note.md').exists())

    def test_interactive_app_only_shows_choice_and_preserves_data(self):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        process = subprocess.Popen([str(self.root / 'bin/kanban'), 'uninstall'],
                                   env=self.env, stdin=slave, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        os.write(master, '1\n삭제\n'.encode())
        output, error = process.communicate(timeout=20)
        self.assertEqual(process.returncode, 0, output + error)
        self.assertIn('선택한 방식: 앱만 제거', output)
        self.assertIn('DB·로그·백업 보관 →', output)
        self.assertFalse(self.root.exists())
        self.assertTrue(self.external_db.exists())
        self.assertTrue((self.vault / 'Kanban/note.md').exists())


if __name__ == '__main__':
    unittest.main()
