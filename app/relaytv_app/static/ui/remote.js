'use strict';

// Remote, queue, realtime, history, track, About, and media-input controller.
// Bootstrap and dependency wiring remain in app.js.

// --- Manual URL modal + clipboard helpers
async function clipboardText(){
  try {
    // Clipboard read is restricted in many contexts (must be secure context + user gesture).
    if (!window.isSecureContext) return "";
    if (!navigator.clipboard || !navigator.clipboard.readText) return "";
    return (await navigator.clipboard.readText()) || "";
  } catch (_) {
    return "";
  }
}

function looksLikeUrl(s){
  if (!s) return false;
  const t = String(s).trim();
  return /^https?:\/\//i.test(t) || /^www\./i.test(t);
}

function normalizeUrl(s){
  const t = String(s || "").trim();
  if (!t) return "";
  if (/^https?:\/\//i.test(t)) return t;
  if (/^www\./i.test(t)) return "https://" + t;
  return t;
}

function _setAddHelper(msg, kind){
  const el = document.getElementById('addHelperTxt');
  if (!el) return;
  el.classList.remove('err', 'ok');
  if (kind === 'err' || kind === 'ok') el.classList.add(kind);
  if (String(msg || '').trim()) {
    el.textContent = String(msg).trim();
    return;
  }
  el.textContent = String(el.getAttribute('data-default') || '').trim();
}

async function openAddUrl(){
  const bd = document.getElementById('addBackdrop');
  const inp = document.getElementById('addUrlInput');
  if (!bd || !inp) return;
  if (!bd.classList.contains('hidden')) return;
  bd.classList.remove('hidden');
  _uiPushLayer();
  _setAddHelper('', '');
  const clip = await clipboardText();
  if (looksLikeUrl(clip) && !inp.value.trim()) inp.value = normalizeUrl(clip);
  inp.focus();
  inp.select();
}

function closeAddUrl(opts){
  const bd = document.getElementById('addBackdrop');
  if (!bd) return;
  const fromNav = !!(opts && opts.fromNav);
  if (!fromNav && !bd.classList.contains('hidden') && __layerNavigation.getDepth() > 0) {
    try { history.back(); } catch (_e) {}
    return;
  }
  bd.classList.add('hidden');
}

async function pasteIntoAddUrl(){
  const inp = document.getElementById('addUrlInput');
  if (!inp) return;
  let clip = '';
  let blockedReason = '';
  if (!window.isSecureContext) {
    blockedReason = 'Paste unavailable here. Use HTTPS/localhost (secure context) to access clipboard.';
  } else if (!navigator.clipboard || !navigator.clipboard.readText) {
    blockedReason = 'Paste unavailable in this browser/runtime (Clipboard API not exposed).';
  } else {
    try {
      clip = (await navigator.clipboard.readText()) || '';
    } catch (_e) {
      blockedReason = 'Clipboard access blocked. Allow clipboard permissions and retry.';
    }
  }
  if (clip) {
    inp.value = normalizeUrl(clip);
    _setAddHelper('Pasted from clipboard.', 'ok');
  } else if (blockedReason) {
    _setAddHelper(blockedReason, 'err');
  } else {
    _setAddHelper('Clipboard is empty.', '');
  }
  inp.focus();
  inp.select();
}

// Guards the window between submitting and the modal closing. Resolving a link
// can take seconds, and the modal stays open and clickable throughout — a
// second tap in that window used to add the item all over again.
let addUrlSubmitting = false;

async function submitAddUrl(mode){
  if (addUrlSubmitting) return;
  const inp = document.getElementById('addUrlInput');
  if (!inp) return;
  const url = normalizeUrl(inp.value);
  if (!looksLikeUrl(url)) {
    alert('Please enter a valid URL (starting with http(s):// or www.)');
    inp.focus();
    return;
  }

  addUrlSubmitting = true;
  try {
    const result = (mode === 'queue')
      ? await post('/enqueue', {url})
      : await post('/play_now', {url, preserve_current:true, preserve_to:'queue_front', resume_current:true, reason:'add_menu'});
    // Keep the modal open when the server refused, so the link is still there
    // to retry or correct. Closing it used to look exactly like success.
    if (result && result.ok === false){
      _setAddHelper(result.detail || 'Could not add that link.', 'err');
      return;
    }
    closeAddUrl();
  } finally {
    addUrlSubmitting = false;
  }
}

function _setNotifyHelper(msg, kind){
  const el = document.getElementById('notifyHelperTxt');
  if (!el) return;
  el.classList.remove('err', 'ok');
  if (kind === 'err' || kind === 'ok') el.classList.add(kind);
  el.textContent = String(msg || '').trim();
}

function readNotifyImageDataUrl(file){
  return new Promise((resolve, reject) => {
    if (!file) {
      resolve('');
      return;
    }
    if (!String(file.type || '').toLowerCase().startsWith('image/')) {
      reject(new Error('Please choose an image file.'));
      return;
    }
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = () => reject(new Error('Could not read selected image.'));
    reader.readAsDataURL(file);
  });
}

async function submitNotificationToast(){
  const textEl = document.getElementById('notifyTextInput');
  const imageEl = document.getElementById('notifyImageInput');
  const imageUrlEl = document.getElementById('notifyImageUrlInput');
  const posEl = document.getElementById('notifyPositionSelect');
  const durEl = document.getElementById('notifyDurationInput');
  const sendBtn = document.getElementById('notifySendBtn');
  const text = String(textEl?.value || '').trim();
  if (!text) {
    _setNotifyHelper('Enter notification text first.', 'err');
    if (textEl) textEl.focus();
    return;
  }
  const position = String(posEl?.value || 'top-left').trim() || 'top-left';
  let duration = Number(durEl?.value || 5);
  if (!Number.isFinite(duration)) duration = 5;
  duration = Math.min(30, Math.max(0.8, duration));
  const payload = {text, position, duration, level:'info', icon:'info'};
  try {
    if (sendBtn) sendBtn.disabled = true;
    _setNotifyHelper('Sending…', '');
    const file = imageEl && imageEl.files && imageEl.files.length ? imageEl.files[0] : null;
    const imageUrl = file ? await readNotifyImageDataUrl(file) : String(imageUrlEl?.value || '').trim();
    if (imageUrl) {
      const normalizedImageUrl = normalizeUrl(imageUrl);
      if (!/^(https?:\/\/|\/|data:image\/)/i.test(normalizedImageUrl)) {
        throw new Error('Image URL must start with http(s)://, www., /, or data:image/.');
      }
      payload.image_url = normalizedImageUrl;
    }
    const r = await _fetchWithTimeout('/overlay', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    }, 5000);
    if (!r.ok) {
      let detail = '';
      try {
        const body = await r.json();
        detail = body && body.detail ? (typeof body.detail === 'string' ? body.detail : (body.detail.message || body.detail.error || '')) : '';
      } catch(_e) {}
      throw new Error(detail || `Notification failed (${r.status})`);
    }
    _setNotifyHelper('Notification sent.', 'ok');
    if (imageEl) imageEl.value = '';
    if (imageUrlEl) imageUrlEl.value = '';
  } catch (e) {
    _setNotifyHelper(e && e.message ? e.message : 'Notification failed.', 'err');
  } finally {
    if (sendBtn) sendBtn.disabled = false;
  }
}

let uploadMediaSubmitting = false;

function _setUploadHelper(message, kind){
  const helper = document.getElementById('uploadHelperTxt');
  if (!helper) return;
  helper.classList.remove('err', 'ok');
  if (kind === 'err' || kind === 'ok') helper.classList.add(kind);
  helper.textContent = String(message || '').trim();
}

async function submitUploadedMedia(mode){
  if (uploadMediaSubmitting) return;
  const input = document.getElementById('uploadMediaInput');
  const file = input?.files?.[0] || null;
  if (!file) {
    _setUploadHelper('Choose a video or audio file first.', 'err');
    input?.focus();
    return;
  }
  const form = new FormData();
  form.append('file', file, file.name);
  const title = String(document.getElementById('uploadTitleInput')?.value || '').trim();
  if (title) form.append('title', title);
  const buttons = ['uploadQueueBtn', 'uploadPlayBtn'].map((id) => document.getElementById(id)).filter(Boolean);
  uploadMediaSubmitting = true;
  buttons.forEach((button) => { button.disabled = true; });
  _setUploadHelper(mode === 'queue' ? 'Uploading to queue…' : 'Uploading and preparing playback…', '');
  try {
    const response = await fetch(mode === 'queue' ? '/ingest/media/enqueue' : '/ingest/media/play', {
      method:'POST',
      body:form,
    });
    let payload = null;
    try { payload = await response.json(); } catch (_error) {}
    if (!response.ok) {
      const detail = payload && payload.detail;
      throw new Error(typeof detail === 'string' ? detail : `Upload failed (${response.status})`);
    }
    _setUploadHelper(mode === 'queue' ? 'Uploaded to queue.' : 'Upload started on the TV.', 'ok');
    if (input) input.value = '';
    const titleInput = document.getElementById('uploadTitleInput');
    if (titleInput) titleInput.value = '';
    closeAddUrl();
    await refresh();
  } catch (error) {
    _setUploadHelper(error?.message || 'Upload failed. Try again.', 'err');
  } finally {
    uploadMediaSubmitting = false;
    buttons.forEach((button) => { button.disabled = false; });
  }
}

function fmtTime(s){
  if (s == null || isNaN(s)) return '--:--';
  s = Math.max(0, Math.floor(s));
  const h = Math.floor(s/3600);
  const m = Math.floor((s%3600)/60);
  const sec = s%60;
  return (h>0?`${h}:`:'') + String(m).padStart(2,'0') + ':' + String(sec).padStart(2,'0');
}

let __lastStatus = __statusStore.getSnapshot();
let __lastStatusFullFetchTs = 0;

function _setStatusSnapshot(next){
  __lastStatus = next;
  __statusStore.setSnapshot(next);
  return next;
}
// Active realtime transport wrapper: {kind, handle, generation}.
let __uiEventSource = null;
let __uiEventSourceLastTs = 0;
let __uiEventSourceBornTs = 0;
let __uiEventReconnectTimer = 0;
let __uiEventGeneration = 0;
let __uiEventConnectInFlight = false;
let __uiEventFailureCount = 0;
let __uiRealtimeCapabilities = null;
const __uiRealtimePolicy = window.RelayTVRealtime.createPolicy({
  fallbackCapabilities:{
    protocol_version:0,
    preferred_transport:'sse',
    websocket:{enabled:false},
    sse:{enabled:true, ui:'/ui/events'},
  },
  capabilityTtlMs:300000,
  websocketCooldownMs:60000,
});
const __uiRealtimeSequence = window.RelayTVRealtime.createSequenceTracker();
let __remoteVolumeKnownValue = null;

function _mergePlaybackStateIntoStatus(base, fast){
  const out = Object.assign({}, (base && typeof base === 'object') ? base : {});
  const src = (fast && typeof fast === 'object') ? fast : null;
  if (!src) return out;
  [
    'state',
    'playing',
    'paused',
    'queue_length',
    'playback_telemetry_source',
    'playback_telemetry_freshness',
  ].forEach((key) => {
    if (Object.prototype.hasOwnProperty.call(src, key)) out[key] = src[key];
  });
  ['position', 'duration', 'volume', 'mute'].forEach((key) => {
    if (!Object.prototype.hasOwnProperty.call(src, key)) return;
    const value = src[key];
    if (value != null || !src.playing || !out.playing) out[key] = value;
  });
  if (Object.prototype.hasOwnProperty.call(src, 'has_now_playing')) {
    out.has_now_playing = !!src.has_now_playing;
    if (!out.has_now_playing && !src.playing && !src.paused) {
      out.now_playing = null;
      out.resume_available = false;
    }
  }
  return out;
}

function _shouldRefreshFullStatus(st, fast){
  if (!st || typeof st !== 'object') return true;
  if (!fast || typeof fast !== 'object') return true;
  const now = Date.now();
  const fastPlaying = !!fast.playing;
  const maxAgeMs = fastPlaying ? 5000 : 12000;
  if ((now - __lastStatusFullFetchTs) > maxAgeMs) return true;
  const hasNow = _hasNowPlayingItem(st, st.now_playing || {});
  if (!!fast.has_now_playing !== !!hasNow) return true;
  if (Array.isArray(st.queue) && Number(st.queue.length || 0) !== Number(fast.queue_length || 0)) return true;
  if (!Array.isArray(st.queue) && Number(fast.queue_length || 0) > 0) return true;
  return false;
}

