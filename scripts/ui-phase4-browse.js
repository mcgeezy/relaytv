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

const PROVIDERS = [
  {
    name: 'jellyfin',
    button: '#browseJellyfinBtn',
    shell: '#jellyfinShell',
    backBtn: '#jfShellBackBtn',
    searchInput: '#jfSearchInput',
    card: '.jfItem',
    surfaceSelector: '#jellyfinCard',
    detail: '#jfDetail',
    detailOpenCheck: () => document.querySelector('#jfDetail')?.getAttribute('aria-hidden') === 'false',
    detailCloseCheck: () => document.querySelector('#jfDetail')?.getAttribute('aria-hidden') === 'true',
    detailTitle: '#jfDetailTitle',
    detailCloseBtn: '#jfDetail .jfDetailClose',
    expectedTitle: 'Fixture movie',
  },
  {
    name: 'plex',
    button: '#browsePlexBtn',
    shell: '#plexShell',
    backBtn: '#plexBackBtn',
    searchInput: '#plexSearchInput',
    card: '.plexCard',
    surfaceSelector: '.plexCard',
    detail: '#plexDetail',
    detailOpenCheck: () => document.querySelector('#plexDetail')?.getAttribute('aria-hidden') === 'false',
    detailCloseCheck: () => document.querySelector('#plexDetail')?.getAttribute('aria-hidden') === 'true',
    detailTitle: '#plexDetailTitle',
    detailCloseBtn: '#plexDetail .plexDetailClose',
    expectedTitle: 'Fixture Plex movie',
  },
  {
    name: 'iptv',
    button: '#browseIptvBtn',
    shell: '#iptvShell',
    backBtn: '#iptvBackBtn',
    searchInput: '#iptvSearch',
    card: '.iptvChannel',
    surfaceSelector: '#iptvBrowsePanel',
    detail: null,
  },
  {
    name: 'seerr',
    button: '#browseSeerrBtn',
    shell: '#seerrShell',
    backBtn: '#seerrBackBtn',
    searchInput: '#seerrSearchInput',
    card: '.seerrCard',
    surfaceSelector: '.seerrCard',
    detail: '#seerrDetail',
    detailOpenCheck: () => {
      const el = document.querySelector('#seerrDetail');
      return !!el && !el.classList.contains('hidden') && el.getAttribute('aria-hidden') === 'false';
    },
    detailCloseCheck: () => {
      const el = document.querySelector('#seerrDetail');
      return !el || el.classList.contains('hidden') || el.getAttribute('aria-hidden') === 'true';
    },
    detailTitle: '#seerrDetailTitle',
    detailCloseBtn: '#seerrDetail .seerrDetailClose',
    expectedTitle: 'Fixture discovery movie',
  },
];

