"""Full-history search over every prompt and answer, grouped by the card shown on the board.

Agent-launched sessions are searched too; their matches count toward the parent card.
Plain substring matching (every space-separated term must appear) suits Korean better
than token-based full-text search, and the data is small enough to scan directly.
"""
from datetime import datetime, timezone

TARGETS = {'all': ('prompt', 'result'), 'prompt': ('prompt',), 'answer': ('result',)}
SNIPPET = 70
LIST_LIMIT = 300


def _bound(value, name):
    if value in (None, ''):
        return None
    if not isinstance(value, str):
        raise ValueError(f'{name} 값이 올바르지 않습니다.')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError(f'{name} 날짜 형식이 올바르지 않습니다.')
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat()


def _terms(query):
    if query is None:
        return []
    if not isinstance(query, str) or len(query) > 200:
        raise ValueError('검색어는 200자 이내로 입력하세요.')
    return [term for term in query.split() if term][:8]


def _snippet(text, terms):
    if not text:
        return ''
    lowered = text.lower()
    hits = [lowered.find(term.lower()) for term in terms]
    hits = [hit for hit in hits if hit >= 0]
    start = max(0, (min(hits) if hits else 0) - SNIPPET // 2)
    piece = ' '.join(text[start:start + SNIPPET * 2].split())
    return ('…' if start else '') + piece + ('…' if start + SNIPPET * 2 < len(text) else '')


def _like(term):
    return '%' + term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'


def run(store, query='', target='all', since=None, until=None, card_id=None):
    """{'cards': {root_id: summary}, 'matches': {owner_id: [turn_key]}, 'items': [...], 'total': n}."""
    terms = _terms(query)
    if target not in TARGETS:
        raise ValueError('검색 대상이 올바르지 않습니다.')
    since, until = _bound(since, '시작'), _bound(until, '종료')
    if not terms and not since and not until:
        raise ValueError('검색어나 기간을 지정하세요.')
    fields = TARGETS[target]
    conditions, params = [], []
    for term in terms:
        conditions.append('(' + ' OR '.join(f"t.{field} LIKE ? ESCAPE '\\'" for field in fields) + ')')
        params += [_like(term)] * len(fields)
    if since:
        conditions.append('t.started_at >= ?')
        params.append(since)
    if until:
        conditions.append('t.started_at < ?')
        params.append(until)
    cards = {card['id']: card for card in store.cards()}
    owners = {(card['provider'], card['session_id']): card for card in cards.values() if card.get('session_id')}
    with store.connect() as db:
        rows = db.execute('''SELECT * FROM (SELECT t.provider, t.session_id, t.turn_key, t.prompt, t.result, t.status,
                t.started_at, ROW_NUMBER() OVER (PARTITION BY t.provider, t.session_id ORDER BY t.started_at, t.turn_key) AS number
                FROM session_turns t) t WHERE ''' + (' AND '.join(conditions) or '1') +
            ' ORDER BY t.started_at DESC, t.turn_key DESC', params).fetchall()
    found = [(owners.get((row['provider'], row['session_id'])), dict(row)) for row in rows]
    # Manual cards that have no recorded turns are searched by their own prompt / result.
    with_turns = {(row['provider'], row['session_id']) for row in rows}
    for card in cards.values():
        if card['source'] != 'manual' or (card['provider'], card.get('session_id')) in with_turns:
            continue
        stamp = card.get('started_at') or card['created_at']
        texts = {'prompt': card['prompt'] or card['title'], 'result': card['result'] or ''}
        if all(any(term.lower() in texts[field].lower() for field in fields) for term in terms) \
                and (not since or stamp >= since) and (not until or stamp < until):
            found.append((card, {'turn_key': 'manual-' + card['id'], 'prompt': texts['prompt'], 'result': texts['result'],
                                 'status': card['status'], 'started_at': stamp, 'number': 1}))
    summary, matches, items = {}, {}, []
    for owner, row in found:
        if owner is None:
            continue
        root = cards.get(owner.get('root_id')) or owner
        if card_id and owner['id'] != card_id:
            continue
        matches.setdefault(owner['id'], []).append(row['turn_key'])
        entry = summary.setdefault(root['id'], {'count': 0, 'children': 0, 'first': None})
        entry['count'] += 1
        if owner['id'] != root['id']:
            entry['children'] += 1
        prompt_hit = not terms or all(term.lower() in (row['prompt'] or '').lower() for term in terms)
        item = {'root_id': root['id'], 'card_id': owner['id'], 'child': owner['id'] != root['id'],
                'provider': owner['provider'], 'turn_key': row['turn_key'], 'number': row['number'],
                'status': row['status'], 'started_at': row['started_at'],
                'prompt': _snippet(row['prompt'], terms) if terms else ' '.join((row['prompt'] or '').split())[:SNIPPET * 2],
                'answer': _snippet(row['result'], terms) if terms and 'result' in fields and not prompt_hit else ''}
        if entry['first'] is None:  # Rows are newest first.
            entry['first'] = item
        if len(items) < LIST_LIMIT:
            items.append(item)
    return {'cards': summary, 'matches': matches, 'items': items, 'total': sum(len(keys) for keys in matches.values()),
            'terms': terms, 'truncated': sum(len(keys) for keys in matches.values()) > LIST_LIMIT}
