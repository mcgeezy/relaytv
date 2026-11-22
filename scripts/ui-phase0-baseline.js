#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');
const { chromium } = require('playwright');

function option(name, fallback) {
  const prefix = `--${name}=`;
  const value = process.argv.find((item) => item.startsWith(prefix));
  return value ? value.slice(prefix.length) : fallback;
}

function ensureDirectory(directory) {
  fs.mkdirSync(directory, { recursive: true });
}

async function quietPage(context) {
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(`page: ${error.message}`));
  page.on('console', (message) => {
    if (message.type() !== 'error') return;
    if (message.text().includes('Failed to load resource')) return;
    errors.push(`console: ${message.text()}`);
  });
  return { page, errors };
}

async function newContext(browser, viewport, colorScheme) {
  const context = await browser.newContext({
    viewport,
    colorScheme,
    deviceScaleFactor: 1,
    reducedMotion: 'reduce',
  });
  await context.addInitScript((theme) => {
    try { localStorage.setItem('relaytv_theme', theme); } catch (_error) {}
    window.__relayBaseline = { cls: 0, longTasks: [] };
    try {
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          if (!entry.hadRecentInput) window.__relayBaseline.cls += entry.value;
        }
      }).observe({ type: 'layout-shift', buffered: true });
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          window.__relayBaseline.longTasks.push({
            start: Math.round(entry.startTime),
            duration: Math.round(entry.duration),
          });
        }
      }).observe({ type: 'longtask', buffered: true });
    } catch (_error) {}
  }, colorScheme);
  return context;
}

async function waitForRemote(page, baseUrl) {
  await page.goto(`${baseUrl}/ui`, { waitUntil: 'domcontentloaded' });
  await page.locator('#nowTopCard').waitFor({ state: 'visible', timeout: 15000 });
  await page.waitForTimeout(800);
}

async function remoteMetrics(page) {
  return page.evaluate(async () => {
    const rectangle = (selector) => {
      const element = document.querySelector(selector);
      if (!element) return null;
      const box = element.getBoundingClientRect();
      return {
        x: Math.round(box.x),
        y: Math.round(box.y),
        width: Math.round(box.width),
        height: Math.round(box.height),
      };
    };
    const named = (element) => String(
      element.getAttribute('aria-label') ||
      element.getAttribute('title') ||
      element.textContent ||
      '',
    ).trim();
    const interactive = Array.from(document.querySelectorAll(
      'button:not([disabled]), a[href], input:not([type="hidden"]), select, textarea, [role="button"]',
    )).filter((element) => {
      const style = getComputedStyle(element);
      const box = element.getBoundingClientRect();
      return style.display !== 'none' && style.visibility !== 'hidden' && box.width && box.height;
    });
    const smallTargets = interactive.map((element) => {
      const box = element.getBoundingClientRect();
      return {
        id: element.id || null,
        name: named(element).slice(0, 80),
        width: Math.round(box.width),
        height: Math.round(box.height),
      };
    }).filter((item) => item.width < 44 || item.height < 44);
    const unnamed = interactive.filter((element) => !named(element)).map((element) => ({
      tag: element.tagName.toLowerCase(),
      id: element.id || null,
    }));
    const menuButton = document.querySelector('#hdrMenuBtn');
    const start = performance.now();
    menuButton?.click();
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const menuAckMs = Math.round((performance.now() - start) * 10) / 10;
    document.querySelector('#hdrMenuPanel')?.classList.add('hidden');
    menuButton?.setAttribute('aria-expanded', 'false');
    const navigation = performance.getEntriesByType('navigation')[0];
    const paint = Object.fromEntries(
      performance.getEntriesByType('paint').map((entry) => [entry.name, Math.round(entry.startTime)]),
    );
    const resources = performance.getEntriesByType('resource')
      .filter((entry) => /\/static\/ui\/.*\.(?:css|js)(?:\?|$)/.test(entry.name))
      .map((entry) => ({
        name: new URL(entry.name).pathname.split('/').at(-1),
        durationMs: Math.round(entry.duration * 10) / 10,
        transferBytes: entry.transferSize,
        decodedBytes: entry.decodedBodySize,
      }));
    return {
      viewport: { width: innerWidth, height: innerHeight },
      document: { width: document.documentElement.scrollWidth, height: document.documentElement.scrollHeight },
      regions: {
        app: rectangle('.wrap'),
        header: rectangle('.wrap > header'),
        remote: rectangle('.topgrid'),
        transport: rectangle('#remoteCard'),
        queue: rectangle('.queueCard'),
      },
      interactiveCount: interactive.length,
      smallTargets,
      unnamed,
      menuAckMs,
      domContentLoadedMs: navigation ? Math.round(navigation.domContentLoadedEventEnd) : null,
      loadMs: navigation ? Math.round(navigation.loadEventEnd) : null,
      paint,
      resources,
      cls: window.__relayBaseline?.cls || 0,
      longTasks: window.__relayBaseline?.longTasks || [],
    };
  });
}

async function captureRemote(browser, baseUrl, outputDirectory, scenario) {
  const context = await newContext(browser, scenario.viewport, scenario.theme);
  const { page, errors } = await quietPage(context);
  try {
    await waitForRemote(page, baseUrl);
    const metrics = await remoteMetrics(page);
    await page.screenshot({
      path: path.join(outputDirectory, scenario.filename),
      fullPage: true,
      type: 'png',
    });
    return { ...scenario, metrics, errors };
  } finally {
    await context.close();
  }
}

