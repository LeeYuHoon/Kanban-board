"""Verify update backups and retire only old automatic snapshots."""
import re
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

AUTO_BACKUP = re.compile(r'pre-(?:update|schema)-\d{8}-\d{6}\.sqlite3\.[A-Za-z0-9]{6}\Z')


def inspect(path):
    # WAL-mode snapshots need a writable connection to create their own -shm file.
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=rw', uri=True)) as db:
        result = db.execute('PRAGMA quick_check').fetchone()[0]
        if result != 'ok':
            raise ValueError(f'DB 무결성 검사 실패: {result}')
        cards = db.execute('SELECT COUNT(*) FROM cards').fetchone()[0]
        schema = tuple(db.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name"))
    return cards, schema


def backup(source, target):
    try:
        with closing(sqlite3.connect(Path(source).resolve().as_uri() + '?mode=rw', uri=True)) as original:
            with closing(sqlite3.connect(target)) as copy:
                original.backup(copy)
                # Keep the snapshot self-contained, without adjacent WAL files.
                copy.execute('PRAGMA journal_mode=DELETE')
        inspect(target)
    except Exception:
        for suffix in ('', '-wal', '-shm'):
            Path(str(target) + suffix).unlink(missing_ok=True)
        raise


def verify_and_protect(source, target):
    old_count, old_schema = inspect(target)
    new_count, new_schema = inspect(source)
    if new_count < old_count:
        raise ValueError(f'업데이트 후 카드 수가 줄었습니다: {old_count} → {new_count}')
    target = Path(target)
    if new_schema != old_schema:
        protected = target.with_name(target.name.replace('pre-update-', 'pre-schema-', 1))
        target.rename(protected)
        target = protected
    return target


def prune(directory):
    directory = Path(directory)
    snapshots = sorted((path for path in directory.iterdir()
                        if AUTO_BACKUP.fullmatch(path.name) and path.is_file() and not path.is_symlink()),
                       key=lambda path: path.stat().st_mtime_ns, reverse=True)
    keep = set(snapshots[:3])
    protected = next((path for path in snapshots if path.name.startswith('pre-schema-')), None)
    if protected:
        keep.add(protected)
    for path in snapshots:
        if path not in keep:
            path.unlink()
            for suffix in ('-wal', '-shm'):
                Path(str(path) + suffix).unlink(missing_ok=True)
    return len(snapshots) - len(keep)


def main(argv):
    try:
        action, *args = argv
        if action == 'backup' and len(args) == 2:
            backup(*args)
        elif action == 'verify' and len(args) == 2:
            print(verify_and_protect(*args))
        elif action == 'prune' and len(args) == 1:
            print(f'오래된 자동 백업 {prune(args[0])}개 정리')
        else:
            raise ValueError('update_db: backup SOURCE TARGET | verify SOURCE BACKUP | prune DIRECTORY')
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
