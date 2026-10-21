import json
import hashlib
import fcntl
import os
import signal
import sqlite3
import threading
import uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

from . import catalog, search
from .obsidian import ObsidianSync, note_key
from .store import ROOT, Store

ASSETS = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'),
          '/style.css': ('style.css', 'text/css')}


def handler(store, collector=None, obsidian=None):
    # A persistent connection observes commits from every writer via data_version.
    # This lets unchanged polls avoid loading and serializing the entire board.
    revision_db = sqlite3.connect(store.path, check_same_thread=False)
    revision_lock = threading.Lock()
    instance = uuid.uuid4().hex

    class Handler(BaseHTTPRequestHandler):
        def respond(self, code, value, mime='application/json'):
            data = json.dumps(value, ensure_ascii=False).encode() if mime == 'application/json' else value
            self.send_response(code)
            self.send_header('Content-Type', mime + '; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            if getattr(self, 'send_state_etag', None):
                self.send_header('ETag', self.send_state_etag)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass  # The tab closed or navigation cancelled its poll.

        def allowed(self, page=False):
            port = self.server.server_address[1]
            hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
            origin = self.headers.get('Origin')
            # Opening the static page from a link or another app is a cross-site top-level
            # navigation; it reads no data. API calls stay same-origin only.
            navigation = (page and self.headers.get('Sec-Fetch-Mode') == 'navigate'
                          and self.headers.get('Sec-Fetch-Dest') == 'document')
            return (self.headers.get('Host') in hosts
                    and (origin is None or origin in {'http://' + h for h in hosts})
                    and (navigation or self.headers.get('Sec-Fetch-Site') != 'cross-site'))

        def do_GET(self):
            path = urlsplit(self.path).path
            if not self.allowed(page=path == '/'):
                return self.respond(403, {'error': 'Local access only'})
            if path in ASSETS:
                file, mime = ASSETS[path]
                return self.respond(200, (ROOT / 'web' / file).read_bytes(), mime)
            if path == '/api/catalog':
                return self.respond(200, catalog.load(store))
            if path == '/api/health':
                return self.respond(200, {'ok': True, 'app': 'Kanban'})
            if path == '/api/state':
                collection_status = collector.status() if collector else {'enabled': False, 'available': False}
                with revision_lock:
                    data_version = revision_db.execute('PRAGMA data_version').fetchone()[0]
                # Last-check time changes every scan but does not change the board.
                collection_revision = {key: value for key, value in collection_status.items()
                                       if key not in ('last_scan', 'files', 'roots')}
                live = (obsidian.running, obsidian.manual) if obsidian else ()
                revision = json.dumps((instance, data_version, collection_revision, live), sort_keys=True)
                etag = '"' + hashlib.sha256(revision.encode()).hexdigest() + '"'
                if self.headers.get('If-None-Match') == etag:
                    self.send_response(304)
                    self.send_header('ETag', etag)
                    self.send_header('Cache-Control', 'no-store')
                    self.end_headers()
                    return
                cards = store.cards()
                links = obsidian.links(cards) if obsidian else {}
                picks = {}
                for key, turn in (obsidian.picks() if obsidian else ()):
                    picks.setdefault(key, []).append(turn)
                for card in cards:
                    # Agent-launched sessions live inside the note of the session that started them.
                    card['obsidian'] = links.get(card.get('root_id') or card['id'])
                    card['obsidian_picks'] = picks.get(note_key(card), [])
                self.send_state_etag = etag
                return self.respond(200, {'boards': store.boards(), 'cards': cards,
                    'collection': collection_status,
                    'obsidian': obsidian.status() if obsidian else {'available': False},
                    'catalog_version': catalog.version(store)})
            if path == '/api/search':
                query = parse_qs(urlsplit(self.path).query)
                try:
                    return self.respond(200, search.run(store, query.get('q', [''])[0], query.get('target', ['all'])[0],
                                                        query.get('since', [None])[0], query.get('until', [None])[0]))
                except (ValueError, TypeError) as exc:
                    return self.respond(400, {'error': str(exc)})
            if path.startswith('/api/cards/') and path.endswith('/history'):
                try:
                    identifier = path.split('/')[3]
                    query = parse_qs(urlsplit(self.path).query)
                    if 'keys' in query:
                        return self.respond(200, store.history_keys(identifier, json.loads(query['keys'][0])))
                    before = query.get('before', [None])[0]
                    return self.respond(200, store.history(identifier, before))
                except (ValueError, TypeError) as exc:
                    return self.respond(400, {'error': str(exc)})
            self.respond(404, {'error': 'Not found'})

        def do_POST(self):
            self.mutate()

        def do_PATCH(self):
            self.mutate()

        def mutate(self):
            if not self.allowed():
                return self.respond(403, {'error': 'Local access only'})
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                return self.respond(415, {'error': 'JSON required'})
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 1048576:
                    raise ValueError('요청 크기가 올바르지 않습니다.')
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError('JSON object required')
                path = urlsplit(self.path).path
                if self.command == 'POST' and path == '/api/collection' and collector:
                    if set(data) == {'enabled'}:
                        store.set_collection_enabled(data['enabled'])
                    elif data != {'scan': True}:
                        raise ValueError('수집 설정이 올바르지 않습니다.')
                    collector.wake.set()
                    value = collector.status()
                elif self.command == 'POST' and path == '/api/obsidian' and obsidian:
                    if set(data) in ({'project', 'mode'}, {'project', 'mode', 'vault'}):
                        value = obsidian.configure_project(data['project'], data['mode'], data.get('vault'))
                    elif set(data) == {'card_id', 'turn_key', 'picked'}:
                        value = obsidian.pick(data['card_id'], data['turn_key'], data['picked'])
                    elif data == {'sync': True}:
                        value = obsidian.request_sync()
                    elif set(data) == {'resolve'}:
                        value = obsidian.resolve(data['resolve'])
                    else:
                        raise ValueError('Obsidian 설정 요청이 올바르지 않습니다.')
                elif self.command == 'POST' and path == '/api/descriptions':
                    if set(data) != {'kind', 'name', 'text'}:
                        raise ValueError('설명 저장 요청이 올바르지 않습니다.')
                    value = catalog.set_user_description(store, data['kind'], data['name'], data['text'])
                elif self.command == 'POST' and path == '/api/boards':
                    value = store.create_board(data.get('name'), data.get('directory'))
                elif self.command == 'PATCH' and path.startswith('/api/cards/'):
                    identifier = path.rsplit('/', 1)[-1]
                    # Cards come from collected sessions (or CLI runs); the web only archives them.
                    if set(data) == {'archived'} and isinstance(data['archived'], bool):
                        value = store.update(identifier, archived=data['archived'])
                    else:
                        raise ValueError('카드는 보관 여부만 변경할 수 있습니다.')
                else:
                    return self.respond(404, {'error': 'Not found'})
                self.respond(200, value)
            except (ValueError, TypeError) as exc:
                self.respond(400, {'error': str(exc)})

        def log_message(self, fmt, *args):
            pass
    return Handler


def serve(port=31229, collect=True):
    lock_path = Path.home() / 'Library' / 'Caches' / 'com.kanban.server.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise OSError('Kanban이 이미 실행 중입니다. 기존 서버를 종료한 뒤 다시 실행하세요.')
        _serve_locked(port, collect)
    finally:
        os.close(lock_fd)


def _serve_locked(port, collect):
    from .collector import Collector
    store = Store()
    collector = Collector(store) if collect else None
    obsidian = ObsidianSync(store)
    server = ThreadingHTTPServer(('127.0.0.1', port), handler(store, collector, obsidian))
    if collector:
        collector.start()
    # Separate thread: a vault failure never blocks session collection or the board.
    obsidian.start()
    print(f'Kanban → http://127.0.0.1:{port}', flush=True)

    def stop(signum, frame):
        # `kanban stop`, launchd and background launches send SIGTERM; shut down like Ctrl+C.
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if collector:
            collector.stop()
        obsidian.stop()
        server.server_close()