async function _fetchFastPlaybackState(){
  const r = await _fetchWithTimeout('/playback/state', {cache:'no-store'}, 2500);
  if (!r.ok) throw new Error(`playback_state ${r.status}`);
  return await r.json();
}

async function _fetchFullStatus(){
  const r = await _fetchWithTimeout('/status', {cache:'no-store'}, 4000);
  if (!r.ok) throw new Error(`status ${r.status}`);
  const st = await r.json();
  __lastStatusFullFetchTs = Date.now();
  return st;
}

function _uiEventMarkAlive(){
  __uiEventSourceLastTs = Date.now();
  __uiEventFailureCount = 0;
  _connSignal(true);
}

function _uiEventHealthy(){
  // Server pings every 5s when idle; allow two missed pings before we call
  // the stream dead. Only real events stamp the clock, so a stream that never
  // delivers anything can never read as healthy — the fallback poll and the
  // reconnect badge stay armed while reconnect attempts are unproven.
  return !!(__uiEventSource && __uiEventSourceLastTs
    && ((Date.now() - __uiEventSourceLastTs) < 12000));
}

function _closeUiEventStream(reason='closed_by_client'){
  const active = __uiEventSource;
  __uiEventSource = null;
  __uiEventGeneration += 1;
  __uiEventConnectInFlight = false;
  if (active) __uiRealtimePolicy.retire(active.kind, reason, active.websocketAttempt);
  if (active && active.handle) { try { active.handle.close(); } catch (_e) {} }
}

// Reconnect on staleness, not just on a null handle: connections killed by
// Android screen-off/Doze or an AP roam often die without ever firing onerror,
// leaving a zombie EventSource that would otherwise block reconnects forever.
function _ensureUiEventStream(){
  if (__uiEventSource){
    if (_uiEventHealthy()) return;
    // A fresh stream gets time to connect and deliver its first event before
    // we recycle it — but it stays "unhealthy" until that event arrives, so
    // this grace never suppresses the fallback poll.
    if ((Date.now() - __uiEventSourceBornTs) < 12000) return;
  }
  _closeUiEventStream('stale');
  connectUiEventStream();
}

// --- Connection indicator ---------------------------------------------------
let __connFailStreak = 0;
let __connBadgeStickyUntil = 0;

function _connSignal(ok, opts){
  const badge = document.getElementById('connBadge');
  if (ok){
    __connFailStreak = 0;
    if (badge && Date.now() >= __connBadgeStickyUntil){
      badge.classList.add('hidden');
      badge.classList.remove('cmdErr');
      document.body.classList.remove('connLost');
    }
    return;
  }
  const o = (opts && typeof opts === 'object') ? opts : {};
  __connFailStreak += 1;
  if (o.sticky) __connBadgeStickyUntil = Date.now() + 2500;
  if (__connFailStreak >= 2 || o.sticky){
    if (badge){
      badge.textContent = o.message || 'Reconnecting…';
      badge.classList.remove('cmdErr');
      badge.classList.remove('hidden');
    }
    document.body.classList.add('connLost');
  }
}

// A command the server actively rejected is not a connection problem: the
// request arrived, was understood, and was refused. Reporting it as
// "Reconnecting…" sends the user to check their wifi over a 409 or an expired
// token, and dimming the remote implies the whole app is offline. Rejections
// get their own badge and leave the connLost state alone.
function _commandRejected(message){
  __connFailStreak = 0;
  __connBadgeStickyUntil = Date.now() + 3500;
  const badge = document.getElementById('connBadge');
  if (!badge) return;
  badge.textContent = String(message || 'Command rejected');
  badge.classList.add('cmdErr');
  badge.classList.remove('hidden');
}

function _scheduleUiEventReconnect(){
  if (__uiEventReconnectTimer) return;
  __uiEventFailureCount += 1;
  const exponent = Math.min(5, Math.max(0, __uiEventFailureCount - 1));
  const baseDelay = Math.min(30000, 1000 * (2 ** exponent));
  const delay = Math.round(baseDelay * (0.75 + (Math.random() * 0.5)));
  __uiEventReconnectTimer = window.setTimeout(() => {
    __uiEventReconnectTimer = 0;
    connectUiEventStream();
  }, delay);
}

function _parseUiEventPayload(ev){
  try {
    return JSON.parse(ev && ev.data ? ev.data : '{}');
  } catch (_e) {
    return null;
  }
}

// Queue drag state (prevents UI refresh from nuking DOM mid-drag)
let __draggingQueue = false;

let __dragStartTs = 0;
let __queueDnDBound = false;
let __queueDnDCleanup = null;

function _queueTileFromPoint(x, y){
  const el = document.elementFromPoint(x, y);
  if (!el) return null;
  return el.closest ? el.closest('.qTile') : null;
}

function bindQueuePointerDnD(){
  if (__queueDnDBound) return;
  __queueDnDBound = true;

  const ol = document.getElementById('queue');
  if (!ol) return;

  let startFrom = null;
  let overTo = null;
  let startQueueId = '';
  let overQueueId = '';
  let startX = 0, startY = 0;
  let active = false;
  const MOVE_PX = 4;

  const cleanup = () => {
    __draggingQueue = false;
    active = false;
    startFrom = null;
    overTo = null;
    startQueueId = '';
    overQueueId = '';
    __dragStartTs = 0;
    document.body.classList.remove('noScroll');
    document.querySelectorAll('.qTile.dragging').forEach(x => x.classList.remove('dragging'));
    document.querySelectorAll('.qTile.dragOver').forEach(x => x.classList.remove('dragOver'));
  };

  __queueDnDCleanup = cleanup;

  const finish = async () => {
    const from = startFrom;
    const to = overTo;
    const fromQueueId = startQueueId;
    const toQueueId = overQueueId;
    const didDrag = active; // capture before cleanup() resets state
    cleanup();
    if (didDrag && from != null && to != null && from !== to) {
      await qMove(from, to, fromQueueId, toQueueId);
    }
  };

  ol.addEventListener('pointerdown', (e) => {
    const handle = e.target && e.target.closest ? e.target.closest('.qHandle') : null;
    if (!handle) return;
    const tile = handle.closest('.qTile');
    if (!tile) return;

    // Only primary mouse button; touch/pen OK.
    if (e.button != null && e.button !== 0) return;

    const fromIdx = parseInt(tile.dataset.index || '', 10);
    if (isNaN(fromIdx)) return;

    startFrom = fromIdx;
    overTo = fromIdx;
    startQueueId = String(tile.dataset.queueId || '');
    overQueueId = startQueueId;
    startX = e.clientX || 0;
    startY = e.clientY || 0;
    active = false;

    __draggingQueue = true;
    __dragStartTs = Date.now();

    tile.classList.add('dragging');
    document.body.classList.add('noScroll');

    try { ol.setPointerCapture(e.pointerId); } catch(_){}
    try { e.preventDefault(); } catch(_){}
  }, {passive:false});

  ol.addEventListener('pointermove', (e) => {
    if (!__draggingQueue || startFrom == null) return;

    const dx = (e.clientX || 0) - startX;
    const dy = (e.clientY || 0) - startY;
    if (!active && (Math.abs(dx) + Math.abs(dy) < MOVE_PX)) return;
    active = true;

    const tile = _queueTileFromPoint(e.clientX, e.clientY);
    if (!tile) return;
    const toIdx = parseInt(tile.dataset.index || '', 10);
    if (isNaN(toIdx)) return;
    overTo = toIdx;
    overQueueId = String(tile.dataset.queueId || '');

    document.querySelectorAll('.qTile.dragOver').forEach(x => x.classList.remove('dragOver'));
    tile.classList.add('dragOver');

    try { e.preventDefault(); } catch(_){}
  }, {passive:false});

  ol.addEventListener('pointerup', async (e) => { try { e.preventDefault(); } catch(_){} await finish(); }, {passive:false});
  ol.addEventListener('pointercancel', async (e) => { try { e.preventDefault(); } catch(_){} await finish(); }, {passive:false});
  const __winUp = async (e) => {
    if (!__draggingQueue) return;
    try { e.preventDefault(); } catch(_){}
    await finish();
  };
  window.addEventListener('pointerup', __winUp, {passive:false});
  window.addEventListener('pointercancel', __winUp, {passive:false});
  window.addEventListener('blur', () => cleanup(), {once:false});
}


// Scrubber state
let __scrubbing = false;
let __scrubPct = 0;

function _isHiddenEl(el){
  return !el || el.classList.contains('hidden');
}

function _uiRefreshInteractionLockActive(){
  if (__draggingQueue) return true;
  if (__queueMenuEl) return true;
  if (__pendingRemove) return true;
  const modalIds = ['addBackdrop', 'histBackdrop', 'aboutBackdrop', 'settingsBackdrop', 'langBackdrop', 'peersBackdrop'];
  for (const id of modalIds) {
    const el = document.getElementById(id);
    if (!_isHiddenEl(el)) return true;
  }
  const menu = document.getElementById('hdrMenuPanel');
  if (menu && !menu.classList.contains('hidden')) return true;
  return false;
}

function _uiPushLayer(){
  __layerNavigation.push();
}

function _uiCloseTopLayerFromNav(){
  if (__queueMenuEl) {
    closeQueueMenu();
    return true;
  }
  if (window.relaytvPlex && window.relaytvPlex.isDetailOpen()) {
    window.relaytvPlex.closeDetail({fromNav:true});
    return true;
  }
  if (window.relaytvPlex && window.relaytvPlex.isOpen()) {
    window.relaytvPlex.close({fromNav:true, force:true});
    return true;
  }
  if (window.relaytvSeerr && window.relaytvSeerr.isDetailOpen()) {
    window.relaytvSeerr.closeDetail({fromNav:true});
    return true;
  }
  if (window.relaytvSeerr && window.relaytvSeerr.isOpen()) {
    window.relaytvSeerr.close({fromNav:true, force:true});
    return true;
  }
  if (window.relaytvIptv && window.relaytvIptv.isOpen()) {
    window.relaytvIptv.close({fromNav:true, force:true});
    return true;
  }
  if (_jfIsDetailOpen()) {
    _jfCloseDetailPanel({fromNav:true});
    return true;
  }
  if (__jfUiVisible) {
    closeJellyfinShell({fromNav:true, force:true});
    return true;
  }
  const subLangBd = document.getElementById('subLangBackdrop');
  if (!_isHiddenEl(subLangBd)) {
    closeNowSubtitleModal({fromNav:true});
    return true;
  }
  const langBd = document.getElementById('langBackdrop');
  if (!_isHiddenEl(langBd)) {
    closeNowLanguageModal({fromNav:true});
    return true;
  }
  const settingsBd = document.getElementById('settingsBackdrop');
  if (!_isHiddenEl(settingsBd)) {
    closeSettings({fromNav:true});
    return true;
  }
  const aboutBd = document.getElementById('aboutBackdrop');
  if (!_isHiddenEl(aboutBd)) {
    closeAbout({fromNav:true});
    return true;
  }
  const peersBd = document.getElementById('peersBackdrop');
  if (!_isHiddenEl(peersBd)) {
    if (window.relaytvPeers) window.relaytvPeers.close({fromNav:true});
    else peersBd.classList.add('hidden');
    return true;
  }
  const histBd = document.getElementById('histBackdrop');
  if (!_isHiddenEl(histBd)) {
    closeHistory({fromNav:true});
    return true;
  }
  const addBd = document.getElementById('addBackdrop');
  if (!_isHiddenEl(addBd)) {
    closeAddUrl({fromNav:true});
    return true;
  }
  const menu = document.getElementById('hdrMenuPanel');
  if (menu && !menu.classList.contains('hidden')) {
    closeHeaderMenu();
    return true;
  }
  return false;
}

function _safeUrlHost(u){
  try {
    const uu = new URL(u);
    return (uu.hostname || '').toLowerCase();
  } catch (_) {
    return '';
  }
}

function _looksLikeJellyfinMediaUrl(u){
  try {
    const uu = new URL(String(u || ''));
    const p = (uu.pathname || '').toLowerCase();
    const hasApi = uu.searchParams.has('api_key') || uu.searchParams.has('ApiKey');
    if ((p.includes('/videos/') || p.includes('/items/')) && (hasApi || p.includes('/stream'))) return true;
  } catch (_) {}
  return false;
}

const __favBrandColors = {
  'youtube.com': '#ff0033', 'youtu.be': '#ff0033',
  'rumble.com': '#85c742',
  'twitch.tv': '#9146ff',
  'tiktok.com': '#000000',
  'vimeo.com': '#17b3e8',
  'odysee.com': '#ef1970',
  'dailymotion.com': '#00aaff',
  'peertube.tv': '#f1680d',
  'bitchute.com': '#d2441c',
  'x.com': '#000000', 'twitter.com': '#1d9bf0',
  'facebook.com': '#0866ff', 'fb.watch': '#0866ff',
  'instagram.com': '#ff0069',
  'kick.com': '#53fc19',
};
const __favPalette = ['#2b6ce7', '#7c3aed', '#0891b2', '#059669', '#d97706', '#dc2626', '#db2777', '#4f46e5'];

