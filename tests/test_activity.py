import tempfile
import unittest
from pathlib import Path
from kanban.collector import new_state, consume, turn_snapshot
from kanban.store import Store


class ActivityTests(unittest.TestCase):
    def test_codex_activity_is_per_request_and_persisted(self):
        state = new_state('codex')
        def event(kind, payload):
            consume(state, {'type': kind, 'timestamp': '2026-09-26T10:00:00Z', 'payload': payload})
        event('session_meta', {'id': 'session', 'cwd': '/tmp'})
        event('event_msg', {'type': 'user_message', 'message': 'first'})
        event('turn_context', {'model': 'test-model'})
        event('response_item', {'type': 'function_call', 'name': 'collaboration.spawn_agent', 'arguments': '{"task_name":"review"}'})
        for _ in range(2):
            event('response_item', {'type': 'custom_tool_call', 'name': 'functions.exec', 'input': 'cat /skills/orca-cli/SKILL.md'})
        event('event_msg', {'type': 'task_complete', 'last_agent_message': 'done'})
        first = turn_snapshot(state)
        self.assertEqual(first['activity']['tools'], ['collaboration.spawn_agent', 'functions.exec'])
        self.assertEqual(first['activity']['agents'], ['review'])
        self.assertEqual(first['activity']['skill_references'], ['orca-cli'])
        self.assertNotIn('skills', first['activity'])
        event('event_msg', {'type': 'user_message', 'message': 'second'})
        self.assertNotIn('tools', state['activity'])
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'db')
            store.save_turns([first, turn_snapshot(state)])
            store.import_session(state, '/tmp/source', state['updated_at'])
            card = store.cards()[0]
            turns = store.history(card['id'])['items']
            saved = next(t for t in turns if t['prompt'] == 'first')
            self.assertEqual(saved['activity'], first['activity'])
            self.assertNotIn('tools', card['activity'])

    def test_claude_explicit_skill_and_agent(self):
        state = new_state('claude')
        def event(kind, message):
            consume(state, {'type': kind, 'timestamp': '2026-09-26T10:00:00Z', 'sessionId': 'session', 'cwd': '/tmp', 'message': message})
        event('user', {'content': 'question'})
        event('assistant', {'model': 'claude-test', 'content': [
            {'type': 'tool_use', 'name': 'Skill', 'input': {'skill': 'review'}},
            {'type': 'tool_use', 'name': 'Agent', 'input': {'subagent_type': 'Explore', 'prompt': 'private prompt'}},
            {'type': 'tool_use', 'name': 'Read', 'input': {'file_path': '/skills/example/SKILL.md'}}]})
        self.assertEqual(state['activity']['skills'], ['review'])
        self.assertEqual(state['activity']['agents'], ['Explore'])
        self.assertEqual(state['activity']['models'], ['claude-test'])
        self.assertEqual(state['activity']['skill_references'], ['example'])
        self.assertNotIn('private prompt', str(state['activity']))
