#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const {newFixturePage, playbackStatus} = require('./ui-phase0-states.js');

function parseArgs(argv) {
  const args = {
    baseUrl: 'http://127.0.0.1:8794',
    out: 'docs/images/ui-phase-5',
    report: 'docs/ui-modernization/phase-5-settings.json',
  };
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (token.startsWith('--base=')) args.baseUrl = token.slice('--base='.length);
    else if (token === '--base-url') args.baseUrl = argv[++i];
    else if (token.startsWith('--out=')) args.out = token.slice('--out='.length);
    else if (token === '--out') args.out = argv[++i];
    else if (token.startsWith('--report=')) args.report = token.slice('--report='.length);
    else if (token === '--report') args.report = argv[++i];
  }
  return args;
}

function sampleSettings() {
  return {
    device_name: 'Living Room TV',
    default_volume: 65,
    cec_enabled: false,
    cec_volume_enabled: false,
    ytdlp_format: '',
    idle_mode: 'clock',
    idle_clock_24h: false,
    idle_panels: {},
    jellyfin_server_url: 'http://jf.local:8096',
    jellyfin_username: 'mark',
    jellyfin_server_type: 'jellyfin',
    jellyfin_connected: true,
    media_servers: [
      {id: 'ms1', type: 'jellyfin', name: 'Home Jellyfin', url: 'http://jf.local:8096', enabled: true, connected: true},
    ],
  };
}

function samplePeers() {
  return {
    device: {device_id: 'self-1', name: 'Living Room TV'},
    peers: [
      {id: 'peer-1', name: 'Bedroom TV', base_url: 'http://10.55.55.12:8787', online: true, device_id: 'dev-2'},
      {id: 'peer-2', name: 'Patio Projector', base_url: 'http://10.55.55.15:8787', online: false, last_error: 'unreachable', device_id: 'dev-3'},
    ],
    discovered: [
      {device_id: 'dev-4', device_name: 'Office Monitor', base_url: 'http://10.55.55.20:8787'},
    ],
    discovery: {enabled: true, active: true},
  };
}

async function installExtraRoutes(page, opts = {}) {
  let savedSettings = sampleSettings();
  await page.route((url) => {
    const p = new URL(url).pathname;
    return p === '/settings' || p === '/media-servers' || p === '/peers' || p.startsWith('/peers/') || p === '/playback/toggle';
  }, async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === '/settings' && route.request().method() === 'GET') {
      await route.fulfill({json: savedSettings});
      return;
    }
    if (pathname === '/settings' && route.request().method() === 'POST') {
      const body = route.request().postDataJSON() || {};
      savedSettings = Object.assign({}, savedSettings, body);
      await route.fulfill({json: {ok: true, settings: savedSettings}});
      return;
    }
    if (pathname === '/media-servers') {
      await route.fulfill({json: {servers: savedSettings.media_servers || []}});
      return;
    }
    if (pathname === '/peers') {
      await route.fulfill({json: samplePeers()});
      return;
    }
    if (pathname.startsWith('/peers/') && pathname.endsWith('/probe')) {
      await route.fulfill({json: {online: true, device_name: 'Bedroom TV'}});
      return;
    }
    if (pathname === '/playback/toggle') {
      if (opts.requireToken) {
        const auth = route.request().headers().authorization || '';
        if (auth !== `Bearer ${opts.requireToken}`) {
          await route.fulfill({
            status: 401,
            headers: {'www-authenticate': 'Bearer realm="relaytv"'},
            contentType: 'application/json',
            body: JSON.stringify({detail: 'Not authorized'}),
          });
          return;
        }
      }
      await route.fulfill({json: {ok: true}});
      return;
    }
    await route.continue();
  });
}

