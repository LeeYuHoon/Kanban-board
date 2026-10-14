// Run after browser_smoke.py, with the dedicated QA server on port 4311.
const {chromium}=require('../.local/browser-tools/node_modules/playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
 const ctx=await browser.newContext({permissions:['clipboard-read','clipboard-write']});
 const page=await ctx.newPage();
 const errors=[]; page.on('pageerror',e=>errors.push(e.message));
 try {
  await page.setViewportSize({width:1440,height:1000});
  await page.goto('http://127.0.0.1:4311');
  await page.locator('.todo .task').waitFor();
  await page.screenshot({path:'.local/visual-qa/board-desktop.png',fullPage:true});
  await page.locator('.todo .task').click();
  const command=await page.locator('.run-box pre').textContent();
  await page.locator('.run-box .small-button').click();
  assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),command);
  assert(await page.locator('#toast').isVisible());
  await page.locator('#detail-dialog .close').click();
  await page.locator('.done .task').click();
  await page.locator('.detail-section-heading .small-button').click();
  assert((await page.evaluate(()=>navigator.clipboard.readText())).startsWith('QA fixture:'));
  await page.locator('.usage summary').click();
  await page.screenshot({path:'.local/visual-qa/detail-desktop.png',fullPage:true});
  await page.locator('#detail-dialog .close').click();
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:'.local/visual-qa/board-mobile.png',fullPage:true});
  await page.locator('#new-card').click();
  assert.equal(await page.evaluate(()=>innerWidth),390);
  await page.locator('#card-form [name=title]').fill('모바일 임시 입력');
  await page.locator('#card-form [name=provider]').selectOption('codex');
  await page.screenshot({path:'.local/visual-qa/new-task-mobile.png',fullPage:true});
  await page.locator('#card-dialog .cancel').click();
  await page.locator('.todo .task').click();
  await page.locator('#edit-card').click();
  assert.equal(await page.locator('#card-title').textContent(),'작업 수정');
  await page.locator('#card-dialog .cancel').click();
  assert.deepEqual(errors,[]);
  console.log('PASS exact command clipboard, exact result clipboard, populated desktop/mobile, mobile create/edit/cancel, no JS errors');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
