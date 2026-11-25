'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const UI_DIR = path.join(__dirname, '../../app/relaytv_app/static/ui');
const APP_JS = fs.readFileSync(path.join(UI_DIR, 'app.js'), 'utf8');
const PLEX_JS = fs.readFileSync(path.join(UI_DIR, 'plex.js'), 'utf8');
const JELLYFIN_JS = fs.readFileSync(path.join(UI_DIR, 'jellyfin.js'), 'utf8');
const IPTV_JS = fs.readFileSync(path.join(UI_DIR, 'iptv.js'), 'utf8');
const SEERR_JS = fs.readFileSync(path.join(UI_DIR, 'seerr.js'), 'utf8');
const ROUTES_INIT_PY = fs.readFileSync(
  path.join(__dirname, '../../app/relaytv_app/routes/__init__.py'),
  'utf8',
);

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
  const listeners = new Map();
  const children = [];
  return {
    id,
    tagName: 'DIV',
    value: '',
    checked: false,
    disabled: false,
    href: '',
    textContent: '',
    className: '',
    style: {},
    dataset: {},
    children,
    listeners,
    parentElement: null,
    parentNode: null,
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
    appendChild(child) {
      if (!child) return child;
      child.parentNode = this;
      child.parentElement = this;
      children.push(child);
      return child;
    },
    append(...nodes) {
      nodes.forEach((n) => this.appendChild(n));
    },
    replaceChildren(...nodes) {
      children.length = 0;
      nodes.forEach((n) => this.appendChild(n));
    },
    addEventListener(type, fn) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(fn);
    },
    querySelector() {
      return null;
    },
    querySelectorAll() {
      return [];
    },
    scrollTo() {},
    focus() {},
  };
}

// --- 1. Subtitle modal lock & nav back-close ---------------------------------

test('subLangBackdrop activates interaction lock and closes on first nav back', () => {
  const subLangBd = makeEl('subLangBackdrop');
  const elements = new Map([['subLangBackdrop', subLangBd]]);
  const closedCalls = [];
  const ctx = vm.createContext({
    __draggingQueue: false,
    __queueMenuEl: null,
    __pendingRemove: null,
    __jfUiVisible: false,
    window: {},
    document: { getElementById: (id) => elements.get(id) || null },
    _jfIsDetailOpen: () => false,
    closeNowSubtitleModal: (opts) => {
      closedCalls.push(opts);
      subLangBd.classList.add('hidden');
    },
  });
  vm.runInContext(extractFunction(APP_JS, '_isHiddenEl'), ctx);
  vm.runInContext(extractFunction(APP_JS, '_uiRefreshInteractionLockActive'), ctx);
  vm.runInContext(extractFunction(APP_JS, '_uiCloseTopLayerFromNav'), ctx);

  assert.equal(ctx._uiRefreshInteractionLockActive(), true);
  assert.equal(ctx._uiCloseTopLayerFromNav(), true);
  assert.deepEqual(JSON.parse(JSON.stringify(closedCalls)), [{ fromNav: true }]);
  assert.equal(subLangBd.classList.contains('hidden'), true);
  assert.equal(ctx._uiRefreshInteractionLockActive(), false);
});

// --- 2. Plex shell: openPlexDetail push guard & force-close ------------------

