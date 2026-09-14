#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const {chromium, firefox, webkit} = require('playwright');
const {newFixturePage, playbackStatus} = require('./ui-phase0-states.js');

function option(name, fallback) {
  const prefix = `--${name}=`;
  const value = process.argv.find((item) => item.startsWith(prefix));
  return value ? value.slice(prefix.length) : fallback;
}

async function routeWrite(page, pathname, calls, response) {
  await page.route((url) => {
    try { return new URL(url).pathname === pathname; } catch (_error) { return false; }
  }, async (route) => {
    const request = route.request();
    let body = null;
    try { body = request.postDataJSON(); } catch (_error) { body = request.postData() || null; }
    calls.push({path:pathname, body});
    const value = typeof response === 'function' ? await response(request, body) : response;
    const status = Number(value && value.__status) || 200;
    const payload = value && typeof value === 'object' ? {...value} : (value || {ok:true});
    if (payload && typeof payload === 'object') delete payload.__status;
    await route.fulfill({status, json:payload});
  });
}

async function waitForCall(page, calls, pathname, count = 1) {
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    if (calls.filter((item) => item.path === pathname).length >= count) return;
    await page.waitForTimeout(25);
  }
  throw new Error(`timed out waiting for ${pathname}`);
}

async function waitForRootLayer(page) {
  await page.waitForFunction(() => window.RelayTV.runtime.layerNavigation.getDepth() === 0);
}

async function layoutEvidence(browser, baseUrl, viewport, outputDirectory, report) {
  const fixture = await newFixturePage(browser, baseUrl, playbackStatus(), {
    viewport,
    colorScheme:'dark',
    themeMode:'dark',
  });
  try {
    const page = fixture.page;
    await page.waitForFunction(() => document.documentElement.classList.contains('shellReady'));
    const metrics = await page.evaluate(() => {
      const targets = [...document.querySelectorAll('.primaryNavItem, .rTile, .qHandle, .qMenuBtn')]
        .filter((node) => {
          const style = getComputedStyle(node);
          return style.display !== 'none' && style.visibility !== 'hidden';
        })
        .map((node) => {
          const rect = node.getBoundingClientRect();
          return {name:node.getAttribute('aria-label') || node.textContent.trim(), width:Math.round(rect.width), height:Math.round(rect.height)};
        });
      return {
        hash:location.hash,
        horizontalOverflow:document.documentElement.scrollWidth > document.documentElement.clientWidth,
        queueRows:document.querySelectorAll('.qTile').length,
        smallTargets:targets.filter((target) => target.width < 44 || target.height < 44),
      };
    });
    if (metrics.hash !== '#remote' || metrics.horizontalOverflow || metrics.queueRows !== 3 || metrics.smallTargets.length) {
      throw new Error(`${viewport.width}x${viewport.height} layout failed: ${JSON.stringify(metrics)}`);
    }
    const key = `${viewport.width}x${viewport.height}`;
    report.layouts[key] = metrics;
    if (viewport.width === 390 || viewport.width === 1280) {
      const filename = `phase3-remote-${key}.png`;
      await page.screenshot({path:path.join(outputDirectory, filename), fullPage:true});
      report.screenshots.push(filename);
    }
    report.errors.push(...fixture.errors);
  } finally {
    await fixture.context.close();
  }
}

