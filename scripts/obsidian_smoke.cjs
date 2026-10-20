// End-to-end per-project Obsidian export in real Chrome. Temporary DB, synthetic logs and temporary vaults only.
// npm install --prefix .local/browser-tools --no-audit --no-fund playwright
const {chromium} = require('../.local/browser-tools/node_modules/playwright');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const fixture = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'kanban-obsidian-')));
const codex = path.join(fixture, 'codex'), claude = path.join(fixture, 'claude');
fs.mkdirSync(codex); fs.mkdirSync(path.join(claude, 'project'), {recursive: true});
const project = path.join(fixture, 'workspace'); fs.mkdirSync(project);
const side = path.join(fixture, 'side'); fs.mkdirSync(side);
const vault = path.join(fixture, 'Test Vault'); fs.mkdirSync(path.join(vault, '.obsidian'), {recursive: true});
const vault2 = path.join(fixture, 'Second Vault'); fs.mkdirSync(path.join(vault2, '.obsidian'), {recursive: true});
fs.writeFileSync(path.join(vault, '내 기존 노트.md'), '사용자 노트');
const plain = path.join(fixture, 'not-a-vault'); fs.mkdirSync(plain);
const out = path.join(root, '.local', 'obsidian-qa'); fs.mkdirSync(out, {recursive: true});
const codexFile = path.join(codex, 'native.jsonl'), sideFile = path.join(codex, 'side.jsonl'), claudeFile = path.join(claude, 'project', 'native.jsonl');
const append = (file, event) => fs.appendFileSync(file, JSON.stringify(event) + '\n');
// Monotonic clock: synthetic events written in the same millisecond would have an ambiguous order.
let clock = Date.now() - 60000;
const stamp = () => new Date(clock = Math.max(clock + 50, Date.now() - 55000)).toISOString();
const ce = (type, payload) => ({timestamp: stamp(), type, payload});
const cl = (type, extra) => ({type, timestamp: stamp(), cwd: project, sessionId: 'qa-claude', entrypoint: 'cli', ...extra});
const codexTurn = (file, prompt, answer) => {
  append(file, ce('event_msg', {type: 'task_started'}));
  append(file, ce('response_item', {type: 'message', role: 'user', content: [{type: 'input_text', text: prompt}]}));
  append(file, ce('event_msg', {type: 'task_complete', last_agent_message: answer}));
};
append(codexFile, ce('session_meta', {id: 'qa-codex', cwd: project, source: 'cli'}));
append(codexFile, ce('turn_context', {model: 'codex-test'}));
append(codexFile, ce('event_msg', {type: 'task_started'}));
append(codexFile, ce('response_item', {type: 'message', role: 'user', content: [{type: 'input_text', text: 'Codex 첫 요청 ```코드``` #태그'}]}));
append(codexFile, ce('response_item', {type: 'function_call', name: 'mcp__browser__navigate', arguments: '{"url":"https://raw-arg.example"}'}));
append(codexFile, ce('event_msg', {type: 'task_complete', last_agent_message: 'Codex 첫 답변'}));
append(sideFile, ce('session_meta', {id: 'qa-side', cwd: side, source: 'cli'}));
codexTurn(sideFile, '사이드 첫 질문', '사이드 첫 답');
codexTurn(sideFile, '사이드 둘째 질문 (저장할 것)', '사이드 둘째 답');
append(claudeFile, cl('user', {uuid: 'u1', message: {content: 'Claude 첫 요청'}}));
append(claudeFile, cl('assistant', {uuid: 'a1', message: {id: 'r1', model: 'claude-test', stop_reason: 'end_turn', content: [{type: 'tool_use', name: 'Skill', input: {skill: 'review'}}, {type: 'text', text: 'Claude 첫 답변'}]}}));
const port = 4313, url = `http://127.0.0.1:${port}`;
const env = {...process.env, KANBAN_DB: path.join(fixture, 'test.sqlite3'), KANBAN_SESSION_ROOTS: JSON.stringify({codex: [codex], claude: [claude]})};
const listNotes = dir => fs.existsSync(path.join(dir, 'Kanban')) ? fs.readdirSync(path.join(dir, 'Kanban'), {recursive: true}).filter(name => name.endsWith('.md') && !name.startsWith('.')).map(name => path.join(dir, 'Kanban', name)) : [];
const noteFor = (dir, word) => listNotes(dir).find(file => path.basename(file).includes(word) && !file.includes('(편집본') && !file.includes('최신본'));
let server, browser, serverOutput = '';
const checks = [];
async function startServer() {
  server = spawn('python3', ['-m', 'kanban', 'serve', '--port', String(port)], {cwd: root, env, stdio: ['ignore', 'pipe', 'pipe']});
  server.stdout.on('data', d => { serverOutput += d; }); server.stderr.on('data', d => { serverOutput += d; });
  for (let i = 0; i < 100; i++) {
    if (server.exitCode !== null) throw new Error(serverOutput);
    try { const s = await (await fetch(url + '/api/state')).json(); if (s.cards.length === 3) return; } catch {}
    await new Promise(r => setTimeout(r, 100));
  }
  throw new Error('Server did not start: ' + serverOutput);
}
async function stopServer() { if (!server || server.exitCode !== null) return; const p = new Promise(r => server.once('exit', r)); server.kill('SIGINT'); await p; }
async function waitFor(predicate, timeout = 40000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { if (await predicate()) return; await new Promise(r => setTimeout(r, 200)); }
  throw new Error('Timed out waiting for condition');
}
function passed(label) { checks.push(label); console.log('PASS ' + label); }
(async () => {
  try {
    await startServer();
    browser = await chromium.launch({headless: true, executablePath: process.env.KANBAN_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    const row = name => page.locator('.obsidian-project').filter({has: page.locator('strong', {hasText: new RegExp('^' + name + '$')})});
    await page.goto(url); await page.locator('.task').first().waitFor();
    await page.getByText('Obsidian 연동 꺼짐', {exact: true}).waitFor();
    assert(await page.locator('#obsidian-sync').isDisabled());
    assert(!fs.existsSync(path.join(vault, 'Kanban')) && !fs.existsSync(path.join(vault2, 'Kanban')));
    passed('Unconfigured: explains per-project setup and writes nothing');

    await page.locator('#obsidian-settings').click();
    assert.equal(await page.locator('.obsidian-project').count(), 2);
    const ws = row('workspace');
    await ws.getByText('자동 저장', {exact: true}).click();
    for (const [value, message] of [['relative/vault', '절대 경로'], [path.join(fixture, 'missing'), '경로가 없습니다'], [plain, '.obsidian']]) {
      await ws.locator('input[name=vault]').fill(value);
      await ws.locator('.op-save').click();
      await ws.locator('.form-error').filter({hasText: message}).waitFor();
    }
    passed('Invalid vault paths are rejected inside the project row');
    await ws.locator('input[name=vault]').fill(vault);
    await ws.locator('.op-save').click();
    await ws.locator('.op-status').filter({hasText: '새 노트 2'}).waitFor({timeout: 20000});
    assert.equal(listNotes(vault).length, 2);
    assert.equal(fs.readFileSync(path.join(vault, '내 기존 노트.md'), 'utf8'), '사용자 노트');
    assert(!listNotes(vault).some(file => file.includes('사이드')));
    const codexNote = noteFor(vault, 'Codex');
    assert(fs.readFileSync(codexNote, 'utf8').includes('kanban_mode: "auto"') && !fs.readFileSync(codexNote, 'utf8').includes('raw-arg'));
    passed('자동 저장 for one project writes all of its sessions only, user notes untouched');

    const sd = row('side');
    assert.equal(await sd.locator('input[name=vault]').inputValue(), vault);  // Prefilled with the last vault.
    await sd.getByText('선택 저장', {exact: true}).click();
    assert((await sd.locator('.op-help').textContent()).includes('누른 것만'));
    await sd.locator('input[name=vault]').fill(vault2);
    await sd.locator('.op-save').click();
    await sd.locator('.op-status').filter({hasText: '선택 없음 1'}).waitFor({timeout: 20000});
    assert.equal(listNotes(vault2).length, 0);
    await page.locator('#obsidian-dialog').screenshot({path: path.join(out, 'projects.png')});
    passed('A second project gets its own vault and 선택 저장; nothing is written before a pick');
    await page.locator('#obsidian-dialog .cancel').click();

    await page.locator('#projects .project').filter({hasText: 'side'}).click();
    await page.getByText('Obsidian 선택 저장 · side', {exact: true}).waitFor();
    await page.locator('.task').first().click();
    await page.locator('.turn-pick').first().waitFor();
    assert.equal(await page.locator('.turn-pick').count(), 2);
    const second = page.locator('.history-turn').filter({hasText: '사이드 둘째 질문'});
    await second.locator('.turn-pick').click();
    await second.locator('.turn-pick.picked').waitFor();
    await waitFor(() => listNotes(vault2).length === 1, 20000);
    const sideText = fs.readFileSync(listNotes(vault2)[0], 'utf8');
    assert(sideText.includes('사이드 둘째 답') && !sideText.includes('사이드 첫 답') && sideText.includes('## 프롬프트 2 ·'));
    assert((await page.locator('#obsidian-note').textContent()).includes('선택한 프롬프트 1개'));
    await page.locator('#detail-dialog').screenshot({path: path.join(out, 'pick.png')});
    await page.locator('#detail-dialog .close').click();
    assert((await page.locator('.task').first().textContent()).includes('Obsidian 선택 1개'));
    passed('선택 저장: picking a prompt writes only that prompt (original number kept) and marks the card');

    await page.locator('#all-projects').click();
    await page.locator('.task').filter({hasText: 'Codex 첫 요청'}).click();
    await page.locator('.history-turn').first().waitFor();
    assert.equal(await page.locator('.turn-pick').count(), 0);
    const href = await page.locator('#open-obsidian').getAttribute('href');
    assert.equal(decodeURIComponent(href.slice('obsidian://open?path='.length)), codexNote);
    assert((await page.locator('#obsidian-note').textContent()).includes('자동 저장'));
    await page.locator('#detail-dialog .close').click();
    passed('자동 저장 project: no pick buttons, "Open in Obsidian" points to its own vault');

    codexTurn(codexFile, '자동 저장 확인 요청', '자동 저장 완료 답변');
    await waitFor(() => fs.readFileSync(codexNote, 'utf8').includes('자동 저장 완료 답변'));
    passed('자동 저장 picks up a new prompt and answer without any click');

    await stopServer(); await startServer(); await page.reload();
    await page.getByText('Obsidian 연동 · 프로젝트 2개', {exact: true}).waitFor();
    await page.locator('#obsidian-settings').click();
    assert(await row('workspace').locator('input[value=auto]').isChecked());
    assert(await row('side').locator('input[value=selected]').isChecked());
    assert.equal(await row('side').locator('input[name=vault]').inputValue(), vault2);
    await page.locator('#obsidian-dialog .cancel').click();
    passed('Per-project modes, vaults and picks survive a server restart');

    const folder = path.dirname(codexNote);
    fs.chmodSync(folder, 0o555);
    codexTurn(codexFile, '실패 중 요청', '실패 중 답변');
    await page.getByRole('heading', {name: '실패 중 요청', exact: true}).waitFor({timeout: 16000});
    await page.locator('#obsidian-sync').click();
    await page.getByText('Obsidian 동기화 실패 (일부 프로젝트)', {exact: true}).waitFor({timeout: 25000});
    const problem = await page.locator('#obsidian-error').textContent();
    assert(problem.includes('workspace:') && problem.includes('쓰기 권한') && problem.includes('지금 동기화'));
    await page.locator('.obsidian-panel').screenshot({path: path.join(out, 'error.png')});
    await page.locator('#projects .project').filter({hasText: 'side'}).click();
    await page.getByText('Obsidian 선택 저장 · side', {exact: true}).waitFor();
    assert(await page.locator('#obsidian-error').isHidden());
    await page.locator('#all-projects').click();
    passed("One project's write failure is shown with retry guidance; the other project stays healthy");
    fs.chmodSync(folder, 0o755);
    await page.locator('#obsidian-sync').click();
    await page.getByText('Obsidian 연동 · 프로젝트 2개', {exact: true}).waitFor({timeout: 25000});
    assert(fs.readFileSync(codexNote, 'utf8').includes('실패 중 답변'));
    passed('Retry after fixing permissions succeeds');

    const claudeNote = noteFor(vault, 'Claude');
    const edited = fs.readFileSync(claudeNote, 'utf8') + '\n내가 Obsidian에서 쓴 메모\n';
    fs.writeFileSync(claudeNote, edited);
    append(claudeFile, cl('user', {uuid: 'u2', message: {content: '충돌 이후 질문'}}));
    append(claudeFile, cl('assistant', {uuid: 'a2', message: {id: 'r2', stop_reason: 'end_turn', content: [{type: 'text', text: '충돌 이후 답변'}]}}));
    const copy = claudeNote.replace(/\.md$/, ' (Kanban 최신본).md');
    await waitFor(() => fs.existsSync(copy) && fs.readFileSync(copy, 'utf8').includes('충돌 이후 답변'));
    assert.equal(fs.readFileSync(claudeNote, 'utf8'), edited);
    await page.locator('#obsidian-error').filter({hasText: '편집된 노트 1개'}).waitFor({timeout: 10000});
    await page.locator('#obsidian-settings').click();
    await page.locator('.conflict-row').waitFor();
    assert((await page.locator('.conflict-row').textContent()).includes('workspace'));
    await page.locator('.conflict-row button').click();
    await page.locator('#obsidian-conflicts').waitFor({state: 'hidden'});
    assert.equal(fs.readFileSync(listNotes(vault).find(file => file.includes('(편집본')), 'utf8'), edited);
    assert(fs.readFileSync(claudeNote, 'utf8').includes('충돌 이후 답변') && !fs.existsSync(copy));
    passed('Edited note preserved, newer content to a copy; restore keeps the edit as a backup');

    const ws2 = row('workspace');
    await ws2.getByText('연동 안 함', {exact: true}).click();
    await ws2.locator('.op-save').click();
    await page.getByText('Obsidian 연동 · 프로젝트 1개', {exact: true}).waitFor();
    assert.equal(listNotes(vault).length, 3);
    await page.locator('#obsidian-dialog .cancel').click();
    await page.locator('.task').filter({hasText: 'Codex'}).first().click();
    assert(await page.locator('#open-obsidian').isHidden());
    await page.locator('#detail-dialog .close').click();
    passed('Turning a project off keeps every note in its vault');

    await page.locator('#projects .project').filter({hasText: 'side'}).click();
    await page.locator('.task').first().click();
    const sideNote = listNotes(vault2)[0], before = fs.readFileSync(sideNote, 'utf8');
    await page.locator('.turn-pick.picked').click();
    await page.locator('.turn-pick.picked').waitFor({state: 'detached'});
    await page.waitForTimeout(1500);
    assert.equal(fs.readFileSync(sideNote, 'utf8'), before);
    await page.locator('#detail-dialog .close').click();
    passed('Unpicking stops updates but never deletes the note');

    await page.locator('#all-projects').click();
    await page.locator('[data-provider=codex]').click();
    assert.equal(await page.locator('.task').count(), 2);
    await page.locator('[data-provider=all]').click();
    passed('Existing filters still work');

    await page.setViewportSize({width: 390, height: 844});
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.locator('#obsidian-settings').click();
    assert(await page.locator('#obsidian-dialog').evaluate(node => node.scrollWidth <= node.clientWidth));
    await page.screenshot({path: path.join(out, 'settings-mobile.png')});
    await page.locator('#obsidian-dialog .cancel').click();
    passed('Mobile layout without horizontal overflow');
    await page.setViewportSize({width: 1440, height: 1000});
    await page.screenshot({path: path.join(out, 'desktop.png')});
    assert.deepEqual(errors, []);
    passed('No browser JavaScript errors');
    fs.copyFileSync(sideNote, path.join(out, 'sample-selected-note.md'));
    fs.writeFileSync(path.join(out, 'report.json'), JSON.stringify({passed: checks.length, checks, model_calls: 0}, null, 2));
    console.log(JSON.stringify({passed: checks.length}, null, 2));
  } finally {
    if (browser) await browser.close();
    await stopServer();
    try { fs.chmodSync(path.join(vault, 'Kanban', 'workspace'), 0o755); } catch {}
    fs.rmSync(fixture, {recursive: true, force: true});
  }
})().catch(e => { console.error(e); console.error(serverOutput); process.exitCode = 1; });
