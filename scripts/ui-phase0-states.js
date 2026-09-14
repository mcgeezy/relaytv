#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { chromium, firefox, webkit } = require('playwright');

function option(name, fallback) {
  const prefix = `--${name}=`;
  const value = process.argv.find((item) => item.startsWith(prefix));
  return value ? value.slice(prefix.length) : fallback;
}

function media(id, title, overrides) {
  return Object.assign({
    item_id: id,
    id,
    title,
    subtitle: 'Fixture metadata · 2026',
    type: 'Movie',
    year: 2026,
    runtime_sec: 5820,
    duration_ms: 5820000,
    resume_pos: 1560,
    progress: 27,
    progress_percent: 27,
    thumbnail: '',
    poster: '',
    poster_url: '',
    overview: 'Deterministic fixture copy used to preserve the current layout without exposing a private library.',
    audio_streams: [],
    subtitle_streams: [],
  }, overrides || {});
}

function playbackStatus(overrides) {
  const queue = [
    {
      queue_id: 'queue-fixture-one',
      provider: 'youtube',
      url: 'https://www.youtube.com/watch?v=fixture-one',
      title: 'A deliberately long queued title that needs room for useful metadata',
      channel: 'Fixture channel',
      duration_sec: 1880,
    },
    {
      queue_id: 'queue-fixture-two',
      provider: 'upload',
      url: '/uploads/fixture-two',
      title: 'Uploaded family video',
      mime_type: 'video/mp4',
      size_bytes: 41943040,
      duration_sec: 420,
    },
    {
      queue_id: 'queue-fixture-three',
      provider: 'other',
      url: 'https://video.example.test/watch/three',
      title: 'Unavailable fixture item',
      channel: 'video.example.test',
      available: false,
    },
  ];
  return Object.assign({
    state: 'playing',
    device_name: 'Fixture room',
    playing: true,
    paused: false,
    has_now_playing: true,
    position: 1420,
    duration: 3600,
    volume: 72,
    mute: false,
    now_playing: {
      provider: 'jellyfin',
      url: 'https://media.example.test/Videos/fixture/stream',
      title: 'Current fixture episode',
      channel: 'Season 2 · Episode 4',
      duration_sec: 3600,
      resume_pos: 1420,
      jellyfin_item_id: 'now-fixture',
      jellyfin_audio_language: 'eng',
      jellyfin_subtitle_language: 'spa',
      jellyfin_subtitle_stream_index: 2,
      audio_streams: [{index: 1}, {index: 3}],
      subtitle_streams: [{index: -1}, {index: 2}],
    },
    queue,
    queue_length: queue.length,
    transitioning_between_items: false,
    transition_in_progress: false,
    playback_runtime_state: 'playing',
    iptv_enabled: true,
    iptv_channel_count: 3,
    jellyfin_enabled: true,
    jellyfin_running: true,
    jellyfin_connected: true,
    jellyfin_authenticated: true,
    jellyfin_cast_target_ready: true,
    jellyfin_catalog_ready: true,
    jellyfin_server_type: 'jellyfin',
    jellyfin_server_url_configured: true,
  }, overrides || {});
}

function fastStatus(status) {
  return Object.fromEntries([
    'state', 'playing', 'paused', 'has_now_playing', 'position', 'duration',
    'volume', 'mute', 'queue_length',
  ].map((key) => [key, status[key]]));
}

function routePath(page, pathname, handler) {
  return page.route((url) => {
    try { return new URL(url).pathname === pathname; }
    catch (_error) { return false; }
  }, handler);
}

async function installCommonRoutes(page, status) {
  await routePath(page, '/status', (route) => route.fulfill({ json: status }));
  await routePath(page, '/playback/state', (route) => route.fulfill({ json: fastStatus(status) }));
  await routePath(page, '/realtime/capabilities', (route) => route.fulfill({
    status: 404,
    contentType: 'application/json',
    body: JSON.stringify({ detail: 'fixture uses polling' }),
  }));
  await routePath(page, '/app/info', (route) => route.fulfill({
    json: { name: 'RelayTV', version: 'v0.11.0', revision: 'fixture' },
  }));
  await routePath(page, '/integrations/plex/status', (route) => route.fulfill({
    json: {
      enabled: true,
      linked: true,
      server_selected: true,
      server: { id: 'plex-server', name: 'Fixture Plex' },
      account: { title: 'Fixture account' },
      link_in_progress: false,
    },
  }));
  await routePath(page, '/integrations/seerr/status', (route) => route.fulfill({
    json: {
      enabled: true,
      configured: true,
      reachable: true,
      version: 'fixture',
      application_title: 'Fixture Seerr',
      request_mode: 'shared_admin',
      caller_connected: false,
      writes_allowed: true,
    },
  }));
}

