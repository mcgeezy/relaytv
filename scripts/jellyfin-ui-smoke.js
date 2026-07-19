#!/usr/bin/env node
'use strict';

const { chromium } = require('playwright');

function option(name, fallback) {
  const prefix = `--${name}=`;
  const fromArg = process.argv.find((value) => value.startsWith(prefix));
  if (fromArg) return fromArg.slice(prefix.length);
  return process.env[name.toUpperCase().replaceAll('-', '_')] || fallback;
}

function check(condition, message) {
  if (!condition) throw new Error(message);
}

async function waitForItems(page, rowId) {
  await page.waitForFunction((id) => {
    return document.querySelectorAll(`.jfRow[data-row-id="${id}"] .jfItem`).length > 0;
  }, rowId, { timeout: 15000 });
}

async function waitForCatalog(page, rowId) {
  await page.waitForFunction((id) => {
    const row = document.querySelector(`.jfRow[data-row-id="${id}"]`);
    return row?.classList.contains('catalog') && row.querySelectorAll('.jfItem').length > 0;
  }, rowId, { timeout: 15000 });
}

async function runScenario(browser, baseUrl, scenario, screenshotDir) {
  const context = await browser.newContext({
    viewport: scenario.viewport,
    colorScheme: scenario.colorScheme,
  });
  const page = await context.newPage();
  const browserErrors = [];
  const unexpectedResponses = [];

  page.on('console', (message) => {
    if (message.type() === 'error') browserErrors.push(`console: ${message.text()}`);
  });
  page.on('pageerror', (error) => browserErrors.push(`page: ${error.message}`));
  page.on('requestfailed', (request) => {
    const errorText = String(request.failure()?.errorText || 'unknown');
    if (!errorText.includes('ERR_ABORTED')) {
      browserErrors.push(`request: ${errorText} ${request.url()}`);
    }
  });
  page.on('response', (response) => {
    if (response.status() >= 400) unexpectedResponses.push(`${response.status()} ${response.url()}`);
  });

  try {
    await page.goto(`${baseUrl}/ui?jfui=modern`, { waitUntil: 'domcontentloaded' });
    await page.locator('#jellyfinOpenBtn').waitFor({ state: 'visible', timeout: 15000 });
    await page.locator('#jellyfinOpenBtn').click();
    await page.locator('#jellyfinShell:not(.hidden)').waitFor();
    await waitForItems(page, 'continue_watching');

    const shellMode = await page.locator('#jellyfinShell').getAttribute('data-jf-ui');
    check(shellMode === 'modern', `${scenario.name}: modern shell was not selected`);
    check(
      (await page.locator('#jfConnectionLabel').textContent()) === 'Connected',
      `${scenario.name}: Jellyfin did not connect`,
    );

    await page.locator('.jfTabBtn[data-jf-tab="movies"]').click();
    await waitForCatalog(page, 'movies');
    const movieCards = page.locator('.jfRow[data-row-id="movies"] .jfItem');
    const initialMovies = await movieCards.count();
    check(initialMovies > 0 && initialMovies <= 48, `${scenario.name}: initial Movies page was not bounded`);

    const firstMovie = movieCards.first();
    await firstMovie.focus();
    const firstMovieId = await firstMovie.getAttribute('data-item-id');
    await page.keyboard.press('ArrowRight');
    const keyboardFocusId = await page.evaluate(() => document.activeElement?.dataset?.itemId || '');
    check(keyboardFocusId && keyboardFocusId !== firstMovieId, `${scenario.name}: card ArrowRight navigation failed`);

    const sentinelButton = page.locator('.jfCatalogSentinel .jfCatalogMoreBtn');
    const canLoadMore = (await sentinelButton.count()) > 0 && !(await sentinelButton.isDisabled());
    if (canLoadMore) {
      await page.locator('.jfCatalogSentinel').scrollIntoViewIfNeeded();
      await page.waitForFunction((initial) => {
        return document.querySelectorAll('.jfRow[data-row-id="movies"] .jfItem').length > initial;
      }, initialMovies, { timeout: 15000 });
    }
    const appendedMovies = await movieCards.count();
    const uniqueMovieIds = await movieCards.evaluateAll((cards) => {
      return new Set(cards.map((card) => card.dataset.itemId).filter(Boolean)).size;
    });
    check(uniqueMovieIds === appendedMovies, `${scenario.name}: appended Movies contain duplicate IDs`);

    const detailTarget = movieCards.nth(Math.min(1, appendedMovies - 1));
    await detailTarget.focus();
    const detailTargetId = await detailTarget.getAttribute('data-item-id');
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => document.querySelector('#jfDetail')?.getAttribute('aria-hidden') === 'false');
    await page.waitForFunction(() => document.querySelector('#jfDetail .jfDetailTitle'));
    await page.waitForFunction(() => {
      const detail = document.querySelector('#jfDetail');
      return detail && detail.contains(document.activeElement);
    });
    await page.waitForFunction((mobile) => {
      const rect = document.querySelector('#jfDetail')?.getBoundingClientRect();
      if (!rect) return false;
      return mobile
        ? Math.abs(rect.left) <= 1 && Math.abs(rect.right - innerWidth) <= 1 && Math.abs(rect.bottom - innerHeight) <= 1
        : Math.abs(rect.top) <= 1 && Math.abs(rect.right - innerWidth) <= 1 && Math.abs(rect.bottom - innerHeight) <= 1;
    }, scenario.mobile);
    const detailMetrics = await page.evaluate(() => {
      const detail = document.querySelector('#jfDetail');
      const rect = detail.getBoundingClientRect();
      return {
        left: Math.round(rect.left),
        top: Math.round(rect.top),
        right: Math.round(rect.right),
        bottom: Math.round(rect.bottom),
        width: Math.round(rect.width),
        focusedInside: detail.contains(document.activeElement),
      };
    });
    check(detailMetrics.focusedInside, `${scenario.name}: detail did not receive focus`);
    if (scenario.mobile) {
      check(detailMetrics.left === 0 && detailMetrics.right === scenario.viewport.width, `${scenario.name}: sheet width is not viewport anchored`);
      check(detailMetrics.bottom === scenario.viewport.height, `${scenario.name}: sheet is not bottom anchored`);
    } else {
      check(detailMetrics.top === 0 && detailMetrics.bottom === scenario.viewport.height, `${scenario.name}: drawer height is not viewport anchored`);
      check(detailMetrics.right === scenario.viewport.width, `${scenario.name}: drawer is not right anchored`);
    }
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => document.querySelector('#jfDetail')?.getAttribute('aria-hidden') === 'true');
    const restoredFocusId = await page.evaluate(() => document.activeElement?.dataset?.itemId || '');
    check(restoredFocusId === detailTargetId, `${scenario.name}: detail focus did not return to its card`);

    const query = String((await firstMovie.getAttribute('data-item-title')) || '').split(/\s+/)[0];
    const searchInput = page.locator('#jfSearchInput');
    await searchInput.fill(query);
    await page.waitForFunction(() => document.querySelector('.jfRow[data-row-id="search"]'));
    const searchTypes = await page.locator('.jfRow[data-row-id="search"] .jfItem').evaluateAll((cards) => {
      return [...new Set(cards.map((card) => card.dataset.itemType))];
    });
    check(searchTypes.every((type) => type === 'movie'), `${scenario.name}: Movies search leaked another media type`);
    await searchInput.fill('');
    await page.keyboard.press('Escape');
    await waitForCatalog(page, 'movies');

    await page.locator('.jfTabBtn[data-jf-tab="tv"]').click();
    await waitForItems(page, 'tv_series');
    const seriesCards = page.locator('.jfRow[data-row-id="tv_series"] .jfItem');
    const seriesCount = await seriesCards.count();
    check(seriesCount > 0 && seriesCount <= 48, `${scenario.name}: TV series page was not bounded`);
    await seriesCards.first().focus();
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => document.querySelector('.jfRow[data-row-id="tv_selection"]'));
    await waitForItems(page, 'tv_episodes');
    const episodeCount = await page.locator('.jfRow[data-row-id="tv_episodes"] .jfItem').count();
    check(episodeCount > 0, `${scenario.name}: series hierarchy did not render episodes`);

    const a11y = await page.evaluate(() => {
      const shell = document.querySelector('#jellyfinShell');
      const nav = document.querySelector('.jfTabs');
      const catalog = document.querySelector('.jfCatalogScroller');
      return {
        bodyOverflow: document.body.scrollWidth > document.body.clientWidth,
        shellOverflow: shell.scrollWidth > shell.clientWidth,
        nestedCatalogScroll: catalog && catalog.scrollHeight > catalog.clientHeight + 8 &&
          ['auto', 'scroll'].includes(getComputedStyle(catalog).overflowY),
        nestedInteractive: document.querySelectorAll('button button, a a, [role="button"] button, [role="button"] a').length,
        navPosition: getComputedStyle(nav).position,
      };
    });
    check(!a11y.bodyOverflow && !a11y.shellOverflow, `${scenario.name}: horizontal viewport overflow detected`);
    check(!a11y.nestedCatalogScroll, `${scenario.name}: nested catalog scrolling returned`);
    check(a11y.nestedInteractive === 0, `${scenario.name}: nested interactive controls detected`);
    check(a11y.navPosition === (scenario.mobile ? 'fixed' : 'sticky'), `${scenario.name}: responsive navigation position is wrong`);
    check(browserErrors.length === 0, `${scenario.name}: browser errors: ${browserErrors.join('; ')}`);
    check(unexpectedResponses.length === 0, `${scenario.name}: HTTP errors: ${unexpectedResponses.join('; ')}`);

    if (screenshotDir) {
      await page.screenshot({ path: `${screenshotDir}/${scenario.name}.png` });
    }

    return {
      name: scenario.name,
      viewport: scenario.viewport,
      colorScheme: scenario.colorScheme,
      initialMovies,
      appendedMovies,
      uniqueMovieIds,
      seriesCount,
      episodeCount,
      detailMetrics,
      a11y,
    };
  } finally {
    await context.close();
  }
}

