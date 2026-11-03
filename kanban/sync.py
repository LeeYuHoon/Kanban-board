"""Durable collector export and device-scoped imports. No network or agent calls here.

The journal coalesces updates to each card/turn, while small immutable checkpoints
identify a restored/replaced database. Canonical history is never pruned on ACK.
"""
import hashlib
import hmac
import json
import secrets
import uuid
from datetime import datetime, timezone

PROTOCOL = 1
BATCH_BYTES = 512 * 1024
BATCH_ITEMS = 100


def stamp():
    return datetime.now(timezone.utc).isoformat()


def migrate(db):
    for table, columns in {
        'cards': {'device_id': "TEXT NOT NULL DEFAULT ''"},
        'boards': {'device_id': "TEXT NOT NULL DEFAULT ''", 'remote_directory': 'TEXT'},
    }.items():
        existing = {r['name'] for r in db.execute(f'PRAGMA table_info({table})')}
        for name, definition in columns.items():
            if name not in existing:
                db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
    if 'device_id' not in {r['name'] for r in db.execute('PRAGMA table_info(session_turns)')}:
        db.execute('''CREATE TABLE device_turns (
            device_id TEXT NOT NULL DEFAULT '', provider TEXT NOT NULL, session_id TEXT NOT NULL,
            turn_key TEXT NOT NULL, prompt TEXT NOT NULL, result TEXT NOT NULL, status TEXT NOT NULL,
            usage TEXT, started_at TEXT NOT NULL, updated_at TEXT NOT NULL, finished_at TEXT, activity TEXT,
            PRIMARY KEY(device_id,provider,session_id,turn_key))''')
        db.execute('''INSERT INTO device_turns (provider,session_id,turn_key,prompt,result,status,
            usage,started_at,updated_at,finished_at,activity) SELECT provider,session_id,turn_key,prompt,
            result,status,usage,started_at,updated_at,finished_at,activity FROM session_turns''')
        db.execute('DROP TABLE session_turns')
        db.execute('ALTER TABLE device_turns RENAME TO session_turns')
    db.execute('''CREATE TABLE IF NOT EXISTS remote_devices (
        id TEXT PRIMARY KEY, alias TEXT NOT NULL, machine TEXT NOT NULL, token TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 0, epoch TEXT NOT NULL DEFAULT '',
        cursor INTEGER NOT NULL DEFAULT 0, anchor TEXT NOT NULL DEFAULT '',
        last_sync TEXT, status TEXT NOT NULL DEFAULT '연결 대기', error TEXT,
        pending INTEGER NOT NULL DEFAULT 0)''')


def identity(store):
    from .installation import check
    installed = check(mode='main')
    if installed:
        return installed['id']  # A DB copy does not clone the installed Main's identity.
    with store.connect() as db:
        db.execute("INSERT OR IGNORE INTO settings VALUES ('main_id', ?)", (uuid.uuid4().hex,))
        return db.execute("SELECT value FROM settings WHERE key='main_id'").fetchone()[0]


def _setting(db, key, default=None):
    row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    return row['value'] if row else default


