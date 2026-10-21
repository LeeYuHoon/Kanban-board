"""Read-only, incremental native Codex / Claude Code session collection.

One card per conversation with durable per-request history. No hooks, agent
configuration, auth files, or source transcripts are modified.
"""
import copy
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import catalog
from .linking import link_sessions
from .store import now

MAX_TEXT = 100000
READ_BUDGET = 8 * 1024 * 1024
LINE_CHUNK = 64 * 1024
STALE_SECONDS = 15 * 60
PARSER_VERSION = 5
# Codex code-mode `exec` scripts call the real tools as `tools.<name>(...)`.
INNER_TOOL = re.compile(r'tools(?:\.([A-Za-z_$][\w$]*)|\[\s*["\']([^"\']+)["\']\s*\])\s*\(')
NOT_TOOLS = {'push', 'reduce', 'map', 'filter', 'forEach', 'join', 'slice', 'then', 'catch'}


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, AttributeError):
        return None


def text_content(content):
    if isinstance(content, str):
        return content[:MAX_TEXT]
    if isinstance(content, list):
        return '\n'.join(item.get('text', '') for item in content if isinstance(item, dict)
                         and item.get('type') in ('text', 'input_text', 'output_text')
                         and isinstance(item.get('text'), str))[:MAX_TEXT]
    return ''


def user_text(text):
    text = re.sub(r'<(?:environment_context|user_instructions)>.*?</(?:environment_context|user_instructions)>',
                  '', text, flags=re.S).strip()
    if text.startswith(('# AGENTS.md instructions', '<local-command-', '<command-name>',
                        '<task-notification>', '<teammate-message>', '<system-reminder>')):
        return ''
    return text


def new_state(provider):
    return {'provider': provider, 'status': 'unknown', 'prompt': '', 'result': '',
            'candidate': '', 'usage': None, 'created_at': None, 'updated_at': None,
            '_version': PARSER_VERSION}


def turn_snapshot(state):
    if not state.get('turn_key') or not state.get('prompt') or state.get('skip'):
        return None
    return {key: copy.deepcopy(state.get(key)) for key in
            ('provider', 'session_id', 'turn_key', 'prompt', 'result', 'status', 'usage', 'activity',
             'started_at', 'updated_at', 'finished_at')}


def start(state, stamp, prompt=None, request_id=None):
    previous = turn_snapshot(state)
    if previous:
        if previous['status'] == 'running':
            previous['status'] = 'unknown'
        state.setdefault('_pending_history', []).append(previous)
    state.update(status='running', result='', candidate='', finished_at=None, started_at=stamp,
                 updated_at=stamp, usage=None, assistant_id=None, assistant_uuid=None,
                 turn_key=None, prompt='', activity={})
    if state.get('context_model'):
        remember(state, 'models', state['context_model'])
    if prompt:
        state['prompt'] = prompt
        # Timestamp + native ID distinguish identical questions asked twice.
        identity = str(request_id or state.get('native_turn_id') or '') + ':' + stamp + ':' + prompt
        state['turn_key'] = hashlib.sha256(identity.encode()).hexdigest()


def finish(state, stamp, status='done'):
    state.update(status=status, finished_at=stamp, updated_at=stamp)
    state['result'] = state.get('candidate', '')


def remember(state, category, value):
    if not isinstance(value, str) or not value.strip():
        return
    values = state.setdefault('activity', {}).setdefault(category, [])
    value = value.strip()[:160]
    if value not in values and len(values) < 100:
        values.append(value)


def agent_name(value):
    """'/root/local_infra' -> 'local_infra'; the root (parent) thread itself is not a sub-agent."""
    if not isinstance(value, str):
        return None
    name = value.strip().rstrip('/').rsplit('/', 1)[-1]
    return None if name in ('', 'root') else name


