"""macOS installation registry and collector lifecycle.

Only the small system registration needs elevation. Services and databases run as
one explicitly recorded OS user. Tests pass an isolated registry directory to the
filesystem helpers; the CLI always uses the machine-wide path.
"""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import signal
import subprocess
import sys
import time
import uuid

SYSTEM = Path('/Library/Application Support/Kanban')
ROOT = Path(__file__).resolve().parent.parent
LABEL = 'com.kanban.collector'


def atomic(path, value, mode=0o600):
    path = Path(path)
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def registration(directory=None):
    directory = Path(directory) if directory is not None else SYSTEM
    path = directory / 'install.json'
    if path.is_symlink() or directory.is_symlink():
        raise ValueError('설치 등록 경로가 심볼릭 링크입니다. 설치를 중단합니다.')
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or data.get('mode') not in ('main', 'collector'):
        raise ValueError('설치 등록 정보가 손상되었습니다. 기존 데이터를 보존하고 설치를 확인하세요.')
    return data


def validate_alias(alias):
    if not isinstance(alias, str) or not alias.strip() or len(alias.strip()) > 80 or any(ord(c) < 32 for c in alias):
        raise ValueError('원격 별칭은 줄바꿈 없이 1~80자로 입력하세요.')
    return alias.strip()


def check(root=None, mode=None, required=False, directory=None, uid=None):
    data = registration(directory)
    if data is None:
        if required:
            raise ValueError('먼저 bin 폴더에서 ./kanban install을 실행하세요.')
        return None  # Existing, unregistered local installations remain usable.
    root = Path(root or ROOT).resolve()
    uid = os.getuid() if uid is None else uid
    if data['uid'] != uid or Path(data['root']) != root:
        raise ValueError('이 컴퓨터에는 이미 Kanban이 설치되어 있습니다: ' + data['root'])
    if mode and data['mode'] != mode:
        raise ValueError('설치 유형이 다릅니다. 기존 설치를 제거한 뒤 유형을 변경하세요.')
    configured = os.environ.get('KANBAN_DB')
    if configured and Path(configured).expanduser().resolve() != Path(data['db']):
        raise ValueError('설치에 등록된 DB와 다릅니다: ' + data['db'])
    return data


