import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATUSES = ('todo', 'running', 'done', 'failed', 'interrupted')


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
            ''')
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
        board = dict(id=uuid.uuid4().hex, name=name.strip(), directory=str(path), created_at=now())
        try:
            with self.connect() as db:
                db.execute('INSERT INTO boards VALUES (:id,:name,:directory,:created_at)', board)
        except sqlite3.IntegrityError:
            raise ValueError('이미 등록된 프로젝트 폴더입니다.')
        return board

    def board(self, identifier):
        with self.connect() as db:
            row = db.execute('SELECT * FROM boards WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise ValueError('프로젝트를 찾을 수 없습니다.')
        return dict(row)

    def cards(self):
        with self.connect() as db:
            return [self.decode(r) for r in db.execute('SELECT * FROM cards ORDER BY updated_at DESC')]

    @staticmethod
    def decode(row):
        value = dict(row)
        value['usage'] = json.loads(value['usage']) if value['usage'] else None
        value['archived'] = bool(value['archived'])
        return value

    def card(self, identifier):
        with self.connect() as db:
            row = db.execute('SELECT * FROM cards WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise ValueError('카드를 찾을 수 없습니다.')
        return self.decode(row)

    def create_card(self, board_id, title, prompt, provider):
        self.board(board_id)
        if provider not in ('codex', 'claude'):
            raise ValueError('Codex 또는 Claude를 선택하세요.')
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise ValueError('제목을 200자 이내로 입력하세요.')
        if not isinstance(prompt, str) or len(prompt) > 100000:
            raise ValueError('요청 내용이 올바르지 않습니다.')
        identifier = uuid.uuid4().hex
        timestamp = now()
        with self.connect() as db:
            db.execute('''INSERT INTO cards
                (id,board_id,title,prompt,provider,status,created_at,updated_at)
                VALUES (?,?,?,?,?,'todo',?,?)''',
                (identifier, board_id, title.strip(), prompt, provider, timestamp, timestamp))
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
        return self.card(identifier)
