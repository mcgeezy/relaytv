'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(
  path.join(__dirname, '../../app/relaytv_app/static/ui/app.js'),
  'utf8',
);

function fixture(){
  const elements = new Map();
  function element(id){
    if (!elements.has(id)) {
      const classes = new Set();
      const attributes = new Map();
      elements.set(id, {
        value: '', checked: false, textContent: '', disabled: false, href: '#',
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
  const markup = fs.readFileSync(
    path.join(__dirname, '../../app/relaytv_app/routes/__init__.py'),
    'utf8',
  );
  for (const match of markup.matchAll(/id="((?:set|settings)[^"]+)"/g)) element(match[1]);
  const requests = [];
  const responses = new Map([
    ['/integrations/plex/auth/start', {flow_id:'flow-1', link_url:'https://app.plex.tv/auth#?code=abc'}],
    ['/integrations/plex/auth/poll', {linked:true}],
    ['/integrations/plex/auth/cancel', {cancelled:true}],
    ['/integrations/plex/disconnect', {linked:false}],
    ['/integrations/plex/server', {server:{machine_id:'server-1'}}],
    ['/integrations/plex/test', {reachable:true, version:'1.43.3'}],
    ['/settings', {ok:true}],
  ]);
  const context = vm.createContext({
    document: {getElementById: id => elements.get(id) || null},
    window: {addEventListener(){}, relaytvSeerr:null},
    openSettings(){}, closeSettings(){}, loadSettingsUi: async() => {},
    syncJellyfinAuthModeUi(){}, syncSeerrRequestModeUi(){},
    SETTINGS_TV_CONTROL_BASELINE: {}, WEATHER_LOCATION_STATE: {},
    collectIdlePanelSettings: () => ({}),
    alert(){},
    fetch: async(url, options={}) => {
      requests.push({url, body: options.body ? JSON.parse(options.body) : null});
      const body = responses.get(url) || {};
      return {ok:true, status:200, json:async() => body};
    },
  });
  const helperStart = source.indexOf('let __plexLinkFlowId');
  vm.runInContext(
    source.slice(helperStart, source.indexOf('async function loadSettingsUi()', helperStart)),
    context,
  );
  const bindStart = source.indexOf('function bindSettingsUi()');
  vm.runInContext(source.slice(bindStart, source.indexOf('// Consume the', bindStart)), context);
  vm.runInContext('bindSettingsUi()', context);
  return {element, requests, context};
}

test('Plex link start exposes the official authorization URL and polls its flow', async() => {
  const f = fixture();

  await f.element('setPlexLinkBtn').onclick();
  await f.element('setPlexPollBtn').onclick();

  assert.equal(f.element('setPlexLinkUrl').href, '#');
  assert.equal(f.requests[0].url, '/integrations/plex/auth/start');
  assert.deepEqual(f.requests[1], {
    url:'/integrations/plex/auth/poll',
    body:{flow_id:'flow-1'},
  });
  assert.match(f.element('setPlexApplyResult').textContent, /account linked/i);
});

test('Apply Plex saves the enable switch and selected server separately', async() => {
  const f = fixture();
  f.element('setPlexEnabled').checked = true;
  f.element('setPlexServer').value = 'server-1';
  f.element('setPlexPlaybackMode').value = 'transcode';
  f.element('setPlexMaxBitrate').value = '8000';

  const result = await f.element('setPlexApplyBtn').onclick();

  assert.equal(result, true);
  assert.deepEqual(f.requests, [
    {url:'/settings', body:{plex_enabled:true, plex_playback_mode:'transcode', plex_max_bitrate:8000, apply_now:true}},
    {url:'/integrations/plex/server', body:{machine_id:'server-1'}},
  ]);
  assert.match(f.element('setPlexApplyResult').textContent, /settings applied/i);
});
