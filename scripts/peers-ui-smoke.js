#!/usr/bin/env node
'use strict';

// Browser smoke for the send-to-device sheet.
//
// Needs two RelayTV servers: --base is the sender whose /ui is driven, --peer
// is the receiving device that gets added and sent to. Run against a remote
// browser service (default) or a local chromium with --local=1.

const { chromium } = require('playwright');

// Endpoints that answer 4xx/5xx on a device without the matching integration
// or without cached artwork. Unrelated to the send-to-device sheet.
const IGNORED_ERROR_PATHS = ['/jellyfin/', '/integrations/', '/thumbs/', '/idle/weather'];

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

async function resetState(page, baseUrl, peerUrl) {
  await page.evaluate(async ({ peerUrl }) => {
    const listing = await fetch('/peers').then((r) => r.json());
    for (const peer of (listing.peers || [])) {
      await fetch(`/peers/${encodeURIComponent(peer.id)}`, { method: 'DELETE' });
    }
    await fetch('/clear', { method: 'POST' });
    for (const url of ['https://example.com/smoke-one', 'https://example.com/smoke-two']) {
      await fetch('/enqueue', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url }),
      });
    }
    return peerUrl;
  }, { peerUrl });
}

async function runScenario(browser, baseUrl, peerUrl, scenario, screenshotDir) {
  const context = await browser.newContext({ viewport: scenario.viewport, colorScheme: scenario.colorScheme });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(`page: ${error.message}`));
  page.on('console', (message) => {
    // Network failures arrive as untyped "Failed to load resource" text; the
    // response listener below judges those with the URL in hand.
    if (message.type() !== 'error') return;
    if (message.text().includes('Failed to load resource')) return;
    errors.push(`console: ${message.text()}`);
  });
  page.on('response', (response) => {
    if (response.status() < 400) return;
    const url = response.url();
    // Only this device's own responses are the feature's business, and these
    // paths are legitimately unavailable on a device without the matching
    // integration or cached artwork.
    if (!url.startsWith(baseUrl)) return;
    if (IGNORED_ERROR_PATHS.some((path) => url.includes(path))) return;
    errors.push(`http ${response.status()}: ${url}`);
  });
  try {
    await page.goto(`${baseUrl}/ui`, { waitUntil: 'domcontentloaded' });
    // Both sides start clean so the assertion counts belong to this run.
    await fetch(`${peerUrl}/clear`, { method: 'POST' });
    await resetState(page, baseUrl, peerUrl);
    await page.reload({ waitUntil: 'domcontentloaded' });

    // The Send pill only appears once there is something to send.
    await page.locator('#queueSendBtn:not(.hidden)').waitFor({ timeout: 15000 });
    await page.locator('#queueSendBtn').click();
    await page.locator('#peersBackdrop:not(.hidden)').waitFor();
    check(
      (await page.locator('#peersSubtitle').textContent()).includes('2 items'),
      `${scenario.name}: sheet did not report the queue size`,
    );
    // Wait for the settled list: the row is a loading placeholder first.
    await page.waitForFunction(
      () => (document.querySelector('.pmEmpty')?.textContent || '').includes('No other devices yet'),
    );

    // Test connection names the device before it is saved.
    await page.locator('#peersAddToggle').click();
    await page.locator('#peersAddForm:not(.hidden)').waitFor();
    await page.locator('#peerUrlInput').fill(peerUrl);
    await page.locator('#peerTestBtn').click();
    await page.waitForFunction(() => (document.querySelector('#peerAddHelper')?.textContent || '').includes('Found'));
    const nameGuess = await page.locator('#peerNameInput').inputValue();
    check(nameGuess.length > 0, `${scenario.name}: probe did not prefill the device name`);

    // Adding this device to itself must be refused.
    await page.locator('#peerUrlInput').fill(baseUrl);
    await page.locator('#peerTestBtn').click();
    await page.waitForFunction(() => (document.querySelector('#peerAddHelper')?.textContent || '').includes('this device'));

    await page.locator('#peerUrlInput').fill(peerUrl);
    await page.locator('#peerSaveBtn').click();
    await page.waitForFunction(() => document.querySelectorAll('.pmRow').length === 1);
    await page.waitForFunction(() => !!document.querySelector('.pmDot.isOnline'));
    const peerName = ((await page.locator('.pmName').first().textContent()) || '').trim();

    const layout = await page.evaluate(() => ({
      bodyOverflow: document.body.scrollWidth > document.body.clientWidth,
      modalOverflow: (() => {
        const modal = document.querySelector('.peersModal');
        return modal ? modal.scrollWidth > modal.clientWidth : false;
      })(),
      nestedInteractive: document.querySelectorAll('button button, a a, [role="button"] button').length,
    }));
    check(!layout.bodyOverflow && !layout.modalOverflow, `${scenario.name}: horizontal overflow detected`);
    check(layout.nestedInteractive === 0, `${scenario.name}: nested interactive controls detected`);

    if (screenshotDir) await page.screenshot({ path: `${screenshotDir}/peers-${scenario.name}.png`, fullPage: true });

    // Tapping the device sends the queue; the receiver ends up holding it.
    await page.locator('.pmDevice').first().click();
    await page.waitForFunction(() => (document.querySelector('#peersStatus')?.textContent || '').startsWith('Sent 2 items'));
    // Read the receiver from node, not the page: a cross-origin fetch from
    // /ui would be blocked by CORS and tell us nothing about the feature.
    const receiverQueue = await fetch(`${peerUrl}/queue`).then((r) => r.json());
    const received = (receiverQueue.queue || []).map((item) => ({
      title: item.title,
      origin: (item.peer_origin || {}).name,
    }));
    check(received.length === 2, `${scenario.name}: receiver holds ${received.length} items, expected 2`);
    check(
      received.every((item) => item.origin && item.origin.length > 0),
      `${scenario.name}: receiver did not record the sending device`,
    );

    // Escape closes the sheet, and removal clears the row.
    await page.keyboard.press('Escape');
    await page.locator('#peersBackdrop').waitFor({ state: 'hidden' });
    await page.locator('#queueSendBtn').click();
    await page.locator('#peersBackdrop:not(.hidden)').waitFor();
    page.once('dialog', (dialog) => dialog.accept());
    await page.locator('.pmRowBtn.danger').first().click();
    await page.waitForFunction(() => document.querySelectorAll('.pmRow').length === 0);

    check(errors.length === 0, `${scenario.name}: browser errors: ${errors.join('; ')}`);
    return { name: scenario.name, peerName, received, layout };
  } finally {
    await context.close();
  }
}

async function main() {
  const wsEndpoint = option('ws', 'ws://10.55.55.98:3000/');
  const baseUrl = option('base', 'http://10.55.55.2:8787').replace(/\/$/, '');
  const peerUrl = option('peer', '').replace(/\/$/, '');
  const screenshotDir = option('screenshots', '');
  check(peerUrl.length > 0, 'pass --peer=<url of a second RelayTV device>');
  const browser = await browserFor(wsEndpoint);
  try {
    const scenarios = [
      { name: 'phone-dark', viewport: { width: 390, height: 844 }, colorScheme: 'dark' },
      { name: 'desktop-light', viewport: { width: 1440, height: 1000 }, colorScheme: 'light' },
    ];
    const results = [];
    for (const scenario of scenarios) results.push(await runScenario(browser, baseUrl, peerUrl, scenario, screenshotDir));
    process.stdout.write(`${JSON.stringify({ ok: true, wsEndpoint, baseUrl, peerUrl, results }, null, 2)}\n`);
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  process.stderr.write(`Peers UI smoke failed: ${error.stack || error}\n`);
  process.exit(1);
});
