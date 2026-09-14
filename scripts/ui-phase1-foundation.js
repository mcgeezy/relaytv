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

function channel(value) {
  const amount = value / 255;
  return amount <= .04045 ? amount / 12.92 : ((amount + .055) / 1.055) ** 2.4;
}

function luminance(hex) {
  const value = String(hex).trim().replace('#', '');
  if (!/^[0-9a-f]{6}$/i.test(value)) throw new Error(`unsupported color: ${hex}`);
  const parts = [0, 2, 4].map((start) => Number.parseInt(value.slice(start, start + 2), 16));
  return .2126 * channel(parts[0]) + .7152 * channel(parts[1]) + .0722 * channel(parts[2]);
}

function contrast(foreground, background) {
  const values = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
  return Number(((values[0] + .05) / (values[1] + .05)).toFixed(2));
}

async function applyMode(page, mode) {
  await page.waitForFunction(() => window.relaytvTheme && document.documentElement.dataset.theme);
  if (mode !== 'auto') {
    await page.locator('#hdrMenuBtn').click();
    await page.locator(`.mtBtn[data-theme-mode="${mode}"]`).click();
    await page.keyboard.press('Escape');
    await page.locator('#hdrMenuPanel').waitFor({state:'hidden'});
  }
  await page.waitForFunction((selected) => {
    const usesLightPalette = selected === 'light'
      || (selected === 'auto' && matchMedia('(prefers-color-scheme: light)').matches);
    const expectedBackground = usesLightPalette
      ? 'rgb(244, 246, 249)'
      : 'rgb(11, 16, 23)';
    return document.documentElement.dataset.theme === selected
      && localStorage.getItem('relaytv_theme') === selected
      && getComputedStyle(document.body).backgroundColor === expectedBackground;
  }, mode);
}

async function tokenEvidence(page) {
  return page.evaluate(() => {
    const styles = getComputedStyle(document.documentElement);
    const value = (name) => styles.getPropertyValue(name).trim();
    return {
      background:value('--background'),
      surface:value('--surface'),
      surfaceRaised:value('--surface-raised'),
      textPrimary:value('--text-primary'),
      textSecondary:value('--text-secondary'),
      accent:value('--accent'),
      onAccent:value('--on-accent'),
      focusRing:value('--focus-ring'),
      bodyBackground:getComputedStyle(document.body).backgroundColor,
    };
  });
}

async function screenshot(page, outputDirectory, filename) {
  await page.screenshot({path:path.join(outputDirectory, filename), fullPage:true, type:'png'});
  return filename;
}

async function remoteEvidence(browser, baseUrl, outputDirectory, mode, report) {
  const fixture = await newFixturePage(browser, baseUrl, playbackStatus(), {
    viewport:{width:390,height:844},
    colorScheme:'dark',
    themeMode:'auto',
  });
  try {
    await applyMode(fixture.page, mode);
    const tokens = await tokenEvidence(fixture.page);
    const ratios = {
      primaryOnBackground:contrast(tokens.textPrimary, tokens.background),
      primaryOnSurface:contrast(tokens.textPrimary, tokens.surface),
      secondaryOnSurface:contrast(tokens.textSecondary, tokens.surface),
      onAccent:contrast(tokens.onAccent, tokens.accent),
      focusOnBackground:contrast(tokens.focusRing, tokens.background),
    };
    if (Object.values(ratios).some((value) => value < 4.5)) {
      throw new Error(`${mode} token contrast failed: ${JSON.stringify(ratios)}`);
    }
    const expectedBodyBackground = mode === 'light'
      ? 'rgb(244, 246, 249)'
      : 'rgb(11, 16, 23)';
    if (tokens.bodyBackground !== expectedBodyBackground) {
      throw new Error(
        `${mode} body background is ${tokens.bodyBackground}; expected ${expectedBodyBackground}`,
      );
    }
    const expectedBackground = mode === 'light' ? 'rgb(244, 246, 249)' : 'rgb(11, 16, 23)';
    if (tokens.bodyBackground !== expectedBackground) {
      throw new Error(
        `${mode} body background is ${tokens.bodyBackground}; expected ${expectedBackground}`,
      );
    }
    const overflow = await fixture.page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    );
    if (overflow) throw new Error(`${mode} remote has horizontal overflow`);
    report.modes[mode] = {tokens, contrast:ratios, remoteHorizontalOverflow:overflow};
    report.screenshots.push(await screenshot(
      fixture.page,
      outputDirectory,
      `phase1-${mode}-remote-phone.png`,
    ));
    report.errors.push(...fixture.errors);
  } finally {
    await fixture.context.close();
  }
}

async function providerEvidence(browser, baseUrl, outputDirectory, mode, report) {
  const fixture = await newFixturePage(browser, baseUrl, playbackStatus(), {
    viewport:{width:1280,height:800},
    colorScheme:'dark',
    themeMode:'auto',
  });
  try {
    await applyMode(fixture.page, mode);
    await fixture.page.locator('#jellyfinOpenBtn').click();
    await fixture.page.locator('#jellyfinShell:not(.hidden)').waitFor();
    await fixture.page.locator('.jfItem').first().waitFor();
    const count = await fixture.page.locator('.jfItem').count();
    if (count !== 3) throw new Error(`${mode} provider fixture rendered ${count} items`);
    report.modes[mode].providerItems = count;
    report.screenshots.push(await screenshot(
      fixture.page,
      outputDirectory,
      `phase1-${mode}-provider-desktop.png`,
    ));
    report.errors.push(...fixture.errors);
  } finally {
    await fixture.context.close();
  }
}

