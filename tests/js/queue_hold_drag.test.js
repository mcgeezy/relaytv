'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const REMOTE_JS = fs.readFileSync(
  path.join(__dirname, '../../app/relaytv_app/static/ui/remote.js'),
  'utf8',
);

function extractFunction(name) {
  for (const prefix of [`async function ${name}(`, `function ${name}(`]) {
    const start = REMOTE_JS.indexOf(prefix);
    if (start === -1) continue;
    let depth = 0;
    let seenBody = false;
    for (let i = start; i < REMOTE_JS.length; i += 1) {
      const ch = REMOTE_JS[i];
      if (ch === '{') { depth += 1; seenBody = true; }
      else if (ch === '}') {
        depth -= 1;
        if (seenBody && depth === 0) return REMOTE_JS.slice(start, i + 1);
      }
    }
  }
  throw new Error(`${name} not found in remote.js`);
}

function makeClassList(initial = []) {
  const set = new Set(initial);
  return {
    add: (...keys) => keys.forEach((k) => set.add(k)),
    remove: (...keys) => keys.forEach((k) => set.delete(k)),
    contains: (k) => set.has(k),
  };
}

function makeTile(index, queueId, top, height = 66) {
  const tile = {
    dataset: {index: String(index), queueId},
    style: {transform: ''},
    isConnected: true,
    classList: makeClassList(['qTile']),
    getBoundingClientRect: () => ({top, bottom: top + height, height, left: 0, width: 320}),
    closest: (sel) => (sel === '.qTile' ? tile : null),
  };
  const body = {
    closest: (sel) => (sel === '.qTile' ? tile : null),
  };
  const handle = {
    closest: (sel) => (sel === '.qHandle' ? handle : (sel === '.qTile' ? tile : null)),
  };
  const menuBtn = {
    closest: (sel) => (sel.includes('button') || sel.includes('.qMenuBtn') ? menuBtn : (sel === '.qTile' ? tile : null)),
  };
  return {tile, body, handle, menuBtn};
}

function fixture() {
  const tiles = [
    makeTile(0, 'q-0', 100),
    makeTile(1, 'q-1', 173),
    makeTile(2, 'q-2', 246),
  ];
  const olListeners = new Map();
  const winListeners = new Map();
  const moves = [];
  const timers = new Map();
  let nextTimerId = 1;
  let capturedPointer = null;
  let vibrated = 0;

  const ol = {
    classList: makeClassList(['queueList']),
    addEventListener: (type, fn) => {
      if (!olListeners.has(type)) olListeners.set(type, []);
      olListeners.get(type).push(fn);
    },
    querySelectorAll: (sel) => {
      if (sel === '.qTile') return tiles.map((t) => t.tile);
      return tiles.map((t) => t.tile);
    },
    setPointerCapture: (id) => { capturedPointer = id; },
    releasePointerCapture: () => { capturedPointer = null; },
  };

  const context = vm.createContext({
    console,
    Math,
    Date,
    Array,
    String,
    parseInt,
    isNaN,
    __draggingQueue: false,
    __dragStartTs: 0,
    __queueDnDBound: false,
    __queueDnDCleanup: null,
    navigator: {vibrate: () => { vibrated += 1; }},
    setTimeout: (fn, ms) => {
      const id = nextTimerId++;
      timers.set(id, {fn, ms});
      return id;
    },
    clearTimeout: (id) => { timers.delete(id); },
    document: {
      body: {classList: makeClassList()},
      getElementById: (id) => (id === 'queue' ? ol : null),
      querySelectorAll: () => tiles.map((t) => t.tile),
      elementFromPoint: () => null,
    },
    window: {
      addEventListener: (type, fn) => {
        if (!winListeners.has(type)) winListeners.set(type, []);
        winListeners.get(type).push(fn);
      },
    },
    qMove: async (from, to, fromId, toId) => {
      moves.push({from, to, fromId, toId});
    },
  });

  vm.runInContext(extractFunction('_queueTileFromPoint'), context);
  vm.runInContext(extractFunction('_queueTileTargetForY'), context);
  vm.runInContext(extractFunction('bindQueuePointerDnD'), context);
  context.bindQueuePointerDnD();

  const emitOl = async (type, event) => {
    for (const fn of olListeners.get(type) || []) await fn(event);
  };
  const flushTimers = () => {
    for (const [id, entry] of Array.from(timers.entries())) {
      timers.delete(id);
      entry.fn();
    }
  };

  return {
    tiles,
    ol,
    context,
    moves,
    emitOl,
    flushTimers,
    pendingTimers: () => timers.size,
    capturedPointer: () => capturedPointer,
    vibrated: () => vibrated,
  };
}

