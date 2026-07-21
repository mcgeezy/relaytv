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
    const livePanel = await page.evaluate(() => {
      const liveState = {
        state: 'playing',
        playing: true,
        paused: false,
        has_now_playing: true,
        now_playing: {title: 'Smoke Live News', provider: 'iptv', is_live: true},
        position: 0,
        duration: 30,
        queue: [],
        queue_length: 0,
      };
      renderStatus(liveState);
      renderStatus({...liveState, position: 29.8, duration: 30.1});
      const card = document.querySelector('#nowTopCard');
      const progress = document.querySelector('#progress');
      const tag = document.querySelector('#nowStateTag');
      return {
        cardLive: card.classList.contains('isLive'),
        position: document.querySelector('#pos').textContent,
        duration: document.querySelector('#dur').textContent,
        tag: tag.textContent,
        tagVisible: !tag.classList.contains('hidden'),
        progressDisplay: getComputedStyle(progress).display,
        seekDisabled: progress.getAttribute('aria-disabled'),
      };
    });
    check(livePanel.cardLive, `${scenario.name}: now-playing card did not enter live mode`);
    check(livePanel.position === 'LIVE' && livePanel.duration === 'Streaming', `${scenario.name}: live time row is unstable`);
    check(livePanel.tag === 'Live' && livePanel.tagVisible, `${scenario.name}: live state tag is missing`);
    check(livePanel.progressDisplay === 'none' && livePanel.seekDisabled === 'true', `${scenario.name}: live seek control is still active`);
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
    await firstCard.locator('.iptvFav').click();
    await page.waitForFunction(() => document.querySelector('.iptvChannel .iptvFav')?.getAttribute('aria-pressed') === 'true');
    await page.locator('[data-iptv-view="favorites"]').click();
    await page.waitForFunction(() => document.querySelectorAll('.iptvChannel').length === 1);
    check((await page.locator('.iptvChannel .iptvFav').first().getAttribute('aria-pressed')) === 'true', `${scenario.name}: favorite view did not persist selection`);

    await page.locator('[data-iptv-view="all"]').click();
    await page.waitForFunction(() => document.querySelectorAll('.iptvChannel').length === 3);

    // The overflow menu opens above neighbouring tiles and holds queue actions.
    const menuCard = page.locator('.iptvChannel').nth(1);
    await menuCard.locator('.iptvKebab').click();
    await menuCard.locator('.iptvMenu:not(.hidden)').waitFor();
    check(await menuCard.locator('[data-action="play_next"]').isVisible(), `${scenario.name}: overflow menu missing queue actions`);
    await page.keyboard.press('Escape');

    // Discover browses the full catalog pulled from your sources.
    await page.locator('[data-iptv-tab="discover"]').click();
    await page.waitForFunction(() => document.querySelectorAll('#iptvDiscoverGrid .iptvChannel').length === 3);
    await page.locator('#iptvDiscoverSearch').fill('Music');
    await page.waitForFunction(() => document.querySelectorAll('#iptvDiscoverGrid .iptvChannel').length === 1);
    check((await page.locator('#iptvDiscoverGrid .iptvChannelTitle').textContent()).includes('Music'), `${scenario.name}: discover search mismatch`);
    await page.locator('#iptvDiscoverSearch').fill('');
    await page.waitForFunction(() => document.querySelectorAll('#iptvDiscoverGrid .iptvChannel').length === 3);

    // Sources hosts the source manager and the free provider directory.
    await page.locator('[data-iptv-tab="sources"]').click();
    await page.waitForFunction(() => document.querySelectorAll('.iptvSourceCard').length >= 1);
    check((await page.locator('.iptvSourceCard h3').allTextContents()).includes('RelayTV UI smoke'), `${scenario.name}: source manager missing smoke source`);
    await page.locator('#iptvDirectorySearch').fill('news');
    await page.waitForFunction(() => document.querySelectorAll('.iptvDirectoryCard').length === 1);
    check((await page.locator('.iptvDirectoryCard h3').textContent()).includes('News'), `${scenario.name}: provider search mismatch`);

    const layout = await page.evaluate(() => ({
      bodyOverflow: document.body.scrollWidth > document.body.clientWidth,
      shellOverflow: document.querySelector('#iptvShell').scrollWidth > document.querySelector('#iptvShell').clientWidth,
      nestedInteractive: document.querySelectorAll('button button, a a, [role="button"] button, [role="button"] a').length,
    }));
    check(!layout.bodyOverflow && !layout.shellOverflow, `${scenario.name}: horizontal overflow detected`);
    check(layout.nestedInteractive === 0, `${scenario.name}: nested interactive controls detected`);
    check(errors.length === 0, `${scenario.name}: browser errors: ${errors.join('; ')}`);

    if (screenshotDir) await page.screenshot({ path: `${screenshotDir}/iptv-${scenario.name}.png`, fullPage: true });
    return { name: scenario.name, initialNames, livePanel, layout };
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
