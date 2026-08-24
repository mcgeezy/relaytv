#!/usr/bin/env node
'use strict';

const { chromium } = require('playwright');

function option(name, fallback) {
  const prefix = `--${name}=`;
  const value = process.argv.find((item) => item.startsWith(prefix));
  return value ? value.slice(prefix.length) : fallback;
}

function check(condition, message) {
  if (!condition) throw new Error(message);
}

function media(id, type, title) {
  return {
    media_type: type, media_id: id, title, original_title: title,
    date: '2026-01-02', year: 2026, overview: `${title} overview`,
    poster_url: '', backdrop_url: '', rating: 8.2, media_status: 'unknown',
    request: null, playback_available: false,
  };
}

async function browserFor(wsEndpoint) {
  if (option('local', '0') === '1') return chromium.launch({headless:true});
  return chromium.connect(wsEndpoint);
}

async function runScenario(browser, baseUrl, scenario, screenshotDir) {
  const context = await browser.newContext({viewport:scenario.viewport, colorScheme:scenario.colorScheme});
  const page = await context.newPage();
  const errors = [];
  let callerConnected = false;
  const playbackCommands = [];
  page.on('pageerror', error => errors.push(`page: ${error.message}`));
  page.on('console', message => { if (message.type() === 'error') errors.push(`console: ${message.text()}`); });
  await page.route('**/integrations/seerr/status', route => route.fulfill({json:{enabled:true, configured:true, reachable:true, version:'3.4.1', application_title:'Smoke Seerr', request_mode:scenario.caller ? 'caller_session' : 'shared_admin', caller_connected:callerConnected, caller_identity:callerConnected ? {id:7,display_name:'Smoke Caller',username:'smoke'} : undefined}}));
  await page.route('**/integrations/seerr/session/quick-connect', route => route.fulfill({json:{flow_id:'opaque-flow',code:'123456',expires_in:600}}));
  await page.route('**/integrations/seerr/session/quick-connect/complete', route => {
    callerConnected = true;
    return route.fulfill({json:{connected:true,pending:false,identity:{id:7,display_name:'Smoke Caller',username:'smoke'},expires_in:43200}});
  });
  await page.route('**/seerr/discover**', route => route.fulfill({json:{page:1, total_pages:1, total_results:2, results:[media(10,'movie','Smoke Movie'),media(20,'tv','Smoke Series')]}}));
  await page.route('**/seerr/search**', async route => {
    const query = new URL(route.request().url()).searchParams.get('query') || '';
    if (query === 'retired') await new Promise(resolve => setTimeout(resolve, 700));
    await route.fulfill({json:{page:1, total_pages:1, total_results:1, results:[media(query === 'retired' ? 31 : 32,'movie',query === 'retired' ? 'Retired Result' : 'Current Result')]}});
  });
  await page.route('**/seerr/item/**', route => route.fulfill({json:{...media(10,'movie','Smoke Movie'), runtime_minutes:112, genres:[{id:18,name:'Drama'}], tagline:'A safe detail', seasons:[], playback_available:true, playback:{provider:'jellyfin',media_type:'movie',media_id:10}}}));
  await page.route('**/seerr/playback', async route => {
    playbackCommands.push((await route.request().postDataJSON()).command);
    await route.fulfill({json:{ok:true,media_type:'movie',media_id:10,command:playbackCommands.at(-1),queued:false,suppressed:false}});
  });
  await page.route('**/seerr/requests**', route => route.fulfill({json:{page:1,total_pages:1,total_results:1,results:[{request_id:4,status:'pending',media_type:'movie',media_id:10,media_status:'pending',is_4k:false,created_at:'',updated_at:''}]}}));
  try {
    await page.goto(`${baseUrl}/ui`, {waitUntil:'domcontentloaded'});
    await page.locator('#seerrOpenBtn').waitFor({state:'visible', timeout:15000});
    await page.locator('#seerrOpenBtn').click();
    await page.locator('#seerrShell:not(.hidden)').waitFor();
    if (scenario.caller) {
      await page.waitForFunction(() => document.querySelector('#seerrConnectCode')?.textContent === '123456');
      await page.waitForFunction(() => document.querySelector('#seerrConnectBackdrop')?.classList.contains('hidden'), null, {timeout:10000});
      check((await page.locator('#seerrConnection').textContent()).includes('Smoke Caller'), `${scenario.name}: caller identity missing after Quick Connect`);
    }
    await page.waitForFunction(() => document.querySelectorAll('.seerrCard').length === 2);
    check((await page.locator('.seerrCardTitle').allTextContents()).join(',') === 'Smoke Movie,Smoke Series', `${scenario.name}: discovery cards mismatch`);

    await page.locator('.seerrCard').first().click();
    await page.waitForFunction(() => document.querySelector('#seerrDetailTitle')?.textContent === 'Smoke Movie');
    check((await page.locator('.seerrDetailOverview').textContent()) === 'Smoke Movie overview', `${scenario.name}: detail mismatch`);
    await page.locator('.seerrPlaybackBtn').click();
    await page.waitForFunction(() => document.querySelector('.seerrRequestResult')?.textContent === 'Playback started on RelayTV.');
    check(playbackCommands.join(',') === 'play_now', `${scenario.name}: validated playback action mismatch`);
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => document.querySelector('#seerrDetail')?.classList.contains('hidden'));

    const search = page.locator('#seerrSearchInput');
    await search.fill('retired');
    await page.waitForTimeout(400);
    await search.fill('current');
    await page.waitForFunction(() => document.querySelector('.seerrCardTitle')?.textContent === 'Current Result');
    await page.waitForTimeout(500);
    check((await page.locator('.seerrCardTitle').textContent()) === 'Current Result', `${scenario.name}: retired search replaced current results`);

    await page.locator('.seerrTab[data-seerr-section="requests"]').click();
    await page.waitForFunction(() => document.querySelector('.seerrState')?.textContent === 'pending');
    const layout = await page.evaluate(() => ({
      bodyOverflow: document.body.scrollWidth > document.body.clientWidth,
      shellOverflow: document.querySelector('#seerrShell').scrollWidth > document.querySelector('#seerrShell').clientWidth,
      nestedInteractive: document.querySelectorAll('button button, a a, [role="button"] button, [role="button"] a').length,
    }));
    check(!layout.bodyOverflow && !layout.shellOverflow, `${scenario.name}: horizontal overflow detected`);
    check(layout.nestedInteractive === 0, `${scenario.name}: nested interactive controls detected`);
    check(errors.length === 0, `${scenario.name}: browser errors: ${errors.join('; ')}`);
    if (screenshotDir) await page.screenshot({path:`${screenshotDir}/seerr-${scenario.name}.png`, fullPage:true});
    return {name:scenario.name, layout};
  } finally {
    await context.close();
  }
}

async function main() {
  const wsEndpoint = option('ws', 'ws://10.55.55.98:3000/');
  const baseUrl = option('base', 'http://10.55.55.2:8787').replace(/\/$/, '');
  const screenshotDir = option('screenshots', '');
  const browser = await browserFor(wsEndpoint);
  try {
    const scenarios = [
      {name:'phone-dark', viewport:{width:390,height:844}, colorScheme:'dark', caller:true},
      {name:'desktop-light', viewport:{width:1440,height:1000}, colorScheme:'light', caller:false},
    ];
    const results = [];
    for (const scenario of scenarios) results.push(await runScenario(browser, baseUrl, scenario, screenshotDir));
    process.stdout.write(`${JSON.stringify({ok:true, wsEndpoint, baseUrl, results}, null, 2)}\n`);
  } finally {
    await browser.close();
  }
}

main().catch(error => {
  process.stderr.write(`Seerr UI smoke failed: ${error.stack || error}\n`);
  process.exit(1);
});
