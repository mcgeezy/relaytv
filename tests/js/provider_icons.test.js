'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const APP_JS = fs.readFileSync(
  path.join(__dirname, '../../app/relaytv_app/static/ui/app.js'),
  'utf8',
);

function fixture(serverType = 'jellyfin'){
  const start = APP_JS.indexOf('function _safeUrlHost(');
  const end = APP_JS.indexOf('function displaySub(', start);
  assert.notEqual(start, -1);
  assert.notEqual(end, -1);
  const context = vm.createContext({
    URL,
    encodeURIComponent,
    __jfServerType: serverType,
  });
  vm.runInContext(APP_JS.slice(start, end), context, {filename: 'app.js'});
  return context;
}

test('popular video hosts use bundled provider icons', () => {
  const context = fixture();
  const cases = [
    ['https://m.youtube.com/watch?v=one', '/pwa/providers/youtube.svg'],
    ['https://youtu.be/two', '/pwa/providers/youtube.svg'],
    ['https://www.rumble.com/video', '/pwa/providers/rumble.svg'],
    ['https://clips.twitch.tv/clip', '/pwa/providers/twitch.svg'],
    ['https://vm.tiktok.com/video', '/pwa/providers/tiktok.svg'],
    ['https://odysee.com/@channel/video', '/pwa/providers/odysee.svg'],
    ['https://player.vimeo.com/video/123', '/pwa/providers/vimeo.svg'],
    ['https://dai.ly/abc', '/pwa/providers/dailymotion.svg'],
    ['https://peertube.tv/w/abc', '/pwa/providers/peertube.svg'],
    ['https://mobile.twitter.com/user/status/1', '/pwa/providers/x.svg'],
    ['https://fb.watch/abc', '/pwa/providers/facebook.svg'],
    ['https://www.instagram.com/reel/abc', '/pwa/providers/instagram.svg'],
    ['https://kick.com/channel', '/pwa/providers/kick.svg'],
  ];
  for (const [url, expected] of cases) {
    assert.equal(vm.runInContext(`faviconUrl(${JSON.stringify(url)})`, context), expected);
  }
});

test('provider metadata supports self-hosted services and aliases', () => {
  const context = fixture();
  assert.equal(
    vm.runInContext("faviconUrl({provider: 'peertube', url: 'https://video.example/w/abc'})", context),
    '/pwa/providers/peertube.svg',
  );
  assert.equal(
    vm.runInContext("faviconUrl({provider: 'twitter', url: 'https://example.invalid/video'})", context),
    '/pwa/providers/x.svg',
  );
});

test('lookalike and unknown hosts stay on the local letter fallback', () => {
  const context = fixture();
  for (const url of ['https://youtube.com.evil.test/video', 'https://evil-youtube.com/video']) {
    const value = vm.runInContext(`faviconUrl(${JSON.stringify(url)})`, context);
    assert.match(value, /^data:image\/svg\+xml;utf8,/);
    assert.equal(value.includes('google.com'), false);
  }
});

test('Jellyfin and Emby continue using their product icons', () => {
  assert.equal(
    vm.runInContext("faviconUrl({provider: 'jellyfin', url: 'https://media.example/video'})", fixture()),
    '/pwa/jellyfin.svg',
  );
  assert.equal(
    vm.runInContext("faviconUrl({provider: 'jellyfin', url: 'https://media.example/video'})", fixture('emby')),
    '/pwa/emby.svg',
  );
});
