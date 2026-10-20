"""One-way Kanban -> Obsidian Markdown export.

Notes are written only inside <vault>/Kanban, one note per session. Each note
carries its identity (kanban_id) and a hash of its own content (kanban_hash).
A note whose hash no longer matches was edited by someone else: it is kept
byte-for-byte and newer content goes to a sibling "(Kanban 최신본)" copy.
Nothing in the vault is deleted because a card was archived or the vault was
disconnected. Only names, prompts and answers already shown in Kanban are
exported; raw tool arguments and reasoning are never collected in the first place.
"""
import errno
import fcntl
import hashlib
import json
import os
import re
import stat
import string
import tempfile
import threading
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from .store import now

FOLDER = 'Kanban'
INTERVAL = 15
RENDER_VERSION = 4
MODES = ('off', 'auto', 'selected')
MODE_LABELS = {'auto': '자동 저장', 'selected': '선택 저장'}
MERGED = '.kanban-merged'  # Hidden from Obsidian; notes folded into a parent note go here.
LOCK_NAME = '.kanban-sync.lock'
PROVIDERS = {'codex': 'Codex', 'claude': 'Claude Code'}
SHORT = {'codex': 'Codex', 'claude': 'Claude'}
LABELS = {'todo': '대기', 'running': '진행 중', 'done': '완료', 'failed': '실패',
          'interrupted': '중단', 'unknown': '상태 확인'}
UNSAFE_NAME = re.compile(r'[\\/:*?"<>|#^\[\]`%\x00-\x1f\x7f]')
MD_PUNCT = re.compile('([' + re.escape(string.punctuation) + '])')
RETRY_HINT = ('원인을 해결한 뒤 ‘지금 동기화’를 누르세요. 자동 동기화가 켜져 있으면 '
              f'{INTERVAL}초마다 다시 시도합니다. 칸반과 세션 수집은 계속 동작합니다.')


class SyncError(Exception):
    def __init__(self, message, hint=RETRY_HINT):
        super().__init__(message)
        self.hint = hint


class Changed(Exception):
    """The target file changed between inspection and replacement."""


# ---------- Markdown / YAML encoding ----------

def clean(text):
    # Lone surrogates cannot be written as UTF-8; NUL confuses editors.
    return re.sub('[\ud800-\udfff\x00]', '�', '' if text is None else str(text))


def yaml_string(value):
    """JSON strings are YAML double-quoted scalars; escape what YAML treats specially."""
    out = []
    for char in json.dumps(clean(value), ensure_ascii=False):
        code = ord(char)
        printable = (code in (9, 10, 13) or 0x20 <= code <= 0x7e or 0xa0 <= code <= 0xd7ff
                     or 0xe000 <= code <= 0xfffd or code >= 0x10000)
        out.append(char if printable and code not in (0x2028, 0x2029) else '\\u%04x' % code)
    return ''.join(out)


def yaml_list(values):
    return '[' + ', '.join(yaml_string(value) for value in values) + ']'


def inline(text, limit=None):
    """Single-line literal text: every ASCII punctuation mark is backslash-escaped."""
    text = ' '.join(clean(text).split())
    if limit and len(text) > limit:
        text = text[:limit].rstrip() + '…'
    return MD_PUNCT.sub(r'\\\1', text)


def code_span(text):
    text = ' '.join(clean(text).split())
    longest = max((len(run) for run in re.findall('`+', text)), default=0)
    fence = '`' * (longest + 1)
    pad = ' ' if text.startswith('`') or text.endswith('`') or not text.strip() else ''
    return fence + pad + text + pad + fence


def code_block(text):
    """A fence longer than any backtick run inside keeps the text verbatim."""
    text = clean(text)
    longest = max((len(run) for run in re.findall('`+', text)), default=0)
    fence = '`' * max(3, longest + 1)
    return fence + 'text\n' + text + ('' if text.endswith('\n') else '\n') + fence


def local_time(value, fmt='%Y-%m-%d %H:%M:%S'):
    try:
        return datetime.fromisoformat(value).astimezone().strftime(fmt)
    except (TypeError, ValueError):
        return ''


def local_iso(value):
    try:
        return datetime.fromisoformat(value).astimezone().isoformat(timespec='seconds')
    except (TypeError, ValueError):
        return ''


def title_text(prompt):
    text = re.sub(r'<image\b[^>]*>[\s\S]*?</image>', '', clean(prompt), flags=re.I)
    text = re.sub(r'\[Image #\d+\]', '', text, flags=re.I).strip()
    return ' '.join(text.split()) or '이미지 첨부 요청'


def safe_name(text, max_bytes=120, fallback='대화'):
    """File/folder component without separators, wiki-link syntax, or leading dots."""
    text = unicodedata.normalize('NFC', clean(text))
    text = ' '.join(UNSAFE_NAME.sub(' ', text).split()).strip(' .')
    encoded = text.encode('utf-8')[:max_bytes]
    text = encoded.decode('utf-8', errors='ignore').strip(' .')
    return text or fallback


def note_key(card):
    if card.get('source') == 'session' and card.get('source_key'):
        return card['source_key']
    return 'manual:' + card['id']


def exportable(card):
    # Agent-launched sessions are written inside the note of the session that started them.
    if card.get('root_id'):
        return False
    return card.get('source') == 'session' or (card.get('source') == 'manual' and card.get('status') != 'todo')


def board_of(card, boards):
    return card['home_board_id'] if card.get('home_board_id') in boards else card['board_id']


def project_folders(boards):
    names = {}
    for board in boards.values():
        names.setdefault(safe_name(board['name'], 80, 'project').casefold(), []).append(board)
    folders = {}
    for group in names.values():
        for board in group:
            name = safe_name(board['name'], 80, 'project')
            if len(group) > 1:  # Same display name, different directories.
                name += ' (' + hashlib.sha256(board['directory'].encode()).hexdigest()[:6] + ')'
            folders[board['id']] = name
    return folders


