"""Descriptions for skills, agents and MCP tools, shown when hovering usage chips.

Only text the agent itself was shown is used: Claude Code's skill / agent / MCP
listings recorded in its transcripts, and the frontmatter of installed SKILL.md
files. Nothing is invented; unknown names simply have no description.
"""
import ast
import json
import re
from pathlib import Path

from .store import now

CATALOG_VERSION = 3
MAX_DESCRIPTION = 400


def _short(text):
    text = ' '.join(str(text or '').split())
    return text[:MAX_DESCRIPTION].rstrip() + ('…' if len(text) > MAX_DESCRIPTION else '')


def _list(value):
    """Attachment fields are lists, or occasionally their Python/JSON string form."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        for parse in (json.loads, ast.literal_eval):
            try:
                parsed = parse(value)
                if isinstance(parsed, list):
                    return parsed
            except (ValueError, SyntaxError):
                continue
    return []


def server_key(name):
    """'claude.ai Google Drive' -> 'claude_ai_Google_Drive', the form used in mcp__<server>__<tool>."""
    return re.sub(r'[^A-Za-z0-9_-]', '_', str(name))


def _listing_lines(lines):
    for line in lines:
        # Names may contain ':' themselves (plugin:agent); the separator is the first ': '.
        match = re.match(r'^\s*-\s+(\S+?):\s+(.+)$', str(line))
        if match:
            # Agent listings end with '(Tools: ...)'; that is configuration, not a description.
            yield match.group(1).strip(), re.sub(r'\s*\(Tools:[^)]*\)\s*$', '', match.group(2)).strip()


def from_claude_event(event):
    """(kind, name, description) entries found in one Claude transcript record."""
    entries = []
    if not isinstance(event, dict):
        return entries
    attachment = event.get('attachment') if event.get('type') == 'attachment' else None
    if isinstance(attachment, dict):
        kind = attachment.get('type')
        if kind == 'skill_listing':
            entries += [('skill', name, text) for name, text in _listing_lines(str(attachment.get('content', '')).split('\n'))]
        elif kind == 'agent_listing_delta':
            entries += [('agent', name, text) for name, text in _listing_lines(_list(attachment.get('addedLines')))]
        elif kind == 'mcp_instructions_delta':
            for name, block in zip(_list(attachment.get('addedNames')), _list(attachment.get('addedBlocks'))):
                lines = [line for line in str(block).split('\n') if line.strip() and not line.startswith('#')]
                if lines:
                    entries.append(('mcp_server', server_key(name), re.sub(r'\*+', '', ' '.join(lines))))
        elif kind == 'deferred_tools_delta':
            for item in _list(attachment.get('surfacedDefinitions')):
                definition = item.get('definition') if isinstance(item, dict) else None
                if isinstance(definition, dict) and str(definition.get('name', '')).startswith('mcp__'):
                    entries.append(('mcp_tool', definition['name'], definition.get('description', '')))
    message = event.get('message')
    if event.get('type') == 'assistant' and isinstance(message, dict) and isinstance(message.get('content'), list):
        for item in message['content']:
            data = item.get('input') if isinstance(item, dict) and item.get('type') == 'tool_use' else None
            # A named teammate ('inq-fe') is an instance of an agent type; names only, no task text.
            if item.get('name') in ('Agent', 'Task') and isinstance(data, dict) \
                    and isinstance(data.get('name'), str) and isinstance(data.get('subagent_type'), str):
                entries.append(('agent_alias', data['name'], data['subagent_type']))
    return [(kind, str(name)[:200], _short(text)) for kind, name, text in entries if name and text]


def skill_description(path):
    """Frontmatter `description:`; supports plain, quoted and folded (>-, |) YAML values."""
    try:
        text = Path(path).read_text(errors='ignore')[:6000]
    except OSError:
        return None
    if not text.startswith('---'):
        return None
    end = text.find('\n---', 3)
    lines = text[3:end if end != -1 else None].split('\n')
    for index, line in enumerate(lines):
        match = re.match(r'^description:\s*(.*)$', line)
        if not match:
            continue
        value = match.group(1).strip()
        if value in ('>', '>-', '|', '|-', '>+', '|+', ''):
            value = ' '.join(next_line.strip() for next_line in
                             _indented(lines[index + 1:]))
        return _short(value.strip().strip('"').strip("'")) or None
    return None


def _indented(lines):
    for line in lines:
        if line.strip() and not line.startswith((' ', '\t')):
            break
        yield line


def project_skill_dirs(directories):
    """Skill folders inside the user's own projects: these names are private, not public listings."""
    result = []
    for directory in directories:
        root = Path(str(directory).split('/.claude/worktrees/')[0])
        result += [root / '.claude/skills', root / '.agents/skills', root / '.codex/skills']
    return [path for path in dict.fromkeys(result) if path.is_dir()]