test('Plex openPlexDetail pushes layer only when opening from hidden and play_now/Back force-close cleanly', async () => {
  const elements = new Map([
    ['plexOpenBtn', makeEl('plexOpenBtn')],
    ['plexConnection', makeEl('plexConnection')],
    ['plexSearchInput', makeEl('plexSearchInput')],
    ['plexMoreBtn', makeEl('plexMoreBtn', ['hidden'])],
    ['plexBackBtn', makeEl('plexBackBtn')],
    ['plexShell', makeEl('plexShell', ['hidden'])],
    ['plexDetailBackdrop', makeEl('plexDetailBackdrop', ['hidden'])],
    ['plexDetail', makeEl('plexDetail', ['hidden'])],
    ['plexBrowseStatus', makeEl('plexBrowseStatus')],
    ['plexContent', makeEl('plexContent')],
  ]);
  let pushCount = 0;
  const historyCalls = [];
  const ctx = vm.createContext({
    AbortController,
    URLSearchParams,
    console,
    fetch: async (url) => {
      if (String(url).includes('/plex/items/action')) {
        return { ok: true, status: 200, json: async () => ({ ok: true }) };
      }
      return { ok: true, status: 200, json: async () => ({ item: { id: 'item-1', title: 'Movie' } }) };
    },
    history: {
      back: () => historyCalls.push('back'),
      go: (n) => historyCalls.push(['go', n]),
    },
    requestAnimationFrame: (cb) => cb(),
    setTimeout: () => 1,
    clearTimeout() {},
    document: {
      activeElement: null,
      body: { classList: makeEl('body').classList },
      createElement: () => makeEl(),
      getElementById: (id) => elements.get(id) || null,
      querySelectorAll: () => [],
    },
    window: { addEventListener() {} },
    __uiNavDepth: 0,
    _uiPushLayer() {
      pushCount += 1;
      ctx.__uiNavDepth += 1;
    },
  });
  vm.runInContext(PLEX_JS, ctx, { filename: 'plex.js' });
  vm.runInContext('__plexReady = true; refreshPlexStatus = async () => {}; loadPlexHome = async () => {}; bindPlexUi();', ctx);

  await ctx.openPlexShell();
  assert.equal(pushCount, 1);
  assert.equal(ctx.__uiNavDepth, 1);

  // Opening first detail pushes once; switching directly to another detail while open must NOT push again
  await ctx.openPlexDetail('item-1', 'Movie 1');
  assert.equal(pushCount, 2);
  await ctx.openPlexDetail('item-2', 'Movie 2');
  assert.equal(pushCount, 2, 'opening detail while already open must not push a duplicate layer');

  // play_now force-closes both detail and shell immediately and pops both layers
  await ctx._plexRunAction({ id: 'item-2' }, 'play_now', null, '', '', '');
  assert.equal(ctx.window.relaytvPlex.isOpen(), false);
  assert.equal(ctx.window.relaytvPlex.isDetailOpen(), false);
  assert.equal(elements.get('plexShell').classList.contains('hidden'), true);
  assert.deepEqual(historyCalls.at(-1), ['go', -2]);

  // Simulate popstate completing the history.go(-2)
  ctx.__uiNavDepth = 0;
  historyCalls.length = 0;

  // Clicking #plexBackBtn force-closes immediately
  await ctx.openPlexShell();
  assert.equal(ctx.window.relaytvPlex.isOpen(), true);
  elements.get('plexBackBtn').listeners.get('click')[0]();
  assert.equal(ctx.window.relaytvPlex.isOpen(), false);
  assert.equal(elements.get('plexShell').classList.contains('hidden'), true);
  assert.equal(historyCalls.at(-1), 'back');
});

// --- 3. Jellyfin shell Escape stopPropagation, Back button, and TV series race ---

