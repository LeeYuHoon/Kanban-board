"""Pull collector records through an existing Herdr connection. No public listener."""
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import shutil
import signal
import sqlite3
import subprocess
import threading
import time

from .sync import PROTOCOL, apply_batch, identity, stamp
import secrets

ENDPOINT = '/Library/Application Support/Kanban/remote'
MAX_OUTPUT = 4 * 1024 * 1024


class HcTransport:
    def __init__(self, executable=None):
        self.cancelled = threading.Event()
        candidates = [executable or os.environ.get('KANBAN_HC'), shutil.which('hc'),
                      str(Path.home() / '.local/bin/hc'), '/opt/homebrew/bin/hc', '/usr/local/bin/hc']
        self.executable = next((p for p in candidates if p and os.path.isfile(p) and os.access(p, os.X_OK)), None)

    def _run(self, args, input_data=b'', timeout=20):
        if not self.executable:
            raise ValueError('Herdr의 hc를 찾지 못했습니다. Herdr를 설치하고 원격 컴퓨터를 먼저 연결하세요.')
        proc = subprocess.Popen([self.executable, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, start_new_session=True)
        output = {'out': bytearray(), 'err': bytearray()}
        deadline = time.monotonic() + timeout
        try:
            proc.stdin.write(input_data)
            proc.stdin.close()
            with selectors.DefaultSelector() as selector:
                for stream, name in ((proc.stdout, 'out'), (proc.stderr, 'err')):
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream, selectors.EVENT_READ, name)
                while selector.get_map():
                    if self.cancelled.is_set():
                        raise ValueError('동기화를 종료합니다. 저장된 위치부터 다시 이어갑니다.')
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ValueError('Herdr 연결 시간이 초과되었습니다. 연결 복구 후 자동으로 다시 시도합니다.')
                    for key, _ in selector.select(min(remaining, 0.25)):
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        output[key.data].extend(chunk)
                        if sum(map(len, output.values())) > MAX_OUTPUT:
                            raise ValueError('원격 응답이 너무 큽니다. 수신 위치를 유지합니다.')
            code = proc.wait(timeout=max(0.1, deadline - time.monotonic()))
            if code:
                detail = output['err'].decode('utf-8', 'replace').strip()[-800:]
                raise ValueError('원격 수집기에 접근하지 못했습니다. Herdr 연결과 원격 전용 설치를 확인하세요.' + ('\n' + detail if detail else ''))
            try:
                return json.loads(output['out'])
            except (ValueError, UnicodeDecodeError):
                raise ValueError('Herdr 응답을 읽지 못했습니다. 지원되는 hc 버전인지 확인하세요.')
        except subprocess.TimeoutExpired:
            raise ValueError('Herdr 명령이 종료되지 않았습니다. 연결을 확인하세요.')
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                stream.close()

    def machines(self):
        value = self._run(['machine', 'ls', '--json'])
        rows = value.get('machines') if isinstance(value, dict) else value
        if not isinstance(rows, list):
            raise ValueError('Herdr 컴퓨터 목록 형식을 지원하지 않습니다. hc를 업데이트하세요.')
        result = []
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get('name'), str):
                result.append({'name': row['name'], 'state': row.get('state') or row.get('status') or ''})
        return result

    def rpc(self, machine, request):
        if not isinstance(machine, str) or not machine or len(machine) > 200 or '\x00' in machine:
            raise ValueError('Herdr 컴퓨터 이름이 올바르지 않습니다.')
        # hc 0.1.17's remote execution splits a program path at spaces.
        # Invoke a space-free shell path and send the fixed endpoint plus JSON
        # through stdin. The quoted heredoc prevents JSON from becoming code;
        # json.dumps escapes embedded newlines, so it cannot close the heredoc.
        payload = json.dumps(dict(request, protocol=PROTOCOL), ensure_ascii=False)
        script = "exec " + shlex.quote(ENDPOINT) + " <<'KANBAN_REQUEST'\n" + payload + "\nKANBAN_REQUEST\n"
        value = self._run(['exec', '-i', '--on', machine, '--', '/bin/sh'], script.encode())
        if not isinstance(value, dict) or not value.get('ok'):
            raise ValueError(value.get('error', '원격 수집기 응답 오류') if isinstance(value, dict) else '원격 응답 오류')
        result = value.get('data')
        if not isinstance(result, dict) or result.get('protocol') != PROTOCOL:
            raise ValueError('동기화 버전이 맞지 않습니다. 양쪽 Kanban을 업데이트하세요.')
        return result


