#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');
const {chromium} = require('playwright');
const {newFixturePage, playbackStatus} = require('./ui-phase0-states.js');

function parseArgs(argv) {
  const args = {
    baseUrl: 'http://127.0.0.1:8794',
    report: 'docs/ui-modernization/phase-7-hardening.json',
  };
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (token.startsWith('--base=')) args.baseUrl = token.slice('--base='.length);
    else if (token === '--base-url') args.baseUrl = argv[++i];
    else if (token.startsWith('--report=')) args.report = token.slice('--report='.length);
    else if (token === '--report') args.report = argv[++i];
  }
  return args;
}

function measureUiAssets() {
  const uiDir = path.join(__dirname, '..', 'app', 'relaytv_app', 'static', 'ui');
  const indexHtml = fs.readFileSync(path.join(uiDir, 'index.html'), 'utf8');
  const refs = new Set();
  for (const m of indexHtml.matchAll(/\/static\/ui\/([A-Za-z0-9_.-]+\.(?:css|js))/g)) {
    refs.add(m[1]);
  }
  const assets = [];
  let totalSource = 0;
  let totalGzip = 0;
  for (const name of [...refs].sort()) {
    const buf = fs.readFileSync(path.join(uiDir, name));
    const gz = zlib.gzipSync(buf, {level: 9}).length;
    totalSource += buf.length;
    totalGzip += gz;
    assets.push({name, sourceBytes: buf.length, gzipBytes: gz});
  }
  let baselineGzip = null;
  let baselineSource = null;
  const baselinePath = path.join(__dirname, '..', 'docs', 'ui-modernization', 'phase-0-baseline.json');
  if (fs.existsSync(baselinePath)) {
    const baseline = JSON.parse(fs.readFileSync(baselinePath, 'utf8'));
    if (Array.isArray(baseline.assets)) {
      baselineSource = baseline.assets.reduce((a, b) => a + Number(b.sourceBytes || 0), 0);
      baselineGzip = baseline.assets.reduce((a, b) => a + Number(b.gzipBytes || 0), 0);
    }
  }
  return {
    requestCount: assets.length,
    totalSourceBytes: totalSource,
    totalGzipBytes: totalGzip,
    baselineSourceBytes: baselineSource,
    baselineGzipBytes: baselineGzip,
    gzipDeltaBytes: baselineGzip === null ? null : totalGzip - baselineGzip,
    gzipDeltaPercent: baselineGzip ? Number((((totalGzip - baselineGzip) / baselineGzip) * 100).toFixed(2)) : null,
    assets,
  };
}

