'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(
  path.join(__dirname, '../../app/relaytv_app/static/ui/jellyfin.js'),
  'utf8',
);

function createDomElement(tagName, owner) {
  const classes = new Set();
  const attributes = new Map();
  const children = [];
  const listeners = new Map();
  const dataset = new Proxy({}, {
    get(_target, prop) {
      const attr = 'data-' + String(prop).replace(/[A-Z]/g, (m) => `-${m.toLowerCase()}`);
      return attributes.get(attr) || '';
    },
    set(_target, prop, value) {
      const attr = 'data-' + String(prop).replace(/[A-Z]/g, (m) => `-${m.toLowerCase()}`);
      attributes.set(attr, String(value));
      return true;
    },
  });

  const el = {
    tagName: String(tagName || 'div').toUpperCase(),
    id: '',
    className: '',
    textContent: '',
    value: '',
    disabled: false,
    tabIndex: 0,
    scrollTop: 0,
    scrollLeft: 0,
    scrollHeight: 1200,
    clientHeight: 600,
    offsetTop: 0,
     style: {},
    dataset,
    children,
    parentNode: null,
    parentElement: null,
    classList: {
      add: (...names) => names.forEach((n) => classes.add(n)),
      remove: (...names) => names.forEach((n) => classes.delete(n)),
      contains: (n) => classes.has(n),
      toggle: (n, force) => {
        const shouldAdd = force === undefined ? !classes.has(n) : !!force;
        if (shouldAdd) classes.add(n);
        else classes.delete(n);
        return shouldAdd;
      },
    },
    setAttribute(name, val) {
      const s = String(val);
      attributes.set(name, s);
      if (name === 'id') {
        this.id = s;
        owner.elements.set(s, this);
      }
      if (name === 'class') {
        classes.clear();
        s.split(/\s+/).filter(Boolean).forEach((c) => classes.add(c));
      }
    },
    getAttribute(name) {
      if (name === 'id') return this.id || null;
      return attributes.has(name) ? attributes.get(name) : null;
    },
    appendChild(child) {
      if (!child) return child;
      if (child.isFragment) {
        child.children.slice().forEach((c) => this.appendChild(c));
        child.children.length = 0;
        return child;
      }
      child.parentNode = this;
      child.parentElement = this;
      children.push(child);
      if (child.id) owner.elements.set(child.id, child);
      return child;
    },
    insertBefore(node, ref) {
      return this.appendChild(node, ref);
    },
    remove() {
      if (!this.parentNode) return;
      const idx = this.parentNode.children.indexOf(this);
      if (idx >= 0) this.parentNode.children.splice(idx, 1);
    },
    addEventListener(type, fn) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(fn);
    },
    removeEventListener() {},
    focus() {},
    scrollTo(opts) {
      if (opts && typeof opts === 'object') {
        if (typeof opts.top === 'number') this.scrollTop = opts.top;
        if (typeof opts.left === 'number') this.scrollLeft = opts.left;
      }
    },
    getBoundingClientRect() {
      return {top: 0, left: 0, right: 800, bottom: 600, width: 800, height: 600};
    },
    querySelectorAll(selector) {
      const results = [];
      const visit = (node) => {
        for (const child of node.children || []) {
          if (matchesSelector(child, selector)) results.push(child);
          visit(child);
        }
      };
      visit(this);
      return results;
    },
    querySelector(selector) {
      return this.querySelectorAll(selector)[0] || null;
    },
  };

  Object.defineProperty(el, 'innerHTML', {
    get() {
      return '';
    },
    set() {
      children.length = 0;
    },
  });

  Object.defineProperty(el, 'className', {
    get() {
      return Array.from(classes).join(' ');
    },
    set(v) {
      classes.clear();
      String(v || '').split(/\s+/).filter(Boolean).forEach((c) => classes.add(c));
    },
  });

  return el;
}