async function interactionEvidence(browser, baseUrl, outputDirectory, report) {
  const status = playbackStatus();
  const fixture = await newFixturePage(browser, baseUrl, status, {
    viewport:{width:390,height:844},
    colorScheme:'dark',
    themeMode:'dark',
  });
  const calls = [];
  try {
    const page = fixture.page;
    for (const pathname of ['/playback/toggle', '/enqueue', '/play_now', '/overlay', '/history/play', '/history/requeue', '/jellyfin/audio/select', '/jellyfin/subtitle/select']) {
      await routeWrite(page, pathname, calls, {ok:true});
    }
    let uploadQueueAttempts = 0;
    await routeWrite(page, '/ingest/media/enqueue', calls, () => {
      uploadQueueAttempts += 1;
      return uploadQueueAttempts === 1
        ? {__status:503, detail:'Fixture storage is temporarily unavailable'}
        : {ok:true, action:'enqueue'};
    });
    await routeWrite(page, '/ingest/media/play', calls, {ok:true, action:'play'});
    await routeWrite(page, '/queue/move', calls, (_request, body) => {
      const [moved] = status.queue.splice(body.from_index, 1);
      status.queue.splice(body.to_index, 0, moved);
      return {queue:status.queue, queue_length:status.queue.length};
    });
    await routeWrite(page, '/queue/remove', calls, (_request, body) => {
      status.queue.splice(body.index, 1);
      status.queue_length = status.queue.length;
      return {queue:status.queue, queue_length:status.queue.length};
    });

    await page.locator('#playPauseBtn').click();
    await waitForCall(page, calls, '/playback/toggle');

    await page.locator('#addUrlBtn').click();
    await page.locator('#addUrlInput').fill('https://video.example.test/phase-three');
    await page.locator('#addQueueBtn').click();
    await waitForCall(page, calls, '/enqueue');
    await page.locator('#addBackdrop').waitFor({state:'hidden'});
    await waitForRootLayer(page);

    await page.locator('#addUrlBtn').click();
    await page.locator('#addUrlInput').fill('https://video.example.test/play-now');
    await page.locator('#addPlayBtn').click();
    await waitForCall(page, calls, '/play_now');
    await page.locator('#addBackdrop').waitFor({state:'hidden'});
    await waitForRootLayer(page);

    await page.locator('#addUrlBtn').click();
    await page.locator('#uploadMediaInput').setInputFiles({name:'fixture.mp4',mimeType:'video/mp4',buffer:Buffer.from('fixture media')});
    await page.locator('#uploadQueueBtn').click();
    await waitForCall(page, calls, '/ingest/media/enqueue');
    await page.getByText('Fixture storage is temporarily unavailable').waitFor();
    await page.locator('#uploadQueueBtn').click();
    await waitForCall(page, calls, '/ingest/media/enqueue', 2);
    await page.locator('#addBackdrop').waitFor({state:'hidden'});
    await waitForRootLayer(page);

    await page.locator('#addUrlBtn').click();
    await page.locator('#uploadMediaInput').setInputFiles({name:'fixture-audio.mp3',mimeType:'audio/mpeg',buffer:Buffer.from('fixture audio')});
    await page.locator('#uploadPlayBtn').click();
    await waitForCall(page, calls, '/ingest/media/play');
    await page.locator('#addBackdrop').waitFor({state:'hidden'});
    await waitForRootLayer(page);

    await page.locator('#addUrlBtn').click();
    await page.locator('#notifyTextInput').fill('Phase 3 fixture notification');
    await page.locator('#notifyImageUrlInput').fill('https://images.example.test/fixture.png');
    await page.locator('#notifySendBtn').click();
    await waitForCall(page, calls, '/overlay');
    await page.getByText('Notification sent.').waitFor();
    await page.locator('#addCloseBtn').click();
    await page.locator('#addBackdrop').waitFor({state:'hidden'});
    await waitForRootLayer(page);

    const secondTitle = await page.locator('.qTitleText').nth(1).textContent();
    await page.locator('.qMenuBtn').nth(1).click();
    await page.getByRole('menuitem', {name:'Move up'}).click();
    await waitForCall(page, calls, '/queue/move');
    await page.waitForFunction((title) => document.querySelector('.qTitleText')?.textContent === title, secondTitle);

    await page.locator('.qMenuBtn').first().click();
    await page.locator('.qPopItem', {hasText:'Remove'}).click();
    await page.getByRole('button', {name:'Undo'}).click();
    if (calls.some((call) => call.path === '/queue/remove')) throw new Error('Undo still committed queue removal');
    await page.locator('.qMenuBtn').first().click();
    await page.locator('.qPopItem', {hasText:'Remove'}).click();
    await page.evaluate(() => window._flushPendingRemove());
    await waitForCall(page, calls, '/queue/remove');

    await page.locator('#hdrMenuBtn').click();
    await page.locator('#histBtn').click();
    await page.locator('.histQueueBtn').first().click();
    await waitForCall(page, calls, '/history/requeue');
    await page.locator('.histPlayBtn').first().click();
    await waitForCall(page, calls, '/history/play');
    await page.locator('#histBackdrop').waitFor({state:'hidden'});

    await page.locator('#nowLangBtn').click();
    await page.locator('#langList .langOpt').nth(1).click();
    await waitForCall(page, calls, '/jellyfin/audio/select');
    await page.waitForTimeout(100);
    await page.locator('#langCloseBtn').click();
    await page.locator('#langBackdrop').waitFor({state:'hidden'});
    await page.locator('#nowSubLangBtn').click();
    await page.locator('#subLangList .langOpt').nth(2).click();
    await waitForCall(page, calls, '/jellyfin/subtitle/select');
    await page.waitForTimeout(100);
    await page.locator('#subLangCloseBtn').click();
    await page.locator('#subLangBackdrop').waitFor({state:'hidden'});

    await page.locator('[data-destination="browse"]').click();
    await page.locator('#browseDestination:not(.hidden)').waitFor();
    await page.locator('#browseJellyfinBtn').click();
    await page.locator('#jellyfinShell:not(.hidden)').waitFor();
    await page.locator('#compactPlaybackBar:not(.hidden)').waitFor();
    await page.locator('#compactPlayPauseBtn').click();
    await waitForCall(page, calls, '/playback/toggle', 2);
    await page.locator('#compactReturnBtn').click();
    await page.waitForFunction(() => location.hash === '#remote' && document.getElementById('jellyfinShell').classList.contains('hidden'));

    await page.locator('[data-destination="browse"]').click();
    await page.locator('#browseIptvBtn').click();
    await page.locator('#iptvShell:not(.hidden)').waitFor();
    await page.goBack();
    await page.waitForFunction(() => location.hash === '#browse' && document.getElementById('iptvShell').classList.contains('hidden'));
    await page.locator('#compactReturnBtn').click();
    await page.waitForFunction(() => location.hash === '#remote');

    await page.locator('[data-destination="settings"]').click();
    await page.locator('#settingsBackdrop:not(.hidden)').waitFor();
    await page.locator('#compactPlaybackBar:not(.hidden)').waitFor();
    await page.locator('#compactReturnBtn').click();
    await page.waitForFunction(() => location.hash === '#remote' && document.getElementById('settingsBackdrop').classList.contains('hidden'));
    await page.locator('[data-destination="settings"]').click();
    await page.locator('#settingsCloseBtn').click();
    await page.waitForFunction(() => location.hash === '#remote' && document.getElementById('settingsBackdrop').classList.contains('hidden'));

    await page.locator('[data-destination="browse"]').click();
    const filename = 'phase3-browse-phone.png';
    await page.screenshot({path:path.join(outputDirectory, filename), fullPage:true});
    report.screenshots.push(filename);
    report.interactions = {
      commandPaths:[...new Set(calls.map((call) => call.path))].sort(),
      historyDepth:await page.evaluate(() => window.RelayTV.runtime.layerNavigation.getDepth()),
      rememberedProvider:await page.evaluate(() => localStorage.getItem('relaytv_browse_provider')),
      uploadQueueAttempts:calls.filter((call) => call.path === '/ingest/media/enqueue').length,
    };
    report.errors.push(...fixture.errors);
  } finally {
    await fixture.context.close();
  }
}

