'use strict';

// GET /share no longer plays the shared link; it redirects into /ui with the
// link in a query parameter, and this is the code that picks it up. The point
// of the redirect is that the side effect moves onto an authenticated JSON
// POST a cross-origin page cannot forge, so these tests care about two things:
// the link reaches the modal, and nothing is submitted without the user.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const APP_JS = fs.readFileSync(
  path.join(__dirname, '../../app/relaytv_app/static/ui/app.js'),
  'utf8',
);

// app.js is a single large script with a lot of ambient DOM state, so pull out
// the one function under test rather than evaluating the whole file.
function extractFunction(name){
  const start = APP_JS.indexOf(`async function ${name}(`);
  assert.notEqual(start, -1, `${name} not found in app.js`);
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
  throw new Error(`unbalanced braces extracting ${name}`);
}

function fixture(search){
  const calls = {opened: 0, replaced: []};
  const input = {value: ''};
  const context = vm.createContext({
    URL,
    URLSearchParams,
    console,
    document: {
      getElementById: id => (id === 'addUrlInput' ? input : null),
    },
    window: {location: {search, href: `http://tv.local/ui${search}`, hash: ''}},
    history: {
      state: {relaytv_root: 1},
      replaceState(state, _title, url){ calls.replaced.push(url); },
    },
    normalizeUrl: value => String(value || '').trim(),
    looksLikeUrl: value => /^https?:\/\//.test(String(value || '')),
    openAddUrl: async() => { calls.opened++; },
  });
  vm.runInContext(extractFunction('consumeShareParam'), context, {filename:'app.js'});
  return {
    context,
    calls,
    input,
    run: () => vm.runInContext('consumeShareParam()', context),
  };
}

test('a shared link opens the modal prefilled instead of playing', async() => {
  const state = fixture('?share=https%3A%2F%2Fyoutu.be%2Fabc123');
  const handled = await state.run();

  assert.equal(handled, true);
  assert.equal(state.calls.opened, 1);
  assert.equal(state.input.value, 'https://youtu.be/abc123');
});

test('the share parameter is stripped so a reload does not re-open it', async() => {
  const state = fixture('?share=https%3A%2F%2Fyoutu.be%2Fabc123');
  await state.run();

  assert.equal(state.calls.replaced.length, 1);
  assert.ok(!state.calls.replaced[0].includes('share='), state.calls.replaced[0]);
});

test('unrelated query parameters survive the strip', async() => {
  const state = fixture('?tab=queue&share=https%3A%2F%2Fyoutu.be%2Fabc123');
  await state.run();

  assert.ok(state.calls.replaced[0].includes('tab=queue'), state.calls.replaced[0]);
  assert.ok(!state.calls.replaced[0].includes('share='), state.calls.replaced[0]);
});

test('no share parameter is a no-op', async() => {
  const state = fixture('?tab=queue');
  const handled = await state.run();

  assert.equal(handled, false);
  assert.equal(state.calls.opened, 0);
  assert.equal(state.calls.replaced.length, 0);
});

test('a non-URL share value is dropped rather than prefilled', async() => {
  const state = fixture('?share=javascript%3Aalert(1)');
  const handled = await state.run();

  assert.equal(handled, false);
  assert.equal(state.calls.opened, 0);
  // Still stripped, so it cannot survive into a later navigation.
  assert.equal(state.calls.replaced.length, 1);
});
