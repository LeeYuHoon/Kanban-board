// Read-only UI verification against the running local service; no model calls.
const {chromium}=require('../.local/browser-tools/node_modules/playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.KANKAN_CHROME||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 try {
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[],report=[];
  page.on('pageerror',e=>errors.push(e.message));
  const url='http://127.0.0.1:31229';await page.goto(url);
  await page.locator('.task').first().waitFor();await page.locator('#all-projects').click();
  for(const provider of ['codex','claude']){
   await page.locator(`[data-provider=${provider}]`).click();
   const count=await page.locator('.task').count();assert(count>0);
   for(let i=0;i<count;i++){
    await page.locator('.task').nth(i).click();await page.locator('.history-turn').first().waitFor();
    const session=(await page.locator('.turn-session').first().innerText()).match(/세션 · ([^\s]+)/)[1];
    const state=await(await fetch(url+'/api/state')).json();
    const card=state.cards.find(c=>c.provider===provider&&c.session_id===session);assert(card);
    const history=await(await fetch(url+`/api/cards/${card.id}/history`)).json();
    for(const turn of history.items){
     const row=page.locator(`.history-turn[data-key="${turn.turn_key}"]`);
     assert.equal(await row.locator('.turn-request').textContent(),turn.prompt);
     if(turn.status==='done')assert.equal(await row.locator('.turn-answer').textContent(),turn.result||'이 요청에 대해 기록된 최종 답변이 없습니다.');
     assert((await row.locator('.turn-session').textContent()).includes(turn.session_id));
     for(const category of ['skills','mcp','agents'])assert.equal(await row.locator('.activity-'+category).count(),1);
    }
    let pages=1;
    if(await page.locator('.history-more').count()){
     const before=await page.locator('.history-turn').count();
     await page.locator('.history-more').click();
     await page.waitForFunction(n=>document.querySelectorAll('.history-turn').length>n,before);
     pages++;assert.equal(await page.locator('.history-turn').evaluateAll(rows=>new Set(rows.map(r=>r.dataset.key)).size),await page.locator('.history-turn').count());
    }
    if(i===0){
     await page.locator('.history-turn').first().scrollIntoViewIfNeeded();
     await page.screenshot({path:`.local/collection-qa/live-${provider}-verified.png`});
     await page.setViewportSize({width:390,height:844});
     assert(await page.evaluate(()=>{const d=document.querySelector('#detail-dialog');return d.scrollWidth<=d.clientWidth}));
     await page.setViewportSize({width:1440,height:1000});
    }
    report.push({provider,matchedRequests:history.items.length,pages,totalRequests:card.turn_count});
    await page.mouse.click(3,400);assert(!(await page.locator('#detail-dialog').isVisible()));
   }
  }
  assert.deepEqual(errors,[]);
  fs.writeFileSync('.local/collection-qa/live-verification.json',JSON.stringify({report,browserErrors:errors,modelCalls:0},null,2));
  console.log(JSON.stringify({report,browserErrors:errors,modelCalls:0}));
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