test('holding a queue item body activates drag and reorders with stable queue ids', async () => {
  const state = fixture();

  await state.emitOl('pointerdown', {
    target: state.tiles[0].body,
    button: 0,
    pointerId: 7,
    clientX: 120,
    clientY: 130,
    preventDefault() {},
  });
  assert.equal(state.context.__draggingQueue, false);
  assert.equal(state.tiles[0].tile.classList.contains('dragArming'), true);
  assert.equal(state.pendingTimers(), 1);

  state.flushTimers();
  assert.equal(state.context.__draggingQueue, true);
  assert.equal(state.tiles[0].tile.classList.contains('dragging'), true);
  assert.equal(state.capturedPointer(), 7);
  assert.equal(state.vibrated(), 1);

  await state.emitOl('pointermove', {
    pointerId: 7,
    clientX: 120,
    clientY: 280,
    preventDefault() {},
  });
  assert.equal(state.tiles[2].tile.classList.contains('dragInsertAfter'), true);

  await state.emitOl('pointerup', {
    pointerId: 7,
    preventDefault() {},
  });
  assert.equal(state.context.__draggingQueue, false);
  assert.equal(state.tiles[0].tile.style.transform, '');
  assert.deepEqual(state.moves, [{from: 0, to: 2, fromId: 'q-0', toId: 'q-2'}]);
});

test('scrolling before hold threshold cancels hold-to-drag cleanly', async () => {
  const state = fixture();

  await state.emitOl('pointerdown', {
    target: state.tiles[1].body,
    button: 0,
    pointerId: 3,
    clientX: 100,
    clientY: 200,
    preventDefault() {},
  });
  assert.equal(state.pendingTimers(), 1);

  await state.emitOl('pointermove', {
    pointerId: 3,
    clientX: 100,
    clientY: 225,
    preventDefault() {},
  });
  assert.equal(state.pendingTimers(), 0);
  assert.equal(state.context.__draggingQueue, false);
  assert.equal(state.tiles[1].tile.classList.contains('dragArming'), false);
});

test('tapping queue item action button never arms hold-to-drag', async () => {
  const state = fixture();

  await state.emitOl('pointerdown', {
    target: state.tiles[0].menuBtn,
    button: 0,
    pointerId: 4,
    clientX: 300,
    clientY: 130,
    preventDefault() {},
  });
  assert.equal(state.pendingTimers(), 0);
  assert.equal(state.context.__draggingQueue, false);
});

test('pointercancel aborts an active drag without reordering', async () => {
  const state = fixture();

  await state.emitOl('pointerdown', {
    target: state.tiles[2].handle,
    button: 0,
    pointerId: 9,
    clientX: 250,
    clientY: 270,
    preventDefault() {},
  });
  assert.equal(state.context.__draggingQueue, true);

  await state.emitOl('pointermove', {
    pointerId: 9,
    clientX: 250,
    clientY: 120,
    preventDefault() {},
  });
  assert.equal(state.tiles[0].tile.classList.contains('dragInsertBefore'), true);

  await state.emitOl('pointercancel', {
    pointerId: 9,
    preventDefault() {},
  });
  assert.equal(state.context.__draggingQueue, false);
  assert.deepEqual(state.moves, []);
});
