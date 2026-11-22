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

async function openAbout(page) {
  await page.locator('#hdrMenuBtn').click();
  await page.locator('#aboutBtn').click();
  await page.locator('#aboutBackdrop:not(.hidden)').waitFor();
}

async function main() {
  const rootDirectory = path.resolve(__dirname, '..');
  const baseUrl = option('base', 'http://127.0.0.1:8787').replace(/\/$/, '');
  const browserName = option('browser', 'chromium');
  const browserType = {chromium, firefox, webkit}[browserName];
  if (!browserType) throw new Error(`unknown browser: ${browserName}`);
  const reportPath = path.resolve(option(
    'report',
    path.join(rootDirectory, 'docs', 'ui-modernization', 'phase-2-seams.json'),
  ));
  fs.mkdirSync(path.dirname(reportPath), {recursive:true});
  const browser = await browserType.launch({headless:true});
  const fixture = await newFixturePage(browser, baseUrl, playbackStatus(), {
    viewport:{width:390, height:844},
    colorScheme:'dark',
    themeMode:'dark',
  });
  const report = {
    schema:1,
    capturedAt:new Date().toISOString(),
    baseUrl,
    runner:{
      playwright:require('playwright/package.json').version,
      browser:browserName,
      browserVersion:browser.version(),
      platform:`${process.platform}/${process.arch}`,
    },
    assertions:{},
    errors:fixture.errors,
  };
  try {
    const page = fixture.page;
    await page.waitForFunction(() => {
      return window.RelayTV?.api
        && window.RelayTV?.store
        && window.RelayTV?.navigation
        && window.RelayTV?.overlays
        && window.RelayTV?.theme
        && window.RelayTV?.runtime?.statusStore?.getSnapshot();
    });
    report.assertions.namespaces = await page.evaluate(() => Object.keys(window.RelayTV).sort());
    report.assertions.packagedAssets = await page.evaluate(() => {
      const names = performance.getEntriesByType('resource').map((entry) => entry.name);
      return ['api.js', 'store.js', 'navigation.js', 'overlays.js', 'remote.js', 'settings.js']
        .filter((asset) => names.some((name) => name.includes(`/static/ui/${asset}?v=`)));
    });
    if (report.assertions.packagedAssets.length !== 6) {
      throw new Error(`missing packaged assets: ${JSON.stringify(report.assertions.packagedAssets)}`);
    }
    report.assertions.statusTitle = await page.evaluate(
      () => window.RelayTV.runtime.statusStore.getSnapshot()?.now_playing?.title,
    );
    if (report.assertions.statusTitle !== 'Current fixture episode') {
      throw new Error(`status store did not receive fixture: ${report.assertions.statusTitle}`);
    }

    await openAbout(page);
    report.assertions.initialDialogFocus = await page.evaluate(() => document.activeElement?.id);
    if (report.assertions.initialDialogFocus !== 'aboutCloseBtn') {
      throw new Error(`unexpected initial focus: ${report.assertions.initialDialogFocus}`);
    }
    await page.locator('#aboutSupportLink').focus();
    await page.keyboard.press('Tab');
    report.assertions.trappedDialogFocus = await page.evaluate(() => document.activeElement?.id);
    if (report.assertions.trappedDialogFocus !== 'aboutCloseBtn') {
      throw new Error(`dialog focus escaped: ${report.assertions.trappedDialogFocus}`);
    }
    await page.keyboard.press('Escape');
    await page.locator('#aboutBackdrop').waitFor({state:'hidden'});
    await page.waitForFunction(() => document.activeElement?.id === 'hdrMenuBtn');
    report.assertions.restoredDialogFocus = await page.evaluate(() => document.activeElement?.id);
    report.assertions.navigationDepth = await page.evaluate(
      () => window.RelayTV.runtime.layerNavigation.getDepth(),
    );
    if (report.assertions.navigationDepth !== 0) {
      throw new Error(`navigation depth leaked: ${report.assertions.navigationDepth}`);
    }

    for (let attempt = 0; attempt < 2; attempt += 1) {
      await openAbout(page);
      await page.locator('#aboutCloseBtn').click();
      await page.locator('#aboutBackdrop').waitFor({state:'hidden'});
    }
    report.assertions.repeatDialogDepth = await page.evaluate(
      () => window.RelayTV.runtime.layerNavigation.getDepth(),
    );
    if (report.assertions.repeatDialogDepth !== 0) {
      throw new Error(`repeated dialog leaked navigation depth: ${report.assertions.repeatDialogDepth}`);
    }
    report.assertions.settingsController = await page.evaluate(
      () => typeof window.bindSettingsUi === 'function' && typeof window.loadSettingsUi === 'function',
    );
    if (!report.assertions.settingsController) throw new Error('settings controller is unavailable');
  } finally {
    await fixture.context.close();
    await browser.close();
  }
  if (report.errors.length) throw new Error(report.errors.join('\n'));
  fs.writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`);
  process.stdout.write(`${JSON.stringify({ok:true, reportPath}, null, 2)}\n`);
}

main().catch((error) => {
  process.stderr.write(`UI phase 2 seam check failed: ${error.stack || error}\n`);
  process.exitCode = 1;
});
