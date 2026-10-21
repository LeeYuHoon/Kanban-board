import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from kanban.update_db import backup, inspect, prune, verify_and_protect


class UpdateBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'data.sqlite3'
        with sqlite3.connect(self.source) as db:
            db.execute('CREATE TABLE cards (id INTEGER PRIMARY KEY)')
            db.execute('INSERT INTO cards VALUES (1)')

    def test_schema_backup_is_protected_and_manual_backup_is_untouched(self):
        directory = self.root / 'backups'
        directory.mkdir()
        original = directory / 'pre-update-20260101-000000.sqlite3.ABC123'
        backup(self.source, original)
        with sqlite3.connect(self.source) as db:
            db.execute('ALTER TABLE cards ADD COLUMN note TEXT')
        protected = verify_and_protect(self.source, original)
        self.assertTrue(protected.name.startswith('pre-schema-'))
        self.assertEqual(inspect(protected)[0], 1)
        for number in range(2, 7):
            candidate = directory / f'pre-update-2026010{number}-000000.sqlite3.ABC123'
            candidate.write_bytes(b'older snapshot')
            os.utime(candidate, (number, number))
        os.utime(protected, (1, 1))
        manual = directory / 'kanban-manual.sqlite3'
        manual.write_bytes(b'manual')
        self.assertEqual(prune(directory), 2)
        self.assertTrue(protected.exists())
        self.assertTrue(manual.exists())
        self.assertEqual(len(list(directory.glob('pre-*.sqlite3.*'))), 4)

    def test_missing_source_cannot_leave_a_partial_backup(self):
        target = self.root / 'pre-update-20260101-000000.sqlite3.ABC123'
        target.touch()
        with self.assertRaises(sqlite3.Error):
            backup(self.root / 'missing.sqlite3', target)
        self.assertFalse(target.exists())

    def test_card_loss_blocks_success_validation(self):
        target = self.root / 'pre-update-20260101-000000.sqlite3.ABC123'
        backup(self.source, target)
        with sqlite3.connect(self.source) as db:
            db.execute('DELETE FROM cards')
        with self.assertRaisesRegex(ValueError, '카드 수가 줄었습니다'):
            verify_and_protect(self.source, target)
        self.assertTrue(target.exists())


if __name__ == '__main__':
    unittest.main()
