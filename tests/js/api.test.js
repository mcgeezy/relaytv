'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const {createApi} = require('../../app/relaytv_app/static/ui/api.js');

class TestHeaders {
  constructor(initial = {}) {
    this.values = new Map();
    if (initial instanceof TestHeaders) {
      for (const [key, value] of initial.values) this.values.set(key, value);
    } else {
      for (const [key, value] of Object.entries(initial)) this.set(key, value);
    }
  }
  get(name) { return this.values.get(String(name).toLowerCase()) || null; }
  set(name, value) { this.values.set(String(name).toLowerCase(), String(value)); }
}

function response(status, challenge = '') {
  return {
    ok:status >= 200 && status < 300,
    status,
    headers:{get:name => String(name).toLowerCase() === 'www-authenticate' ? challenge : null},
  };
}

function fixture({token = '', prompt = () => null, responder = () => response(200)} = {}) {
  const values = new Map(token ? [['relaytv_api_token', token]] : []);
  const calls = [];
  const window = {
    location:{href:'http://relaytv.local/ui', origin:'http://relaytv.local'},
    prompt,
    fetch:null,
  };
  const api = createApi({
    window,
    Headers:TestHeaders,
    storage:{
      getItem:key => values.get(key) || null,
      setItem:(key, value) => values.set(key, value),
      removeItem:key => values.delete(key),
    },
    fetch:async(input, options = {}) => {
      calls.push({input, options});
      return responder(calls.length);
    },
    AbortController:null,
    setTimeout,
    clearTimeout,
  });
  api.installAuthFetch();
  return {api, calls, values, window};
}

test('stored API token is attached only to same-origin requests', async() => {
  const state = fixture({token:'secret'});
  await state.window.fetch('/status');
  await state.window.fetch('https://example.invalid/status');
  await state.window.fetch(new URL('http://relaytv.local/playback/state'));
  await state.window.fetch(new URL('https://attacker.example/steal'));

  assert.equal(state.calls[0].options.headers.get('authorization'), 'Bearer secret');
  assert.equal(state.calls[1].options.headers, undefined);
  assert.equal(state.calls[2].options.headers.get('authorization'), 'Bearer secret');
  assert.equal(state.calls[3].options.headers, undefined);
});

test('a Bearer challenge retries once after explicit token entry', async() => {
  let prompts = 0;
  const state = fixture({
    prompt:() => { prompts += 1; return ' replacement '; },
    responder:attempt => attempt === 1 ? response(401, 'Bearer realm="relaytv"') : response(200),
  });
  const result = await state.window.fetch('/pause', {method:'POST'});

  assert.equal(result.status, 200);
  assert.equal(state.calls.length, 2);
  assert.equal(state.calls[1].options.headers.get('authorization'), 'Bearer replacement');
  assert.equal(state.values.get('relaytv_api_token'), 'replacement');
  assert.equal(prompts, 1);
});

test('canceling token entry leaves the challenged write rejected and never replays it', async() => {
  let prompts = 0;
  const state = fixture({
    prompt:() => { prompts += 1; return null; },
    responder:() => response(401, 'Bearer realm="relaytv"'),
  });
  const first = await state.window.fetch('/next', {method:'POST'});
  const second = await state.window.fetch('/next', {method:'POST'});

  assert.equal(first.status, 401);
  assert.equal(second.status, 401);
  assert.equal(state.calls.length, 2);
  assert.equal(prompts, 1);
});

test('a non-Bearer rejection never opens token recovery', async() => {
  let prompts = 0;
  const state = fixture({
    prompt:() => { prompts += 1; return 'unused'; },
    responder:() => response(401, 'Basic realm="other"'),
  });
  await state.window.fetch('/pause', {method:'POST'});

  assert.equal(state.calls.length, 1);
  assert.equal(prompts, 0);
});

test('custom async token prompt replaces window.prompt and replays once on submit', async() => {
  let nativePrompts = 0;
  let dialogCalls = 0;
  const state = fixture({
    prompt:() => { nativePrompts += 1; return 'should-not-be-used'; },
    responder:attempt => attempt === 1 ? response(401, 'Bearer realm="relaytv"') : response(200),
  });
  state.api.setTokenPrompt(async ({hasStoredToken, reason}) => {
    dialogCalls += 1;
    assert.equal(hasStoredToken, false);
    assert.equal(reason, 'challenge');
    return {action:'submit', token:'dialog-secret'};
  });

  const result = await state.window.fetch('/pause', {method:'POST'});
  assert.equal(result.status, 200);
  assert.equal(nativePrompts, 0);
  assert.equal(dialogCalls, 1);
  assert.equal(state.calls.length, 2);
  assert.equal(state.calls[1].options.headers.get('authorization'), 'Bearer dialog-secret');
  assert.equal(state.values.get('relaytv_api_token'), 'dialog-secret');
});