const __providerIconPaths = Object.freeze({
  youtube: '/pwa/providers/youtube.svg',
  rumble: '/pwa/providers/rumble.svg',
  twitch: '/pwa/providers/twitch.svg',
  tiktok: '/pwa/providers/tiktok.svg',
  odysee: '/pwa/providers/odysee.svg',
  vimeo: '/pwa/providers/vimeo.svg',
  dailymotion: '/pwa/providers/dailymotion.svg',
  peertube: '/pwa/providers/peertube.svg',
  x: '/pwa/providers/x.svg',
  facebook: '/pwa/providers/facebook.svg',
  instagram: '/pwa/providers/instagram.svg',
  kick: '/pwa/providers/kick.svg',
});

const __providerIconDomains = Object.freeze([
  ['youtube', ['youtube.com', 'youtu.be', 'youtube-nocookie.com', 'youtubekids.com']],
  ['rumble', ['rumble.com']],
  ['twitch', ['twitch.tv']],
  ['tiktok', ['tiktok.com']],
  ['odysee', ['odysee.com', 'lbry.tv']],
  ['vimeo', ['vimeo.com']],
  ['dailymotion', ['dailymotion.com', 'dai.ly']],
  ['peertube', ['peertube.tv']],
  ['x', ['x.com', 'twitter.com']],
  ['facebook', ['facebook.com', 'fb.watch']],
  ['instagram', ['instagram.com']],
  ['kick', ['kick.com']],
]);

function _hostMatchesDomain(host, domain){
  const candidate = String(host || '').toLowerCase().replace(/\.$/, '');
  const expected = String(domain || '').toLowerCase().replace(/\.$/, '');
  return !!candidate && !!expected && (candidate === expected || candidate.endsWith(`.${expected}`));
}

function _providerIconKey(provider, host){
  let key = String(provider || '').trim().toLowerCase();
  if (key === 'twitter') key = 'x';
  if (__providerIconPaths[key]) return key;
  for (const [candidate, domains] of __providerIconDomains) {
    if (domains.some(domain => _hostMatchesDomain(host, domain))) return candidate;
  }
  return '';
}

/* Local-first provider badge: a letter tile rendered as an SVG data URI.
   Deterministic per host, needs no network and leaks nothing to third
   parties (the Google S2 favicon service used to see every queued domain). */
function _letterFaviconDataUri(host){
  const clean = String(host || '').replace(/^www\./, '');
  const letter = (clean[0] || '?').toUpperCase();
  let color = __favBrandColors[clean] || __favBrandColors[clean.split('.').slice(-2).join('.')] || '';
  if (!color) {
    let h = 0;
    for (let i = 0; i < clean.length; i++) h = (h * 31 + clean.charCodeAt(i)) >>> 0;
    color = __favPalette[h % __favPalette.length];
  }
  const svg = `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'><rect width='64' height='64' rx='14' fill='${color}'/><text x='32' y='43' font-family='system-ui,Segoe UI,sans-serif' font-size='32' font-weight='700' fill='#ffffff' text-anchor='middle'>${letter}</text></svg>`;
  return 'data:image/svg+xml;utf8,' + encodeURIComponent(svg);
}

function faviconUrl(input){
  const obj = (input && typeof input === 'object') ? input : null;
  const u = obj ? String(obj.url || '') : String(input || '');
  const provider = obj ? String(obj.provider || '').trim().toLowerCase() : '';
  if (provider === 'jellyfin' || _looksLikeJellyfinMediaUrl(u)) {
    return __jfServerType === 'emby' ? '/pwa/emby.svg' : '/pwa/jellyfin.svg';
  }
  const host = _safeUrlHost(u);
  const providerIcon = __providerIconPaths[_providerIconKey(provider, host)];
  if (providerIcon) return providerIcon;
  if (!host) return '';
  return _letterFaviconDataUri(host);
}

function displaySub(item){
  if (item && String(item.provider || '').trim().toLowerCase() === 'upload') {
    return _uploadSummary(item);
  }
  // Prefer channel/uploader when available; otherwise show a shortened URL host.
  const ch = item?.channel || '';
  if (ch) return ch;
  const u = item?.url || '';
  try {
    const uu = new URL(u);
    return uu.hostname || u;
  } catch (_){
    return u;
  }
}

function _uploadKind(item){
  const mime = String(item?.mime_type || '').trim().toLowerCase();
  if (mime.startsWith('audio/')) return 'Uploaded audio';
  if (mime.startsWith('video/')) return 'Uploaded video';
  return 'Uploaded media';
}

function _uploadRemovedCopy(item){
  const mime = String(item?.mime_type || '').trim().toLowerCase();
  if (mime.startsWith('audio/')) return 'Uploaded audio removed';
  if (mime.startsWith('video/')) return 'Uploaded video removed';
  return 'Uploaded media removed';
}

function _formatUploadSize(bytes){
  const raw = Number(bytes);
  if (!Number.isFinite(raw) || raw <= 0) return '';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = raw;
  let idx = 0;
  while (value >= 1024 && idx < units.length - 1) {
    value /= 1024;
    idx += 1;
  }
  const digits = value >= 100 || idx === 0 ? 0 : 1;
  return `${value.toFixed(digits)} ${units[idx]}`;
}

function _uploadSummary(item){
  if (!item || String(item.provider || '').trim().toLowerCase() !== 'upload') return '';
  const base = item.available === false ? _uploadRemovedCopy(item) : _uploadKind(item);
  const size = _formatUploadSize(item.size_bytes);
  return size ? `${base} • ${size}` : base;
}

function _hasNowPlayingItem(st, np){
  if (st && (st.playing || st.paused)) return true;
  const hasItem = !!(np && (np.title || np.url || np.stream));
  // Both /status and /playback/state now carry the authoritative flag; trust
  // it when present so the fast and full views cannot disagree at idle.
  if (st && typeof st.has_now_playing === 'boolean') return st.has_now_playing && hasItem;
  return hasItem;
}

function _isNowPlayingJellyfin(np){
  if (!np || typeof np !== 'object') return false;
  const provider = String(np.provider || '').trim().toLowerCase();
  if (provider === 'jellyfin') return true;
  if (String(np.jellyfin_item_id || '').trim()) return true;
  return _looksLikeJellyfinMediaUrl(String(np.url || ''));
}

function _isNowPlayingLive(np){
  if (!np || typeof np !== 'object') return false;
  const provider = String(np.provider || '').trim().toLowerCase();
  const liveStatus = String(np.live_status || '').trim().toLowerCase();
  if (provider === 'iptv') return true;
  if (np.is_live === true || np.live === true) return true;
  return liveStatus === 'is_live' || liveStatus === 'live';
}

function _labelNowAudioLanguage(np){
  const lang = String(
    (np && (np.jellyfin_audio_language || np.audio_language)) || ''
  ).trim();
  if (!lang) return 'Audio';
  return `Audio: ${lang.toUpperCase()}`;
}

function _labelNowSubtitleLanguage(np){
  const idx = String((np && np.jellyfin_subtitle_stream_index) || '').trim();
  const lang = String(
    (np && (np.jellyfin_subtitle_language || np.subtitle_language)) || ''
  ).trim();
  if (idx === '-1' || lang.toLowerCase() === 'off') return 'Subs: Off';
  if (!lang) return 'Subs';
  return `Subs: ${lang.toUpperCase()}`;
}

function _renderNowLanguageButton(st, np, hasNow){
  const btn = document.getElementById('nowLangBtn');
  if (!btn) return;
  const streamCount = Array.isArray(np && np.audio_streams) ? np.audio_streams.length : 0;
  const hasMultipleOrUnknown = (streamCount === 0) || (streamCount > 1);
  const show = !!(hasNow && _isNowPlayingJellyfin(np) && hasMultipleOrUnknown);
  btn.classList.toggle('hidden', !show);
  btn.disabled = !show;
  btn.textContent = _labelNowAudioLanguage(np);
}

function _renderNowSubtitleButton(st, np, hasNow){
  const btn = document.getElementById('nowSubLangBtn');
  if (!btn) return;
  const streamCount = Array.isArray(np && np.subtitle_streams) ? np.subtitle_streams.length : 0;
  const show = !!(hasNow && _isNowPlayingJellyfin(np) && streamCount > 0);
  btn.classList.toggle('hidden', !show);
  btn.disabled = !show;
  btn.textContent = _labelNowSubtitleLanguage(np);
}

function youtubeIdFromUrl(u){
  try {
    const uu = new URL(u);
    const host = (uu.hostname || '').toLowerCase();
    if (host.endsWith('youtu.be')) {
      const id = (uu.pathname || '').replace(/^\//,'').split('/')[0];
      return id || null;
    }
    if (host.includes('youtube.com')) {
      const v = uu.searchParams.get('v');
      if (v) return v;
      const p = uu.pathname || '';
      if (p.startsWith('/shorts/')) return p.split('/')[2] || null;
      if (p.startsWith('/embed/')) return p.split('/')[2] || null;
      if (p.startsWith('/live/')) return p.split('/')[2] || null;
    }
  } catch (_) {}
  return null;
}

function thumbUrl(item){
  // Prefer locally cached thumbnail, then upstream URL.
  const th = item?.thumbnail_local || item?.thumbnail || '';
  if (th) return th;

  const u = item?.url || '';
  const prov = item?.provider || '';
  if (prov === 'youtube') {
    const id = youtubeIdFromUrl(u);
    if (id) return `https://i.ytimg.com/vi/${encodeURIComponent(id)}/hqdefault.jpg`;
  }
  return '';
}

function setBg(el, imgUrl){
  if (!el) return;
  if (imgUrl) {
    el.classList.add('hasBg');
    // Overlay gradient keeps text readable over busy thumbs
    el.style.backgroundImage = `linear-gradient(to top, rgba(0,0,0,.45) 0%, rgba(0,0,0,.30) 40%, rgba(0,0,0,.10) 75%, rgba(0,0,0,.05) 100%), url('${imgUrl}')`;
  } else {
    el.classList.remove('hasBg');
    el.style.backgroundImage = '';
  }
}

function _setProgressFill(pct){
  const fill = document.getElementById('progFill');
  if (!fill) return;
  const clamped = Math.max(0, Math.min(1, pct));
  fill.style.width = `${(clamped*100).toFixed(2)}%`;
}

function _renderRemoteVolume(value, opts){
  const options = (opts && typeof opts === 'object') ? opts : {};
  const source = String(options.source || 'status');
  const label = document.getElementById('remoteVolValue');
  const slider = document.getElementById('remoteVolSlider');
  const num = Number(value);
  let safe = Number.isFinite(num) ? Math.max(0, Math.min(200, Math.round(num))) : null;
  const known = Number.isFinite(Number(__remoteVolumeKnownValue))
    ? Math.max(0, Math.min(200, Math.round(Number(__remoteVolumeKnownValue))))
    : null;
  if (safe === 0 && source !== 'user' && known != null && known > 0) {
    safe = known;
  }
  const effective = safe != null ? safe : known;
  if (slider) {
    if (effective != null && !slider.__draggingVolume) slider.value = String(effective);
    const liveDragValue = Math.max(0, Math.min(200, Number(slider.value || 100)));
    const base = slider.__draggingVolume ? liveDragValue : (effective != null ? effective : liveDragValue);
    slider.style.setProperty('--remote-vol-pct', `${((base / 200) * 100).toFixed(2)}%`);
    if (label) label.textContent = `${Math.round(base)}%`;
  } else if (label) {
    label.textContent = effective == null ? '--%' : `${effective}%`;
  }
  if (effective != null) {
    __remoteVolumeKnownValue = effective;
    try { localStorage.setItem('relaytv.remoteVolume', String(effective)); } catch (_e) {}
  }
}

function initRemoteVolumeSlider(){
  const slider = document.getElementById('remoteVolSlider');
  if (!slider || slider.__volumeBound) return;
  slider.__volumeBound = true;

  try {
    const cached = Number(localStorage.getItem('relaytv.remoteVolume'));
    if (Number.isFinite(cached)) {
      __remoteVolumeKnownValue = Math.max(0, Math.min(200, Math.round(cached)));
      _renderRemoteVolume(cached, {source:'cache'});
    }
  } catch (_e) {}

  const commit = async () => {
    const val = Math.max(0, Math.min(200, Number(slider.value || 0)));
    slider.__draggingVolume = false;
    _renderRemoteVolume(val, {source:'user'});
    await post('/volume', {set: val}, {idempotent: true});
  };

  slider.addEventListener('pointerdown', () => { slider.__draggingVolume = true; });
  slider.addEventListener('input', () => {
    slider.__draggingVolume = true;
    _renderRemoteVolume(slider.value, {source:'user'});
  });
  slider.addEventListener('change', commit);
  slider.addEventListener('pointerup', commit);
  slider.addEventListener('pointercancel', () => { slider.__draggingVolume = false; });
}

async function primeRemoteVolumeSlider(){
  try {
    if (__lastStatus && Number.isFinite(Number(__lastStatus.volume))) {
      _renderRemoteVolume(__lastStatus.volume, {source:'status'});
      return;
    }
    const r = await fetch('/status', {cache:'no-store'});
    if (!r.ok) return;
    const st = await r.json();
    if (st && Number.isFinite(Number(st.volume))) _renderRemoteVolume(st.volume, {source:'status'});
  } catch (_e) {}
}

function _updatePreviewTime(pct){
  // Show preview time while scrubbing
  const posEl = document.getElementById('pos');
  if (!posEl || !__lastStatus) return;
  if (_isNowPlayingLive(__lastStatus.now_playing)) return;
  const dur = __lastStatus.duration;
  if (dur == null || isNaN(dur) || dur <= 0) return;
  const sec = pct * dur;
  posEl.textContent = fmtTime(sec);
}

function _pctFromClientX(clientX){
  const bar = document.getElementById('progress');
  if (!bar) return 0;
  const rect = bar.getBoundingClientRect();
  const x = (clientX ?? 0) - rect.left;
  return Math.max(0, Math.min(1, x / Math.max(1, rect.width)));
}

async function _commitSeekFromPct(pct){
  if (!__lastStatus || !__lastStatus.playing) return;
  if (_isNowPlayingLive(__lastStatus.now_playing)) return;
  const dur = __lastStatus.duration;
  if (dur == null || isNaN(dur) || dur <= 0) return;
  const sec = pct * dur;
  await post('/seek_abs', {sec: sec}, {idempotent: true});
}

function initScrubber(){
  const bar = document.getElementById('progress');
  if (!bar) return;

  // Avoid double-binding if UI hot reloads
  if (bar.__scrubberBound) return;
  bar.__scrubberBound = true;

  bar.addEventListener('pointerdown', (e) => {
    if (!__lastStatus || !__lastStatus.playing) return;
    if (_isNowPlayingLive(__lastStatus.now_playing)) return;
    const dur = __lastStatus.duration;
    if (dur == null || isNaN(dur) || dur <= 0) return;
    if (typeof e.preventDefault === 'function') e.preventDefault();

    __scrubbing = true;
    __scrubPct = _pctFromClientX(e.clientX);
    _setProgressFill(__scrubPct);
    _updatePreviewTime(__scrubPct);
    const pointerId = e.pointerId;

    try { bar.setPointerCapture(pointerId); } catch (_) {}

    const onMove = (ev) => {
      if (!__scrubbing) return;
      if (typeof ev.preventDefault === 'function') ev.preventDefault();
      __scrubPct = _pctFromClientX(ev.clientX);
      _setProgressFill(__scrubPct);
      _updatePreviewTime(__scrubPct);
    };

    const onUp = async (ev) => {
      if (!__scrubbing) return;
      if (typeof ev.preventDefault === 'function') ev.preventDefault();
      __scrubbing = false;

      try { bar.releasePointerCapture(pointerId); } catch (_) {}

      // Commit seek on release
      const pct = _pctFromClientX(ev.clientX);
      _setProgressFill(pct);
      await _commitSeekFromPct(pct);

      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onUp);
    };

    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onUp);
  });
}

