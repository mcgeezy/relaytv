'use strict';

// post() used to set ok = true whenever the fetch settled, so a 401, 409, or
// 500 was indistinguishable from success: the UI applied its optimistic state
// and the next poll quietly reverted it. These tests pin the three outcomes
// apart — success, server rejection, and unreachable server — because the
// difference is what the user acts on.

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

function fixture(responder){
  const calls = {fetches: [], rejected: [], conn: [], refreshes: 0};
  const badge = {
    textContent: '',
    _classes: new Set(['hidden']),
    classList: {
      add: (...n) => n.forEach(x => badge._classes.add(x)),
      remove: (...n) => n.forEach(x => badge._classes.delete(x)),
      contains: x => badge._classes.has(x),
    },
  };
  const bodyClasses = new Set();
  const context = vm.createContext({
    console,
    Date,
    Number,
    String,
    document: {
      getElementById: id => (id === 'connBadge' ? badge : null),
      body: {classList: {
        add: x => bodyClasses.add(x),
        remove: x => bodyClasses.delete(x),
        contains: x => bodyClasses.has(x),
      }},
    },
    __connFailStreak: 0,
    __connBadgeStickyUntil: 0,
    _fetchWithTimeout: async(url, opts) => {
      calls.fetches.push({url, method: opts.method});
      return responder(calls.fetches.length);
    },
    refresh: async() => { calls.refreshes++; },
    _setAddHelper(){},
  });
  for(const fn of ['_connSignal', '_commandRejected', '_commandErrorDetail', 'post']){
    vm.runInContext(extractFunction(fn), context, {filename:'app.js'});
  }
  return {
    context,
    calls,
    badge,
    bodyClasses,
    post: (p, body, opts) =>
      vm.runInContext(`post(${JSON.stringify(p)}, ${JSON.stringify(body || null)}, ${JSON.stringify(opts || null)})`, context),
  };
}

function jsonResponse(status, payload){
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async() => payload,
  };
}

test('a successful command reports ok and clears the badge', async() => {
  const state = fixture(() => jsonResponse(200, {ok: true}));
  const result = await state.post('/pause');

  assert.equal(result.ok, true);
  assert.equal(result.status, 200);
  assert.equal(state.badge.classList.contains('hidden'), true);
  assert.equal(state.calls.refreshes, 1);
});

test('401 surfaces an authorization message, not a connection error', async() => {
  const state = fixture(() => jsonResponse(401, {detail: 'api token required'}));
  const result = await state.post('/pause');

  assert.equal(result.ok, false);
  assert.equal(result.status, 401);
  assert.equal(result.reached, true);
  assert.match(state.badge.textContent, /API token/i);
  // A rejection must not claim the connection is down.
  assert.equal(state.bodyClasses.has('connLost'), false);
  assert.equal(state.badge.classList.contains('cmdErr'), true);
});

test('409 shows the server detail verbatim', async() => {
  const state = fixture(() => jsonResponse(409, {detail: 'No active playback for snapshot'}));
  const result = await state.post('/snapshot');

  assert.equal(result.ok, false);
  assert.equal(result.status, 409);
  assert.equal(state.badge.textContent, 'No active playback for snapshot');
  assert.equal(state.bodyClasses.has('connLost'), false);
});

test('500 without a parseable body still names the failure', async() => {
  const state = fixture(() => ({
    ok: false,
    status: 500,
    json: async() => { throw new Error('not json'); },
  }));
  const result = await state.post('/next');

  assert.equal(result.ok, false);
  assert.equal(state.badge.textContent, 'Command failed (HTTP 500)');
});

test('a rejected command is never retried, even when marked idempotent', async() => {
  // The server answered. Resending would apply the command twice.
  const state = fixture(() => jsonResponse(409, {detail: 'nope'}));
  const result = await state.post('/volume', {set: 50}, {idempotent: true});

  assert.equal(result.ok, false);
  assert.equal(state.calls.fetches.length, 1);
});

test('an unreachable server is reported as a connection failure', async() => {
  const state = fixture(() => { throw new Error('network down'); });
  const result = await state.post('/pause');

  assert.equal(result.ok, false);
  assert.equal(result.reached, false);
  assert.equal(state.bodyClasses.has('connLost'), true);
  assert.match(state.badge.textContent, /check connection/i);
});

test('an idempotent command retries once on a network error and can succeed', async() => {
  const state = fixture(attempt => {
    if(attempt === 1) throw new Error('first packet lost');
    return jsonResponse(200, {ok: true});
  });
  const result = await state.post('/volume', {set: 50}, {idempotent: true});

  assert.equal(result.ok, true);
  assert.equal(state.calls.fetches.length, 2);
  assert.equal(state.bodyClasses.has('connLost'), false);
});

test('a non-idempotent command is not retried on a network error', async() => {
  const state = fixture(() => { throw new Error('network down'); });
  const result = await state.post('/next');

  assert.equal(result.ok, false);
  assert.equal(state.calls.fetches.length, 1);
});