def tool_activity(state, name, arguments, namespace=None):
    """Keep names/evidence only, never raw tool arguments or reasoning."""
    if not isinstance(name, str):
        return
    if isinstance(namespace, str) and namespace:
        name = namespace + '.' + name
    inner = set()
    if name.rsplit('.', 1)[-1] == 'exec' and isinstance(arguments, str):
        for plain, quoted in INNER_TOOL.findall(arguments):
            tool = plain or quoted
            if tool and tool not in NOT_TOOLS:
                inner.add(tool)
    for tool in sorted(inner):
        # `server__tool` is Codex's namespaced form; keep mcp__ names as-is for MCP grouping.
        remember(state, 'tools', tool if tool.startswith('mcp__') else tool.replace('__', '.'))
    if not inner:
        remember(state, 'tools', name)
    if isinstance(arguments, str):
        try:
            data = json.loads(arguments)
        except (ValueError, TypeError):
            data = {}
    else:
        data = arguments
    if not isinstance(data, dict):
        data = {}
    short = name.rsplit('.', 1)[-1]
    if short in ('Agent', 'Task', 'spawn_agent'):
        remember(state, 'agents', data.get('subagent_type') or data.get('task_name') or short)
        remember(state, 'models', data.get('model'))
    # Messages to an already running sub-agent are evidence it worked on this prompt.
    if short in ('send_message', 'followup_task'):
        remember(state, 'agents', agent_name(data.get('target')))
    if short == 'SendMessage':
        target = agent_name(data.get('to') or data.get('recipient'))
        # Claude addresses async agents by an opaque id; show the name/type it was launched with.
        remember(state, 'agents', state.get('_agent_ids', {}).get(target, target))
    if short in ('Agent', 'Task') and isinstance(state.get('_tool_id'), str):
        calls = state.setdefault('_agent_calls', {})
        calls[state['_tool_id']] = data.get('name') or data.get('subagent_type') or short
        while len(calls) > 200:
            calls.pop(next(iter(calls)))
    if short == 'Skill':
        remember(state, 'skills', data.get('skill'))
    # A file read is evidence of a reference, not proof the skill was executed.
    if short in ('Read', 'read_file'):
        file = data.get('file_path') or data.get('path')
        if isinstance(file, str) and file.endswith('/SKILL.md'):
            remember(state, 'skill_references', Path(file).parent.name)
    if short in ('exec', 'exec_command', 'Bash', 'shell_command'):
        command = data.get('cmd') or data.get('command') or (arguments if isinstance(arguments, str) else '')
        if isinstance(command, str):
            for match in re.finditer(r'([A-Za-z0-9_.:-]+)/SKILL\.md', command):
                remember(state, 'skill_references', match.group(1))
            for match in re.finditer(r'orca skills get ([A-Za-z0-9_.:-]+)', command):
                remember(state, 'skill_references', match.group(1))