def enable_export(store):
    """Enable triggers only in the collector DB; writes and export entries commit together."""
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if _setting(db, 'export_schema') == '1':
            return
        db.execute('''CREATE TABLE IF NOT EXISTS sync_items (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, item_key TEXT NOT NULL,
            payload TEXT NOT NULL, nonce TEXT NOT NULL, UNIQUE(kind,item_key))''')
        db.execute('CREATE TABLE IF NOT EXISTS sync_checkpoints (seq INTEGER PRIMARY KEY, nonce TEXT NOT NULL)')
        db.execute('''CREATE TRIGGER IF NOT EXISTS sync_checkpoint AFTER INSERT ON sync_items BEGIN
            INSERT INTO sync_checkpoints VALUES (NEW.seq, NEW.nonce); END''')
        db.execute("INSERT OR IGNORE INTO settings VALUES ('export_epoch', ?)", (uuid.uuid4().hex,))
        for table, kind, key in [('cards', 'card', 'NEW.id'), ('session_turns', 'turn',
                "json_array(NEW.provider,NEW.session_id,NEW.turn_key)")]:
            cols = [r['name'] for r in db.execute(f'PRAGMA table_info({table})')]
            # Personal board/archive settings are owned by the receiving main.
            cols = [c for c in cols if c not in ('archived', 'device_id')]
            args = ','.join(f"'{c}',NEW.{c}" for c in cols)
            if table == 'cards':
                args += ", 'board', json((SELECT json_object('id',id,'name',name,'directory',directory,'created_at',created_at) FROM boards WHERE id=NEW.board_id))"
            payload = 'json_object(' + args + ')'
            # An outer UPSERT can override a trigger's OR REPLACE conflict policy.
            # Explicit deletion and insertion stay in the canonical write transaction.
            write = (f"DELETE FROM sync_items WHERE kind='{kind}' AND item_key={key}; "
                     f"INSERT INTO sync_items(kind,item_key,payload,nonce) VALUES ('{kind}',{key},{payload},lower(hex(randomblob(16))));")
            condition = "NEW.device_id=''"
            if table == 'cards':
                condition += " AND NEW.source='session'"
            for event in ('INSERT', 'UPDATE'):
                changed = '' if event == 'INSERT' else ' AND (' + ' OR '.join(f'NEW.{c} IS NOT OLD.{c}' for c in cols) + ')'
                db.execute(f'CREATE TRIGGER export_{table}_{event.lower()} AFTER {event} ON {table} WHEN {condition}{changed} BEGIN {write} END')
            # Seed existing history, including logs no longer available on disk.
            select_payload = payload.replace('NEW.', 's.')
            select_key = key.replace('NEW.', 's.')
            where = "s.device_id=''" + (" AND s.source='session'" if table == 'cards' else '')
            db.execute(f"INSERT OR REPLACE INTO sync_items(kind,item_key,payload,nonce) SELECT '{kind}',{select_key},{select_payload},lower(hex(randomblob(16))) FROM {table} s WHERE {where}")
        db.execute("INSERT OR REPLACE INTO settings VALUES ('export_schema','1')")


def _credentials(request):
    main = request.get('main_id')
    token = request.get('token')
    if not isinstance(main, str) or len(main) != 32 or not isinstance(token, str) or len(token) != 64:
        raise ValueError('연결 정보가 올바르지 않습니다.')
    return main, hashlib.sha256(token.encode()).hexdigest()


def _authorized(db, request):
    main, digest = _credentials(request)
    raw = _setting(db, 'collector_owner')
    owner = json.loads(raw) if raw else {}
    if owner.get('main_id') != main or not hmac.compare_digest(owner.get('token_hash', ''), digest):
        raise ValueError('등록된 메인이 아닙니다. 원격 컴퓨터의 연결 상태를 확인하세요.')
    return owner


def dispatch(store, device, request):
    """Called by the authenticated remote OS user over hc exec's stdin/stdout."""
    if not isinstance(request, dict) or request.get('protocol') != PROTOCOL:
        raise ValueError('동기화 버전이 맞지 않습니다. 양쪽 Kanban을 업데이트하세요.')
    op = request.get('op')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE' if op in ('claim', 'release', 'ack') else 'BEGIN')
        owner = json.loads(_setting(db, 'collector_owner', '{}'))
        epoch = _setting(db, 'export_epoch')
        base = {'protocol': PROTOCOL, 'device_id': device['id'], 'alias': device['alias'], 'epoch': epoch}
        if op == 'info':
            status = json.loads(_setting(db, 'collector_status', '{}'))
            return dict(base, bound=bool(owner), collector=status)
        if op == 'claim':
            main, digest = _credentials(request)
            if owner:
                _authorized(db, request)  # Retrying a lost response is safe.
            else:
                db.execute("INSERT OR REPLACE INTO settings VALUES ('collector_owner',?)",
                           (json.dumps({'main_id': main, 'token_hash': digest, 'ack': 0}),))
            return dict(base, bound=True)
        if op == 'release':
            _credentials(request)
            if owner:
                _authorized(db, request)
            db.execute("DELETE FROM settings WHERE key='collector_owner'")
            return dict(base, bound=False)
        owner = _authorized(db, request)
        after = request.get('after', 0)
        if type(after) is not int or after < 0:
            raise ValueError('수신 위치가 올바르지 않습니다.')
        checkpoint = db.execute('SELECT nonce FROM sync_checkpoints WHERE seq=?', (after,)).fetchone()
        reset = request.get('epoch') not in ('', epoch) or (after > 0 and (not checkpoint or checkpoint['nonce'] != request.get('anchor')))
        if op == 'ack':
            if reset:
                return dict(base, reset=True)
            if after > owner.get('ack', 0):
                owner.update(ack=after, last_ack=stamp())
                db.execute("INSERT OR REPLACE INTO settings VALUES ('collector_owner',?)", (json.dumps(owner),))
            return dict(base, acknowledged=after)
        if op != 'read':
            raise ValueError('지원하지 않는 동기화 요청입니다.')
        if reset:
            return dict(base, reset=True)
        rows = db.execute('SELECT seq,kind,payload,nonce FROM sync_items WHERE seq>? ORDER BY seq LIMIT ?',
                          (after, BATCH_ITEMS + 1))
        items, size, cursor, anchor = [], 0, after, request.get('anchor', '')
        for row in rows:
            payload = row['payload']
            if items and (size + len(payload.encode()) > BATCH_BYTES or len(items) >= BATCH_ITEMS):
                break
            # Canonical rows can include long prompts; never silently skip a row.
            if len(payload.encode()) > 2 * 1024 * 1024:
                raise ValueError('전송 항목이 너무 큽니다. 수신 위치를 유지합니다.')
            items.append({'seq': row['seq'], 'kind': row['kind'], 'value': json.loads(payload)})
            size += len(payload.encode())
            cursor, anchor = row['seq'], row['nonce']
        more = db.execute('SELECT 1 FROM sync_items WHERE seq>? LIMIT 1', (cursor,)).fetchone() is not None
        return dict(base, items=items, cursor=cursor, anchor=anchor, more=more,
                    collector=json.loads(_setting(db, 'collector_status', '{}')))


