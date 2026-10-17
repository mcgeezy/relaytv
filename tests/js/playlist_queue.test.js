'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { webcrypto } = require('node:crypto');

const SOURCE = fs.readFileSync(path.join(__dirname, '../../app/relaytv_app/static/ui/playlist_queue.js'), 'utf8');
const URL_ONLY = 'https://www.youtube.com/playlist?list=PLreview123';
const URL_MIXED = 'https://www.youtube.com/watch?v=jNQXAC9IVRw&list=PLreview123&index=4';
const STORAGE_KEY = 'relaytv_playlist_import_v1';

function fixture(responder, storage = new Map()){
  const nodes = new Map();
  for (const id of ['addUrlInput', 'addPlaylistBtn', 'addQueueBtn', 'addPlayBtn', 'playlistLinkHint', 'playlistImportPanel', 'playlistImportStatus', 'playlistImportCancel', 'playlistImportCheck', 'playlistImportDismiss']){
    const classes = new Set(['hidden']);
    nodes.set(id, {
      value:'', disabled:false, textContent:'', events:{},
      classList:{toggle(name, force){ if(force) classes.add(name); else classes.delete(name); }, contains(name){return classes.has(name);}},
      addEventListener(name, callback){this.events[name] = callback;},
    });
  }
  const calls = [];
  const timers = new Map();
  let nextTimer = 0;
  const window = {
    crypto:webcrypto,
    localStorage:{getItem:key => storage.get(key), setItem:(key, value) => storage.set(key, value), removeItem:key => storage.delete(key)},
    setTimeout:callback => {timers.set(++nextTimer, callback); return nextTimer;},
    clearTimeout:key => timers.delete(key),
    _fetchWithTimeout:async(url, options) => {
      calls.push({url, options});
      return responder(url, options);
    },
    refresh:async() => {},
  };
  vm.runInNewContext(SOURCE, {window, document:{getElementById:id => nodes.get(id)}, URL, Uint8Array, console});
  const api = window.RelayTVPlaylistQueue;
  api.bind();
  return {api, nodes, calls, storage, timers, hidden:id => nodes.get(id).classList.contains('hidden')};
}

function response(status, data){ return {ok:status >= 200 && status < 300, status, json:async() => data}; }
function completed(){return {status:'completed', added:3, skipped:1, detail:'Added 3 videos to queue; skipped 1 unavailable entries'};}
async function settle(){for(let i = 0; i < 8; i++) await Promise.resolve();}

test('playlist detection accepts real YouTube hosts and removes selected-video context', () => {
  const state = fixture(() => response(200, {}));
  const info = state.api.linkInfo(URL_MIXED);
  assert.equal(info.url, URL_ONLY);
  assert.equal(info.hasVideo, true);
  assert.equal(state.api.linkInfo(URL_ONLY).hasVideo, false);
  assert.equal(state.api.linkInfo('https://youtu.be/jNQXAC9IVRw?list=PLreview123').hasVideo, true);
  assert.equal(state.api.linkInfo('https://music.youtube.com/playlist?list=PLreview123').url, URL_ONLY);
  assert.equal(state.api.linkInfo('www.youtube.com/playlist?list=PLreview123').url, URL_ONLY);
  assert.equal(state.api.linkInfo('https://www.youtube.com/embed/videoseries?list=PLreview123').hasVideo, false);
  for (const url of ['https://youtube.com.evil.test/playlist?list=PLreview123', 'https://evil-youtube.com/playlist?list=PLreview123', 'file://youtube.com/playlist?list=PLreview123', 'https://youtube.com/watch?v=jNQXAC9IVRw', URL_ONLY + '&list=PLother']){
    assert.equal(state.api.linkInfo(url), null, url);
  }
});

test('playlist-only links expose the playlist action; mixed links retain video actions', () => {
  const state = fixture(() => response(200, {}));
  state.nodes.get('addUrlInput').value = URL_ONLY;
  state.api.update();
  assert.equal(state.hidden('addPlaylistBtn'), false);
  assert.equal(state.nodes.get('addQueueBtn').disabled, true);
  assert.equal(state.nodes.get('addPlayBtn').disabled, true);
  state.nodes.get('addUrlInput').value = URL_MIXED;
  state.api.update();
  assert.equal(state.nodes.get('addQueueBtn').disabled, false);
  assert.equal(state.nodes.get('addPlayBtn').disabled, false);
  assert.match(state.nodes.get('playlistLinkHint').textContent, /from the beginning/);
});

test('dynamic mixes explain why entire-playlist import is unavailable', () => {
  const state = fixture(() => response(200, {}));
  state.nodes.get('addUrlInput').value = 'https://youtube.com/watch?v=jNQXAC9IVRw&list=RDjNQXAC9IVRw';
  state.api.update();
  assert.equal(state.nodes.get('addPlaylistBtn').disabled, true);
  assert.equal(state.nodes.get('addQueueBtn').disabled, false);
  assert.match(state.nodes.get('playlistLinkHint').textContent, /saved playlist/);
});

test('double taps submit once and store the request ID before sending', async() => {
  let finish;
  let state;
  state = fixture(async(_url, options) => {
    const sent = JSON.parse(options.body);
    assert.equal(sent.request_id, JSON.parse(state.storage.get(STORAGE_KEY)).request_id);
    assert.match(sent.request_id, /^[a-f0-9]{32}$/);
    return new Promise(resolve => { finish = resolve; });
  });
  state.nodes.get('addUrlInput').value = URL_MIXED;
  const first = state.api.submit();
  await state.api.submit();
  assert.equal(state.calls.length, 1);
  assert.equal(JSON.parse(state.calls[0].options.body).url, URL_ONLY);
  finish(response(202, {status:'loading', detail:'Reading playlist…'}));
  await first;
  assert.equal(state.hidden('playlistImportCancel'), false);
  assert.equal(state.nodes.get('addPlaylistBtn').disabled, true);
});