def consume(state, event):
    """Normalize transcript records; retain tool names but not payloads/thinking."""
    if not isinstance(event, dict):
        return
    kind = event.get('type')
    stamp = timestamp(event.get('timestamp'))
    if state['provider'] == 'codex':
        payload = event.get('payload')
        if not isinstance(payload, dict):
            return
        if kind == 'session_meta':
            source = payload.get('source')
            state['skip'] = isinstance(source, dict) or str(source).startswith(('subagent', 'sub_agent'))
            state['session_id'] = payload.get('id') or payload.get('session_id')
            state['cwd'] = payload.get('cwd')
            state['created_at'] = timestamp(payload.get('timestamp')) or stamp
            return
        if state.get('skip') or not stamp:
            return
        # A copied or replayed older record must not revert a newer turn.
        if state.get('updated_at') and stamp < state['updated_at']:
            return
        if kind == 'turn_context':
            if isinstance(payload.get('cwd'), str):
                state['cwd'] = payload['cwd']
            if isinstance(payload.get('model'), str):
                state['context_model'] = payload['model']
                remember(state, 'models', payload['model'])
        if kind == 'event_msg':
            event_kind = payload.get('type')
            if event_kind == 'task_started':
                start(state, stamp)
                state['native_turn_id'] = payload.get('turn_id')
            elif event_kind == 'user_message':
                prompt = user_text(str(payload.get('message', '')))
                if prompt and (prompt != state.get('prompt') or state['status'] != 'running'):
                    start(state, stamp, prompt)
            elif event_kind == 'task_complete':
                if isinstance(payload.get('last_agent_message'), str):
                    state['candidate'] = payload['last_agent_message'][:MAX_TEXT]
                finish(state, stamp)
            elif event_kind == 'turn_aborted':
                finish(state, stamp, 'interrupted')
            elif event_kind in ('turn_failed', 'task_failed'):
                finish(state, stamp, 'failed')
            elif event_kind == 'token_count':
                info = payload.get('info')
                if isinstance(info, dict) and isinstance(info.get('total_token_usage'), dict):
                    state['usage'] = {'scope': 'session_total', 'reported': info['total_token_usage']}
            elif event_kind == 'agent_message' and isinstance(payload.get('message'), str):
                state['candidate'] = payload['message'][:MAX_TEXT]
            elif event_kind == 'item_completed' and isinstance(payload.get('item'), dict):
                item = payload['item']
                if item.get('type') == 'SubAgentActivity':
                    remember(state, 'agents', agent_name(item.get('agent_path')))
        elif kind == 'response_item':
            if payload.get('type') in ('function_call', 'custom_tool_call'):
                tool_activity(state, payload.get('name'), payload.get('arguments', payload.get('input')),
                              payload.get('namespace'))
            if payload.get('type') == 'message':
                text = text_content(payload.get('content'))
                if payload.get('role') == 'user':
                    prompt = user_text(text)
                    if prompt and (prompt != state.get('prompt') or state['status'] != 'running'):
                        start(state, stamp, prompt)
                elif payload.get('role') == 'assistant' and payload.get('phase') == 'final_answer':
                    state['candidate'] = text
            if state['status'] == 'running':
                state['updated_at'] = stamp
        elif kind == 'token_usage_record':
            usage = payload.get('thread_token_usage')
            if isinstance(usage, dict):
                state['usage'] = {'scope': 'session_total', 'reported': usage}
    else:
        # Skill / agent / MCP descriptions Claude Code was shown; flushed to the catalog by _read.
        found = catalog.from_claude_event(event)
        if found:
            state.setdefault('_catalog', []).extend(found)
        if event.get('isSidechain'):
            return
        if isinstance(event.get('sessionId'), str):
            state['session_id'] = event['sessionId']
        if isinstance(event.get('cwd'), str):
            state['cwd'] = event['cwd']
        if not stamp or (state.get('updated_at') and stamp < state['updated_at']):
            return
        if state['created_at'] is None:
            state['created_at'] = stamp
        message = event.get('message')
        if kind == 'user' and isinstance(message, dict):
            content = message.get('content')
            text = text_content(content)
            if text.startswith('[Request interrupted by user'):
                finish(state, stamp, 'interrupted')
                return
            result = event.get('toolUseResult')
            if isinstance(result, dict) and isinstance(result.get('agentId'), str) and isinstance(content, list):
                for item in content:
                    label = state.get('_agent_calls', {}).get(item.get('tool_use_id')) if isinstance(item, dict) else None
                    if label:
                        ids = state.setdefault('_agent_ids', {})
                        ids[result['agentId']] = label
                        while len(ids) > 500:
                            ids.pop(next(iter(ids)))
            if event.get('isMeta') or event.get('isCompactSummary') or event.get('toolUseResult') is not None:
                return
            if isinstance(content, list) and any(isinstance(item, dict) and item.get('type') == 'tool_result' for item in content):
                if state['status'] == 'running':
                    state['updated_at'] = stamp
                return
            prompt = user_text(text)
            if prompt:
                if event.get('uuid') and event['uuid'] == state.get('user_uuid'):
                    return
                start(state, stamp, prompt, event.get('uuid'))
                state['user_uuid'] = event.get('uuid')
        elif kind == 'assistant' and isinstance(message, dict):
            remember(state, 'models', message.get('model'))
            for item in message.get('content', []) if isinstance(message.get('content'), list) else []:
                if isinstance(item, dict) and item.get('type') == 'tool_use':
                    state['_tool_id'] = item.get('id')
                    tool_activity(state, item.get('name'), item.get('input'))
                    state.pop('_tool_id', None)
            text = text_content(message.get('content'))
            message_id = message.get('id')
            if message_id != state.get('assistant_id'):
                state['candidate'] = ''
                state['assistant_id'] = message_id
            text_id = event.get('uuid') or text
            if text and text_id != state.get('assistant_uuid'):
                state['candidate'] = (state.get('candidate', '') + '\n' + text).strip()[:MAX_TEXT]
                state['assistant_uuid'] = text_id
            if isinstance(message.get('usage'), dict):
                state['usage'] = {'scope': 'latest_response', 'reported': message['usage']}
            state['updated_at'] = stamp
            reason = message.get('stop_reason')
            if event.get('isApiErrorMessage'):
                finish(state, stamp, 'failed')
            elif reason in ('end_turn', 'stop_sequence'):
                finish(state, stamp)
            elif reason == 'max_tokens':
                finish(state, stamp, 'interrupted')
            else:
                state['status'] = 'running'
                state['finished_at'] = None
        elif kind == 'system' and event.get('subtype') == 'turn_duration' and state.get('prompt'):
            finish(state, stamp)


