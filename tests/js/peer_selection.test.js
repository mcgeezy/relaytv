// SPDX-License-Identifier: GPL-3.0-only
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../app/relaytv_app/static/ui/peers.js'), 'utf8');

function extract(name){
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, name);
  let depth = 0;
  for (let i = source.indexOf('{', start); i < source.length; i++) {
    if (source[i] === '{') depth++;
    else if (source[i] === '}' && --depth === 0) return source.slice(start, i + 1);
  }
  throw Error(name);
}

function fixture(){
  const status = {queue: [{url: 'https://example.com/a', title: 'A', queue_id: 'a'}]};
  const state = {mode: 'send', items: [], deselected: new Set(), scopedSelection: null};
  const backdrop = {classList: {remove(){}}};
  const context = vm.createContext({
    state, Set, encodeURIComponent, document: {activeElement: null},
    lastStatus: () => status, playbackActive: () => false,
    $: id => id === 'peersBackdrop' ? backdrop : null,
    isOpen: () => false, setStatus(){}, setAddHelper(){}, toggleAddForm(){}, syncModes(){},
    load: async() => {},
  });
  for (const name of ['itemRow', 'buildItems', 'isSelected', 'selectedNow', 'selectedQueueIndexes',
    'selectedQueueIds', 'sendRequest', 'open']) {
    vm.runInContext(extract(name), context);
  }
  // Allow the previous implementation to execute fully during revert proof.
  for (const name of ['queueRowCount', 'selectionCoversQueue']) {
    if (source.includes(`function ${name}(`)) vm.runInContext(extract(name), context);
  }
  return {status, state, context, request: () => JSON.parse(JSON.stringify(
    vm.runInContext("sendRequest({id:'bedroom'})", context)
  ))};
}

test('sending the only tile retains its explicit id in the request', () => {
  const f = fixture();
  vm.runInContext("open({index:0, queueId:'a'})", f.context);
  assert.deepEqual(f.request().body, {mode: 'move', queue_ids: ['a']});
});

test('tile selection excludes items arriving while the sheet is open', () => {
  const f = fixture();
  vm.runInContext("open({index:0, queueId:'a'})", f.context);
  f.status.queue.push({url: 'https://example.com/b', title: 'B', queue_id: 'b'});
  vm.runInContext('buildItems()', f.context);
  assert.deepEqual(f.request().body.queue_ids, ['a']);
  // The selected tile disappears: its replacement must not become selected.
  f.status.queue.shift();
  vm.runInContext('buildItems()', f.context);
  assert.deepEqual(f.request().body.queue_ids, []);
});

test('whole-sheet selection sends only the displayed snapshot', () => {
  const f = fixture();
  vm.runInContext('open({})', f.context);
  const request = f.request();
  f.status.queue.push({url: 'https://example.com/b', queue_id: 'b'});
  assert.deepEqual(request.body.queue_ids, ['a']);
});