def write_registration(data, directory=None):
    directory = Path(directory) if directory is not None else SYSTEM
    if directory.is_symlink():
        raise ValueError('설치 경로가 심볼릭 링크입니다.')
    directory.mkdir(mode=0o755, parents=True, exist_ok=True)
    fd = os.open(directory / 'install.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        existing = registration(directory)
        if existing:
            raise ValueError('중복 설치할 수 없습니다. 기존 설치: ' + existing['root'])
        if data['mode'] == 'collector':
            endpoint = ('#!/bin/sh\nset -eu\n'
                + '[ "$(/usr/bin/id -u)" = ' + shlex.quote(str(data['uid'])) + ' ] || exit 1\n'
                + 'export PYTHONDONTWRITEBYTECODE=1\n'
                + 'export KANBAN_DB=' + shlex.quote(data['db']) + '\n'
                + 'cd ' + shlex.quote(data['root']) + '\n'
                + 'exec ' + shlex.quote(data['python']) + ' -m kanban.remote_rpc\n')
            atomic(directory / 'remote', endpoint, 0o755)
        atomic(directory / 'install.json', json.dumps(data, ensure_ascii=False, indent=2), 0o644)
    finally:
        os.close(fd)


def remove_registration(root, uid, directory=None):
    directory = Path(directory) if directory is not None else SYSTEM
    fd = os.open(directory / 'install.lock', os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        data = registration(directory)
        if not data or data['root'] != str(Path(root).resolve()) or data['uid'] != uid:
            raise ValueError('다른 설치의 등록은 제거할 수 없습니다.')
        (directory / 'remote').unlink(missing_ok=True)
        (directory / 'install.json').unlink()
        # Keep the shared lock inode, so simultaneous installers cannot bypass it.
    finally:
        os.close(fd)


def privileged(action, data):
    command = [sys.executable, str(Path(__file__).resolve()), '_system', action, json.dumps(data)]
    if os.geteuid() != 0:
        command = ['/usr/bin/sudo', *command]
    result = subprocess.run(command)
    if result.returncode:
        raise ValueError('컴퓨터 전체 설치 등록을 변경하지 못했습니다.')


def system_action(action, data):
    if os.geteuid() != 0:
        raise ValueError('설치 등록에는 관리자 권한이 필요합니다.')
    caller = int(os.environ.get('SUDO_UID', os.getuid()))
    if caller != data['uid']:
        raise ValueError('설치 사용자 정보가 일치하지 않습니다.')
    if action == 'remove':
        remove_registration(data['root'], caller)
        return
    if action != 'register' or data.get('mode') not in ('main', 'collector'):
        raise ValueError('설치 요청이 올바르지 않습니다.')
    root = Path(data['root']).resolve()
    if root == Path('/') or not (root / 'bin/kanban').is_file():
        raise ValueError('Kanban 프로젝트 폴더를 확인하세요.')
    if data['mode'] == 'collector':
        data['alias'] = validate_alias(data.get('alias'))
    elif 'alias' in data:
        raise ValueError('메인 설치에는 별칭을 사용하지 않습니다.')
    write_registration(data)


def check_legacy_processes():
    result = subprocess.run(['/bin/ps', '-axo', 'pid=,command='], capture_output=True, text=True, check=True)
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        try:
            words = shlex.split(parts[1])
        except ValueError:
            continue
        if any(words[i:i+3] in (['-m', 'kanban', 'serve'], ['-m', 'kanban', 'collector']) for i in range(len(words))):
            raise ValueError('기존 Kanban이 실행 중입니다 (PID ' + parts[0] + '). '
                             '기존 설치에서 ./kanban stop 후 설치하세요.')


def install(mode=None, alias=None):
    if sys.platform != 'darwin':
        raise ValueError('이번 버전은 macOS만 지원합니다.')
    existing = registration()
    if existing:
        raise ValueError('이미 설치되어 있습니다: ' + existing['root'] + '\n업데이트는 ./kanban update를 사용하세요.')
    if os.getuid() == 0:
        raise ValueError('sudo 없이 ./kanban install을 실행하세요. 등록 단계에서만 관리자 확인을 받습니다.')
    check_legacy_processes()
    if mode is None:
        print('설치 유형을 선택하세요.\n  1. 메인 — 로컬·원격 로그와 화면\n  2. 원격 전용 — 로그 수집·보관만 실행')
        choice = input('선택 [1/2]: ').strip()
        mode = {'1': 'main', '2': 'collector'}.get(choice)
    if mode not in ('main', 'collector'):
        raise ValueError('설치 유형을 선택하세요.')
    if mode == 'main' and alias is not None:
        raise ValueError('메인은 별칭을 입력하지 않습니다.')
    if mode == 'collector':
        alias = validate_alias(alias if alias is not None else input('원격 컴퓨터 별칭 (예: home-macStudio): '))
    state = ROOT / '.local'
    state.mkdir(mode=0o700, exist_ok=True)
    saved_db = state / 'db-path'
    db = os.environ.get('KANBAN_DB') or (saved_db.read_text().strip() if saved_db.exists() else '')
    db = state / 'kanban.sqlite3' if not db or db == '@default' else Path(db).expanduser()
    if not db.is_absolute():
        db = ROOT / db
    db = db.resolve()
    if db.exists():
        from .update_db import backup
        target = state / 'backups' / ('pre-install-' + time.strftime('%Y%m%d-%H%M%S') + '.sqlite3')
        target.parent.mkdir(exist_ok=True)
        backup(db, target)
        print('기존 DB 백업: ' + str(target))
    from .store import Store
    store = Store(db)
    id_file = state / 'device-id'
    previous_main = store.setting('main_id') if mode == 'main' else None
    device_id = id_file.read_text().strip() if id_file.exists() else previous_main or uuid.uuid4().hex
    atomic(id_file, device_id)
    data = dict(mode=mode, uid=os.getuid(), root=str(ROOT), python=sys.executable,
                db=str(db), id=device_id)
    if mode == 'collector':
        data['alias'] = alias
    atomic(state / 'db-path', str(db))
    print('컴퓨터 전체 중복 설치를 막기 위해 설치 등록에 관리자 확인이 필요합니다.')
    privileged('register', data)
    try:
        os.environ['KANBAN_DB'] = str(db)
        if mode == 'collector':
            start_collector(data)
        else:
            subprocess.run([str(ROOT / 'bin/kanban'), 'install-login'], check=True)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        # Keep the role and data: removing the registry could let a second role
        # start while launchd is still retrying the first service.
        raise ValueError('설치는 등록됐지만 실행에 실패했습니다. ./kanban logs로 확인하고 '
                         './kanban start로 다시 실행하세요. 제거는 ./kanban uninstall입니다.\n' + str(exc)) from exc
    print('설치 완료: ' + ('Main' if mode == 'main' else 'Remote / ' + alias))
    if mode == 'collector':
        print('메인 Kanban의 원격 컴퓨터 메뉴에서 이 컴퓨터를 선택하세요. 수집 이력은 자동 삭제하지 않습니다.')


def _plist():
    return Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')


def start_collector(data):
    target = 'gui/' + str(data['uid'])
    path = _plist()
    path.parent.mkdir(parents=True, exist_ok=True)
    environment = {'HOME': str(Path.home()), 'KANBAN_DB': data['db'], 'PYTHONDONTWRITEBYTECODE': '1'}
    for name in ('KANBAN_SESSION_ROOTS', 'CODEX_HOME', 'CLAUDE_CONFIG_DIR'):
        if os.environ.get(name):
            environment[name] = os.environ[name]
    if path.exists():
        old = plistlib.loads(path.read_bytes()).get('EnvironmentVariables', {})
        environment = dict(old, **environment)
    log = str(Path(data['root']) / '.local/collector.log')
    config = dict(Label=LABEL, ProgramArguments=[data['python'], '-m', 'kanban', 'collector'],
                  WorkingDirectory=data['root'], EnvironmentVariables=environment,
                  RunAtLoad=True, KeepAlive={'SuccessfulExit': False},
                  StandardOutPath=log, StandardErrorPath=log)
    stop_collector(data)
    from .store import Store
    store = Store(data['db'])
    store.set_setting('collector_status', '{}')
    atomic(path, plistlib.dumps(config).decode())
    result = subprocess.run(['launchctl', 'bootstrap', target, str(path)], capture_output=True, text=True)
    if result.returncode:
        raise ValueError('수집기 자동 실행 등록 실패: ' + result.stderr.strip())
    for _ in range(60):
        if collector_alive(store):
            head = subprocess.run(['git', '-C', data['root'], 'rev-parse', 'HEAD'], capture_output=True, text=True)
            if head.returncode == 0:
                atomic(Path(data['root']) / '.local/applied-head', head.stdout.strip())
            print('원격 수집기 실행 중 / ' + data['alias'])
            return
        time.sleep(0.25)
    raise ValueError('수집기가 응답하지 않습니다. ./kanban logs로 확인하세요.')


def stop_collector(data, remove=False):
    target = 'gui/' + str(data['uid']) + '/' + LABEL
    subprocess.run(['launchctl', 'bootout', target], capture_output=True)
    for _ in range(60):
        if subprocess.run(['launchctl', 'print', target], capture_output=True).returncode:
            stop_foreground_collector(data)
            if remove:
                _plist().unlink(missing_ok=True)
            return
        time.sleep(0.25)
    raise ValueError('수집기를 종료하지 못했습니다. 설치 등록과 데이터는 유지합니다.')


def process_birth(pid):
    return subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'lstart='],
                          capture_output=True, text=True).stdout.strip()