function _applyQueueSnapshot(payload){
  if (!payload || typeof payload !== 'object' || !Array.isArray(payload.queue)) return false;
  const next = (__lastStatus && typeof __lastStatus === 'object') ? {...__lastStatus} : {};
  next.queue = payload.queue;
  next.queue_length = Number(payload.queue_length ?? payload.queue.length ?? 0);
  _setStatusSnapshot(next);
  return true;
}

async function qRemove(index, queueId){
  try {
    const request = {index};
    if (queueId) request.queue_id = String(queueId);
    const res = await fetch('/queue/remove', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(request)});
    let payload = null;
    try { payload = await res.json(); } catch(_) {}
    if (!res.ok) {
      console.warn('queue/remove failed', res.status, payload);
    } else {
      _applyQueueSnapshot(payload);
    }
  } catch (e) {
    console.warn('queue/remove error', e);
  }
  await refresh();
}

async function qPlayNow(index, queueId){
  try {
    const request = {index};
    if (queueId) request.queue_id = String(queueId);
    const res = await fetch('/queue/play', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(request)});
    let payload = null;
    try { payload = await res.json(); } catch(_) {}
    if (!res.ok) {
      console.warn('queue/play failed', res.status, payload);
      _showToast((payload && payload.detail) ? String(payload.detail) : 'Could not play that item', {kind:'warn'});
    } else {
      _applyQueueSnapshot(payload);
    }
  } catch (e) {
    console.warn('queue/play error', e);
    _showToast('Could not play that item', {kind:'warn'});
  }
  await refresh();
}

/* ---- queue tile action menu (Play now / Send to device / Remove) ---- */
let __queueMenuEl = null;
let __queueMenuBackdrop = null;

function _queueMenuKeyHandler(ev){
  if (ev.key === 'Escape') {
    ev.stopPropagation();
    closeQueueMenu();
  }
}

function closeQueueMenu(){
  if (__queueMenuBackdrop) { __queueMenuBackdrop.remove(); __queueMenuBackdrop = null; }
  if (__queueMenuEl) { __queueMenuEl.remove(); __queueMenuEl = null; }
  document.removeEventListener('keydown', _queueMenuKeyHandler, true);
}

function _queueMenuItem(label, svgPath, onPick, cls){
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'qPopItem' + (cls ? ' ' + cls : '');
  btn.setAttribute('role', 'menuitem');
  btn.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${svgPath}" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg><span></span>`;
  btn.querySelector('span').textContent = label;
  btn.onclick = () => { closeQueueMenu(); onPick(); };
  return btn;
}

function openQueueMenu(index, item, anchor){
  closeQueueMenu();
  const backdrop = document.createElement('div');
  backdrop.className = 'qPopBackdrop';
  backdrop.addEventListener('pointerdown', (ev) => { ev.preventDefault(); closeQueueMenu(); });

  const menu = document.createElement('div');
  menu.className = 'qPopMenu';
  menu.setAttribute('role', 'menu');
  menu.setAttribute('aria-label', 'Queue item actions');
  const queueId = String((item && item.queue_id) || '');
  menu.appendChild(_queueMenuItem('Play now', 'M8 5.5v13l11-6.5z', () => qPlayNow(index, queueId)));
  menu.appendChild(_queueMenuItem('Send to device', 'M4 12h13m0 0-4.5-4.5M17 12l-4.5 4.5M20 5v14', () => {
    if (window.relaytvPeers) window.relaytvPeers.open({index, queueId, title: (item && (item.title || item.url)) || ''});
  }));
  const queue = Array.isArray(__lastStatus && __lastStatus.queue) ? __lastStatus.queue : [];
  const moveUp = _queueMenuItem('Move up', 'M12 19V5m0 0-5 5m5-5 5 5', () => {
    const target = queue[index - 1] || {};
    qMove(index, index - 1, queueId, target.queue_id);
  });
  moveUp.disabled = index <= 0;
  menu.appendChild(moveUp);
  const moveDown = _queueMenuItem('Move down', 'M12 5v14m0 0-5-5m5 5 5-5', () => {
    const target = queue[index + 1] || {};
    qMove(index, index + 1, queueId, target.queue_id);
  });
  moveDown.disabled = index >= queue.length - 1;
  menu.appendChild(moveDown);
  menu.appendChild(_queueMenuItem('Remove', 'M5 7h14M10 7V5h4v2m-7 0 1 12h8l1-12', () => qSoftRemove(index, queueId), 'danger'));

  document.body.appendChild(backdrop);
  document.body.appendChild(menu);
  __queueMenuBackdrop = backdrop;
  __queueMenuEl = menu;
  document.addEventListener('keydown', _queueMenuKeyHandler, true);

  const r = anchor.getBoundingClientRect();
  const mw = menu.offsetWidth;
  const mh = menu.offsetHeight;
  let left = Math.min(r.right - mw, window.innerWidth - mw - 8);
  left = Math.max(8, left);
  let top = r.bottom + 6;
  if (top + mh > window.innerHeight - 8) top = Math.max(8, r.top - mh - 6);
  menu.style.left = left + 'px';
  menu.style.top = top + 'px';
  const first = menu.querySelector('button');
  if (first) first.focus({preventScroll: true});
}

/* ---- undo toast + soft remove ---- */
let __toastEl = null;
let __toastTimer = null;

function _hideToast(){
  if (__toastTimer) { clearTimeout(__toastTimer); __toastTimer = null; }
  if (__toastEl) { __toastEl.remove(); __toastEl = null; }
}

function _showToast(label, {actionLabel = null, onAction = null, duration = 4000, kind = 'info'} = {}){
  _hideToast();
  const el = document.createElement('div');
  el.className = 'qToast ' + kind;
  el.setAttribute('role', 'status');
  const span = document.createElement('span');
  span.className = 'qToastLabel';
  span.textContent = label;
  el.appendChild(span);
  if (actionLabel && onAction) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'qToastAction';
    btn.textContent = actionLabel;
    btn.onclick = () => { onAction(); };
    el.appendChild(btn);
  }
  document.body.appendChild(el);
  __toastEl = el;
  if (duration > 0) __toastTimer = setTimeout(_hideToast, duration);
  return el;
}

/* Soft remove: the tile collapses immediately but the server delete only
   fires when the undo window closes. If the tab goes away first, the item
   simply stays queued — the safer failure direction. */
let __pendingRemove = null;
let __removeFlushPromise = null;

function _commitPendingRemove(pending){
  if (!pending) return Promise.resolve();
  const flush = Promise.resolve(qRemove(pending.index, pending.queueId)).finally(() => {
    if (__removeFlushPromise === flush) __removeFlushPromise = null;
    if (typeof __lastStatus !== 'undefined' && __lastStatus &&
        typeof _uiRefreshInteractionLockActive === 'function' && !_uiRefreshInteractionLockActive()) {
      renderStatus(__lastStatus);
    }
  });
  __removeFlushPromise = flush;
  return flush;
}

function _flushPendingRemove(){
  if (__removeFlushPromise) return __removeFlushPromise;
  if (!__pendingRemove) return Promise.resolve();
  const pending = __pendingRemove;
  __pendingRemove = null;
  clearTimeout(pending.timer);
  return _commitPendingRemove(pending);
}

function qSoftRemove(index, queueId){
  if (__removeFlushPromise) {
    _showToast('Finishing the previous removal…', {duration: 2500});
    return;
  }
  if (__pendingRemove) {
    _flushPendingRemove();
    // The selected index came from the pre-removal queue. Do not reuse it
    // after the first request shifts the server-side indexes; the refreshed
    // queue will expose a safe target for the user's next action.
    _showToast('Previous removal saved — select Remove again', {duration: 3000});
    return;
  }
  const tile = document.querySelector(`.qTile[data-index="${index}"]`);
  if (tile) tile.classList.add('qPendingRemove');
  const timer = setTimeout(() => {
    const pending = __pendingRemove;
    __pendingRemove = null;
    _hideToast();
    if (pending) _commitPendingRemove(pending);
  }, 6000);
  __pendingRemove = { index, queueId: String(queueId || ''), timer };
  _showToast('Removed from queue', {
    actionLabel: 'Undo',
    duration: 6000,
    onAction: () => {
      if (__pendingRemove) clearTimeout(__pendingRemove.timer);
      __pendingRemove = null;
      _hideToast();
      const t = document.querySelector(`.qTile[data-index="${index}"]`);
      if (t) t.classList.remove('qPendingRemove');
      if (typeof __lastStatus !== 'undefined' && __lastStatus &&
          typeof _uiRefreshInteractionLockActive === 'function' && !_uiRefreshInteractionLockActive()) {
        renderStatus(__lastStatus);
      }
    },
  });
}