async function run() {
  const args = parseArgs(process.argv.slice(2));
  fs.mkdirSync(args.out, {recursive: true});
  fs.mkdirSync(path.dirname(args.report), {recursive: true});

  const browser = await chromium.launch({headless: true});
  const report = {
    phase: 'phase-5-settings',
    timestamp: new Date().toISOString(),
    checks: {},
    screenshots: [],
    errors: [],
  };

  try {
    // 1. Settings destination layout + dirty leave guard (desktop 1280x800 + phone 390x844)
    for (const vp of [
      {name: 'desktop-dark', width: 1280, height: 800, colorScheme: 'dark'},
      {name: 'phone-dark', width: 390, height: 844, colorScheme: 'dark'},
      {name: 'phone-light', width: 390, height: 844, colorScheme: 'light'},
    ]) {
      const fixture = await newFixturePage(browser, args.baseUrl, playbackStatus(), {
        viewport: {width: vp.width, height: vp.height},
        colorScheme: vp.colorScheme,
        themeMode: vp.colorScheme,
      });
      try {
        const page = fixture.page;
        await installExtraRoutes(page);
        await page.waitForSelector('html.shellReady');
        await page.waitForFunction(() => document.querySelectorAll('.qTile').length > 0);

        await page.locator('[data-destination="settings"]').click();
        await page.waitForSelector('#settingsBackdrop.isDestination:not(.hidden)');
        await page.waitForSelector('#compactPlaybackBar:not(.hidden)');

        const layout = await page.evaluate(() => {
          const bd = document.getElementById('settingsBackdrop');
          const nav = document.querySelector('.primaryNav');
          const bdRect = bd.getBoundingClientRect();
          const navRect = nav.getBoundingClientRect();
          return {
            isDestination: bd.classList.contains('isDestination'),
            position: getComputedStyle(bd).position,
            overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
            bdLeft: Math.round(bdRect.left),
            navRight: Math.round(navRect.right),
            navVisible: navRect.width > 0 && navRect.height > 0,
            compactVisible: !document.getElementById('compactPlaybackBar').classList.contains('hidden'),
          };
        });

        const shot = path.join(args.out, `phase5-settings-${vp.name}.png`);
        await page.screenshot({path: shot, fullPage: false});
        report.screenshots.push(shot);
        report.checks[`layout_${vp.name}`] = layout;
        if (layout.overflow) throw new Error(`Horizontal overflow on ${vp.name}`);
        if (!layout.isDestination || layout.position !== 'static') throw new Error(`Settings not in-flow destination on ${vp.name}`);
        if (vp.width >= 768 && layout.bdLeft < layout.navRight - 2) {
          throw new Error(`Settings overlaps desktop nav rail on ${vp.name}: bdLeft=${layout.bdLeft} navRight=${layout.navRight}`);
        }

        if (vp.name === 'desktop-dark') {
          await page.locator('#setDeviceName').fill('Living Room TV Edited');
          await page.waitForSelector('#settingsDirtyBadge:not(.hidden)');
          await page.locator('[data-destination="remote"]').click();
          await page.waitForSelector('#settingsDiscardBar:not(.hidden)');
          const stillSettings = await page.evaluate(() => !document.getElementById('settingsBackdrop').classList.contains('hidden'));
          if (!stillSettings) throw new Error('Dirty guard failed to keep Settings open');
          await page.locator('#settingsKeepEditingBtn').click();
          await page.locator('#settingsDiscardBar').waitFor({state: 'hidden'});
          await page.locator('[data-destination="remote"]').click();
          await page.waitForSelector('#settingsDiscardBar:not(.hidden)');
          await page.locator('#settingsDiscardBtn').click();
          await page.locator('#settingsBackdrop').waitFor({state: 'hidden'});
          await page.waitForSelector('#remoteDestination:not(.hidden)');
          report.checks.dirtyGuard = {badgeShown: true, keepEditingWorked: true, discardLeftCleanly: true};
        }

        report.errors.push(...fixture.errors);
      } finally {
        await fixture.context.close();
      }
    }

    // 2. First-party API token dialog (no native prompt)
    {
      const fixture = await newFixturePage(browser, args.baseUrl, playbackStatus(), {
        viewport: {width: 390, height: 844},
        colorScheme: 'dark',
        themeMode: 'dark',
      });
      try {
        const page = fixture.page;
        let nativeDialog = false;
        page.on('dialog', async (d) => { nativeDialog = true; await d.dismiss(); });
        await installExtraRoutes(page, {requireToken: 'phase5-token'});
        await page.waitForSelector('html.shellReady');

        await page.locator('#playPauseBtn').click();
        await page.waitForSelector('#authTokenBackdrop:not(.hidden)');
        const authShot = path.join(args.out, 'phase5-auth-dialog-phone.png');
        await page.screenshot({path: authShot, fullPage: false});
        report.screenshots.push(authShot);
        await page.locator('#authTokenCancelBtn').click();
        await page.locator('#authTokenBackdrop').waitFor({state: 'hidden'});
        const tokenAfterCancel = await page.evaluate(() => localStorage.getItem('relaytv_api_token') || '');
        if (tokenAfterCancel) throw new Error('Token was stored after Cancel');

        await page.locator('#hdrMenuBtn').click();
        await page.locator('#authTokenManageBtn').click();
        await page.waitForSelector('#authTokenBackdrop:not(.hidden)');
        await page.locator('#authTokenInput').fill('phase5-token');
        await page.locator('#authTokenSubmitBtn').click();
        await page.locator('#authTokenBackdrop').waitFor({state: 'hidden'});
        const tokenAfterSave = await page.evaluate(() => localStorage.getItem('relaytv_api_token') || '');
        if (tokenAfterSave !== 'phase5-token') throw new Error('Token was not stored after Submit');

        await page.locator('#hdrMenuBtn').click();
        await page.locator('#authTokenManageBtn').click();
        await page.waitForSelector('#authTokenForgetBtn:not(.hidden)');
        await page.locator('#authTokenForgetBtn').click();
        await page.locator('#authTokenBackdrop').waitFor({state: 'hidden'});
        const tokenAfterForget = await page.evaluate(() => localStorage.getItem('relaytv_api_token') || '');
        if (tokenAfterForget !== '') throw new Error('Token was not cleared after Forget');
        if (nativeDialog) throw new Error('Native window.prompt fired instead of first-party dialog');

        report.checks.authTokenDialog = {
          nativeDialogFired: nativeDialog,
          cancelRejected: true,
          submitStored: true,
          forgetCleared: true,
        };
        report.errors.push(...fixture.errors);
      } finally {
        await fixture.context.close();
      }
    }

    // 3. Peers overlay focus, Send/Copy mode, Escape close & focus restore
    {
      const fixture = await newFixturePage(browser, args.baseUrl, playbackStatus(), {
        viewport: {width: 390, height: 844},
        colorScheme: 'dark',
        themeMode: 'dark',
      });
      try {
        const page = fixture.page;
        await installExtraRoutes(page);
        await page.waitForSelector('html.shellReady');
        await page.waitForSelector('#queueSendBtn');

        await page.locator('#queueSendBtn').click();
        await page.waitForSelector('#peersBackdrop:not(.hidden)');
        await page.waitForSelector('.pmRow');

        const peersInitialFocus = await page.evaluate(() => document.activeElement?.id || '');
        const peersShot = path.join(args.out, 'phase5-peers-phone.png');
        await page.screenshot({path: peersShot, fullPage: false});
        report.screenshots.push(peersShot);

        await page.locator('.pmMode[data-peer-mode="copy"]').click();
        const copyHeading = await page.locator('#peersListHead').textContent();
        if (copyHeading !== 'Copy to:') throw new Error(`Expected Copy to:, got ${copyHeading}`);

        await page.keyboard.press('Escape');
        await page.locator('#peersBackdrop').waitFor({state: 'hidden'});
        const restoredFocus = await page.evaluate(() => document.activeElement?.id || '');
        report.checks.peersOverlay = {
          initialFocus: peersInitialFocus,
          copyHeading,
          closedOnEscape: true,
          restoredFocus,
        };
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
