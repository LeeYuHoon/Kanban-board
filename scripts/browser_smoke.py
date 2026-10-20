"""Exercise the UI using real browser clicks through Orca, against an empty QA DB.

Start the server with KANBAN_DB=.local/browser-qa.sqlite3 on port 4311 with --no-collect,
open its Orca browser tab, then pass that tab's --page ID.
No actual model is called; completion fixtures are explicitly marked as QA.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kanban.store import Store

parser = argparse.ArgumentParser()
parser.add_argument('--page', required=True)
parser.add_argument('--orca', default='orca')
parser.add_argument('--db', default='.local/browser-qa.sqlite3')
args = parser.parse_args()
db_path = Path(args.db).resolve()
if db_path.name != 'browser-qa.sqlite3':
    raise SystemExit('Use a dedicated browser-qa.sqlite3 database.')
store = Store(db_path)
if store.boards():
    raise SystemExit('QA database must be empty. Use a new test directory and server.')
qa_root = db_path.parent / 'qa-projects'
for name in ('alpha', 'beta'):
    (qa_root / name).mkdir(parents=True, exist_ok=True)
checks = []


def call(*command):
    completed = subprocess.run([args.orca, *command, '--page', args.page, '--json'],
                               text=True, capture_output=True, timeout=40)
    data = json.loads(completed.stdout)
    if not data.get('ok'):
        raise AssertionError(data)
    return data['result']


def snapshot():
    return call('snapshot')['snapshot']


def click(selector):
    call('eval', '--expression', f'document.querySelector({json.dumps(selector)}).scrollIntoView({{block:"center"}})')
    call('click', '--element', selector)
    snapshot()


def fill(selector, value):
    call('fill', '--element', selector, '--value', value)


def check(expression, label):
    result = call('eval', '--expression', f'Boolean({expression})')['result']
    assert result in ('true', True), (label, result, snapshot())
    checks.append(label)
    print('PASS ' + label, flush=True)


def wait(selector):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        exists = call('eval', '--expression', f'Boolean(document.querySelector({json.dumps(selector)}))')['result']
        if exists in ('true', True):
            snapshot()
            return
        time.sleep(.15)
    raise AssertionError('Timed out waiting for ' + selector)


def close():
    click('dialog[open] .close')


call('reload')
snapshot()
wait('#connection')
check("location.port === '4311' && !document.querySelector('#welcome').hidden", 'Empty onboarding')
click('#new-card')
check("document.querySelector('#project-dialog').open", 'New task without a project opens registration')
click('#project-dialog .cancel')
click('#welcome-project')
fill('#project-form [name=name]', 'QA · 디자인 시스템')
fill('#project-form [name=directory]', '/missing/kanban-qa-folder')
click('#project-form .primary')
wait('#project-form .form-error:not(:empty)')
check("document.querySelector('#project-form .form-error').textContent.includes('존재')", 'Invalid folder is rejected inline')
fill('#project-form [name=directory]', str(qa_root / 'alpha'))
click('#project-form .primary')
wait('#projects .project')
check("document.querySelector('#welcome').hidden && document.querySelector('#board-title').textContent === 'QA · 디자인 시스템'", 'Project created and selected')
click('#add-project')
fill('#project-form [name=name]', 'Duplicate')
fill('#project-form [name=directory]', str(qa_root / 'alpha'))
click('#project-form .primary')
wait('#project-form .form-error:not(:empty)')
check("document.querySelector('#project-form .form-error').textContent.includes('이미 등록')", 'Duplicate folder is rejected')
close()
click('#add-project')
fill('#project-form [name=name]', 'QA · 서비스 웹')
fill('#project-form [name=directory]', str(qa_root / 'beta'))
click('#project-form .primary')
wait('#projects .project:nth-child(2)')
check("document.querySelector('#project-count').textContent === '2'", 'Second project')
click('#projects .project:first-child')
click('#new-card')
check("JSON.stringify(Array.from(document.querySelector('[name=provider]').options, o=>o.value)) === '[\"claude\",\"codex\"]'", 'Only Claude Code and Codex selectable')
click('#save-card')
check("document.querySelector('#card-dialog').open && !document.querySelector('#card-form').checkValidity()", 'Empty title blocked by form validation')
fill('#card-form [name=title]', '컴포넌트 라이브러리 정리')
fill('#card-form [name=prompt]', '버튼과 입력 필드의 상태를 정리해주세요.\nQA 테스트 카드입니다.')
click('#save-card')
wait('#detail-dialog[open] .run-box')
check("document.querySelector('#detail-body').textContent.includes('browser-qa.sqlite3')", 'Created card has command bound to the correct DB')
click('.run-box .small-button')
check("!document.querySelector('#toast').hidden", 'Copy command feedback')
click('#edit-card')
fill('#card-form [name=title]', '컴포넌트 라이브러리 정리 · 수정')
call('select', '--element', '#card-form [name=provider]', '--value', 'codex')
click('#save-card')
wait('#detail-dialog[open]')
check("document.querySelector('#detail-title').textContent.endsWith('수정') && document.querySelector('#detail-body .badge').textContent === 'Codex'", 'Todo title and provider editing')
click('#duplicate-card')
fill('#card-form [name=title]', '접근성 점검')
call('select', '--element', '#card-form [name=provider]', '--value', 'claude')
click('#save-card')
wait('#detail-dialog[open]')
close()
check("document.querySelectorAll('.task').length === 2", 'Duplicate creates a separate Todo card')
click('[data-provider=codex]')
check("document.querySelectorAll('.task').length === 1 && document.querySelector('.task .badge').textContent === 'Codex'", 'Codex filter')
click('[data-provider=claude]')
check("document.querySelectorAll('.task').length === 1 && document.querySelector('.task .badge').textContent === 'Claude Code'", 'Claude Code filter')
click('[data-provider=all]')
fill('#search', '존재하지 않는 검색어')
check("document.querySelectorAll('.task').length === 0 && !document.querySelector('#filter-empty').hidden", 'Empty search state')
click('#reset-filters')
check("document.querySelectorAll('.task').length === 2", 'Reset filters')
fill('#search', '접근성')
check("document.querySelectorAll('.task').length === 1", 'Title search')
click('.task')
click('#archive-card')
wait('#detail-dialog:not([open])')
check("document.querySelectorAll('.task').length === 0", 'Archive removes active card')
click('#archived')
check("document.querySelectorAll('.task').length === 1", 'Archive view')
click('.task')
check("document.querySelector('#edit-card').hidden && !document.querySelector('.run-box')", 'Archived task has no edit or start controls')
click('#archive-card')
click('#reset-filters')
check("document.querySelectorAll('.task').length === 2", 'Unarchive restores card')
click('#projects .project:nth-child(2)')
check("document.querySelectorAll('.task').length === 0", 'Project isolation')
click('.column-add')
fill('#card-form [name=title]', '<img src=x onerror=alert(1)> QA')
fill('#card-form [name=prompt]', 'QA: HTML 입력은 텍스트로 표시됩니다.')
click('#save-card')
wait('#detail-dialog[open]')
check("document.querySelector('#detail-title').textContent.includes('<img') && !document.querySelector('#detail-title img')", 'User HTML is rendered as text')
close()
click('#all-projects')
check("document.querySelectorAll('.task').length === 3", 'All projects aggregates cards')
# Update only the dedicated QA database, to verify polling without paid model calls.
card = next(card for card in store.cards() if card['title'].endswith('수정'))
store.update(card['id'], status='running')
wait('.running .task')
click('.running .task')
check("document.querySelector('#detail-body').textContent.includes('작업 중') && document.querySelector('#edit-card').hidden", 'Running state in open detail')
store.update(card['id'], status='done', result='QA fixture: 접근성 검증 완료\n<button>안전한 텍스트</button>', usage={'input_tokens': 120, 'output_tokens': 32}, session_id='qa-session')
call('wait', '--text', 'QA fixture: 접근성 검증 완료')
snapshot()
check("document.querySelector('#detail-body').textContent.includes('QA fixture') && document.querySelector('.done .task')", 'Open detail and board refresh on completion')
click('.detail-section-heading .small-button')
check("!document.querySelector('#toast').hidden", 'Copy result feedback')
click('.usage summary')
check("document.querySelector('.usage').open && document.querySelector('.usage').textContent.includes('120')", 'Usage and session expansion')
close()
other = next(card for card in store.cards() if card['title'] == '접근성 점검')
store.update(other['id'], status='failed', result='QA fixture: 실행 실패')
wait('.failed .task')
check("document.querySelectorAll('.failed .task').length === 1", 'Failed state appears under attention')
store.update(other['id'], status='interrupted', result='QA fixture: 사용자 중단')
call('wait', '--text', '중단')
snapshot()
check("document.querySelector('.failed .task').textContent.includes('중단')", 'Interrupted state label')
click('#open-guide')
check("document.querySelector('#guide-dialog').open", 'Guide opens')
click('.guide-done')
check("!document.querySelector('#guide-dialog').open", 'Guide dismisses')
click('#projects .project:first-child')
call('reload')
snapshot()
check("document.querySelector('#board-title').textContent === 'QA · 디자인 시스템'", 'Selected project persists after reload')
call('exec', '--command', 'set viewport 390 844')
snapshot()
check("document.documentElement.scrollWidth <= innerWidth", 'Mobile page has no horizontal overflow')
click('#add-project')
check("document.querySelector('#project-dialog').open", 'Mobile project add is reachable')
close()
click('#new-card')
check("document.querySelector('#card-dialog').getBoundingClientRect().width <= innerWidth", 'Mobile task dialog fits viewport')
click('#card-dialog .cancel')
click('#open-guide')
close()
check("!document.querySelector('dialog[open]')", 'Mobile guide and close')
call('exec', '--command', 'set viewport 1440 1000')
snapshot()
click('#all-projects')
check("document.documentElement.scrollWidth <= innerWidth", 'Desktop page has no horizontal overflow')
report = {'checks': checks, 'passed': len(checks), 'model_calls': 0, 'database': str(db_path)}
(db_path.parent / 'browser-qa-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps(report, ensure_ascii=False, indent=2))