def remote_id(device_id, native_id):
    return uuid.uuid5(uuid.NAMESPACE_URL, 'kanban-remote:' + device_id + ':' + native_id).hex if native_id else None


def _text(value, name, maximum=200000, required=True):
    if not isinstance(value, str) or len(value) > maximum or (required and not value):
        raise ValueError('원격 데이터 형식 오류: ' + name)
    return value


def apply_batch(store, device_id, batch):
    """Import records and the durable cursor in ONE transaction before ACKing."""
    if batch.get('protocol') != PROTOCOL or batch.get('device_id') != device_id:
        raise ValueError('원격 컴퓨터 ID 또는 동기화 버전이 바뀌었습니다. 다시 연결하세요.')
    _text(batch.get('epoch'), 'epoch', 64)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        device = db.execute('SELECT * FROM remote_devices WHERE id=?', (device_id,)).fetchone()
        if not device or not device['enabled']:
            raise ValueError('연결이 해제된 컴퓨터입니다.')
        if batch.get('reset'):
            db.execute("UPDATE remote_devices SET epoch=?,cursor=0,anchor='',status='이력 재대조' WHERE id=?", (batch['epoch'], device_id))
            return
        if device['epoch'] not in ('', batch['epoch']):
            raise ValueError('원격 저장소가 변경되었습니다. 이력을 다시 대조해야 합니다.')
        items = batch.get('items')
        if not isinstance(items, list) or len(items) > BATCH_ITEMS:
            raise ValueError('원격 데이터 목록이 올바르지 않습니다.')
        cursor = batch.get('cursor')
        if type(cursor) is not int or cursor < 0 or not isinstance(batch.get('anchor'), str):
            raise ValueError('원격 수신 위치가 올바르지 않습니다.')
        # A retry after a durable save must never replay older snapshots over newer ones.
        if cursor <= device['cursor'] and items:
            if cursor == device['cursor'] and batch['anchor'] != device['anchor']:
                raise ValueError('원격 수신 위치가 달라졌습니다. 이력을 다시 대조하세요.')
            return
        previous = device['cursor']
        for item in items:
            if not isinstance(item, dict) or type(item.get('seq')) is not int or item['seq'] <= previous:
                raise ValueError('원격 기록 순서가 올바르지 않습니다.')
            previous = item['seq']
            value = item.get('value')
            if not isinstance(value, dict) or value.get('provider') not in ('codex', 'claude'):
                raise ValueError('원격 기록 형식이 올바르지 않습니다.')
            for name in ('session_id', 'prompt', 'status', 'updated_at'):
                _text(value.get(name), name)
            if value['status'] not in ('running', 'done', 'failed', 'interrupted', 'unknown', 'todo'):
                raise ValueError('원격 상태가 올바르지 않습니다.')
            for name in ('activity', 'usage'):
                if value.get(name) is not None and not isinstance(json.loads(value[name]), (dict, type(None))):
                    raise ValueError('원격 사용 내역이 올바르지 않습니다.')
            if item['kind'] == 'turn':
                _text(value.get('turn_key'), 'turn_key')
                _text(value.get('started_at'), 'started_at')
                columns = ['provider', 'session_id', 'turn_key', 'prompt', 'result', 'status', 'usage',
                           'started_at', 'updated_at', 'finished_at', 'activity']
                values = {key: value.get(key) for key in columns}
                values['device_id'] = device_id
                columns.append('device_id')
                updates = ','.join(f'{key}=excluded.{key}' for key in columns if key not in ('device_id','provider','session_id','turn_key'))
                db.execute('INSERT INTO session_turns (' + ','.join(columns) + ') VALUES (' + ','.join(':'+key for key in columns) +
                           ') ON CONFLICT(device_id,provider,session_id,turn_key) DO UPDATE SET ' + updates +
                           ' WHERE excluded.updated_at >= session_turns.updated_at', values)
            elif item['kind'] == 'card':
                _import_card(db, device, value)
            else:
                raise ValueError('알 수 없는 원격 기록입니다.')
        if cursor != previous:
            raise ValueError('원격 수신 위치가 올바르지 않습니다.')
        alias = _text(batch.get('alias'), 'alias', 80)
        health = batch.get('collector') or {}
        if not isinstance(health, dict):
            raise ValueError('원격 수집 상태가 올바르지 않습니다.')
        status = '동기화 중' if batch.get('more') else '연결됨'
        if health.get('errors'):
            status = '원격 수집 오류'
        elif not health.get('active'):
            status = '수집기 중지'
        elif not health.get('enabled', True):
            status = '원격 수집 일시정지'
        else:
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(health['heartbeat'])).total_seconds()
                if age > 30:
                    status = '수집기 응답 없음'
            except (KeyError, ValueError, TypeError):
                status = '수집기 응답 없음'
        pending = int(bool(batch.get('more')))
        if not items and (device['alias'], device['epoch'], device['cursor'], device['anchor'], device['status'], device['error'], device['pending']) == (alias, batch['epoch'], previous, batch['anchor'], status, None, pending):
            return  # No DB write/board redraw on an unchanged poll.
        db.execute('''UPDATE remote_devices SET alias=?,epoch=?,cursor=?,anchor=?,last_sync=?,
            status=?,error=NULL,pending=? WHERE id=?''',
            (alias, batch['epoch'], previous, batch['anchor'], stamp() if items else device['last_sync'], status, pending, device_id))


