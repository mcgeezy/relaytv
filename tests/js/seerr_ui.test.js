'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SOURCE = fs.readFileSync(
  path.join(__dirname, '../../app/relaytv_app/static/ui/seerr.js'),
  'utf8',
);

function fakeElement(){
  const classes = new Set();
  return {
    children: [],
    className: '',
    textContent: '',
    value: '',
    disabled: false,
    classList: {
      add: (...names) => names.forEach(name => classes.add(name)),
      remove: (...names) => names.forEach(name => classes.delete(name)),
      toggle: (name, force) => force ? classes.add(name) : classes.delete(name),
      contains: name => classes.has(name),
    },
    append(...children){ this.children.push(...children); },
    appendChild(child){ this.children.push(child); return child; },
    replaceChildren(...children){ this.children = children; },
    setAttribute(){},
    focus(){},
    querySelectorAll(){ return []; },
  };
}

function fixture(){
  let nextTimer = 1;
  const timers = new Map();
  const elements = new Map([
    ['seerrSearchInput', fakeElement()],
    ['seerrRequestFilter', fakeElement()],
    ['seerrGrid', fakeElement()],
    ['seerrMoreBtn', fakeElement()],
    ['seerrBrowseStatus', fakeElement()],
    ['seerrConnection', fakeElement()],
  ]);
  const context = vm.createContext({
    AbortController,
    URL,
    console,
    fetch: async() => ({ok:true, json:async() => ({})}),
    history: {back(){}},
    requestAnimationFrame: callback => callback(),
    setInterval: () => 1,
    clearInterval(){},
    setTimeout(callback){
      const id = nextTimer++;
      timers.set(id, callback);
      return id;
    },
    clearTimeout(id){ timers.delete(id); },
    document: {
      activeElement: null,
      body: {classList: fakeElement().classList},
      createElement: fakeElement,
      getElementById: id => elements.get(id) || null,
      querySelectorAll: () => [],
      addEventListener(){},
    },
    window: {addEventListener(){}},
    __uiNavDepth: 0,
    _uiPushLayer(){},
  });
  vm.runInContext(SOURCE, context, {filename:'seerr.js'});
  const realLoadSeerrBrowse = vm.runInContext('loadSeerrBrowse', context);
  vm.runInContext('loadSeerrBrowse = () => {};', context);
  return {
    context,
    elements,
    realLoadSeerrBrowse,
    timers,
    evaluate: source => vm.runInContext(source, context),
  };
}

test('selecting a section retires the pending search debounce', () => {
  const state = fixture();
  state.evaluate(`
    __seerrSearchTimer = setTimeout(() => { __seerrQuery = 'stale'; }, 350);
    _seerrSelectSection('movies');
  `);

  for(const callback of state.timers.values()) callback();

  assert.equal(state.evaluate('__seerrQuery'), '');
  assert.equal(state.evaluate('__seerrSection'), 'movies');
  assert.equal(state.evaluate('__seerrSearchTimer'), 0);
});

test('active movie requests show state while failed requests remain retryable', () => {
  const state = fixture();
  state.evaluate("__seerrRequestMode = 'shared_admin';");
  const activeCases = [
    [{request:{status:'pending'}, media_status:'pending'}, 'Request pending in Seerr'],
    [{request:{status:'approved'}, media_status:'processing'}, 'Request approved in Seerr'],
    [{request:{status:'completed'}, media_status:'available'}, 'Request completed in Seerr'],
    [{request:null, media_status:'processing'}, 'Request processing in Seerr'],
    [{request:null, media_status:'available'}, 'Available in Seerr'],
  ];
  for(const [item, label] of activeCases){
    const controls = state.evaluate(`_seerrRequestControls(${JSON.stringify({
      media_type:'movie',
      playback_available:false,
      ...item,
    })})`);
    assert.equal(controls.children[0].className, 'seerrExistingRequest');
    assert.equal(controls.children[0].textContent, label);
    assert.equal(
      controls.children.some(child => child.textContent === 'Request movie'),
      false,
    );
  }

  for(const requestStatus of ['failed', 'declined']){
    const controls = state.evaluate(`_seerrRequestControls(${JSON.stringify({
      media_type:'movie',
      media_status:'unknown',
      playback_available:false,
      request:{status:requestStatus},
    })})`);
    assert.equal(
      controls.children.some(child => child.textContent === 'Request movie'),
      true,
    );
  }
});