async function installProviderRoutes(page) {
  await routePath(page, '/jellyfin/home', (route) => route.fulfill({ json: {
    ok: true,
    connected: true,
    authenticated: true,
    last_error: '',
    device_name: 'Fixture room',
    rows: [
      { id: 'continue_watching', title: 'Continue Watching', items: [media('jf-1', 'Fixture movie'), media('jf-2', 'Fixture episode', {type:'Episode', subtitle:'S2 E4 · 48m'})] },
      { id: 'recent_movies', title: 'Recently Added Movies', items: [media('jf-3', 'Long movie title that exercises wrapping', {progress_percent:0, resume_pos:0})] },
    ],
  }}));
  await routePath(page, '/plex/home', (route) => route.fulfill({ json: {
    rows: [
      { title: 'Continue Watching', items: [media('plex-1', 'Fixture Plex movie'), media('plex-2', 'Fixture Plex episode', {subtitle:'S3 E7', progress:64})] },
      { title: 'Recently Added', items: [media('plex-3', 'Another Plex title', {watched:true, progress:0})] },
    ],
  }}));
  await routePath(page, '/iptv/sources', (route) => route.fulfill({ json: {
    items: [{id:'source-fixture', name:'Fixture channels', enabled:true, kind:'url', channel_count:3, last_error:''}],
  }}));
  await routePath(page, '/iptv/channels', (route) => route.fulfill({ json: {
    items: [
      {source_id:'source-fixture', channel_id:'channel-1', name:'Fixture News', group_title:'News', source_name:'Fixture channels', active:true, availability:'available', favorite:true, added:true, logo_url:''},
      {source_id:'source-fixture', channel_id:'channel-2', name:'Fixture Sports', group_title:'Sports', source_name:'Fixture channels', active:true, availability:'unknown', favorite:false, added:true, logo_url:''},
      {source_id:'source-fixture', channel_id:'channel-3', name:'Unavailable Fixture Channel', group_title:'Local', source_name:'Fixture channels', active:false, availability:'unavailable', favorite:false, added:true, logo_url:''},
    ],
    total: 3,
    count: 3,
    has_more: false,
    groups: ['News', 'Sports', 'Local'],
  }}));
  await routePath(page, '/seerr/discover', (route) => route.fulfill({ json: {
    page: 1,
    total_pages: 1,
    total_results: 3,
    results: [
      {media_type:'movie', media_id:101, title:'Fixture discovery movie', original_title:'Fixture discovery movie', date:'2026-01-02', year:2026, overview:'Fixture overview', poster_url:'', backdrop_url:'', rating:8.2, media_status:'unknown', request:null, playback_available:false},
      {media_type:'tv', media_id:102, title:'Fixture discovery series', original_title:'Fixture discovery series', date:'2025-02-03', year:2025, overview:'Fixture overview', poster_url:'', backdrop_url:'', rating:7.8, media_status:'available', request:null, playback_available:true},
      {media_type:'movie', media_id:103, title:'Long Seerr title used to verify card wrapping', original_title:'Long Seerr title used to verify card wrapping', date:'2024-03-04', year:2024, overview:'Fixture overview', poster_url:'', backdrop_url:'', rating:6.9, media_status:'pending', request:{request_id:9,status:'pending'}, playback_available:false},
    ],
  }}));
  await page.route((url) => {
    try { return /^\/jellyfin\/item\/[^/]+$/.test(new URL(url).pathname); }
    catch (_error) { return false; }
  }, (route) => route.fulfill({ json: {
    ok:true,
    connected:true,
    item:media('jf-1', 'Fixture movie', {
      type:'Movie',
      subtitle:'Fixture feature',
      runtime_sec:5820,
      resume_pos:1560,
      audio_language:'eng',
      subtitle_language:'spa',
      audio_streams:[{language:'eng'}, {language:'spa'}],
      subtitle_streams:[{language:'spa'}, {language:'eng'}],
    }),
  }}));
  await page.route((url) => {
    try { return /^\/plex\/items\/[^/]+$/.test(new URL(url).pathname); }
    catch (_error) { return false; }
  }, (route) => route.fulfill({ json: { item:media('plex-1', 'Fixture Plex movie', {
    type:'movie',
    subtitle:'Fixture feature',
    summary:'Deterministic Plex detail copy used to preserve the current interaction and layout.',
    tagline:'A fixture worth resuming',
    content_rating:'PG',
    view_offset_ms:1560000,
    genres:['Drama', 'Fixture'],
    versions:[
      {id:'version-1', label:'1080p', audio_tracks:[{id:'audio-1',label:'English'}], subtitle_tracks:[{id:'subtitle-1',label:'Spanish'}]},
      {id:'version-2', label:'720p', audio_tracks:[{id:'audio-2',label:'English'}], subtitle_tracks:[]},
    ],
  }) }}));
  await page.route((url) => {
    try { return /^\/seerr\/item\/[^/]+\/\d+$/.test(new URL(url).pathname); }
    catch (_error) { return false; }
  }, (route) => route.fulfill({ json: {
    media_type:'movie', media_id:101, title:'Fixture discovery movie', year:2026,
    runtime_minutes:112, rating:8.2, media_status:'pending',
    overview:'Deterministic Seerr detail copy used to preserve the current interaction and layout.',
    tagline:'A safe fixture detail', genres:[{id:18,name:'Drama'}], seasons:[],
    request:{request_id:9,status:'pending',is_4k:false}, playback_available:false,
  }}));
}