test('custom async token prompt cancel and forgetToken do not replay challenged writes', async() => {
  let dialogCalls = 0;
  const state = fixture({
    token:'stale-secret',
    responder:() => response(401, 'Bearer realm="relaytv"'),
  });
  state.api.setTokenPrompt(async ({hasStoredToken}) => {
    dialogCalls += 1;
    assert.equal(hasStoredToken, true);
    return {action:'forget', token:''};
  });

  const first = await state.window.fetch('/next', {method:'POST'});
  assert.equal(first.status, 401);
  assert.equal(dialogCalls, 1);
  assert.equal(state.calls.length, 1);
  assert.equal(state.values.get('relaytv_api_token'), undefined);

  state.api.setTokenPrompt(async () => {
    dialogCalls += 1;
    return {action:'cancel', token:''};
  });
  const second = await state.window.fetch('/next', {method:'POST'});
  assert.equal(second.status, 401);
  assert.equal(dialogCalls, 2);
  assert.equal(state.calls.length, 2);
});

test('forgetToken clears stored credentials and allows a future Bearer challenge prompt', async() => {
  let dialogCalls = 0;
  const state = fixture({
    token:'old-secret',
    responder:attempt => attempt === 1 ? response(401, 'Bearer realm="relaytv"') : response(200),
  });
  state.api.setTokenPrompt(async () => {
    dialogCalls += 1;
    return {action:'cancel', token:''};
  });
  await state.window.fetch('/pause', {method:'POST'});
  assert.equal(dialogCalls, 1);

  state.api.forgetToken();
  assert.equal(state.api.storedToken(), '');

  state.api.setTokenPrompt(async () => {
    dialogCalls += 1;
    return {action:'submit', token:'fresh-secret'};
  });
  // Force next call to challenge once then succeed
  let step = 0;
  const state2 = fixture({
    token:'old-secret',
    responder:() => {
      step += 1;
      return step === 1 || step === 2 ? response(401, 'Bearer realm="relaytv"') : response(200);
    },
  });
  let count2 = 0;
  state2.api.setTokenPrompt(async () => {
    count2 += 1;
    return count2 === 1 ? {action:'cancel', token:''} : {action:'submit', token:'fresh-secret'};
  });
  const r1 = await state2.window.fetch('/pause', {method:'POST'});
  assert.equal(r1.status, 401);
  state2.api.forgetToken();
  const r2 = await state2.window.fetch('/pause', {method:'POST'});
  assert.equal(r2.status, 200);
  assert.equal(count2, 2);
  assert.equal(state2.api.storedToken(), 'fresh-secret');
});

test('concurrent Bearer challenges coalesce onto one prompt and retry both requests', async() => {
  let dialogCalls = 0;
  let releasePrompt = null;
  let attempts = 0;
  const state = fixture({
    responder:() => {
      attempts += 1;
      return attempts <= 2 ? response(401, 'Bearer realm="relaytv"') : response(200);
    },
  });
  state.api.setTokenPrompt(() => {
    dialogCalls += 1;
    return new Promise((resolve) => {
      releasePrompt = () => resolve({action:'submit', token:'shared-secret'});
    });
  });

  const p1 = state.window.fetch('/peers/a/probe', {method:'POST'});
  const p2 = state.window.fetch('/peers/b/probe', {method:'POST'});
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(dialogCalls, 1);
  releasePrompt();

  const [r1, r2] = await Promise.all([p1, p2]);
  assert.equal(r1.status, 200);
  assert.equal(r2.status, 200);
  assert.equal(dialogCalls, 1);
  assert.equal(state.calls.length, 4);
  assert.equal(state.calls[2].options.headers.get('authorization'), 'Bearer shared-secret');
  assert.equal(state.calls[3].options.headers.get('authorization'), 'Bearer shared-secret');
});

test('storing a replacement token resets prompt suppression for future challenges', async() => {
  let dialogCalls = 0;
  let step = 0;
  const state = fixture({
    responder:() => {
      step += 1;
      return step <= 2 ? response(401, 'Bearer realm="relaytv"') : response(200);
    },
  });
  state.api.setTokenPrompt(async () => {
    dialogCalls += 1;
    return dialogCalls === 1 ? {action:'cancel', token:''} : {action:'submit', token:'good-token'};
  });

  const first = await state.window.fetch('/pause', {method:'POST'});
  assert.equal(first.status, 401);
  assert.equal(dialogCalls, 1);

  state.api.storeToken('typo-token');
  const second = await state.window.fetch('/pause', {method:'POST'});
  assert.equal(second.status, 200);
  assert.equal(dialogCalls, 2);
  assert.equal(state.api.storedToken(), 'good-token');
});