test('Jellyfin detail Escape stops propagation and shell Back honors mobile vs desktop', () => {
  const elements = new Map();
  for (const id of ['jellyfinShell', 'jfGrid', 'jfDetail', 'jfRows', 'jfShellBackBtn', 'jfDetailBackdrop', 'jfSearchInput', 'jfSortSelect']) {
    elements.set(id, makeEl(id));
  }
  const winListeners = new Map();
  const historyCalls = [];
  const ctx = vm.createContext({
    AbortController,
    URLSearchParams,
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
    _uiPushLayer() {
      ctx.__uiNavDepth += 1;
    },
    history: {
      back: () => historyCalls.push('back'),
      go: (n) => historyCalls.push(['go', n]),
    },
    refresh: async () => {},
    requestAnimationFrame: (cb) => cb(),
    setTimeout: () => 1,
    clearTimeout() {},
    window: {
      innerWidth: 1280,
      matchMedia: () => ({ matches: false }),
      addEventListener(type, fn) {
        if (!winListeners.has(type)) winListeners.set(type, []);
        winListeners.get(type).push(fn);
      },
      setTimeout: () => 1,
    },
    document: {
      activeElement: null,
      body: makeEl('body'),
      documentElement: makeEl('html'),
      scrollingElement: makeEl('html'),
      getElementById: (id) => elements.get(id) || null,
      createElement: () => makeEl(),
      createDocumentFragment: () => makeEl('fragment'),
      querySelectorAll: () => [],
      querySelector: () => null,
    },
    fetch: async () => ({ ok: true, status: 200, json: async () => ({}) }),
  });

  vm.runInContext(JELLYFIN_JS, ctx, { filename: 'jellyfin.js' });
  ctx.bindJellyfinUi();
  vm.runInContext('_jfSetShellVisible(true); _uiPushLayer(); _jfOpenDetailPanel();', ctx);
  assert.equal(ctx._jfIsDetailOpen(), true);

  // Escape inside #jfDetail must call stopPropagation so window keydown doesn't also close shell
  const actionBtn = makeEl('act');
  actionBtn.closest = (sel) => (sel === '.jfActionRow button' ? actionBtn : null);
  elements.get('jfDetail').querySelectorAll = (sel) => (sel === '.jfActionRow button' ? [actionBtn] : []);
  let stopped = false;
  let prevented = false;
  const ev = {
    key: 'Escape',
    target: actionBtn,
    preventDefault: () => { prevented = true; },
    stopPropagation: () => { stopped = true; },
  };
  elements.get('jfDetail').listeners.get('keydown')[0](ev);
  assert.equal(stopped, true, 'Escape inside #jfDetail action button must call stopPropagation()');
  assert.equal(prevented, true);

  // Also test Escape from .jfThumbNav
  const navBtn = makeEl('nav');
  navBtn.closest = (sel) => (sel === '.jfThumbNav' ? navBtn : null);
  let navStopped = false;
  elements.get('jfDetail').listeners.get('keydown')[0]({
    key: 'Escape',
    target: navBtn,
    preventDefault() {},
    stopPropagation: () => { navStopped = true; },
  });
  assert.equal(navStopped, true, 'Escape inside #jfDetail thumb nav must call stopPropagation()');

  // Mobile #jfShellBackBtn closes detail panel first
  ctx.window.innerWidth = 600;
  historyCalls.length = 0;
  vm.runInContext('_jfSetShellVisible(true); _jfOpenDetailPanel();', ctx);
  elements.get('jfShellBackBtn').onclick();
  assert.equal(vm.runInContext('__jfUiVisible', ctx), true);
  assert.equal(historyCalls.at(-1), 'back');

  // Desktop #jfShellBackBtn force-closes shell immediately
  ctx.window.innerWidth = 1280;
  historyCalls.length = 0;
  vm.runInContext('_jfSetShellVisible(true); _jfOpenDetailPanel();', ctx);
  elements.get('jfShellBackBtn').onclick();
  assert.equal(vm.runInContext('__jfUiVisible', ctx), false);
  assert.equal(ctx._jfIsDetailOpen(), false);
});