def stop_foreground_collector(data):
    path = Path(data['root']) / '.local/collector.lock'
    if not path.exists():
        return
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            pass
        record = json.loads(os.read(fd, 4096))
        pid = record.get('pid')
        if type(pid) is not int or pid <= 1 or not record.get('birth') or process_birth(pid) != record['birth']:
            raise ValueError('수집기 PID를 확인하지 못했습니다. 실행 중인 터미널에서 종료하세요.')
        os.kill(pid, signal.SIGTERM)
        for _ in range(80):
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError:
                time.sleep(0.25)
        raise ValueError('실행 중인 수집기를 종료하지 못했습니다.')
    finally:
        os.close(fd)


def collector_alive(store):
    from datetime import datetime, timezone
    status = json.loads(store.setting('collector_status', '{}'))
    try:
        return bool(status.get('active') and (datetime.now(timezone.utc) - datetime.fromisoformat(status['heartbeat'])).total_seconds() < 30)
    except (KeyError, ValueError, TypeError):
        return False


@contextmanager
def collector_lock(data):
    path = Path(data['root']) / '.local/collector.lock'
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Kanban 수집기가 이미 실행 중입니다.')
        os.ftruncate(fd, 0)
        os.write(fd, json.dumps({'pid': os.getpid(), 'birth': process_birth(os.getpid())}).encode())
        os.fsync(fd)
        yield
    finally:
        os.close(fd)


