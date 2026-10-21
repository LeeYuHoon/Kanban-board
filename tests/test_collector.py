import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from kanban.collector import Collector, source_roots
from kanban.store import Store


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'my project'
        self.project.mkdir()
        self.codex = self.root / 'codex'
        self.claude = self.root / 'claude'
        self.codex.mkdir()
        (self.claude / 'project').mkdir(parents=True)
        self.store = Store(self.root / 'kanban.sqlite3')
        self.collector = self.new_collector()
        self.clock = datetime.now(timezone.utc) - timedelta(seconds=20)
        self.sequence = 0

    def new_collector(self):
        return Collector(self.store, [('codex', self.codex), ('claude', self.claude)])

    def stamp(self):
        self.sequence += 1
        return (self.clock + timedelta(milliseconds=self.sequence)).isoformat()

    def write(self, path, events, mode='a'):
        with path.open(mode) as stream:
            for event in events:
                stream.write(json.dumps(event) + '\n')

    def ce(self, kind, payload):
        return dict(timestamp=self.stamp(), type=kind, payload=payload)

    def codex_session(self, filename='session.jsonl', session='codex-1', source='cli'):
        path = self.codex / filename
        self.write(path, [self.ce('session_meta', dict(id=session, cwd=str(self.project), source=source)),
                          self.ce('event_msg', {'type': 'task_started'}),
                          self.ce('response_item', dict(type='message', role='user', content=[{'type': 'input_text', 'text': '실제 사용자 요청'}]))])
        return path

    def claude_event(self, kind, **values):
        return dict(type=kind, timestamp=self.stamp(), cwd=str(self.project), sessionId='claude-1', **values)

    def test_codex_incremental_completion_resume_archive_and_restart(self):
        path = self.codex_session()
        original = path.read_bytes()
        self.collector.scan()
        self.assertEqual(path.read_bytes(), original)
        card = self.store.cards()[0]
        self.assertEqual(card['status'], 'running')
        self.assertEqual(card['source'], 'session')
        self.assertEqual(len(self.store.boards()), 1)
        self.store.update(card['id'], archived=True)
        self.write(path, [self.ce('token_usage_record', {'thread_token_usage': {'input_tokens': 10}}),
                          self.ce('event_msg', {'type': 'task_complete', 'last_agent_message': '응답 완료'})])
        restarted = self.new_collector()
        restarted.scan()
        saved = self.store.card(card['id'])
        self.assertEqual(saved['result'], '응답 완료')
        self.assertEqual(saved['status'], 'done')
        self.assertTrue(saved['archived'])
        self.assertEqual(saved['usage']['reported']['input_tokens'], 10)
        observed = saved['observed_at']
        restarted.scan()
        self.assertEqual(self.store.card(card['id'])['observed_at'], observed)
        self.assertEqual(len(self.store.cards()), 1)
        self.write(path, [self.ce('event_msg', {'type': 'task_started'}),
                          self.ce('response_item', dict(type='message', role='user', content=[{'type': 'input_text', 'text': '두 번째 요청'}]))])
        restarted.scan()
        saved = self.store.card(card['id'])
        self.assertEqual(saved['prompt'], '두 번째 요청')
        self.assertEqual(saved['status'], 'running')
        self.assertEqual(saved['result'], '')
        self.assertIsNone(saved['finished_at'])
        self.assertTrue(saved['archived'])
        history = self.store.history(card['id'])['items']
        self.assertEqual(len(history), 2)
        self.assertEqual((history[1]['prompt'], history[1]['result']), ('실제 사용자 요청', '응답 완료'))
        self.assertEqual(history[0]['prompt'], '두 번째 요청')


    def test_claude_real_user_tool_result_and_end_turn(self):
        path = self.claude / 'project' / 'main.jsonl'
        self.write(path, [self.claude_event('user', message={'content': '첫 요청'}),
                          self.claude_event('assistant', uuid='a', message={'id': 'm1', 'content': [{'type': 'tool_use', 'name': 'Read'}], 'stop_reason': 'tool_use'}),
                          self.claude_event('user', message={'content': [{'type': 'tool_result', 'content': 'sensitive tool output'}]})])
        self.collector.scan()
        card = self.store.cards()[0]
        self.assertEqual(card['prompt'], '첫 요청')
        self.assertEqual(card['status'], 'running')
        self.write(path, [self.claude_event('assistant', uuid='b', message={'id': 'm2', 'content': [{'type': 'text', 'text': '최종 응답'}], 'stop_reason': 'end_turn', 'usage': {'output_tokens': 15}})])
        self.collector.scan()
        card = self.store.cards()[0]
        self.assertEqual(card['status'], 'done')
        self.assertEqual(card['result'], '최종 응답')
        self.assertEqual(card['usage']['scope'], 'latest_response')
        self.write(path, [self.claude_event('user', isMeta=True, message={'content': '<task-notification>background job</task-notification>'})])
        self.collector.scan()
        self.assertEqual(self.store.cards()[0]['status'], 'done')
        self.write(path, [self.claude_event('user', message={'content': '추가 질문'})])
        self.collector.scan()
        self.assertEqual(self.store.cards()[0]['status'], 'running')
        self.assertEqual(len(self.store.cards()), 1)

    def test_partial_line_malformed_line_and_replay(self):
        path = self.codex_session()
        self.collector.scan()
        event = self.ce('event_msg', {'type': 'task_complete', 'last_agent_message': 'complete'})
        raw = json.dumps(event).encode()
        with path.open('ab') as stream:
            stream.write(b'not valid json\n[]\n' + raw[:20])
        self.collector.scan()
        self.assertEqual(self.store.cards()[0]['status'], 'running')
        with self.store.connect() as db:
            issues = db.execute('SELECT reason,raw FROM collection_issues ORDER BY offset').fetchall()
        self.assertEqual([row['raw'] for row in issues], [b'not valid json\n', b'[]\n'])
        self.assertEqual(self.collector.status()['errors'], 2)
        with path.open('ab') as stream:
            stream.write(raw[20:] + b'\n')
        self.collector.scan()
        self.assertEqual(self.store.cards()[0]['status'], 'done')
        self.write(path, [dict(timestamp=self.clock.isoformat(), type='event_msg', payload={'type': 'task_started'})])
        self.collector.scan()
        self.assertEqual(self.store.cards()[0]['status'], 'done')

    def test_pause_persists_and_resume_catches_up(self):
        path = self.codex_session()
        self.store.set_collection_enabled(False)
        self.collector.scan()
        self.assertEqual(self.store.cards(), [])
        self.assertFalse(Store(self.store.path).collection_enabled())
        self.write(path, [self.ce('event_msg', {'type': 'task_complete', 'last_agent_message': 'finished while paused'})])
        self.store.set_collection_enabled(True)
        self.new_collector().scan()
        self.assertEqual(self.store.cards()[0]['result'], 'finished while paused')

    def test_unchanged_file_is_not_opened_and_restart_catches_up(self):
        path = self.codex_session()
        self.collector.scan()
        with patch.object(self.collector, '_read', side_effect=AssertionError('unchanged file opened')):
            self.collector.scan()
        self.write(path, [self.ce('event_msg', {'type': 'task_complete', 'last_agent_message': 'after sleep'})])
        self.new_collector().scan()
        self.assertEqual(self.store.cards()[0]['result'], 'after sleep')

    def test_same_size_rewrite_is_rechecked(self):
        path = self.codex_session()
        self.collector.scan()
        events = [json.loads(line) for line in path.read_text().splitlines()]
        events[-1]['payload']['content'][0]['text'] = '다른 사용자 요청'
        self.write(path, events, mode='w')
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
        self.collector.scan()
        self.assertEqual(self.store.cards()[0]['prompt'], '다른 사용자 요청')

    def test_large_line_is_collected_without_a_size_cutoff(self):
        path = self.codex_session()
        self.collector.scan()
        self.write(path, [self.ce('unknown', {'large': 'x' * (4 * 1024 * 1024 + 1)}),
                          self.ce('event_msg', {'type': 'task_complete', 'last_agent_message': '큰 행 다음 답변'})])
        status = self.collector.scan()
        self.assertEqual(status['errors'], 0)
        self.assertEqual(self.store.cards()[0]['result'], '큰 행 다음 답변')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT offset FROM collection_files').fetchone()['offset'], path.stat().st_size)

    def test_stale_activity_is_not_claimed_as_running_or_done(self):
        self.clock -= timedelta(hours=1)
        self.codex_session()
        self.collector.scan()
        card = self.store.cards()[0]
        self.assertEqual(card['status'], 'unknown')
        self.assertIsNone(card['finished_at'])
        self.assertIn('15분', card['status_note'])

    def test_subagents_and_instruction_only_sessions_are_excluded(self):
        self.codex_session(source={'subagent': {'parent_thread_id': 'parent'}})
        p = self.codex_session('instructions.jsonl', 'instructions')
        p.write_text('')
        self.write(p, [self.ce('session_meta', dict(id='instructions', cwd=str(self.project))),
                       self.ce('response_item', dict(type='message', role='user', content=[{'type':'input_text', 'text':'<environment_context>metadata</environment_context>'}]))])
        sub = self.claude / 'project' / 'session' / 'subagents'
        sub.mkdir(parents=True)
        self.write(sub / 'agent.jsonl', [self.claude_event('user', message={'content': 'subtask'})])
        self.collector.scan()
        self.assertEqual(self.store.cards(), [])

    def test_wrapper_session_deduplicates_both_import_orders(self):
        path = self.codex_session()
        self.collector.scan()
        board = self.store.boards()[0]
        manual = self.store.create_card(board['id'], 'wrapped', '', 'codex')
        self.store.update(manual['id'], status='done', session_id='codex-1', result='wrapped result')
        self.assertEqual(len(self.store.cards()), 1)
        self.write(path, [self.ce('event_msg', {'type': 'task_complete', 'last_agent_message': 'native result'})])
        self.collector.scan()
        self.assertEqual(len(self.store.cards()), 1)
        self.assertEqual(self.store.cards()[0]['result'], 'wrapped result')

    def test_rotated_file_and_duplicate_source_cannot_regress_card(self):
        path = self.codex_session()
        old = path.read_text()
        self.write(path, [self.ce('event_msg', {'type':'task_complete', 'last_agent_message':'newest'})])
        self.collector.scan()
        copied = self.codex / 'older-copy.jsonl'
        copied.write_text(old)
        self.collector.scan()
        self.assertEqual(len(self.store.cards()), 1)
        self.assertEqual(self.store.cards()[0]['status'], 'done')
        path.unlink()
        self.codex_session()
        self.collector.scan()
        self.assertEqual(self.store.cards()[0]['status'], 'running')

    def test_claude_identical_questions_keep_separate_answers(self):
        path = self.claude / 'project' / 'conversation.jsonl'
        for n in range(2):
            self.write(path, [self.claude_event('user', uuid='user-' + str(n), message={'content': '같은 질문'}),
                self.claude_event('assistant', uuid='answer-' + str(n), message={
                    'id': 'message-' + str(n), 'stop_reason': 'end_turn',
                    'content': [{'type': 'text', 'text': '답변 ' + str(n)}]})])
        self.collector.scan()
        card = self.store.cards()[0]
        items = self.store.history(card['id'])['items']
        self.assertEqual([(v['prompt'], v['result']) for v in items], [('같은 질문', '답변 1'), ('같은 질문', '답변 0')])
        self.new_collector().scan()
        self.assertEqual(self.store.cards()[0]['turn_count'], 2)

    def test_legacy_cursor_backfills_history_without_changing_card(self):
        path = self.codex_session()
        self.write(path, [self.ce('event_msg', {'type': 'task_complete', 'last_agent_message': '첫 답변'}),
                          self.ce('event_msg', {'type': 'task_started', 'turn_id': 'second'}),
                          self.ce('response_item', {'type': 'message', 'role': 'user', 'content': [{'type':'input_text','text':'둘째 질문'}]}),
                          self.ce('event_msg', {'type': 'task_complete', 'last_agent_message': '둘째 답변'})])
        self.collector.scan()
        card = self.store.cards()[0]
        self.store.update(card['id'], archived=True)
        original = path.read_bytes()
        with self.store.connect() as db:
            cursor = db.execute('SELECT state FROM collection_files').fetchone()
            legacy = json.loads(cursor['state']); legacy.pop('_version')
            db.execute('UPDATE collection_files SET state=?', (json.dumps(legacy),))
            db.execute('DELETE FROM session_turns')
        self.new_collector().scan()
        recovered = self.store.cards()[0]
        self.assertEqual(recovered['id'], card['id'])
        self.assertTrue(recovered['archived'])
        self.assertEqual(recovered['turn_count'], 2)
        self.assertEqual([x['result'] for x in self.store.history(card['id'])['items']], ['둘째 답변', '첫 답변'])
        self.assertEqual(path.read_bytes(), original)

    def test_history_pagination_is_stable_after_new_request(self):
        path = self.codex_session()
        self.write(path, [self.ce('event_msg', {'type':'task_complete','last_agent_message':'answer 0'})])
        for n in range(1, 35):
            self.write(path, [self.ce('event_msg', {'type':'task_started','turn_id':str(n)}),
                self.ce('event_msg', {'type':'user_message','message':'question '+str(n)}),
                self.ce('event_msg', {'type':'task_complete','last_agent_message':'answer '+str(n)})])
        self.collector.scan()
        card = self.store.cards()[0]
        first = self.store.history(card['id'])
        self.assertEqual(len(first['items']), 30)
        self.write(path, [self.ce('event_msg', {'type':'task_started','turn_id':'new'}),
                         self.ce('event_msg', {'type':'user_message','message':'new request'})])
        self.collector.scan()
        second = self.store.history(card['id'], first['next'])
        self.assertEqual(len(second['items']), 5)
        self.assertIsNone(second['next'])
        self.assertEqual(len({x['turn_key'] for x in first['items'] + second['items']}), 35)

    def test_old_logs_are_collected_and_config_roots(self):
        p = self.codex_session()
        old = (datetime.now(timezone.utc) - timedelta(days=8)).timestamp()
        os.utime(p, (old, old))
        self.collector.scan()
        self.assertEqual(len(self.store.cards()), 1)
        with patch.dict(os.environ, {'KANBAN_SESSION_ROOTS': json.dumps({'codex':[str(self.codex)]})}):
            self.assertEqual(source_roots(), [('codex', self.codex)])
        with patch.dict(os.environ, {'KANBAN_SESSION_ROOTS': '{}'}):
            self.assertEqual(source_roots(), [])

    def test_orca_claude_profile_is_discovered(self):
        profile = self.root / 'Library/Application Support/orca/claude-profiles/account/home/projects'
        profile.mkdir(parents=True)
        with patch('kanban.collector.Path.home', return_value=self.root), patch.dict(os.environ, {}, clear=True):
            roots = source_roots()
        self.assertIn(('claude', profile), roots)

    def test_codex_code_mode_tools_and_sub_agent_messages(self):
        path = self.codex_session()
        script = ('text(await tools.exec_command({cmd:"cat /s/review/SKILL.md"}));\n'
                  'await tools.apply_patch({input:"*** Begin Patch"}); await tools.view_image({path:"/x.png"});\n'
                  'await tools.web__run({search_query:[]}); [1].reduce((a)=>a); await tools.mcp__figma__get_file({});')
        self.write(path, [self.ce('response_item', dict(type='custom_tool_call', name='exec', input=script)),
                          self.ce('response_item', dict(type='function_call', namespace='collaboration', name='send_message',
                                                        arguments=json.dumps({'target': '/root/local_infra', 'message': 'secret'}))),
                          self.ce('response_item', dict(type='function_call', namespace='collaboration', name='followup_task',
                                                        arguments=json.dumps({'target': '/root', 'message': 'x'}))),
                          self.ce('event_msg', {'type': 'item_completed', 'item': {'type': 'SubAgentActivity', 'kind': 'interacted',
                                                                                    'agent_path': '/root/mobile_recovery'}})])
        self.collector.scan()
        activity = self.store.all_turns('codex', 'codex-1')[0]['activity']
        self.assertEqual(sorted(activity['tools']), sorted(['exec_command', 'apply_patch', 'view_image', 'web.run',
                                                            'mcp__figma__get_file', 'collaboration.send_message',
                                                            'collaboration.followup_task']))
        self.assertEqual(activity['agents'], ['local_infra', 'mobile_recovery'])
        self.assertEqual(activity['skill_references'], ['review'])
        self.assertNotIn('secret', json.dumps(activity))

    def test_claude_send_message_recipient_is_an_agent(self):
        path = self.claude / 'project' / 'team.jsonl'
        self.write(path, [self.claude_event('user', uuid='u1', message={'content': '팀 진행'}),
                          self.claude_event('assistant', uuid='a1', message={'id': 'm1', 'stop_reason': 'end_turn', 'content': [
                              {'type': 'tool_use', 'name': 'SendMessage', 'input': {'to': 'inq-fe', 'message': 'private'}},
                              {'type': 'text', 'text': '보냈습니다'}]})])
        self.collector.scan()
        self.assertEqual(self.store.all_turns('claude', 'claude-1')[0]['activity']['agents'], ['inq-fe'])

    def test_claude_agent_ids_resolve_to_launch_name(self):
        path = self.claude / 'project' / 'ids.jsonl'
        self.write(path, [self.claude_event('user', uuid='u1', message={'content': '에이전트 실행'}),
                          self.claude_event('assistant', uuid='a1', message={'id': 'm1', 'content': [
                              {'type': 'tool_use', 'id': 'tu1', 'name': 'Agent', 'input': {'subagent_type': 'general-purpose', 'name': 'inq-fe', 'prompt': 'secret task'}}]}),
                          self.claude_event('user', uuid='r1', toolUseResult={'agentId': 'a0123456789abcdef', 'status': 'async_launched'},
                                            message={'content': [{'type': 'tool_result', 'tool_use_id': 'tu1', 'content': 'launched'}]}),
                          self.claude_event('assistant', uuid='a2', message={'id': 'm2', 'stop_reason': 'end_turn', 'content': [
                              {'type': 'tool_use', 'id': 'tu2', 'name': 'SendMessage', 'input': {'to': 'a0123456789abcdef', 'message': 'x'}},
                              {'type': 'tool_use', 'id': 'tu3', 'name': 'SendMessage', 'input': {'to': 'affffffffffffffff', 'message': 'y'}}]})])
        self.collector.scan()
        agents = self.store.all_turns('claude', 'claude-1')[0]['activity']['agents']
        self.assertEqual(agents, ['general-purpose', 'inq-fe', 'affffffffffffffff'])  # Unknown ids stay as recorded.
        self.assertNotIn('secret', json.dumps(self.store.all_turns('claude', 'claude-1')))

    def test_parser_upgrade_rereads_old_files(self):
        path = self.codex_session()
        self.write(path, [self.ce('response_item', dict(type='custom_tool_call', name='exec',
                                                        input='await tools.apply_patch({})'))])
        self.collector.scan()
        with self.store.connect() as db:  # Simulate data collected by an older parser.
            db.execute("UPDATE session_turns SET activity='{}'")
            state = json.loads(db.execute('SELECT state FROM collection_files').fetchone()['state'])
            state['_version'] = 4
            db.execute('UPDATE collection_files SET state=?', (json.dumps(state),))
        old = time.time() - 30 * 86400
        os.utime(path, (old, old))
        self.collector.scan()
        self.assertEqual(self.store.all_turns('codex', 'codex-1')[0]['activity']['tools'], ['apply_patch'])
        self.assertEqual(len(self.store.cards()), 1)
        with self.store.connect() as db:  # Upgraded cursor: not re-read on the next scans.
            self.assertEqual(json.loads(db.execute('SELECT state FROM collection_files').fetchone()['state'])['_version'], 5)

    def test_large_old_file_is_resumed_until_the_end(self):
        path = self.codex_session()
        filler = [self.ce('response_item', dict(type='custom_tool_call', name='exec', input='x' * 1000)) for _ in range(30)]
        self.write(path, filler + [self.ce('response_item', dict(type='custom_tool_call', name='exec',
                                                                 input='await tools.view_image({})'))])
        old = time.time() - 30 * 86400
        os.utime(path, (old, old))
        with patch('kanban.collector.READ_BUDGET', 4000):
            collector = Collector(self.store, [('codex', self.codex), ('claude', self.claude)])
            collector.scan()
            for _ in range(20):
                collector.scan()
        with self.store.connect() as db:
            offset = db.execute('SELECT offset FROM collection_files').fetchone()['offset']
        self.assertEqual(offset, path.stat().st_size)
        self.assertIn('view_image', self.store.all_turns('codex', 'codex-1')[0]['activity']['tools'])


if __name__ == '__main__':
    unittest.main()