async function qMove(from_index, to_index, queueId, toQueueId){
  try {
    const request = {from_index, to_index};
    if (queueId) request.queue_id = String(queueId);
    if (toQueueId) request.to_queue_id = String(toQueueId);
    const res = await fetch('/queue/move', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(request)});
    let payload = null;
    try { payload = await res.json(); } catch(_) {}
    if (!res.ok) {
      console.warn('queue/move failed', res.status, payload);
    } else {
      _applyQueueSnapshot(payload);
    }
  } catch (e) {
    console.warn('queue/move error', e);
  }
  await refresh();
}

const __UI_FALLBACK_REFRESH_MS = 8000;
const __NOW_IDLE_DEBOUNCE_MS = 4000;
let __nowIdleSinceTs = 0;
let __nowIdleSettleTimer = 0;
let __nowLastShownNp = null;
const __UI_EVENT_RECONNECT_MS = 5000;
const __UI_TRANSPORT_UPGRADE_MS = 300000;

function renderStatus(st) {
  if (!st) return;
  if (_uiRefreshInteractionLockActive()) return;
  _jfSetLaunchVisible(_jfCanLaunchFromStatus(st));
  applyJfBranding(st.jellyfin_server_type, st.jellyfin_server_url_configured);
  if (typeof window.iptvUpdateLaunch === 'function') window.iptvUpdateLaunch(st);

  // state pill
  const dot = document.getElementById('dot');
  const state = document.getElementById('state');
  const brand = document.getElementById('appBrandName');
  const sess = st.state || (st.playing ? (st.paused ? 'paused' : 'playing') : 'idle');
  if (brand) brand.textContent = st.device_name || 'RelayTV';
  const menuDev = document.getElementById('menuDeviceName');
  if (menuDev) menuDev.textContent = st.device_name || 'RelayTV';
  if (dot) {
    dot.className = 'dot' + (sess === 'playing' ? ' playing' : (sess === 'paused' ? ' paused' : (sess === 'closed' ? ' closed' : '')));
  }
  if (state) state.textContent = sess;

  // now playing
  let np = st.now_playing || {};
  const picon = document.getElementById('picon');
  // Debounce the idle flip: a transient idle signal (transition gap, fast/full
  // disagreement) must not blank the card. Content applies instantly; idle
  // only lands after the signal has been stable for a few seconds, and the
  // last-shown item stays frozen on screen during that window.
  let hasNow = _hasNowPlayingItem(st, np);
  if (!hasNow){
    if (!__nowIdleSinceTs){
      __nowIdleSinceTs = Date.now();
      if (!__nowIdleSettleTimer){
        __nowIdleSettleTimer = window.setTimeout(() => {
          __nowIdleSettleTimer = 0;
          if (__lastStatus) renderStatus(__lastStatus);
        }, __NOW_IDLE_DEBOUNCE_MS + 300);
      }
    }
    if (((Date.now() - __nowIdleSinceTs) < __NOW_IDLE_DEBOUNCE_MS) && __nowLastShownNp){
      hasNow = true;
      np = __nowLastShownNp;
    }
  } else {
    __nowIdleSinceTs = 0;
    if (__nowIdleSettleTimer){ window.clearTimeout(__nowIdleSettleTimer); __nowIdleSettleTimer = 0; }
  }
  if (hasNow) __nowLastShownNp = np;
  else __nowLastShownNp = null;
  const fav = hasNow ? faviconUrl(np) : '/pwa/brand/logo.svg';
  picon.innerHTML = fav ? `<img src="${fav}" alt="" />` : '🎞️';
  document.getElementById('now').textContent = hasNow ? (np.title || 'Now Playing') : 'Ready';
  document.getElementById('nowSub').textContent = hasNow ? (displaySub(np) || '') : '';
  if (picon) picon.classList.toggle('hidden', !hasNow);
  _renderNowLanguageButton(st, np, hasNow);
  _renderNowSubtitleButton(st, np, hasNow);
  const nowSkipBtn = document.getElementById('nowSkipBtn');
  if (nowSkipBtn) {
    const canSkipNow = !!hasNow;
    nowSkipBtn.classList.toggle('hidden', !canSkipNow);
    nowSkipBtn.onclick = async (e) => {
      try { if (e) e.preventDefault(); } catch(_){}
      await post('/now_playing/clear');
    };
  }

  // hero artwork (YouTube supported; others fall back to glass gradient)
  setBg(document.getElementById('nHeroArt'), hasNow ? thumbUrl(np) : '');

  const nowCardEl = document.getElementById('nowTopCard');
  const paused = !!st.paused && hasNow;
  const activelyPlaying = !!st.playing && !st.paused && hasNow;
  const liveNow = !!hasNow && _isNowPlayingLive(np);
  // Ended/closed: an item is still on the card but playback is down and no
  // transition is in flight. This is the state that used to render as a live
  // card with dead "--:--" times.
  const ended = hasNow && !st.playing && !st.paused
    && !st.transitioning_between_items && !st.transition_in_progress
    && String(st.playback_runtime_state || '') !== 'buffering';
  const resumePos = (np && typeof np.resume_pos === 'number' && Number.isFinite(np.resume_pos)) ? np.resume_pos : null;
  const npDuration = (np && typeof np.duration_sec === 'number' && Number.isFinite(np.duration_sec) && np.duration_sec > 0) ? np.duration_sec : null;
  const effDuration = st.duration != null ? st.duration : (ended ? npDuration : null);
  if (nowCardEl){
    nowCardEl.classList.toggle('isIdle', !hasNow);
    nowCardEl.classList.toggle('isPaused', paused);
    nowCardEl.classList.toggle('isLive', liveNow);
    nowCardEl.classList.toggle('isEnded', ended);
  }
  const stateTag = document.getElementById('nowStateTag');
  if (stateTag) {
    stateTag.textContent = paused ? 'Paused' : (ended ? 'Ended' : 'Live');
    stateTag.classList.toggle('hidden', !paused && !liveNow && !ended);
    stateTag.classList.toggle('live', liveNow && !paused);
  }
  const stateDot = document.getElementById('nowStateDot');
  if (stateDot) {
    stateDot.classList.toggle('playing', activelyPlaying);
    stateDot.classList.toggle('live', liveNow && activelyPlaying);
  }

  const posTxt = liveNow ? 'LIVE' : fmtTime((ended && st.position == null && resumePos != null) ? resumePos : st.position);
  const durTxt = liveNow ? (paused ? 'Paused' : 'Streaming') : fmtTime(effDuration);

  // Only overwrite the pos readout if not scrubbing
  if (!__scrubbing) document.getElementById('pos').textContent = posTxt;
  document.getElementById('dur').textContent = durTxt;
  const progressEl = document.getElementById('progress');
  if (progressEl) {
    progressEl.title = liveNow ? 'Live stream' : 'Drag to seek (or tap)';
    progressEl.setAttribute('aria-disabled', liveNow ? 'true' : 'false');
  }

  _renderRemoteVolume(st.volume);
  const mute = !!st.mute;
  const mb = document.getElementById('muteBtn');
  if (mb){
    mb.classList.toggle('muted', mute);
    const muteLbl = mb.querySelector('.rLabel');
    if (muteLbl) muteLbl.textContent = mute ? 'Unmute' : 'Mute';
  }
  const ppb = document.getElementById('playPauseBtn');
  if (ppb) {
    ppb.classList.toggle('isPlaying', !!st.playing && !st.paused);
    // In the ended state the button resumes the closed session; say so.
    const ppLbl = ppb.querySelector('.rLabel');
    if (ppLbl) ppLbl.textContent = ended ? 'Resume' : 'Play/Pause';
  }
  const qCount = document.getElementById('queueCount');
  if (qCount) qCount.textContent = String(st.queue_length || 0);
  const qClear = document.getElementById('queueClearBtn');
  if (qClear) qClear.classList.toggle('hidden', !(Number(st.queue_length) > 0));
  // Sending needs something to send, which includes a lone playing item with an
  // empty queue; peers.js owns the sheet behind this button.
  const qSend = document.getElementById('queueSendBtn');
  const canSend = Number(st.queue_length) > 0 || !!st.playing || !!st.paused;
  if (qSend) qSend.classList.toggle('hidden', !canSend);
  // The sheet lists what is playing alongside the queue, so an open sheet has to
  // hear about playback and queue changes.
  if (window.relaytvPeers && window.relaytvPeers.syncPlayback) window.relaytvPeers.syncPlayback();

  // progress bar fill
  if (!__scrubbing && liveNow) {
    _setProgressFill(0);
  } else if (!__scrubbing && ended && resumePos != null && effDuration != null && effDuration > 0) {
    // Show where a resume would pick up, not an empty bar.
    _setProgressFill(resumePos / effDuration);
  } else if (!__scrubbing && st.position != null && st.duration != null && st.duration > 0) {
    _setProgressFill(st.position / st.duration);
  } else if (!__scrubbing && (!st.playing || st.duration == null || st.duration <= 0)) {
    _setProgressFill(0);
  }

  // up-next strip: the queue's head with a direct play action, shown whenever
  // nothing live is on the card (ended item or empty idle card).
  const upNextEl = document.getElementById('nowUpNext');
  if (upNextEl) {
    const nextItem = (Array.isArray(st.queue) && st.queue.length) ? st.queue[0] : null;
    const showUpNext = !!nextItem && (ended || !hasNow);
    upNextEl.classList.toggle('hidden', !showUpNext);
    if (showUpNext) {
      const titleEl = document.getElementById('upNextTitle');
      if (titleEl) titleEl.textContent = nextItem.title || nextItem.url || '';
      const thumbEl = document.getElementById('upNextThumb');
      if (thumbEl) {
        const tu = thumbUrl(nextItem);
        thumbEl.style.backgroundImage = tu ? `url('${tu}')` : '';
        thumbEl.classList.toggle('hasThumb', !!tu);
      }
      const playBtn = document.getElementById('upNextPlayBtn');
      if (playBtn) playBtn.onclick = () => qPlayNow(0, nextItem.queue_id);
    }
  }

  // queue list
  const ol = document.getElementById('queue');

  // If a drag got stuck (e.g., pointerup missed), recover so UI keeps rendering.
  if (__draggingQueue && __dragStartTs && (Date.now() - __dragStartTs) > 8000) {
    try { if (typeof __queueDnDCleanup === 'function') __queueDnDCleanup(); } catch(_e) {}
  }

  if (!__draggingQueue) {
    // The re-render detaches the tile the menu was anchored to.
    closeQueueMenu();
    ol.innerHTML = '';
    (st.queue || []).forEach((item, idx) => {
    const li = document.createElement('li');
    li.className = 'qTile';
    if (item && item.available === false) li.classList.add('isUnavailable');
    li.dataset.index = String(idx);
    li.dataset.queueId = String((item && item.queue_id) || '');

    // Contained 16:9 artwork (not a background: text stays on clean glass)
    const thumb = document.createElement('div');
    thumb.className = 'qThumb';
    const turl = thumbUrl(item);
    if (turl){
      const art = document.createElement('img');
      art.className = 'qThumbImg';
      art.alt = '';
      art.loading = 'lazy';
      art.src = turl;
      art.onerror = () => { try { art.remove(); } catch(_e) {} };
      thumb.appendChild(art);
    }
    const thumbFav = faviconUrl(item);
    if (thumbFav){
      const favBadge = document.createElement('img');
      favBadge.className = 'qThumbFav';
      favBadge.alt = '';
      favBadge.loading = 'lazy';
      favBadge.src = thumbFav;
      favBadge.onerror = () => { try { favBadge.remove(); } catch(_e) {} };
      thumb.appendChild(favBadge);
    }

    // Drag handle (hamburger)
    const handle = document.createElement('button');
    handle.className = 'qHandle';
    handle.type = 'button';
    handle.innerHTML = `
      <svg class="qGrip" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
        <path d="M8 6h8M8 12h8M8 18h8" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
      </svg>`;
    handle.title = 'Drag to reorder; use Arrow keys to move';
    handle.setAttribute('aria-label', `Reorder ${item.title || item.url || 'queue item'}`);
    handle.addEventListener('keydown', (event) => {
      if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
      const toIndex = event.key === 'ArrowUp' ? idx - 1 : idx + 1;
      if (toIndex < 0 || toIndex >= (st.queue || []).length) return;
      event.preventDefault();
      const target = st.queue[toIndex] || {};
      qMove(idx, toIndex, item.queue_id, target.queue_id).catch(() => {});
    });

    const body = document.createElement('div');
    body.className = 'qBody';

    const title = document.createElement('div');
    title.className = 'qTitle';

    const tspan = document.createElement('span');
    tspan.className = 'qTitleText';
    tspan.textContent = item.title || item.url || '';

    title.appendChild(tspan);
    const titleBadge = _uploadBadge(item);
    if (titleBadge) title.insertAdjacentHTML('beforeend', titleBadge);

    const chan = document.createElement('div');
    chan.className = 'qChan';
    chan.textContent = displaySub(item) || '';
    if (item && item.available === false){
      const tag = document.createElement('span');
      tag.className = 'qUnavailTag';
      tag.textContent = 'unavailable';
      chan.appendChild(tag);
    }

    body.appendChild(title);
    body.appendChild(chan);

    // One indirection into the tile's actions keeps the tile clean; a button
    // per action would not scale and crams tap targets together.
    const menuBtn = document.createElement('button');
    menuBtn.className = 'qMenuBtn';
    menuBtn.type = 'button';
    menuBtn.title = 'Item actions';
    menuBtn.setAttribute('aria-label', 'Item actions');
    menuBtn.setAttribute('aria-haspopup', 'menu');
    menuBtn.textContent = '⋯';
    menuBtn.onclick = () => openQueueMenu(idx, item, menuBtn);

    li.appendChild(thumb);
    li.appendChild(body);
    li.appendChild(handle);
    li.appendChild(menuBtn);
    ol.appendChild(li);
  });
  }

  // Bind once (event delegation on the <ol>)
  bindQueuePointerDnD();

}

