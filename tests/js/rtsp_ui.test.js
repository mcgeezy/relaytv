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

function extractFunction(name){
  for (const prefix of [`async function ${name}(`, `function ${name}(`]) {
    const start = APP_JS.indexOf(prefix);
    if (start === -1) continue;
    let depth = 0;
    let seenBody = false;
    for (let i = start; i < APP_JS.length; i++) {
      const ch = APP_JS[i];
      if (ch === '{') {
        depth++;
        seenBody = true;
      } else if (ch === '}') {
        depth--;
        if (seenBody && depth === 0) return APP_JS.slice(start, i + 1);
      }
    }
  }
  throw new Error(`${name} not found in app.js`);
}

function urlHelpersFixture(includeUrlCtor = true){
  const globals = {console};
  if (includeUrlCtor) globals.URL = URL;
  const context = vm.createContext(globals);
  for (const fn of ['looksLikeUrl', 'normalizeUrl', '_isNowPlayingLive']) {
    vm.runInContext(extractFunction(fn), context, {filename: 'app.js'});
  }
  return context;
}

test('looksLikeUrl accepts http(s), rtsp(s), and www URLs case-insensitively', () => {
  for (const includeUrlCtor of [true, false]) {
    const ctx = urlHelpersFixture(includeUrlCtor);
    const valid = [
      'http://192.168.1.20:8080/video.mp4',
      'https://youtu.be/abc123',
      'HTTPS://example.com/live',
      'rtsp://192.168.1.50:554/stream1',
      'rtsps://nvr.lan:7441/abc?enable_srtp',
      'RTSP://admin:secret@192.168.1.50:554/h264Preview_01_main',
      'RTSPS://cam.example.com/front_door',
      '  rtsp://go2rtc.lan:8554/driveway  ',
      'rtsp://[fd00::10]:554/stream1',
      'www.youtube.com/watch?v=abc123',
      'WWW.EXAMPLE.COM/stream',
    ];
    for (const candidate of valid) {
      assert.equal(ctx.looksLikeUrl(candidate), true, `${candidate} (URL=${includeUrlCtor})`);
    }
  }
});

test('looksLikeUrl rejects unsupported schemes and hostless URLs', () => {
  for (const includeUrlCtor of [true, false]) {
    const ctx = urlHelpersFixture(includeUrlCtor);
    const invalid = [
      '',
      '   ',
      null,
      undefined,
      'file:///etc/passwd',
      'javascript:alert(1)',
      'rtmp://live.example.com/app/stream',
      'rtspx://192.168.1.50/live',
      'ftp://files.example.com/clip.mp4',
      'data:text/html,<script>alert(1)</script>',
      'av://lavfi:testsrc',
      'edl://foo',
      'http://',
      'https://',
      'rtsp://',
      'rtsps://',
      'rtsp:///stream1',
      'www.',
      'rtsp://cam host/stream1',
    ];
    for (const candidate of invalid) {
      assert.equal(ctx.looksLikeUrl(candidate), false, `${String(candidate)} (URL=${includeUrlCtor})`);
    }
  }
});

test('normalizeUrl normalizes rtsp(s) and http(s) schemes while preserving path and credential case', () => {
  const ctx = urlHelpersFixture();
  assert.equal(
    ctx.normalizeUrl('  RTSP://admin:SecretPass@Cam.Local:554/h264Preview_01_Main  '),
    'rtsp://admin:SecretPass@Cam.Local:554/h264Preview_01_Main',
  );
  assert.equal(
    ctx.normalizeUrl('RTSPS://nvr.lan:7441/AbCdEf?enable_srtp'),
    'rtsps://nvr.lan:7441/AbCdEf?enable_srtp',
  );
  assert.equal(
    ctx.normalizeUrl('HTTPS://youtu.be/dQw4w9WgXcQ'),
    'https://youtu.be/dQw4w9WgXcQ',
  );
  assert.equal(
    ctx.normalizeUrl('www.example.com/PathCase'),
    'https://www.example.com/PathCase',
  );
  assert.equal(ctx.normalizeUrl('   '), '');
});

test('_isNowPlayingLive treats rtsp and iptv providers as live streams', () => {
  const ctx = urlHelpersFixture();
  assert.equal(ctx._isNowPlayingLive({provider: 'rtsp'}), true);
  assert.equal(ctx._isNowPlayingLive({provider: 'RTSP'}), true);
  assert.equal(ctx._isNowPlayingLive({provider: 'iptv'}), true);
  assert.equal(ctx._isNowPlayingLive({provider: 'youtube', is_live: true}), true);
  assert.equal(ctx._isNowPlayingLive({provider: 'youtube', is_live: false}), false);
  assert.equal(ctx._isNowPlayingLive({provider: 'jellyfin'}), false);
  assert.equal(ctx._isNowPlayingLive(null), false);
});

test('submitAddUrl accepts rtsp(s) links and mentions http(s):// and rtsp(s):// on invalid input', async() => {
  const alerts = [];
  const helpers = [];
  const posts = [];
  let closed = 0;
  let focused = 0;
  const input = {
    value: '',
    focus(){ focused++; },
  };
  const context = vm.createContext({
    URL,
    addUrlSubmitting: false,
    document: {
      getElementById: id => (id === 'addUrlInput' ? input : null),
    },
    window: {
      RelayTVPlaylistQueue: {linkInfo: () => null},
    },
    alert: msg => alerts.push(msg),
    _setAddHelper: (msg, kind) => helpers.push({msg, kind}),
    post: async(url, body) => {
      posts.push({url, body});
      return {ok: true};
    },
    closeAddUrl(){ closed++; },
  });
  for (const fn of ['looksLikeUrl', 'normalizeUrl', 'submitAddUrl']) {
    vm.runInContext(extractFunction(fn), context, {filename: 'app.js'});
  }

  input.value = 'rtmp://live.example.com/app/key';
  await vm.runInContext("submitAddUrl('play')", context);
  assert.equal(posts.length, 0);
  assert.equal(focused, 1);
  assert.equal(alerts.length, 1);
  assert.match(alerts[0], /http\(s\):\/\//);
  assert.match(alerts[0], /rtsp\(s\):\/\//);
  assert.equal(helpers.length, 1);
  assert.equal(helpers[0].kind, 'err');
  assert.match(helpers[0].msg, /rtsp\(s\):\/\//);

  input.value = 'RTSP://go2rtc.lan:8554/front_door';
  await vm.runInContext("submitAddUrl('play')", context);
  assert.equal(posts.length, 1);
  assert.equal(posts[0].url, '/play_now');
  assert.equal(posts[0].body.url, 'rtsp://go2rtc.lan:8554/front_door');
  assert.equal(closed, 1);

  input.value = 'rtsps://192.168.1.50:7441/driveway';
  await vm.runInContext("submitAddUrl('queue')", context);
  assert.equal(posts.length, 2);
  assert.equal(posts[1].url, '/enqueue');
  assert.equal(posts[1].body.url, 'rtsps://192.168.1.50:7441/driveway');
  assert.equal(closed, 2);
});
