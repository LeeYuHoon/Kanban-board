import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

from kanban import search
from kanban.server import handler
from kanban.store import Store


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'repo').mkdir()
        self.store = Store(self.root / 'k.sqlite3')
        self.day = datetime(2026, 10, 10, 3, 0, tzinfo=timezone.utc)

    def session(self, session, turns, cwd=None):
        rows = []
        for number, (offset, prompt, result) in enumerate(turns):
            stamp = (self.day + timedelta(days=offset, minutes=number)).isoformat()
            rows.append(dict(provider='codex', session_id=session, turn_key=f'{session}-{number}', prompt=prompt, result=result,
                             status='done', started_at=stamp, updated_at=stamp, finished_at=stamp, activity={}))
        self.store.save_turns(rows)
        self.store.import_session(dict(provider='codex', session_id=session, prompt=rows[-1]['prompt'], cwd=str(cwd or self.root / 'repo'),
                                       status='done', result=rows[-1]['result'], created_at=rows[0]['started_at'],
                                       updated_at=rows[-1]['updated_at']), self.root / 'x.jsonl', rows[-1]['updated_at'])
        return next(card for card in self.store.cards() if card['session_id'] == session)

    def test_all_prompts_terms_targets_and_children(self):
        parent = self.session('p', [(0, '결제 화면 버그 봐줘', '쿠폰 금액 캐시 문제입니다'), (1, '최신 요청', '최신 답변')])
        child = self.session('c', [(0, '자동 실행: 결제 콜백 테스트', 'PG 응답 확인')])
        with self.store.connect() as db:
            db.execute('UPDATE cards SET parent_id=?,parent_turn_key=? WHERE id=?', (parent['id'], 'p-0', child['id']))
        result = search.run(self.store, '결제')
        self.assertEqual(result['cards'][parent['id']]['count'], 2)  # An old prompt and a child session both match.
        self.assertEqual(result['cards'][parent['id']]['children'], 1)
        self.assertNotIn(child['id'], result['cards'])
        self.assertEqual(result['matches'], {parent['id']: ['p-0'], child['id']: ['c-0']})
        self.assertEqual(search.run(self.store, '결제 캐시')['total'], 1)  # Every term must appear.
        self.assertEqual(search.run(self.store, '쿠폰', 'prompt')['total'], 0)
        self.assertEqual(search.run(self.store, '쿠폰', 'answer')['total'], 1)
        item = search.run(self.store, '쿠폰')['items'][0]
        self.assertEqual((item['number'], item['answer']), (1, '쿠폰 금액 캐시 문제입니다'))
        self.assertEqual(search.run(self.store, '100%_x')['total'], 0)  # LIKE wildcards are literal.

    def test_period_bounds_manual_cards_and_validation(self):
        card = self.session('p', [(0, '첫날', ''), (2, '셋째 날', ''), (5, '엿새째', '')])
        since = (self.day + timedelta(days=1)).isoformat()
        until = (self.day + timedelta(days=3)).replace(tzinfo=None).isoformat() + 'Z'
        result = search.run(self.store, '', since=since, until=until)
        self.assertEqual(result['matches'], {card['id']: ['p-1']})
        board = self.store.create_board('수동', str(self.root))
        manual = self.store.create_card(board['id'], '수동 작업', '배포 스크립트 점검', 'claude')
        self.store.update(manual['id'], status='done', result='완료')
        self.assertIn(manual['id'], search.run(self.store, '배포')['cards'])
        for kwargs in ({}, {'query': 'x' * 201}, {'query': 'a', 'target': 'title'}, {'query': 'a', 'since': 'yesterday'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                search.run(self.store, **kwargs)

    def test_history_keys_and_api(self):
        card = self.session('p', [(index, f'질문 {index}', '') for index in range(35)])
        self.assertEqual([turn['turn_key'] for turn in self.store.history_keys(card['id'], ['p-0', 'p-34'])['items']], ['p-34', 'p-0'])
        with self.assertRaises(ValueError):
            self.store.history_keys(card['id'], 'p-0')
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler(self.store))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        def get(path, headers=None):
            connection = HTTPConnection('127.0.0.1', server.server_address[1])
            connection.request('GET', path, headers=headers or {})
            response = connection.getresponse()
            value = response.status, json.loads(response.read())
            connection.close()
            return value
        status, value = get('/api/search?q=' + '%EC%A7%88%EB%AC%B8%201')  # '질문 1'
        self.assertEqual((status, value['total']), (200, 13))  # Terms '질문' AND '1': 1, 10..19, 21, 31
        self.assertEqual(get('/api/search')[0], 400)
        self.assertEqual(get('/api/search?q=a', {'Origin': 'https://evil.example'})[0], 403)
        keys = json.dumps(['p-0'])
        self.assertEqual(get(f'/api/cards/{card["id"]}/history?keys={keys}')[1]['items'][0]['prompt'], '질문 0')


if __name__ == '__main__':
    unittest.main()