async function refresh() {
  let st = __lastStatus || null;
  let fast = null;
  let reachedServer = false;
  try {
    fast = await _fetchFastPlaybackState();
    reachedServer = true;
    st = _mergePlaybackStateIntoStatus(st, fast);
  } catch(_e) {}

  try {
    if (_shouldRefreshFullStatus(st, fast)) {
      const full = await _fetchFullStatus();
      reachedServer = true;
      st = fast ? _mergePlaybackStateIntoStatus(full, fast) : full;
    }
  } catch(_e) {}

  _connSignal(reachedServer);
  if (!st) return;
  _setStatusSnapshot(st);
  renderStatus(st);
}

function _applyUiPlaybackEvent(payload){
  if (!payload || typeof payload !== 'object') return;
  _uiEventMarkAlive();
  const merged = _mergePlaybackStateIntoStatus(__lastStatus || {}, payload);
  _setStatusSnapshot(merged);
  renderStatus(merged);
}

function _applyUiStatusEvent(payload){
  if (!payload || typeof payload !== 'object') return;
  _uiEventMarkAlive();
  _setStatusSnapshot(payload);
  __lastStatusFullFetchTs = Date.now();
  renderStatus(payload);
}

function _applyUiQueueEvent(payload){
  if (!payload || typeof payload !== 'object') return;
  _uiEventMarkAlive();
  const applied = _applyQueueSnapshot(payload);
  if (applied && __lastStatus) renderStatus(__lastStatus);
  if (!applied || _uiRefreshInteractionLockActive()) {
    refresh().catch(() => {});
  }
}

function _applyUiJellyfinEvent(payload){
  if (!payload || typeof payload !== 'object') return;
  _uiEventMarkAlive();

  const settingsBd = document.getElementById('settingsBackdrop');
  const settingsOpen = !!(settingsBd && !settingsBd.classList.contains('hidden'));
  if (payload.refresh_settings && settingsOpen) {
    loadSettingsUi().catch(console.warn);
  }

  if (!payload.refresh_active_tab || !__jfUiVisible) {
    if (payload.refresh_status) refresh().catch(() => {});
    return;
  }

  if (__jfBusy) {
    window.setTimeout(() => _applyUiJellyfinEvent(payload), 700);
    return;
  }

  if (__jfLastMode === 'search' && __jfLastQuery) {
    runJellyfinSearch(true).catch(console.warn);
  } else if (__jfActiveTab === 'tv' && __jfTvViewMode === 'detail' && __jfTvSeriesId) {
    loadJellyfinTvSeriesDetail(__jfTvSeriesId, {
      title: __jfTvSeriesTitle,
      thumbnail: __jfTvSeriesThumb,
      thumbnail_local: __jfTvSeriesThumb,
      refresh: true,
    }).catch(console.warn);
  } else {
    _jfLoadActiveTabDefault(true);
  }

  if (__jfSelectedItemId && _jfIsDetailOpen()) {
    loadJellyfinDetail(__jfSelectedItemId, {keepDetail:true}).catch(console.warn);
  }
}

async function _loadUiRealtimeCapabilities(options){
  const settings = (options && typeof options === 'object') ? options : {};
  __uiRealtimeCapabilities = await __uiRealtimePolicy.discover(async()=>{
      const response = await _fetchWithTimeout('/realtime/capabilities', {cache:'no-store'}, 2500);
      if (!response.ok) {
        const error = new Error(`realtime capabilities ${response.status}`);
        error.status = response.status;
        error.legacy = response.status === 404;
        throw error;
      }
      const payload = await response.json();
      if (!payload || Number(payload.protocol_version) !== 1) {
        const error = new Error('unsupported realtime protocol');
        error.cacheFallback = true;
        throw error;
      }
      return payload;
  }, {force:settings.force === true});
  return __uiRealtimeCapabilities;
}

function _dispatchUiRealtimeEvent(eventName, payload, sequence){
  const name = String(eventName || '').trim();
  if (!name) return;
  _uiEventMarkAlive();
  const sequenceDecision = __uiRealtimeSequence.accept(name, sequence);
  if (sequenceDecision.gap) refresh().catch(() => {});
  if (!sequenceDecision.apply) return;
  if (name === 'hello') {
    if (!__lastStatus) refresh().catch(() => {});
  } else if (name === 'ping') {
    return;
  } else if (name === 'playback') {
    _applyUiPlaybackEvent(payload);
  } else if (name === 'status') {
    _applyUiStatusEvent(payload);
  } else if (name === 'queue') {
    _applyUiQueueEvent(payload);
  } else if (name === 'jellyfin') {
    _applyUiJellyfinEvent(payload);
  }
}

function _retireUiRealtimeTransport(active, opts){
  if (__uiEventSource !== active || active.generation !== __uiEventGeneration) return;
  const options = (opts && typeof opts === 'object') ? opts : {};
  __uiEventSource = null;
  __uiEventGeneration += 1;
  try { active.handle.close(); } catch (_e) {}
  __uiRealtimePolicy.retire(
    active.kind,
    String(options.reason || 'closed'),
    active.websocketAttempt,
  );
  _scheduleUiEventReconnect();
}

function _openUiSseTransport(capabilities, generation){
  if (generation !== __uiEventGeneration || __uiEventSource) return;
  const path = String(capabilities?.sse?.ui || '/ui/events');
  let es = null;
  try {
    es = new EventSource(path);
  } catch (_e) {
    _scheduleUiEventReconnect();
    return;
  }
  const active = {kind:'sse', handle:es, generation};
  __uiEventSource = active;
  window.__relaytvRealtimeTransport = 'sse';
  ['hello', 'ping', 'playback', 'status', 'queue', 'jellyfin'].forEach((name) => {
    es.addEventListener(name, (ev) => {
      if (__uiEventSource !== active || generation !== __uiEventGeneration) return;
      _dispatchUiRealtimeEvent(name, _parseUiEventPayload(ev), 0);
    });
  });
  es.onerror = () => _retireUiRealtimeTransport(active, {reason:'error'});
}

function _openUiWebSocketTransport(capabilities, generation){
  if (generation !== __uiEventGeneration || __uiEventSource) return;
  const configuredPath = String(capabilities?.websocket?.ui || '/ui/ws');
  const socketUrl = new URL(configuredPath, window.location.href);
  socketUrl.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  let socket = null;
  try {
    socket = new WebSocket(socketUrl.toString(), 'relaytv.realtime.v1');
  } catch (_e) {
    __uiRealtimePolicy.retire('websocket', 'open_error', null);
    _openUiSseTransport(capabilities, generation);
    return;
  }
  const active = {
    kind:'websocket',
    handle:socket,
    generation,
    websocketAttempt:__uiRealtimePolicy.createWebSocketAttempt(capabilities),
  };
  __uiEventSource = active;
  window.__relaytvRealtimeTransport = 'websocket';
  socket.onmessage = (ev) => {
    if (__uiEventSource !== active || generation !== __uiEventGeneration) return;
    let envelope = null;
    try { envelope = JSON.parse(ev.data || '{}'); }
    catch (_e) {
      _retireUiRealtimeTransport(active, {reason:'protocol_error'});
      return;
    }
    const accepted = __uiRealtimePolicy.acceptWebSocketEnvelope(active.websocketAttempt, envelope);
    if (!accepted) {
      _retireUiRealtimeTransport(active, {reason:'protocol_error'});
      return;
    }
    _dispatchUiRealtimeEvent(accepted.event, accepted.payload, accepted.sequence);
  };
  socket.onerror = () => {};
  socket.onclose = () => _retireUiRealtimeTransport(active, {reason:'closed'});
}

async function _connectUiRealtimeGeneration(generation){
  const capabilities = await _loadUiRealtimeCapabilities();
  if (generation !== __uiEventGeneration || __uiEventSource) return;
  const selected = __uiRealtimePolicy.select(capabilities, {
    websocketAvailable:typeof window.WebSocket === 'function',
  });
  if (selected === 'websocket') {
    _openUiWebSocketTransport(capabilities, generation);
  } else {
    _openUiSseTransport(capabilities, generation);
  }
}

function connectUiEventStream(){
  if (__uiEventSource || __uiEventConnectInFlight) return;
  const generation = ++__uiEventGeneration;
  __uiEventConnectInFlight = true;
  // Birth is only a connection grace clock. Health starts with a real message.
  __uiEventSourceBornTs = Date.now();
  __uiEventSourceLastTs = 0;
  _connectUiRealtimeGeneration(generation)
    .catch(() => _scheduleUiEventReconnect())
    .finally(() => {
      if (generation === __uiEventGeneration) __uiEventConnectInFlight = false;
    });
}

// --- History modal (hidden by default)
async function fetchHistory(){
  const r = await fetch('/history', {cache:'no-store'});
  return await r.json();
}

function closeHeaderMenu(){
  const panel = document.getElementById('hdrMenuPanel');
  const btn = document.getElementById('hdrMenuBtn');
  if (panel) panel.classList.add('hidden');
  if (btn) btn.setAttribute('aria-expanded', 'false');
}

let __menuFootVersionLoaded = false;
async function _loadMenuFootVersion(){
  if (__menuFootVersionLoaded) return;
  __menuFootVersionLoaded = true;
  try {
    const r = await fetch('/app/info', {cache:'no-store'});
    if (!r.ok) throw new Error('status ' + r.status);
    const info = await r.json();
    const v = String(info.version || info.release_version || '').trim();
    const el = document.getElementById('menuAppVersion');
    if (el && v) el.textContent = /^\d/.test(v) ? `v${v}` : v;
  } catch (_e) {
    __menuFootVersionLoaded = false;
  }
}

function bindHeaderMenu(){
  const wrap = document.getElementById('hdrMenuWrap');
  const btn = document.getElementById('hdrMenuBtn');
  const panel = document.getElementById('hdrMenuPanel');
  if (!btn || !panel || !wrap) return;

  btn.onclick = (e) => {
    try { if (e) e.preventDefault(); } catch(_){}
    const isHidden = panel.classList.contains('hidden');
    panel.classList.toggle('hidden', !isHidden);
    btn.setAttribute('aria-expanded', isHidden ? 'true' : 'false');
    if (isHidden) _loadMenuFootVersion().catch(() => {});
  };
  panel.addEventListener('pointerdown', (e) => {
    try { e.stopPropagation(); } catch(_){}
  });
  panel.addEventListener('click', (e) => {
    try { e.stopPropagation(); } catch(_){}
  });

  document.addEventListener('click', (e) => {
    if (panel.classList.contains('hidden')) return;
    const t = e && e.target;
    if (t && t.closest && t.closest('#hdrMenuWrap')) return;
    closeHeaderMenu();
  });
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeHeaderMenu();
  });
}

function _fmtTs(ts){
  // Compact: time-only today, short date within the year, year beyond that.
  // The verbose locale string used to eat the whole meta line and truncate
  // the mode suffix ("auto ne…") it shares the row with.
  try {
    const d = new Date((ts||0)*1000);
    if (isNaN(d.getTime())) return '';
    const now = new Date();
    if (d.toDateString() === now.toDateString()) {
      return d.toLocaleTimeString([], {hour:'numeric', minute:'2-digit'});
    }
    if (d.getFullYear() === now.getFullYear()) {
      return d.toLocaleString([], {month:'short', day:'numeric', hour:'numeric', minute:'2-digit'});
    }
    return d.toLocaleString([], {month:'short', day:'numeric', year:'numeric'});
  } catch (_) { return ''; }
}

function _fmtTsFull(ts){
  try {
    const d = new Date((ts||0)*1000);
    if (isNaN(d.getTime())) return '';
    return d.toLocaleString();
  } catch (_) { return ''; }
}

