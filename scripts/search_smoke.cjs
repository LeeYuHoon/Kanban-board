// Search (all prompts, text and/or period) in real Chrome. Synthetic logs and a temporary DB only.
// npm install --prefix .local/browser-tools --no-audit --no-fund playwright
const {chromium} = require('../.local/browser-tools/node_modules/playwright');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const fixture = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'kankan-search-')));
const codex = path.join(fixture, 'codex'); fs.mkdirSync(codex);
const repo = path.join(fixture, 'shop'); fs.mkdirSync(path.join(repo, '.git'), {recursive: true});
fs.writeFileSync(path.join(repo, '.git', 'config'), '[remote "origin"]\n\turl = https://github.com/Example/shop.git\n');
const other = path.join(fixture, 'blog'); fs.mkdirSync(other);
const out = path.join(root, '.local', 'search-qa'); fs.mkdirSync(out, {recursive: true});
const day = 86400000, now = Date.now();
const append = (file, event) => fs.appendFileSync(file, JSON.stringify(event) + '\n');
const ev = (time, type, payload) => ({timestamp: new Date(time).toISOString(), type, payload});
const git = {repository_url: 'https://github.com/Example/shop.git'};
function session(name, id, cwd, source, turns, extra = {}) {
  const file = path.join(codex, name + '.jsonl');
  append(file, ev(turns[0][0] - 1000, 'session_meta', {id, cwd, source, timestamp: new Date(turns[0][0] - 1000).toISOString(), git, ...extra}));
  for (const [time, prompt, answer, tool] of turns) {
    append(file, ev(time, 'event_msg', {type: 'task_started'}));
    append(file, ev(time + 10, 'response_item', {type: 'message', role: 'user', content: [{type: 'input_text', text: prompt}]}));
    if (tool) append(file, ev(time + 20, 'response_item', {type: 'function_call', name: 'exec_command', arguments: '{}'}));
    append(file, ev(time + 60000, 'event_msg', {type: 'task_complete', last_agent_message: answer}));
  }
}
// Old prompt (10 days ago) is not the card's latest; its answer holds another term.
session('shop', 'shop-session', repo, 'cli', [
  [now - 10 * day, '결제 화면에서 쿠폰 금액이 0원으로 보여', '결제 요약 API 캐시가 원인입니다'],
  [now - 3 * day, '하위 작업 돌려줘', '하위 작업을 마쳤습니다', true],
  [now - 3600000, '오늘의 최신 요청', '정산 리포트를 정리했습니다']]);