async function dialogEvidence(browser, baseUrl, outputDirectory, mode, report) {
  const fixture = await newFixturePage(browser, baseUrl, playbackStatus(), {
    viewport:{width:390,height:844},
    colorScheme:'dark',
    themeMode:'auto',
  });
  try {
    await applyMode(fixture.page, mode);
    await fixture.page.locator('#hdrMenuBtn').click();
    await fixture.page.locator('#hdrMenuPanel:not(.hidden)').waitFor();
    await fixture.page.locator('#aboutBtn').click();
    const dialog = fixture.page.locator('#aboutBackdrop:not(.hidden)');
    await dialog.waitFor();
    await fixture.page.locator('#aboutCloseBtn').focus();
    await fixture.page.keyboard.press('Tab');
    await fixture.page.keyboard.press('Shift+Tab');
    await fixture.page.waitForFunction(() => document.activeElement?.id === 'aboutCloseBtn');
    const controls = await dialog.locator('.ui-button').evaluateAll((items) => items.map((item) => {
      const rect = item.getBoundingClientRect();
      return {width:Math.round(rect.width), height:Math.round(rect.height)};
    }));
    if (controls.some((item) => item.width < 44 || item.height < 44)) {
      throw new Error(`${mode} dialog has a target below 44px: ${JSON.stringify(controls)}`);
    }
    const style = await fixture.page.locator('#aboutCloseBtn').evaluate((button) => {
      const computed = getComputedStyle(button);
      return {
        outlineStyle:computed.outlineStyle,
        outlineWidth:computed.outlineWidth,
        transitionDuration:computed.transitionDuration,
      };
    });
    if (style.outlineStyle === 'none' || Number.parseFloat(style.outlineWidth) < 3) {
      throw new Error(`${mode} dialog focus indicator is missing: ${JSON.stringify(style)}`);
    }
    const transitionSeconds = style.transitionDuration
      .split(',')
      .map((duration) => Number.parseFloat(duration));
    if (transitionSeconds.some((duration) => !Number.isFinite(duration) || duration > .000011)) {
      throw new Error(`${mode} reduced-motion duration is unexpected: ${style.transitionDuration}`);
    }
    const metrics = await dialog.locator('.ui-dialog').evaluate((element) => {
      const rect = element.getBoundingClientRect();
      return {
        bottom:Math.round(rect.bottom),
        viewportHeight:innerHeight,
        role:element.parentElement.getAttribute('role'),
        labelledBy:element.parentElement.getAttribute('aria-labelledby'),
      };
    });
    if (metrics.bottom !== metrics.viewportHeight || metrics.role !== 'dialog'
        || metrics.labelledBy !== 'aboutDialogTitle') {
      throw new Error(`${mode} phone dialog contract failed: ${JSON.stringify(metrics)}`);
    }
    report.modes[mode].dialog = {controls, focus:style, metrics};
    report.screenshots.push(await screenshot(
      fixture.page,
      outputDirectory,
      `phase1-${mode}-dialog-phone.png`,
    ));
    report.errors.push(...fixture.errors);
  } finally {
    await fixture.context.close();
  }
}

async function main() {
  const rootDirectory = path.resolve(__dirname, '..');
  const baseUrl = option('base', 'http://127.0.0.1:8787').replace(/\/$/, '');
  const browserName = option('browser', 'chromium');
  const browserType = {chromium, firefox, webkit}[browserName];
  if (!browserType) throw new Error(`unknown browser: ${browserName}`);
  const outputDirectory = path.resolve(option(
    'output',
    path.join(rootDirectory, 'docs', 'images', 'ui-phase-1'),
  ));
  const reportPath = path.resolve(option(
    'report',
    path.join(rootDirectory, 'docs', 'ui-modernization', 'phase-1-foundation.json'),
  ));
  fs.mkdirSync(outputDirectory, {recursive:true});
  fs.mkdirSync(path.dirname(reportPath), {recursive:true});
  const browser = await browserType.launch({headless:true});
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
    modes:{},
    screenshots:[],
    errors:[],
  };
  try {
    for (const mode of ['auto', 'dark', 'light']) {
      await remoteEvidence(browser, baseUrl, outputDirectory, mode, report);
      await providerEvidence(browser, baseUrl, outputDirectory, mode, report);
      await dialogEvidence(browser, baseUrl, outputDirectory, mode, report);
    }
  } finally {
    await browser.close();
  }
  if (report.errors.length) throw new Error(report.errors.join('\n'));
  fs.writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`);
  process.stdout.write(`${JSON.stringify({
    ok:true,
    reportPath,
    outputDirectory,
    screenshots:report.screenshots.length,
  }, null, 2)}\n`);
}

if (require.main === module) {
  main().catch((error) => {
    process.stderr.write(`UI phase 1 foundation capture failed: ${error.stack || error}\n`);
    process.exitCode = 1;
  });
}

module.exports = {contrast, luminance};
