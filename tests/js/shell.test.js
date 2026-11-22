'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const {
  createDestinationState,
  destinationFromHash,
  hasNowPlaying,
  playbackCopy,
} = require('../../app/relaytv_app/static/ui/shell.js');

function fakeWindow(initialHash = '') {
  const listeners = new Map();
  const location = {pathname:'/ui', search:'', hash:initialHash};
  const calls = [];
  function apply(url) {
    const hashIndex = url.indexOf('#');
    location.hash = hashIndex >= 0 ? url.slice(hashIndex) : '';
  }
  return {
    calls,
    history:{
      state:{relaytv_root:1},
      pushState(state, _title, url) { this.state = state; calls.push(['push', url]); apply(url); },
      replaceState(state, _title, url) { this.state = state; calls.push(['replace', url]); apply(url); },
    },
    location,
    addEventListener(type, listener) { listeners.set(type, listener); },
    removeEventListener(type, listener) { if (listeners.get(type) === listener) listeners.delete(type); },
    dispatch(type) { listeners.get(type)?.(); },
  };
}

test('destination state keeps navigation under /ui hashes and reacts to Back', () => {
  const win = fakeWindow('#remote');
  const changes = [];
  const state = createDestinationState(win, (destination, source) => changes.push([destination, source]));
  const unbind = state.bind();

  assert.equal(state.navigate('browse'), 'browse');
  assert.deepEqual(win.calls[0], ['push', '/ui#browse']);
  assert.equal(win.history.state.relaytv_destination, 'browse');
  assert.equal(win.history.state.relaytv_previous_destination, 'remote');
  win.location.hash = '#remote';
  win.dispatch('popstate');
  assert.equal(state.getCurrent(), 'remote');
  assert.deepEqual(changes.at(-1), ['remote', 'popstate']);

  unbind();
  win.location.hash = '#settings';
  win.dispatch('popstate');
  assert.equal(state.getCurrent(), 'remote');
});

test('destination parsing rejects private or unknown hash content', () => {
  assert.equal(destinationFromHash('#browse/jellyfin'), 'browse');
  assert.equal(destinationFromHash('#settings'), 'settings');
  assert.equal(destinationFromHash('#plex-token=secret'), 'remote');
  assert.equal(destinationFromHash(''), 'remote');
});

test('root destination receives an explicit public hash', () => {
  const win = fakeWindow('');
  const state = createDestinationState(win);
  state.navigate('remote', {replace:true});
  assert.deepEqual(win.calls, [['replace', '/ui#remote']]);
  assert.equal(win.location.hash, '#remote');
});

test('compact playback copy derives from the shared status snapshot', () => {
  const status = {
    playing:true,
    paused:false,
    now_playing:{title:'Fixture title', channel:'Season 2 · Episode 4'},
  };
  assert.equal(hasNowPlaying(status), true);
  assert.deepEqual(playbackCopy(status), {
    hasItem:true,
    meta:'Season 2 · Episode 4',
    playing:true,
    title:'Fixture title',
  });
  assert.deepEqual(playbackCopy({playing:false}), {
    hasItem:false,
    meta:'RelayTV',
    playing:false,
    title:'Nothing playing',
  });
  assert.equal(hasNowPlaying({playing:false, has_now_playing:false, now_playing:{title:'Stale'}}), false);
  assert.equal(hasNowPlaying({playing:false, has_now_playing:true, now_playing:{stream:'http://stream'}}), true);
  assert.equal(hasNowPlaying({playing:true, now_playing:null}), true);
});

test('shell.css references only CSS custom properties defined in the UI stylesheets', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const uiDir = path.join(__dirname, '../../app/relaytv_app/static/ui');
  const defined = new Set();
  for (const file of fs.readdirSync(uiDir).filter((name) => name.endsWith('.css'))) {
    const css = fs.readFileSync(path.join(uiDir, file), 'utf8');
    for (const match of css.matchAll(/(--[A-Za-z0-9_-]+)\s*:/g)) defined.add(match[1]);
  }
  const shellCss = fs.readFileSync(path.join(uiDir, 'shell.css'), 'utf8');
  const missing = [];
  for (const match of shellCss.matchAll(/var\(\s*(--[A-Za-z0-9_-]+)\s*\)/g)) {
    if (!defined.has(match[1])) missing.push(match[1]);
  }
  assert.deepEqual(missing, []);
});

test('syncBrowseLaunchButtons clears jellyfinOpenBtn when Plex is disabled and Jellyfin is not ready', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const vm = require('node:vm');
  const remoteSrc = fs.readFileSync(path.join(__dirname, '../../app/relaytv_app/static/ui/remote.js'), 'utf8');
  const start = remoteSrc.indexOf('function syncBrowseLaunchButtons(');
  let depth = 0;
  let seen = false;
  let end = start;
  for (let i = start; i < remoteSrc.length; i += 1) {
    if (remoteSrc[i] === '{') { depth += 1; seen = true; }
    else if (remoteSrc[i] === '}') { depth -= 1; if (seen && depth === 0) { end = i + 1; break; } }
  }
  const fnSrc = remoteSrc.slice(start, end);
  const makeBtn = () => {
    const cls = new Set();
    return {
      disabled:true,
      title:'',
      attrs:new Map(),
      classList:{
        add:(c) => cls.add(c),
        remove:(c) => cls.delete(c),
        contains:(c) => cls.has(c),
        toggle:(c, f) => (f ? cls.add(c) : cls.delete(c)),
      },
      setAttribute(k, v) { this.attrs.set(k, v); },
    };
  };
  const jfBtn = makeBtn();
  const plexBtn = makeBtn();
  const ctx = vm.createContext({
    __jfLaunchVisible:false,
    __plexReady:true,
    document:{
      getElementById:(id) => (id === 'jellyfinOpenBtn' ? jfBtn : (id === 'plexOpenBtn' ? plexBtn : null)),
    },
  });
  vm.runInContext(fnSrc, ctx);
  ctx.syncBrowseLaunchButtons();
  assert.equal(jfBtn.classList.contains('show'), true);
  assert.equal(jfBtn.disabled, false);

  ctx.__plexReady = false;
  ctx.syncBrowseLaunchButtons();
  assert.equal(jfBtn.classList.contains('show'), false);
  assert.equal(jfBtn.disabled, true);
});