async function installDetailRoutes(page) {
  await routePath(page, '/history', (route) => route.fulfill({ json: { history: [
    {history_id:'history-1', ts:1789330000, mode:'jellyfin_play', provider:'jellyfin', url:'https://media.example.test/Videos/one', title:'Fixture episode', channel:'S2 E4', resume_pos:1420, duration_sec:3600, completed:false, available:true},
    {history_id:'history-2', ts:1789320000, mode:'play_now', provider:'youtube', url:'https://www.youtube.com/watch?v=fixture-history', title:'Completed fixture video', channel:'Fixture channel', resume_pos:0, duration_sec:420, completed:true, available:true},
    {history_id:'history-3', ts:1789310000, mode:'upload_play', provider:'upload', url:'/uploads/removed', title:'Removed upload', mime_type:'video/mp4', size_bytes:5242880, resume_pos:0, duration_sec:180, completed:false, available:false},
  ]}}));
  await routePath(page, '/jellyfin/audio/options', (route) => route.fulfill({ json: {
    current_audio_language: 'eng',
    current_audio_stream_index: 1,
    options: [
      {index:1, language:'eng', display:'English stereo', is_default:true, is_current:true},
      {index:3, language:'spa', display:'Spanish stereo', is_default:false, is_current:false},
    ],
  }}));
  await routePath(page, '/jellyfin/subtitle/options', (route) => route.fulfill({ json: {
    current_subtitle_off: false,
    current_subtitle_language: 'spa',
    current_subtitle_stream_index: 2,
    options: [
      {index:-1, language:'', display:'', is_off:true, is_default:false, is_current:false},
      {index:2, language:'spa', display:'Spanish full', is_off:false, is_default:true, is_current:true},
      {index:4, language:'eng', display:'English SDH', is_off:false, is_default:false, is_current:false},
    ],
  }}));
  await routePath(page, '/peers', (route) => route.fulfill({ json: {
    device: {id:'local-fixture', device_name:'Fixture room', base_url:'http://fixture.local:8787'},
    peers: [
      {id:'peer-one', name:'Bedroom TV', base_url:'http://bedroom.fixture:8787', online:true, last_error:''},
      {id:'peer-two', name:'Kitchen display', base_url:'http://kitchen.fixture:8787', online:false, last_error:'offline'},
    ],
    discovered: [{id:'nearby-one', device_name:'Nearby RelayTV', base_url:'http://nearby.fixture:8787'}],
    discovery: {enabled:true, active:true, found:1, reason:''},
  }}));
  await page.route((url) => {
    try { return /\/peers\/[^/]+\/probe$/.test(new URL(url).pathname); }
    catch (_error) { return false; }
  }, async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    const online = pathname.includes('peer-one');
    await route.fulfill({ json: {online, device_name:online ? 'Bedroom TV' : 'Kitchen display', error:online ? '' : 'offline'} });
  });
}

