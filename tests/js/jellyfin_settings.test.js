'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../app/relaytv_app/static/ui/app.js'), 'utf8');

function fixture(){
  const elements = new Map();
  function element(id){
    if (!elements.has(id)) {
      const classes = new Set();
      const attributes = new Map();
      elements.set(id, {
        value: '', checked: false, textContent: '', disabled: false,
        classList: {
          add: (...names) => names.forEach(name => classes.add(name)),
          remove: (...names) => names.forEach(name => classes.delete(name)),
          contains: name => classes.has(name),
          toggle: (name, on) => on ? classes.add(name) : classes.delete(name),
        },
        setAttribute: (name, value) => attributes.set(name, value),
        getAttribute: name => attributes.get(name),
        addEventListener(){},
      });
    }
    return elements.get(id);
  }
  // Use only real IDs from the served settings markup, so removed selectors
  // cannot quietly remain available to the event handlers under test.
  const markup = fs.readFileSync(path.join(__dirname, '../../app/relaytv_app/routes/__init__.py'), 'utf8');
  for (const match of markup.matchAll(/id="((?:set|settings)[^"]+)"/g)) element(match[1]);
  const requests = [];
  const alerts = [];
  const context = vm.createContext({
    document: {getElementById: id => elements.get(id) || null},
    window: {addEventListener(){}},
    openSettings(){}, closeSettings(){}, loadSettingsUi: async() => {},
    syncSeerrRequestModeUi(){}, applyJfBranding(){}, jfBrandName: () => 'Jellyfin',
    SETTINGS_TV_CONTROL_BASELINE: {}, WEATHER_LOCATION_STATE: {},
    collectIdlePanelSettings: () => ({}),
    alert: message => alerts.push(message),
    fetch: async(url, options) => {
      if (options) requests.push({url, body: JSON.parse(options.body)});
      return {ok: true, json: async() => ({})};
    },
  });
  const helpersStart = source.indexOf('function syncJellyfinAuthModeUi()');
  vm.runInContext(source.slice(helpersStart, source.indexOf('async function loadSettingsUi()', helpersStart)), context);
  const bindStart = source.indexOf('function bindSettingsUi()');
  vm.runInContext(source.slice(bindStart, source.indexOf('// Consume the', bindStart)), context);
  vm.runInContext('bindSettingsUi()', context);
  element('setJfEnabled').checked = true;
  element('setJfServerUrl').value = 'http://jf.example';
  element('setJfSharedCastEnabled').checked = true;
  element('setJfApiKey').value = 'cast-key';
  element('setJfUsername').value = 'viewer';
  element('setJfPassword').value = 'password';
  return {element, requests, alerts, context};
}

for (const button of ['setJfApplyBtn', 'settingsSaveBtn']) {
  test(`${button} saves client login alongside the shared cast key`, async() => {
    const f = fixture();
    await f.element(button).onclick();
    assert.equal(f.requests.length, 1);
    assert.equal(f.requests[0].body.jellyfin_auth_mode, 'shared_api_key');
    assert.equal(f.requests[0].body.jellyfin_api_key, 'cast-key');
    assert.equal(f.requests[0].body.jellyfin_username, 'viewer');
    assert.equal(f.requests[0].body.jellyfin_password, 'password');
  });

  test(`${button} preserves blank secrets and switches casting without hiding login`, async() => {
    const f = fixture();
    f.element('setJfPassword').value = '';
    f.element('setJfPasswordState').setAttribute('data-configured', '1');
    f.element('setJfApiKey').value = '';
    f.element('setJfApiKeyState').setAttribute('data-configured', '1');
    for (const shared of [true, false]) {
      f.element('setJfSharedCastEnabled').checked = shared;
      f.element('setJfSharedCastEnabled').onchange();
      assert.equal(f.element('setJfUserAuthFields').classList.contains('hidden'), false);
      assert.equal(f.element('setJfSharedAuthFields').classList.contains('hidden'), false);
      await f.element(button).onclick();
      const {body} = f.requests.at(-1);
      assert.equal(body.jellyfin_auth_mode, shared ? 'shared_api_key' : 'user_login');
      assert.equal(Object.hasOwn(body, 'jellyfin_password'), false);
      assert.equal(Object.hasOwn(body, 'jellyfin_api_key'), false);
    }
    assert.equal(f.requests.length, 2);
  });

  test(`${button} rejects incomplete client login even when casting is configured`, async() => {
    const f = fixture();
    f.element('setJfPassword').value = '';
    await f.element(button).onclick();
    assert.equal(f.requests.length, 0);
    const error = button === 'settingsSaveBtn' ? f.alerts[0] : f.element('setJfApplyResult').textContent;
    assert.match(error, /required for client login/);
  });

  test(`${button} honors explicit clear over a newly typed secret`, async() => {
    const f = fixture();
    f.element('setJfClearApiKey').checked = true;
    await f.element(button).onclick();
    assert.equal(f.requests.length, 0);
    f.element('setJfClearApiKey').checked = false;
    f.element('setJfClearPassword').checked = true;
    await f.element(button).onclick();
    assert.equal(f.requests.length, 0);
    f.element('setJfUsername').value = '';
    await f.element(button).onclick();
    assert.equal(f.requests.length, 1);
    assert.equal(f.requests[0].body.jellyfin_password, '');
    assert.equal(f.requests[0].body.jellyfin_auth_mode, 'shared_api_key');
  });
}
