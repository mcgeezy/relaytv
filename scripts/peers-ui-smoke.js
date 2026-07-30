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

async function queueLength(url) {
  const payload = await fetch(`${url}/queue`).then((r) => r.json());
  return Number(payload.queue_length || 0);
}

async function forgetSavedPeers(page) {
  await page.evaluate(async () => {
    const listing = await fetch('/peers').then((r) => r.json());
    for (const peer of (listing.peers || [])) {
      await fetch(`/peers/${encodeURIComponent(peer.id)}`, { method: 'DELETE' });
    }
  });
}

async function seedSenderQueue(page) {
  // Put two known items in the queue. The device may be playing while this
  // runs, so nothing downstream assumes an exact count.
  await page.evaluate(async () => {
    await fetch('/clear', { method: 'POST' });
    for (const url of ['https://example.com/smoke-one', 'https://example.com/smoke-two']) {
      await fetch('/enqueue', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url }),
      });
    }
  });
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
    await forgetSavedPeers(page);
    await seedSenderQueue(page);
    await page.reload({ waitUntil: 'domcontentloaded' });

    // The Send pill only appears once there is something to send.
    await page.locator('#queueSendBtn:not(.hidden)').waitFor({ timeout: 15000 });
    await page.locator('#queueSendBtn').click();
    await page.locator('#peersBackdrop:not(.hidden)').waitFor();
    // Shape, not an exact count: a live device may be draining its queue while
    // the smoke runs, so the subtitle is compared against the copy contract.
    const subtitle = (await page.locator('#peersSubtitle').textContent()) || '';
    check(
      /^(Nothing queued|1 item in the queue|\d+ items in the queue)$/.test(subtitle.trim()),
      `${scenario.name}: sheet did not report the queue size ("${subtitle}")`,
    );

    // Wait for the settled list. With no saved devices the sheet shows either
    // the empty row or, when discovery found something, the nearby group only.
    await page.waitForFunction(() => {
      const saved = document.querySelectorAll('#peersList .pmRow').length;
      const empty = (document.querySelector('.pmEmpty')?.textContent || '').includes('No other devices yet');
      const nearby = document.querySelectorAll('#peersNearby .pmRow').length;
      return saved === 0 && (empty || nearby > 0);
    });

    // Discovery: when the peer is announcing itself over mDNS it shows up under
    // "Found nearby" and one tap adopts it. Skipped when the run's devices are
    // not on a network where multicast reaches them.
    const discovery = await page.evaluate(() => fetch('/peers').then((r) => r.json()).then((d) => d.discovery || {}));
    let adopted = false;
    if (discovery.active && discovery.found > 0) {
      await page.locator('#peersNearbyWrap:not(.hidden)').waitFor();
      await page.waitForFunction(() => document.querySelectorAll('#peersNearby .pmRow').length > 0);
      await page.locator('#peersNearby .pmRowBtn').first().click();
      await page.waitForFunction(() => document.querySelectorAll('#peersList .pmRow').length === 1);
      // Adopting removes the candidate from the nearby list (same device id).
      await page.waitForFunction(() => document.querySelectorAll('#peersNearby .pmRow').length === 0);
      adopted = true;
      page.once('dialog', (dialog) => dialog.accept());
      await page.locator('#peersList .pmRowBtn.danger').first().click();
      await page.waitForFunction(() => document.querySelectorAll('#peersList .pmRow').length === 0);
    } else {
      check(
        (await page.locator('#peersNearbyNote').textContent()).length > 0,
        `${scenario.name}: discovery state was not explained to the user`,
      );
    }

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
    await page.waitForFunction(() => document.querySelectorAll('#peersList .pmRow').length === 1);
    await page.waitForFunction(() => !!document.querySelector('.pmDot.isOnline'));
    const peerName = ((await page.locator('#peersList .pmName').first().textContent()) || '').trim();

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
    // Re-seed first: a device that is playing consumes its own queue, and this
    // assertion is about the transfer, not about autoplay timing.
    await seedSenderQueue(page);
    // Both devices may be playing, so compare a delta rather than a total.
    const receiverBefore = await queueLength(peerUrl);
    await page.locator('#peersList .pmDevice').first().click();
    await page.waitForFunction(() => /^Sent \d+ item/.test(document.querySelector('#peersStatus')?.textContent || ''));
    const sentText = (await page.locator('#peersStatus').textContent()) || '';
    const sentCount = Number((sentText.match(/^Sent (\d+) item/) || [])[1] || 0);
    check(sentCount > 0, `${scenario.name}: send reported no items ("${sentText}")`);

    // Read the receiver from node, not the page: a cross-origin fetch from
    // /ui would be blocked by CORS and tell us nothing about the feature.
    const receiverQueue = await fetch(`${peerUrl}/queue`).then((r) => r.json());
    const received = (receiverQueue.queue || []).map((item) => ({
      title: item.title,
      origin: (item.peer_origin || {}).name,
    }));
    check(
      received.length >= receiverBefore + sentCount,
      `${scenario.name}: receiver holds ${received.length}, expected at least ${receiverBefore + sentCount}`,
    );
    const arrived = received.slice(-sentCount);
    check(
      arrived.every((item) => item.origin && item.origin.length > 0),
      `${scenario.name}: receiver did not record the sending device`,
    );

    // Mode selector: Copy and Move are always offered; Handoff appears only
    // when this device is actually playing something.
    // Playback state comes from the server: app.js keeps its snapshot in a
    // script-scoped binding, which is not reachable as a window property.
    const playbackState = await fetch(`${baseUrl}/playback/state`).then((r) => r.json());
    const modes = await page.evaluate(() => {
      const visible = [];
      document.querySelectorAll('.pmMode').forEach((button) => {
        if (!button.classList.contains('hidden')) visible.push(button.dataset.peerMode);
      });
      return { visible };
    });
    modes.playing = !!(playbackState.playing || playbackState.paused);
    check(modes.visible.includes('copy') && modes.visible.includes('move'), `${scenario.name}: mode selector is incomplete`);
    check(
      modes.visible.includes('handoff') === modes.playing,
      `${scenario.name}: handoff offered=${modes.visible.includes('handoff')} while playing=${modes.playing}`,
    );

    // Move hands over ownership: the sender's queue is empty afterwards.
    await seedSenderQueue(page);
    await page.locator('[data-peer-mode="move"]').click();
    await page.waitForFunction(() => document.querySelector('#peersTitle')?.textContent === 'Move queue to');
    await page.locator('#peersList .pmDevice').first().click();
    await page.waitForFunction(() => /^Moved \d+ item/.test(document.querySelector('#peersStatus')?.textContent || ''));
    const afterMove = await queueLength(baseUrl);
    check(afterMove === 0, `${scenario.name}: move left ${afterMove} items on the sender`);
    await page.locator('[data-peer-mode="copy"]').click();

    // Per-item send: the tile's ⋯ opens the sheet scoped to that one item.
    await page.keyboard.press('Escape');
    await page.locator('#peersBackdrop').waitFor({ state: 'hidden' });
    await seedSenderQueue(page);
    await page.waitForFunction(() => document.querySelectorAll('#queue .qTile').length >= 1);
    const itemTitle = ((await page.locator('#queue .qTitleText').first().textContent()) || '').trim();
    await page.locator('#queue .qSendItemBtn').first().click();
    await page.locator('#peersBackdrop:not(.hidden)').waitFor();
    await page.waitForFunction(() => document.querySelector('#peersTitle')?.textContent === 'Send item to');
    check(
      ((await page.locator('#peersSubtitle').textContent()) || '').trim() === itemTitle,
      `${scenario.name}: item scope did not name the item`,
    );
    check(
      await page.locator('[data-peer-mode="handoff"]').isHidden(),
      `${scenario.name}: handoff must not be offered for a single item`,
    );
    const beforeItem = await queueLength(peerUrl);
    await page.locator('#peersList .pmDevice').first().click();
    await page.waitForFunction(() => /^Sent 1 item/.test(document.querySelector('#peersStatus')?.textContent || ''));
    check(
      (await queueLength(peerUrl)) >= beforeItem + 1,
      `${scenario.name}: receiver did not gain the single item`,
    );

    // Escape closes the sheet, and removal clears the row.
    await page.keyboard.press('Escape');
    await page.locator('#peersBackdrop').waitFor({ state: 'hidden' });
    await page.locator('#queueSendBtn').click();
    await page.locator('#peersBackdrop:not(.hidden)').waitFor();
    page.once('dialog', (dialog) => dialog.accept());
    await page.locator('#peersList .pmRowBtn.danger').first().click();
    await page.waitForFunction(() => document.querySelectorAll('#peersList .pmRow').length === 0);

    check(errors.length === 0, `${scenario.name}: browser errors: ${errors.join('; ')}`);
    return { name: scenario.name, peerName, sentCount, layout, discovery, adopted };
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