def activity_groups(activity, provider):
    activity = activity or {}
    mcp, tools = [], []
    for name in activity.get('tools') or []:
        match = re.search(r'(?:^|\.)mcp__(.+?)__(.+)$', name)
        if match:
            mcp.append(code_span(match.group(1)) + ' / ' + code_span(match.group(2)))
        else:
            tools.append(code_span(name))
    names = lambda key: [code_span(value) for value in activity.get(key) or []]
    return [
        ('스킬', names('skills')),
        ('스킬 파일 참조 (실행 여부 미확인)', names('skill_references')),
        ('MCP 서버 / 도구', mcp),
        ('실행 에이전트', [PROVIDERS.get(provider, provider)]),
        ('하위 에이전트', names('agents')),
        ('모델', names('models')),
        ('일반 도구', tools),
    ]


def manual_turn(card):
    return {'turn_key': 'manual-' + card['id'], 'prompt': card.get('prompt') or card.get('title') or '',
            'result': card.get('result') or '', 'status': card.get('status'),
            'started_at': card.get('started_at') or card.get('created_at'), 'updated_at': card.get('updated_at'),
            'finished_at': card.get('finished_at'), 'activity': card.get('activity') or {}}


def render(card, board, turns, role='note', original=None, launched=None, mode='auto'):
    """Deterministic note text (no sync timestamp) so unchanged sessions are not rewritten."""
    provider = card['provider']
    title = title_text(turns[0]['prompt'] if turns else card.get('prompt') or card.get('title'))
    merged = {}
    for turn in turns:
        for key, values in (turn.get('activity') or {}).items():
            bucket = merged.setdefault(key, [])
            bucket.extend(value for value in values if value not in bucket)
    servers = []
    for name in merged.get('tools', []):
        match = re.search(r'(?:^|\.)mcp__(.+?)__', name)
        if match and match.group(1) not in servers:
            servers.append(match.group(1))
    status = card.get('status')
    lines = [
        '---',
        'kanban_id: ' + yaml_string(note_key(card)),
        'kanban_role: "' + role + '"',
        'kanban_hash: ""',
        f'kanban_version: {RENDER_VERSION}',
        'title: ' + yaml_string(title[:200]),
        'project: ' + yaml_string(board['name']),
        'project_path: ' + yaml_string(board['directory']),
        'provider: ' + yaml_string(provider),
        'provider_label: ' + yaml_string(PROVIDERS.get(provider, provider)),
        'session_id: ' + yaml_string(card.get('session_id') or ''),
        'status: ' + yaml_string(status),
        'archived: ' + ('true' if card.get('archived') else 'false'),
        f'turn_count: {len(turns)}',
        f'session_turn_count: {max(card.get("turn_count") or 0, len(turns))}',
        'kanban_mode: ' + yaml_string(mode),
        'created: ' + yaml_string(local_iso(card.get('created_at'))),
        'updated: ' + yaml_string(local_iso(card.get('source_updated_at') or card.get('updated_at'))),
        'skills: ' + yaml_list(merged.get('skills', [])),
        'mcp_servers: ' + yaml_list(servers),
        'agents: ' + yaml_list([PROVIDERS.get(provider, provider)] + merged.get('agents', [])),
        'models: ' + yaml_list(merged.get('models', [])),
        'launch: ' + yaml_string(card.get('launch') or 'interactive'),
        f'child_sessions: {sum(len(items) for items in (launched or {}).values())}',
        'tags: ' + yaml_list(['kanban', 'kanban/' + provider]),
        '---',
        '',
        '# ' + inline(title, 140),
        '',
    ]
    if role == 'conflict-copy':
        lines += ['> [!warning] Kanban 최신본',
                  '> 원본 노트가 편집되어 Kanban이 덮어쓰지 않았습니다. 이 파일은 최신 내보내기 사본입니다.',
                  '> 원본: [[' + original[:-3] + ']]', '']
    else:
        lines += ['> [!info] Kanban 자동 내보내기',
                  '> Kanban이 생성·갱신하는 읽기용 노트입니다. 이 파일을 편집하면 Kanban은 편집본을 '
                  '덮어쓰지 않고 최신 내용을 별도 사본에 기록합니다.', '']
    lines += [
        '- 프로젝트: ' + inline(board['name']) + ' · ' + code_span(board['directory']),
        '- 도구: ' + PROVIDERS.get(provider, provider),
        '- 세션 ID: ' + (code_span(card['session_id']) if card.get('session_id') else '기록 없음'),
        '- 상태: ' + LABELS.get(status, status or '기록 없음') + (' · 보관됨' if card.get('archived') else ''),
        (f'- 프롬프트: {len(turns)}개' if mode != 'selected' else
         f'- 프롬프트: 선택한 {len(turns)}개 (세션 전체 {max(card.get("turn_count") or 0, len(turns))}개)'),
        '- 생성: ' + (local_time(card.get('created_at')) or '기록 없음')
        + ' · 갱신: ' + (local_time(card.get('source_updated_at') or card.get('updated_at')) or '기록 없음'),
        '',
    ]
    if not turns:
        lines += ['기록된 프롬프트가 없습니다.', '']
    for number, turn in enumerate(turns, 1):
        number = turn.get('number', number)  # Original position, also when only picked prompts are saved.
        label = LABELS.get(turn.get('status'), turn.get('status') or '기록 없음')
        lines += [
            f'## 프롬프트 {number} · ' + (local_time(turn.get('started_at'), '%Y-%m-%d %H:%M') or '시각 기록 없음') + ' · ' + label,
            '',
            '<!-- kanban-turn: ' + re.sub(r'[^A-Za-z0-9_-]', '', turn['turn_key']) + ' -->',
            '',
            '- 세션 ID: ' + (code_span(card['session_id']) if card.get('session_id') else '기록 없음'),
            '- 요청 ID: ' + code_span(turn['turn_key'][:12]),
            '- 시각: ' + (local_time(turn.get('started_at')) or '기록 없음')
            + (' → ' + local_time(turn['finished_at']) if turn.get('finished_at') else ''),
            '- 상태: ' + label,
            '',
            '### 요청',
            '',
            code_block(turn.get('prompt') or ''),
            '',
            '### 답변',
            '',
        ]
        if turn.get('result'):
            lines.append(code_block(turn['result']))
        elif turn.get('status') == 'running':
            lines.append('_답변을 작성하고 있습니다. 완료되면 다음 동기화에 반영됩니다._')
        else:
            lines.append('_이 요청에 대해 기록된 최종 답변이 없습니다._')
        lines += ['', '### 스킬 / MCP / 에이전트', '']
        for name, values in activity_groups(turn.get('activity'), provider):
            lines.append(f'- {name}: ' + (', '.join(values) if values else '확인된 기록 없음'))
        lines.append('')
        lines += render_children((launched or {}).get(turn['turn_key']), card)
    if (launched or {}).get(None):
        lines += ['## 시작한 프롬프트를 특정하지 못한 하위 세션', ''] + render_children(launched[None], card)
    lines.append('<!-- 로그에 기록된 호출만 표시합니다. 원시 도구 인자와 추론 내용은 포함하지 않습니다. -->')
    text = '\n'.join(lines) + '\n'
    digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
    return text.replace('kanban_hash: ""', f'kanban_hash: "{digest}"', 1).encode('utf-8')