async function verifyProviderThemeAndNavigation(browser, baseUrl, viewport, themeMode, report) {
  const viewportKey = `${viewport.width}x${viewport.height}`;
  for (const provider of PROVIDERS) {
    const fixture = await newFixturePage(browser, baseUrl, playbackStatus(), {
      viewport,
      colorScheme: themeMode,
      themeMode,
    });
    try {
      const {page} = fixture;
      await page.waitForFunction(() => document.documentElement.classList.contains('shellReady'));
      await page.locator('[data-destination="browse"]').click();
      await page.locator('#browseDestination:not(.hidden)').waitFor();
      await page.locator(`${provider.button}:not(.isUnavailable)`).waitFor({state: 'visible'});
      await page.locator(provider.button).click();
      await page.locator(`${provider.shell}:not(.hidden)`).waitFor();
      await page.locator(provider.card).first().waitFor({state: 'visible'});

      const shellDepth = await page.evaluate(() => window.RelayTV.runtime.layerNavigation.getDepth());
      if (shellDepth !== 1) {
        throw new Error(`${provider.name} (${themeMode} ${viewportKey}): expected layer depth 1 on open, got ${shellDepth}`);
      }

      await page.locator(provider.backBtn).focus();
      await page.keyboard.press('Tab');
      await page.keyboard.press('Shift+Tab');
      await page.waitForFunction((sel) => document.activeElement === document.querySelector(sel), provider.backBtn);
      const metrics = await page.evaluate((cfg) => {
        const shell = document.querySelector(cfg.shell);
        const surface = document.querySelector(cfg.surfaceSelector);
        const backBtn = document.querySelector(cfg.backBtn);
        const searchInput = document.querySelector(cfg.searchInput);
        const shellStyle = getComputedStyle(shell);
        const surfaceStyle = getComputedStyle(surface);
        const backStyle = getComputedStyle(backBtn);
        const backRect = backBtn.getBoundingClientRect();
        const searchRect = searchInput.getBoundingClientRect();
        return {
          shellBg: shellStyle.backgroundColor,
          shellColor: shellStyle.color,
          surfaceBg: surfaceStyle.backgroundColor,
          docOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
          shellOverflow: shell.scrollWidth > shell.clientWidth,
          nestedInteractive: shell.querySelectorAll('button button, a a, [role="button"] button, [role="button"] a').length,
          cardCount: shell.querySelectorAll(cfg.card).length,
          backHeight: Math.round(backRect.height),
          backWidth: Math.round(backRect.width),
          searchHeight: Math.round(searchRect.height),
          focusOutlineStyle: backStyle.outlineStyle,
          focusOutlineWidth: Number.parseFloat(backStyle.outlineWidth) || 0,
        };
      }, {
        shell: provider.shell,
        surfaceSelector: provider.surfaceSelector,
        backBtn: provider.backBtn,
        searchInput: provider.searchInput,
        card: provider.card,
      });

      if (metrics.docOverflow || metrics.shellOverflow) {
        throw new Error(`${provider.name} (${themeMode} ${viewportKey}): horizontal overflow detected: ${JSON.stringify(metrics)}`);
      }
      if (metrics.nestedInteractive !== 0) {
        throw new Error(`${provider.name} (${themeMode} ${viewportKey}): nested interactive elements: ${metrics.nestedInteractive}`);
      }
      if (metrics.cardCount !== 3) {
        throw new Error(`${provider.name} (${themeMode} ${viewportKey}): expected 3 cards, got ${metrics.cardCount}`);
      }
      if (metrics.backHeight < 44 || metrics.backWidth < 44 || metrics.searchHeight < 44) {
        throw new Error(`${provider.name} (${themeMode} ${viewportKey}): primary control below 44px target: ${JSON.stringify(metrics)}`);
      }
      if (metrics.focusOutlineStyle === 'none' || metrics.focusOutlineWidth < 3) {
        throw new Error(`${provider.name} (${themeMode} ${viewportKey}): focus ring below 3px: ${JSON.stringify(metrics)}`);
      }

      if (provider.detail) {
        // 1. Open detail and close via Escape
        await page.locator(provider.card).first().click({force: true});
        await page.waitForFunction(provider.detailOpenCheck);
        await page.locator(provider.detailTitle).waitFor({state: 'visible'});
        const titleText = (await page.locator(provider.detailTitle).textContent() || '').trim();
        if (titleText !== provider.expectedTitle) {
          throw new Error(`${provider.name} (${themeMode} ${viewportKey}): detail title '${titleText}' !== '${provider.expectedTitle}'`);
        }
        const detailDepth = await page.evaluate(() => window.RelayTV.runtime.layerNavigation.getDepth());
        if (detailDepth !== 2) {
          throw new Error(`${provider.name} (${themeMode} ${viewportKey}): expected layer depth 2 on detail open, got ${detailDepth}`);
        }
        await page.keyboard.press('Escape');
        await page.waitForFunction(provider.detailCloseCheck);
        await page.waitForFunction(() => window.RelayTV.runtime.layerNavigation.getDepth() === 1);
        const shellStillOpen = await page.locator(provider.shell).evaluate((el) => !el.classList.contains('hidden'));
        if (!shellStillOpen) {
          throw new Error(`${provider.name} (${themeMode} ${viewportKey}): Escape on detail closed the provider shell`);
        }

        // 2. Reopen detail and close via browser Back
        await page.locator(provider.card).first().click({force: true});
        await page.waitForFunction(provider.detailOpenCheck);
        await page.waitForFunction(() => window.RelayTV.runtime.layerNavigation.getDepth() === 2);
        await page.goBack();
        await page.waitForFunction(provider.detailCloseCheck);
        await page.waitForFunction(() => window.RelayTV.runtime.layerNavigation.getDepth() === 1);
      }

      // Close shell via Back button and verify layerNavigation depth returns to 0
      await page.locator(provider.backBtn).click();
      await page.waitForFunction((sel) => document.querySelector(sel)?.classList.contains('hidden'), provider.shell);
      await page.waitForFunction(() => window.RelayTV.runtime.layerNavigation.getDepth() === 0 && location.hash === '#browse');

      report.providers[provider.name] = report.providers[provider.name] || {};
      report.providers[provider.name][`${themeMode}_${viewportKey}`] = metrics;
      report.errors.push(...fixture.errors);
    } finally {
      await fixture.context.close();
    }
  }
}

