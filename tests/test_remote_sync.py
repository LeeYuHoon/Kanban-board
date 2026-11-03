"""Device isolation and durable pull/ACK recovery with actual SQLite databases."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from kanban import search, linking
from kanban.obsidian import project_folders, render
from kanban.remote import RemoteManager
from kanban.store import Store
from kanban.sync import PROTOCOL, apply_batch, dispatch, enable_export, remote_id


def session(store, prompt='remote answer', session_id='same-session', provider='codex', number=1):
    at = f'2026-11-03T00:00:{number:02d}+00:00'
    row = dict(provider=provider, session_id=session_id, turn_key='turn-' + str(number),
               prompt=prompt, result=prompt + ' result', status='done', started_at=at,
               updated_at=at, finished_at=at, activity={'tool_calls': 2})
    store.save_turns([row])
    store.import_session(dict(row, cwd='/Users/example/project', created_at=at), '/logs/session.jsonl', at)
    return next(c for c in store.cards() if c['session_id'] == session_id and c['provider'] == provider)


class DirectTransport:
    def __init__(self, source, device):
        self.source, self.device = source, device
        self.executable = 'fixture'
        self.fail = None
        self.calls = []

    def machines(self):
        return [{'name': 'home', 'state': 'connected'}]

    def rpc(self, machine, request):
        self.calls.append(request['op'])
        if self.fail == request['op']:
            raise OSError('offline')
        result = dispatch(self.source, self.device, dict(request, protocol=PROTOCOL))
        if self.fail == request['op'] + '-response':
            raise OSError('response lost')
        return result


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = Store(self.root / 'source.sqlite3')
        self.main = Store(self.root / 'main.sqlite3')
        self.device = {'id': 'a' * 32, 'alias': 'home-macStudio'}
        enable_export(self.source)
        self.transport = DirectTransport(self.source, self.device)
        self.manager = RemoteManager(self.main, self.transport)
        self.manager.connect('home', self.device['id'])

    def pull(self):
        for _ in range(50):
            if not self.manager.sync_one(self.device['id']):
                return
        self.fail('sync did not settle')

    def test_two_providers_and_same_session_on_main_and_two_remotes(self):
        local = session(self.main, 'main only')
        session(self.source, 'remote only')
        session(self.source, 'claude remote only', provider='claude')
        self.pull()
        other_device = dict(id='b' * 32, alias='office')
        other = Store(self.root / 'other.sqlite3')
        session(other, 'office only')
        enable_export(other)  # Seed history that existed before installation.
        manager = RemoteManager(self.main, DirectTransport(other, other_device))
        manager.connect('home', other_device['id'])
        manager.sync_one(other_device['id'])
        self.assertEqual(len(self.main.cards()), 4)
        self.assertEqual(len(self.main.boards()), 3)
        for card in self.main.cards():
            expected = ('main only' if not card['device_id'] else
                        'office only' if card['device_id'] == other_device['id'] else
                        'claude remote only' if card['provider'] == 'claude' else 'remote only')
            self.assertEqual(self.main.history(card['id'])['items'][0]['prompt'], expected)
            self.assertEqual(card['turn_count'], 1)
            self.assertEqual(card['tool_calls'], 2)
        found = search.run(self.main, 'office')['cards']
        self.assertEqual(len(found), 1)
        self.assertNotIn(local['id'], found)
        folders = project_folders({b['id']: b for b in self.main.boards()})
        self.assertEqual(len(set(folders.values())), 3)
        with patch('kanban.linking.session_origin', return_value=('interactive', None)) as origin:
            linking.link_sessions(self.main)
            self.assertEqual(origin.call_count, 1)  # Only Main opens local source paths.

    def test_delta_replay_archive_and_ack_after_commit(self):
        source_card = session(self.source)
        device = self.manager._device(self.device['id'])
        batch = self.transport.rpc('home', self.manager._request(device, 'read'))
        apply_batch(self.main, self.device['id'], batch)
        card_id = remote_id(self.device['id'], source_card['id'])
        self.main.update(card_id, archived=True)
        session(self.source, 'new answer', number=2)
        self.pull()
        apply_batch(self.main, self.device['id'], batch)  # Old response cannot overwrite newer data.
        self.assertEqual(self.main.card(card_id)['prompt'], 'new answer')
        self.assertTrue(self.main.card(card_id)['archived'])
        self.assertEqual(len(self.main.history(card_id)['items']), 2)
        owner = json.loads(self.source.setting('collector_owner'))
        self.assertEqual(owner['ack'], self.manager._device(self.device['id'])['cursor'])
        with self.source.connect() as db:
            self.assertGreater(db.execute('SELECT COUNT(*) FROM sync_items').fetchone()[0], 0)

    def test_lost_ack_and_offline_resume_without_duplicates(self):
        session(self.source)
        self.transport.fail = 'ack'
        with self.assertRaises(OSError):
            self.pull()
        self.assertEqual(len(self.main.cards()), 1)  # Main saved before asking for ACK.
        self.assertEqual(json.loads(self.source.setting('collector_owner'))['ack'], 0)
        session(self.source, 'after disconnection', number=2)
        self.transport.fail = None
        self.manager = RemoteManager(self.main, self.transport)  # Simulate Main restart.
        self.pull()
        self.assertEqual(len(self.main.cards()), 1)
        self.assertEqual(len(self.main.history(self.main.cards()[0]['id'])['items']), 2)

    def test_claim_response_loss_and_only_one_main(self):
        self.manager.disconnect(self.device['id'])
        self.transport.fail = 'claim-response'
        with self.assertRaises(OSError):
            self.manager.connect('home', self.device['id'])
        token = self.manager._device(self.device['id'])['token']
        self.transport.fail = None
        self.manager = RemoteManager(self.main, self.transport)
        self.manager.connect('home', self.device['id'])
        self.assertEqual(token, self.manager._device(self.device['id'])['token'])
        stranger = RemoteManager(Store(self.root / 'stranger.sqlite3'), self.transport)
        with self.assertRaisesRegex(ValueError, '등록된 메인'):
            stranger.connect('home', self.device['id'])
        self.manager.disconnect(self.device['id'])
        stranger.connect('home', self.device['id'])

    def test_release_response_loss_can_be_retried(self):
        self.transport.fail = 'release-response'
        with self.assertRaises(OSError):
            self.manager.disconnect(self.device['id'])
        self.transport.fail = None
        self.manager.disconnect(self.device['id'])
        self.assertFalse(self.manager._device(self.device['id'])['enabled'])

    def test_bad_record_rolls_back_records_and_cursor(self):
        session(self.source)
        batch = self.transport.rpc('home', self.manager._request(self.manager._device(self.device['id']), 'read'))
        bad = copy.deepcopy(batch)
        bad['items'][-1]['value']['title'] = None
        with self.assertRaises((ValueError, sqlite3.Error)):
            apply_batch(self.main, self.device['id'], bad)
        self.assertEqual(self.main.cards(), [])
        self.assertEqual(self.manager._device(self.device['id'])['cursor'], 0)
        self.assertEqual(self.main.all_turns('codex', 'same-session', self.device['id']), [])
        self.pull()
        self.assertEqual(len(self.main.cards()), 1)

    def test_database_restore_reconciles_without_deleting_main_history(self):
        session(self.source, 'first')
        backup_path = self.root / 'backup.sqlite3'
        with self.source.connect() as src, sqlite3.connect(backup_path) as dst:
            src.backup(dst)
        self.pull()
        session(self.source, 'second', number=2)
        self.pull()
        # Restoring the source preserves its identity/epoch but removes later checkpoints.
        with sqlite3.connect(backup_path) as src, self.source.connect() as dst:
            src.backup(dst)
        self.pull()
        self.assertEqual(self.main.cards()[0]['prompt'], 'second')
        session(self.source, 'replacement', number=3)
        self.pull()
        prompts = {t['prompt'] for t in self.main.all_turns('codex', 'same-session', self.device['id'])}
        self.assertEqual(prompts, {'first', 'second', 'replacement'})

    def test_batches_unchanged_polls_and_alias_change(self):
        for number in range(55):
            session(self.source, 'batch', session_id='session-' + str(number))
        self.assertTrue(self.manager.sync_one(self.device['id']))
        self.pull()
        self.assertEqual(len(self.main.cards()), 55)
        with self.main.connect() as watch:
            before = watch.execute('PRAGMA data_version').fetchone()[0]
            self.pull()
            self.assertEqual(watch.execute('PRAGMA data_version').fetchone()[0], before)
        self.device['alias'] = 'renamed'
        self.pull()
        self.assertTrue(all(c['device_alias'] == 'renamed' for c in self.main.cards()))
        self.assertEqual(len(self.main.cards()), 55)
        self.assertNotIn('token', self.manager.devices()[0])

    def test_pause_release_and_version_mismatch_preserve_history(self):
        session(self.source)
        self.pull()
        self.manager.pause(self.device['id'], True)
        calls = len(self.transport.calls)
        self.assertFalse(self.manager.sync_one(self.device['id']))
        self.assertEqual(len(self.transport.calls), calls)
        self.manager.pause(self.device['id'], False)
        self.pull()
        self.manager.disconnect(self.device['id'])
        self.assertEqual(len(self.main.cards()), 1)
        with self.assertRaisesRegex(ValueError, '버전'):
            dispatch(self.source, self.device, {'protocol': 100, 'op': 'info'})

    def test_export_updates_coalesce_but_canonical_turns_survive(self):
        session(self.source, 'first')
        self.pull()
        for i in range(20):
            session(self.source, 'changed ' + str(i))
        with self.source.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sync_items').fetchone()[0], 2)
        self.pull()
        self.assertEqual(self.main.cards()[0]['prompt'], 'changed 19')


class MigrationTests(unittest.TestCase):
    def test_pre_device_database_keeps_ids_and_turns(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'old.sqlite3'
            store = Store(path)
            card = session(store, 'legacy')
            # Reproduce the previous version's primary key, then open with the new version.
            with store.connect() as db:
                db.execute('ALTER TABLE session_turns RENAME TO new_turns')
                db.execute('''CREATE TABLE session_turns (provider TEXT NOT NULL,session_id TEXT NOT NULL,
                    turn_key TEXT NOT NULL,prompt TEXT NOT NULL,result TEXT NOT NULL,status TEXT NOT NULL,
                    usage TEXT,started_at TEXT NOT NULL,updated_at TEXT NOT NULL,finished_at TEXT,activity TEXT,
                    PRIMARY KEY(provider,session_id,turn_key))''')
                db.execute('''INSERT INTO session_turns SELECT provider,session_id,turn_key,prompt,result,status,
                    usage,started_at,updated_at,finished_at,activity FROM new_turns''')
                db.execute('DROP TABLE new_turns')
            store = Store(path)
            self.assertEqual(store.cards()[0]['id'], card['id'])
            self.assertEqual(store.history(card['id'])['items'][0]['prompt'], 'legacy')
            self.assertEqual(store.cards()[0]['device_id'], '')
            enable_export(store)
            self.assertEqual(Store(path).history(card['id'])['items'][0]['prompt'], 'legacy')


if __name__ == '__main__':
    unittest.main()
