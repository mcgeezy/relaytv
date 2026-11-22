'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const {
  createThemeController,
} = require('../../app/relaytv_app/static/ui/theme.js');

function button(mode) {
  const classes = new Set();
  const attrs = new Map();
  return {
    dataset:{themeMode:mode},
    classList:{
      toggle(name, enabled) {
        if (enabled) classes.add(name);
        else classes.delete(name);
      },
      contains(name) { return classes.has(name); },
    },
    setAttribute(name, value) { attrs.set(name, value); },
    getAttribute(name) { return attrs.get(name); },
    onclick:null,
  };
}

function fixture(options = {}) {
  const stored = new Map(Object.entries(options.storage || {}));
  const buttons = ['auto', 'dark', 'light'].map(button);
  const meta = {
    content:'',
    setAttribute(name, value) { if (name === 'content') this.content = value; },
  };
  const media = options.media || [
    {mediaText:'(prefers-color-scheme: light)'},
    {mediaText:'screen and (prefers-color-scheme: dark)'},
  ];
  const listeners = {};
  const query = {
    matches:!!options.systemLight,
    addEventListener(name, callback) { listeners[name] = callback; },
  };
  const document = {
    styleSheets:[{cssRules:media.map((value) => ({media:value}))}],
    documentElement:{dataset:{}, style:{}},
    querySelector(selector) { return selector === 'meta[name="theme-color"]' ? meta : null; },
    querySelectorAll(selector) { return selector === '.mtBtn' ? buttons : []; },
    addEventListener() {},
  };
  const window = {
    matchMedia() { return query; },
    addEventListener() {},
  };
  const storage = {
    getItem(key) { return stored.get(key) || null; },
    setItem(key, value) { stored.set(key, value); },
  };
  return {
    controller:createThemeController({window, document, storage}),
    buttons,
    document,
    listeners,
    media,
    meta,
    query,
    stored,
  };
}

test('manual theme selection persists and updates product state', () => {
  const value = fixture();
  value.controller.bind();
  value.buttons[2].onclick();

  assert.equal(value.stored.get('relaytv_theme'), 'light');
  assert.equal(value.document.documentElement.dataset.theme, 'light');
  assert.equal(value.document.documentElement.style.colorScheme, 'light');
  assert.equal(value.meta.content, '#f4f6f9');
  assert.equal(value.buttons[2].getAttribute('aria-checked'), 'true');
  assert.equal(value.buttons[0].getAttribute('aria-checked'), 'false');
});

test('manual mode rewrites legacy theme media and auto restores it', () => {
  const value = fixture();
  value.controller.apply('dark');

  assert.equal(value.media[0].mediaText, '(min-width: 99999px)');
  assert.equal(value.media[1].mediaText, 'screen and (min-width: 0px)');

  value.controller.apply('auto');
  assert.equal(value.media[0].mediaText, '(prefers-color-scheme: light)');
  assert.equal(value.media[1].mediaText, 'screen and (prefers-color-scheme: dark)');
});

test('auto theme follows a system color-scheme change', () => {
  const value = fixture({systemLight:false});
  value.controller.bind();
  value.controller.apply('auto');
  assert.equal(value.meta.content, '#0b1017');

  value.query.matches = true;
  value.listeners.change();
  assert.equal(value.meta.content, '#f4f6f9');
  assert.equal(value.document.documentElement.dataset.theme, 'auto');
});

test('an unreadable stylesheet does not prevent the remaining sheets from updating', () => {
  const value = fixture();
  value.document.styleSheets.unshift({
    get cssRules() { throw new Error('cross-origin stylesheet'); },
  });

  assert.doesNotThrow(() => value.controller.apply('light'));
  assert.equal(value.media[0].mediaText, '(min-width: 0px)');
  assert.equal(value.media[1].mediaText, 'screen and (min-width: 99999px)');
});