async function run() {
  const args = parseArgs(process.argv.slice(2));
  fs.mkdirSync(path.dirname(args.report), {recursive: true});

  const browser = await chromium.launch({headless: true});
  const report = {
    phase: 'phase-7-hardening',
    timestamp: new Date().toISOString(),
    transfer: measureUiAssets(),
    responsiveMatrix: {},
    keyboardFlow: {},
    shareTarget: {},
    navigationStability: {},
    errors: [],
  };

  try {
    // 1. Responsive / Zoom / Landscape / Reduced Motion matrix
    const shapes = [
      {name: 'narrow-320x568', width: 320, height: 568, colorScheme: 'dark', reducedMotion: 'no-preference'},
      {name: 'zoom200-640x400', width: 640, height: 400, colorScheme: 'light', reducedMotion: 'no-preference'},
      {name: 'landscape-667x375', width: 667, height: 375, colorScheme: 'dark', reducedMotion: 'no-preference'},
      {name: 'reduced-motion-1280x800', width: 1280, height: 800, colorScheme: 'dark', reducedMotion: 'reduce'},
    ];
    for (const shape of shapes) {
      const fixture = await newFixturePage(browser, args.baseUrl, playbackStatus(), {
        viewport: {width: shape.width, height: shape.height},
        colorScheme: shape.colorScheme,
        themeMode: shape.colorScheme,
      });
      try {
        const page = fixture.page;
        await page.emulateMedia({reducedMotion: shape.reducedMotion});
        await page.waitForSelector('html.shellReady');
        await page.waitForFunction(() => document.querySelectorAll('.qTile').length > 0);
        const remoteOverflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);

        await page.locator('[data-destination="browse"]').click();
        await page.waitForSelector('#browseDestination:not(.hidden)');
        const browseOverflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);

        await page.locator('[data-destination="settings"]').click();
        await page.waitForSelector('#settingsBackdrop.isDestination:not(.hidden)');
        const settingsOverflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);

        report.responsiveMatrix[shape.name] = {
          remoteOverflow,
          browseOverflow,
          settingsOverflow,
          errors: fixture.errors.length,
        };
        if (remoteOverflow || browseOverflow || settingsOverflow) {
          throw new Error(`Horizontal overflow on ${shape.name}: ${JSON.stringify(report.responsiveMatrix[shape.name])}`);
        }
        report.errors.push(...fixture.errors);
      } finally {
        await fixture.context.close();
      }
    }

    // 2. Keyboard-only navigation & modal focus containment
    {
      const fixture = await newFixturePage(browser, args.baseUrl, playbackStatus(), {
        viewport: {width: 1280, height: 800},
        colorScheme: 'dark',
        themeMode: 'dark',
      });
      try {
        const page = fixture.page;
        await page.route((u) => new URL(u).pathname === '/peers', (r) => r.fulfill({json: {peers: [], discovered: [], discovery: {enabled: false}}}));
        await page.waitForSelector('html.shellReady');

        // Open Add modal via keyboard Enter
        await page.locator('#addUrlBtn').focus();
        await page.keyboard.press('Enter');
        await page.waitForSelector('#addBackdrop:not(.hidden)');
        await page.keyboard.press('Escape');
        await page.locator('#addBackdrop').waitFor({state: 'hidden'});
        const addRestored = await page.evaluate(() => document.activeElement?.id || '');

        // Open Peers modal via keyboard Enter, Tab inside dialog, Escape closes and restores focus
        await page.locator('#queueSendBtn').focus();
        await page.keyboard.press('Enter');
        await page.waitForSelector('#peersBackdrop:not(.hidden)');
        const peersFocused = await page.evaluate(() => document.activeElement?.id || '');
        await page.keyboard.press('Tab');
        const tabStayedInside = await page.evaluate(() => !!document.activeElement?.closest('#peersBackdrop'));
        await page.keyboard.press('Escape');
        await page.locator('#peersBackdrop').waitFor({state: 'hidden'});
        const peersRestored = await page.evaluate(() => document.activeElement?.id || '');

        if (!tabStayedInside) throw new Error('Tab focus escaped #peersBackdrop');
        report.keyboardFlow = {addRestored, peersFocused, tabStayedInside, peersRestored};
        report.errors.push(...fixture.errors);
      } finally {
        await fixture.context.close();
      }
    }

    // 3. Share-target prepopulate without auto-playback
    {
      const fixture = await newFixturePage(browser, args.baseUrl, playbackStatus(), {
        viewport: {width: 390, height: 844},
        colorScheme: 'dark',
        themeMode: 'dark',
      });
      try {
        const page = fixture.page;
        const writes = [];
        await page.route((u) => ['/play_now', '/enqueue'].includes(new URL(u).pathname), async (route) => {
          writes.push(new URL(route.request().url()).pathname);
          await route.fulfill({json: {ok: true}});
        });
        const shareUrl = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ';
        await page.goto(`${args.baseUrl}/ui?share=${encodeURIComponent(shareUrl)}`, {waitUntil: 'domcontentloaded'});
        await page.waitForSelector('#addBackdrop:not(.hidden)');
        const prefilled = await page.locator('#addUrlInput').inputValue();
        const searchStripped = await page.evaluate(() => !new URLSearchParams(location.search).has('share'));
        if (prefilled !== shareUrl || !searchStripped || writes.length > 0) {
          throw new Error(`Share target check failed: prefilled=${prefilled} stripped=${searchStripped} writes=${writes.length}`);
        }
        report.shareTarget = {prefilled: true, searchStripped, unexpectedWrites: writes.length};
        report.errors.push(...fixture.errors);
      } finally {
        await fixture.context.close();
      }
    }

    // 4. Repeated destination navigation DOM stability
    {
      const fixture = await newFixturePage(browser, args.baseUrl, playbackStatus(), {
        viewport: {width: 1280, height: 800},
        colorScheme: 'dark',
        themeMode: 'dark',
      });
      try {
        const page = fixture.page;
        await page.route((u) => new URL(u).pathname === '/settings', (r) => r.fulfill({json: {device_name: 'TV', media_servers: []}}));
        await page.route((u) => new URL(u).pathname === '/media-servers', (r) => r.fulfill({json: {servers: []}}));
        await page.waitForSelector('html.shellReady');
        // Warm up once
        await page.locator('[data-destination="browse"]').click();
        await page.locator('[data-destination="settings"]').click();
        await page.locator('[data-destination="remote"]').click();
        const initialNodes = await page.evaluate(() => document.querySelectorAll('*').length);
        for (let i = 0; i < 10; i += 1) {
          await page.locator('[data-destination="browse"]').click();
          await page.locator('[data-destination="settings"]').click();
          await page.locator('[data-destination="remote"]').click();
        }
        const finalNodes = await page.evaluate(() => document.querySelectorAll('*').length);
        const finalDepth = await page.evaluate(() => window.RelayTV?.runtime?.layerNavigation?.getDepth?.() ?? -1);
        const nodeDelta = finalNodes - initialNodes;
        if (finalDepth !== 0 || nodeDelta > 25) {
          throw new Error(`Navigation stability failed: depth=${finalDepth} nodeDelta=${nodeDelta}`);
        }
        report.navigationStability = {cycles: 10, initialNodes, finalNodes, nodeDelta, finalDepth};
        report.errors.push(...fixture.errors);
      } finally {
        await fixture.context.close();
      }
    }

    if (report.errors.length) {
      throw new Error(`Page errors: ${report.errors.join('; ')}`);
    }
    fs.writeFileSync(args.report, `${JSON.stringify(report, null, 2)}\n`);
    console.log(JSON.stringify(report, null, 2));
  } finally {
    await browser.close();
  }
}

run().catch((err) => {
  console.error(err);
  process.exit(1);
});