async function fixtureContext(browser, viewport, colorScheme, themeMode) {
  const context = await browser.newContext({
    viewport,
    colorScheme,
    reducedMotion:'reduce',
    serviceWorkers:'block',
  });
  await context.addInitScript((theme) => {
    try { localStorage.setItem('relaytv_theme', theme); } catch (_error) {}
    class QuietEventSource {
      addEventListener() {}
      close() {}
    }
    Object.defineProperty(window, 'EventSource', {value:QuietEventSource, configurable:true});
    Object.defineProperty(window, 'WebSocket', {value:undefined, configurable:true});
  }, themeMode || colorScheme);
  return context;
}

async function newFixturePage(browser, baseUrl, status, scenario) {
  const context = await fixtureContext(
    browser,
    scenario.viewport,
    scenario.colorScheme,
    scenario.themeMode,
  );
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(`page: ${error.message}`));
  page.on('console', (message) => {
    if (message.type() === 'error' && !message.text().includes('Failed to load resource')) {
      errors.push(`console: ${message.text()}`);
    }
  });
  await installCommonRoutes(page, status);
  await installProviderRoutes(page);
  await installDetailRoutes(page);
  await page.goto(`${baseUrl}/ui`, {waitUntil:'domcontentloaded'});
  await page.locator('#nowTopCard').waitFor({state:'visible'});
  await page.waitForFunction((title) => document.querySelector('#now')?.textContent === title, status.now_playing?.title || 'Ready');
  return {context, page, errors};
}

async function screenshot(page, outputDirectory, name) {
  const filename = `${name}.png`;
  await page.screenshot({path:path.join(outputDirectory, filename), fullPage:true, type:'png'});
  return filename;
}

async function captureRemoteStates(browser, baseUrl, outputDirectory, report) {
  const status = playbackStatus();
  const scenario = {viewport:{width:390,height:844}, colorScheme:'dark'};
  let fixture = await newFixturePage(browser, baseUrl, status, scenario);
  try {
    await fixture.page.waitForFunction(() => document.querySelectorAll('.qTile').length === 3);
    report.screenshots.push(await screenshot(fixture.page, outputDirectory, 'fixture-playing-queue-phone'));
    await fixture.page.locator('.qMenuBtn').first().click();
    await fixture.page.locator('.qPopMenu').waitFor();
    report.screenshots.push(await screenshot(fixture.page, outputDirectory, 'fixture-queue-menu-phone'));
    report.assertions.queueItems = await fixture.page.locator('.qTile').count();
    report.assertions.queueMenuActions = await fixture.page.locator('.qPopMenu button').allTextContents();
    report.errors.push(...fixture.errors);
  } finally { await fixture.context.close(); }

  fixture = await newFixturePage(browser, baseUrl, playbackStatus({
    now_playing:{provider:'iptv',url:'https://live.example.test/channel',title:'Fixture live channel',channel:'News · Fixture channels',is_live:true,iptv_source_id:'source-fixture',iptv_channel_id:'channel-1'},
    position:null,
    duration:null,
  }), scenario);
  try {
    await fixture.page.waitForFunction(() => document.querySelector('#pos')?.textContent === 'LIVE');
    report.screenshots.push(await screenshot(fixture.page, outputDirectory, 'fixture-live-unseekable-phone'));
    report.assertions.liveProgressDisabled = await fixture.page.locator('#progress').getAttribute('aria-disabled');
    report.errors.push(...fixture.errors);
  } finally { await fixture.context.close(); }
}

async function captureDialogs(browser, baseUrl, outputDirectory, report) {
  const scenario = {viewport:{width:1280,height:800}, colorScheme:'dark'};
  for (const item of [
    {name:'history', open:async (page) => { await page.locator('#hdrMenuBtn').click(); await page.locator('#histBtn').click(); await page.locator('.histItem').first().waitFor(); }},
    {name:'audio-tracks', open:async (page) => { await page.locator('#nowLangBtn').click(); await page.locator('#langBackdrop:not(.hidden)').waitFor(); await page.locator('.langOpt').first().waitFor(); }},
    {name:'subtitle-tracks', open:async (page) => { await page.locator('#nowSubLangBtn').click(); await page.locator('#subLangBackdrop:not(.hidden)').waitFor(); await page.locator('.langOpt').first().waitFor(); }},
    {name:'peer-transfer', open:async (page) => { await page.locator('#queueSendBtn').click(); await page.locator('#peersBackdrop:not(.hidden)').waitFor(); await page.locator('#peersList .pmRow').first().waitFor(); }},
  ]) {
    const fixture = await newFixturePage(browser, baseUrl, playbackStatus(), scenario);
    try {
      await item.open(fixture.page);
      report.screenshots.push(await screenshot(fixture.page, outputDirectory, `fixture-${item.name}-desktop`));
      report.errors.push(...fixture.errors);
    } finally { await fixture.context.close(); }
  }
}

