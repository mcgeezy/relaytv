'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const {
  createPolicy,
  createSequenceTracker,
} = require('../../app/relaytv_app/static/ui/realtime_transport.js');

function fixture(){
  let nowMs = 0;
  const fallbackCapabilities = {
    protocol_version:0,
    websocket:{enabled:false},
    sse:{enabled:true, ui:'/ui/events'},
  };
  const websocketCapabilities = {
    protocol_version:1,
    heartbeat_sec:5,
    websocket:{enabled:true, ui:'/ui/ws'},
    sse:{enabled:true, ui:'/ui/events'},
  };
  const policy = createPolicy({
    now:()=>nowMs,
    fallbackCapabilities,
    capabilityTtlMs:300000,
    websocketCooldownMs:60000,
  });
  return {
    fallbackCapabilities,
    policy,
    websocketCapabilities,
    advance(milliseconds){ nowMs += milliseconds; },
  };
}

test('transient capability failures are not cached', async()=>{
  const failures = [
    Object.assign(new Error('temporary upstream failure'), {status:503}),
    new Error('request timed out'),
    new SyntaxError('malformed JSON'),
  ];
  for(const failure of failures){
    const state = fixture();
    let calls = 0;
    const first = await state.policy.discover(async()=>{
      calls += 1;
      throw failure;
    });

    assert.equal(first, state.fallbackCapabilities);
    assert.equal(state.policy.state().cachedCapabilities, null);
    const recovered = await state.policy.discover(async()=>{
      calls += 1;
      return state.websocketCapabilities;
    });

    assert.equal(calls, 2);
    assert.equal(recovered, state.websocketCapabilities);
    assert.equal(state.policy.select(recovered), 'websocket');
  }
});

test('legacy capability result expires or can be force-refreshed after an upgrade', async()=>{
  const state = fixture();
  let calls = 0;
  const legacy = await state.policy.discover(async()=>{
    calls += 1;
    const error = new Error('not found');
    error.status = 404;
    throw error;
  });

  assert.equal(state.policy.select(legacy), 'sse');
  await state.policy.discover(async()=>{
    calls += 1;
    return state.websocketCapabilities;
  });
  assert.equal(calls, 1, 'the explicit legacy result stays cached for one TTL');

  const upgraded = await state.policy.discover(async()=>{
    calls += 1;
    return state.websocketCapabilities;
  }, {force:true});
  assert.equal(calls, 2);
  assert.equal(state.policy.select(upgraded), 'websocket');

  state.advance(300001);
  await state.policy.discover(async()=>{
    calls += 1;
    return state.websocketCapabilities;
  });
  assert.equal(calls, 3, 'successful discovery also expires at the capability TTL');
});

test('silent stable websocket enters cooldown and selects SSE', ()=>{
  const state = fixture();
  const attempt = state.policy.createWebSocketAttempt(state.websocketCapabilities);
  const hello = state.policy.acceptWebSocketEnvelope(attempt, {
    version:1,
    event:'hello',
    sequence:0,
    data:{protocol_version:1},
  });
  assert.equal(hello.event, 'hello');
  assert.equal(attempt.stable, false);

  state.advance(10000);
  const ping = state.policy.acceptWebSocketEnvelope(attempt, {
    version:1,
    event:'ping',
    sequence:0,
    data:{},
  });
  assert.equal(ping.event, 'ping');
  assert.equal(attempt.stable, true);

  const retired = state.policy.retire('websocket', 'stale', attempt);
  assert.equal(retired.failed, true);
  assert.equal(state.policy.select(state.websocketCapabilities), 'sse');

  state.advance(60000);
  assert.equal(state.policy.select(state.websocketCapabilities), 'websocket');
});

test('short-lived and malformed websockets enter cooldown', ()=>{
  const shortLived = fixture();
  const shortAttempt = shortLived.policy.createWebSocketAttempt(shortLived.websocketCapabilities);
  shortLived.policy.acceptWebSocketEnvelope(shortAttempt, {
    version:1,
    event:'hello',
    data:{protocol_version:1},
  });
  assert.equal(shortLived.policy.retire('websocket', 'closed', shortAttempt).failed, true);
  assert.equal(shortLived.policy.select(shortLived.websocketCapabilities), 'sse');

  const malformed = fixture();
  const malformedAttempt = malformed.policy.createWebSocketAttempt(malformed.websocketCapabilities);
  assert.equal(
    malformed.policy.acceptWebSocketEnvelope(malformedAttempt, {version:2, event:'hello'}),
    null,
  );
  assert.equal(malformed.policy.retire('websocket', 'protocol_error', malformedAttempt).failed, true);
});

test('intentional SSE retirement does not block websocket upgrade', ()=>{
  const state = fixture();
  const retired = state.policy.retire('sse', 'upgrade', null);
  assert.equal(retired.failed, false);
  assert.equal(state.policy.select(state.websocketCapabilities), 'websocket');
});

test('an established websocket close remains eligible for websocket reconnect', ()=>{
  const state = fixture();
  const attempt = state.policy.createWebSocketAttempt(state.websocketCapabilities);
  state.policy.acceptWebSocketEnvelope(attempt, {
    version:1,
    event:'hello',
    data:{protocol_version:1},
  });
  state.advance(10000);
  state.policy.acceptWebSocketEnvelope(attempt, {version:1, event:'ping', data:{}});

  assert.equal(state.policy.retire('websocket', 'closed', attempt).failed, false);
  assert.equal(state.policy.select(state.websocketCapabilities), 'websocket');
});

test('application sequence tracking rejects stale state and reports forward gaps', ()=>{
  const tracker = createSequenceTracker();

  assert.deepEqual(tracker.accept('playback', 1), {
    apply:true,
    gap:false,
    lastSequence:1,
  });
  assert.equal(tracker.accept('playback', 1).apply, false);
  assert.equal(tracker.accept('ping', 1).apply, true, 'heartbeat equality is allowed');
  assert.deepEqual(tracker.accept('ping', 3), {
    apply:true,
    gap:true,
    lastSequence:1,
  });
  assert.deepEqual(tracker.accept('status', 3), {
    apply:true,
    gap:true,
    lastSequence:3,
  });
  assert.equal(tracker.accept('queue', 2).apply, false);

  assert.equal(tracker.accept('hello', 0).lastSequence, 0);
  assert.equal(tracker.accept('playback', 1).apply, true);
});