function _uploadBadge(item){
  if (!item || String(item.provider || '').trim().toLowerCase() !== 'upload') return '';
  const unavailable = item.available === false;
  return `<span class="mediaBadge${unavailable ? ' unavailable' : ''}">${unavailable ? 'Removed' : 'Uploaded'}</span>`;
}

function openHistory(){
  closeHeaderMenu();
  const bd = document.getElementById('histBackdrop');
  if (!bd || !bd.classList.contains('hidden')) return;
  bd.classList.remove('hidden');
  _uiPushLayer();
  renderHistory();
}

function closeHistory(opts){
  const bd = document.getElementById('histBackdrop');
  if (!bd) return;
  const fromNav = !!(opts && opts.fromNav);
  if (!fromNav && !bd.classList.contains('hidden') && __layerNavigation.getDepth() > 0) {
    try { history.back(); } catch (_e) {}
    return;
  }
  bd.classList.add('hidden');
}

async function renderHistory(){
  const list = document.getElementById('histList');
  if (!list) return;
  list.innerHTML = '';

  const data = await fetchHistory();
  const items = data.history || [];
  if (!items.length){
    const empty = document.createElement('div');
    empty.className = 'muted';
    empty.textContent = 'No history yet.';
    list.appendChild(empty);
    return;
  }

  items.forEach((it, idx) => {
    const available = it && it.available !== false;
    const row = document.createElement('div');
    row.className = 'histItem';
    if (!available) row.classList.add('isUnavailable');

    const thumb = document.createElement('div');
    thumb.className = 'histThumb';
    const turl = thumbUrl(it);
    if (turl){
      const img = document.createElement('img');
      img.className = 'histThumbImg';
      img.alt = '';
      img.loading = 'lazy';
      img.src = turl;
      img.onerror = () => { try { img.remove(); } catch(_e){} };
      thumb.appendChild(img);
    }
    const fav = faviconUrl(it);
    if (fav){
      const badge = document.createElement('img');
      badge.className = 'histThumbFav';
      badge.alt = '';
      badge.loading = 'lazy';
      badge.src = fav;
      badge.onerror = () => { try { badge.remove(); } catch(_e){} };
      thumb.appendChild(badge);
    }

    const resumePos = Number(it.resume_pos);
    const duration = Number(it.duration_sec);
    const progressRatio = (it.completed === true) ? 0 : (resumePos / duration);
    if (Number.isFinite(progressRatio) && progressRatio > 0 && progressRatio < 1) {
      const bar = document.createElement('div');
      bar.className = 'histProgress';
      const fill = document.createElement('span');
      fill.style.width = `${Math.max(0, Math.min(100, progressRatio * 100))}%`;
      bar.appendChild(fill);
      thumb.appendChild(bar);
    }

    const meta = document.createElement('div');
    meta.className = 'histMeta';

    const title = document.createElement('div');
    title.className = 'histTitle';
    const tspan = document.createElement('span');
    tspan.className = 'histTitleText';
    tspan.textContent = it.title || it.url || '(unknown)';
    title.appendChild(tspan);
    const titleBadge = _uploadBadge(it);
    if (titleBadge) title.insertAdjacentHTML('beforeend', titleBadge);

    const channel = document.createElement('div');
    channel.className = 'histSub';
    channel.textContent = displaySub(it) || '';

    const when = document.createElement('div');
    when.className = 'histSub';
    when.textContent = `${_fmtTs(it.ts)} · ${String(it.mode || '').replace(/_/g, ' ')}`.replace(/ · $/, '');
    when.title = _fmtTsFull(it.ts);

    const tags = document.createElement('div');
    tags.className = 'histTags';
    if (it.completed === true){
      const t = document.createElement('span');
      t.className = 'histTag done';
      t.textContent = 'Completed';
      tags.appendChild(t);
    } else if (Number.isFinite(resumePos) && resumePos > 0){
      const t = document.createElement('span');
      t.className = 'histTag resume';
      t.textContent = `Resume ${fmtTime(resumePos)}`;
      tags.appendChild(t);
    }
    if (!available){
      const t = document.createElement('span');
      t.className = 'histTag gone';
      t.textContent = 'Upload removed';
      tags.appendChild(t);
    }

    const btns = document.createElement('div');
    btns.className = 'histBtns';

    const play = document.createElement('button');
    play.className = 'histPlayBtn';
    play.textContent = 'Play';
    play.disabled = !available;
    play.onclick = async () => {
      if (!available) return;
      await fetch('/history/play', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({index: idx})});
      closeHistory();
      await refresh();
    };

    const queue = document.createElement('button');
    queue.className = 'histQueueBtn';
    queue.textContent = 'Queue';
    queue.disabled = !available;
    queue.onclick = async () => {
      if (!available) return;
      // Requeue by index so the server uses its stored (unredacted) URL —
      // the url in this payload is display-safe and may lack credentials.
      await fetch('/history/requeue', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({index: idx})});
      await refresh();
    };

    btns.appendChild(play);
    btns.appendChild(queue);

    meta.appendChild(title);
    meta.appendChild(channel);
    meta.appendChild(when);
    if (tags.childElementCount > 0) meta.appendChild(tags);
    meta.appendChild(btns);

    row.appendChild(thumb);
    row.appendChild(meta);
    list.appendChild(row);
  });
}

function bindHistoryUi(){
  const btn = document.getElementById('histBtn');
  const closeBtn = document.getElementById('histCloseBtn');
  const clearBtn = document.getElementById('histClearBtn');
  const bd = document.getElementById('histBackdrop');

  if (btn) btn.onclick = openHistory;
  if (closeBtn) closeBtn.onclick = closeHistory;
  if (clearBtn) clearBtn.onclick = async () => {
    // Two-tap guard: wiping the whole history record deserves a beat.
    if (clearBtn.dataset.armed !== '1') {
      clearBtn.dataset.armed = '1';
      clearBtn.dataset.label = clearBtn.textContent;
      clearBtn.textContent = 'Clear all?';
      clearBtn.classList.add('armed');
      setTimeout(() => {
        if (clearBtn.dataset.armed === '1') {
          delete clearBtn.dataset.armed;
          clearBtn.textContent = clearBtn.dataset.label || 'Clear';
          clearBtn.classList.remove('armed');
        }
      }, 3500);
      return;
    }
    delete clearBtn.dataset.armed;
    clearBtn.textContent = clearBtn.dataset.label || 'Clear';
    clearBtn.classList.remove('armed');
    await fetch('/history/clear', {method:'POST', headers:{'Content-Type':'application/json'}, body: '{}'});
    await renderHistory();
  };
  if (bd) bd.addEventListener('click', (e) => {
    if (e.target === bd) closeHistory();
  });
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeHistory();
  });
}

let __aboutInfoLoadedAt = 0;
let __aboutDialogController = null;

function _aboutSetText(id, text){
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function _aboutSetHref(id, href){
  const el = document.getElementById(id);
  if (el && href) el.href = href;
}

function _aboutSetUpdateState(text, cls){
  const el = document.getElementById('aboutUpdateValue');
  if (!el) return;
  el.classList.remove('ok', 'warn', 'err');
  if (cls) el.classList.add(cls);
  el.textContent = text;
}

async function loadAboutInfo(force){
  const now = Date.now();
  if (!force && __aboutInfoLoadedAt && (now - __aboutInfoLoadedAt) < 300000) return;
  __aboutInfoLoadedAt = now;
  _aboutSetText('aboutVersionValue', 'Loading…');
  _aboutSetText('aboutRevisionValue', 'Loading…');
  _aboutSetUpdateState('Checking…', '');
  try {
    const r = await fetch('/app/info', {cache:'no-store'});
    if (!r.ok) throw new Error('status ' + r.status);
    const info = await r.json();
    const version = String(info.version || info.release_version || 'unknown');
    const imageVersion = String(info.image_version || '').trim();
    const suffix = imageVersion && imageVersion !== version ? ` (${imageVersion})` : '';
    _aboutSetText('aboutVersionValue', `${version}${suffix}`);
    const rev = String(info.revision_short || info.revision || '').trim();
    const created = String(info.image_created || '').trim();
    _aboutSetText('aboutRevisionValue', rev ? `${rev}${created ? ` · ${created}` : ''}` : 'Not available');
    _aboutSetHref('aboutGithubLink', String(info.source_url || 'https://github.com/mcgeezy/relaytv'));
    _aboutSetHref('aboutChangelogLink', String(info.changelog_url || 'https://github.com/mcgeezy/relaytv/blob/main/CHANGELOG.md'));
    const latest = info.latest_release || {};
    const latestTag = String(latest.tag_name || '').trim();
    const latestUrl = String(latest.html_url || info.releases_url || '').trim();
    _aboutSetHref('aboutReleaseLink', latestUrl || String(info.releases_url || 'https://github.com/mcgeezy/relaytv/releases'));
    const releaseSub = document.getElementById('aboutReleaseLinkSub');
    if (releaseSub && latestTag) releaseSub.textContent = `Latest published release: ${latestTag}`;
    if (info.update_available === true) {
      _aboutSetUpdateState(`Update available${latestTag ? `: ${latestTag}` : ''}`, 'warn');
    } else if (info.update_available === false) {
      _aboutSetUpdateState(`Up to date${latestTag ? ` (${latestTag})` : ''}`, 'ok');
    } else {
      const reason = String(info.update_check_error || '').trim();
      _aboutSetUpdateState(reason === 'disabled' ? 'Update check disabled' : 'Update status unavailable', reason === 'disabled' ? '' : 'err');
    }
  } catch (_e) {
    _aboutSetText('aboutVersionValue', 'Unavailable');
    _aboutSetText('aboutRevisionValue', 'Unavailable');
    _aboutSetUpdateState('Update status unavailable', 'err');
  }
}

function openAbout(){
  closeHeaderMenu();
  if (!__aboutDialogController || !__aboutDialogController.open()) return;
  loadAboutInfo(false);
  _uiPushLayer();
}

function closeAbout(opts){
  if (!__aboutDialogController) return;
  const fromNav = !!(opts && opts.fromNav);
  if (!fromNav && __aboutDialogController.isOpen() && __layerNavigation.back()) return;
  __aboutDialogController.close();
}

function bindAboutUi(){
  const btn = document.getElementById('aboutBtn');
  const closeBtn = document.getElementById('aboutCloseBtn');
  const bd = document.getElementById('aboutBackdrop');
  if (!btn || !closeBtn || !bd) return;
  __aboutDialogController = window.RelayTV.overlays.createDialogController({
    document,
    window,
    backdrop:bd,
    opener:document.getElementById('hdrMenuBtn') || btn,
    initialFocus:closeBtn,
    onRequestClose:() => closeAbout(),
  });
  __aboutDialogController.mount();
  if (btn) btn.onclick = openAbout;
  if (closeBtn) closeBtn.onclick = closeAbout;
}

function closeNowLanguageModal(opts){
  const bd = document.getElementById('langBackdrop');
  if (!bd) return;
  const fromNav = !!(opts && opts.fromNav);
  if (!fromNav && !bd.classList.contains('hidden') && __layerNavigation.getDepth() > 0) {
    try { history.back(); } catch (_e) {}
    return;
  }
  bd.classList.add('hidden');
}

async function _fetchNowLanguageOptions(refresh){
  const url = `/jellyfin/audio/options${refresh ? '?refresh=1' : ''}`;
  const r = await fetch(url, {cache:'no-store'});
  const body = await r.json().catch(() => ({}));
  if (!r.ok) {
    const msg = String((body && (body.detail || body.reason || body.error)) || `HTTP ${r.status}`);
    throw new Error(msg);
  }
  return body;
}

function _renderNowLanguageOptions(optionsBody){
  const list = document.getElementById('langList');
  const cur = document.getElementById('langCurrent');
  const msg = document.getElementById('langMsg');
  if (!list || !cur || !msg) return;
  msg.classList.remove('ok', 'err');
  msg.textContent = '';
  list.innerHTML = '';

  const currentLang = String(optionsBody.current_audio_language || '').trim();
  const currentIdx = optionsBody.current_audio_stream_index;
  const currentIdxText = (currentIdx === 0 || Number.isInteger(currentIdx)) ? String(currentIdx) : '--';
  cur.textContent = currentLang ? `Current: ${currentLang.toUpperCase()} (#${currentIdxText})` : `Current audio track: #${currentIdxText}`;

  const rows = Array.isArray(optionsBody.options) ? optionsBody.options : [];
  if (!rows.length) {
    const empty = document.createElement('div');
    empty.className = 'muted';
    empty.textContent = 'No alternate audio streams were reported for this item.';
    list.appendChild(empty);
    return;
  }

  rows.forEach((row) => {
    const idx = Number(row && row.index);
    if (!Number.isInteger(idx)) return;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = `langOpt${row && row.is_current ? ' active' : ''}`;
    const lang = String((row && row.language) || '').trim();
    const display = String((row && row.display) || '').trim();
    const suffix = [];
    if (row && row.is_default) suffix.push('default');
    if (row && row.is_current) suffix.push('active');
    btn.innerHTML = `
      <span class="langOptIdx">#${idx}</span>
      <span>${lang ? lang.toUpperCase() : 'Unknown language'}${display ? ` — ${display}` : ''}</span>
      <span class="langOptMeta">${suffix.join(' · ')}</span>
    `;
    btn.disabled = !!(row && row.is_current);
    btn.onclick = async () => {
      const oldText = btn.textContent || '';
      btn.disabled = true;
      btn.textContent = `Switching to #${idx}…`;
      try {
        const r = await fetch('/jellyfin/audio/select', {
          method:'POST',
          headers:{'Content-Type':'application/json'},
          body: JSON.stringify({index: idx})
        });
        const b = await r.json().catch(() => ({}));
        if (!r.ok) {
          throw new Error(String((b && (b.detail || b.reason || b.error)) || `HTTP ${r.status}`));
        }
        msg.classList.remove('err');
        msg.classList.add('ok');
        const switchedLang = String((b && b.current_audio_language) || '').trim();
        msg.textContent = switchedLang
          ? `Audio switched to ${switchedLang.toUpperCase()}.`
          : `Audio switched to track #${idx}.`;
        await refresh();
        const latest = await _fetchNowLanguageOptions(false);
        _renderNowLanguageOptions(latest);
      } catch (e) {
        btn.disabled = false;
        btn.textContent = oldText;
        msg.classList.remove('ok');
        msg.classList.add('err');
        msg.textContent = `Switch failed: ${e && e.message ? e.message : e}`;
      }
    };
    list.appendChild(btn);
  });
}

async function openNowLanguageModal(){
  closeHeaderMenu();
  const bd = document.getElementById('langBackdrop');
  const msg = document.getElementById('langMsg');
  const cur = document.getElementById('langCurrent');
  const list = document.getElementById('langList');
  if (!bd || !cur || !list) return;
  if (!bd.classList.contains('hidden')) return;
  bd.classList.remove('hidden');
  _uiPushLayer();
  if (msg) {
    msg.classList.remove('ok', 'err');
    msg.textContent = '';
  }
  cur.textContent = 'Loading audio tracks…';
  list.innerHTML = '';
  try {
    const optionsBody = await _fetchNowLanguageOptions(false);
    _renderNowLanguageOptions(optionsBody);
  } catch (e) {
    if (msg) {
      msg.classList.add('err');
      msg.textContent = `Audio tracks unavailable: ${e && e.message ? e.message : e}`;
    }
  }
}

function bindNowLanguageUi(){
  const btn = document.getElementById('nowLangBtn');
  const closeBtn = document.getElementById('langCloseBtn');
  const bd = document.getElementById('langBackdrop');
  if (btn) btn.onclick = openNowLanguageModal;
  if (closeBtn) closeBtn.onclick = () => closeNowLanguageModal();
  if (bd) bd.addEventListener('click', (e) => {
    if (e.target === bd) closeNowLanguageModal();
  });
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeNowLanguageModal();
  });
}