async function shareEvidence(browser, baseUrl, report) {
  const fixture = await newFixturePage(browser, baseUrl, playbackStatus(), {
    viewport:{width:390,height:844},colorScheme:'dark',themeMode:'dark',
  });
  try {
    const page = fixture.page;
    await page.goto(`${baseUrl}/ui?share=${encodeURIComponent('https://video.example.test/shared')}`, {waitUntil:'domcontentloaded'});
    await page.locator('#addBackdrop:not(.hidden)').waitFor();
    report.share = await page.evaluate(() => ({hash:location.hash, search:location.search, value:document.getElementById('addUrlInput').value}));
    if (report.share.hash !== '#remote' || report.share.search || report.share.value !== 'https://video.example.test/shared') {
      throw new Error(`share target failed: ${JSON.stringify(report.share)}`);
    }
    report.errors.push(...fixture.errors);
  } finally {
    await fixture.context.close();
  }
}

async function main() {
  const rootDirectory = path.resolve(__dirname, '..');
  const baseUrl = option('base', 'http://127.0.0.1:8787').replace(/\/$/, '');
  const browserName = option('browser', 'chromium');
  const browserType = {chromium, firefox, webkit}[browserName];
  if (!browserType) throw new Error(`unknown browser: ${browserName}`);
  const outputDirectory = path.resolve(option('output', path.join(rootDirectory, 'docs', 'images', 'ui-phase-3')));
  const reportPath = path.resolve(option('report', path.join(rootDirectory, 'docs', 'ui-modernization', 'phase-3-remote.json')));
  fs.mkdirSync(outputDirectory, {recursive:true});
  const browser = await browserType.launch({headless:true});
  const report = {
    schema:1,
    capturedAt:new Date().toISOString(),
    baseUrl,
    runner:{playwright:require('playwright/package.json').version,browser:browserName,browserVersion:browser.version(),platform:`${process.platform}/${process.arch}`},
    layouts:{},interactions:{},share:{},screenshots:[],errors:[],
  };
  try {
    for (const viewport of [
      {width:320,height:720}, {width:390,height:844}, {width:768,height:1024},
      {width:1280,height:800}, {width:1920,height:1080}, {width:844,height:390},
    ]) await layoutEvidence(browser, baseUrl, viewport, outputDirectory, report);
    if (browserName === 'chromium') await interactionEvidence(browser, baseUrl, outputDirectory, report);
    else report.interactions = {scope:'responsive layout and share target; full action matrix runs in Chromium'};
    await shareEvidence(browser, baseUrl, report);
  } finally {
    await browser.close();
  }
  if (report.errors.length) throw new Error(report.errors.join('\n'));
  fs.writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`);
  process.stdout.write(`${JSON.stringify({ok:true,reportPath,outputDirectory,screenshots:report.screenshots.length},null,2)}\n`);
}

if (require.main === module) main().catch((error) => {
  process.stderr.write(`UI phase 3 remote check failed: ${error.stack || error}\n`);
  process.exitCode = 1;
});
