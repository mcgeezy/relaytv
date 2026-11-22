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
});