async function captureProviders(browser, baseUrl, outputDirectory, report) {
  const scenario = {viewport:{width:1280,height:800}, colorScheme:'light'};
  const providers = [
    {name:'jellyfin', button:'#browseJellyfinBtn', shell:'#jellyfinShell:not(.hidden)', ready:'.jfItem', detail:'#jfDetail[aria-hidden="false"]', detailTitle:'#jfDetailTitle'},
    {name:'plex', button:'#browsePlexBtn', shell:'#plexShell:not(.hidden)', ready:'.plexCard', detail:'#plexDetail[aria-hidden="false"]', detailTitle:'#plexDetailTitle'},
    {name:'iptv', button:'#browseIptvBtn', shell:'#iptvShell:not(.hidden)', ready:'.iptvChannel'},
    {name:'seerr', button:'#browseSeerrBtn', shell:'#seerrShell:not(.hidden)', ready:'.seerrCard', detail:'#seerrDetail:not(.hidden)', detailTitle:'#seerrDetailTitle'},
  ];
  for (const provider of providers) {
    const fixture = await newFixturePage(browser, baseUrl, playbackStatus(), scenario);
    try {
      await fixture.page.locator('[data-destination="browse"]').click();
      await fixture.page.locator(provider.button).waitFor({state:'visible'});
      await fixture.page.locator(provider.button).click();
      await fixture.page.locator(provider.shell).waitFor();
      await fixture.page.locator(provider.ready).first().waitFor();
      report.screenshots.push(await screenshot(fixture.page, outputDirectory, `fixture-${provider.name}-desktop`));
      report.assertions[`${provider.name}Items`] = await fixture.page.locator(provider.ready).count();
      if (provider.detail) {
        await fixture.page.locator(provider.ready).first().click({force:true});
        await fixture.page.locator(provider.detail).waitFor();
        await fixture.page.locator(provider.detailTitle).waitFor();
        report.screenshots.push(await screenshot(fixture.page, outputDirectory, `fixture-${provider.name}-detail-desktop`));
        report.assertions[`${provider.name}Detail`] = await fixture.page.locator(provider.detailTitle).textContent();
      }
      report.errors.push(...fixture.errors);
    } finally { await fixture.context.close(); }
  }
}

async function captureConnectionFailure(browser, baseUrl, outputDirectory, report) {
  const fixture = await newFixturePage(browser, baseUrl, playbackStatus(), {viewport:{width:390,height:844},colorScheme:'dark'});
  try {
    await routePath(fixture.page, '/playback/toggle', (route) => route.abort('connectionfailed'));
    await fixture.page.locator('#playPauseBtn').click();
    await fixture.page.waitForFunction(() => !document.querySelector('#connBadge')?.classList.contains('hidden'));
    report.screenshots.push(await screenshot(fixture.page, outputDirectory, 'fixture-command-connection-failure-phone'));
    report.assertions.connectionFailure = await fixture.page.locator('#connBadge').textContent();
    await routePath(fixture.page, '/playback/state', (route) => route.abort('connectionfailed'));
    await routePath(fixture.page, '/status', (route) => route.abort('connectionfailed'));
    await fixture.page.evaluate(async () => {
      await refresh();
      await refresh();
    });
    await fixture.page.waitForFunction(() => document.querySelector('#connBadge')?.textContent === 'Reconnecting…');
    report.screenshots.push(await screenshot(fixture.page, outputDirectory, 'fixture-reconnecting-phone'));
    report.assertions.reconnecting = await fixture.page.locator('#connBadge').textContent();
    report.errors.push(...fixture.errors);
  } finally { await fixture.context.close(); }
}