def source_roots():
    """Explicit JSON configuration replaces defaults, useful for tests/custom homes."""
    configured = os.environ.get('KANBAN_SESSION_ROOTS')
    if configured is not None:
        values = json.loads(configured)
        if not isinstance(values, dict) or set(values) - {'codex', 'claude'}:
            raise ValueError('KANBAN_SESSION_ROOTS must map codex/claude to path arrays')
        result = []
        for provider, paths in values.items():
            if not isinstance(paths, list) or any(not isinstance(path, str) for path in paths):
                raise ValueError('Session roots must be arrays of paths')
            result.extend((provider, Path(path).expanduser()) for path in paths)
        return result
    home = Path.home()
    codex_homes = [home / '.codex']
    if os.environ.get('CODEX_HOME'):
        codex_homes.append(Path(os.environ['CODEX_HOME']).expanduser())
    accounts = home / 'Library/Application Support/orca/codex-accounts'
    if accounts.exists():
        codex_homes.extend(accounts.glob('*/home'))
    claude_homes = [home / '.claude']
    if os.environ.get('CLAUDE_CONFIG_DIR'):
        claude_homes.append(Path(os.environ['CLAUDE_CONFIG_DIR']).expanduser())
    claude_profiles = home / 'Library/Application Support/orca/claude-profiles'
    if claude_profiles.exists():
        claude_homes.extend(claude_profiles.glob('*/home'))
    return ([('codex', root / name) for root in codex_homes for name in ('sessions', 'archived_sessions')]
            + [('claude', root / 'projects') for root in claude_homes])


