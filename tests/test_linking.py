import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kanban.collector import Collector
from kanban.linking import normalize_repo, repo_from_directory
from kanban.obsidian import FOLDER, MERGED, ObsidianSync, ownership
from kanban.store import Store

URL = 'https://github.com/Example/blind-app.git'


class LinkingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / 'blind-app'
        (self.repo / '.git').mkdir(parents=True)
        (self.repo / '.git' / 'config').write_text(f'[remote "origin"]\n\turl = {URL}\n')
        self.codex = self.root / 'codex'
        self.claude = self.root / 'claude'
        self.codex.mkdir()
        (self.claude / 'p').mkdir(parents=True)
        self.store = Store(self.root / 'k.sqlite3')
        self.collector = Collector(self.store, [('codex', self.codex), ('claude', self.claude)])
        self.clock = datetime.now(timezone.utc) - timedelta(minutes=30)
        self.sequence = 0

    def stamp(self):
        self.sequence += 1
        return (self.clock + timedelta(seconds=self.sequence)).isoformat()

    def write(self, name, events):
        path = self.codex / name if name.endswith('.codex') else self.claude / 'p' / name
        with path.with_suffix('.jsonl').open('a') as stream:
            for event in events:
                stream.write(json.dumps(event) + '\n')

    def ce(self, kind, payload):
        return dict(timestamp=self.stamp(), type=kind, payload=payload)

    def codex_start(self, name, session, source, cwd, prompt, tool=None):
        meta = dict(id=session, cwd=str(cwd), source=source, timestamp=self.stamp(),
                    originator='codex_exec' if source == 'exec' else 'codex-tui', git={'repository_url': URL})
        events = [self.ce('session_meta', meta), self.ce('event_msg', {'type': 'task_started'}),
                  self.ce('response_item', dict(type='message', role='user', content=[{'type': 'input_text', 'text': prompt}]))]
        if tool:
            events.append(self.ce('response_item', dict(type='function_call', name=tool, arguments='{"cmd":"codex exec ..."}')))
        self.write(name, events)

    def codex_turn(self, name, prompt, tool=None):
        events = [self.ce('event_msg', {'type': 'task_started'}),
                  self.ce('response_item', dict(type='message', role='user', content=[{'type': 'input_text', 'text': prompt}]))]
        if tool:
            events.append(self.ce('response_item', dict(type='function_call', name=tool, arguments='{}')))
        self.write(name, events)

    def codex_finish(self, name, answer):
        self.write(name, [self.ce('event_msg', {'type': 'task_complete', 'last_agent_message': answer})])

    def card(self, session):
        return next(card for card in self.store.cards() if card['session_id'] == session)

    def test_repository_identity(self):
        self.assertEqual(normalize_repo('git@github.com:Example/Blind-App.git'), 'github.com/example/blind-app')
        self.assertEqual(normalize_repo('https://token@github.com/Example/blind-app/'), 'github.com/example/blind-app')
        worktree = self.root / 'wt'
        worktree.mkdir()
        (worktree / '.git').write_text(f'gitdir: {self.repo}/.git/worktrees/wt\n')
        self.assertEqual(repo_from_directory(str(worktree)), 'github.com/example/blind-app')
        self.assertEqual(repo_from_directory(str(self.repo / 'sub' / 'dir')), 'github.com/example/blind-app')
        self.assertEqual(repo_from_directory(str(self.repo / '.claude/worktrees/gone')), 'github.com/example/blind-app')

    def test_codex_exec_runs_attach_to_the_prompt_that_launched_them(self):
        self.codex_start('parent.codex', 'parent', 'cli', self.repo, '하위 작업을 돌려줘', 'functions.exec_command')
        # Worktree folders that no longer exist still match through git.repository_url.
        for number in range(3):
            self.codex_start(f'child{number}.codex', f'child-{number}', 'exec',
                             self.repo / '.claude/worktrees' / f'w{number}', f'자동 작업 {number}')
            self.codex_finish(f'child{number}.codex', f'자동 결과 {number}')
        self.codex_finish('parent.codex', '모두 끝났습니다')
        self.codex_turn('parent.codex', '다음 질문')
        self.codex_start('late.codex', 'late', 'exec', self.repo, '나중 실행')
        self.collector.scan()
        parent = self.card('parent')
        first_turn = self.store.all_turns('codex', 'parent')[0]
        self.assertEqual(parent['launch'], 'interactive')
        for number in range(3):
            child = self.card(f'child-{number}')
            self.assertEqual((child['launch'], child['parent_id'], child['root_id']), ('automated', parent['id'], parent['id']))
            self.assertEqual(child['parent_turn_key'], first_turn['turn_key'])
        # Started during the second prompt, which did not run a shell, but is the only candidate.
        self.assertEqual(self.card('late')['parent_turn_key'], self.store.all_turns('codex', 'parent')[1]['turn_key'])
        before = [(card['id'], card['parent_id']) for card in self.store.cards()]
        self.collector.scan()
        self.assertEqual([(card['id'], card['parent_id']) for card in self.store.cards()], before)
        self.assertIsNone(parent['root_id'])

    def test_unmatched_and_ambiguous_runs_stay_separate(self):
        self.codex_start('a.codex', 'a', 'cli', self.repo, 'A 작업', 'exec_command')
        self.codex_start('b.codex', 'b', 'cli', self.repo, 'B 작업', 'exec_command')
        self.codex_start('both.codex', 'both', 'exec', self.repo, '누가 실행했는지 모름')
        self.codex_finish('a.codex', 'A 끝')
        self.codex_finish('b.codex', 'B 끝')
        self.codex_start('orphan.codex', 'orphan', 'exec', self.repo / '.claude/worktrees/x', '실행 중인 프롬프트 없음')
        self.collector.scan()
        for session in ('both', 'orphan'):
            card = self.card(session)
            self.assertEqual((card['launch'], card['parent_id']), ('automated', None))
        # Unlinked runs are shown in the repository's main project, not a worktree project.
        self.assertIn(self.card('orphan')['home_board_id'], {self.card('a')['board_id'], self.card('b')['board_id']})

    def test_shell_use_breaks_ties_and_nested_runs_resolve_to_the_top(self):
        self.codex_start('a.codex', 'a', 'cli', self.repo, '대화만 하는 세션')
        self.codex_start('b.codex', 'b', 'cli', self.repo, '실행하는 세션', 'exec_command')
        self.codex_start('kid.codex', 'kid', 'exec', self.repo, '1단계', 'exec_command')
        self.codex_start('grandkid.codex', 'grandkid', 'exec', self.repo, '2단계')
        self.collector.scan()
        b, kid = self.card('b'), self.card('kid')
        self.assertEqual(kid['parent_id'], b['id'])
        # Both interactive prompts were running, b used a shell; the nested run's root is b as well.
        self.assertEqual(self.card('grandkid')['root_id'], b['id'])

    def test_claude_sdk_runs_attach_to_interactive_claude_prompt(self):
        def cl(kind, session, entrypoint, **values):
            return dict(type=kind, timestamp=self.stamp(), cwd=str(self.repo), sessionId=session, entrypoint=entrypoint, **values)
        self.write('parent.claude', [cl('user', 'cp', 'cli', uuid='u1', message={'content': '조사해줘'}),
                                     cl('assistant', 'cp', 'cli', uuid='a1', message={'id': 'm1', 'content': [{'type': 'tool_use', 'name': 'Bash', 'input': {'command': 'claude -p'}}]})])
        self.write('kid.claude', [cl('user', 'ck', 'sdk-cli', uuid='u2', message={'content': '자동 조사'}),
                                  cl('assistant', 'ck', 'sdk-cli', uuid='a2', message={'id': 'm2', 'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': '조사 결과'}]})])
        self.collector.scan()
        self.assertEqual(self.card('ck')['parent_id'], self.card('cp')['id'])
        self.assertEqual(self.card('cp')['launch'], 'interactive')

    def test_cd_into_subfolder_keeps_project_and_boards_record_origin(self):
        manual = self.store.create_board('직접 등록', str(self.root / 'codex'))
        self.assertEqual(manual['origin'], 'manual')
        self.codex_start('s.codex', 's', 'cli', self.repo, '루트에서 시작')
        self.collector.scan()
        board = self.card('s')['board_id']
        (self.repo / 'backend').mkdir()
        self.write('s.codex', [self.ce('turn_context', dict(cwd=str(self.repo / 'backend'), model='m')),
                               self.ce('event_msg', {'type': 'task_complete', 'last_agent_message': '끝'})])
        self.collector.scan()
        self.assertEqual(self.card('s')['board_id'], board)
        origins = {item['directory']: item['origin'] for item in self.store.boards()}
        self.assertEqual(origins, {str(self.root / 'codex'): 'manual', str(self.repo): 'session'})

    def test_selected_prompt_brings_only_its_own_agent_runs(self):
        vault = self.root / 'vault'
        (vault / '.obsidian').mkdir(parents=True)
        self.codex_start('parent.codex', 'parent', 'cli', self.repo, '첫 프롬프트', 'exec_command')
        self.codex_start('k1.codex', 'k1', 'exec', self.repo, '첫 프롬프트의 자동 실행')
        self.codex_finish('parent.codex', '첫 답')
        self.codex_turn('parent.codex', '둘째 프롬프트', 'exec_command')
        self.codex_start('k2.codex', 'k2', 'exec', self.repo, '둘째 프롬프트의 자동 실행')
        self.codex_finish('parent.codex', '둘째 답')
        self.collector.scan()
        sync = ObsidianSync(self.store)
        sync.configure_project(self.card('parent')['board_id'], 'selected', str(vault))
        with self.assertRaises(ValueError):  # Agent runs follow their prompt; they are not picked directly.
            sync.pick(self.card('k2')['id'], self.store.all_turns('codex', 'k2')[0]['turn_key'], True)
        sync.pick(self.card('parent')['id'], self.store.all_turns('codex', 'parent')[1]['turn_key'], True)
        sync.sync()
        text = next((vault / FOLDER).rglob('*.md')).read_text()
        self.assertIn('둘째 프롬프트의 자동 실행', text)
        self.assertNotIn('첫 프롬프트의 자동 실행', text)
        self.assertNotIn('첫 답', text)

    def test_obsidian_writes_children_inside_parent_and_folds_old_notes(self):
        vault = self.root / 'vault'
        (vault / '.obsidian').mkdir(parents=True)
        sync = ObsidianSync(self.store)
        self.codex_start('parent.codex', 'parent', 'cli', self.repo, '부모 요청', 'exec_command')
        self.codex_start('c1.codex', 'c1', 'exec', self.repo / '.claude/worktrees/c1', '첫 자동 요청')
        self.codex_finish('c1.codex', '첫 자동 답변')
        self.codex_start('c2.codex', 'c2', 'exec', self.repo / '.claude/worktrees/c2', '둘째 자동 요청')
        self.codex_finish('c2.codex', '둘째 자동 답변')
        self.codex_finish('parent.codex', '부모 답변')
        # Simulate notes exported before grouping existed: one separate note per child.
        self.collector.scan()
        with self.store.connect() as db:
            db.execute('UPDATE cards SET parent_id=NULL,parent_turn_key=NULL')
        for board in self.store.boards():
            sync.configure_project(board['id'], 'auto', str(vault))
        self.assertEqual(sync.sync()['result']['created'], 3)
        old = {path.name: path for path in (vault / FOLDER).rglob('*.md')}
        c2_note = next(path for name, path in old.items() if 'c2' in name)
        c2_note.write_text(c2_note.read_text() + '\n사용자 메모\n')
        self.collector.scan()  # Links again.
        status = sync.sync()
        self.assertIsNone(status['error'])
        self.assertEqual(status['result']['merged'], 1)
        self.assertEqual(status['result']['merged_kept'], 1)
        visible = [path for path in (vault / FOLDER).rglob('*.md') if MERGED not in path.parts]
        self.assertEqual(sorted(path.name for path in visible), sorted([c2_note.name, next(n for n in old if '부모' in n)]))
        self.assertIn('사용자 메모', c2_note.read_text())  # Edited note left in place.
        hidden = list((vault / FOLDER / MERGED).rglob('*.md'))
        self.assertEqual(len(hidden), 1)
        self.assertTrue(ownership(hidden[0].read_bytes())[2])
        parent_note = next(path for path in visible if '부모' in path.name).read_text()
        self.assertIn('### 에이전트가 실행한 하위 세션 (2)', parent_note)
        self.assertIn('첫 자동 요청', parent_note)
        self.assertIn('둘째 자동 답변', parent_note)
        self.assertIn('child_sessions: 2', parent_note)
        self.assertLess(parent_note.index('부모 답변'), parent_note.index('첫 자동 요청'))
        self.assertEqual(sync.status()['notes'], 1)
        # Project folders emptied by the move are removed; a folder holding anything else is kept.
        self.assertEqual(status['result']['pruned'], 1)
        self.assertFalse((vault / FOLDER / 'c1').exists())
        self.assertTrue(c2_note.parent.is_dir())
        links = sync.links(self.store.cards())
        self.assertEqual(list(links), [self.card('parent')['id']])
        self.assertEqual(sync.sync()['result']['unchanged'], 1)
        # A new child arriving later updates the parent note, not a new file.
        self.codex_turn('parent.codex', '하나 더', 'exec_command')
        self.codex_start('c3.codex', 'c3', 'exec', self.repo, '셋째 자동 요청')
        self.collector.scan()
        self.assertEqual(sync.sync()['result']['updated'], 1)
        self.assertEqual(len([p for p in (vault / FOLDER).rglob('*.md') if MERGED not in p.parts]), 2)


if __name__ == '__main__':
    unittest.main()
