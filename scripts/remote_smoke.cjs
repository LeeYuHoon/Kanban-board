// Real browser + HTTP + SQLite + hc-shaped subprocess fixture. No user logs or system install changes.
const {chromium} = require('../.local/browser-tools/node_modules/playwright');
const {spawn} = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const out = path.join(root, '.local', 'remote-qa'); fs.mkdirSync(out, {recursive: true});
const code = `
import signal, threading
from http.server import ThreadingHTTPServer
from tests.test_remote_transport import TransportTests
from tests.test_remote_sync import session
from kanban.server import handler
fixture = TransportTests(); fixture.setUp()
session(fixture.main, 'Main only request')
session(fixture.source, 'Remote only request')
server = ThreadingHTTPServer(('127.0.0.1', 0), handler(fixture.main, remote=fixture.manager))
fixture.manager.start()
def stop(*_): raise KeyboardInterrupt
signal.signal(signal.SIGTERM,stop)
print('READY ' + str(server.server_address[1]), flush=True)
try: server.serve_forever()
except KeyboardInterrupt: pass
finally:
 fixture.manager.stop(); server.server_close(); fixture.doCleanups()
`;
let browser, server, output = '';
(async () => {
  try {
    server = spawn('python3', ['-u', '-c', code], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']});
    server.stdout.on('data', data => output += data);
    server.stderr.on('data', data => output += data);
    for (let i = 0; i < 100 && !/READY (\d+)/.test(output); i++) {
      if (server.exitCode !== null) throw new Error(output);
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    const port = /READY (\d+)/.exec(output)?.[1]; assert(port, output);
    const url = `http://127.0.0.1:${port}`;
    browser = await chromium.launch({headless:true, executablePath:process.env.KANBAN_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
    const page = await browser.newPage({viewport:{width:1440,height:1000}});
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    await page.goto(url);
    await page.locator('#board .task .device-label', {hasText:'Main'}).waitFor();
    await page.locator('#open-remotes').click();
    await page.locator('#remote-load').click();
    await page.locator('#remote-machine option').waitFor({state:'attached'});
    await page.locator('#remote-probe').click();
    await page.locator('#remote-connect').waitFor({state:'visible'});
    assert((await page.locator('#remote-probe-result').textContent()).includes('home-macStudio'));
    await page.locator('#remote-connect').click();
    await page.locator('.remote-device strong', {hasText:'Remote / home-macStudio'}).waitFor();
    await page.waitForFunction(() => document.querySelectorAll('#board .task').length === 2);
    await page.screenshot({path:path.join(out,'connections.png'),fullPage:true});
    await page.locator('#remote-dialog .close').click();
    assert.equal(await page.locator('#board .device-label', {hasText:'Main'}).count(),1);
    assert.equal(await page.locator('#board .device-label', {hasText:'Remote / home-macStudio'}).count(),1);
    const remoteCard = page.locator('#board .task', {hasText:'Remote only request'});
    await remoteCard.click();
    assert((await page.locator('#detail-body').textContent()).includes('Remote / home-macStudio'));
    assert(!(await page.locator('#history-panel').textContent()).includes('Main only request'));
    await page.locator('#detail-dialog .close').click();
    await page.locator('#search').fill('Remote only');
    await page.waitForFunction(() => document.querySelectorAll('#board .task').length === 1);
    assert((await page.locator('#board .task').textContent()).includes('Remote / home-macStudio'));
    await page.locator('#search-reset').click();
    await page.locator('#open-remotes').click();
    await page.locator('.remote-device button', {hasText:'일시정지'}).click();
    await page.locator('.remote-status', {hasText:'일시정지'}).waitFor();
    await page.locator('.remote-device button', {hasText:'동기화 재개'}).click();
    await page.locator('.remote-device button', {hasText:'일시정지'}).waitFor();
    page.once('dialog', dialog => dialog.accept());
    await page.locator('.remote-device button', {hasText:'연결 해제'}).click();
    await page.locator('.remote-status', {hasText:'연결 해제'}).waitFor();
    await page.locator('#remote-dialog .close').click();
    await page.waitForFunction(() => document.querySelectorAll('#board .task').length === 2);
    await page.screenshot({path:path.join(out,'board.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.locator('#open-remotes').click();
    await page.screenshot({path:path.join(out,'mobile.png'),fullPage:true});
    assert(await page.locator('#remote-dialog').evaluate(n => n.scrollWidth <= n.clientWidth + 1));
    assert.deepEqual(errors,[]);
    console.log('PASS Main/Remote labels, connect, device-isolated detail/search, pause/resume, disconnect retention, mobile layout');
  } finally {
    if (browser) await browser.close();
    if (server && server.exitCode === null) {
      const done = new Promise(resolve => server.once('exit',resolve)); server.kill('SIGTERM'); await done;
    }
  }
})().catch(error => { console.error(error); console.error(output); process.exitCode=1; });