test('loadJellyfinTvSeriesDetail ignores stale out-of-order responses via _jfBeginBrowseRequest', async () => {
  const elements = new Map();
  for (const id of ['jellyfinShell', 'jellyfinCard', 'jfGrid', 'jfRows', 'jfDetail', 'jfStatus', 'jfConnection', 'jfConnectionLabel', 'jfCardHeadLabel', 'jfSearchInput', 'jfSortSelect', 'jfAlphaIndicator', 'jfActionStatus']) {
    elements.set(id, makeEl(id));
  }
  const pending = new Map();
  const ctx = vm.createContext({
    AbortController,
    URLSearchParams,
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
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    __uiNavDepth: 0,
    _uiPushLayer() {},
    refresh: async () => {},
    requestAnimationFrame: (cb) => cb(),
    setTimeout: () => 1,
    clearTimeout() {},
    window: { innerWidth: 1280, matchMedia: () => ({ matches: false }), addEventListener() {}, setTimeout: () => 1 },
    document: {
      activeElement: null,
      body: makeEl('body'),
      documentElement: makeEl('html'),
      scrollingElement: makeEl('html'),
      getElementById: (id) => elements.get(id) || null,
      createElement: () => makeEl(),
      createDocumentFragment: () => makeEl('fragment'),
      querySelectorAll: () => [],
      querySelector: () => null,
    },
    fetch: (url, opts) => new Promise((resolve, reject) => {
      pending.set(String(url), { resolve, reject, signal: opts && opts.signal });
    }),
  });
  vm.runInContext(JELLYFIN_JS, ctx, { filename: 'jellyfin.js' });
  vm.runInContext("__jfActiveTab = 'tv';", ctx);

  const pA = ctx.loadJellyfinTvSeriesDetail('series-a', { title: 'Series A' });
  const pB = ctx.loadJellyfinTvSeriesDetail('series-b', { title: 'Series B' });

  // Series A request must have been aborted when Series B began
  assert.equal(pending.get('/browse/tv/series/series-a/seasons').signal.aborted, true);

  // Resolve Series B seasons + episodes
  pending.get('/browse/tv/series/series-b/seasons').resolve({
    ok: true,
    status: 200,
    json: async () => ({ seasons: [{ season_number: 1, title: 'Season 1' }] }),
  });
  await new Promise((r) => setImmediate(r));
  pending.get('/browse/tv/series/series-b/episodes?season_number=1').resolve({
    ok: true,
    status: 200,
    json: async () => ({ episodes: [{ item_id: 'b-e1', title: 'B1', type: 'episode' }] }),
  });
  await pB;
  assert.equal(vm.runInContext('__jfTvSeriesId', ctx), 'series-b');

  // Now resolve stale Series A late — must NOT overwrite Series B
  pending.get('/browse/tv/series/series-a/seasons').resolve({
    ok: true,
    status: 200,
    json: async () => ({ seasons: [{ season_number: 1, title: 'Season 1' }] }),
  });
  await pA;
  assert.equal(vm.runInContext('__jfTvSeriesId', ctx), 'series-b');
  assert.equal(vm.runInContext('__jfBusy', ctx), false);
});

// --- 4. IPTV shell nav integration & pendingReload queue ---------------------

