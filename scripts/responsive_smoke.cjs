// npm install --prefix .local/browser-tools --no-audit --no-fund playwright
// Uses a separate temporary Chrome profile. Does not touch the user's browser profile.
const {chromium} = require('../.local/browser-tools/node_modules/playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const output = path.resolve('.local/visual-qa');
fs.mkdirSync(output, {recursive: true});
(async () => {
  const browser = await chromium.launch({headless: true, executablePath: process.env.KANBAN_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  const context = await browser.newContext();
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const checks = [];
  try {
    for (const width of [1440, 1024, 768, 390, 360]) {
      await page.setViewportSize({width, height: 1000});
      await page.goto('http://127.0.0.1:31229');
      await page.getByText('● 로컬 연결됨', {exact: true}).waitFor();
      assert.equal(await page.evaluate(() => innerWidth), width);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await page.locator('#add-project').click();
      assert(await page.locator('#project-dialog').isVisible());
      const rect = await page.locator('#project-dialog').boundingBox();
      assert(rect.x >= 0 && rect.x + rect.width <= width);
      await page.locator('#project-dialog .cancel').click();
      await page.locator('#open-guide').click();
      await page.locator('.guide-done').click();
      assert.equal(await page.locator('dialog[open]').count(), 0);
      await page.screenshot({path: path.join(output, `welcome-${width}.png`), fullPage: true});
      checks.push(`Onboarding, project dialog and guide at ${width}px`);
      console.log('PASS ' + checks.at(-1));
    }
    // Verify empty-board keyboard access and an offline -> reconnect transition.
    await page.setViewportSize({width: 1440, height: 1000});
    await page.locator('#new-card').click();
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('dialog[open]').count(), 0);
    checks.push('Escape closes dialog');
    await page.route('**/api/state', route => route.abort('failed'));
    await page.getByText('○ 연결 끊김', {exact: true}).waitFor({timeout: 15000});
    assert(await page.locator('#new-card').isDisabled());
    assert(await page.locator('#error').isVisible());
    await page.unroute('**/api/state');
    await page.getByText('● 로컬 연결됨', {exact: true}).waitFor({timeout: 15000});
    assert(await page.locator('#new-card').isEnabled());
    checks.push('Connection failure and automatic recovery');
    assert.deepEqual(errors, []);
    checks.push('No browser JavaScript errors');
    fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify({passed: checks.length, checks, errors}, null, 2));
    console.log(JSON.stringify({passed: checks.length, checks}, null, 2));
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
