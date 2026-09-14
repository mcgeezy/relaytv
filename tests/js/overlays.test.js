'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const {createDialogController} = require('../../app/relaytv_app/static/ui/overlays.js');

function element(name) {
  const classes = new Set(name === 'backdrop' ? ['hidden'] : []);
  const listeners = new Map();
  return {
    name,
    isConnected:true,
    classList:{contains:key => classes.has(key), add:key => classes.add(key), remove:key => classes.delete(key)},
    addEventListener:(type, listener) => listeners.set(type, listener),
    removeEventListener:(type, listener) => {
      if (listeners.get(type) === listener) listeners.delete(type);
    },
    hasAttribute:() => false,
    getClientRects:() => [{}],
    focus(){ this.focused = true; document.activeElement = this; },
    listeners,
  };
}

const document = {activeElement:null};

function fixture() {
  const opener = element('opener');
  const first = element('first');
  const last = element('last');
  const backdrop = element('backdrop');
  backdrop.querySelectorAll = () => [first, last];
  const windowListeners = new Map();
  const window = {
    addEventListener:(type, listener) => windowListeners.set(type, listener),
    removeEventListener:(type, listener) => {
      if (windowListeners.get(type) === listener) windowListeners.delete(type);
    },
  };
  let closeRequests = 0;
  const controller = createDialogController({
    document,
    window,
    backdrop,
    opener,
    initialFocus:first,
    onRequestClose:() => { closeRequests += 1; },
  });
  return {backdrop, closeRequests:() => closeRequests, controller, first, last, opener, windowListeners};
}

test('dialog open and close manage initial and return focus', () => {
  const state = fixture();
  state.controller.open();
  assert.equal(state.first.focused, true);
  assert.equal(state.controller.isOpen(), true);

  state.controller.close();
  assert.equal(state.opener.focused, true);
  assert.equal(state.controller.isOpen(), false);
});

test('dialog traps boundary tabs and routes Escape through the owner', () => {
  const state = fixture();
  state.controller.mount();
  state.controller.open();
  let prevented = 0;
  document.activeElement = state.last;
  state.windowListeners.get('keydown')({key:'Tab', shiftKey:false, preventDefault:() => { prevented += 1; }});
  assert.equal(document.activeElement, state.first);

  state.windowListeners.get('keydown')({key:'Escape', preventDefault:() => { prevented += 1; }});
  assert.equal(state.closeRequests(), 1);
  assert.equal(prevented, 2);
});

test('unmount removes dialog listeners', () => {
  const state = fixture();
  state.controller.mount();
  state.controller.unmount();

  assert.equal(state.windowListeners.has('keydown'), false);
  assert.equal(state.backdrop.listeners.has('click'), false);
});