test('IPTV shell pushes nav layer, exposes window.relaytvIptv, and queues pendingReload while busy', async () => {
  const elements = new Map();
  for (const id of [
    'iptvShell', 'iptvOpenBtn', 'iptvBackBtn', 'iptvStatus',
    'iptvBrowsePanel', 'iptvDiscoverPanel', 'iptvSourcesPanel',
    'iptvChannelGrid', 'iptvDiscoverGrid', 'iptvMoreBtn', 'iptvDiscoverMoreBtn',
    'iptvDiscoverSearch', 'iptvDiscoverGroup', 'iptvSearch',
    'iptvSourceList', 'iptvAddCard', 'iptvDirectoryGrid',
  ]) {
    elements.set(id, makeEl(id, id === 'iptvShell' ? ['hidden'] : []));
  }
  let pushCount = 0;
  const historyCalls = [];
  const discFetchCalls = [];
  const pendingDisc = [];

  const ctx = vm.createContext({
    URLSearchParams,
    Set,
    Number,
    String,
    Array,
    Object,
    JSON,
    Promise,
    Error,
    setTimeout: () => 1,
    clearTimeout() {},
    history: { back: () => historyCalls.push('back') },
    window: { confirm: () => true },
    document: {
      readyState: 'complete',
      activeElement: null,
      body: makeEl('body'),
      getElementById: (id) => elements.get(id) || null,
      querySelectorAll: () => [],
      addEventListener() {},
    },
    __uiNavDepth: 0,
    _uiPushLayer() {
      pushCount += 1;
      ctx.__uiNavDepth += 1;
    },
    fetch: (url) => {
      const u = String(url);
      if (u === '/settings') return Promise.resolve({ ok: true, json: async () => ({ iptv_enabled: true }) });
      if (u === '/iptv/sources') return Promise.resolve({ ok: true, json: async () => ({ items: [{ id: 's1', name: 'Src' }] }) });
      if (u.startsWith('/iptv/channels?') && u.includes('visibility=visible')) {
        discFetchCalls.push(u);
        return new Promise((resolve) => pendingDisc.push(resolve));
      }
      return Promise.resolve({ ok: true, json: async () => ({ items: [], total: 0, has_more: false, groups: [] }) });
    },
  });

  vm.runInContext(IPTV_JS, ctx, { filename: 'iptv.js' });
  await new Promise((r) => setImmediate(r));

  assert.equal(typeof ctx.window.relaytvIptv.isOpen, 'function');
  assert.equal(typeof ctx.window.relaytvIptv.close, 'function');
  assert.equal(ctx.window.relaytvIptv.isOpen(), false);

  // Open shell -> pushes layer once
  await elements.get('iptvOpenBtn').listeners.get('click')[0]();
  assert.equal(ctx.window.relaytvIptv.isOpen(), true);
  assert.equal(pushCount, 1);

  // Trigger a Discover load while busy, then change group filter -> must queue pendingReload
  const groupSel = elements.get('iptvDiscoverGroup');
  groupSel.value = 'News';
  groupSel.listeners.get('change')[0]();
  assert.equal(discFetchCalls.length, 1);
  assert.match(discFetchCalls[0], /group=News/);

  // While first request is still pending, user switches group to Sports
  groupSel.value = 'Sports';
  groupSel.listeners.get('change')[0]();
  assert.equal(discFetchCalls.length, 1, 'second request waits for busy to finish');

  // Resolve first request -> queued pendingReload fires immediately for Sports
  pendingDisc.shift()({ ok: true, json: async () => ({ items: [], total: 0, has_more: false, groups: ['News', 'Sports'] }) });
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setImmediate(r));
  assert.equal(discFetchCalls.length, 2, 'queued pendingReload must run after busy finishes');
  assert.match(discFetchCalls[1], /group=Sports/);
  pendingDisc.shift()({ ok: true, json: async () => ({ items: [], total: 0, has_more: false, groups: ['News', 'Sports'] }) });
  await new Promise((r) => setImmediate(r));

  // Non-nav close calls history.back(); nav close hides shell
  ctx.window.relaytvIptv.close();
  assert.deepEqual(historyCalls, ['back']);
  ctx.window.relaytvIptv.close({ fromNav: true });
  assert.equal(ctx.window.relaytvIptv.isOpen(), false);
  assert.equal(elements.get('iptvShell').classList.contains('hidden'), true);
});

// --- 5. Realtime reconnect watchdog & 'poll' policy --------------------------