class RemoteManager:
    def __init__(self, store, transport=None):
        self.store = store
        self.transport = transport or HcTransport()
        self.main_id = identity(store)
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.thread = None
        self.due = {}
        self.failures = {}
        self.checked = {}
        self.acknowledged = {}

    def devices(self):
        with self.store.connect() as db:
            rows = db.execute('''SELECT id,alias,machine,enabled,last_sync,status,error,pending
                                 FROM remote_devices ORDER BY alias''').fetchall()
        return [dict(row, last_check=self.checked.get(row['id'])) for row in rows]

    def _device(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch('[0-9a-f]{32}', identifier):
            raise ValueError('원격 컴퓨터 ID가 올바르지 않습니다.')
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM remote_devices WHERE id=?', (identifier,)).fetchone()
        if not row:
            raise ValueError('등록되지 않은 원격 컴퓨터입니다.')
        return dict(row)

    def machines(self):
        return self.transport.machines()

    def probe(self, machine):
        if machine not in [m['name'] for m in self.machines()]:
            raise ValueError('Herdr에 등록된 컴퓨터를 선택하세요.')
        info = self.transport.rpc(machine, {'op': 'info'})
        if not re.fullmatch('[0-9a-f]{32}', str(info.get('device_id', ''))):
            raise ValueError('원격 컴퓨터 ID가 올바르지 않습니다.')
        if not isinstance(info.get('alias'), str) or not 1 <= len(info['alias']) <= 80:
            raise ValueError('원격 별칭이 올바르지 않습니다.')
        return info

    def connect(self, machine, expected_id):
        with self.lock:
            info = self.probe(machine)
            if info['device_id'] != expected_id:
                raise ValueError('선택한 원격 컴퓨터가 바뀌었습니다. 다시 확인하세요.')
            with self.store.connect() as db:
                # Save the credential BEFORE claim: a lost claim response can be retried.
                db.execute('''INSERT INTO remote_devices(id,alias,machine,token) VALUES (?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET alias=excluded.alias,machine=excluded.machine''',
                    (expected_id, info['alias'], machine, secrets.token_hex(32)))
            device = self._device(expected_id)
            self.transport.rpc(machine, self._request(device, 'claim'))
            with self.store.connect() as db:
                db.execute("UPDATE remote_devices SET enabled=1,status='동기화 대기',error=NULL WHERE id=?", (expected_id,))
            self.due[expected_id] = 0
            self.wake.set()
        return self.devices()

    def disconnect(self, identifier):
        with self.lock:
            device = self._device(identifier)
            self.transport.rpc(device['machine'], self._request(device, 'release'))
            with self.store.connect() as db:
                db.execute("UPDATE remote_devices SET enabled=0,status='연결 해제',error=NULL WHERE id=?", (identifier,))
        return self.devices()

    def pause(self, identifier, paused):
        if type(paused) is not bool:
            raise ValueError('일시정지 값이 올바르지 않습니다.')
        with self.lock:
            self._device(identifier)
            with self.store.connect() as db:
                db.execute('UPDATE remote_devices SET enabled=?,status=? WHERE id=?',
                           (int(not paused), '일시정지' if paused else '동기화 대기', identifier))
            self.due[identifier] = 0
            self.wake.set()
        return self.devices()

    def _request(self, device, op):
        return dict(op=op, main_id=self.main_id, token=device['token'], epoch=device['epoch'],
                    after=device['cursor'], anchor=device['anchor'])

    def sync_one(self, identifier):
        with self.lock:
            device = self._device(identifier)
            if not device['enabled']:
                return False
            batch = self.transport.rpc(device['machine'], self._request(device, 'read'))
            apply_batch(self.store, identifier, batch)
            self.checked[identifier] = stamp()
            if not batch.get('reset'):
                saved = self._device(identifier)
                checkpoint = (saved['epoch'], saved['cursor'], saved['anchor'])
                if saved['cursor'] and self.acknowledged.get(identifier) != checkpoint:
                    ack = self.transport.rpc(saved['machine'], self._request(saved, 'ack'))
                    if ack.get('reset'):
                        apply_batch(self.store, identifier, ack)
                    else:
                        self.acknowledged[identifier] = checkpoint
            else:
                self.acknowledged.pop(identifier, None)
            return bool(batch.get('more') or batch.get('reset'))

    def start(self):
        self.thread = threading.Thread(target=self._loop, name='kanban-remote', daemon=True)
        self.thread.start()

    def stop(self):
        self.stopping.set()
        self.wake.set()
        if hasattr(self.transport, 'cancelled'):
            self.transport.cancelled.set()
        if self.thread:
            self.thread.join(timeout=3)

    def _loop(self):
        while not self.stopping.is_set():
            for device in self.devices():
                identifier = device['id']
                if self.stopping.is_set():
                    break
                if not device['enabled'] or self.due.get(identifier, 0) > time.monotonic():
                    continue
                try:
                    more = self.sync_one(identifier)
                    self.failures[identifier] = 0
                    delay = 1 if more else 15
                except (ValueError, OSError, sqlite3.Error, TypeError, KeyError) as exc:
                    self.failures[identifier] = min(self.failures.get(identifier, 0) + 1, 6)
                    delay = min(300, 5 * 2 ** self.failures[identifier])
                    with self.store.connect() as db:
                        db.execute("UPDATE remote_devices SET status='연결 확인 필요',error=? WHERE id=? AND (status<>'연결 확인 필요' OR error IS NOT ?)",
                                   (str(exc), identifier, str(exc)))
                self.due[identifier] = time.monotonic() + delay
            self.wake.wait(1)
            self.wake.clear()