def render_children(children, root):
    """Agent-launched sessions under the prompt that started them; full text stays searchable."""
    if not children:
        return []
    lines = [f'### 에이전트가 실행한 하위 세션 ({len(children)})', '']
    for number, (child, turns) in enumerate(children, 1):
        provider = child['provider']
        lines += [f'#### 하위 세션 {number} · ' + PROVIDERS.get(provider, provider) + ' · '
                  + LABELS.get(child.get('status'), child.get('status') or '기록 없음') + ' · '
                  + (local_time(child.get('created_at'), '%Y-%m-%d %H:%M') or '시각 기록 없음'), '',
                  '- 세션 ID: ' + (code_span(child['session_id']) if child.get('session_id') else '기록 없음')]
        if child.get('parent_id') != root['id']:
            lines.append('- 다른 하위 세션이 다시 실행한 세션입니다.')
        lines.append('')
        for index, turn in enumerate(turns, 1):
            if len(turns) > 1:
                lines += [f'##### 요청 {index}', '']
            lines += ['**요청**', '', code_block(turn.get('prompt') or ''), '', '**답변**', '']
            if turn.get('result'):
                lines.append(code_block(turn['result']))
            elif turn.get('status') == 'running':
                lines.append('_답변을 작성하고 있습니다._')
            else:
                lines.append('_기록된 최종 답변이 없습니다._')
            lines.append('')
            for name, values in activity_groups(turn.get('activity'), provider):
                if name != '실행 에이전트':
                    lines.append(f'- {name}: ' + (', '.join(values) if values else '확인된 기록 없음'))
            lines.append('')
    return lines


# ---------- Ownership inspection ----------

def frontmatter(text):
    if not text.startswith('---\n'):
        return None
    end = text.find('\n---\n', 4)
    return text[4:end] if end != -1 else None


def ownership(data):
    """Returns (kanban_id, role, unmodified) or (None, None, False) for foreign files."""
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        return None, None, False
    meta = frontmatter(text)
    if meta is None:
        return None, None, False
    identity = re.search(r'^(?:kanban|kankan)_id: (".*")$', meta, re.M)
    role = re.search(r'^(?:kanban|kankan)_role: "([a-z-]+)"$', meta, re.M)
    if not identity or not role:
        return None, None, False
    try:
        identity = json.loads(identity.group(1))
    except ValueError:
        return None, None, False
    digest = re.search(r'^(kanban|kankan)_hash: "([0-9a-f]{64})"$', meta, re.M)
    unmodified = False
    if digest:
        field = digest.group(1) + '_hash'
        original = text.replace(f'{field}: "{digest.group(2)}"', f'{field}: ""', 1)
        unmodified = hashlib.sha256(original.encode('utf-8')).hexdigest() == digest.group(2)
    return identity, role.group(1), unmodified


