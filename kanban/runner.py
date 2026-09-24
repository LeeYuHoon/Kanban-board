"""Explicit CLI runs only; never edits agent configuration or starts agents from HTTP."""
import json
import os
import signal
import subprocess
import tempfile
from pathlib import Path


def parse_output(provider, output, final=''):
    usage = None
    session = None
    failed = False
    if provider == 'claude':
        data = json.loads(output)
        if not isinstance(data, dict) or data.get('type') != 'result':
            raise ValueError('Claude 결과 형식을 확인할 수 없습니다.')
        return (str(data.get('result', '')), data.get('usage'), data.get('session_id'),
                bool(data.get('is_error')) or data.get('subtype') != 'success')
    completed = False
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get('type') == 'thread.started':
            session = event.get('thread_id')
        elif event.get('type') == 'turn.completed':
            completed = True
            usage = event.get('usage')
        elif event.get('type') in ('turn.failed', 'error'):
            failed = True
    return final, usage, session, failed or not completed


def run(store, card_id):
    card = store.card(card_id)
    if card['status'] != 'todo':
        raise ValueError('Todo 카드만 실행할 수 있습니다. 다시 실행하려면 새 카드를 만드세요.')
    board = store.board(card['board_id'])
    if not Path(board['directory']).is_dir():
        raise ValueError('프로젝트 폴더가 없습니다.')
    # Claim atomically: two terminals cannot start the same card.
    from .store import now
    with store.connect() as db:
        timestamp = now()
        changed = db.execute("UPDATE cards SET status='running',started_at=?,updated_at=? WHERE id=? AND status='todo'",
                             (timestamp, timestamp, card_id)).rowcount
    if not changed:
        raise ValueError('이미 실행된 카드입니다.')
    process = None
    try:
        with tempfile.TemporaryDirectory(prefix='kanban-run-') as directory:
            last = Path(directory) / 'last.txt'
            command = (['codex', 'exec', '--json', '--output-last-message', str(last), '-']
                       if card['provider'] == 'codex' else ['claude', '-p', '--output-format', 'json'])
            # File-backed stdout avoids pipe deadlocks; raw tool events are not persisted to the DB.
            with tempfile.TemporaryFile() as output:
                process = subprocess.Popen(command, cwd=board['directory'], stdin=subprocess.PIPE,
                                           stdout=output, start_new_session=True)
                process.communicate((card['prompt'] or card['title']).encode())
                output.seek(0)
                raw = output.read().decode('utf-8', errors='replace')
            result, usage, session, failed = parse_output(
                card['provider'], raw, last.read_text() if last.exists() else '')
            failed = failed or process.returncode != 0
            store.update(card_id, status='failed' if failed else 'done',
                         result=result or ('CLI 실행 실패. 터미널 출력을 확인하세요.' if failed else ''),
                         usage=usage, session_id=session)
            print(result)
            return process.returncode or (1 if failed else 0)
    except KeyboardInterrupt:
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        store.update(card_id, status='interrupted', result='사용자가 실행을 중단했습니다.')
        return 130
    except (OSError, ValueError) as exc:
        store.update(card_id, status='failed', result=str(exc))
        raise
