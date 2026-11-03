"""Collector daemon and one-shot stdin/stdout endpoint reached via Herdr."""
import json
import os
import signal
import sqlite3
import sys
import threading

from .installation import check, collector_lock
from .store import Store
from .sync import dispatch, enable_export, stamp


def serve_collector(data=None):
    data = data or check(mode='collector', required=True)
    os.environ['KANBAN_DB'] = data['db']
    with collector_lock(data):
        store = Store(data['db'])
        enable_export(store)
        from .collector import Collector
        collector = Collector(store)
        stopping = threading.Event()
        def stop(signum, frame):
            stopping.set()
            collector.stop_event.set()
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        # The heartbeat stays fresh even when a very large source takes a long time.
        collector.start()
        print('Kanban 원격 수집기 시작 / ' + data['alias'], flush=True)
        try:
            while not stopping.is_set():
                status = dict(collector.status(), active=True, heartbeat=stamp())
                store.set_setting('collector_status', json.dumps(status, ensure_ascii=False))
                stopping.wait(5)
        finally:
            collector.stop()
            store.set_setting('collector_status', json.dumps(dict(collector.status(), active=False, heartbeat=stamp())))


def main():
    try:
        data = check(mode='collector', required=True)
        request = sys.stdin.buffer.read(8193)
        if len(request) > 8192:
            raise ValueError('동기화 요청이 너무 큽니다.')
        request = json.loads(request)
        store = Store(data['db'])
        enable_export(store)
        result = dispatch(store, data, request)
        response = {'ok': True, 'data': result}
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
        response = {'ok': False, 'error': str(exc)}
    print(json.dumps(response, ensure_ascii=False))


if __name__ == '__main__':
    main()