function matchesSelector(el, selector) {
  const parts = String(selector || '').split(',').map((s) => s.trim()).filter(Boolean);
  if (parts.length > 1) return parts.some((p) => matchesSelector(el, p));
  const s = parts[0] || '';
  if (s === '.jfItem') return el.classList.contains('jfItem');
  if (s === '.jfItem.selected') return el.classList.contains('jfItem') && el.classList.contains('selected');
  if (s === '.jfRow') return el.classList.contains('jfRow');
  if (s === '.jfTabBtn') return el.classList.contains('jfTabBtn');
  if (s.includes('.jfCatalogScroller') || s.includes('.jfScroller')) {
    return el.classList.contains('jfCatalogScroller') || el.classList.contains('jfScroller');
  }
  if (s.includes('.jfSeasonOption.active')) {
    return el.classList.contains('jfSeasonOption') && el.classList.contains('active');
  }
  if (s.includes('.jfSeasonOption')) return el.classList.contains('jfSeasonOption');
  const rowMatch = s.match(/^\.jfRow\[data-row-id="([^"]+)"\]$/);
  if (rowMatch) return el.classList.contains('jfRow') && el.dataset.rowId === rowMatch[1];
  return false;
}

function createFixture(sharedStorage) {
  const storageMap = sharedStorage || new Map();
  const localStorage = {
    getItem: (k) => (storageMap.has(k) ? storageMap.get(k) : null),
    setItem: (k, v) => storageMap.set(k, String(v)),
    removeItem: (k) => storageMap.delete(k),
  };

  const owner = {elements: new Map()};
  const ensureElement = (id) => {
    if (!owner.elements.has(id)) {
      const el = createDomElement('div', owner);
      el.id = id;
      owner.elements.set(id, el);
    }
    return owner.elements.get(id);
  };

  [
    'jellyfinShell',
    'jellyfinCard',
    'jfGrid',
    'jfRows',
    'jfDetail',
    'jfStatus',
    'jfConnection',
    'jfConnectionLabel',
    'jfCardHeadLabel',
    'jfSearchInput',
    'jfSortSelect',
    'jfAlphaIndicator',
    'jfActionStatus',
  ].forEach(ensureElement);

  const scrollingElement = createDomElement('html', owner);
  const documentElement = createDomElement('html', owner);
  const body = createDomElement('body', owner);

  const requestedUrls = [];
  const seasonsBySeries = {
    'series-a': [
      {season_id: 'sa-1', season_number: 1, title: 'Season 1'},
      {season_id: 'sa-2', season_number: 2, title: 'Season 2'},
      {season_id: 'sa-3', season_number: 3, title: 'Season 3'},
    ],
    'series-b': [
      {season_id: 'sb-1', season_number: 1, title: 'Season 1'},
      {season_id: 'sb-2', season_number: 2, title: 'Season 2'},
    ],
  };

  const context = vm.createContext({
    URLSearchParams,
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
    localStorage,
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
      scrollingElement,
      documentElement,
      body,
      activeElement: null,
      getElementById: (id) => owner.elements.get(id) || null,
      createElement: (tag) => createDomElement(tag, owner),
      createDocumentFragment: () => ({isFragment: true, children: [], appendChild(c) { this.children.push(c); return c; }}),
      querySelectorAll: (sel) => ensureElement('jfRows').querySelectorAll(sel),
      querySelector: (sel) => ensureElement('jfRows').querySelector(sel),
    },
    fetch: async (url) => {
      const u = String(url);
      requestedUrls.push(u);
      if (u.startsWith('/browse/tv/series?')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            connected: true,
            count: 2,
            limit: 48,
            items: [
              {item_id: 'series-a', title: 'Severance', type: 'series'},
              {item_id: 'series-b', title: 'The Bear', type: 'series'},
            ],
          }),
        };
      }
      const seasonMatch = u.match(/^\/browse\/tv\/series\/([^/?]+)\/seasons/);
      if (seasonMatch) {
        const sid = decodeURIComponent(seasonMatch[1]);
        return {
          ok: true,
          status: 200,
          json: async () => ({
            ok: true,
            connected: true,
            series_id: sid,
            seasons: seasonsBySeries[sid] || [],
          }),
        };
      }
      const epMatch = u.match(/^\/browse\/tv\/series\/([^/?]+)\/episodes(?:\?(.*))?$/);
      if (epMatch) {
        const sid = decodeURIComponent(epMatch[1]);
        const qs = new URLSearchParams(epMatch[2] || '');
        const sNum = Number(qs.get('season_number') || 1);
        return {
          ok: true,
          status: 200,
          json: async () => ({
            ok: true,
            connected: true,
            episodes: [
              {
                item_id: `${sid}-s${sNum}e1`,
                title: `S${sNum}E1`,
                subtitle: 'Pilot',
                type: 'episode',
                season_number: sNum,
                episode_number: 1,
              },
            ],
          }),
        };
      }
      return {ok: true, status: 200, json: async () => ({connected: true})};
    },
  });

  vm.runInContext(source, context);
  vm.runInContext("__jfActiveTab = 'tv';", context);
  return {context, ensureElement, requestedUrls, storageMap, scrollingElement};
}