function closeNowSubtitleModal(opts){
  const bd = document.getElementById('subLangBackdrop');
  if (!bd) return;
  const fromNav = !!(opts && opts.fromNav);
  if (!fromNav && !bd.classList.contains('hidden') && __layerNavigation.getDepth() > 0) {
    try { history.back(); } catch (_e) {}
    return;
  }
  bd.classList.add('hidden');
}

async function _fetchNowSubtitleOptions(refresh){
  const url = `/jellyfin/subtitle/options${refresh ? '?refresh=1' : ''}`;
  const r = await fetch(url, {cache:'no-store'});
  const body = await r.json().catch(() => ({}));
  if (!r.ok) {
    const msg = String((body && (body.detail || body.reason || body.error)) || `HTTP ${r.status}`);
    throw new Error(msg);
  }
  return body;
}

function _renderNowSubtitleOptions(optionsBody){
  const list = document.getElementById('subLangList');
  const cur = document.getElementById('subLangCurrent');
  const msg = document.getElementById('subLangMsg');
  if (!list || !cur || !msg) return;
  msg.classList.remove('ok', 'err');
  msg.textContent = '';
  list.innerHTML = '';

  const currentOff = !!(optionsBody && optionsBody.current_subtitle_off);
  const currentLang = String(optionsBody.current_subtitle_language || '').trim();
  const currentIdx = optionsBody.current_subtitle_stream_index;
  const currentIdxText = currentOff
    ? 'Off'
    : ((currentIdx === 0 || Number.isInteger(currentIdx)) ? String(currentIdx) : '--');
  cur.textContent = currentOff
    ? 'Current: Off'
    : (currentLang ? `Current: ${currentLang.toUpperCase()} (#${currentIdxText})` : `Current subtitle track: #${currentIdxText}`);

  const rows = Array.isArray(optionsBody.options) ? optionsBody.options : [];
  if (!rows.length) {
    const empty = document.createElement('div');
    empty.className = 'muted';
    empty.textContent = 'No subtitle streams were reported for this item.';
    list.appendChild(empty);
    return;
  }

  rows.forEach((row) => {
    const idx = Number(row && row.index);
    if (!Number.isInteger(idx)) return;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = `langOpt${row && row.is_current ? ' active' : ''}`;
    const isOff = !!(row && row.is_off);
    const lang = String((row && row.language) || '').trim();
    const display = String((row && row.display) || '').trim();
    const suffix = [];
    if (row && row.is_default) suffix.push('default');
    if (row && row.is_current) suffix.push('active');
    btn.innerHTML = `
      <span class="langOptIdx">${isOff ? 'OFF' : `#${idx}`}</span>
      <span>${isOff ? 'Off' : (lang ? lang.toUpperCase() : 'Unknown language')}${display && !isOff ? ` — ${display}` : ''}</span>
      <span class="langOptMeta">${suffix.join(' · ')}</span>
    `;
    btn.disabled = !!(row && row.is_current);
    btn.onclick = async () => {
      const oldText = btn.textContent || '';
      btn.disabled = true;
      btn.textContent = isOff ? 'Turning subtitles off…' : `Switching to #${idx}…`;
      try {
        const r = await fetch('/jellyfin/subtitle/select', {
          method:'POST',
          headers:{'Content-Type':'application/json'},
          body: JSON.stringify({index: idx})
        });
        const b = await r.json().catch(() => ({}));
        if (!r.ok) {
          throw new Error(String((b && (b.detail || b.reason || b.error)) || `HTTP ${r.status}`));
        }
        msg.classList.remove('err');
        msg.classList.add('ok');
        const switchedOff = !!(b && b.current_subtitle_off);
        const switchedLang = String((b && b.current_subtitle_language) || '').trim();
        msg.textContent = switchedOff
          ? 'Subtitles turned off.'
          : (switchedLang ? `Subtitles switched to ${switchedLang.toUpperCase()}.` : `Subtitles switched to track #${idx}.`);
        await refresh();
        const latest = await _fetchNowSubtitleOptions(false);
        _renderNowSubtitleOptions(latest);
      } catch (e) {
        btn.disabled = false;
        btn.textContent = oldText;
        msg.classList.remove('ok');
        msg.classList.add('err');
        msg.textContent = `Subtitle switch failed: ${e && e.message ? e.message : e}`;
      }
    };
    list.appendChild(btn);
  });
}

async function openNowSubtitleModal(){
  closeHeaderMenu();
  const bd = document.getElementById('subLangBackdrop');
  const msg = document.getElementById('subLangMsg');
  const cur = document.getElementById('subLangCurrent');
  const list = document.getElementById('subLangList');
  if (!bd || !cur || !list) return;
  if (!bd.classList.contains('hidden')) return;
  bd.classList.remove('hidden');
  _uiPushLayer();
  if (msg) {
    msg.classList.remove('ok', 'err');
    msg.textContent = '';
  }
  cur.textContent = 'Loading subtitle tracks…';
  list.innerHTML = '';
  try {
    const optionsBody = await _fetchNowSubtitleOptions(false);
    _renderNowSubtitleOptions(optionsBody);
  } catch (e) {
    if (msg) {
      msg.classList.add('err');
      msg.textContent = `Subtitle tracks unavailable: ${e && e.message ? e.message : e}`;
    }
  }
}

function bindNowSubtitleUi(){
  const btn = document.getElementById('nowSubLangBtn');
  const closeBtn = document.getElementById('subLangCloseBtn');
  const bd = document.getElementById('subLangBackdrop');
  if (btn) btn.onclick = openNowSubtitleModal;
  if (closeBtn) closeBtn.onclick = () => closeNowSubtitleModal();
  if (bd) bd.addEventListener('click', (e) => {
    if (e.target === bd) closeNowSubtitleModal();
  });
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeNowSubtitleModal();
  });
}

// Consume the ``?share=`` parameter the share target redirects us to.
//
// ``GET /share`` used to play the link itself. It no longer does: a bare GET is
// reachable from any page the operator visits, so the side effect moved here,
// behind the same authenticated JSON POST every other control uses. The user
// still gets one tap — the modal opens prefilled — and now gets a choice
// between Play and Queue that the old share target never offered.

async function consumeShareParam(){
  let shared = '';
  try {
    shared = (new URLSearchParams(window.location.search || '').get('share') || '').trim();
  } catch(_e) {
    return false;
  }
  if (!shared) return false;
  // Strip it before anything can fail, so a reload or a back-navigation does
  // not re-open the modal with a link the user already dealt with.
  try {
    const here = new URL(window.location.href);
    here.searchParams.delete('share');
    history.replaceState(history.state, '', here.pathname + here.search + here.hash);
  } catch(_e) {}
  const normalized = normalizeUrl(shared);
  if (!looksLikeUrl(normalized)) return false;
  const inp = document.getElementById('addUrlInput');
  // Set the value first: openAddUrl only reaches for the clipboard when the
  // field is empty, and the shared link is the better answer.
  if (inp) inp.value = normalized;
  await openAddUrl();
  return true;
}


function bindAddUrlUi(){
  const btn = document.getElementById('addUrlBtn');
  const bd  = document.getElementById('addBackdrop');
  const closeBtn = document.getElementById('addCloseBtn');
  const pasteBtn = document.getElementById('addPasteBtn');
  const playBtn  = document.getElementById('addPlayBtn');
  const queueBtn = document.getElementById('addQueueBtn');
  const inp      = document.getElementById('addUrlInput');
  const notifyBtn = document.getElementById('notifySendBtn');
  const uploadQueueBtn = document.getElementById('uploadQueueBtn');
  const uploadPlayBtn = document.getElementById('uploadPlayBtn');

  if (btn) btn.onclick = openAddUrl;
  if (closeBtn) closeBtn.onclick = closeAddUrl;
  if (pasteBtn) pasteBtn.onclick = pasteIntoAddUrl;
  if (playBtn) playBtn.onclick = ()=>submitAddUrl('play');
  if (queueBtn) queueBtn.onclick = ()=>submitAddUrl('queue');
  if (notifyBtn) notifyBtn.onclick = submitNotificationToast;
  if (uploadQueueBtn) uploadQueueBtn.onclick = () => submitUploadedMedia('queue');
  if (uploadPlayBtn) uploadPlayBtn.onclick = () => submitUploadedMedia('play');

  if (bd) bd.addEventListener('click', (e) => {
    if (e.target === bd) closeAddUrl();
  });

  // Some browsers only allow clipboard reads after a user gesture.
  if (inp) inp.addEventListener('focus', async ()=>{
    if (inp.value.trim()) return;
    const clip = await clipboardText();
    if (looksLikeUrl(clip)) inp.value = normalizeUrl(clip);
  });

  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeAddUrl();
    // When the modal is open, Enter defaults to Play.
    const open = bd && !bd.classList.contains('hidden');
    if (!open || e.key !== 'Enter') return;
    const target = e.target;
    if (!(target && target.closest)) { submitAddUrl('play'); return; }
    if (target.closest('#notifySection, #uploadSection')) return;
    // A focused control already turns Enter into a click, so handling it here
    // too submits twice — and from the Queue button that meant one keypress
    // firing play_now *and* enqueue, adding the item twice and stealing
    // playback with it.
    if (target.closest('button, a[href], select, textarea')) return;
    submitAddUrl('play');
  });
}
