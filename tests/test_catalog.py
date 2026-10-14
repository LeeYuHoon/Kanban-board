import json
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from kanban import catalog
from kanban.collector import Collector
from kanban.server import handler
from kanban.store import Store


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'k.sqlite3')

    def test_listings_from_claude_transcript_records(self):
        stamp = datetime.now(timezone.utc).isoformat()
        records = [
            {'type': 'attachment', 'timestamp': stamp, 'attachment': {'type': 'skill_listing',
             'content': '- run-pr: PR 1개를 새 세션에서 구현\n- orca-cli: Use the public orca CLI\nnot a listing line'}},
            {'type': 'attachment', 'attachment': {'type': 'agent_listing_delta',
             'addedLines': "['- general-purpose: General-purpose agent for research. (Tools: *)', '- Explore: Read-only search agent (Tools: Read, Grep)', '- oh-my-claudecode:critic: Work plan and code review expert (Opus) (Tools: All tools except Write, Edit)']"}},
            {'type': 'attachment', 'attachment': {'type': 'mcp_instructions_delta', 'addedNames': ['claude.ai Google Drive'],
             'addedBlocks': ['## claude.ai Google Drive\n**IMPORTANT**: Search and read files in Google Drive.']}},
            {'type': 'attachment', 'attachment': {'type': 'deferred_tools_delta', 'surfacedDefinitions': [
                {'definition': {'name': 'mcp__claude-in-chrome__navigate', 'description': 'Navigate to a URL'}},
                {'definition': {'name': 'Monitor', 'description': 'not an MCP tool'}}]}},
            {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': 'Agent',
             'input': {'name': 'inq-fe', 'subagent_type': 'general-purpose', 'prompt': 'secret task'}}]}},
        ]
        entries = [entry for record in records for entry in catalog.from_claude_event(record)]
        self.assertIn(('skill', 'run-pr', 'PR 1개를 새 세션에서 구현'), entries)
        self.assertIn(('agent', 'general-purpose', 'General-purpose agent for research.'), entries)
        self.assertIn(('agent', 'Explore', 'Read-only search agent'), entries)
        self.assertIn(('agent', 'oh-my-claudecode:critic', 'Work plan and code review expert (Opus)'), entries)
        self.assertIn(('mcp_server', 'claude_ai_Google_Drive', 'IMPORTANT: Search and read files in Google Drive.'), entries)
        self.assertIn(('mcp_tool', 'mcp__claude-in-chrome__navigate', 'Navigate to a URL'), entries)
        self.assertIn(('agent_alias', 'inq-fe', 'general-purpose'), entries)
        self.assertEqual(len(entries), 8)
        self.assertNotIn('secret', json.dumps(entries))

    def test_skill_file_frontmatter_forms(self):
        cases = {'plain': 'description: Plain words here', 'quoted': 'description: "blind-app 전용. 따옴표"',
                 'folded': 'description: >-\n  Use Orca CLI\n  for terminals\nother: x', 'none': 'name: x'}
        for name, front in cases.items():
            path = self.root / name / 'SKILL.md'
            path.parent.mkdir()
            path.write_text(f'---\nname: {name}\n{front}\n---\n# Body\ndescription: not this')
        self.assertEqual(catalog.skill_description(self.root / 'plain/SKILL.md'), 'Plain words here')
        self.assertEqual(catalog.skill_description(self.root / 'quoted/SKILL.md'), 'blind-app 전용. 따옴표')
        self.assertEqual(catalog.skill_description(self.root / 'folded/SKILL.md'), 'Use Orca CLI for terminals')
        self.assertIsNone(catalog.skill_description(self.root / 'none/SKILL.md'))

    def test_user_descriptions_save_edit_delete_and_validate(self):
        catalog.save(self.store, [('skill', 'run-pr', '기록된 설명')], 'transcript')
        before = catalog.version(self.store)
        catalog.set_user_description(self.store, 'tool', 'exec_command', '  터미널 명령 실행  ')
        catalog.set_user_description(self.store, 'skill', 'run-pr', '내가 쓴 설명')
        loaded = catalog.load(self.store)
        self.assertEqual(loaded['user']['tool']['exec_command']['text'], '터미널 명령 실행')
        self.assertEqual(loaded['skill']['run-pr']['description'], '기록된 설명')  # Recorded text is untouched.
        self.assertNotEqual(catalog.version(self.store), before)
        catalog.set_user_description(self.store, 'tool', 'exec_command', '수정한 설명')
        self.assertEqual(catalog.load(self.store)['user']['tool']['exec_command']['text'], '수정한 설명')
        self.assertTrue(catalog.set_user_description(self.store, 'tool', 'exec_command', '   ')['deleted'])
        self.assertNotIn('tool', catalog.load(self.store)['user'])
        for kind, name, text in (('model', 'x', 'y'), ('tool', '', 'y'), ('tool', 'x' * 201, 'y'), ('tool', 'x', 'y' * 2001), ('tool', 'x', None)):
            with self.assertRaises(ValueError):
                catalog.set_user_description(self.store, kind, name, text)
        self.assertEqual(Store(self.store.path) and catalog.load(Store(self.store.path))['user']['skill']['run-pr']['text'], '내가 쓴 설명')

    def test_collection_backfill_live_capture_and_api(self):
        project = self.root / 'repo'
        (project / '.claude/skills/run-spec').mkdir(parents=True)
        (project / '.claude/skills/run-spec/SKILL.md').write_text('---\ndescription: 명세 일괄 진행\n---\n')
        (project / '.claude/skills/run-pr').mkdir(parents=True)
        (project / '.claude/skills/run-pr/SKILL.md').write_text('---\ndescription: 파일 설명\n---\n')
        logs = self.root / 'claude' / 'p'
        logs.mkdir(parents=True)
        stamp = datetime.now(timezone.utc).isoformat()
        def event(**values):
            return dict(timestamp=stamp, cwd=str(project), sessionId='s1', entrypoint='cli', **values)
        (logs / 'a.jsonl').write_text('\n'.join(json.dumps(item) for item in [
            event(type='attachment', attachment={'type': 'skill_listing', 'content': '- run-pr: 기록된 설명'}),
            event(type='user', uuid='u1', message={'content': '요청'}),
            event(type='assistant', uuid='a1', message={'id': 'm', 'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': '답'}]}),
        ]) + '\n')
        collector = Collector(self.store, [('claude', self.root / 'claude')])
        with patch.object(catalog, 'skill_dirs', lambda directories: [project / '.claude/skills']):
            collector.scan()
        loaded = catalog.load(self.store)
        self.assertEqual(loaded['skill']['run-pr']['description'], '기록된 설명')  # Transcript wins over the file.
        self.assertEqual(loaded['skill']['run-spec'], {'description': '명세 일괄 진행', 'source': 'skill_file'})
        with patch.object(catalog, 'project_skill_dirs', lambda directories: [project / '.claude/skills']):
            catalog.refresh_skill_files(self.store)
        self.assertEqual(set(catalog.load(self.store)['skill_scope']), {'run-spec', 'run-pr'})
        self.assertEqual(self.store.setting('catalog_version'), str(catalog.CATALOG_VERSION))
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler(self.store))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        def get(path):
            connection = HTTPConnection('127.0.0.1', server.server_address[1])
            connection.request('GET', path)
            response = connection.getresponse()
            value = json.loads(response.read())
            connection.close()
            return value
        self.assertEqual(get('/api/catalog')['skill']['run-spec']['description'], '명세 일괄 진행')
        self.assertEqual(get('/api/state')['catalog_version'], catalog.version(self.store))
        def post(data, headers=None):
            connection = HTTPConnection('127.0.0.1', server.server_address[1])
            connection.request('POST', '/api/descriptions', json.dumps(data), {'Content-Type': 'application/json', **(headers or {})})
            response = connection.getresponse()
            result = response.status, json.loads(response.read())
            connection.close()
            return result
        self.assertEqual(post({'kind': 'tool', 'name': 'apply_patch', 'text': '파일 수정'})[0], 200)
        self.assertEqual(get('/api/catalog')['user']['tool']['apply_patch']['text'], '파일 수정')
        self.assertEqual(post({'kind': 'tool', 'name': 'apply_patch'})[0], 400)
        self.assertEqual(post({'kind': 'tool', 'name': 'apply_patch', 'text': 'x'}, {'Origin': 'https://evil.example'})[0], 403)
        self.assertTrue(post({'kind': 'tool', 'name': 'apply_patch', 'text': ''})[1]['deleted'])


if __name__ == '__main__':
    unittest.main()
