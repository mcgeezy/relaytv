'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SOURCE = fs.readFileSync(
  path.join(__dirname, '../../app/relaytv_app/static/ui/plex.js'),
  'utf8',
);

function fakeElement(){
  const classes = new Set(['hidden']);
  const listeners = new Map();
  return {
    children: [],
    className: '',
    textContent: '',
    value: '',
    disabled: false,
    dataset: {},
    style: {},
    listeners,
    classList: {
      add: (...names) => names.forEach(name => classes.add(name)),
      remove: (...names) => names.forEach(name => classes.delete(name)),
      toggle: (name, force) => force ? classes.add(name) : classes.delete(name),
      contains: name => classes.has(name),
    },
    append(...children){ this.children.push(...children); },
    appendChild(child){ this.children.push(child); return child; },
    replaceChildren(...children){ this.children = children; },
    replaceWith(){},
    setAttribute(){},
    addEventListener(name, callback){ listeners.set(name, callback); },
    querySelector(){ return null; },
    focus(){},
  };
}

function fixture(){
  let nextTimer = 1;
  const timers = new Map();
  const elements = new Map([
    ['plexOpenBtn', fakeElement()],
    ['plexConnection', fakeElement()],
    ['plexSearchInput', fakeElement()],
    ['plexMoreBtn', fakeElement()],
    ['plexBackBtn', fakeElement()],
    ['plexDetailBackdrop', fakeElement()],
    ['plexDetail', fakeElement()],
  ]);
  const homeTab = fakeElement(); homeTab.dataset.plexView = 'home';
  const librariesTab = fakeElement(); librariesTab.dataset.plexView = 'libraries';
  const context = vm.createContext({
    AbortController,
    URLSearchParams,
    console,
    fetch: async() => ({ok:true, status:200, json:async() => ({})}),
    history: {back(){}},
    requestAnimationFrame: callback => callback(),
    setTimeout(callback){ const id = nextTimer++; timers.set(id, callback); return id; },
    clearTimeout(id){ timers.delete(id); },
    document: {
      activeElement: null,
      body: {classList: fakeElement().classList},
      createElement: fakeElement,
      getElementById: id => elements.get(id) || null,
      querySelectorAll: selector => selector === '.plexTab' ? [homeTab, librariesTab] : [],
    },
    window: {addEventListener(){}},
    __uiNavDepth: 0,
    _uiPushLayer(){},
  });
  vm.runInContext(SOURCE, context, {filename:'plex.js'});
  return {
    context,
    elements,
    homeTab,
    librariesTab,
    timers,
    evaluate: source => vm.runInContext(source, context),
  };
}

test('Plex launch appears only for an enabled linked account with a server', () => {
  const state = fixture();
  state.evaluate("updatePlexStatus({enabled:true, linked:true, server_selected:false})");
  assert.equal(state.elements.get('plexOpenBtn').classList.contains('show'), false);
  assert.match(state.elements.get('plexConnection').textContent, /setup required/i);

  state.evaluate("updatePlexStatus({enabled:true, linked:true, server_selected:true, server:{name:'Living Room'}})");
  assert.equal(state.elements.get('plexOpenBtn').classList.contains('show'), true);
  assert.equal(state.elements.get('plexOpenBtn').disabled, false);
  assert.match(state.elements.get('plexConnection').textContent, /Living Room/);
});

test('Plex cards render metadata and partial progress without HTML injection', () => {
  const state = fixture();
  const result = state.evaluate(`(() => {
    const card = _plexCard({
      id:'opaque', title:'<b>A Movie</b>', subtitle:'Drama', year:2026,
      duration_ms:7200000, progress:25, poster_url:''
    });
    return {
      title:card.children[1].children[0].textContent,
      meta:card.children[1].children[1].textContent,
      progress:card.children[2].children[0].style.width,
    };
  })()`);

  assert.deepEqual(JSON.parse(JSON.stringify(result)), {
    title:'<b>A Movie</b>',
    meta:'Drama · 2026 · 2h 0m',
    progress:'25%',
  });
});

test('switching tabs retires a pending Plex search', () => {
  const state = fixture();
  state.evaluate('refreshPlexStatus = () => {}; loadPlexHome = () => {}; loadPlexLibraries = () => {}; bindPlexUi();');
  state.elements.get('plexSearchInput').listeners.get('input')({target:{value:'stale search'}});
  assert.equal(state.timers.size, 1);

  state.homeTab.listeners.get('click')();
  for (const callback of state.timers.values()) callback();

  assert.equal(state.evaluate('__plexSearchTimer'), 0);
  assert.equal(state.evaluate('__plexQuery'), '');
});

test('playable Plex details offer start, resume, and queue actions', () => {
  const state = fixture();
  const labels = state.evaluate(`(() => {
    _plexRenderDetail({
      id:'opaque', type:'movie', title:'A Movie', summary:'Summary',
      duration_ms:7200000, view_offset_ms:1800000, children_available:false
    });
    const detail = document.getElementById('plexDetail');
    const actions = detail.children[1].children.find(child => child.className === 'plexActions');
    return actions.children.map(child => child.textContent).filter(Boolean);
  })()`);

  assert.deepEqual(JSON.parse(JSON.stringify(labels)), [
    'Play now', 'Resume', 'Play next', 'Add to queue',
  ]);
});