test('_ensureUiEventStream preserves backoff timer and in-flight connects; poll policy never opens SSE', async () => {
  let connectCalls = 0;
  let clearedTimers = [];
  let sseCalls = 0;
  let reconnectScheduled = 0;
  const ctx = vm.createContext({
    Date,
    URL,
    String,
    window: {
      location: { href: 'http://127.0.0.1:8787/ui', protocol: 'http:' },
      clearTimeout: (id) => clearedTimers.push(id),
      WebSocket: function FailingWS() { throw new Error('blocked'); },
      __relaytvRealtimeTransport: '',
    },
    __uiEventSource: null,
    __uiEventSourceLastTs: 0,
    __uiEventSourceBornTs: 0,
    __uiEventGeneration: 0,
    __uiEventConnectInFlight: false,
    __uiEventReconnectTimer: 42,
    __uiRealtimePolicy: {
      retire() {},
      select: () => 'poll',
    },
    _loadUiRealtimeCapabilities: async () => ({ websocket: { enabled: false }, sse: { enabled: false } }),
    _openUiWebSocketTransport() {},
    _openUiSseTransport() { sseCalls += 1; },
    _scheduleUiEventReconnect() { reconnectScheduled += 1; },
    connectUiEventStream() { connectCalls += 1; },
  });

  vm.runInContext(extractFunction(APP_JS, '_uiEventHealthy'), ctx);
  vm.runInContext(extractFunction(APP_JS, '_closeUiEventStream'), ctx);
  vm.runInContext(extractFunction(APP_JS, '_ensureUiEventStream'), ctx);
  vm.runInContext(extractFunction(APP_JS, '_openUiWebSocketTransport'), ctx);
  vm.runInContext(extractFunction(APP_JS, '_connectUiRealtimeGeneration'), ctx);

  // Backoff timer active -> _ensureUiEventStream must NOT abort backoff
  ctx._ensureUiEventStream();
  assert.equal(connectCalls, 0);

  // Connect in flight -> _ensureUiEventStream must NOT abort in-flight capability fetch
  ctx.__uiEventReconnectTimer = 0;
  ctx.__uiEventConnectInFlight = true;
  ctx._ensureUiEventStream();
  assert.equal(connectCalls, 0);

  // _closeUiEventStream clears active reconnect timer
  ctx.__uiEventConnectInFlight = false;
  ctx.__uiEventReconnectTimer = 99;
  ctx._closeUiEventStream('manual');
  assert.deepEqual(clearedTimers, [99]);
  assert.equal(ctx.__uiEventReconnectTimer, 0);

  // selected === 'poll' must NOT open SSE
  ctx.__uiEventGeneration = 5;
  await ctx._connectUiRealtimeGeneration(5);
  assert.equal(sseCalls, 0);
  assert.equal(ctx.window.__relaytvRealtimeTransport, 'poll');
  assert.equal(reconnectScheduled, 1);

  // WebSocket constructor catch with sse.enabled === false must NOT open SSE
  ctx._openUiWebSocketTransport({ websocket: { enabled: true }, sse: { enabled: false } }, 5);
  assert.equal(sseCalls, 0);
  assert.equal(reconnectScheduled, 2);

  // Overlay inline JS also guards selected === 'sse' and capabilities?.sse?.enabled !== false
  assert.match(ROUTES_INIT_PY, /else if\(selected === 'sse'\) openOverlaySse\(capabilities, generation\);/);
  assert.match(ROUTES_INIT_PY, /if\(capabilities\?\.sse\?\.enabled !== false\) openOverlaySse\(capabilities, generation\);/);
});

// --- 6. Audio dropdown 'auto' normalization, Bearer fetch wrapper, async races & URL hygiene ---

test('_populateAudioDeviceSelect normalizes auto (case-insensitive) to Auto (System Default)', () => {
  const ctx = vm.createContext({
    Array,
    Map,
    String,
    document: { createElement: () => makeEl('option') },
  });
  vm.runInContext(extractFunction(APP_JS, '_normalizeAudioSelectionKey'), ctx);
  vm.runInContext(extractFunction(APP_JS, '_populateAudioDeviceSelect'), ctx);

  const selectEl = makeEl('select');
  ctx._populateAudioDeviceSelect(selectEl, { audio_devices: [{ id: 'alsa/hdmi:0', label: 'HDMI 1' }] }, 'AUTO');
  assert.equal(selectEl.children.length, 2);
  assert.equal(selectEl.value, '');
});