function assertThemeContrastBetweenDarkAndLight(report) {
  for (const provider of PROVIDERS) {
    const entry = report.providers[provider.name] || {};
    for (const viewportKey of ['390x844', '1280x800']) {
      const dark = entry[`dark_${viewportKey}`];
      const light = entry[`light_${viewportKey}`];
      if (!dark || !light) {
        throw new Error(`missing dark/light metrics for ${provider.name} at ${viewportKey}`);
      }
      if (dark.shellBg === light.shellBg) {
        throw new Error(`${provider.name} (${viewportKey}): shellBg did not change between dark and light (${dark.shellBg})`);
      }
      if (dark.surfaceBg === light.surfaceBg) {
        throw new Error(`${provider.name} (${viewportKey}): surfaceBg did not change between dark and light (${dark.surfaceBg})`);
      }
      if (dark.shellColor === light.shellColor) {
        throw new Error(`${provider.name} (${viewportKey}): shellColor did not change between dark and light (${dark.shellColor})`);
      }
    }
  }
}

async function verifyUnavailableProviderRoutesToSettings(browser, baseUrl, report) {
  const disabledStatus = playbackStatus({
    iptv_enabled: false,
    iptv_channel_count: 0,
    jellyfin_enabled: false,
    jellyfin_running: false,
    jellyfin_connected: false,
    jellyfin_authenticated: false,
    jellyfin_catalog_ready: false,
    browse_enabled: false,
    browse_connected: false,
    browse_server_count: 0,
    browse_connected_count: 0,
  });
  const fixture = await newFixturePage(browser, baseUrl, disabledStatus, {
    viewport: {width: 390, height: 844},
    colorScheme: 'dark',
    themeMode: 'dark',
  });
  try {
    const {page} = fixture;
    await page.route((url) => {
      try { return new URL(url).pathname === '/integrations/plex/status'; } catch (_error) { return false; }
    }, (route) => route.fulfill({json: {enabled: false, linked: false, server_selected: false}}));
    await page.route((url) => {
      try { return new URL(url).pathname === '/integrations/seerr/status'; } catch (_error) { return false; }
    }, (route) => route.fulfill({json: {enabled: false, configured: false, reachable: false}}));
    await page.reload({waitUntil: 'domcontentloaded'});
    await page.waitForFunction(() => document.documentElement.classList.contains('shellReady'));

    await page.locator('[data-destination="browse"]').click();
    await page.locator('#browseDestination:not(.hidden)').waitFor();
    await page.waitForFunction(() => document.querySelector('#browseIptvBtn')?.classList.contains('isUnavailable'));

    await page.locator('#browseIptvBtn').click();
    await page.locator('#settingsBackdrop:not(.hidden)').waitFor();
    await page.waitForFunction(() => location.hash === '#settings');
    await page.locator('#settingsCloseBtn').click();
    await page.locator('#settingsBackdrop').waitFor({state: 'hidden'});
    await page.waitForFunction(() => location.hash === '#browse' && window.RelayTV.runtime.layerNavigation.getDepth() === 0);

    report.unavailableRouting = {
      ok: true,
      routedToSettings: true,
      returnedToBrowseCleanly: true,
    };
    report.errors.push(...fixture.errors);
  } finally {
    await fixture.context.close();
  }
}

async function main() {
  const rootDirectory = path.resolve(__dirname, '..');
  const baseUrl = option('base', 'http://127.0.0.1:8792').replace(/\/$/, '');
  const browserName = option('browser', 'chromium');
  const browserType = {chromium, firefox, webkit}[browserName];
  if (!browserType) throw new Error(`unknown browser: ${browserName}`);
  const reportPath = path.resolve(option('report', path.join(rootDirectory, 'docs', 'ui-modernization', 'phase-4-browse.json')));
  fs.mkdirSync(path.dirname(reportPath), {recursive: true});

  const browser = await browserType.launch({headless: true});
  const report = {
    schema: 1,
    capturedAt: new Date().toISOString(),
    baseUrl,
    runner: {
      playwright: require('playwright/package.json').version,
      browser: browserName,
      browserVersion: browser.version(),
      platform: `${process.platform}/${process.arch}`,
    },
    providers: {},
    unavailableRouting: {},
    errors: [],
  };

  try {
    for (const viewport of [{width: 390, height: 844}, {width: 1280, height: 800}]) {
      for (const themeMode of ['dark', 'light']) {
        await verifyProviderThemeAndNavigation(browser, baseUrl, viewport, themeMode, report);
      }
    }
    assertThemeContrastBetweenDarkAndLight(report);
    await verifyUnavailableProviderRoutesToSettings(browser, baseUrl, report);
  } finally {
    await browser.close();
  }

  if (report.errors.length) throw new Error(report.errors.join('\n'));
  fs.writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`);
  process.stdout.write(`${JSON.stringify({ok: true, reportPath, providers: Object.keys(report.providers)}, null, 2)}\n`);
}

if (require.main === module) {
  main().catch((error) => {
    process.stderr.write(`UI phase 4 browse check failed: ${error.stack || error}\n`);
    process.exitCode = 1;
  });
}
