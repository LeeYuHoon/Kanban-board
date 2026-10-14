import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATUSES = ('todo', 'running', 'done', 'failed', 'interrupted', 'unknown')


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get('KANBAN_DB', ROOT / '.local/kanban.sqlite3'))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS boards (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    directory TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cards (
                    id TEXT PRIMARY KEY, board_id TEXT NOT NULL REFERENCES boards(id),
                    title TEXT NOT NULL, prompt TEXT NOT NULL, provider TEXT NOT NULL,
                    status TEXT NOT NULL, result TEXT NOT NULL DEFAULT '',
                    usage TEXT, session_id TEXT, archived INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    started_at TEXT, finished_at TEXT
                );
                CREATE TABLE IF NOT EXISTS collection_files (
                    path TEXT PRIMARY KEY, inode TEXT NOT NULL, offset INTEGER NOT NULL,
                    state TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS session_turns (
                    provider TEXT NOT NULL, session_id TEXT NOT NULL, turn_key TEXT NOT NULL,
                    prompt TEXT NOT NULL, result TEXT NOT NULL, status TEXT NOT NULL,
                    usage TEXT, started_at TEXT NOT NULL, updated_at TEXT NOT NULL, finished_at TEXT,
                    PRIMARY KEY (provider, session_id, turn_key)
                );
                CREATE TABLE IF NOT EXISTS obsidian_notes (
                    vault TEXT NOT NULL, source_key TEXT NOT NULL, path TEXT NOT NULL,
                    signature TEXT, mtime_ns INTEGER, size INTEGER, conflict_path TEXT,
                    synced_at TEXT, PRIMARY KEY (vault, source_key)
                );
                CREATE TABLE IF NOT EXISTS obsidian_projects (
                    board_id TEXT PRIMARY KEY, vault TEXT, mode TEXT NOT NULL DEFAULT 'off',
                    status TEXT, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS catalog (
                    kind TEXT NOT NULL, name TEXT NOT NULL, description TEXT NOT NULL,
                    source TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (kind, name)
                );
                CREATE TABLE IF NOT EXISTS user_descriptions (
                    kind TEXT NOT NULL, name TEXT NOT NULL, text TEXT NOT NULL,
                    updated_at TEXT NOT NULL, PRIMARY KEY (kind, name)
                );
                CREATE TABLE IF NOT EXISTS obsidian_picks (
                    note_key TEXT NOT NULL, turn_key TEXT NOT NULL, picked_at TEXT NOT NULL,
                    PRIMARY KEY (note_key, turn_key)
                );
            ''')
            db.execute('BEGIN IMMEDIATE')
            columns = {r['name'] for r in db.execute('PRAGMA table_info(cards)')}
            for name, definition in {'source': "TEXT NOT NULL DEFAULT 'manual'",
                                     'source_key': 'TEXT', 'source_path': 'TEXT',
                                     'observed_at': 'TEXT', 'status_note': 'TEXT', 'source_updated_at': 'TEXT',
                                     # Agent-launched session grouping (see linking.py).
                                     'launch': 'TEXT', 'repo_key': 'TEXT', 'parent_id': 'TEXT',
                                     'parent_turn_key': 'TEXT', 'home_board_id': 'TEXT'}.items():
                if name not in columns:
                    db.execute(f'ALTER TABLE cards ADD COLUMN {name} {definition}')
            if 'origin' not in {r['name'] for r in db.execute('PRAGMA table_info(boards)')}:
                # 'manual' = registered by the user; empty auto-created projects are hidden in the UI.
                db.execute('ALTER TABLE boards ADD COLUMN origin TEXT')
            for table in ('cards', 'session_turns'):
                if 'activity' not in {r['name'] for r in db.execute(f'PRAGMA table_info({table})')}:
                    db.execute(f'ALTER TABLE {table} ADD COLUMN activity TEXT')
            db.execute('CREATE UNIQUE INDEX IF NOT EXISTS cards_source_key ON cards(source_key)')
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def boards(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM boards ORDER BY created_at')]

    def create_board(self, name, directory):
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise ValueError('프로젝트 이름을 120자 이내로 입력하세요.')
        if not isinstance(directory, str) or not Path(directory).expanduser().is_absolute():
            raise ValueError('프로젝트 폴더의 절대 경로를 입력하세요.')
        path = Path(directory).expanduser().resolve()
        if not path.is_dir():
            raise ValueError('존재하는 프로젝트 폴더를 입력하세요.')
        board = dict(id=uuid.uuid4().hex, name=name.strip(), directory=str(path), created_at=now(), origin='manual')
        try:
            with self.connect() as db:
                db.execute('INSERT INTO boards (id,name,directory,created_at,origin) '
                           'VALUES (:id,:name,:directory,:created_at,:origin)', board)
        except sqlite3.IntegrityError:
            raise ValueError('이미 등록된 프로젝트 폴더입니다.')
        return board

    def board(self, identifier):
        if not isinstance(identifier, str):
            raise ValueError('프로젝트 ID가 올바르지 않습니다.')
        with self.connect() as db:
            row = db.execute('SELECT * FROM boards WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise ValueError('프로젝트를 찾을 수 없습니다.')
        return dict(row)

    def cards(self):
        with self.connect() as db:
            cards = [self.decode(r) for r in db.execute('''SELECT cards.*,
                (SELECT COUNT(*) FROM session_turns t WHERE t.provider=cards.provider
                 AND t.session_id=cards.session_id) AS turn_count
                FROM cards ORDER BY updated_at DESC''')]
        by_id = {card['id']: card for card in cards}
        for card in cards:
            # Top-level session for nested agent launches; None for cards shown on the board.
            root, seen = card.get('parent_id'), set()
            while root in by_id and by_id[root].get('parent_id') and root not in seen:
                seen.add(root)
                root = by_id[root]['parent_id']
            card['root_id'] = root if root in by_id else None
        return cards

    def save_turns(self, turns):
        with self.connect() as db:
            for turn in turns:
                if not all(turn.get(key) for key in ('provider', 'session_id', 'turn_key', 'prompt', 'started_at', 'updated_at')):
                    continue
                values = {key: turn.get(key) for key in ('provider', 'session_id', 'turn_key', 'prompt',
                          'result', 'status', 'started_at', 'updated_at', 'finished_at')}
                values['activity'] = json.dumps(turn.get('activity') or {})
                values['usage'] = json.dumps(turn.get('usage')) if turn.get('usage') else None
                db.execute('''INSERT INTO session_turns
                    (provider,session_id,turn_key,prompt,result,status,usage,started_at,updated_at,finished_at,activity)
                    VALUES (:provider,:session_id,:turn_key,:prompt,:result,:status,:usage,:started_at,:updated_at,:finished_at,:activity)
                    ON CONFLICT(provider,session_id,turn_key) DO UPDATE SET
                    prompt=excluded.prompt,result=excluded.result,status=excluded.status,usage=excluded.usage,activity=excluded.activity,
                    updated_at=excluded.updated_at,finished_at=excluded.finished_at
                    WHERE excluded.updated_at >= session_turns.updated_at''', values)

    def all_turns(self, provider, session_id):
        """Complete, oldest-first history for exports; not paginated like the UI."""
        stale = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
        with self.connect() as db:
            rows = db.execute('SELECT * FROM session_turns WHERE provider=? AND session_id=? '
                              'ORDER BY started_at,turn_key', (provider, session_id)).fetchall()
        items = [dict(row) for row in rows]
        for item in items:
            item['activity'] = json.loads(item['activity']) if item['activity'] else {}
            item['usage'] = json.loads(item['usage']) if item['usage'] else None
            if item['status'] == 'running' and item['updated_at'] < stale:
                item['status'] = 'unknown'
        return items

    def history_keys(self, identifier, keys):
        """Specific turns of one card (search matches), newest first, regardless of pagination."""
        if not isinstance(keys, list) or len(keys) > 500 or not all(isinstance(key, str) for key in keys):
            raise ValueError('프롬프트 목록이 올바르지 않습니다.')
        card = self.card(identifier)
        wanted = set(keys)
        items = [turn for turn in self.all_turns(card['provider'], card['session_id']) if turn['turn_key'] in wanted] \
            if card.get('session_id') else []
        return {'items': sorted(items, key=lambda turn: (turn['started_at'], turn['turn_key']), reverse=True), 'next': None}

    def history(self, identifier, before=None, limit=30):
        card = self.card(identifier)
        params = [card['provider'], card['session_id']]
        condition = ''
        if before:
            try:
                stamp, key = json.loads(before)
                if not isinstance(stamp, str) or not isinstance(key, str):
                    raise ValueError()
            except (ValueError, TypeError):
                raise ValueError('대화 이력 위치가 올바르지 않습니다.')
            condition = ' AND (started_at,turn_key) < (?,?)'
            params.extend((stamp, key))
        with self.connect() as db:
            rows = db.execute('''SELECT * FROM session_turns WHERE provider=? AND session_id=?'''
                              + condition + ' ORDER BY started_at DESC,turn_key DESC LIMIT ?',
                              (*params, limit + 1)).fetchall()
        items = [dict(row) for row in rows[:limit]]
        for item in items:
            item['activity'] = json.loads(item['activity']) if item['activity'] else {}
            item['usage'] = json.loads(item['usage']) if item['usage'] else None
            if item['status'] == 'running' and item['updated_at'] < (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat():
                item['status'] = 'unknown'
        cursor = json.dumps([items[-1]['started_at'], items[-1]['turn_key']]) if len(rows) > limit else None
        return {'items': items, 'next': cursor}

    @staticmethod
    def decode(row):
        value = dict(row)
        value['usage'] = json.loads(value['usage']) if value['usage'] else None
        value['activity'] = json.loads(value['activity']) if value['activity'] else {}
        value['archived'] = bool(value['archived'])
        return value

    def card(self, identifier):
        if not isinstance(identifier, str):
            raise ValueError('카드 ID가 올바르지 않습니다.')
        with self.connect() as db:
            row = db.execute('SELECT * FROM cards WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise ValueError('카드를 찾을 수 없습니다.')
        return self.decode(row)

    def create_card(self, board_id, title, prompt, provider):
        self.board(board_id)
        self.validate_card(title, prompt, provider)
        identifier = uuid.uuid4().hex
        timestamp = now()
        with self.connect() as db:
            db.execute('''INSERT INTO cards
                (id,board_id,title,prompt,provider,status,created_at,updated_at)
                VALUES (?,?,?,?,?,'todo',?,?)''',
                (identifier, board_id, title.strip(), prompt, provider, timestamp, timestamp))
        return self.card(identifier)

    @staticmethod
    def validate_card(title, prompt, provider):
        if provider not in ('codex', 'claude'):
            raise ValueError('Codex 또는 Claude Code를 선택하세요.')
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise ValueError('제목을 200자 이내로 입력하세요.')
        if not isinstance(prompt, str) or len(prompt) > 100000:
            raise ValueError('요청 내용이 올바르지 않습니다.')

    def edit_card(self, identifier, title, prompt, provider):
        self.validate_card(title, prompt, provider)
        with self.connect() as db:
            changed = db.execute('''UPDATE cards SET title=?,prompt=?,provider=?,updated_at=?
                WHERE id=? AND status='todo' AND archived=0 AND source='manual' ''',
                (title.strip(), prompt, provider, now(), identifier)).rowcount
        if not changed:
            raise ValueError('보관되지 않은 대기 카드만 수정할 수 있습니다.')
        return self.card(identifier)

    def update(self, identifier, *, status=None, archived=None, result=None, usage=None, session_id=None):
        if status is not None and status not in STATUSES:
            raise ValueError('지원하지 않는 상태입니다.')
        if archived is not None and not isinstance(archived, bool):
            raise ValueError('archived는 boolean이어야 합니다.')
        values = {'updated_at': now()}
        if status is not None:
            values['status'] = status
            if status == 'running':
                values['started_at'] = now()
            elif status in ('done', 'failed', 'interrupted'):
                values['finished_at'] = now()
        if archived is not None:
            values['archived'] = int(archived)
        if result is not None:
            values['result'] = result
        if usage is not None:
            values['usage'] = json.dumps(usage)
        if session_id is not None:
            values['session_id'] = session_id
        with self.connect() as db:
            cursor = db.execute('UPDATE cards SET ' + ','.join(k + '=?' for k in values) + ' WHERE id=?',
                                (*values.values(), identifier))
            if cursor.rowcount != 1:
                raise ValueError('카드를 찾을 수 없습니다.')
            if session_id:
                # A wrapped run can also leave a native transcript. Keep its original card.
                card = db.execute('SELECT provider,source FROM cards WHERE id=?', (identifier,)).fetchone()
                if card['source'] == 'manual':
                    db.execute("DELETE FROM cards WHERE source='session' AND provider=? AND session_id=?",
                               (card['provider'], session_id))
        return self.card(identifier)

    def collection_enabled(self):
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key='collection_enabled'").fetchone()
        return row is None or row['value'] == 'true'

    def set_collection_enabled(self, enabled):
        if not isinstance(enabled, bool):
            raise ValueError('enabled는 boolean이어야 합니다.')
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES ('collection_enabled',?)",
                       ('true' if enabled else 'false',))

    def setting(self, key, default=None):
        with self.connect() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return default if row is None else row['value']

    def set_setting(self, key, value):
        with self.connect() as db:
            if value is None:
                db.execute('DELETE FROM settings WHERE key=?', (key,))
            else:
                db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, value))

    def import_session(self, record, path, observed_at):
        """Idempotent native-session upsert. Source files are never opened for writing."""
        if not record.get('session_id') or not record.get('prompt') or not record.get('cwd'):
            return False
        directory = Path(record['cwd']).expanduser()
        if not directory.is_absolute():
            return False
        directory = str(directory.resolve())
        key = record['provider'] + ':' + record['session_id']
        identifier = uuid.uuid5(uuid.NAMESPACE_URL, 'kankan:' + key).hex
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM cards WHERE provider=? AND session_id=? AND source='manual'",
                          (record['provider'], record['session_id'])).fetchone():
                return False
            existing = db.execute('''SELECT c.source_updated_at, b.id board_id, b.directory FROM cards c
                JOIN boards b ON b.id=c.board_id WHERE c.source_key=?''', (key,)).fetchone()
            if existing and (existing['source_updated_at'] or '') > record['updated_at']:
                return False
            board = db.execute('SELECT id FROM boards WHERE directory=?', (directory,)).fetchone()
            if existing and directory.startswith(existing['directory'].rstrip('/') + '/'):
                board_id = existing['board_id']  # A `cd` into a subfolder stays in the same project.
            elif board:
                board_id = board['id']
            else:
                board_id = uuid.uuid4().hex
                db.execute("INSERT INTO boards (id,name,directory,created_at,origin) VALUES (?,?,?,?,'session')",
                           (board_id, Path(directory).name or directory, directory, observed_at))
            values = dict(id=identifier, board_id=board_id, title=record['prompt'].strip()[:100],
                          prompt=record['prompt'], provider=record['provider'], status=record['status'],
                          result=record.get('result', ''), usage=json.dumps(record.get('usage')) if record.get('usage') else None,
                          session_id=record['session_id'], created_at=record['created_at'],
                          updated_at=record['updated_at'], started_at=record.get('started_at'),
                          finished_at=record.get('finished_at'), source='session', source_key=key,
                          source_path=str(path), observed_at=observed_at, status_note=record.get('status_note', ''))
            values['activity'] = json.dumps(record.get('activity') or {})
            values['source_updated_at'] = record['updated_at']
            fields = list(values)
            changed = [name for name in fields if name not in ('id', 'source_key', 'created_at')]
            db.execute('INSERT INTO cards (' + ','.join(fields) + ') VALUES (' +
                       ','.join(':' + name for name in fields) + ') ON CONFLICT(source_key) DO UPDATE SET ' +
                       ','.join(name + '=excluded.' + name for name in changed) +
                       " WHERE excluded.source_updated_at >= COALESCE(cards.source_updated_at, '')", values)
        return True
