'use strict';

// Command results must keep success, server rejection, and unknown transport
// outcomes distinct. A rejected or ambiguous non-idempotent write is never
// replayed automatically.

const test = require('node:test');
const assert = require('node:assert/strict');
const {createApi} = require('../../app/relaytv_app/static/ui/api.js');

function fixture(responder){
  const calls = {fetches:[], rejected:[], connection:[], refreshes:0};
  const api = createApi({
    fetch:async(url, options) => {
      calls.fetches.push({url, method:options.method});
      return responder(calls.fetches.length);
    },
    AbortController:null,
    setTimeout,
    clearTimeout,
  });
  return {
    calls,
    post:(path, body, options) => api.post(path, body, options, {
      connection:(ok, detail) => calls.connection.push({ok, detail}),
      rejected:detail => calls.rejected.push(detail),
      refresh:async() => { calls.refreshes += 1; },
    }),
  };
}

function jsonResponse(status, payload){
  return {
    ok:status >= 200 && status < 300,
    status,
    json:async() => payload,
  };
}

test('a successful command reports ok and clears the connection failure', async() => {
  const state = fixture(() => jsonResponse(200, {ok:true}));
  const result = await state.post('/pause');

  assert.equal(result.ok, true);
  assert.equal(result.status, 200);
  assert.deepEqual(state.calls.connection, [{ok:true, detail:undefined}]);
  assert.equal(state.calls.refreshes, 1);
});

test('401 surfaces an authorization message, not a connection error', async() => {
  const state = fixture(() => jsonResponse(401, {detail:'api token required'}));
  const result = await state.post('/pause');

  assert.equal(result.ok, false);
  assert.equal(result.status, 401);
  assert.equal(result.reached, true);
  assert.match(state.calls.rejected[0], /API token/i);
  assert.equal(state.calls.connection.length, 0);
});

test('409 shows the server detail verbatim', async() => {
  const state = fixture(() => jsonResponse(409, {detail:'No active playback for snapshot'}));
  const result = await state.post('/snapshot');

  assert.equal(result.ok, false);
  assert.equal(result.status, 409);
  assert.equal(state.calls.rejected[0], 'No active playback for snapshot');
  assert.equal(state.calls.connection.length, 0);
});

test('500 without a parseable body still names the failure', async() => {
  const state = fixture(() => ({
    ok:false,
    status:500,
    json:async() => { throw new Error('not json'); },
  }));
  const result = await state.post('/next');

  assert.equal(result.ok, false);
  assert.equal(state.calls.rejected[0], 'Command failed (HTTP 500)');
});

test('a rejected command is never retried, even when marked idempotent', async() => {
  const state = fixture(() => jsonResponse(409, {detail:'nope'}));
  const result = await state.post('/volume', {set:50}, {idempotent:true});

  assert.equal(result.ok, false);
  assert.equal(state.calls.fetches.length, 1);
});

test('an unreachable server is reported as a connection failure', async() => {
  const state = fixture(() => { throw new Error('network down'); });
  const result = await state.post('/pause');

  assert.equal(result.ok, false);
  assert.equal(result.reached, false);
  assert.equal(state.calls.connection[0].ok, false);
  assert.match(state.calls.connection[0].detail.message, /check connection/i);
});

test('an idempotent command retries once on a network error and can succeed', async() => {
  const state = fixture(attempt => {
    if(attempt === 1) throw new Error('first packet lost');
    return jsonResponse(200, {ok:true});
  });
  const result = await state.post('/volume', {set:50}, {idempotent:true});

  assert.equal(result.ok, true);
  assert.equal(state.calls.fetches.length, 2);
  assert.equal(state.calls.connection.at(-1).ok, true);
});

test('a non-idempotent command is not retried on a network error', async() => {
  const state = fixture(() => { throw new Error('network down'); });
  const result = await state.post('/next');

  assert.equal(result.ok, false);
  assert.equal(state.calls.fetches.length, 1);
});