async function runRecoveryScenario(browser, baseUrl) {
  const context = await browser.newContext({ viewport: { width: 1024, height: 768 }, colorScheme: 'dark' });
  const page = await context.newPage();
  let outage = true;
  let homeCalls = 0;
  await page.route('**/jellyfin/home*', async (route) => {
    homeCalls += 1;
    if (outage) {
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'simulated catalog outage' }),
      });
    } else {
      await route.continue();
    }
  });
  await page.route('**/integrations/jellyfin/register', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ ok: true }),
  }));
  try {
    await page.goto(`${baseUrl}/ui?jfui=modern`, { waitUntil: 'domcontentloaded' });
    await page.locator('#jellyfinOpenBtn').waitFor({ state: 'visible', timeout: 15000 });
    await page.locator('#jellyfinOpenBtn').click();
    await page.locator('.jfUnavailable').waitFor();
    check(
      (await page.locator('#jfConnectionLabel').textContent()) === 'Unavailable',
      'recovery: simulated outage did not set the connection state',
    );
    outage = false;
    await page.locator('.jfReconnectInline').click();
    await waitForItems(page, 'continue_watching');
    check(
      (await page.locator('#jfConnectionLabel').textContent()) === 'Connected',
      'recovery: reconnect did not restore the connection state',
    );
    return { name: 'offline-recovery', homeCalls, recoveredRows: await page.locator('.jfRow').count() };
  } finally {
    await context.close();
  }
}

async function main() {
  const wsEndpoint = option('ws', 'ws://10.55.55.98:3000/');
  const baseUrl = option('base', 'http://10.55.55.2:8787').replace(/\/$/, '');
  const screenshotDir = option('screenshots', '');
  const scenarios = [
    { name: 'phone-dark', viewport: { width: 390, height: 844 }, colorScheme: 'dark', mobile: true },
    { name: 'desktop-light', viewport: { width: 1440, height: 1000 }, colorScheme: 'light', mobile: false },
  ];
  const browser = await chromium.connect(wsEndpoint);
  try {
    const results = [];
    for (const scenario of scenarios) {
      results.push(await runScenario(browser, baseUrl, scenario, screenshotDir));
    }
    results.push(await runRecoveryScenario(browser, baseUrl));
    process.stdout.write(`${JSON.stringify({ ok: true, wsEndpoint, baseUrl, results }, null, 2)}\n`);
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  process.stderr.write(`Jellyfin UI smoke failed: ${error.stack || error}\n`);
  process.exit(1);
});