session('child', 'child-session', path.join(repo, '.claude/worktrees/w1'), 'exec', [[now - 3 * day + 5000, '자동 실행: 환불 콜백 테스트', 'PG 응답 확인']], {originator: 'codex_exec'});
session('blog', 'blog-session', other, 'cli', [[now - 2 * day, '블로그 결제 위젯 문구 바꿔줘', '위젯 문구를 수정했습니다']]);
const port = 4315, url = `http://127.0.0.1:${port}`;
const env = {...process.env, KANBAN_DB: path.join(fixture, 'test.sqlite3'), KANKAN_SESSION_ROOTS: JSON.stringify({codex: [codex]})};
let server, browser, serverOutput = '';
const checks = [];
function passed(label) { checks.push(label); console.log('PASS ' + label); }
async function startServer() {
  server = spawn('python3', ['-m', 'kanban', 'serve', '--port', String(port)], {cwd: root, env, stdio: ['ignore', 'pipe', 'pipe']});
  server.stdout.on('data', d => { serverOutput += d; }); server.stderr.on('data', d => { serverOutput += d; });
  for (let i = 0; i < 100; i++) {
    if (server.exitCode !== null) throw new Error(serverOutput);
    try { const s = await (await fetch(url + '/api/state')).json(); if (s.cards.length === 3 && s.cards.some(c => c.parent_id)) return; } catch {}
    await new Promise(r => setTimeout(r, 100));
  }
  throw new Error('Server did not start: ' + serverOutput);
}
async function stopServer() { if (!server || server.exitCode !== null) return; const p = new Promise(r => server.once('exit', r)); server.kill('SIGINT'); await p; }
(async () => {
  try {
    await startServer();
    browser = await chromium.launch({headless: true, executablePath: process.env.KANKAN_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    const tasks = () => page.locator('#board .task');
    const waitTasks = n => page.waitForFunction(count => !document.querySelector('#board').hidden && document.querySelectorAll('#board .task').length === count, n, {timeout: 8000});
    // Wait for the results of the current query (not the previous one still on screen).
    const settle = q => page.waitForFunction(text => { const t = document.querySelector('#search-count').textContent; return t.includes(text) && t.includes('카드') && !t.includes('검색 중'); }, `‘${q}’`, {timeout: 8000});
    await page.goto(url); await tasks().first().waitFor();
    assert.equal(await tasks().count(), 2);
    const input = await page.locator('#search').boundingBox(), icon = await page.locator('.search-icon').boundingBox();
    assert(icon.x > input.x + input.width - 40, 'magnifier sits at the right end');
    assert.equal(await page.locator('#search').evaluate(n => getComputedStyle(n).paddingLeft), '12px');
    passed('Search panel shows; magnifier is at the right end and does not overlap text');

    await page.locator('#search').fill('쿠폰');
    await settle('쿠폰');
    await waitTasks(1);
    const card = tasks().first();
    assert((await card.textContent()).includes('일치 프롬프트 1개'));
    assert.equal(await card.locator('.snippet mark').first().textContent(), '쿠폰');
    await page.locator('#search-count', {hasText: '카드 1개 · 일치 프롬프트 1개'}).waitFor();
    passed('An old prompt (not the card\'s latest) is found, with match count and highlighted snippet');

    await page.locator('#search').fill('환불 콜백');
    await settle('환불 콜백');
    await waitTasks(1);
    assert((await tasks().first().textContent()).includes('하위 세션 1개 포함'));
    passed('Text inside an agent-launched sub-session finds the parent card');

    await page.locator('#search').fill('캐시');
    await settle('캐시');
    await waitTasks(1);
    await page.locator('#search-target').selectOption('prompt');
    await waitTasks(0);
    assert(await page.locator('#filter-empty').isVisible());
    await page.locator('#search-target').selectOption('answer');
    await waitTasks(1);
    passed('Search target: requests only / answers only');

    await page.locator('#search-target').selectOption('all');
    await page.locator('#search').fill('결제');
    await settle('결제');
    await waitTasks(2);
    await page.locator('[data-period="7d"]').click();
    await waitTasks(1);
    assert((await tasks().first().textContent()).includes('블로그'));
    await page.locator('[data-period="30d"]').click();
    await waitTasks(2);
    passed('Period chips narrow results by when the prompt was asked');

    await page.locator('#search').fill('');
    await page.locator('[data-period="today"]').click();
    await waitTasks(1);
    assert((await page.locator('#search-count').textContent()).includes('오늘'));
    const from = new Date(now - 11 * day), to = new Date(now - 9 * day);
    const value = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    await page.locator('#search-from').fill(value(from));
    await page.locator('#search-to').fill(value(to));
    await page.locator('#search-to').dispatchEvent('change');
    await waitTasks(1);
    assert.equal(await page.locator('[data-period="custom"]').getAttribute('aria-pressed'), 'true');
    assert((await tasks().first().textContent()).includes('일치 프롬프트 1개'));
    passed('Period without words, and a custom date range');

    await page.locator('#search-reset').click();
    await waitTasks(2);
    assert(await page.locator('#search-summary').isHidden());
    await page.locator('#search').fill('결제');
    await settle('결제');
    await waitTasks(2);
    await page.locator('[data-view="prompts"]').click();
    await page.locator('.result').first().waitFor();
    assert(await page.locator('#board').isHidden());
    assert.equal(await page.locator('.result').count(), 2);
    assert.equal(await page.locator('.result-day').count(), 2);
    assert((await page.locator('.result').nth(1).textContent()).includes('프롬프트 1'));
    await page.screenshot({path: path.join(out, 'prompts.png')});
    passed('Prompt list view groups matching prompts by date');

    await page.locator('.result').nth(1).locator('.open').click();
    await page.locator('#detail-dialog').waitFor();
    await page.locator('.match-only').waitFor();
    assert.equal(await page.locator('.history-turn').count(), 1);
    const turn = page.locator('.history-turn').first();
    assert(await turn.locator('.turn-answer').isVisible());
    assert.equal(await turn.locator('.turn-request mark').first().textContent(), '결제');
    await page.locator('#detail-dialog').screenshot({path: path.join(out, 'detail.png')});
    await page.locator('.match-only input').uncheck();
    await page.waitForFunction(() => document.querySelectorAll('.history-turn').length === 3);
    assert(await page.locator('.history-turn.matched .turn-answer').isVisible());
    passed('Opening a result expands and highlights the matching prompt; "일치만 보기" toggles the rest');
    await page.locator('#detail-dialog .close').click();

    await page.locator('[data-view="cards"]').click();
    await page.locator('#projects .project', {hasText: 'shop'}).click();
    await waitTasks(1);
    await page.locator('#search-scope').selectOption('all');
    await waitTasks(2);
    passed('Scope: current project vs all projects');

    await page.locator('#board').click({position: {x: 5, y: 5}});
    await page.keyboard.press('/');
    assert.equal(await page.evaluate(() => document.activeElement.id), 'search');
    await page.keyboard.press('Escape');
    await waitTasks(1);
    assert.equal(await page.locator('#search').inputValue(), '');
    passed('"/" focuses search and Esc clears it');

    await page.setViewportSize({width: 390, height: 844});
    assert(await page.locator('.period').isHidden());
    await page.locator('#period-toggle').click();
    assert(await page.locator('.period').isVisible());
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({path: path.join(out, 'mobile.png')});
    passed('Mobile: period row behind a button, no horizontal overflow');
    await page.setViewportSize({width: 1440, height: 1000});
    await page.locator('#all-projects').click();
    await page.locator('#search').fill('결제');
    await settle('결제');
    await waitTasks(2);
    await page.screenshot({path: path.join(out, 'cards.png')});
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
