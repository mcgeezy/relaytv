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
  vm.runInContext('loadSeerrBrowse = () => {};', context);
  return {
    context,
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