test('request-user selector preserves a configured user missing from inventory', () => {
  const state = fixture();
  const result = state.evaluate(`(() => {
    const select = document.createElement('select');
    _seerrPopulateRequestUsers(select, {
      users: [{id: 2, display_name: 'Available User', username: 'available'}],
    }, 7);
    return {
      value: select.value,
      options: select.children.map(option => ({
        value: option.value,
        textContent: option.textContent,
      })),
    };
  })()`);

  assert.equal(result.value, '7');
  assert.deepEqual(JSON.parse(JSON.stringify(result.options)), [
    {value: '', textContent: 'API identity (default)'},
    {value: '2', textContent: 'Available User (available)'},
    {value: '7', textContent: 'Configured user #7 (unavailable)'},
  ]);
});

test('request-user selector survives lookup failure and permits an intentional clear', () => {
  const state = fixture();
  const result = state.evaluate(`(() => {
    const select = document.createElement('select');
    _seerrPopulateRequestUsers(select, null, 7);
    const retainedValue = select.value;
    select.value = '';
    return {
      retainedValue,
      clearedValue: select.value,
      options: select.children.map(option => option.value),
    };
  })()`);

  assert.equal(result.retainedValue, '7');
  assert.equal(result.clearedValue, '');
  assert.deepEqual(result.options, ['', '7']);
});

test('multi-word search distinguishes upstream failure from zero matches', async() => {
  const state = fixture();
  const urls = [];
  state.context.loadSeerrBrowse = state.realLoadSeerrBrowse;
  state.evaluate('__seerrVisible = true; __seerrQuery = "the matrix";');

  state.context.fetch = async url => {
    urls.push(url);
    return {
      ok: false,
      status: 502,
      json: async() => ({detail: {message: 'Seerr returned an upstream error'}}),
    };
  };
  await state.realLoadSeerrBrowse({append: false});
  assert.equal(urls[0], '/seerr/search?query=the%20matrix&page=1');
  assert.equal(
    state.elements.get('seerrGrid').children[0].textContent,
    'Search could not be completed.',
  );
  assert.equal(
    state.elements.get('seerrBrowseStatus').textContent,
    'Seerr returned an upstream error',
  );

  state.context.fetch = async url => {
    urls.push(url);
    return {
      ok: true,
      status: 200,
      json: async() => ({page: 1, total_pages: 1, total_results: 0, results: []}),
    };
  };
  await state.realLoadSeerrBrowse({append: false});
  assert.equal(
    state.elements.get('seerrGrid').children[0].textContent,
    'No matching movies or series.',
  );
  assert.equal(state.elements.get('seerrBrowseStatus').textContent, '0 results');
});

test('updateSeerrStatus hides Connected status badge unless caller_session is active', () => {
  const state = fixture();
  const connection = state.elements.get('seerrConnection');

  state.evaluate(`updateSeerrStatus({
    enabled: true,
    configured: true,
    reachable: true,
    version: '2.5.2',
    request_mode: 'shared_admin',
  })`);
  assert.equal(connection.classList.contains('hidden'), true);
  assert.equal(connection.textContent, '');

  state.evaluate(`updateSeerrStatus({
    enabled: true,
    configured: true,
    reachable: true,
    request_mode: 'caller_session',
    caller_connected: false,
  })`);
  assert.equal(connection.classList.contains('hidden'), false);
  assert.equal(connection.textContent, 'Connect account');
});