def remote_command(action, args):
    if args and action != 'uninstall':
        raise ValueError('원격 전용에는 포트나 추가 인자를 지정하지 않습니다.')
    data = check(mode='collector', required=True)
    os.environ['KANBAN_DB'] = data['db']
    from .store import Store
    store = Store(data['db'])
    if action in ('start', 'restart', 'install-login'):
        if args:
            raise ValueError('원격 전용에는 포트나 추가 인자를 지정하지 않습니다.')
        start_collector(data)
    elif action in ('stop', 'uninstall-login'):
        stop_collector(data, remove=action == 'uninstall-login')
        print('원격 수집기를 종료했습니다. 기록은 유지합니다.')
    elif action == 'foreground':
        from .remote_rpc import serve_collector
        serve_collector(data)
    elif action in ('status', 'doctor'):
        owner = json.loads(store.setting('collector_owner', '{}'))
        print('원격 전용 / ' + data['alias'])
        print('실행 중' if collector_alive(store) else '수집기 중지')
        print('메인 연결됨' if owner else '메인 연결 대기')
        print('DB: ' + data['db'])
        print('수집 상태: ' + store.setting('collector_status', '{}'))
    elif action == 'logs':
        path = Path(data['root']) / '.local/collector.log'
        path.touch(exist_ok=True)
        subprocess.run(['tail', '-n', '50', '-f', str(path)])
    elif action == 'reset-connection':
        if input('메인 연결만 초기화합니다. 수집 이력은 유지합니다. 계속하려면 reset 입력: ').strip() != 'reset':
            print('취소했습니다.')
            return
        store.set_setting('collector_owner', None)
        print('연결을 초기화했습니다. 새 메인에서 연결하세요.')
    elif action == 'uninstall':
        uninstall_collector(data, args)
    elif action == 'open':
        raise ValueError('원격 전용에는 웹 화면이 없습니다. 메인 컴퓨터에서 확인하세요.')
    else:
        raise ValueError('원격 전용에서 지원하지 않는 명령입니다: ' + action)


def uninstall_collector(data, args):
    if set(args) - {'--yes', '--keep-data'}:
        raise ValueError('원격 제거 옵션은 --yes, --keep-data입니다.')
    from .store import Store
    store = Store(data['db'])
    owner = json.loads(store.setting('collector_owner', '{}'))
    with store.connect() as db:
        pending = db.execute('SELECT COUNT(*) FROM sync_items WHERE seq>?', (owner.get('ack', 0),)).fetchone()[0]
    print(f'원격 전용 제거: {data["root"]}\n메인 저장 미확인 변경 항목: {pending}개\nCodex·Claude 원본 로그는 유지합니다.')
    keep = '--keep-data' in args
    if '--yes' not in args:
        choice = input('1. 앱만 제거 (수집 이력 보관)\n2. 앱과 수집 이력 제거\n0. 취소\n선택: ').strip()
        if choice == '0':
            return
        if choice not in ('1', '2'):
            raise ValueError('선택이 올바르지 않습니다.')
        keep = choice == '1'
        if input('계속하려면 delete 입력: ').strip() != 'delete':
            print('취소했습니다.')
            return
    stop_collector(data, remove=True)
    root, db_path = Path(data['root']), Path(data['db'])
    if root == Path.home() or root == Path('/'):
        raise ValueError('앱 폴더를 확인하세요.')
    if keep:
        destination = Path.home() / ('Kanban-data-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
        destination.mkdir(mode=0o700)
        shutil.copytree(root / '.local', destination / '.local')
        from .update_db import backup
        backup(db_path, destination / 'collector.sqlite3')
        print('이력 보관: ' + str(destination))
    privileged('remove', data)
    if not keep and not db_path.is_relative_to(root):
        for suffix in ('', '-wal', '-shm'):
            Path(str(db_path) + suffix).unlink(missing_ok=True)
    shutil.rmtree(root)
    print('원격 수집기를 제거했습니다.')


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '_system':
        system_action(sys.argv[2], json.loads(sys.argv[3]))
        return
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    install_parser = commands.add_parser('install')
    install_parser.add_argument('--mode', choices=('main', 'collector'))
    install_parser.add_argument('--alias')
    commands.add_parser('check')
    commands.add_parser('release')
    remote = commands.add_parser('remote-command')
    remote.add_argument('action')
    remote.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command == 'install':
        install(args.mode, args.alias)
    elif args.command == 'check':
        value = check()
        print(value['mode'] if value else 'main')
    elif args.command == 'release':
        value = check()
        if value:
            privileged('remove', value)
    else:
        remote_command(args.action, args.arguments)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, EOFError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
