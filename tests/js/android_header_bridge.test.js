'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const UI_DIR = path.join(__dirname, '../../app/relaytv_app/static/ui');
const APP_JS = fs.readFileSync(path.join(UI_DIR, 'app.js'), 'utf8');
const APP_CSS = fs.readFileSync(path.join(UI_DIR, 'app.css'), 'utf8');

function extractFunction(source, name) {
  for (const prefix of [`async function ${name}(`, `function ${name}(`]) {
    const start = source.indexOf(prefix);
    if (start === -1) continue;
    let depth = 0;
    let seenBody = false;
    for (let i = start; i < source.length; i++) {
      const ch = source[i];
      if (ch === '{') {
        depth++;
        seenBody = true;
      } else if (ch === '}') {
        depth--;
        if (seenBody && depth === 0) return source.slice(start, i + 1);
      }
    }
  }
  throw new Error(`${name} not found`);
}

function makeEl(id = '', initialClasses = []) {
  const classes = new Set(initialClasses);
  const attrs = new Map();
  return {
    id,
    style: {},
    dataset: {},
    onclick: null,
    onkeydown: null,
    classList: {
      add: (...names) => names.forEach((n) => classes.add(n)),
      remove: (...names) => names.forEach((n) => classes.delete(n)),
      contains: (n) => classes.has(n),
      toggle: (n, force) => {
        const on = force === undefined ? !classes.has(n) : !!force;
        if (on) classes.add(n);
        else classes.delete(n);
        return on;
      },
    },
    setAttribute(name, val) {
      attrs.set(name, String(val));
    },
    getAttribute(name) {
      return attrs.has(name) ? attrs.get(name) : null;
    },
  };
}

function createHarness({ bridge = undefined, storedTheme = 'auto', prefersLight = false } = {}) {
  const elements = new Map();
  const register = (id, classes = []) => {
    const el = makeEl(id, classes);
    elements.set(id, el);
    return el;
  };

  const htmlEl = makeEl('html');
  const metaTheme = makeEl('meta-theme');
  metaTheme.setAttribute('content', '#05070d');
  const hdrBrand = register('hdrBrand', ['hdrBrand']);
  const hdrMenuPanel = register('hdrMenuPanel', ['hdrMenuPanel']);
  const hdrMenuBtn = register('hdrMenuBtn', ['hdrMenuBtn']);
  hdrMenuBtn.setAttribute('aria-expanded', 'true');
  const hdrMenuAndroidSection = register('hdrMenuAndroidSection', ['hdrMenuAndroid', 'hidden']);
  const androidServersBtn = register('androidServersBtn', ['hdrMenuItem']);
  const androidSettingsBtn = register('androidSettingsBtn', ['hdrMenuItem']);
  const androidReloadBtn = register('androidReloadBtn', ['hdrMenuItem']);
  const androidPrivacyBtn = register('androidPrivacyBtn', ['hdrMenuItem']);

  const mtBtns = ['auto', 'dark', 'light'].map((mode) => {
    const b = makeEl(`mt-${mode}`, ['mtBtn']);
    b.dataset.themeMode = mode;
    return b;
  });

  const windowObj = {
    RelayTVAndroid: bridge,
    matchMedia: (query) => ({
      matches: query.includes('light') ? prefersLight : !prefersLight,
    }),
  };

  const sandbox = {
    window: windowObj,
    localStorage: {
      getItem: (k) => (k === 'relaytv_theme' ? storedTheme : null),
    },
    document: {
      documentElement: htmlEl,
      styleSheets: [],
      getElementById: (id) => elements.get(id) || null,
      querySelector: (sel) => (sel === 'meta[name="theme-color"]' ? metaTheme : null),
      querySelectorAll: (sel) => (sel === '.mtBtn' ? mtBtns : []),
    },
  };

  const code = [
    "const __THEME_KEY = 'relaytv_theme';",
    "const __THEME_RE = /\\(prefers-color-scheme:\\s*(light|dark)\\)/;",
    extractFunction(APP_JS, 'closeHeaderMenu'),
    extractFunction(APP_JS, '_themeStoredMode'),
    extractFunction(APP_JS, '_themeApplyToSheets'),
    extractFunction(APP_JS, '_themeEffective'),
    extractFunction(APP_JS, 'applyTheme'),
    extractFunction(APP_JS, 'bindAndroidBridge'),
  ].join('\n\n');

  vm.runInNewContext(code, sandbox);
  return {
    sandbox,
    htmlEl,
    metaTheme,
    hdrBrand,
    hdrMenuPanel,
    hdrMenuBtn,
    hdrMenuAndroidSection,
    androidServersBtn,
    androidSettingsBtn,
    androidReloadBtn,
    androidPrivacyBtn,
  };
}

test('normal browser without RelayTVAndroid leaves DOM and header completely untouched', () => {
  const h = createHarness({ bridge: undefined });

  const bound = h.sandbox.bindAndroidBridge();
  assert.equal(bound, false);
  assert.equal(h.htmlEl.classList.contains('android-app'), false);
  assert.equal(h.hdrMenuAndroidSection.classList.contains('hidden'), true);
  assert.equal(h.hdrBrand.getAttribute('role'), null);
  assert.equal(h.hdrBrand.getAttribute('tabindex'), null);
  assert.equal(h.hdrBrand.getAttribute('aria-label'), null);
  assert.equal(h.hdrBrand.onclick, null);
  assert.equal(h.hdrBrand.onkeydown, null);

  // applyTheme still works cleanly without bridge
  h.sandbox.applyTheme('light');
  assert.equal(h.metaTheme.getAttribute('content'), '#edf2ff');
  h.sandbox.applyTheme('dark');
  assert.equal(h.metaTheme.getAttribute('content'), '#05070d');
});