test('Bearer fetch wrapper handles URL instances, strips aborted signal on retry, and clears bad token on retry 401', async () => {
  const storage = new Map();
  const seenCalls = [];
  const promptAnswers = ['wrong-token', 'good-token'];
  const nativeFetch = async (input, opts) => {
    seenCalls.push({ input, opts });
    const auth = opts && opts.headers ? opts.headers.get('Authorization') : null;
    if (auth === 'Bearer good-token') return { status: 200, headers: new Headers() };
    return { status: 401, headers: new Headers({ 'www-authenticate': 'Bearer realm="relaytv"' }) };
  };

  const iifeEnd = APP_JS.indexOf('})();') + 5;
  const iifeSrc = APP_JS.slice(0, iifeEnd);
  const win = {
    location: { href: 'http://127.0.0.1:8787/ui', origin: 'http://127.0.0.1:8787' },
    localStorage: {
      getItem: (k) => (storage.has(k) ? storage.get(k) : null),
      setItem: (k, v) => storage.set(k, String(v)),
      removeItem: (k) => storage.delete(k),
    },
    prompt: () => promptAnswers.shift() || '',
    fetch: nativeFetch,
  };
  const ctx = vm.createContext({ window: win, URL, Headers, Object, String });
  vm.runInContext(iifeSrc, ctx);

  // First attempt: URL instance + already-aborted signal; user enters wrong token -> retry returns 401 -> token cleared & re-prompt allowed
  const ac = new AbortController();
  ac.abort();
  const res1 = await win.fetch(new URL('/play', 'http://127.0.0.1:8787'), { method: 'POST', signal: ac.signal });
  assert.equal(res1.status, 401);
  assert.equal(seenCalls.length, 2);
  assert.equal(seenCalls[1].opts.signal, undefined, 'retry after window.prompt must not reuse expired/aborted signal');
  assert.equal(storage.has('relaytv_api_token'), false, 'bad token must be cleared on retry 401');

  // Second attempt: prompt is allowed again because promptedThisLoad was reset; good token succeeds
  const res2 = await win.fetch(new URL('/play', 'http://127.0.0.1:8787'), { method: 'POST' });
  assert.equal(res2.status, 200);
  assert.equal(storage.get('relaytv_api_token'), 'good-token');
});

test('loadSettingsUi survives /devices failure and aborts DOM overwrite if __settingsDirty flips mid-flight', async () => {
  const deviceName = makeEl('setDeviceName');
  deviceName.value = 'User Typing';
  const elements = new Map([['setDeviceName', deviceName]]);
  let resolveSettings;
  const ctx = vm.createContext({
    Promise,
    String,
    Array,
    Object,
    document: { getElementById: (id) => elements.get(id) || null },
    _refreshBrowseServersSettings() {},
    applyJfBranding() {},
    _populateAudioDeviceSelect() {},
    fetch: (url) => {
      if (url === '/devices') return Promise.reject(new Error('devices offline'));
      if (url === '/settings') {
        return new Promise((resolve) => {
          resolveSettings = () => resolve({ ok: true, json: async () => ({ device_name: 'Server Name' }) });
        });
      }
      return Promise.resolve({ ok: false });
    },
    __settingsDirty: false,
  });
  vm.runInContext('var __settingsLoadSeq = 0;\n' + extractFunction(APP_JS, 'loadSettingsUi'), ctx);

  const loadPromise = ctx.loadSettingsUi();
  // User starts editing before /settings resolves
  ctx.__settingsDirty = true;
  resolveSettings();
  await loadPromise;
  assert.equal(deviceName.value, 'User Typing', 'in-flight loadSettingsUi must not clobber dirty form');
});

