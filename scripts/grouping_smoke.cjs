// Agent-launched session grouping + collapsible prompts in real Chrome. Synthetic logs, temp DB and vault only.
// npm install --prefix .local/browser-tools --no-audit --no-fund playwright
const {chromium} = require('../.local/browser-tools/node_modules/playwright');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const fixture = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'kanban-grouping-')));
const codex = path.join(fixture, 'codex'), claude = path.join(fixture, 'claude');
fs.mkdirSync(codex); fs.mkdirSync(path.join(claude, 'p'), {recursive: true});
const project = path.join(fixture, 'workspace');
fs.mkdirSync(path.join(project, '.git'), {recursive: true});
fs.writeFileSync(path.join(project, '.git', 'config'), '[remote "origin"]\n\turl = https://github.com/Example/workspace.git\n');
const vault = path.join(fixture, 'Vault'); fs.mkdirSync(path.join(vault, '.obsidian'), {recursive: true});
const out = path.join(root, '.local', 'grouping-qa'); fs.mkdirSync(out, {recursive: true});
let clock = Date.now() - 20 * 60 * 1000;
const stamp = () => new Date(clock += 1000).toISOString();
const append = (file, event) => fs.appendFileSync(file, JSON.stringify(event) + '\n');
const ce = (type, payload) => ({timestamp: stamp(), type, payload});
const git = {repository_url: 'https://github.com/Example/workspace.git'};
const parent = path.join(codex, 'parent.jsonl');
append(parent, ce('session_meta', {id: 'parent-session', cwd: project, source: 'cli', originator: 'codex-tui', timestamp: stamp(), git}));
append(parent, ce('event_msg', {type: 'task_started'}));
append(parent, ce('response_item', {type: 'message', role: 'user', content: [{type: 'input_text', text: '부모 작업: 하위 에이전트 세 개를 돌려줘'}]}));
append(parent, ce('response_item', {type: 'function_call', name: 'exec_command', arguments: '{"cmd":"codex exec ..."}'}));
for (let n = 1; n <= 3; n++) {
  const file = path.join(codex, `child${n}.jsonl`);
  append(file, ce('session_meta', {id: `child-session-${n}`, cwd: path.join(project, '.claude/worktrees', `w${n}`), source: 'exec', originator: 'codex_exec', timestamp: stamp(), git}));
  append(file, ce('event_msg', {type: 'task_started'}));
  append(file, ce('response_item', {type: 'message', role: 'user', content: [{type: 'input_text', text: `하위 작업 ${n}: 자동 실행 요청`}]}));
  append(file, ce('event_msg', {type: 'task_complete', last_agent_message: `하위 작업 ${n} 결과`}));
}
append(parent, ce('event_msg', {type: 'task_complete', last_agent_message: '세 하위 작업을 모두 마쳤습니다.'}));
append(parent, ce('event_msg', {type: 'task_started'}));
append(parent, ce('response_item', {type: 'message', role: 'user', content: [{type: 'input_text', text: '두 번째 질문'}]}));
append(parent, ce('event_msg', {type: 'task_complete', last_agent_message: '긴 답변\n'.repeat(80)}));
append(path.join(claude, 'p', 'other.jsonl'), {type: 'user', timestamp: stamp(), cwd: project, sessionId: 'claude-other', entrypoint: 'cli', uuid: 'u1', message: {content: '별개의 Claude 대화'}});
const port = 4314, url = `http://127.0.0.1:${port}`;
const env = {...process.env, KANBAN_DB: path.join(fixture, 'test.sqlite3'), KANBAN_SESSION_ROOTS: JSON.stringify({codex: [codex], claude: [claude]})};
let server, browser, serverOutput = '';
const checks = [];
function passed(label) { checks.push(label); console.log('PASS ' + label); }
async function startServer() {
  server = spawn('python3', ['-m', 'kanban', 'serve', '--port', String(port)], {cwd: root, env, stdio: ['ignore', 'pipe', 'pipe']});
  server.stdout.on('data', d => { serverOutput += d; }); server.stderr.on('data', d => { serverOutput += d; });
  for (let i = 0; i < 100; i++) {
    if (server.exitCode !== null) throw new Error(serverOutput);
    try { const s = await (await fetch(url + '/api/state')).json(); if (s.cards.length === 5 && s.cards.filter(c => c.parent_id).length === 3) return s; } catch {}
    await new Promise(r => setTimeout(r, 100));
  }
  throw new Error('Server did not start: ' + serverOutput);
}
async function stopServer() { if (!server || server.exitCode !== null) return; const p = new Promise(r => server.once('exit', r)); server.kill('SIGINT'); await p; }
(async () => {
  try {
    await startServer();
    browser = await chromium.launch({headless: true, executablePath: process.env.KANBAN_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    await page.goto(url); await page.locator('.task').first().waitFor();
    assert.equal(await page.locator('.task').count(), 2);
    assert.equal(await page.locator('#projects .project').count(), 1);
    assert.equal(await page.locator('#project-count').textContent(), '1');
    const parentCard = page.locator('.task').filter({hasText: '두 번째 질문'});
    assert((await parentCard.textContent()).includes('에이전트 자동 실행 3개 포함'));
    passed('Agent-launched runs are folded into their parent card; worktree projects are hidden');

    await page.locator('#search').fill('하위 작업 2');
    await page.locator('#search-count', {hasText: '카드 1개'}).waitFor();
    assert.equal(await page.locator('.task').count(), 1);
    assert((await page.locator('.task').textContent()).includes('두 번째 질문'));
    await page.locator('#search').fill('');
    await page.waitForFunction(() => document.querySelectorAll('#board .task').length === 2);
    passed("Searching a child session's prompt finds the parent card");

    await parentCard.click();
    await page.locator('.history-turn').nth(1).waitFor();
    assert.equal(await page.locator('.history-turn').count(), 2);
    assert.equal(await page.locator('.turn-body:visible').count(), 0);
    const older = page.locator('.history-turn').nth(1);
    assert((await older.locator('.turn-toggle').textContent()).includes('부모 작업'));
    assert((await older.locator('.turn-badge').textContent()).includes('하위 세션 3'));
    const box = await page.locator('#detail-dialog').boundingBox();
    assert(box.height < 900);
    await page.locator('#detail-dialog').screenshot({path: path.join(out, 'collapsed.png')});
    passed('Prompts start collapsed with a one-line preview, status and child badge');

    await older.locator('.turn-toggle').click();
    assert.equal(await older.locator('.turn-toggle').getAttribute('aria-expanded'), 'true');
    assert(await older.locator('.turn-answer').isVisible());
    await page.waitForTimeout(4000);  // Survives the 3-second poll re-render.
    assert(await older.locator('.turn-answer').isVisible());
    assert(!(await page.locator('.history-turn').first().locator('.turn-answer').isVisible()));
    passed('Clicking a prompt expands it and it stays open across live refreshes');

    await page.getByRole('button', {name: '모두 펼치기'}).click();
    assert.equal(await page.locator('.turn-body:visible').count(), 2);
    await page.getByRole('button', {name: '모두 접기'}).click();
    assert.equal(await page.locator('.turn-body:visible').count(), 0);
    passed('Expand all / collapse all');

    await older.locator('.turn-toggle').click();
    await older.locator('.launched-toggle').click();
    assert.equal(await older.locator('.launched-row').count(), 3);
    // A running child changes while the list is open: prompt and list stay open across the re-render.
    const child1 = path.join(codex, 'child1.jsonl'), before = await older.getAttribute('data-signature');
    append(child1, ce('event_msg', {type: 'task_started'}));
    append(child1, ce('response_item', {type: 'message', role: 'user', content: [{type: 'input_text', text: '하위 작업 1 후속 요청'}]}));
    await page.waitForFunction(old => document.querySelectorAll('.history-turn')[1]?.dataset.signature !== old, before, {timeout: 20000});
    assert(await older.locator('.launched-list').isVisible());
    assert(await older.locator('.turn-answer').isVisible());
    const rowText = await older.locator('.launched-row').first().textContent();
    assert(!rowText.includes('완료') && /진행 중|상태 확인/.test(rowText));  // Fixture clock is in the past: stale running.
    passed('Open prompt and child list survive a live update from a running child session');
    const order = await older.locator('.turn-body > h4, .turn-body > .activity-panel > h4').allTextContents();
    assert.deepEqual(order, ['요청', '답변', '스킬 / MCP / 에이전트']);
    await page.locator('#detail-dialog').screenshot({path: path.join(out, 'children.png')});
    await older.locator('.launched-row').nth(1).click();
    await page.locator('.parent-link').waitFor();
    await page.locator('.history-turn').first().locator('.turn-toggle').click();
    assert.equal(await page.locator('.history-turn').first().locator('.turn-request').textContent(), '하위 작업 2: 자동 실행 요청');
    assert.equal(await page.locator('.history-turn').first().locator('.turn-answer').textContent(), '하위 작업 2 결과');
    await page.locator('.parent-link').click();
    await page.locator('.history-turn .turn-badge').waitFor();
    assert((await page.locator('#detail-title').textContent()).includes('두 번째 질문'));
    passed('Child sessions are listed under the launching prompt; open one and return to the parent');
    await page.locator('#detail-dialog .close').click();

    await page.locator('#obsidian-settings').click();
    const row = page.locator('.obsidian-project').filter({hasText: 'workspace'});
    await row.getByText('자동 저장', {exact: true}).click();
    await row.locator('input[name=vault]').fill(vault);
    await row.locator('.op-save').click();
    await row.locator('.op-status').filter({hasText: '새 노트 2'}).waitFor({timeout: 20000});
    await page.locator('#obsidian-dialog .cancel').click();
    const notes = fs.readdirSync(path.join(vault, 'Kanban'), {recursive: true}).filter(name => name.endsWith('.md'));
    assert.equal(notes.length, 2);
    const parentNote = fs.readFileSync(path.join(vault, 'Kanban', notes.find(name => name.includes('Codex'))), 'utf8');
    assert(parentNote.includes('### 에이전트가 실행한 하위 세션 (3)') && parentNote.includes('하위 작업 3 결과'));
    assert(parentNote.indexOf('세 하위 작업을 모두 마쳤습니다.') < parentNote.indexOf('하위 작업 1: 자동 실행 요청'));
    passed('Obsidian: one note for the parent with child sessions under the launching prompt');

    await page.setViewportSize({width: 390, height: 844});
    await parentCard.click();
    await page.locator('.history-turn').first().waitFor();
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    const dialogOverflow = await page.locator('#detail-dialog').evaluate(node => node.scrollWidth <= node.clientWidth);
    assert(dialogOverflow);
    await page.screenshot({path: path.join(out, 'mobile.png')});
    passed('Mobile: collapsed prompts and badges fit without horizontal overflow');
    assert.deepEqual(errors, []);
    passed('No browser JavaScript errors');
    fs.writeFileSync(path.join(out, 'report.json'), JSON.stringify({passed: checks.length, checks, model_calls: 0}, null, 2));
    console.log(JSON.stringify({passed: checks.length}, null, 2));
  } finally {
    if (browser) await browser.close();
    await stopServer();
    fs.rmSync(fixture, {recursive: true, force: true});
  }
})().catch(e => { console.error(e); console.error(serverOutput); process.exitCode = 1; });