test('Android bridge activates header switcher, menu items, ready callback, and theme sync', () => {
  const calls = [];
  const bridge = {
    onBridgeReady: () => calls.push(['onBridgeReady']),
    openServers: () => calls.push(['openServers']),
    openSettings: () => calls.push(['openSettings']),
    reload: () => calls.push(['reload']),
    openPrivacy: () => calls.push(['openPrivacy']),
    onThemeChanged: (theme, hex) => calls.push(['onThemeChanged', theme, hex]),
  };

  const h = createHarness({ bridge, storedTheme: 'auto', prefersLight: true });
  const bound = h.sandbox.bindAndroidBridge();

  assert.equal(bound, true);
  assert.equal(h.htmlEl.classList.contains('android-app'), true);
  assert.equal(h.hdrMenuAndroidSection.classList.contains('hidden'), false);
  assert.equal(h.hdrBrand.getAttribute('role'), 'button');
  assert.equal(h.hdrBrand.getAttribute('tabindex'), '0');
  assert.equal(h.hdrBrand.getAttribute('aria-label'), 'Switch RelayTV server');

  // Ready fires before initial theme report
  assert.deepEqual(calls, [
    ['onBridgeReady'],
    ['onThemeChanged', 'light', '#edf2ff'],
  ]);

  // Clicking header brand closes open menu and calls openServers()
  h.hdrMenuPanel.classList.remove('hidden');
  h.hdrMenuBtn.setAttribute('aria-expanded', 'true');
  h.hdrBrand.onclick();
  assert.equal(h.hdrMenuPanel.classList.contains('hidden'), true);
  assert.equal(h.hdrMenuBtn.getAttribute('aria-expanded'), 'false');
  assert.deepEqual(calls.at(-1), ['openServers']);

  // Keyboard Enter and Space activate openServers() with preventDefault()
  for (const key of ['Enter', ' ']) {
    let prevented = false;
    h.hdrMenuPanel.classList.remove('hidden');
    h.hdrBrand.onkeydown({ key, preventDefault: () => { prevented = true; } });
    assert.equal(prevented, true);
    assert.equal(h.hdrMenuPanel.classList.contains('hidden'), true);
    assert.deepEqual(calls.at(-1), ['openServers']);
  }

  // Unrelated keys do not activate openServers()
  const countBefore = calls.length;
  h.hdrBrand.onkeydown({ key: 'Escape', preventDefault: () => {} });
  assert.equal(calls.length, countBefore);

  // Android menu items each close the menu and invoke the matching bridge method
  const itemExpectations = [
    [h.androidServersBtn, 'openServers'],
    [h.androidSettingsBtn, 'openSettings'],
    [h.androidReloadBtn, 'reload'],
    [h.androidPrivacyBtn, 'openPrivacy'],
  ];
  for (const [btn, expectedMethod] of itemExpectations) {
    h.hdrMenuPanel.classList.remove('hidden');
    h.hdrMenuBtn.setAttribute('aria-expanded', 'true');
    btn.onclick();
    assert.equal(h.hdrMenuPanel.classList.contains('hidden'), true);
    assert.equal(h.hdrMenuBtn.getAttribute('aria-expanded'), 'false');
    assert.deepEqual(calls.at(-1), [expectedMethod]);
  }

  // Manual theme switches report exact effective theme + bgColorHex
  h.sandbox.applyTheme('dark');
  assert.deepEqual(calls.at(-1), ['onThemeChanged', 'dark', '#05070d']);
  assert.equal(h.metaTheme.getAttribute('content'), '#05070d');

  h.sandbox.applyTheme('light');
  assert.deepEqual(calls.at(-1), ['onThemeChanged', 'light', '#edf2ff']);
  assert.equal(h.metaTheme.getAttribute('content'), '#edf2ff');
});

test('throwing Android bridge methods are safely caught', () => {
  const bridge = {
    onBridgeReady: () => { throw new Error('boom ready'); },
    openServers: () => { throw new Error('boom servers'); },
    onThemeChanged: () => { throw new Error('boom theme'); },
  };
  const h = createHarness({ bridge });
  assert.doesNotThrow(() => h.sandbox.bindAndroidBridge());
  assert.doesNotThrow(() => h.hdrBrand.onclick());
  assert.doesNotThrow(() => h.sandbox.applyTheme('dark'));
});

test('CSS gates Android header switcher and menu section strictly behind html.android-app', () => {
  assert.match(APP_CSS, /\.hdrBrandChevron\s*\{[^}]*display:\s*none;/s);
  assert.match(APP_CSS, /html\.android-app\s+\.hdrBrandChevron\s*\{[^}]*display:\s*block;/s);
  assert.match(APP_CSS, /\.hdrMenuAndroid\s*\{[^}]*display:\s*none;/s);
  assert.match(APP_CSS, /html\.android-app\s+\.hdrMenuAndroid\s*\{[^}]*display:\s*grid;/s);
  assert.match(APP_CSS, /html\.android-app\s+body\s*\{[^}]*padding-top:\s*max\(/s);
  assert.match(APP_CSS, /@media\s*\(prefers-color-scheme:\s*light\)\s*\{[\s\S]*?html\.android-app\s+\.hdrBrandChevron\s*\{/);
  assert.match(APP_CSS, /@media\s*\(prefers-color-scheme:\s*light\)\s*\{[\s\S]*?\.hdrMenuAndroid\s*\{\s*border-top-color:/);
});
