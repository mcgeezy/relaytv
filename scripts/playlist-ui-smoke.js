#!/usr/bin/env node
'use strict';

// Browser coverage uses controlled playlist API responses, so it is safe to run
// against a live host without changing its queue. Real extraction/playback is a
// separate device check. Requires Playwright, as do the other UI smoke scripts.
const {chromium} = require('playwright');
const fs = require('node:fs');

function option(name, fallback){
  return process.argv.find(value => value.startsWith(`--${name}=`))?.slice(name.length + 3) || fallback;
}
function check(condition, message){if (!condition) throw new Error(message);}

async function scenario(browser, base, viewport, colorScheme, screenshotDir){
  const context = await browser.newContext({viewport, colorScheme});
  const page = await context.newPage();
  const errors = [];
  const jobs = new Map();
  let posts = 0;
  let deletes = 0;
  let status = 'loading';
  let loseResponse = false;
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/queue/playlists**', async route => {
    const request = route.request();
    if (request.method() === 'POST'){
      posts++;
      const body = request.postDataJSON();
      jobs.set(body.request_id, body);
      if (loseResponse) return route.abort();
      await new Promise(resolve => setTimeout(resolve, 250));
      return route.fulfill({status:202, json:{job_id:body.request_id, status:'loading', detail:'Reading playlist…'}});
    }
    if (request.method() === 'DELETE'){
      deletes++;
      status = 'cancelled';
    }
    return route.fulfill({status:200, json:{
      job_id:request.url().split('/').pop(), status, added:3, skipped:1,
      detail:status === 'completed' ? 'Added 3 videos to queue; skipped 1 unavailable entries'
        : (status === 'cancelled' ? 'Import cancelled; nothing was added' : 'Reading playlist…'),
    }});
  });
  try {
    const mixed = 'https://www.youtube.com/watch?v=jNQXAC9IVRw&list=PLreview123&index=4';
    await page.goto(`${base}/ui?share=${encodeURIComponent(mixed)}`, {waitUntil:'domcontentloaded'});
    await page.locator('#addPlaylistBtn').waitFor({state:'visible'});
    check(await page.locator('#addPlayBtn').isEnabled(), 'mixed link lost Play');
    check(await page.locator('#addQueueBtn').isEnabled(), 'mixed link lost Queue');
    await page.locator('#addPlaylistBtn').evaluate(button => {button.click(); button.click();});
    await page.locator('#playlistImportStatus').filter({hasText:'Reading playlist'}).waitFor();
    await page.waitForTimeout(350);
    check(posts === 1 && jobs.size === 1, 'duplicate taps created multiple imports');
    const requestId = [...jobs.keys()][0];

    await page.reload({waitUntil:'domcontentloaded'});
    await page.evaluate(() => openAddUrl());
    await page.locator('#playlistImportCancel').waitFor({state:'visible'});
    check(posts === 1, 'reload resubmitted the import');
    status = 'completed';
    await page.evaluate(() => RelayTVPlaylistQueue.check());
    await page.locator('#playlistImportStatus').filter({hasText:'Added 3 videos'}).waitFor();
    const stored = await page.evaluate(() => JSON.parse(localStorage.getItem('relaytv_playlist_import_v1')));
    check(stored.request_id === requestId, 'reload lost the retry identity');
    if (screenshotDir){
      fs.mkdirSync(screenshotDir, {recursive:true});
      await page.screenshot({path:`${screenshotDir}/playlist-${viewport.width}-${colorScheme}.png`, fullPage:true});
    }
    await page.locator('#playlistImportDismiss').click();
    await page.locator('#addUrlInput').fill('https://www.youtube.com/playlist?list=PLreview123');
    check(await page.locator('#addPlayBtn').isDisabled(), 'playlist-only link offers single Play');
    check(await page.locator('#addQueueBtn').isDisabled(), 'playlist-only link offers single Queue');
    status = 'loading';
    await page.locator('#addPlaylistBtn').click();
    await page.waitForTimeout(350);
    await page.locator('#playlistImportCancel').click();
    await page.locator('#playlistImportStatus').filter({hasText:'nothing was added'}).waitFor();
    check(deletes === 1, 'cancel did not use DELETE');
    await page.locator('#playlistImportDismiss').click();

    for (const link of ['www.youtube.com/playlist?list=PLreview123', 'https://youtube.com/embed/videoseries?list=PLreview123']){
      await page.locator('#addUrlInput').fill(link);
      check(await page.locator('#addPlaylistBtn').isVisible(), 'pasted or embedded playlist lost its action');
      check(await page.locator('#addPlayBtn').isDisabled(), 'embedded playlist offers individual Play');
    }
    await page.locator('#addUrlInput').fill('https://youtube.com/watch?v=jNQXAC9IVRw&list=RDjNQXAC9IVRw');
    check(await page.locator('#addPlaylistBtn').isDisabled(), 'mix permits unbounded import');
    check(await page.locator('#addPlayBtn').isEnabled(), 'mix lost individual Play');
    await page.locator('#addUrlInput').fill('https://youtube.com.evil.test/playlist?list=PLreview123');
    check(await page.locator('#addPlaylistBtn').isHidden(), 'lookalike host exposes playlist action');

    await page.locator('#addUrlInput').fill(mixed);
    loseResponse = true;
    await page.locator('#addPlaylistBtn').click();
    await page.locator('#playlistImportCheck').waitFor({state:'visible'});
    check(posts === 3, 'unexpected import count after lost response');
    status = 'completed';
    await page.locator('#playlistImportCheck').click();
    await page.locator('#playlistImportStatus').filter({hasText:'Added 3 videos'}).waitFor();
    check(posts === 3, 'status recovery resent a POST');
    const bounds = await page.locator('.modal.addModal').first().boundingBox();
    check(bounds && bounds.x >= 0 && bounds.x + bounds.width <= viewport.width + 1, 'modal overflows viewport');
    check(errors.length === 0, errors.join('\n'));
    return {viewport, colorScheme, posts, deletes, reloadRecovered:true, lostResponseRecovered:true};
  } finally {await context.close();}
}

(async() => {
  const base = option('base', 'http://127.0.0.1:8787').replace(/\/$/, '');
  const browser = option('ws', '')
    ? await chromium.connect(option('ws', '')) : await chromium.launch({headless:true});
  try {
    const results = [];
    results.push(await scenario(browser, base, {width:1440, height:1000}, 'dark', option('screenshots', '')));
    results.push(await scenario(browser, base, {width:390, height:844}, 'light', option('screenshots', '')));
    process.stdout.write(JSON.stringify({ok:true, base, results}, null, 2) + '\n');
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