def skill_dirs(directories):
    home = Path.home()
    bases = [home / '.claude/skills', home / '.codex/skills', home / '.codex/skills/.system', home / '.agents/skills']
    accounts = home / 'Library/Application Support/orca/codex-accounts'
    if accounts.is_dir():
        bases += [path for account in accounts.glob('*/home') for path in (account / 'skills', account / 'skills/.system')]
    for directory in directories:
        root = Path(str(directory).split('/.claude/worktrees/')[0])
        bases += [root / '.claude/skills', root / '.agents/skills', root / '.codex/skills']
    plugins = home / '.claude/plugins'
    if plugins.is_dir():
        bases += sorted({path.parent for path in plugins.glob('**/skills/*/SKILL.md')})
    seen, result = set(), []
    for base in bases:
        if base not in seen and base.is_dir():
            seen.add(base)
            result.append(base)
    return result


def refresh_skill_files(store):
    """Fill descriptions for installed skills; transcript listings already recorded are kept."""
    entries = []
    directories = [board['directory'] for board in store.boards()]
    for base in skill_dirs(directories):
        for path in base.glob('*/SKILL.md'):
            text = skill_description(path)
            if text:
                entries.append(('skill', path.parent.name, text))
    save(store, entries, 'skill_file', replace=False)
    # Project-only skills get no public search link in the UI.
    save(store, [('skill_scope', path.parent.name, 'project') for base in project_skill_dirs(directories)
                 for path in base.glob('*/SKILL.md')], 'skill_file')


def save(store, entries, source, replace=True):
    if not entries:
        return
    verb = 'INSERT OR REPLACE' if replace else 'INSERT OR IGNORE'
    with store.connect() as db:
        db.executemany(verb + ' INTO catalog VALUES (?,?,?,?,?)',
                       [(kind, name, text, source, now()) for kind, name, text in entries])


def backfill(store, paths):
    """One pass over known Claude transcripts, reading only records that can carry descriptions."""
    entries = []
    for path in paths:
        try:
            with open(path, 'rb') as stream:
                for line in stream:
                    if b'"attachment"' not in line and b'"subagent_type"' not in line:
                        continue
                    try:
                        entries += from_claude_event(json.loads(line))
                    except ValueError:
                        continue
        except OSError:
            continue
    save(store, entries, 'transcript')


USER_KINDS = ('tool', 'skill', 'mcp', 'agent')


def set_user_description(store, kind, name, text):
    """The user's own description; an empty text deletes it. Kept apart from recorded descriptions."""
    if kind not in USER_KINDS:
        raise ValueError('설명을 쓸 수 없는 항목입니다.')
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        raise ValueError('항목 이름이 올바르지 않습니다.')
    if not isinstance(text, str) or len(text) > 2000:
        raise ValueError('설명은 2,000자 이내로 입력하세요.')
    text = text.strip()
    with store.connect() as db:
        if text:
            db.execute('INSERT OR REPLACE INTO user_descriptions VALUES (?,?,?,?)', (kind, name, text, now()))
        else:
            db.execute('DELETE FROM user_descriptions WHERE kind=? AND name=?', (kind, name))
    return {'kind': kind, 'name': name, 'text': text, 'deleted': not text}


def version(store):
    with store.connect() as db:
        row = db.execute('SELECT COUNT(*), MAX(updated_at) FROM catalog').fetchone()
        user = db.execute('SELECT COUNT(*), MAX(updated_at) FROM user_descriptions').fetchone()
    return f'{row[0]}:{row[1]}:{user[0]}:{user[1]}'


def load(store):
    value = {'skill': {}, 'skill_scope': {}, 'agent': {}, 'agent_alias': {}, 'mcp_server': {}, 'mcp_tool': {}}
    with store.connect() as db:
        for row in db.execute('SELECT kind,name,description,source FROM catalog'):
            value.setdefault(row['kind'], {})[row['name']] = {'description': row['description'], 'source': row['source']}
        value['user'] = {}
        for row in db.execute('SELECT kind,name,text,updated_at FROM user_descriptions'):
            value['user'].setdefault(row['kind'], {})[row['name']] = {'text': row['text'], 'updated_at': row['updated_at']}
    return value