def read_regular(path, limit=None):
    """Read without following symlinks. None if missing; raises Changed for non-files."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise Changed()
        raise
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise Changed()
        return stream.read(limit) if limit else stream.read()


def inspect(path, key, role):
    """'missing' | 'clean' (ours, unmodified) | 'edited' (ours, modified) | 'foreign'."""
    try:
        data = read_regular(path)
    except Changed:
        return 'foreign', None
    if data is None:
        return ('foreign', None) if os.path.lexists(path) else ('missing', None)
    identity, found_role, unmodified = ownership(data)
    if identity != key or found_role != role:
        return 'foreign', data
    return ('clean' if unmodified else 'edited'), data


# ---------- Safe file operations ----------

def fsync_dir(directory):
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def temp_file(directory, data):
    fd, temp = tempfile.mkstemp(prefix='.kanban-', suffix='.tmp', dir=directory)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, 0o644)
    except BaseException:
        os.unlink(temp)
        raise
    return temp


def create_file(path, data):
    """Atomic and never clobbers: the hard link fails if anything appeared at path."""
    directory = os.path.dirname(path)
    temp = temp_file(directory, data)
    try:
        try:
            os.link(temp, path)
        except FileExistsError:
            return False
        except OSError as exc:
            if exc.errno not in (errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EMLINK, errno.EXDEV):
                raise
            # Filesystems without hard links: exclusive create, still no overwrite.
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
            except FileExistsError:
                return False
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        fsync_dir(directory)
        return True
    finally:
        os.unlink(temp)


def replace_file(path, data, expected):
    """Atomically replace a Kanban-owned file only if it still holds `expected` bytes."""
    directory = os.path.dirname(path)
    temp = temp_file(directory, data)
    try:
        if read_regular(path) != expected:
            raise Changed()
        os.replace(temp, path)
        temp = None
        fsync_dir(directory)
    finally:
        if temp:
            os.unlink(temp)


def move_no_clobber(source, target):
    try:
        os.link(source, target)
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            return False
        if os.path.lexists(target):
            return False
        os.rename(source, target)
        return True
    os.unlink(source)
    return True


def safe_rel(rel):
    if not isinstance(rel, str) or '\x00' in rel or '\\' in rel or not rel.endswith('.md'):
        return False
    parts = rel.split('/')
    return len(parts) >= 2 and parts[0] == FOLDER and all(part not in ('', '.', '..') for part in parts)


def ensure_parent(vault, rel):
    """Create folders below the vault, refusing symlinks or files on the way."""
    current = vault
    for part in rel.split('/')[:-1]:
        current = os.path.join(current, part)
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            try:
                os.mkdir(current, 0o755)
            except FileExistsError:
                pass
            info = os.lstat(current)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise SyncError(f'Kanban 폴더 안에 심볼릭 링크 또는 파일이 있어 쓰지 않았습니다: {os.path.relpath(current, vault)}',
                            '해당 항목을 일반 폴더로 바꾸거나 다른 곳으로 옮긴 뒤 다시 동기화하세요. Kanban은 링크를 따라가지 않습니다.')
    return current


def candidate(rel, label, number):
    """'name.md' -> 'name 2.md', 'name (label).md', 'name (label 2).md'."""
    parts = [rel[:-3]]
    if label:
        parts.append(label if number == 1 else f'{label[:-1]} {number})')
    elif number > 1:
        parts.append(str(number))
    return ' '.join(parts) + '.md'


def describe(exc):
    code = getattr(exc, 'errno', None)
    where = getattr(exc, 'filename', None) or ''
    if os.path.basename(str(where)).startswith('.kanban-'):
        where = os.path.dirname(where)  # Name the folder, not Kanban's hidden temp file.
    if code in (errno.EACCES, errno.EPERM):
        return f'쓰기 권한이 없습니다: {where}'
    if code == errno.EROFS:
        return f'읽기 전용 위치입니다: {where}'
    if code == errno.ENOSPC:
        return '디스크 공간이 부족합니다.'
    if code == errno.ENOENT:
        return f'경로를 찾을 수 없습니다: {where}'
    return str(exc)


def validate_vault(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Obsidian Vault 폴더의 절대 경로를 입력하세요.')
    raw = os.path.expanduser(value.strip())
    if not os.path.isabs(raw):
        raise ValueError('Vault 경로는 절대 경로로 입력하세요. 예: /Users/name/Documents/MyVault')
    if not os.path.exists(raw):
        raise ValueError('해당 경로가 없습니다. Obsidian Vault 폴더의 위치를 확인하세요.')
    real = os.path.realpath(raw)
    if not os.path.isdir(real):
        raise ValueError('폴더가 아닙니다. Obsidian Vault 폴더를 지정하세요.')
    if not os.path.isdir(os.path.join(real, '.obsidian')):
        raise ValueError('Obsidian Vault가 아닙니다(.obsidian 폴더 없음). Obsidian에서 Vault로 연 폴더를 지정하세요.')
    if not os.access(real, os.W_OK | os.X_OK):
        raise ValueError('Vault 폴더에 쓰기 권한이 없습니다. 폴더 권한을 확인하세요.')
    folder = os.path.join(real, FOLDER)
    if os.path.lexists(folder):
        info = os.lstat(folder)
        if stat.S_ISLNK(info.st_mode):
            raise ValueError(f'Vault의 {FOLDER} 항목이 심볼릭 링크입니다. 안전을 위해 링크를 따라 쓰지 않습니다.')
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError(f'Vault에 {FOLDER}라는 파일이 이미 있습니다. 이름을 바꾼 뒤 다시 시도하세요.')
        if not os.access(folder, os.W_OK | os.X_OK):
            raise ValueError(f'Vault의 {FOLDER} 폴더에 쓰기 권한이 없습니다.')
    return real


# ---------- Synchronizer ----------

class ObsidianSync:
    """Per-project export. 'auto' saves every prompt; 'selected' saves only prompts the user picked."""

    def __init__(self, store, interval=INTERVAL):
        self.store = store
        self.interval = interval
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.stop_event = threading.Event()
        self.manual = False
        self.running = False
        self.thread = None

    # Settings survive restarts in the obsidian_projects table.
    def configs(self):
        with self.store.connect() as db:
            rows = db.execute('SELECT * FROM obsidian_projects').fetchall()
        configs = {}
        for row in rows:
            value = dict(row)
            try:
                value['status'] = json.loads(value['status'] or '{}')
            except ValueError:
                value['status'] = {}
            configs[row['board_id']] = value
        return configs

    def active(self):
        return {key: value for key, value in self.configs().items() if value['mode'] in ('auto', 'selected') and value['vault']}

    def default_vault(self):
        """Last vault used (also the pre-per-project global vault) to prefill new projects."""
        value = self.store.setting('obsidian_vault')
        if not value:
            try:
                value = json.loads(self.store.setting('obsidian_status') or '{}').get('vault')
            except ValueError:
                value = None
        return value if value and os.path.isdir(value) else None

    def picks(self):
        with self.store.connect() as db:
            return {(row['note_key'], row['turn_key']) for row in db.execute('SELECT note_key,turn_key FROM obsidian_picks')}

    def status(self):
        boards = {board['id']: board for board in self.store.boards()}
        with self.store.connect() as db:
            rows = db.execute("""SELECT vault,source_key,path,conflict_path FROM obsidian_notes
                WHERE COALESCE(signature,'') NOT IN ('merged','merged-kept')""").fetchall()
        owners = {}
        for card in self.store.cards():
            if exportable(card):
                owners[note_key(card)] = board_of(card, boards)
        projects = []
        for board_id, config in self.configs().items():
            if board_id not in boards or config['mode'] == 'off':
                continue
            saved = config['status'] if config['status'].get('vault') == config['vault'] else {}
            mine = [row for row in rows if row['vault'] == config['vault'] and owners.get(row['source_key']) == board_id]
            projects.append({
                'board_id': board_id, 'name': boards[board_id]['name'], 'mode': config['mode'],
                'mode_label': MODE_LABELS[config['mode']], 'vault': config['vault'],
                'vault_name': os.path.basename(config['vault'] or ''), 'notes': len(mine),
                'conflicts': [{'key': row['source_key'], 'path': row['path'], 'conflict_path': row['conflict_path'],
                               'project': boards[board_id]['name'], 'board_id': board_id} for row in mine if row['conflict_path']],
                **{key: saved.get(key) for key in ('last_attempt_at', 'last_finished_at', 'last_success_at', 'result', 'error', 'hint')}})
        projects.sort(key=lambda item: item['name'])
        failing = [item for item in projects if item['error']]
        finished = [item['last_finished_at'] for item in projects if item['last_finished_at']]
        succeeded = [item['last_success_at'] for item in projects if item['last_success_at']]
        return {'available': True, 'folder': FOLDER, 'interval': self.interval, 'running': self.running,
                'queued': self.manual, 'default_vault': self.default_vault(), 'projects': projects,
                'configs': {key: {'mode': value['mode'], 'vault': value['vault']} for key, value in self.configs().items()},
                'connected': len(projects), 'notes': sum(item['notes'] for item in projects),
                'conflicts': [conflict for item in projects for conflict in item['conflicts']],
                'error': failing[0]['name'] + ': ' + failing[0]['error'] if failing else None,
                'hint': failing[0]['hint'] if failing else None,
                'last_finished_at': max(finished) if finished else None,
                'last_success_at': max(succeeded) if succeeded else None,
                'result': {key: sum((item['result'] or {}).get(key, 0) for item in projects)
                           for key in ('created', 'updated', 'unchanged', 'skipped', 'conflicts', 'errors', 'merged', 'merged_kept', 'pruned')}}

    def links(self, cards):
        """card id -> note location for the 'Open in Obsidian' button (top-level cards only)."""
        boards = {board['id'] for board in self.store.boards()}
        configs = self.active()
        with self.store.connect() as db:
            rows = {(row['vault'], row['source_key']): row for row in db.execute(
                """SELECT vault,source_key,path,conflict_path FROM obsidian_notes
                WHERE COALESCE(signature,'') NOT IN ('merged','merged-kept')""")}
        links = {}
        for card in cards:
            config = configs.get(board_of(card, boards))
            row = config and rows.get((config['vault'], note_key(card)))
            if row:
                links[card['id']] = {'path': row['path'], 'conflict_path': row['conflict_path'], 'mode': config['mode'],
                                     'url': 'obsidian://open?path=' + quote(os.path.join(config['vault'], row['path']), safe='')}
        return links

    def configure_project(self, board_id, mode, vault=None):
        """mode: 'off' | 'auto' | 'selected'. Turning a project off keeps its notes and vault path."""
        self.store.board(board_id)
        if mode not in MODES:
            raise ValueError('연동 방식은 연동 안 함 / 자동 저장 / 선택 저장 중에서 고르세요.')
        current = self.configs().get(board_id) or {}
        real = current.get('vault')
        if mode != 'off' or vault:
            real = validate_vault(vault if vault else (current.get('vault') or self.default_vault()))
            self.store.set_setting('obsidian_vault', real)
        with self.store.connect() as db:
            db.execute('''INSERT INTO obsidian_projects (board_id,vault,mode,status,updated_at) VALUES (?,?,?,NULL,?)
                ON CONFLICT(board_id) DO UPDATE SET vault=excluded.vault,mode=excluded.mode,updated_at=excluded.updated_at''',
                       (board_id, real, mode, now()))
        if mode != 'off':
            self.request_sync()
        return self.status()

    def pick(self, card_id, turn_key, picked):
        """Choose a prompt for a project in 'selected' mode; agent runs it launched come along."""
        if not isinstance(picked, bool) or not isinstance(turn_key, str):
            raise ValueError('선택 정보가 올바르지 않습니다.')
        card = self.store.card(card_id)
        if card.get('parent_id'):
            raise ValueError('에이전트가 실행한 하위 세션은 상위 세션의 프롬프트를 선택하면 함께 저장됩니다.')
        if turn_key not in {turn['turn_key'] for turn in self._turns(card)}:
            raise ValueError('프롬프트를 찾을 수 없습니다.')
        with self.store.connect() as db:
            if picked:
                db.execute('INSERT OR IGNORE INTO obsidian_picks VALUES (?,?,?)', (note_key(card), turn_key, now()))
            else:
                db.execute('DELETE FROM obsidian_picks WHERE note_key=? AND turn_key=?', (note_key(card), turn_key))
        self.manual = True
        self.wake.set()
        return {'card_id': card_id, 'turn_key': turn_key, 'picked': picked}

    def request_sync(self):
        if not self.active():
            raise ValueError('먼저 프로젝트의 Obsidian 연동 방식을 정하세요.')
        self.manual = True
        self.wake.set()
        return self.status()

    def start(self):
        self.thread = threading.Thread(target=self._loop, name='kanban-obsidian', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=10)

    def _loop(self):
        while not self.stop_event.is_set():
            self.wake.wait(self.interval)
            self.wake.clear()
            if self.stop_event.is_set():
                break
            manual, self.manual = self.manual, False
            # Both modes stay current: new prompts (auto) and later answers to picked prompts (selected).
            if manual or self.active():
                try:
                    self.sync()
                except Exception:  # sync() records its own failures; never kill the thread.
                    pass

    @contextmanager
    def _vault_lock(self, root):
        """Serializes Kanban servers that share one vault."""
        fd = os.open(os.path.join(root, LOCK_NAME), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o644)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SyncError('다른 Kanban 프로세스가 같은 Vault를 동기화하고 있습니다.',
                                '잠시 후 다시 동기화하세요.')
            yield
        finally:
            os.close(fd)

    def _check_vault(self, vault):
        if not vault:
            raise SyncError('Obsidian Vault가 설정되지 않았습니다.', '프로젝트 설정에서 Vault 폴더를 지정하세요.')
        if not os.path.isdir(vault):
            raise SyncError('Vault 폴더를 찾을 수 없습니다: ' + vault,
                            '외장 디스크·클라우드 폴더 연결을 확인하거나 프로젝트 설정에서 경로를 다시 지정한 뒤 동기화하세요.')
        if not os.path.isdir(os.path.join(vault, '.obsidian')):
            raise SyncError('Obsidian Vault 표시(.obsidian 폴더)가 없습니다: ' + vault,
                            'Obsidian에서 해당 폴더를 Vault로 열었는지 확인하거나 Vault를 다시 지정하세요.')
        self._migrate_legacy_folder(vault)
        return ensure_parent(vault, FOLDER + '/x.md')

    def _migrate_legacy_folder(self, vault):
        legacy = 'Kankan'
        with self.store.connect() as db:
            rows = db.execute('SELECT source_key,path,conflict_path FROM obsidian_notes WHERE vault=? '
                              'AND (path LIKE ? OR conflict_path LIKE ?)',
                              (vault, legacy + '/%', legacy + '/%')).fetchall()
            if not rows:
                return
            old, new = os.path.join(vault, legacy), os.path.join(vault, FOLDER)
            if os.path.lexists(old):
                if os.path.islink(old) or not os.path.isdir(old):
                    raise SyncError('기존 Obsidian 노트 폴더를 안전하게 옮길 수 없습니다: ' + old)
                if os.path.lexists(new):
                    raise SyncError('기존 노트 폴더와 새 노트 폴더가 모두 있어 자동 이전을 중단했습니다: ' + vault)
                os.rename(old, new)
            elif not os.path.isdir(new) or os.path.islink(new):
                raise SyncError('기존 Obsidian 노트 폴더를 찾을 수 없습니다: ' + old)
            for row in rows:
                path, conflict = row['path'], row['conflict_path']
                if path and path.startswith(legacy + '/'):
                    path = FOLDER + path[len(legacy):]
                if conflict and conflict.startswith(legacy + '/'):
                    conflict = FOLDER + conflict[len(legacy):]
                db.execute('UPDATE obsidian_notes SET path=?,conflict_path=? WHERE vault=? AND source_key=?',
                           (path, conflict, vault, row['source_key']))

    def sync(self):
        """Export every connected project. Returns the status; never raises for vault problems."""
        if not self.lock.acquire(blocking=False):
            return dict(self.status(), busy=True)
        self.running = True
        try:
            configs = self.active()
            context = self._context()
            by_vault = {}
            for config in configs.values():
                by_vault.setdefault(config['vault'], []).append(config)
            for vault, group in by_vault.items():
                started = now()
                results = {config['board_id']: dict.fromkeys(
                    ('created', 'updated', 'unchanged', 'skipped', 'conflicts', 'errors', 'total'), 0) for config in group}
                failures = {config['board_id']: [] for config in group}
                error = hint = None
                try:
                    root = self._check_vault(vault)
                    with self._vault_lock(root):
                        self._fold_merged(vault, context['everything'], results[group[0]['board_id']])
                        for config in group:
                            self._export(vault, config, context, results[config['board_id']], failures[config['board_id']])
                except SyncError as exc:
                    error, hint = str(exc), exc.hint
                except OSError as exc:
                    error, hint = describe(exc), RETRY_HINT
                except Exception as exc:  # Unexpected: report, but never break collection or the board.
                    error, hint = f'예상하지 못한 오류: {exc}', RETRY_HINT
                for config in group:
                    self._record(config, started, results[config['board_id']], failures[config['board_id']], error, hint)
        finally:
            self.running = False
            self.lock.release()
        return self.status()

    def _record(self, config, started, result, failures, error, hint):
        if not error and failures:
            error, hint = f'노트 {len(failures)}개를 쓰지 못했습니다. 첫 오류: {failures[0]}', RETRY_HINT
        previous = config['status'] if config['status'].get('vault') == config['vault'] else {}
        finished = now()
        record = {'vault': config['vault'], 'last_attempt_at': started, 'last_finished_at': finished,
                  'last_success_at': finished if not error else previous.get('last_success_at'),
                  'result': result, 'error': error, 'hint': hint}
        with self.store.connect() as db:
            db.execute('UPDATE obsidian_projects SET status=? WHERE board_id=?',
                       (json.dumps(record, ensure_ascii=False), config['board_id']))

    def _context(self):
        boards = {board['id']: board for board in self.store.boards()}
        everything = self.store.cards()
        family = {}
        for item in everything:
            if item.get('root_id'):
                family.setdefault(item['root_id'], []).append(item)
        stale = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
        with self.store.connect() as db:
            aggregates = {(row[0], row[1]): tuple(row[2:]) for row in db.execute(
                "SELECT provider,session_id,COUNT(*),MAX(updated_at),SUM(status='running' AND updated_at<?) "
                'FROM session_turns GROUP BY provider,session_id', (stale,))}
        return {'boards': boards, 'folders': project_folders(boards), 'everything': everything,
                'family': family, 'aggregates': aggregates, 'picks': self.picks()}

    def _export(self, vault, config, context, result, failures):
        boards, folders, aggregates = context['boards'], context['folders'], context['aggregates']
        board = boards[config['board_id']]
        selected = config['mode'] == 'selected'
        cards = [card for card in context['everything'] if exportable(card) and board_of(card, boards) == board['id']]
        with self.store.connect() as db:
            records = {row['source_key']: dict(row) for row in db.execute(
                'SELECT * FROM obsidian_notes WHERE vault=?', (vault,))}
        self._prune_empty(vault, [folders[board['id']]], result)
        index = {}
        for card in cards:
            if self.stop_event.is_set():
                break
            result['total'] += 1
            key = note_key(card)
            chosen = sorted(turn for note, turn in context['picks'] if note == key) if selected else None
            if selected and not chosen:
                result['skipped'] += 1  # Nothing picked: no note; an existing note is left as it is.
                continue
            light = {name: value for name, value in card.items() if name not in ('prompt', 'result', 'usage')}
            kids = sorted(context['family'].get(card['id'], []), key=lambda item: item['created_at'])
            signature = hashlib.sha256(json.dumps(
                [RENDER_VERSION, config['mode'], chosen, light, board, folders[board['id']],
                 aggregates.get((card['provider'], card.get('session_id'))),
                 [[kid['id'], kid['updated_at'], kid.get('parent_id'), kid.get('parent_turn_key'),
                   aggregates.get((kid['provider'], kid.get('session_id')))] for kid in kids]],
                sort_keys=True, default=str).encode()).hexdigest()
            record = records.get(key)
            try:
                if record and record['signature'] == signature and self._untouched(vault, record):
                    result['conflicts' if record['conflict_path'] else 'unchanged'] += 1
                    continue
                turns, launched = self._content(card, kids, context['everything'], chosen)
                outcome = self._write(vault, card, board, folders[board['id']], record, index, launched, turns, config['mode'])
                result[outcome['outcome']] += 1
                self._save(vault, key, signature, outcome)
            except (OSError, SyncError, Changed) as exc:
                result['errors'] += 1
                failures.append(describe(exc) if isinstance(exc, OSError) else str(exc) or '파일이 쓰는 중에 바뀌었습니다.')

    def _content(self, card, kids, everything, chosen=None):
        """Turns to write (original numbering kept) and the agent runs each one launched."""
        turns = self._turns(card)
        for number, turn in enumerate(turns, 1):
            turn['number'] = number
        launched = self._launched(card, kids, everything)
        if chosen is not None:
            keep = set(chosen)
            turns = [turn for turn in turns if turn['turn_key'] in keep]
            launched = {key: value for key, value in launched.items() if key in keep}
        return turns, launched

    def _launched(self, card, kids, everything):
        """Map each top-level turn_key to the agent-launched sessions it started (None: unknown turn)."""
        by_id = {item['id']: item for item in everything}
        turn_keys = {turn['turn_key'] for turn in self._turns(card)}
        grouped = {}
        for kid in kids:
            node = kid
            for _ in range(20):
                if node.get('parent_id') == card['id'] or node.get('parent_id') not in by_id:
                    break
                node = by_id[node['parent_id']]
            key = node.get('parent_turn_key') if node.get('parent_turn_key') in turn_keys else None
            grouped.setdefault(key, []).append((kid, self._turns(kid)))
        return grouped

    def _fold_merged(self, vault, everything, result):
        """Notes of sessions now shown inside a parent note move to a hidden folder; edited ones stay."""
        children = {note_key(card) for card in everything if card.get('root_id')}
        with self.store.connect() as db:
            records = {row['source_key']: dict(row) for row in db.execute(
                'SELECT * FROM obsidian_notes WHERE vault=?', (vault,))}
        for key, record in records.items():
            if key not in children or record['signature'] in ('merged', 'merged-kept') or not safe_rel(record['path']):
                continue
            path = os.path.join(vault, record['path'])
            state, _ = inspect(path, key, 'note')
            signature, target = 'merged-kept', record['path']
            try:
                if state == 'missing':
                    signature = 'merged'
                elif state == 'clean':
                    base = f'{FOLDER}/{MERGED}/' + record['path'][len(FOLDER) + 1:]
                    for number in range(1, 30):
                        rel = candidate(base, '', number)
                        ensure_parent(vault, rel)
                        if move_no_clobber(path, os.path.join(vault, rel)):
                            signature, target = 'merged', rel
                            result['merged'] = result.get('merged', 0) + 1
                            break
                else:
                    result['merged_kept'] = result.get('merged_kept', 0) + 1
            except (OSError, SyncError):
                continue
            with self.store.connect() as db:
                db.execute('UPDATE obsidian_notes SET path=?,signature=?,conflict_path=NULL WHERE vault=? AND source_key=?',
                           (target, signature, vault, key))
            record.update(path=target, signature=signature, conflict_path=None)

    @staticmethod
    def _prune_empty(vault, folders, result):
        """Remove project folders Kanban generates once they are completely empty (rmdir only)."""
        for name in set(folders):
            path = os.path.join(vault, FOLDER, name)
            try:
                if stat.S_ISDIR(os.lstat(path).st_mode) and not os.listdir(path):
                    os.rmdir(path)
                    result['pruned'] = result.get('pruned', 0) + 1
            except OSError:
                continue

    def _untouched(self, vault, record):
        if not safe_rel(record['path']):
            return False
        try:
            info = os.lstat(os.path.join(vault, record['path']))
        except OSError:
            return False
        return stat.S_ISREG(info.st_mode) and info.st_mtime_ns == record['mtime_ns'] and info.st_size == record['size']

    def _save(self, vault, key, signature, outcome):
        with self.store.connect() as db:
            db.execute('INSERT OR REPLACE INTO obsidian_notes VALUES (?,?,?,?,?,?,?,?)',
                       (vault, key, outcome['path'], signature, outcome['mtime_ns'], outcome['size'],
                        outcome['conflict_path'], now()))

    def _turns(self, card):
        turns = self.store.all_turns(card['provider'], card['session_id']) if card.get('session_id') else []
        return turns or ([manual_turn(card)] if card.get('prompt') or card.get('title') else [])

    def _locate(self, vault, key, index):
        """Find a moved note (by kanban_id) inside the Kanban folder; built lazily once per run."""
        if 'built' not in index:
            index['built'] = True
            root = os.path.join(vault, FOLDER)
            for directory, subdirs, files in os.walk(root, followlinks=False):
                subdirs[:] = [name for name in subdirs if not name.startswith('.')]
                for name in files:
                    # Backups made by "resolve" keep the user's edit and are never adopted again.
                    if not name.endswith('.md') or name.startswith('.') or '(편집본 ' in name:
                        continue
                    path = os.path.join(directory, name)
                    try:
                        head = read_regular(path, 16384)
                    except (Changed, OSError):
                        continue
                    if not head:
                        continue
                    text = head.decode('utf-8', errors='ignore')
                    meta = frontmatter(text) or ''
                    identity = re.search(r'^(?:kanban|kankan)_id: (".*")$', meta, re.M)
                    if identity and re.search(r'^(?:kanban|kankan)_role: "note"$', meta, re.M):
                        try:
                            index.setdefault(json.loads(identity.group(1)), os.path.relpath(path, vault).replace(os.sep, '/'))
                        except ValueError:
                            pass
        return index.get(key)

    def _write(self, vault, card, board, folder, record, index, launched=None, turns=None, mode='auto'):
        key = note_key(card)
        turns = self._turns(card) if turns is None else turns
        previous = record['path'] if record and safe_rel(record['path']) else None
        if previous and not os.path.lexists(os.path.join(vault, previous)):
            previous = self._locate(vault, key, index) or previous
        if previous:
            base = previous
        else:
            first = turns[0]['prompt'] if turns else card.get('title')
            stem = (local_time(card.get('created_at'), '%Y-%m-%d') + ' ' + safe_name(title_text(first), 90)
                    + ' (' + SHORT.get(card['provider'], card['provider']) + ' '
                    + safe_name((card.get('session_id') or card['id'])[:8], 16, 'session') + ')').strip()
            base = f'{FOLDER}/{folder}/{stem}.md'
        content = render(card, board, turns, launched=launched, mode=mode)
        number = retries = 0
        while number < 60:
            number += 1
            rel = candidate(base, '', number)
            path = os.path.join(vault, rel)
            ensure_parent(vault, rel)
            state, data = inspect(path, key, 'note')
            if state == 'foreign' and previous and rel == previous:
                state = 'edited'  # Our earlier note, rewritten without Kanban's markers.
            try:
                if state == 'missing':
                    if not create_file(path, content):
                        raise Changed()
                    return self._outcome(path, rel, None, 'created')
                if state == 'clean':
                    if data == content:
                        return self._outcome(path, rel, None, 'unchanged')
                    replace_file(path, content, data)
                    return self._outcome(path, rel, None, 'updated')
                if state == 'edited':
                    copy = self._write_copy(vault, card, board, turns, rel, record, launched, mode)
                    return self._outcome(path, rel, copy, 'conflicts')
            except Changed:
                # Written concurrently by someone else: inspect the same name again.
                retries += 1
                if retries > 5:
                    raise SyncError('노트가 계속 바뀌고 있어 이번 동기화에서 건너뜁니다: ' + rel)
                number -= 1
            # A foreign file (user note, symlink, another session) keeps its name; try the next one.
        raise SyncError('사용 가능한 노트 파일 이름을 찾지 못했습니다: ' + base)

    def _write_copy(self, vault, card, board, turns, original, record, launched=None, mode='auto'):
        key = note_key(card)
        content = render(card, board, turns, 'conflict-copy', original, launched, mode)
        base = candidate(original, '(Kanban 최신본)', 1)
        known = record.get('conflict_path') if record and safe_rel(record.get('conflict_path')) else None
        names = ([known] if known else []) + [candidate(original, '(Kanban 최신본)', n) for n in range(1, 30)]
        for rel in names:
            path = os.path.join(vault, rel)
            ensure_parent(vault, rel)
            state, data = inspect(path, key, 'conflict-copy')
            try:
                if state == 'missing' and create_file(path, content):
                    return rel
                if state == 'clean':
                    if data != content:
                        replace_file(path, content, data)
                    return rel
            except Changed:
                continue
        raise SyncError('최신본 사본을 쓸 이름을 찾지 못했습니다: ' + base)

    @staticmethod
    def _outcome(path, rel, conflict, outcome):
        info = os.lstat(path)
        return {'path': rel, 'conflict_path': conflict, 'outcome': outcome,
                'mtime_ns': info.st_mtime_ns, 'size': info.st_size}

    def resolve(self, key):
        """User-confirmed: keep the edited note as a backup file and restore the Kanban note."""
        if not isinstance(key, str):
            raise ValueError('충돌 노트 식별자가 올바르지 않습니다.')
        if not self.lock.acquire(timeout=30):
            raise ValueError('동기화가 진행 중입니다. 잠시 후 다시 시도하세요.')
        backup = None
        try:
            context = self._context()
            card = next((item for item in context['everything'] if exportable(item) and note_key(item) == key), None)
            if card is None:
                raise ValueError('카드를 찾을 수 없습니다.')
            boards = context['boards']
            board = boards[board_of(card, boards)]
            config = self.active().get(board['id'])
            if not config:
                raise ValueError('이 프로젝트는 Obsidian 연동이 꺼져 있습니다.')
            vault = config['vault']
            root = self._check_vault(vault)
            with self.store.connect() as db:
                row = db.execute('SELECT * FROM obsidian_notes WHERE vault=? AND source_key=?', (vault, key)).fetchone()
            if not row or not row['conflict_path'] or not safe_rel(row['path']):
                raise ValueError('해결할 편집 충돌이 없습니다.')
            record = dict(row)
            chosen = (sorted(turn for note, turn in context['picks'] if note == key)
                      if config['mode'] == 'selected' else None)
            kids = sorted(context['family'].get(card['id'], []), key=lambda item: item['created_at'])
            turns, launched = self._content(card, kids, context['everything'], chosen)
            with self._vault_lock(root):
                main = os.path.join(vault, record['path'])
                state, _ = inspect(main, key, 'note')
                if state in ('edited', 'foreign'):
                    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
                    for number in range(1, 30):
                        rel = candidate(record['path'], f'(편집본 {stamp})', number)
                        if move_no_clobber(main, os.path.join(vault, rel)):
                            backup = rel
                            break
                    else:
                        raise SyncError('편집본을 보존할 파일 이름을 찾지 못했습니다.')
                copy = record['conflict_path']
                if safe_rel(copy) and inspect(os.path.join(vault, copy), key, 'conflict-copy')[0] == 'clean':
                    # Kanban's own unmodified copy; its content is rewritten into the main note.
                    os.unlink(os.path.join(vault, copy))
                record['conflict_path'] = None
                outcome = self._write(vault, card, board, context['folders'][board['id']], record, {'built': True},
                                      launched, turns, config['mode'])
                self._save(vault, key, None, outcome)
        except SyncError as exc:
            raise ValueError(str(exc))
        finally:
            self.lock.release()
        return dict(self.status(), backup=backup)