test('completion shows counts and dismissal permits an intentional second import', async() => {
  const state = fixture(() => response(202, completed()));
  state.nodes.get('addUrlInput').value = URL_ONLY;
  await state.api.submit();
  assert.match(state.nodes.get('playlistImportStatus').textContent, /Added 3.*skipped 1/);
  assert.equal(state.hidden('playlistImportDismiss'), false);
  const firstId = JSON.parse(state.calls[0].options.body).request_id;
  state.nodes.get('playlistImportDismiss').onclick();
  assert.equal(state.storage.has(STORAGE_KEY), false);
  assert.equal(state.hidden('playlistImportPanel'), true);
  await state.api.submit();
  assert.notEqual(JSON.parse(state.calls[1].options.body).request_id, firstId);
});

test('an ambiguous POST failure keeps its request ID and only reads status afterward', async() => {
  const state = fixture((url, options) => {
    if (options.method === 'POST') throw new Error('lost response');
    return response(200, completed());
  });
  state.nodes.get('addUrlInput').value = URL_ONLY;
  await state.api.submit();
  const pending = JSON.parse(state.storage.get(STORAGE_KEY));
  assert.equal(state.hidden('playlistImportCheck'), false);
  await state.api.submit();
  assert.equal(state.calls.length, 1);
  await state.api.check();
  assert.equal(state.calls.length, 2);
  assert.equal(state.calls[1].url, '/queue/playlists/' + pending.request_id);
  assert.equal(state.calls[1].options.method, undefined);
  assert.match(state.nodes.get('playlistImportStatus').textContent, /Added 3/);
});

test('a definitive rejection shows its reason and allows dismissal without a status lookup', async() => {
  const state = fixture(() => response(429, {detail:'Playlist imports are busy; try again later'}));
  state.nodes.get('addUrlInput').value = URL_ONLY;
  await state.api.submit();
  assert.match(state.nodes.get('playlistImportStatus').textContent, /busy/);
  assert.equal(state.hidden('playlistImportCheck'), true);
  assert.equal(state.hidden('playlistImportDismiss'), false);
  assert.equal(state.calls.length, 1);
});

test('reload resumes a saved job through GET without sending another import', async() => {
  const storage = new Map([[STORAGE_KEY, JSON.stringify({url:URL_ONLY, request_id:'a'.repeat(32)})]]);
  const state = fixture(() => response(200, completed()), storage);
  await settle();
  assert.equal(state.calls.length, 1);
  assert.equal(state.calls[0].options.method, undefined);
  assert.match(state.nodes.get('playlistImportStatus').textContent, /Added 3/);
});

test('expired or restarted jobs require checking the queue instead of silent resubmission', async() => {
  const storage = new Map([[STORAGE_KEY, JSON.stringify({url:URL_ONLY, request_id:'b'.repeat(32)})]]);
  const state = fixture(() => response(404, {detail:'expired'}), storage);
  await settle();
  assert.match(state.nodes.get('playlistImportStatus').textContent, /Check the queue before/);
  state.nodes.get('addUrlInput').value = URL_ONLY;
  await state.api.submit();
  assert.equal(state.calls.length, 1);
  assert.equal(state.storage.has(STORAGE_KEY), true);
});

test('cancel sends DELETE for the current job and shows the terminal result', async() => {
  const state = fixture((_url, options) => response(202, options.method === 'DELETE'
    ? {status:'cancelled', detail:'Import cancelled; nothing was added'}
    : {status:'loading', detail:'Reading playlist…'}));
  state.nodes.get('addUrlInput').value = URL_ONLY;
  await state.api.submit();
  await state.nodes.get('playlistImportCancel').onclick();
  assert.equal(state.calls[1].options.method, 'DELETE');
  assert.match(state.nodes.get('playlistImportStatus').textContent, /nothing was added/);
  assert.equal(state.hidden('playlistImportCancel'), true);
  assert.equal(state.hidden('playlistImportDismiss'), false);
});

test('web individual-video actions explicitly bypass automatic playlist expansion', async() => {
  const source = fs.readFileSync(path.join(__dirname, '../../app/relaytv_app/static/ui/app.js'), 'utf8');
  const start = source.indexOf('async function submitAddUrl(');
  const end = source.indexOf('\nfunction openNotifyModal(', start);
  assert.ok(start >= 0 && end > start);
  const calls = [];
  const context = vm.createContext({
    addUrlSubmitting:false,
    document:{getElementById:() => ({value:URL_MIXED})},
    window:{RelayTVPlaylistQueue:{linkInfo:() => ({hasVideo:true})}},
    normalizeUrl:value => value, looksLikeUrl:() => true,
    post:async(url, body) => {calls.push({url, body}); return {ok:true};},
    closeAddUrl(){},
  });
  vm.runInContext(source.slice(start, end), context);
  await vm.runInContext("submitAddUrl('queue')", context);
  await vm.runInContext("submitAddUrl('play')", context);
  assert.equal(calls[0].url, '/enqueue');
  assert.equal(calls[1].url, '/play_now');
  assert.ok(calls.every(call => call.body.playlist_mode === 'video'));
});