async function exerciseAuthorization(browser, baseUrl, report, accept) {
  const context = await fixtureContext(browser, {width:390,height:844}, 'dark');
  const page = await context.newPage();
  const status = playbackStatus();
  await installCommonRoutes(page, status);
  let attempts = 0;
  let authorized = false;
  await routePath(page, '/playback/toggle', async (route) => {
    attempts += 1;
    authorized = route.request().headers().authorization === 'Bearer fixture-token';
    if (!authorized) {
      await route.fulfill({
        status:401,
        headers:{'www-authenticate':'Bearer'},
        contentType:'application/json',
        body:JSON.stringify({detail:'Not authorized'}),
      });
      return;
    }
    await route.fulfill({json:{ok:true}});
  });
  page.once('dialog', async (dialog) => {
    if (accept) await dialog.accept('fixture-token');
    else await dialog.dismiss();
  });
  try {
    await page.goto(`${baseUrl}/ui`, {waitUntil:'domcontentloaded'});
    await page.locator('#playPauseBtn').click();
    await page.waitForTimeout(250);
    const stored = await page.evaluate(() => localStorage.getItem('relaytv_api_token') || '');
    return {accept, attempts, authorized, stored:stored ? '[stored]' : ''};
  } finally { await context.close(); }
}

async function captureOverlay(browser, baseUrl, outputDirectory, report) {
  const status = playbackStatus();
  const context = await fixtureContext(browser, {width:1600,height:900}, 'dark');
  const page = await context.newPage();
  await installCommonRoutes(page, status);
  try {
    await page.goto(`${baseUrl}/x11/overlay`, {waitUntil:'domcontentloaded'});
    await page.evaluate(() => addToast({
      level:'info',
      icon:'play',
      text:'Fixture notification with a bounded long message that remains readable across the room.',
      position:'top-right',
      duration:30,
    }));
    await page.locator('.toast.show').waitFor();
    report.screenshots.push(await screenshot(page, outputDirectory, 'fixture-overlay-notification-1600x900'));
    report.assertions.overlayToasts = await page.locator('.toast.show').count();
  } finally { await context.close(); }
}

async function captureCompatibility(browser, baseUrl, report) {
  let fixture = await newFixturePage(browser, baseUrl, playbackStatus(), {
    viewport:{width:390,height:844},
    colorScheme:'dark',
  });
  try {
    await fixture.page.waitForFunction(() => document.querySelectorAll('.qTile').length === 3);
    report.assertions.phoneHorizontalOverflow = await fixture.page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    );
    report.assertions.darkThemeRequested = await fixture.page.evaluate(
      () => matchMedia('(prefers-color-scheme: dark)').matches,
    );
    await fixture.page.locator('.qMenuBtn').first().click({force:true});
    await fixture.page.locator('.qPopMenu').waitFor();
    report.assertions.queueMenuActions = await fixture.page.locator('.qPopMenu button').allTextContents();
    await fixture.page.keyboard.press('Escape');
    await fixture.page.locator('#hdrMenuBtn').click();
    await fixture.page.locator('#hdrMenuPanel:not(.hidden)').waitFor();
    await fixture.page.locator('#settingsBtn').click();
    await fixture.page.locator('#settingsBackdrop:not(.hidden)').waitFor();
    report.assertions.settingsDialogRole = await fixture.page.locator('#settingsBackdrop').getAttribute('role');
    report.errors.push(...fixture.errors);
  } finally { await fixture.context.close(); }

  fixture = await newFixturePage(browser, baseUrl, playbackStatus(), {
    viewport:{width:1280,height:800},
    colorScheme:'light',
  });
  try {
    await fixture.page.locator('[data-destination="browse"]').click();
    await fixture.page.locator('#browseJellyfinBtn').click();
    await fixture.page.locator('#jellyfinShell:not(.hidden)').waitFor();
    await fixture.page.locator('.jfItem').first().waitFor();
    report.assertions.jellyfinItems = await fixture.page.locator('.jfItem').count();
    report.assertions.jellyfinVisibleItems = await fixture.page.locator('.jfItem').evaluateAll(
      (items) => items.filter((item) => item.getClientRects().length > 0).length,
    );
    report.assertions.jellyfinItemTitles = await fixture.page.locator('.jfItemTitle').allTextContents();
    report.assertions.desktopHorizontalOverflow = await fixture.page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    );
    report.errors.push(...fixture.errors);
  } finally { await fixture.context.close(); }
}

