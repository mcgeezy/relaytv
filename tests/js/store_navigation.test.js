'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const {createStore} = require('../../app/relaytv_app/static/ui/store.js');
const {createLayerNavigation} = require('../../app/relaytv_app/static/ui/navigation.js');

test('status subscribers receive new snapshots and stop after unsubscribe', () => {
  const store = createStore({state:'idle'});
  const seen = [];
  const unsubscribe = store.subscribe(snapshot => seen.push(snapshot.state));

  store.setSnapshot({state:'playing'});
  unsubscribe();
  unsubscribe();
  store.setSnapshot({state:'paused'});

  assert.deepEqual(seen, ['playing']);
  assert.equal(store.getSnapshot().state, 'paused');
});

test('transient UI state stays separate from the server snapshot', () => {
  const snapshot = {playing:true};
  const store = createStore(snapshot);
  store.setTransient('selectedQueueId', 'queue-2');

  assert.equal(store.getSnapshot(), snapshot);
  assert.equal(store.getTransient('selectedQueueId'), 'queue-2');
  assert.equal(store.getTransient('missing', 'fallback'), 'fallback');
});

test('layer navigation owns depth and removes its pop listener cleanly', () => {
  const listeners = new Map();
  const pushes = [];
  const window = {
    history:{pushState:state => pushes.push(state), back(){}},
    addEventListener:(name, listener) => listeners.set(name, listener),
    removeEventListener:(name, listener) => {
      if (listeners.get(name) === listener) listeners.delete(name);
    },
  };
  const navigation = createLayerNavigation(window);
  let pops = 0;
  const unbind = navigation.bind(() => { pops += 1; });
  navigation.push();
  navigation.push();
  listeners.get('popstate')();

  assert.equal(navigation.getDepth(), 1);
  assert.equal(pushes.length, 2);
  assert.equal(pops, 1);
  unbind();
  assert.equal(listeners.has('popstate'), false);
});
