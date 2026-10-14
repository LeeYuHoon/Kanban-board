"""Group agent-launched sessions under the user prompt that started them.

`codex exec` and Claude SDK (`claude -p`) runs are non-interactive: an agent
started them. Each is linked to the one interactive prompt that was running in
the same repository when it started. Ambiguous cases stay unlinked rather
than guessed. Source transcripts are only read, never modified.
"""
import configparser
import json
import os
import re
from pathlib import Path

SHELL_TOOLS = {'Bash', 'exec_command', 'shell', 'shell_command', 'local_shell', 'exec', 'unified_exec'}


def normalize_repo(url):
    """'git@github.com:A/b.git' and 'https://github.com/A/b' -> 'github.com/a/b'."""
    if not isinstance(url, str) or not url.strip():
        return None
    text = url.strip().rstrip('/')
    text = re.sub(r'\.git$', '', text)
    match = re.match(r'^[\w.+-]+@([^:/]+):(.+)$', text)
    if match:
        text = match.group(1) + '/' + match.group(2)
    text = re.sub(r'^[a-z][\w+.-]*://(?:[^@/]+@)?', '', text, flags=re.I)
    return text.lower()


def _origin_url(git_dir):
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    try:
        parser.read(os.path.join(git_dir, 'config'))
        return parser.get('remote "origin"', 'url', fallback=None)
    except (configparser.Error, OSError, UnicodeDecodeError):
        return None


def repo_from_directory(directory):
    """Repository identity for a folder, also for worktrees and deleted worktrees."""
    if not directory:
        return None
    path = Path(directory)
    for candidate in [path, *path.parents]:
        marker = candidate / '.git'
        try:
            if marker.is_dir():
                return normalize_repo(_origin_url(str(marker))) or 'path:' + str(candidate)
            if marker.is_file():
                text = marker.read_text(errors='ignore').strip()
                if text.startswith('gitdir:'):
                    git_dir = Path(text[7:].strip())
                    common = git_dir.parent.parent if git_dir.parent.name == 'worktrees' else git_dir
                    return normalize_repo(_origin_url(str(common))) or 'path:' + str(common.parent)
        except OSError:
            break
    if '/.claude/worktrees/' in str(path):  # Worktree folder already removed.
        root = str(path).split('/.claude/worktrees/')[0]
        return repo_from_directory(root) if Path(root).exists() else 'path:' + root
    return 'path:' + str(path)


def session_origin(path, provider, directory):
    """(launch, repo_key) from the transcript head: 'automated' for codex exec / Claude SDK runs."""
    launch, url = 'unknown', None
    try:
        with open(path, 'rb') as stream:
            for number, line in enumerate(stream):
                if number >= 40:
                    break
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                if provider == 'codex' and event.get('type') == 'session_meta':
                    payload = event.get('payload') if isinstance(event.get('payload'), dict) else {}
                    source = str(payload.get('source'))
                    launch = 'automated' if source == 'exec' or payload.get('originator') == 'codex_exec' else 'interactive'
                    git = payload.get('git') if isinstance(payload.get('git'), dict) else {}
                    url = git.get('repository_url')
                    break
                if provider == 'claude' and isinstance(event.get('entrypoint'), str):
                    launch = 'automated' if event['entrypoint'].startswith('sdk') else 'interactive'
                    break
    except OSError:
        pass
    return launch, normalize_repo(url) or repo_from_directory(directory)


def _used_shell(activity):
    try:
        tools = json.loads(activity or '{}').get('tools') or []
    except ValueError:
        return False
    return any(str(name).rsplit('.', 1)[-1] in SHELL_TOOLS for name in tools)


def _parent(db, card, launch):
    rows = db.execute('''SELECT c.id, t.turn_key, t.activity, t.started_at FROM cards c
        JOIN session_turns t ON t.provider=c.provider AND t.session_id=c.session_id
        WHERE c.source='session' AND c.launch=? AND c.repo_key=? AND c.id<>? AND c.created_at<=?
          AND t.started_at<=? AND COALESCE(t.finished_at,
              CASE WHEN t.status='running' THEN '9999' ELSE t.updated_at END)>=?''',
        (launch, card['repo_key'], card['id'], card['created_at'], card['created_at'], card['created_at'])).fetchall()
    # A nested launch must come from a shell command; for user prompts that only breaks ties.
    if launch == 'automated' or len({row['id'] for row in rows}) > 1:
        rows = [row for row in rows if _used_shell(row['activity'])]
    if len({row['id'] for row in rows}) != 1:
        return None
    return max(rows, key=lambda row: row['started_at'])


def link_sessions(store):
    """Idempotent: classify new session cards, then link automated ones still without a parent."""
    with store.connect() as db:
        pending = db.execute('''SELECT c.id, c.provider, c.source_path, b.directory FROM cards c
            JOIN boards b ON b.id=c.board_id WHERE c.source='session' AND c.launch IS NULL''').fetchall()
    for row in pending:
        launch, repo = session_origin(row['source_path'], row['provider'], row['directory'])
        with store.connect() as db:
            db.execute('UPDATE cards SET launch=?,repo_key=? WHERE id=?', (launch, repo, row['id']))
    linked = 0
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        orphans = db.execute('''SELECT id, repo_key, created_at FROM cards WHERE source='session'
            AND launch='automated' AND parent_id IS NULL AND repo_key IS NOT NULL''').fetchall()
        for card in orphans:
            # Prefer the user's own prompt; fall back to another automated run (nested launches).
            parent = _parent(db, card, 'interactive') or _parent(db, card, 'automated')
            if parent:
                db.execute('UPDATE cards SET parent_id=?,parent_turn_key=? WHERE id=?',
                           (parent['id'], parent['turn_key'], card['id']))
                linked += 1
        # Unlinked automated runs are shown in the repository's main project instead of a worktree project.
        db.execute('''UPDATE cards SET home_board_id=(SELECT p.board_id FROM cards p WHERE p.source='session'
                AND p.launch='interactive' AND p.repo_key=cards.repo_key ORDER BY p.updated_at DESC LIMIT 1)
            WHERE source='session' AND launch='automated' AND parent_id IS NULL AND home_board_id IS NULL''')
    return linked
