'use strict';

// Queue removals carry stable ids on the wire. Indexes remain as a compatibility
// fallback, but a delayed undo-window commit must still name the same item.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const APP_JS = fs.readFileSync(
  path.join(__dirname, '../../app/relaytv_app/static/ui/app.js'),
  'utf8',
);

function extractFunction(name){
  for(const prefix of [`async function ${name}(`, `function ${name}(`]){
    const start = APP_JS.indexOf(prefix);
    if(start === -1) continue;
    let depth = 0;
    let seenBody = false;
    for(let i = start; i < APP_JS.length; i++){
      const ch = APP_JS[i];
      if(ch === '{'){ depth++; seenBody = true; }
      else if(ch === '}'){
        depth--;
        if(seenBody && depth === 0) return APP_JS.slice(start, i + 1);
      }
    }
  }
  throw new Error(`${name} not found in app.js`);
}

function fixture(){
  const queue = [
    {id: 'id-a', title: 'A'},
    {id: 'id-b', title: 'B'},
    {id: 'id-c', title: 'C'},
  ];
  const requests = [];
  const toasts = [];
  let completed = 0;
  let rendered = 0;
  let releaseFirst;
  const firstRequestGate = new Promise(resolve => { releaseFirst = resolve; });

  const context = vm.createContext({
    console,
    JSON,
    __pendingRemove: null,
    __removeFlushPromise: null,
    __lastStatus: {queue: []},
    clearTimeout(){},
    setTimeout(){ return 1; },
    document: {
      querySelector(){
        return {classList: {add(){}, remove(){}}};
      },
    },
    _showToast(message){ toasts.push(message); },
    _hideToast(){},
    _applyQueueSnapshot(){},
    _uiRefreshInteractionLockActive(){
      return !!context.__pendingRemove || !!context.__removeFlushPromise;
    },
    renderStatus(){ rendered++; },
    refresh: async() => { completed++; },
    fetch: async(_url, options) => {
      const {index, queue_id: queueId} = JSON.parse(options.body);
      requests.push({index, queueId: queueId || ''});
      if(requests.length === 1) await firstRequestGate;
      const liveIndex = queueId ? queue.findIndex(item => item.id === queueId) : index;
      if(liveIndex >= 0) queue.splice(liveIndex, 1);
      return {
        ok: true,
        json: async() => ({queue: queue.map(item => ({title: item.title})), queue_length: queue.length}),
      };
    },
  });
  const functions = ['qRemove', '_flushPendingRemove', 'qSoftRemove'];
  if(APP_JS.includes('function _commitPendingRemove(')) functions.splice(1, 0, '_commitPendingRemove');
  for(const name of functions){
    vm.runInContext(extractFunction(name), context, {filename:'app.js'});
  }
  return {
    context,
    queue,
    requests,
    releaseFirst,
    toasts,
    completed: () => completed,
    rendered: () => rendered,
  };
}

test('overlapping soft removals never reuse an index from the old queue', async() => {
  const state = fixture();

  vm.runInContext("qSoftRemove(0, 'id-a'); qSoftRemove(1, 'id-b');", state.context);
  const firstFlush = vm.runInContext('__removeFlushPromise', state.context);

  assert.deepEqual(state.requests, [{index: 0, queueId: 'id-a'}]);
  state.releaseFirst();
  if(firstFlush) await firstFlush;
  while(state.completed() < 1) await new Promise(setImmediate);
  assert.deepEqual(state.queue.map(item => item.title), ['B', 'C']);

  // The refreshed rendering now identifies B as index zero. A new action can
  // safely commit that index without deleting C.
  vm.runInContext("qSoftRemove(0, 'id-b')", state.context);
  await vm.runInContext('_flushPendingRemove()', state.context);

  assert.deepEqual(state.requests, [
    {index: 0, queueId: 'id-a'},
    {index: 0, queueId: 'id-b'},
  ]);
  assert.deepEqual(state.queue.map(item => item.title), ['C']);
  assert.ok(state.toasts.some(message => /select Remove again/.test(message)));
  assert.equal(state.rendered(), 2);
});

test('external queue advancement cannot retarget a delayed removal', async() => {
  const state = fixture();

  vm.runInContext("qSoftRemove(1, 'id-b')", state.context);
  state.queue.shift();
  const flush = vm.runInContext('_flushPendingRemove()', state.context);
  state.releaseFirst();
  await flush;

  assert.deepEqual(state.requests, [{index: 1, queueId: 'id-b'}]);
  assert.deepEqual(state.queue.map(item => item.title), ['C']);
  assert.equal(state.rendered(), 1);
});

test('play-now and drag requests carry stable queue targets', async() => {
  const requests = [];
  const context = vm.createContext({
    console,
    JSON,
    _applyQueueSnapshot(){},
    _showToast(){},
    refresh: async() => {},
    fetch: async(url, options) => {
      requests.push({url, body: JSON.parse(options.body)});
      return {ok: true, json: async() => ({queue: [], queue_length: 0})};
    },
  });
  vm.runInContext(extractFunction('qPlayNow'), context, {filename: 'app.js'});
  vm.runInContext(extractFunction('qMove'), context, {filename: 'app.js'});

  await vm.runInContext("qPlayNow(2, 'id-c')", context);
  await vm.runInContext("qMove(2, 0, 'id-c', 'id-a')", context);

  assert.deepEqual(requests, [
    {url: '/queue/play', body: {index: 2, queue_id: 'id-c'}},
    {
      url: '/queue/move',
      body: {from_index: 2, to_index: 0, queue_id: 'id-c', to_queue_id: 'id-a'},
    },
  ]);
});
