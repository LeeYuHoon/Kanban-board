import json
import os
import re
import sqlite3
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

from kanban.collector import Collector
from kanban.obsidian import FOLDER, ObsidianSync, ownership, render, yaml_string
from kanban.server import handler
from kanban.store import Store


def code_blocks(text):
    """Contents of ```text fences in order (request, answer, request, answer ...)."""
    lines, found, index = text.split('\n'), [], 0
    while index < len(lines):
        match = re.match(r'^(`{3,})text$', lines[index])
        if match:
            end = lines.index(match.group(1), index + 1)
            found.append('\n'.join(lines[index + 1:end]))
            index = end
        index += 1
    return found


class ObsidianTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / 'my project'
        self.project.mkdir()
        self.vault = self.vault_at('vault')
        self.store = Store(self.root / 'kanban.sqlite3')
        self.board = self.store.create_board('my project', str(self.project))
        self.sync = ObsidianSync(self.store)
        self.clock = datetime.now(timezone.utc) - timedelta(minutes=10)
        self.sequence = 0

    def vault_at(self, name):
        path = self.root / name
        (path / '.obsidian').mkdir(parents=True)
        return path

    def stamp(self):
        self.sequence += 1
        return (self.clock + timedelta(milliseconds=self.sequence * 10)).isoformat()

    def notes(self, vault=None):
        return sorted(path for path in ((vault or self.vault) / FOLDER).rglob('*.md'))

    def session(self, provider='claude', session='session-1', turns=1, prompt='질문 {}', result='답변 {}',
                status='done', cwd=None):
        rows = []
        for number in range(turns):
            started, finished = self.stamp(), self.stamp()
            rows.append(dict(provider=provider, session_id=session, turn_key=f'{session}-{number:04d}',
                             prompt=prompt.format(number), result=result.format(number) if status == 'done' else '',
                             status=status, started_at=started, updated_at=finished,
                             finished_at=finished if status == 'done' else None, activity={}))
        self.store.save_turns(rows)
        self.store.import_session(dict(provider=provider, session_id=session, prompt=rows[-1]['prompt'],
                                       cwd=str(cwd or self.project), status=status, result=rows[-1]['result'],
                                       created_at=rows[0]['started_at'], updated_at=rows[-1]['updated_at'],
                                       activity={}), self.root / 'source.jsonl', self.stamp())
        return rows

    def connect(self, vault=None, mode='auto', sync=None):
        for board in self.store.boards():
            (sync or self.sync).configure_project(board['id'], mode, str(vault or self.vault))

    def configure_and_sync(self):
        self.connect()
        return self.sync.sync()

    # ----- export content -----

    def test_codex_and_claude_sessions_export_from_native_logs(self):
        codex, claude = self.root / 'codex', self.root / 'claude'
        codex.mkdir()
        (claude / 'p').mkdir(parents=True)
        def ce(kind, payload):
            return dict(timestamp=self.stamp(), type=kind, payload=payload)
        def cl(kind, **values):
            return dict(type=kind, timestamp=self.stamp(), cwd=str(self.project), sessionId='claude-1', **values)
        events = [ce('session_meta', dict(id='codex-1', cwd=str(self.project), source='cli')),
                  ce('turn_context', dict(model='gpt-test')),
                  ce('event_msg', {'type': 'task_started'}),
                  ce('response_item', dict(type='message', role='user', content=[{'type': 'input_text', 'text': 'Codex 첫 요청'}])),
                  ce('response_item', dict(type='function_call', name='mcp__browser__navigate', arguments='{"url":"https://private.example/?token=raw-arg"}')),
                  ce('response_item', dict(type='function_call', name='collaboration.spawn_agent', arguments=json.dumps({'task_name': 'reviewer'}))),
                  ce('response_item', dict(type='function_call', name='exec_command', arguments=json.dumps({'cmd': 'cat /s/orca-cli/SKILL.md'}))),
                  ce('event_msg', {'type': 'task_complete', 'last_agent_message': 'Codex 첫 답변'}),
                  ce('event_msg', {'type': 'task_started'}),
                  ce('response_item', dict(type='message', role='user', content=[{'type': 'input_text', 'text': 'Codex 두 번째 요청'}])),
                  ce('event_msg', {'type': 'task_complete', 'last_agent_message': 'Codex 두 번째 답변'})]
        (codex / 'a.jsonl').write_text(''.join(json.dumps(event) + '\n' for event in events))
        events = [cl('user', uuid='u1', message={'content': 'Claude 첫 요청'}),
                  cl('assistant', uuid='a1', message={'id': 'm1', 'model': 'claude-test', 'stop_reason': 'end_turn', 'content': [
                      {'type': 'thinking', 'thinking': 'SECRET-REASONING'},
                      {'type': 'tool_use', 'name': 'mcp__claude-in-chrome__navigate', 'input': {'url': 'https://private.example/raw-arg'}},
                      {'type': 'tool_use', 'name': 'Skill', 'input': {'skill': 'review'}},
                      {'type': 'tool_use', 'name': 'Agent', 'input': {'subagent_type': 'Explore', 'prompt': 'raw-arg prompt'}},
                      {'type': 'text', 'text': 'Claude 첫 답변'}]}),
                  cl('user', uuid='u2', message={'content': 'Claude 두 번째 요청'}),
                  cl('assistant', uuid='a2', message={'id': 'm2', 'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': 'Claude 두 번째 답변'}]})]
        (claude / 'p' / 'b.jsonl').write_text(''.join(json.dumps(event) + '\n' for event in events))
        Collector(self.store, [('codex', codex), ('claude', claude)]).scan()
        status = self.configure_and_sync()
        self.assertIsNone(status['error'])
        self.assertEqual(status['result']['created'], 2)
        notes = self.notes()
        self.assertEqual([path.parent.name for path in notes], ['my project', 'my project'])
        texts = {('codex' if 'Codex' in path.name else 'claude'): path.read_text() for path in notes}
        codex_note, claude_note = texts['codex'], texts['claude']
        self.assertIn('provider: "codex"', codex_note)
        self.assertIn('session_id: "codex-1"', codex_note)
        self.assertIn('project: "my project"', codex_note)
        self.assertEqual(code_blocks(codex_note), ['Codex 첫 요청', 'Codex 첫 답변', 'Codex 두 번째 요청', 'Codex 두 번째 답변'])
        self.assertEqual(code_blocks(claude_note), ['Claude 첫 요청', 'Claude 첫 답변', 'Claude 두 번째 요청', 'Claude 두 번째 답변'])
        first = codex_note.split('## 프롬프트 2')[0]
        self.assertIn('`browser` / `navigate`', first)
        self.assertIn('`reviewer`', first)
        self.assertIn('스킬 파일 참조 (실행 여부 미확인): `orca-cli`', first)
        self.assertIn('`gpt-test`', first)
        self.assertIn('provider_label: "Claude Code"', claude_note)
        self.assertIn('스킬: `review`', claude_note)
        self.assertIn('하위 에이전트: `Explore`', claude_note)
        self.assertIn('`claude-in-chrome` / `navigate`', claude_note)
        self.assertIn('mcp_servers: ["claude-in-chrome"]', claude_note)
        # Second Claude prompt has no recorded usage: say so instead of guessing.
        self.assertIn('스킬: 확인된 기록 없음', claude_note.split('## 프롬프트 2')[1])
        for text in (codex_note, claude_note):
            self.assertNotIn('raw-arg', text)
            self.assertNotIn('SECRET-REASONING', text)
            self.assertNotIn('private.example', text)

    def test_full_history_beyond_first_api_page(self):
        self.session(turns=45)
        card = self.store.cards()[0]
        self.assertEqual(len(self.store.history(card['id'])['items']), 30)
        self.configure_and_sync()
        text = self.notes()[0].read_text()
        self.assertEqual(text.count('<!-- kankan-turn:'), 45)
        self.assertIn('turn_count: 45', text)
        self.assertEqual(code_blocks(text)[::2], [f'질문 {n}' for n in range(45)])
        self.assertEqual(code_blocks(text)[1::2], [f'답변 {n}' for n in range(45)])

    def test_text_with_korean_code_fences_markdown_and_long_content_round_trips(self):
        tricky = ('한글 요청 😀\n```python\nprint("```")\n```\n````\n~~~\n---\nkankan_hash: ""\n'
                  '# 제목처럼 보이는 줄\n<script>alert(1)</script>\n[[링크]] #태그 %%주석%% $x$ | a | b |\n'
                  '\t탭 줄\r\n끝 \\ 백슬래시')
        long_answer = ('긴 답변 ' + 'abc```def ' * 9000)[:100000]
        self.session(session='weird"id: 1', prompt=tricky, result=long_answer, cwd=self.project)
        board = self.store.boards()[0]
        with self.store.connect() as db:
            db.execute('UPDATE boards SET name=? WHERE id=?', ('../../탈출 *?:"프로젝트" #x', board['id']))
        self.configure_and_sync()
        notes = self.notes()
        self.assertEqual(len(notes), 1)
        note = notes[0]
        self.assertTrue(str(note.resolve()).startswith(str((self.vault / FOLDER).resolve()) + os.sep))
        self.assertEqual(note.parent.parent, self.vault / FOLDER)
        text = note.read_bytes().decode()  # read_text() would normalize the preserved \r\n.
        self.assertEqual(code_blocks(text), [tricky, long_answer])
        self.assertIn('session_id: "weird\\"id: 1"', text)
        self.assertEqual(yaml_string('줄\u2028바꿈\x7f\ud800'), '"줄\\u2028바꿈\\u007f\ufffd"')
        identity, role, unmodified = ownership(note.read_bytes())
        self.assertEqual((identity, role, unmodified), ('claude:weird"id: 1', 'note', True))
        for line in text.split('\n---\n')[0].splitlines()[1:]:
            key, value = line.split(': ', 1)
            if value.startswith('"'):
                json.loads(value)  # Every string property is a valid quoted scalar.

    def test_manual_runs_are_exported_and_todo_cards_are_not(self):
        board = self.store.create_board('수동', str(self.root))
        todo = self.store.create_card(board['id'], '대기 작업', '아직 실행 안 함', 'codex')
        ran = self.store.create_card(board['id'], '수동 작업', '수동 요청', 'claude')
        self.store.update(ran['id'], status='done', result='수동 결과')
        self.configure_and_sync()
        notes = self.notes()
        self.assertEqual(len(notes), 1)
        self.assertEqual(code_blocks(notes[0].read_text()), ['수동 요청', '수동 결과'])
        self.assertNotIn(todo['id'], notes[0].read_text())

    # ----- idempotency and updates -----

    def test_resync_does_not_duplicate_even_after_losing_sync_records(self):
        self.session(turns=2)
        self.configure_and_sync()
        note = self.notes()[0]
        before = note.stat().st_mtime_ns
        self.assertEqual(self.sync.sync()['result']['unchanged'], 1)
        with self.store.connect() as db:
            db.execute('DELETE FROM obsidian_notes')
        self.assertEqual(self.sync.sync()['result']['unchanged'], 1)
        restarted = ObsidianSync(Store(self.store.path))
        self.assertEqual(restarted.sync()['result']['unchanged'], 1)
        self.assertEqual(self.notes(), [note])
        self.assertEqual(note.stat().st_mtime_ns, before)
        self.assertEqual(list((self.vault / FOLDER).rglob('.kankan-*.tmp')), [])

    def test_new_prompts_and_completed_answers_update_the_same_note(self):
        rows = self.session(status='running', prompt='진행 중 요청')
        self.configure_and_sync()
        note = self.notes()[0]
        self.assertIn('답변을 작성하고 있습니다', note.read_text())
        done = dict(rows[0], status='done', result='완료된 답변', updated_at=self.stamp(), finished_at=self.stamp())
        follow = dict(rows[0], turn_key='follow-up', prompt='새 요청', result='새 답변', status='done',
                      started_at=self.stamp(), updated_at=self.stamp(), finished_at=self.stamp())
        self.store.save_turns([done, follow])
        self.store.import_session(dict(provider='claude', session_id='session-1', prompt='새 요청', cwd=str(self.project),
                                       status='done', result='새 답변', created_at=rows[0]['started_at'],
                                       updated_at=follow['updated_at']), self.root / 's.jsonl', self.stamp())
        status = self.sync.sync()
        self.assertEqual(status['result']['updated'], 1)
        self.assertEqual(self.notes(), [note])
        self.assertEqual(code_blocks(note.read_text()), ['진행 중 요청', '완료된 답변', '새 요청', '새 답변'])

    def test_settings_and_status_survive_restart_and_migration_keeps_data(self):
        self.session()
        self.connect(mode='selected')
        self.connect()
        first = self.sync.sync()
        with self.store.connect() as db:  # Simulate a pre-Obsidian database.
            db.execute('DROP TABLE obsidian_notes')
        reopened = Store(self.store.path)
        self.assertEqual(len(reopened.cards()), 1)
        status = ObsidianSync(reopened).status()
        project = status['projects'][0]
        self.assertEqual((status['connected'], project['vault'], project['mode']), (1, str(self.vault), 'auto'))
        self.assertEqual(status['last_success_at'], first['last_success_at'])
        self.assertEqual(status['result']['created'], 1)
        self.assertEqual(ObsidianSync(reopened).sync()['result']['unchanged'], 1)
        self.assertEqual(len(self.notes()), 1)

    # ----- validation and failures -----

    def test_invalid_vault_paths_are_rejected_without_saving(self):
        plain = self.root / 'plain'
        plain.mkdir()
        file = self.root / 'file.md'
        file.write_text('x')
        linked = self.vault_at('linked')
        (linked / FOLDER).symlink_to(self.root / 'plain')
        blocked = self.vault_at('blocked')
        (blocked / FOLDER).write_text('user file')
        for value in ('', '   ', 'relative/vault', str(self.root / 'missing'), str(file), str(plain), str(linked), str(blocked), None, 3):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.sync.configure_project(self.board['id'], 'auto', value)
        if os.geteuid() != 0:
            readonly = self.vault_at('readonly')
            readonly.chmod(0o555)
            self.addCleanup(readonly.chmod, 0o755)
            with self.assertRaises(ValueError):
                self.sync.configure_project(self.board['id'], 'auto', str(readonly))
        self.assertEqual(self.sync.status()['connected'], 0)
        with self.assertRaises(ValueError):
            self.sync.configure_project(self.board['id'], 'selected')
        with self.assertRaises(ValueError):
            self.sync.configure_project(self.board['id'], 'everything', str(self.vault))
        with self.assertRaises(ValueError):
            self.sync.request_sync()
        self.assertEqual(list(plain.iterdir()), [])
        self.assertEqual((blocked / FOLDER).read_text(), 'user file')

    @unittest.skipIf(os.geteuid() == 0, 'root ignores permissions')
    def test_write_failures_are_reported_and_do_not_stop_collection(self):
        self.session()
        self.configure_and_sync()
        folder = self.notes()[0].parent
        self.session(session='session-2', cwd=self.project)
        folder.chmod(0o555)
        self.addCleanup(lambda: folder.exists() and folder.chmod(0o755))
        status = self.sync.sync()
        self.assertIn('쓰기 권한', status['error'])
        self.assertIn('지금 동기화', status['hint'])
        self.assertEqual(status['result']['errors'], 1)
        failed_at = status['last_success_at']
        # Collection keeps working while the vault is broken.
        codex = self.root / 'codex'
        codex.mkdir()
        (codex / 'x.jsonl').write_text(json.dumps(dict(timestamp=self.stamp(), type='session_meta', payload=dict(id='c-new', cwd=str(self.project), source='cli'))) + '\n'
                                       + json.dumps(dict(timestamp=self.stamp(), type='event_msg', payload={'type': 'user_message', 'message': '수집은 계속'})) + '\n')
        Collector(self.store, [('codex', codex)]).scan()
        self.assertIn('수집은 계속', [card['prompt'] for card in self.store.cards()])
        folder.chmod(0o755)
        status = self.sync.sync()
        self.assertIsNone(status['error'])
        self.assertNotEqual(status['last_success_at'], failed_at)
        self.assertEqual(len(self.notes()), 3)
        # A vanished vault is reported and never recreated.
        moved = self.root / 'moved-vault'
        self.vault.rename(moved)
        status = self.sync.sync()
        self.assertIn('Vault 폴더를 찾을 수 없습니다', status['error'])
        self.assertFalse(self.vault.exists())

    # ----- user file protection -----

    def test_user_edits_are_preserved_and_newer_content_goes_to_a_copy(self):
        rows = self.session()
        self.configure_and_sync()
        note = self.notes()[0]
        edited = note.read_text().replace('# 질문 0', '# 질문 0\n\n내가 추가한 메모').encode()
        note.write_bytes(edited)
        def add(prompt):
            self.store.save_turns([dict(rows[0], turn_key=prompt, prompt=prompt, result=prompt + ' 답변',
                                        started_at=self.stamp(), updated_at=self.stamp(), finished_at=self.stamp())])
            with self.store.connect() as db:
                db.execute('UPDATE cards SET updated_at=?,source_updated_at=?', (self.stamp(), self.stamp()))
        add('편집 후 새 요청')
        status = self.sync.sync()
        self.assertEqual(status['result']['conflicts'], 1)
        self.assertEqual(note.read_bytes(), edited)
        copy = note.with_name(note.stem + ' (Kankan 최신본).md')
        self.assertIn('편집 후 새 요청', copy.read_text())
        self.assertIn('kankan_role: "conflict-copy"', copy.read_text())
        self.assertEqual(status['conflicts'][0]['conflict_path'], str(copy.relative_to(self.vault)))
        self.assertEqual(self.sync.sync()['result']['conflicts'], 1)
        add('두 번째 새 요청')
        self.sync.sync()
        self.assertEqual(len(self.notes()), 2)
        self.assertIn('두 번째 새 요청', copy.read_text())
        self.assertEqual(note.read_bytes(), edited)
        resolved = self.sync.resolve('claude:session-1')
        self.assertEqual(resolved['conflicts'], [])
        backup = self.vault / resolved['backup']
        self.assertEqual(backup.read_bytes(), edited)
        self.assertFalse(copy.exists())
        self.assertIn('두 번째 새 요청', note.read_text())
        self.assertTrue(ownership(note.read_bytes())[2])
        self.assertEqual(self.sync.sync()['result']['unchanged'], 1)
        self.assertEqual(sorted(self.notes()), sorted([note, backup]))
        with self.assertRaises(ValueError):
            self.sync.resolve('claude:session-1')

    def test_frontmatter_removal_counts_as_edit(self):
        self.session()
        self.configure_and_sync()
        note = self.notes()[0]
        note.write_text('사용자가 전부 다시 쓴 노트')
        with self.store.connect() as db:
            db.execute('UPDATE cards SET updated_at=?', (self.stamp(),))
        self.assertEqual(self.sync.sync()['result']['conflicts'], 1)
        self.assertEqual(note.read_text(), '사용자가 전부 다시 쓴 노트')

    def test_foreign_files_and_symlinks_are_never_written_through(self):
        self.session()
        self.configure_and_sync()
        rel = self.notes()[0].relative_to(self.vault)
        other = self.vault_at('other')
        (other / rel).parent.mkdir(parents=True)
        (other / rel).write_text('사용자 노트')
        outside = self.root / 'outside.md'
        outside.write_text('바깥 파일')
        (other / rel).with_name(rel.stem + ' 2.md').symlink_to(outside)
        self.connect(other)
        status = self.sync.sync()
        self.assertIsNone(status['error'])
        self.assertEqual((other / rel).read_text(), '사용자 노트')
        self.assertEqual(outside.read_text(), '바깥 파일')
        self.assertTrue((other / rel).with_name(rel.stem + ' 3.md').is_file())
        # A symlinked project folder inside Kankan is refused, nothing written outside.
        third = self.vault_at('third')
        target = self.root / 'escape'
        target.mkdir()
        (third / FOLDER).mkdir()
        (third / FOLDER / 'my project').symlink_to(target)
        self.connect(third)
        status = self.sync.sync()
        self.assertIn('심볼릭 링크', status['error'])
        self.assertEqual(list(target.iterdir()), [])

    def test_archiving_deleting_and_disconnecting_never_remove_notes(self):
        self.session()
        self.session(session='session-2')
        self.configure_and_sync()
        notes = self.notes()
        card = self.store.cards()[0]
        self.store.update(card['id'], archived=True)
        self.assertEqual(self.sync.sync()['result']['updated'], 1)
        self.assertEqual(self.notes(), notes)
        self.assertIn(1, ['archived: true' in path.read_text() for path in notes])
        with self.store.connect() as db:
            db.execute('DELETE FROM cards WHERE id=?', (card['id'],))
        self.sync.sync()
        self.assertEqual(self.notes(), notes)
        self.connect(mode='off')
        self.assertEqual(self.notes(), notes)
        status = self.sync.status()
        self.assertEqual((status['connected'], status['projects']), (0, []))
        self.assertIsNone(self.sync.sync()['error'])
        self.assertEqual(self.notes(), notes)

    def test_concurrent_syncs_never_duplicate_or_leave_temp_files(self):
        for number in range(5):
            self.session(session=f's-{number}', turns=3)
        self.connect()
        workers = [ObsidianSync(Store(self.store.path)) for _ in range(4)]
        threads = [threading.Thread(target=worker.sync) for worker in workers for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.sync.sync()
        self.assertEqual(len(self.notes()), 5)
        self.assertEqual(list((self.vault / FOLDER).rglob('.kankan-*.tmp')), [])
        self.assertTrue(all(ownership(path.read_bytes())[2] for path in self.notes()))
        self.sync.lock.acquire()
        try:
            self.assertTrue(self.sync.sync()['busy'])
        finally:
            self.sync.lock.release()

    def test_moved_note_inside_kankan_folder_is_followed_not_duplicated(self):
        self.session()
        self.configure_and_sync()
        note = self.notes()[0]
        moved = self.vault / FOLDER / '내가 옮긴 노트.md'
        note.rename(moved)
        with self.store.connect() as db:
            db.execute('UPDATE cards SET updated_at=?', (self.stamp(),))
        self.assertIsNone(self.sync.sync()['error'])
        self.assertEqual(self.notes(), [moved])

    def test_auto_sync_thread_exports_new_activity_only_when_enabled(self):
        sync = ObsidianSync(self.store, interval=0.1)
        sync.start()
        self.addCleanup(sync.stop)
        rows = self.session()
        time.sleep(0.4)
        self.assertEqual(self.notes(), [])  # Nothing is written before a project is connected.
        self.connect(sync=sync)
        deadline = time.time() + 5
        while not self.notes() and time.time() < deadline:
            time.sleep(0.05)
        note = self.notes()[0]
        self.store.save_turns([dict(rows[0], turn_key='auto-2', prompt='자동 반영 요청', started_at=self.stamp(),
                                    updated_at=self.stamp(), finished_at=self.stamp())])
        while '자동 반영 요청' not in note.read_text() and time.time() < deadline:
            time.sleep(0.05)
        self.assertIn('자동 반영 요청', note.read_text())
        self.connect(mode='off', sync=sync)
        time.sleep(0.3)
        self.store.save_turns([dict(rows[0], turn_key='auto-3', prompt='꺼진 뒤 요청', started_at=self.stamp(),
                                    updated_at=self.stamp(), finished_at=self.stamp())])
        time.sleep(0.4)
        self.assertNotIn('꺼진 뒤 요청', note.read_text())

    def test_http_api(self):
        self.session()
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler(self.store, None, self.sync))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        def request(method, path, data=None, headers=None):
            connection = HTTPConnection('127.0.0.1', port)
            merged = {'Content-Type': 'application/json'}
            merged.update(headers or {})
            connection.request(method, path, json.dumps(data) if data is not None else None, merged)
            response = connection.getresponse()
            result = response.status, json.loads(response.read())
            connection.close()
            return result
        state = request('GET', '/api/state')[1]
        self.assertEqual((state['obsidian']['connected'], state['cards'][0]['obsidian']), (0, None))
        project = self.board['id']
        status, value = request('POST', '/api/obsidian', {'project': project, 'mode': 'auto', 'vault': 'relative'})
        self.assertEqual(status, 400)
        self.assertIn('절대 경로', value['error'])
        self.assertEqual(request('POST', '/api/obsidian', {'sync': True})[0], 400)
        configure = {'project': project, 'mode': 'auto', 'vault': str(self.vault)}
        self.assertEqual(request('POST', '/api/obsidian', configure, {'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(request('POST', '/api/obsidian', configure)[0], 200)
        self.assertEqual(request('POST', '/api/obsidian', {'project': project, 'mode': 'all'})[0], 400)
        self.assertEqual(request('POST', '/api/obsidian', {'project': 'missing', 'mode': 'auto'})[0], 400)
        self.assertEqual(request('POST', '/api/obsidian', {'bogus': 1})[0], 400)
        self.assertEqual(request('POST', '/api/obsidian', {'resolve': 'claude:session-1'})[0], 400)
        self.assertTrue(request('POST', '/api/obsidian', {'sync': True})[1]['queued'])
        self.sync.sync()  # The background thread is not running in this test.
        card = state['cards'][0]
        turn = self.store.all_turns('claude', 'session-1')[0]['turn_key']
        self.assertEqual(request('POST', '/api/obsidian', {'card_id': card['id'], 'turn_key': 'nope', 'picked': True})[0], 400)
        self.assertTrue(request('POST', '/api/obsidian', {'card_id': card['id'], 'turn_key': turn, 'picked': True})[1]['picked'])
        state = request('GET', '/api/state')[1]
        self.assertEqual(state['cards'][0]['obsidian_picks'], [turn])
        link = state['cards'][0]['obsidian']
        self.assertTrue(link['url'].startswith('obsidian://open?path=%2F'))
        self.assertTrue((self.vault / link['path']).is_file())
        self.assertEqual(state['obsidian']['notes'], 1)
        self.assertEqual(request('POST', '/api/obsidian', {'project': project, 'mode': 'off'})[1]['connected'], 0)
        self.assertTrue((self.vault / link['path']).is_file())

    def test_projects_have_independent_vaults_modes_and_status(self):
        other_dir = self.root / 'other project'
        other_dir.mkdir()
        other = self.store.create_board('other project', str(other_dir))
        second = self.vault_at('second')
        self.session(session='a-1', turns=2)
        rows = self.session(session='b-1', turns=3, cwd=other_dir)
        self.sync.configure_project(self.board['id'], 'auto', str(self.vault))
        self.sync.configure_project(other['id'], 'selected', str(second))
        status = self.sync.sync()
        self.assertEqual(len(self.notes()), 1)
        self.assertEqual(self.notes(second), [])  # Selected mode: nothing picked, nothing written.
        projects = {item['name']: item for item in status['projects']}
        self.assertEqual((projects['my project']['mode'], projects['other project']['mode']), ('auto', 'selected'))
        self.assertEqual(projects['other project']['result']['skipped'], 1)
        card = next(item for item in self.store.cards() if item['session_id'] == 'b-1')
        self.sync.pick(card['id'], rows[1]['turn_key'], True)
        self.sync.sync()
        note = self.notes(second)[0]
        text = note.read_text()
        self.assertEqual(code_blocks(text), ['질문 1', '답변 1'])
        self.assertIn('## 프롬프트 2 ·', text)  # Original numbering is kept.
        self.assertIn('kankan_mode: "selected"', text)
        self.assertIn('turn_count: 1', text)
        self.assertIn('session_turn_count: 3', text)
        self.sync.pick(card['id'], rows[2]['turn_key'], True)
        self.sync.sync()
        self.assertEqual(code_blocks(note.read_text()), ['질문 1', '답변 1', '질문 2', '답변 2'])
        # Unpicking everything stops updates but never deletes the note.
        for row in rows:
            self.sync.pick(card['id'], row['turn_key'], False)
        before = note.read_bytes()
        self.sync.sync()
        self.assertEqual(note.read_bytes(), before)
        # One project's broken vault does not affect the other project.
        second.rename(self.root / 'second-moved')
        status = self.sync.sync()
        projects = {item['name']: item for item in status['projects']}
        self.assertIn('Vault 폴더를 찾을 수 없습니다', projects['other project']['error'])
        self.assertIsNone(projects['my project']['error'])
        self.assertTrue(status['error'].startswith('other project: '))
        # Card links follow each project's own vault.
        links = self.sync.links(self.store.cards())
        mine = next(item for item in self.store.cards() if item['session_id'] == 'a-1')
        self.assertTrue(links[mine['id']]['url'].startswith('obsidian://open?path=' + str(self.vault).replace('/', '%2F')))

    def test_pick_validation(self):
        rows = self.session()
        card = self.store.cards()[0]
        for turn, picked in (('missing', True), (rows[0]['turn_key'], 'yes'), (None, True)):
            with self.assertRaises(ValueError):
                self.sync.pick(card['id'], turn, picked)
        with self.assertRaises(ValueError):
            self.sync.pick('no-card', rows[0]['turn_key'], True)
        self.assertEqual(self.sync.pick(card['id'], rows[0]['turn_key'], True)['picked'], True)
        self.assertEqual(self.sync.picks(), {('claude:session-1', rows[0]['turn_key'])})

    def test_render_is_deterministic(self):
        self.session()
        card = self.store.cards()[0]
        board = self.store.boards()[0]
        turns = self.store.all_turns('claude', 'session-1')
        self.assertEqual(render(card, board, turns), render(card, board, turns))


if __name__ == '__main__':
    unittest.main()