def _import_card(db, device, value):
    device_id = device['id']
    board = value.get('board')
    if not isinstance(board, dict):
        raise ValueError('원격 프로젝트 정보가 없습니다.')
    for name in ('id', 'name', 'directory'):
        _text(board.get(name), 'board.' + name)
    if not board['directory'].startswith('/'):
        raise ValueError('원격 프로젝트 경로가 올바르지 않습니다.')
    identifier = remote_id(device_id, _text(value.get('id'), 'id'))
    board_id = remote_id(device_id, board['id'])
    # Keep a namespaced unique directory internally; never resolve remote paths locally.
    db.execute('''INSERT INTO boards (id,name,directory,created_at,origin,device_id,remote_directory)
        VALUES (?,?,?,?,'session',?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,
        remote_directory=excluded.remote_directory''',
        (board_id, board['name'], 'remote://' + device_id + '/' + board['id'],
         board.get('created_at') or stamp(), device_id, board['directory']))
    columns = ['title','prompt','provider','status','result','usage','session_id','created_at','updated_at',
               'started_at','finished_at','source_path','status_note','source_updated_at','activity','launch',
               'repo_key','parent_turn_key']
    values = {key: value.get(key) for key in columns}
    values.update(id=identifier, board_id=board_id, device_id=device_id, source='session',
                  source_key='remote:' + device_id + ':' + _text(value.get('source_key'), 'source_key'),
                  observed_at=stamp(), parent_id=remote_id(device_id, value.get('parent_id')),
                  home_board_id=remote_id(device_id, value.get('home_board_id')))
    columns = list(values)
    updates = ','.join(f'{key}=excluded.{key}' for key in columns if key not in ('id', 'created_at', 'source_key'))
    db.execute('INSERT INTO cards (' + ','.join(columns) + ') VALUES (' + ','.join(':'+key for key in columns) +
               ') ON CONFLICT(source_key) DO UPDATE SET ' + updates +
               " WHERE COALESCE(excluded.source_updated_at,excluded.updated_at) >= COALESCE(cards.source_updated_at,'')", values)
