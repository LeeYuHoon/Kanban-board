import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from kanban.runner import parse_output, run
from kanban.server import handler
from kanban.store import Store


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'test.sqlite3')
        self.board = self.store.create_board('테스트', str(self.root))

    def card(self, provider='codex'):
        return self.store.create_card(self.board['id'], '작업', 'hello', provider)

    def test_persistence_and_isolation(self):
        card = self.card()
        self.store.update(card['id'], status='done', result='결과', usage={'input_tokens': 12})
        reopened = Store(self.store.path)
        self.assertEqual(reopened.card(card['id'])['usage']['input_tokens'], 12)
        self.assertEqual(reopened.card(card['id'])['result'], '결과')
        with self.assertRaises(ValueError):
            self.store.create_board('duplicate', str(self.root))
        with self.assertRaises(ValueError):
            self.card('hermes')

    def test_unknown_completion_is_failure(self):
        self.assertTrue(parse_output('codex', '{"type":"thread.started","thread_id":"x"}')[3])
        self.assertTrue(parse_output('claude', '{"type":"result","subtype":"error_max_turns","is_error":true}')[3])

    def test_edit_only_active_todo_and_validate_providers(self):
        card = self.card()
        saved = self.store.edit_card(card['id'], '수정', 'new prompt', 'claude')
        self.assertEqual((saved['title'], saved['prompt'], saved['provider']), ('수정', 'new prompt', 'claude'))
        for provider in ('hermes', '', None, [], {}):
            with self.assertRaises(ValueError):
                self.store.edit_card(card['id'], 'title', '', provider)
        self.store.update(card['id'], archived=True)
        with self.assertRaises(ValueError):
            self.store.edit_card(card['id'], 'title', '', 'codex')
        with self.assertRaises(ValueError):
            run(self.store, card['id'])
        self.store.update(card['id'], archived=False, status='running')
        with self.assertRaises(ValueError):
            self.store.edit_card(card['id'], 'title', '', 'codex')
        self.assertEqual(self.store.card(card['id'])['prompt'], 'new prompt')

    def test_bad_identifiers_and_card_input(self):
        for identifier in (None, [], {}):
            with self.assertRaises(ValueError):
                self.store.create_card(identifier, 'title', '', 'codex')
        for title, prompt in (('', ''), ('   ', ''), ('a' * 201, ''), ('title', None), ('title', 'a' * 100001)):
            with self.assertRaises(ValueError):
                self.store.create_card(self.board['id'], title, prompt, 'codex')

    def fake_cli(self, name, body):
        path = self.root / name
        path.write_text('#!/usr/bin/env python3\n' + body)
        path.chmod(0o700)

    def test_codex_run_and_atomic_claim(self):
        self.fake_cli('codex', '''import sys, pathlib, json, time
prompt = sys.stdin.read()
time.sleep(.1)
pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text('result: '+prompt)
print(json.dumps({'type':'thread.started','thread_id':'session-1'}))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':10}}))
''')
        card = self.card()
        def start():
            try:
                return run(self.store, card['id'])
            except ValueError:
                return 'rejected'
        with patch.dict(os.environ, {'PATH': str(self.root) + os.pathsep + os.environ['PATH']}):
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(lambda _: start(), range(2)))
        self.assertCountEqual(outcomes, [0, 'rejected'])
        saved = self.store.card(card['id'])
        self.assertEqual(saved['status'], 'done')
        self.assertEqual(saved['session_id'], 'session-1')
        self.assertEqual(saved['result'], 'result: hello')

    def test_claude_success_and_error(self):
        for failed in (False, True):
            self.fake_cli('claude', 'import sys,json\nsys.stdin.read()\nprint(json.dumps(' + repr({
                'type': 'result', 'subtype': 'error_max_turns' if failed else 'success',
                'result': 'answer', 'is_error': failed, 'usage': {'output_tokens': 8},
                'session_id': 'claude-session'}) + '))\n')
            card = self.card('claude')
            with patch.dict(os.environ, {'PATH': str(self.root) + os.pathsep + os.environ['PATH']}):
                self.assertEqual(run(self.store, card['id']), 1 if failed else 0)
            self.assertEqual(self.store.card(card['id'])['status'], 'failed' if failed else 'done')

    def test_missing_cli_records_failure(self):
        card = self.card()
        with patch.dict(os.environ, {'PATH': str(self.root)}):
            with self.assertRaises(OSError):
                run(self.store, card['id'])
        self.assertEqual(self.store.card(card['id'])['status'], 'failed')

    def test_http_boundaries_and_workflow(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler(self.store))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        def request(method, path, data=None, headers=None):
            connection = HTTPConnection('127.0.0.1', port)
            merged = {'Content-Type': 'application/json'}
            merged.update(headers or {})
            connection.request(method, path, json.dumps(data) if data is not None else None, merged)
            response = connection.getresponse()
            result = response.status, response.read()
            connection.close()
            return result
        self.assertEqual(request('GET', '/')[0], 200)
        self.assertEqual(request('GET', '/api/state', headers={'Host':'evil.example'})[0], 403)
        self.assertEqual(request('GET', '/api/state', headers={'Origin':'https://evil.example'})[0], 403)
        self.assertEqual(request('GET', '/../../README.md')[0], 404)
        navigation = {'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'document'}
        self.assertEqual(request('GET', '/', headers=navigation)[0], 200)
        self.assertEqual(request('GET', '/api/state', headers=navigation)[0], 403)
        self.assertEqual(request('GET', '/app.js', headers=dict(navigation, **{'Sec-Fetch-Dest': 'script'}))[0], 403)
        self.assertEqual(request('GET', '/', headers=dict(navigation, Host='evil.example'))[0], 403)
        self.assertEqual(request('POST', '/api/cards', {}, navigation)[0], 403)
        # Cards are no longer created or edited from the web; only archiving is allowed.
        payload = dict(board_id=self.board['id'], title='<script>alert(1)</script>', provider='codex')
        self.assertEqual(request('POST', '/api/cards', payload)[0], 404)
        card = self.card()
        self.assertEqual(request('PATCH', '/api/cards/'+card['id'], {'status':'done'})[0], 400)
        self.assertEqual(request('PATCH', '/api/cards/'+card['id'], {'archived':None})[0], 400)
        self.assertEqual(request('PATCH', '/api/cards/'+card['id'], dict(title='수정', prompt='새 요청', provider='claude'))[0], 400)
        self.assertEqual(self.store.card(card['id'])['title'], '작업')
        status, raw = request('GET', '/api/state')
        self.assertEqual(status, 200)
        self.assertNotIn('run_command', json.loads(raw)['cards'][0])
        self.assertEqual(request('PATCH', '/api/cards/'+card['id'], {'archived':True})[0], 200)
        self.assertTrue(self.store.card(card['id'])['archived'])
        self.assertEqual(request('POST', '/api/boards', payload, {'Content-Type':'text/plain'})[0], 415)

if __name__ == '__main__':
    unittest.main()
