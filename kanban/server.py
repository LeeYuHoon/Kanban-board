import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .store import ROOT, Store

ASSETS = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'),
          '/style.css': ('style.css', 'text/css')}


def handler(store):
    class Handler(BaseHTTPRequestHandler):
        def respond(self, code, value, mime='application/json'):
            data = json.dumps(value, ensure_ascii=False).encode() if mime == 'application/json' else value
            self.send_response(code)
            self.send_header('Content-Type', mime + '; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(data)

        def allowed(self):
            port = self.server.server_address[1]
            hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
            origin = self.headers.get('Origin')
            return (self.headers.get('Host') in hosts
                    and (origin is None or origin in {'http://' + h for h in hosts})
                    and self.headers.get('Sec-Fetch-Site') != 'cross-site')

        def do_GET(self):
            if not self.allowed():
                return self.respond(403, {'error': 'Local access only'})
            path = urlsplit(self.path).path
            if path in ASSETS:
                file, mime = ASSETS[path]
                return self.respond(200, (ROOT / 'web' / file).read_bytes(), mime)
            if path == '/api/health':
                return self.respond(200, {'ok': True, 'app': 'Kanban-board'})
            if path == '/api/state':
                return self.respond(200, {'boards': store.boards(), 'cards': store.cards()})
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
                if self.command == 'POST' and path == '/api/boards':
                    value = store.create_board(data.get('name'), data.get('directory'))
                elif self.command == 'POST' and path == '/api/cards':
                    value = store.create_card(data.get('board_id'), data.get('title'),
                                              data.get('prompt', ''), data.get('provider'))
                elif self.command == 'PATCH' and path.startswith('/api/cards/'):
                    if set(data) - {'archived'} or 'archived' not in data:
                        raise ValueError('실행 상태는 CLI에서 관리합니다. 보관 여부만 변경할 수 있습니다.')
                    value = store.update(path.rsplit('/', 1)[-1], archived=data['archived'])
                else:
                    return self.respond(404, {'error': 'Not found'})
                self.respond(200, value)
            except (ValueError, TypeError) as exc:
                self.respond(400, {'error': str(exc)})

        def log_message(self, fmt, *args):
            pass
    return Handler


def serve(port=4310):
    server = ThreadingHTTPServer(('127.0.0.1', port), handler(Store()))
    print(f'Kanban-board → http://127.0.0.1:{port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