test('_applyUiJellyfinEvent deduplicates 700ms deferred timer without marking stream alive or reloading settings on retry', () => {
  const timers = [];
  let aliveCalls = 0;
  let settingsLoads = 0;
  let defaultLoads = 0;
  const settingsBd = makeEl('settingsBackdrop');
  const ctx = vm.createContext({
    console,
    __jfUiVisible: true,
    __jfBusy: true,
    __jfLastMode: 'home',
    __jfLastQuery: '',
    __jfActiveTab: 'dashboard',
    __jfSelectedItemId: '',
    __settingsDirty: false,
    document: {
      activeElement: null,
      getElementById: (id) => (id === 'settingsBackdrop' ? settingsBd : null),
    },
    window: {
      setTimeout: (fn, ms) => {
        timers.push({ fn, ms });
        return timers.length;
      },
      clearTimeout() {},
    },
    _uiEventMarkAlive: () => { aliveCalls += 1; },
    _isSettingsControlFocused: () => false,
    loadSettingsUi: async () => { settingsLoads += 1; },
    _jfLoadActiveTabDefault: () => { defaultLoads += 1; },
    _jfIsDetailOpen: () => false,
    refresh: async () => {},
  });
  vm.runInContext(extractFunction(APP_JS, '_applyUiJellyfinEvent'), ctx);

  ctx._applyUiJellyfinEvent({ refresh_settings: true, refresh_active_tab: true });
  ctx._applyUiJellyfinEvent({ refresh_settings: true, refresh_active_tab: true });
  assert.equal(timers.length, 1, 'multiple busy events must deduplicate into one 700ms deferred timer');
  assert.equal(aliveCalls, 2);
  assert.equal(settingsLoads, 2);

  // Fire deferred retry once busy clears -> must NOT call _uiEventMarkAlive or loadSettingsUi again
  ctx.__jfBusy = false;
  timers[0].fn();
  assert.equal(aliveCalls, 2);
  assert.equal(settingsLoads, 2);
  assert.equal(defaultLoads, 1);
});

test('URL hygiene: _aboutSetHref rejects javascript:, seerr backdrop escapes CSS URL, _letterFaviconDataUri guards prototype keys', () => {
  const link = makeEl('aboutGithubLink');
  link.href = 'https://github.com/mcgeezy/relaytv';
  const seerrDetail = makeEl('seerrDetail');
  const ctx = vm.createContext({
    String,
    Array,
    Object,
    Number,
    encodeURIComponent,
    document: {
      getElementById: (id) => (id === 'aboutGithubLink' ? link : (id === 'seerrDetail' ? seerrDetail : null)),
      createElement: () => makeEl(),
    },
    __favBrandColors: { 'youtube.com': '#ff0033' },
    __favPalette: ['#2563eb'],
    _seerrRequestControls: () => makeEl(),
    closeSeerrDetail: () => {},
  });
  vm.runInContext(extractFunction(APP_JS, '_aboutSetHref'), ctx);
  vm.runInContext(extractFunction(APP_JS, '_letterFaviconDataUri'), ctx);
  vm.runInContext(extractFunction(SEERR_JS, '_seerrRenderDetail'), ctx);

  ctx._aboutSetHref('aboutGithubLink', 'javascript:alert(1)');
  assert.equal(link.href, 'https://github.com/mcgeezy/relaytv');
  ctx._aboutSetHref('aboutGithubLink', 'https://example.org/release');
  assert.equal(link.href, 'https://example.org/release');

  ctx._seerrRenderDetail({
    title: 'Test',
    backdrop_url: 'https://img.example/bg.jpg"); background:url("https://evil.tld',
  });
  const hero = seerrDetail.children[0];
  assert.equal(hero.style.backgroundImage, 'url("https://img.example/bg.jpg; background:url(https://evil.tld")');

  const protoUri = ctx._letterFaviconDataUri('constructor');
  assert.match(protoUri, /^data:image\/svg\+xml;utf8,/);
  assert.equal(decodeURIComponent(protoUri).includes('function Object'), false);
  assert.equal(decodeURIComponent(protoUri).includes("fill='#2563eb'"), true);
});
