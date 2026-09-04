'use strict';

// Queue removals are index-based on the wire. When an undo window is committed,
// a second index captured from the old rendering must not be sent after the
// first request shifts the queue.

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
  const queue = ['A', 'B', 'C'];
  const requests = [];
  const toasts = [];
  let completed = 0;
  let releaseFirst;
  const firstRequestGate = new Promise(resolve => { releaseFirst = resolve; });

  const context = vm.createContext({
    console,
    JSON,
    __pendingRemove: null,
    __removeFlushPromise: null,
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
    refresh: async() => { completed++; },
    fetch: async(_url, options) => {
      const {index} = JSON.parse(options.body);
      requests.push(index);
      if(requests.length === 1) await firstRequestGate;
      queue.splice(index, 1);
      return {
        ok: true,
        json: async() => ({queue: [...queue], queue_length: queue.length}),
      };
    },
  });
  const functions = ['qRemove', '_flushPendingRemove', 'qSoftRemove'];
  if(APP_JS.includes('function _commitPendingRemove(')) functions.splice(1, 0, '_commitPendingRemove');
  for(const name of functions){
    vm.runInContext(extractFunction(name), context, {filename:'app.js'});
  }
  return {context, queue, requests, releaseFirst, toasts, completed: () => completed};
}

test('overlapping soft removals never reuse an index from the old queue', async() => {
  const state = fixture();

  vm.runInContext('qSoftRemove(0); qSoftRemove(1);', state.context);
  const firstFlush = vm.runInContext('__removeFlushPromise', state.context);

  assert.deepEqual(state.requests, [0]);
  state.releaseFirst();
  if(firstFlush) await firstFlush;
  while(state.completed() < 1) await new Promise(setImmediate);
  assert.deepEqual(state.queue, ['B', 'C']);

  // The refreshed rendering now identifies B as index zero. A new action can
  // safely commit that index without deleting C.
  vm.runInContext('qSoftRemove(0)', state.context);
  await vm.runInContext('_flushPendingRemove()', state.context);

  assert.deepEqual(state.requests, [0, 0]);
  assert.deepEqual(state.queue, ['C']);
  assert.ok(state.toasts.some(message => /select Remove again/.test(message)));
});