test('selecting a TV series resets browse scroll containers to the top of the page', async () => {
  const f = createFixture();
  await vm.runInContext('loadJellyfinTvSeries(false)', f.context);

  const shell = f.ensureElement('jellyfinShell');
  const card = f.ensureElement('jellyfinCard');
  const rows = f.ensureElement('jfRows');
  shell.scrollTop = 840;
  card.scrollTop = 320;
  rows.scrollTop = 410;
  f.scrollingElement.scrollTop = 500;

  await vm.runInContext(
    "_jfOpenSeriesDetailFromRich({item_id: 'series-a', title: 'Severance', type: 'series'})",
    f.context,
  );

  assert.equal(shell.scrollTop, 0);
  assert.equal(card.scrollTop, 0);
  assert.equal(rows.scrollTop, 0);
  assert.equal(f.scrollingElement.scrollTop, 0);

  // A background push refresh while already viewing the same series should preserve user scroll.
  shell.scrollTop = 620;
  await vm.runInContext(
    "loadJellyfinTvSeriesDetail('series-a', {title: 'Severance', refresh: true})",
    f.context,
  );
  assert.equal(shell.scrollTop, 620);
});

test('clicking a TV series selects the last season that was browsed for that series', async () => {
  const sharedStorage = new Map();
  const f = createFixture(sharedStorage);

  // First open of series-a defaults to Season 1.
  await vm.runInContext(
    "loadJellyfinTvSeriesDetail('series-a', {title: 'Severance'})",
    f.context,
  );
  assert.equal(vm.runInContext('__jfTvSeasonNumber', f.context), 1);
  assert.ok(f.requestedUrls.includes('/browse/tv/series/series-a/episodes?season_number=1'));

  // User browses Season 3 of series-a.
  await vm.runInContext(
    "_jfOpenSeriesDetailFromRich({type: 'season', series_id: 'series-a', season_number: 3})",
    f.context,
  );
  assert.equal(vm.runInContext('__jfTvSeasonNumber', f.context), 3);
  assert.ok(f.requestedUrls.includes('/browse/tv/series/series-a/episodes?season_number=3'));

  // User navigates back to the TV series list (which clears active series state).
  await vm.runInContext('loadJellyfinTvSeries(false)', f.context);
  assert.equal(vm.runInContext('__jfTvSeasonNumber', f.context), null);

  // User opens series-b: should default to Season 1 of series-b, not Season 3 from series-a.
  await vm.runInContext(
    "loadJellyfinTvSeriesDetail('series-b', {title: 'The Bear'})",
    f.context,
  );
  assert.equal(vm.runInContext('__jfTvSeasonNumber', f.context), 1);

  // User browses Season 2 of series-b, then returns to the series list.
  await vm.runInContext(
    "_jfOpenSeriesDetailFromRich({type: 'season', series_id: 'series-b', season_number: 2})",
    f.context,
  );
  assert.equal(vm.runInContext('__jfTvSeasonNumber', f.context), 2);
  await vm.runInContext('loadJellyfinTvSeries(false)', f.context);

  // Clicking series-a again selects Season 3 (the last season browsed on series-a).
  f.requestedUrls.length = 0;
  await vm.runInContext(
    "loadJellyfinTvSeriesDetail('series-a', {title: 'Severance'})",
    f.context,
  );
  assert.equal(vm.runInContext('__jfTvSeasonNumber', f.context), 3);
  assert.ok(f.requestedUrls.includes('/browse/tv/series/series-a/episodes?season_number=3'));

  // Reloading the page (fresh VM context with the same localStorage) also remembers Season 2 for series-b.
  const reloaded = createFixture(sharedStorage);
  await vm.runInContext(
    "loadJellyfinTvSeriesDetail('series-b', {title: 'The Bear'})",
    reloaded.context,
  );
  assert.equal(vm.runInContext('__jfTvSeasonNumber', reloaded.context), 2);
  assert.ok(reloaded.requestedUrls.includes('/browse/tv/series/series-b/episodes?season_number=2'));
});
