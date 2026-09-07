#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
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

async function waitForCards(page) {
  return page.waitForFunction(() => {
    const status = document.querySelector('#plexBrowseStatus');
    const count = document.querySelectorAll('#plexContent .plexCard').length;
    return count > 0 && !status?.classList.contains('err') ? count : 0;
  }, null, {timeout:15000}).then(value => value.jsonValue());
}

async function runScenario(browser, baseUrl, scenario, screenshotDir) {
  const context = await browser.newContext({
    viewport: scenario.viewport,
    colorScheme: scenario.colorScheme,
  });
  const page = await context.newPage();
  const browserErrors = [];
  const unexpectedResponses = [];
  const artworkFailures = [];
  let simulatedActionFailure = false;

  page.on('console', message => {
    if (message.type() !== 'error') return;
    if (simulatedActionFailure && message.text().includes('502')) return;
    browserErrors.push(`console: ${message.text()}`);
  });
  page.on('pageerror', error => browserErrors.push(`page: ${error.message}`));
  page.on('requestfailed', request => {
    const errorText = String(request.failure()?.errorText || 'unknown');
    if (errorText.includes('ERR_ABORTED')) return;
    if (request.url().includes('/plex/artwork/')) artworkFailures.push(`${errorText} ${request.url()}`);
    else browserErrors.push(`request: ${errorText} ${request.url()}`);
  });
  page.on('response', response => {
    if (response.status() >= 400 && !response.url().endsWith('/plex/items/action')) {
      unexpectedResponses.push(`${response.status()} ${response.url()}`);
    }
  });

  try {
    await page.goto(`${baseUrl}/ui`, {waitUntil:'domcontentloaded'});
    await page.locator('#plexOpenBtn').waitFor({state:'visible', timeout:15000});
    await page.locator('#plexOpenBtn').click();
    await page.locator('#plexShell:not(.hidden)').waitFor();
    const homeCards = await waitForCards(page);
    check(homeCards > 0, `${scenario.name}: Plex Home is empty`);

    await page.locator('.plexTab[data-plex-view="libraries"]').click();
    await page.locator('.plexLibrary').first().waitFor({timeout:15000});
    const libraries = await page.locator('.plexLibrary').count();
    check(libraries > 0, `${scenario.name}: no Plex libraries were rendered`);
    const movieLibrary = page.locator('.plexLibrary').filter({hasText:'Movies'}).first();
    if (await movieLibrary.count()) await movieLibrary.click();
    else await page.locator('.plexLibrary').first().click();

    const initialItems = await waitForCards(page);
    check(initialItems > 1 && initialItems <= 60, `${scenario.name}: initial library page was not bounded`);
    const cards = page.locator('#plexContent .plexCard');
    const firstCard = cards.first();
    const cardLayout = await firstCard.evaluate(card => {
      const poster = card.querySelector('.plexPoster');
      const body = card.querySelector('.plexCardBody');
      const cardRect = card.getBoundingClientRect();
      const bodyRect = body.getBoundingClientRect();
      return {
        display:getComputedStyle(card).display,
        direction:getComputedStyle(card).flexDirection,
        cardWidth:Math.round(cardRect.width),
        bodyWidth:Math.round(bodyRect.width),
        posterWidth:Math.round(poster.getBoundingClientRect().width),
      };
    });
    check(cardLayout.display === 'flex' && cardLayout.direction === 'column', `${scenario.name}: cards are not vertical`);
    check(cardLayout.bodyWidth >= cardLayout.cardWidth - 3, `${scenario.name}: card metadata is horizontally clipped`);
    check(cardLayout.posterWidth >= cardLayout.cardWidth - 3, `${scenario.name}: poster does not fill its card`);

    await firstCard.focus();
    const firstId = await firstCard.getAttribute('data-item-id');
    await page.keyboard.press('ArrowRight');
    const focusedId = await page.evaluate(() => document.activeElement?.dataset?.itemId || '');
    const arrowMovedFocus = !!focusedId && focusedId !== firstId;
    check(arrowMovedFocus, `${scenario.name}: card ArrowRight navigation failed`);

    const detailTarget = cards.nth(1);
    const detailTargetId = await detailTarget.getAttribute('data-item-id');
    const detailTargetTitle = await detailTarget.getAttribute('data-item-title');
    await detailTarget.focus();
    await page.route('**/plex/items/action', route => route.fulfill({
      status:502,
      contentType:'application/json',
      body:JSON.stringify({detail:{message:'Playback preparation could not reach Plex.'}}),
    }));
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => document.querySelector('#plexDetail')?.getAttribute('aria-hidden') === 'false');
    await page.locator('#plexDetail .plexDetailClose').waitFor({timeout:15000});
    const detailMetrics = await page.locator('#plexDetail').evaluate(detail => {
      const rect = detail.getBoundingClientRect();
      return {
        left:Math.round(rect.left),
        top:Math.round(rect.top),
        right:Math.round(rect.right),
        bottom:Math.round(rect.bottom),
        focusedInside:detail.contains(document.activeElement),
      };
    });
    check(detailMetrics.focusedInside, `${scenario.name}: detail did not receive focus`);
    if (scenario.mobile) {
      check(detailMetrics.left === 0 && detailMetrics.right === scenario.viewport.width, `${scenario.name}: mobile detail width is not viewport anchored`);
      check(detailMetrics.top === 0 && detailMetrics.bottom === scenario.viewport.height, `${scenario.name}: mobile detail height is not viewport anchored`);
    } else {
      check(detailMetrics.left > 0 && detailMetrics.right < scenario.viewport.width, `${scenario.name}: desktop detail lacks side margins`);
      check(detailMetrics.top > 0 && detailMetrics.bottom < scenario.viewport.height, `${scenario.name}: desktop detail lacks vertical margins`);
    }
    if (screenshotDir) await page.screenshot({path:`${screenshotDir}/${scenario.name}-detail.png`});

    const preActionErrors = browserErrors.slice();
    const action = page.locator('#plexDetail .plexAction').first();
    check(await action.count() === 1, `${scenario.name}: playable detail has no actions`);
    simulatedActionFailure = true;
    await action.click();
    await page.waitForFunction(() => document.querySelector('.plexActionResult')?.textContent.includes('could not reach Plex'));
    simulatedActionFailure = false;
    check(!(await action.isDisabled()), `${scenario.name}: failed action stayed disabled`);
    check(preActionErrors.length === 0, `${scenario.name}: browser errors before simulated action: ${preActionErrors.join('; ')}`);

    await page.keyboard.press('Escape');
    await page.waitForFunction(() => document.querySelector('#plexDetail')?.getAttribute('aria-hidden') === 'true');
    await page.waitForFunction(id => document.activeElement?.dataset?.itemId === id, detailTargetId);
    const focusReturned = await page.evaluate(id => document.activeElement?.dataset?.itemId === id, detailTargetId);
    check(focusReturned, `${scenario.name}: detail focus did not return to its card`);

    const searchInput = page.locator('#plexSearchInput');
    await searchInput.fill(detailTargetTitle || 'movie');
    await page.waitForFunction(() => {
      const status = document.querySelector('#plexBrowseStatus');
      return /result/.test(status?.textContent || '') && document.querySelectorAll('#plexContent .plexCard').length > 0;
    }, null, {timeout:15000});
    const searchItems = await page.locator('#plexContent .plexCard').count();
    check(await page.locator('#plexMoreBtn').evaluate(button => button.classList.contains('hidden')), `${scenario.name}: Load more remained visible after search`);
    if (screenshotDir) await page.screenshot({path:`${screenshotDir}/${scenario.name}-search.png`});

    const a11y = await page.evaluate(() => {
      const shell = document.querySelector('#plexShell');
      const toolbar = document.querySelector('.plexToolbar');
      return {
        bodyOverflow:document.body.scrollWidth > document.body.clientWidth,
        shellOverflow:shell.scrollWidth > shell.clientWidth,
        nestedInteractive:document.querySelectorAll('button button, a a, [role="button"] button, [role="button"] a').length,
        toolbarDirection:getComputedStyle(toolbar).flexDirection,
      };
    });
    check(!a11y.bodyOverflow && !a11y.shellOverflow, `${scenario.name}: horizontal viewport overflow detected`);
    check(a11y.nestedInteractive === 0, `${scenario.name}: nested interactive controls detected`);
    check(a11y.toolbarDirection === (scenario.mobile ? 'column' : 'row'), `${scenario.name}: responsive toolbar layout is wrong`);
    check(browserErrors.length === 0, `${scenario.name}: browser errors: ${browserErrors.join('; ')}`);
    check(unexpectedResponses.length === 0, `${scenario.name}: HTTP errors: ${unexpectedResponses.join('; ')}`);

    return {
      name:scenario.name,
      viewport:scenario.viewport,
      colorScheme:scenario.colorScheme,
      homeCards,
      libraries,
      initialItems,
      searchItems,
      arrowMovedFocus,
      focusReturned,
      cardLayout,
      detailMetrics,
      a11y,
      artworkFailures:artworkFailures.length,
    };
  } finally {
    await context.close();
  }
}

async function main() {
  const wsEndpoint = option('ws', 'ws://10.55.55.98:3000/');
  const baseUrl = option('base', 'http://10.55.55.2:8787').replace(/\/$/, '');
  const screenshotDir = option('screenshots', '');
  if (screenshotDir) fs.mkdirSync(screenshotDir, {recursive:true});
  const scenarios = [
    {name:'phone-dark', viewport:{width:390, height:844}, colorScheme:'dark', mobile:true},
    {name:'desktop-light', viewport:{width:1440, height:900}, colorScheme:'light', mobile:false},
  ];
  const browser = wsEndpoint === 'launch'
    ? await chromium.launch({headless:true})
    : await chromium.connect(wsEndpoint);
  try {
    const results = [];
    for (const scenario of scenarios) results.push(await runScenario(browser, baseUrl, scenario, screenshotDir));
    process.stdout.write(`${JSON.stringify({ok:true, wsEndpoint, baseUrl, results}, null, 2)}\n`);
  } finally {
    await browser.close();
  }
}

main().catch(error => {
  process.stderr.write(`Plex UI smoke failed: ${error.stack || error}\n`);
  process.exit(1);
});
