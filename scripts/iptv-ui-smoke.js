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

async function browserFor(wsEndpoint) {
  if (option('local', '0') === '1') return chromium.launch({ headless: true });
  return chromium.connect(wsEndpoint);
}

async function runScenario(browser, baseUrl, scenario, screenshotDir) {
  const context = await browser.newContext({ viewport: scenario.viewport, colorScheme: scenario.colorScheme });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(`page: ${error.message}`));
  page.on('console', (message) => { if (message.type() === 'error') errors.push(`console: ${message.text()}`); });
  try {
    await page.goto(`${baseUrl}/ui`, { waitUntil: 'domcontentloaded' });
    await page.evaluate(async () => {
      const catalog = await fetch('/iptv/channels?visibility=all&limit=500').then((response) => response.json());
      for (const channel of (catalog.items || [])) {
        if (!channel.favorite && !channel.hidden) continue;
        await fetch(`/iptv/channels/${encodeURIComponent(channel.channel_id)}`, {
          method: 'PATCH',
          headers: {'Content-Type':'application/json'},
          body: JSON.stringify({source_id:channel.source_id, favorite:false, hidden:false}),
        });
      }
    });
    await page.locator('#iptvOpenBtn').waitFor({ state: 'visible', timeout: 15000 });
    await page.locator('#iptvOpenBtn').click();
    await page.locator('#iptvShell:not(.hidden)').waitFor();
    await page.waitForFunction(() => document.querySelectorAll('.iptvChannel').length === 3);

    const initialNames = await page.locator('.iptvChannelTitle').allTextContents();
    check(initialNames.length === 3, `${scenario.name}: expected three smoke channels`);

    await page.locator('#iptvSearch').fill('Music');
    await page.waitForFunction(() => document.querySelectorAll('.iptvChannel').length === 1);
    check((await page.locator('.iptvChannelTitle').textContent()).includes('Music'), `${scenario.name}: search result mismatch`);
    await page.locator('#iptvSearch').fill('');
    await page.waitForFunction(() => document.querySelectorAll('.iptvChannel').length === 3);

    const firstCard = page.locator('.iptvChannel').first();
    await firstCard.locator('[data-action="favorite"]').click();
    await page.waitForFunction(() => document.querySelector('.iptvChannel .iptvBadge')?.textContent === 'Favorite');
    await page.locator('[data-iptv-tab="favorites"]').click();
    await page.waitForFunction(() => document.querySelectorAll('.iptvChannel').length === 1);
    check((await page.locator('.iptvChannel .iptvBadge').allTextContents()).includes('Favorite'), `${scenario.name}: favorite view did not persist selection`);

    await page.locator('[data-iptv-tab="channels"]').click();
    await page.waitForFunction(() => document.querySelectorAll('.iptvChannel').length === 3);
    await page.locator('.iptvChannel').nth(1).locator('[data-action="hidden"]').click();
    await page.waitForFunction(() => document.querySelectorAll('.iptvChannel').length === 2);
    await page.locator('[data-iptv-tab="hidden"]').click();
    await page.waitForFunction(() => document.querySelectorAll('.iptvChannel').length === 1);
    check((await page.locator('.iptvChannel .iptvBadge').allTextContents()).includes('Hidden'), `${scenario.name}: hidden view did not persist selection`);

    await page.locator('[data-iptv-tab="discover"]').click();
    await page.locator('#iptvDirectorySearch').fill('news');
    await page.waitForFunction(() => document.querySelectorAll('.iptvDirectoryCard').length === 1);
    check((await page.locator('.iptvDirectoryCard h3').textContent()).includes('News'), `${scenario.name}: provider search mismatch`);

    await page.locator('[data-iptv-tab="sources"]').click();
    await page.waitForFunction(() => document.querySelectorAll('.iptvSourceCard').length >= 1);
    check((await page.locator('.iptvSourceCard h3').allTextContents()).includes('RelayTV UI smoke'), `${scenario.name}: source manager missing smoke source`);

    const layout = await page.evaluate(() => ({
      bodyOverflow: document.body.scrollWidth > document.body.clientWidth,
      shellOverflow: document.querySelector('#iptvShell').scrollWidth > document.querySelector('#iptvShell').clientWidth,
      nestedInteractive: document.querySelectorAll('button button, a a, [role="button"] button, [role="button"] a').length,
    }));
    check(!layout.bodyOverflow && !layout.shellOverflow, `${scenario.name}: horizontal overflow detected`);
    check(layout.nestedInteractive === 0, `${scenario.name}: nested interactive controls detected`);
    check(errors.length === 0, `${scenario.name}: browser errors: ${errors.join('; ')}`);

    if (screenshotDir) await page.screenshot({ path: `${screenshotDir}/iptv-${scenario.name}.png`, fullPage: true });
    return { name: scenario.name, initialNames, layout };
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
      { name: 'phone-dark', viewport: { width: 390, height: 844 }, colorScheme: 'dark' },
      { name: 'desktop-light', viewport: { width: 1440, height: 1000 }, colorScheme: 'light' },
    ];
    const results = [];
    for (const scenario of scenarios) results.push(await runScenario(browser, baseUrl, scenario, screenshotDir));
    process.stdout.write(`${JSON.stringify({ ok: true, wsEndpoint, baseUrl, results }, null, 2)}\n`);
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  process.stderr.write(`IPTV UI smoke failed: ${error.stack || error}\n`);
  process.exit(1);
});