class Collector:
    def __init__(self, store, roots=None, interval=5):
        self.store = store
        self.roots = roots
        self.interval = interval
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.thread = None
        self.info = {'running': False, 'last_scan': None, 'files': 0, 'errors': 0, 'roots': []}
        self.skills_checked = 0

    def status(self):
        return dict(self.info, enabled=self.store.collection_enabled())

    def start(self):
        self.thread = threading.Thread(target=self._loop, name='kanban-collector', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=10)

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                self.scan()
            except Exception:
                # A bad/unavailable source must not kill the dashboard or collector loop.
                self.info = dict(self.info, running=False, errors=self.info['errors'] + 1)
            self.wake.wait(self.interval)
            self.wake.clear()

    def scan(self):
        if not self.store.collection_enabled() or not self.lock.acquire(blocking=False):
            return self.status()
        self.info = dict(self.info, running=True)
        errors = 0
        try:
            roots = self.roots if self.roots is not None else source_roots()
            paths = {}
            found_roots = []
            seen_roots = set()
            for provider, root in roots:
                root = Path(root).resolve()
                if not root.is_dir() or (provider, str(root)) in seen_roots:
                    continue
                seen_roots.add((provider, str(root)))
                found_roots.append({'provider': provider, 'path': str(root)})
                pattern = '**/*.jsonl' if provider == 'codex' else '*/*.jsonl'
                for path in root.glob(pattern):
                    if path.is_symlink():
                        continue
                    try:
                        stat = path.stat()
                        paths[str(path.resolve())] = (provider, stat)
                    except OSError:
                        errors += 1
            with self.store.connect() as db:
                cursors = {row['path']: dict(row) for row in db.execute('SELECT * FROM collection_files')}
            # Continue any known file that moved outside its original root.
            for path, cursor in cursors.items():
                if path in paths:
                    continue
                try:
                    saved = json.loads(cursor['state'])
                    if saved.get('provider') not in ('codex', 'claude') or Path(path).is_symlink():
                        continue
                    stat = Path(path).stat()
                    paths[path] = (saved['provider'], stat)
                except (OSError, ValueError, TypeError, KeyError):
                    continue
            self.info = dict(self.info, roots=found_roots, files=len(paths))
            for path, (provider, stat) in sorted(paths.items(), key=lambda pair: pair[1][1].st_mtime, reverse=True):
                if self.stop_event.is_set():
                    break
                try:
                    cursor = cursors.get(path)
                    inode = f'{stat.st_dev}:{stat.st_ino}'
                    if cursor and cursor['inode'] == inode and cursor['offset'] == stat.st_size:
                        saved = json.loads(cursor['state'])
                        if (saved.get('_version') == PARSER_VERSION
                                and saved.get('_mtime_ns') == stat.st_mtime_ns):
                            continue
                    self._read(Path(path), provider, stat, cursor)
                except (OSError, ValueError, TypeError, KeyError):
                    errors += 1
            try:
                link_sessions(self.store)
            except (OSError, ValueError, sqlite3.Error):
                errors += 1
            try:
                self._refresh_catalog(cursors)
            except (OSError, ValueError, sqlite3.Error):
                errors += 1
            stale_before = datetime.fromtimestamp(time.time() - STALE_SECONDS, timezone.utc).isoformat()
            with self.store.connect() as db:
                db.execute("""UPDATE cards SET status='unknown',status_note=?
                    WHERE source='session' AND status='running' AND source_updated_at<?""",
                    ('15분 이상 새 기록이 없습니다. 실행·종료 여부는 원래 터미널에서 확인하세요.', stale_before))
                issues = db.execute('SELECT COUNT(*) FROM collection_issues').fetchone()[0]
            self.info = dict(self.info, running=False, last_scan=now(), errors=errors + issues)
            return self.status()
        finally:
            self.info = dict(self.info, running=False)
            self.lock.release()

    def _refresh_catalog(self, cursors):
        if self.store.setting('catalog_version') != str(catalog.CATALOG_VERSION):
            # Once: descriptions from transcripts collected before the catalog existed.
            claude = [path for path, cursor in cursors.items() if json.loads(cursor['state']).get('provider') == 'claude']
            catalog.backfill(self.store, claude)
            self.store.set_setting('catalog_version', str(catalog.CATALOG_VERSION))
        if time.time() - self.skills_checked > 600:
            self.skills_checked = time.time()
            catalog.refresh_skill_files(self.store)

    def _read(self, path, provider, stat, cursor):
        inode = str(stat.st_dev) + ':' + str(stat.st_ino)
        state = new_state(provider)
        offset = 0
        if cursor and cursor['inode'] == inode and cursor['offset'] <= stat.st_size:
            saved = json.loads(cursor['state'])
            same_size_changed = (cursor['offset'] == stat.st_size
                                 and saved.get('_mtime_ns') not in (None, stat.st_mtime_ns))
            if saved.get('_version') == PARSER_VERSION and not same_size_changed:
                state = saved
                offset = cursor['offset']
        original = offset
        with path.open('rb') as stream:
            stream.seek(offset)
            while stream.tell() - original < READ_BUDGET:
                beginning = stream.tell()
                chunk = stream.readline(LINE_CHUNK)
                if not chunk:
                    break
                if chunk.endswith(b'\n'):
                    line = chunk
                else:
                    # Find the newline in bounded chunks. An unfinished giant line
                    # consumes constant memory and stays unread until complete.
                    while chunk and not chunk.endswith(b'\n'):
                        chunk = stream.readline(LINE_CHUNK)
                    if not chunk:
                        break
                    end = stream.tell()
                    stream.seek(beginning)
                    line = stream.read(end - beginning)
                offset = stream.tell()
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    self._record_issue(path, beginning, 'invalid JSONL', line)
                    continue
                if not isinstance(event, dict):
                    self._record_issue(path, beginning, 'JSONL event is not an object', line)
                    continue
                consume(state, event)
        catalog.save(self.store, state.pop('_catalog', []), 'transcript')
        turns = state.pop('_pending_history', [])
        current = turn_snapshot(state)
        if current:
            turns.append(current)
        if turns and (offset != original or cursor is None):
            self.store.save_turns(turns)
        if offset >= stat.st_size and not state.get('skip') and state.get('session_id') and state.get('updated_at'):
            record = copy.deepcopy(state)
            record.pop('_imported', None)
            record.pop('_mtime_ns', None)
            stamp = datetime.fromisoformat(record['updated_at']).timestamp()
            record['status_note'] = '최신 요청을 표시합니다. 이전 요청·답변은 아래 대화 이력에 보존됩니다.'
            if record['status'] == 'running' and time.time() - stamp > STALE_SECONDS:
                record['status'] = 'unknown'
                record['status_note'] = '15분 이상 새 기록이 없습니다. 실행·종료 여부는 원래 터미널에서 확인하세요.'
            previous = state.get('_imported')
            signature = hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            if previous != signature:
                # Persist the card before its cursor: a crash can replay bytes, never lose them.
                self.store.import_session(record, path, now())
                state['_imported'] = signature
        if offset != original or cursor is None or state.get('_mtime_ns') != stat.st_mtime_ns:
            state['_mtime_ns'] = stat.st_mtime_ns
            with self.store.connect() as db:
                db.execute('INSERT OR REPLACE INTO collection_files VALUES (?,?,?,?)',
                           (str(path), inode, offset, json.dumps(state, ensure_ascii=False)))

    def _record_issue(self, path, offset, reason, raw):
        # Keep the original bytes before advancing the cursor. Replays are idempotent.
        with self.store.connect() as db:
            db.execute('INSERT OR IGNORE INTO collection_issues VALUES (?,?,?,?)',
                       (str(path), offset, reason, raw))
