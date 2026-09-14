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

  assert.equal(state.calls[0].options.headers.get('authorization'), 'Bearer secret');
  assert.equal(state.calls[1].options.headers, undefined);
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