async function main() {
  const rootDirectory = path.resolve(__dirname, '..');
  const baseUrl = option('base', 'http://127.0.0.1:8787').replace(/\/$/, '');
  const browserName = option('browser', 'chromium');
  const mode = option('mode', 'full');
  const browserType = {chromium, firefox, webkit}[browserName];
  if (!browserType) throw new Error(`unknown browser: ${browserName}`);
  if (!['full', 'compatibility'].includes(mode)) throw new Error(`unknown mode: ${mode}`);
  const outputDirectory = path.resolve(option('output', path.join(rootDirectory, 'docs', 'images', 'ui-phase-0', 'states')));
  const reportPath = path.resolve(option('report', path.join(rootDirectory, 'docs', 'ui-modernization', 'phase-0-states.json')));
  fs.mkdirSync(outputDirectory, {recursive:true});
  fs.mkdirSync(path.dirname(reportPath), {recursive:true});
  const browser = await browserType.launch({headless:true});
  const report = {
    schema:1,
    capturedAt:new Date().toISOString(),
    baseUrl,
    mode,
    runner:{playwright:require('playwright/package.json').version, browser:browserName, browserVersion:browser.version(), platform:`${process.platform}/${process.arch}`},
    fixtures:'All displayed media, account, device, provider, and network values are deterministic examples.',
    screenshots:[],
    assertions:{},
    authorization:[],
    errors:[],
  };
  try {
    if (mode === 'compatibility') {
      await captureCompatibility(browser, baseUrl, report);
    } else {
      await captureRemoteStates(browser, baseUrl, outputDirectory, report);
      await captureDialogs(browser, baseUrl, outputDirectory, report);
      await captureProviders(browser, baseUrl, outputDirectory, report);
      await captureConnectionFailure(browser, baseUrl, outputDirectory, report);
      report.authorization.push(await exerciseAuthorization(browser, baseUrl, report, true));
      report.authorization.push(await exerciseAuthorization(browser, baseUrl, report, false));
      await captureOverlay(browser, baseUrl, outputDirectory, report);
    }
  } finally { await browser.close(); }
  if (report.errors.length) throw new Error(report.errors.join('\n'));
  if (mode === 'full') {
    const accepted = report.authorization.find((item) => item.accept);
    const canceled = report.authorization.find((item) => !item.accept);
    if (!accepted || accepted.attempts !== 2 || !accepted.authorized || accepted.stored !== '[stored]') {
      throw new Error(`authorization accept evidence failed: ${JSON.stringify(accepted)}`);
    }
    if (!canceled || canceled.attempts !== 1 || canceled.authorized || canceled.stored) {
      throw new Error(`authorization cancel evidence failed: ${JSON.stringify(canceled)}`);
    }
    const required = {
      queueItems:3,
      liveProgressDisabled:'true',
      jellyfinItems:3,
      jellyfinDetail:'Fixture movie',
      plexItems:3,
      plexDetail:'Fixture Plex movie',
      iptvItems:3,
      seerrItems:3,
      seerrDetail:'Fixture discovery movie',
      connectionFailure:'Command failed — check connection',
      reconnecting:'Reconnecting…',
      overlayToasts:1,
    };
    const expectedActions = ['Play now', 'Send to device', 'Move up', 'Move down', 'Remove'];
    const mismatch = Object.entries(required).find(([key, value]) => report.assertions[key] !== value);
    if (mismatch || JSON.stringify(report.assertions.queueMenuActions) !== JSON.stringify(expectedActions)) {
      throw new Error(`full state evidence failed: ${JSON.stringify(report.assertions)}`);
    }
  } else {
    const expectedActions = ['Play now', 'Send to device', 'Move up', 'Move down', 'Remove'];
    if (report.assertions.phoneHorizontalOverflow || report.assertions.desktopHorizontalOverflow
        || !report.assertions.darkThemeRequested
        || report.assertions.settingsDialogRole !== 'dialog'
        || report.assertions.jellyfinItems !== 3
        || report.assertions.jellyfinVisibleItems !== 3
        || JSON.stringify(report.assertions.queueMenuActions) !== JSON.stringify(expectedActions)) {
      throw new Error(`compatibility evidence failed: ${JSON.stringify(report.assertions)}`);
    }
  }
  fs.writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`);
  process.stdout.write(`${JSON.stringify({ok:true, reportPath, outputDirectory, screenshots:report.screenshots.length}, null, 2)}\n`);
}

if (require.main === module) {
  main().catch((error) => {
    process.stderr.write(`UI phase 0 state capture failed: ${error.stack || error}\n`);
    process.exitCode = 1;
  });
}

module.exports = {newFixturePage, playbackStatus};