async function captureDialog(browser, baseUrl, outputDirectory, dialog) {
  const context = await newContext(browser, { width: 1280, height: 800 }, 'dark');
  const { page, errors } = await quietPage(context);
  try {
    await waitForRemote(page, baseUrl);
    if (dialog.beforeTrigger) {
      await page.locator(dialog.beforeTrigger).click();
    }
    await page.locator(dialog.trigger).click();
    await page.locator(dialog.visible).waitFor({ state: 'visible' });
    if (dialog.ready) {
      await page.waitForFunction((selector) => {
        const element = document.querySelector(selector);
        return Boolean(element && String(element.value || '').trim());
      }, dialog.ready);
    }
    await page.evaluate(() => {
      for (const input of document.querySelectorAll('input, textarea')) {
        if (input.type === 'range' || input.type === 'checkbox') continue;
        input.value = '';
      }
      for (const node of document.querySelectorAll('[data-sensitive]')) node.textContent = 'Configured';
    });
    await page.screenshot({
      path: path.join(outputDirectory, dialog.filename),
      fullPage: true,
      type: 'png',
    });
    return { name: dialog.name, errors };
  } finally {
    await context.close();
  }
}

async function captureIdle(browser, baseUrl, outputDirectory) {
  const context = await newContext(browser, { width: 1600, height: 900 }, 'dark');
  const { page, errors } = await quietPage(context);
  try {
    await page.goto(`${baseUrl}/idle`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(1200);
    await page.evaluate(() => {
      document.querySelector('#idleQrWrap')?.classList.add('hidden');
      for (const selector of ['#deviceName', '#idleDeviceName']) {
        const node = document.querySelector(selector);
        if (node) node.textContent = 'RelayTV device';
      }
      const location = document.querySelector('#weatherHeroPanel .cardDesc');
      if (location) location.textContent = 'Weather location hidden for baseline';
    });
    await page.screenshot({
      path: path.join(outputDirectory, 'current-tv-idle-dark.png'),
      fullPage: true,
      type: 'png',
    });
    return { name: 'tv-idle', errors };
  } finally {
    await context.close();
  }
}

function assetMetrics(rootDirectory) {
  const directory = path.join(rootDirectory, 'app', 'relaytv_app', 'static', 'ui');
  return fs.readdirSync(directory)
    .filter((name) => name.endsWith('.js') || name.endsWith('.css'))
    .sort()
    .map((name) => {
      const body = fs.readFileSync(path.join(directory, name));
      return { name, sourceBytes: body.length, gzipBytes: zlib.gzipSync(body).length };
    });
}

async function main() {
  const rootDirectory = path.resolve(__dirname, '..');
  const baseUrl = option('base', 'http://127.0.0.1:8787').replace(/\/$/, '');
  const outputDirectory = path.resolve(option(
    'output',
    path.join(rootDirectory, 'docs', 'images', 'ui-phase-0'),
  ));
  const reportPath = path.resolve(option(
    'report',
    path.join(rootDirectory, 'docs', 'ui-modernization', 'phase-0-baseline.json'),
  ));
  ensureDirectory(outputDirectory);
  ensureDirectory(path.dirname(reportPath));

  const browser = await chromium.launch({ headless: true });
  try {
    const appInfo = await fetch(`${baseUrl}/app/info`).then((response) => response.json());
    const remotes = [];
    for (const scenario of [
      { name: 'phone-dark', viewport: { width: 390, height: 844 }, theme: 'dark', filename: 'current-phone-dark.png' },
      { name: 'desktop-light', viewport: { width: 1280, height: 800 }, theme: 'light', filename: 'current-desktop-light.png' },
    ]) {
      remotes.push(await captureRemote(browser, baseUrl, outputDirectory, scenario));
    }
    const dialogs = [];
    for (const dialog of [
      { name: 'settings', beforeTrigger: '#hdrMenuBtn', trigger: '#settingsBtn', visible: '#settingsBackdrop:not(.hidden)', ready: '#setDeviceName', filename: 'current-settings-dark.png' },
      { name: 'add-media', trigger: '#addUrlBtn', visible: '#addBackdrop:not(.hidden)', filename: 'current-add-media-dark.png' },
    ]) {
      dialogs.push(await captureDialog(browser, baseUrl, outputDirectory, dialog));
    }
    const idle = await captureIdle(browser, baseUrl, outputDirectory);
    const report = {
      schema: 1,
      capturedAt: new Date().toISOString(),
      baseUrl,
      server: {
        version: appInfo.version || '',
        revision: appInfo.revision || '',
      },
      runner: {
        playwright: require('playwright/package.json').version,
        chromium: browser.version(),
        platform: `${process.platform}/${process.arch}`,
      },
      assets: assetMetrics(rootDirectory),
      remotes,
      dialogs,
      idle,
      notes: [
        'Measurements are diagnostic snapshots, not benchmark guarantees.',
        'Input and textarea values, the idle QR code, and location/device labels are hidden in screenshots.',
        'Run against the same server, host, browser, and dataset for before/after comparisons.',
      ],
    };
    fs.writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`);
    process.stdout.write(`${JSON.stringify({ ok: true, reportPath, outputDirectory }, null, 2)}\n`);
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  process.stderr.write(`UI phase 0 baseline failed: ${error.stack || error}\n`);
  process.exitCode = 1;
});
