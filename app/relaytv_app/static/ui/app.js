// --- Optional API token support (RELAYTV_API_TOKEN, docs/API.md) -----------
// When the operator enables the write-endpoint token, browsers must send
// Authorization: Bearer <token>. The token lives in localStorage and is
// attached transparently to same-origin requests; on the first rejected
// write we prompt once, store the entered token, and retry.
(function(){
  const STORAGE_KEY = 'relaytv_api_token';
  let promptedThisLoad = false;

  function storedToken(){
    try { return (window.localStorage.getItem(STORAGE_KEY) || '').trim(); } catch(_e) { return ''; }
  }
  function storeToken(value){
    try {
      const v = String(value || '').trim();
      if (v) window.localStorage.setItem(STORAGE_KEY, v);
      else window.localStorage.removeItem(STORAGE_KEY);
    } catch(_e) {}
  }
  function isSameOrigin(input){
    try {
      const url = typeof input === 'string' ? input : ((input && input.url) || '');
      return new URL(url, window.location.href).origin === window.location.origin;
    } catch(_e) { return false; }
  }
  function withAuth(opts, token){
    const out = Object.assign({}, opts || {});
    const headers = new Headers((opts && opts.headers) || {});
    headers.set('Authorization', 'Bearer ' + token);
    out.headers = headers;
    return out;
  }

  const nativeFetch = window.fetch.bind(window);
  window.fetch = async function(input, opts){
    const sameOrigin = isSameOrigin(input);
    const token = sameOrigin ? storedToken() : '';
    let res = await nativeFetch(input, token ? withAuth(opts, token) : opts);
    if (res.status === 401 && sameOrigin && !promptedThisLoad){
      let wantsBearer = false;
      try { wantsBearer = ((res.headers.get('www-authenticate') || '').toLowerCase().indexOf('bearer') === 0); } catch(_e) {}
      if (wantsBearer){
        promptedThisLoad = true;
        const entered = window.prompt('This RelayTV server requires an API token for control actions.\nEnter the API token (RELAYTV_API_TOKEN):', '');
        const v = String(entered || '').trim();
        if (v){
          storeToken(v);
          res = await nativeFetch(input, withAuth(opts, v));
        }
      }
    }
    return res;
  };
})();

function _fetchWithTimeout(url, opts, timeoutMs){
  const ms = Number(timeoutMs || 0);
  if (!(Number.isFinite(ms) && ms > 0) || typeof AbortController === 'undefined'){
    return fetch(url, opts || {});
  }
  const controller = new AbortController();
  const finalOpts = Object.assign({}, opts || {}, {signal: controller.signal});
  const timer = setTimeout(() => {
    try { controller.abort(); } catch(_e) {}
  }, ms);
  return fetch(url, finalOpts).finally(() => clearTimeout(timer));
}

async function post(path, body, postOpts) {
  const opts = {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: body ? JSON.stringify(body) : '{}'
  };
  // Generous timeout: Android wifi power-save adds seconds of first-packet
  // latency right when the user picks the phone up.
  let ok = false;
  try {
    await _fetchWithTimeout(path, opts, 5000);
    ok = true;
  } catch(_e) {
    // A network error doesn't prove the request never reached the server (the
    // connection can drop after the command lands), and most commands are
    // toggles or relative seeks where a resend would double-apply. Only
    // retry commands the caller marked idempotent (absolute sets); everything
    // else surfaces the failure so the user can retap.
    if (postOpts && postOpts.idempotent) {
      try {
        await _fetchWithTimeout(path, opts, 5000);
        ok = true;
      } catch(_e2) {}
    }
  }
  if (!ok) _connSignal(false, {sticky: true, message: 'Command failed — check connection'});
  refresh().catch(() => null);
}

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
  if (!fromNav && !bd.classList.contains('hidden') && __uiNavDepth > 0) {
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
    if (mode === 'queue') {
      await post('/enqueue', {url});
    } else {
      await post('/play_now', {url, preserve_current:true, preserve_to:'queue_front', resume_current:true, reason:'add_menu'});
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

function fmtTime(s){
  if (s == null || isNaN(s)) return '--:--';
  s = Math.max(0, Math.floor(s));
  const h = Math.floor(s/3600);
  const m = Math.floor((s%3600)/60);
  const sec = s%60;
  return (h>0?`${h}:`:'') + String(m).padStart(2,'0') + ':' + String(sec).padStart(2,'0');
}

let __lastStatus = null;
let __lastStatusFullFetchTs = 0;
let __uiEventSource = null;
let __uiEventSourceLastTs = 0;
let __uiEventSourceBornTs = 0;
let __uiEventReconnectTimer = 0;
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

function _closeUiEventStream(){
  const es = __uiEventSource;
  __uiEventSource = null;
  if (es) { try { es.close(); } catch (_e) {} }
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
  _closeUiEventStream();
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
      badge.classList.remove('hidden');
    }
    document.body.classList.add('connLost');
  }
}

function _scheduleUiEventReconnect(){
  if (__uiEventReconnectTimer) return;
  __uiEventReconnectTimer = window.setTimeout(() => {
    __uiEventReconnectTimer = 0;
    connectUiEventStream();
  }, 2000);
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
  let startX = 0, startY = 0;
  let active = false;
  const MOVE_PX = 4;

  const cleanup = () => {
    __draggingQueue = false;
    active = false;
    startFrom = null;
    overTo = null;
    __dragStartTs = 0;
    document.body.classList.remove('noScroll');
    document.querySelectorAll('.qTile.dragging').forEach(x => x.classList.remove('dragging'));
    document.querySelectorAll('.qTile.dragOver').forEach(x => x.classList.remove('dragOver'));
  };

  __queueDnDCleanup = cleanup;

  const finish = async () => {
    const from = startFrom;
    const to = overTo;
    const didDrag = active; // capture before cleanup() resets state
    cleanup();
    if (didDrag && from != null && to != null && from !== to) {
      await qMove(from, to);
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
let __uiNavDepth = 0;

function _isHiddenEl(el){
  return !el || el.classList.contains('hidden');
}

function _uiRefreshInteractionLockActive(){
  if (__draggingQueue) return true;
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
  try {
    history.pushState({relaytv_ui: 1, t: Date.now()}, '');
    __uiNavDepth += 1;
  } catch (_e) {}
}

function _uiCloseTopLayerFromNav(){
  if (window.relaytvSeerr && window.relaytvSeerr.isDetailOpen()) {
    window.relaytvSeerr.closeDetail({fromNav:true});
    return true;
  }
  if (window.relaytvSeerr && window.relaytvSeerr.isOpen()) {
    window.relaytvSeerr.close({fromNav:true, force:true});
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

function faviconUrl(input){
  const obj = (input && typeof input === 'object') ? input : null;
  const u = obj ? String(obj.url || '') : String(input || '');
  const provider = obj ? String(obj.provider || '').toLowerCase() : '';
  if (provider === 'jellyfin' || _looksLikeJellyfinMediaUrl(u)) {
    return __jfServerType === 'emby' ? '/pwa/emby.svg' : '/pwa/jellyfin.svg';
  }
  const host = _safeUrlHost(u);
  if (!host) return '';
  // Google S2 favicon service (works well without CORS headaches for <img>)
  return `https://www.google.com/s2/favicons?domain=${encodeURIComponent(host)}&sz=64`;
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
  __lastStatus = next;
  return true;
}

async function qRemove(index){
  try {
    const res = await fetch('/queue/remove', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({index})});
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

async function qMove(from_index, to_index){
  try {
    const res = await fetch('/queue/move', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({from_index, to_index})});
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
  if (nowCardEl){
    nowCardEl.classList.toggle('isIdle', !hasNow);
    nowCardEl.classList.toggle('isPaused', paused);
    nowCardEl.classList.toggle('isLive', liveNow);
  }
  const stateTag = document.getElementById('nowStateTag');
  if (stateTag) {
    stateTag.textContent = paused ? 'Paused' : 'Live';
    stateTag.classList.toggle('hidden', !paused && !liveNow);
    stateTag.classList.toggle('live', liveNow && !paused);
  }
  const stateDot = document.getElementById('nowStateDot');
  if (stateDot) {
    stateDot.classList.toggle('playing', activelyPlaying);
    stateDot.classList.toggle('live', liveNow && activelyPlaying);
  }

  const posTxt = liveNow ? 'LIVE' : fmtTime(st.position);
  const durTxt = liveNow ? (paused ? 'Paused' : 'Streaming') : fmtTime(st.duration);

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
  if (ppb) ppb.classList.toggle('isPlaying', !!st.playing && !st.paused);
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
  } else if (!__scrubbing && st.position != null && st.duration != null && st.duration > 0) {
    _setProgressFill(st.position / st.duration);
  } else if (!__scrubbing && (!st.playing || st.duration == null || st.duration <= 0)) {
    _setProgressFill(0);
  }

  // queue list
  const ol = document.getElementById('queue');

  // If a drag got stuck (e.g., pointerup missed), recover so UI keeps rendering.
  if (__draggingQueue && __dragStartTs && (Date.now() - __dragStartTs) > 8000) {
    try { if (typeof __queueDnDCleanup === 'function') __queueDnDCleanup(); } catch(_e) {}
  }

  if (!__draggingQueue) {
    ol.innerHTML = '';
    (st.queue || []).forEach((item, idx) => {
    const li = document.createElement('li');
    li.className = 'qTile';
    if (item && item.available === false) li.classList.add('isUnavailable');
    li.dataset.index = String(idx);

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
    const handle = document.createElement('div');
    handle.className = 'qHandle';
    handle.innerHTML = `
      <svg class="qGrip" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
        <path d="M8 6h8M8 12h8M8 18h8" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
      </svg>`;
    handle.title = 'Drag to reorder';

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

    const del = document.createElement('button');
    del.className = 'qDelBtn';
    del.textContent = '✕';
    del.title = 'Remove from queue';
    del.onclick = () => qRemove(idx);

    // Send just this item. One indirection into the device sheet keeps the tile
    // clean; a device picker per tile would not scale.
    const send = document.createElement('button');
    send.className = 'qSendItemBtn';
    send.type = 'button';
    send.title = 'Send this item to another device';
    send.setAttribute('aria-label', 'Send this item to another device');
    send.textContent = '⋯';
    send.onclick = () => {
      if (window.relaytvPeers) window.relaytvPeers.open({index: idx, title: item.title || item.url || ''});
    };

    li.appendChild(thumb);
    li.appendChild(body);
    li.appendChild(handle);
    li.appendChild(send);
    li.appendChild(del);
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
  __lastStatus = st;
  renderStatus(st);
}

function _applyUiPlaybackEvent(payload){
  if (!payload || typeof payload !== 'object') return;
  _uiEventMarkAlive();
  const merged = _mergePlaybackStateIntoStatus(__lastStatus || {}, payload);
  __lastStatus = merged;
  renderStatus(merged);
}

function _applyUiStatusEvent(payload){
  if (!payload || typeof payload !== 'object') return;
  _uiEventMarkAlive();
  __lastStatus = payload;
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

function connectUiEventStream(){
  if (__uiEventSource) return;
  let es = null;
  try {
    es = new EventSource('/ui/events');
  } catch (_e) {
    _scheduleUiEventReconnect();
    return;
  }
  __uiEventSource = es;
  // Record birth only — never the health clock. The stream hasn't proven
  // itself until a real event (hello/ping) arrives; stamping health here
  // would let a silent reconnect loop read as healthy forever.
  __uiEventSourceBornTs = Date.now();

  es.addEventListener('hello', (ev) => {
    _uiEventMarkAlive();
    const payload = _parseUiEventPayload(ev);
    if (payload && payload.type === 'hello' && !__lastStatus) {
      refresh().catch(() => {});
    }
  });
  es.addEventListener('ping', () => {
    _uiEventMarkAlive();
  });
  es.addEventListener('playback', (ev) => {
    _applyUiPlaybackEvent(_parseUiEventPayload(ev));
  });
  es.addEventListener('status', (ev) => {
    _applyUiStatusEvent(_parseUiEventPayload(ev));
  });
  es.addEventListener('queue', (ev) => {
    _applyUiQueueEvent(_parseUiEventPayload(ev));
  });
  es.addEventListener('jellyfin', (ev) => {
    _applyUiJellyfinEvent(_parseUiEventPayload(ev));
  });
  es.onerror = () => {
    if (__uiEventSource !== es) return;
    try { es.close(); } catch (_e) {}
    __uiEventSource = null;
    _scheduleUiEventReconnect();
  };
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

// --- Theme override (Auto / Dark / Light) -----------------------------------
// All light styling lives in `prefers-color-scheme: light` media blocks; the
// manual override rewrites those rules' media conditions at runtime instead of
// duplicating every block under a data-attribute selector.
const __THEME_KEY = 'relaytv_theme';
const __THEME_RE = /\(prefers-color-scheme:\s*(light|dark)\)/;

function _themeStoredMode(){
  try {
    const v = localStorage.getItem(__THEME_KEY);
    return (v === 'dark' || v === 'light') ? v : 'auto';
  } catch (_e) { return 'auto'; }
}

function _themeApplyToSheets(mode){
  for (const sheet of Array.from(document.styleSheets)){
    let rules = null;
    try { rules = sheet.cssRules; } catch (_e) { continue; }
    if (!rules) continue;
    for (const rule of Array.from(rules)){
      if (!rule.media || !rule.media.mediaText) continue;
      const orig = rule.__relaytvOrigMedia || rule.media.mediaText;
      const m = orig.match(__THEME_RE);
      if (!m) continue;
      rule.__relaytvOrigMedia = orig;
      if (mode === 'auto'){
        rule.media.mediaText = orig;
      } else {
        // Always/never tokens that stay valid inside `and (...)` chains.
        const on = (m[1] === mode);
        rule.media.mediaText = orig.replace(__THEME_RE, on ? '(min-width: 0px)' : '(min-width: 99999px)');
      }
    }
  }
}

function _themeEffective(mode){
  if (mode !== 'auto') return mode;
  try {
    return (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) ? 'light' : 'dark';
  } catch (_e) { return 'dark'; }
}

function applyTheme(mode){
  _themeApplyToSheets(mode);
  try { document.documentElement.style.colorScheme = (mode === 'auto') ? '' : mode; } catch (_e) {}
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', _themeEffective(mode) === 'light' ? '#edf2ff' : '#05070d');
  document.querySelectorAll('.mtBtn').forEach((b) => {
    const on = (b.dataset.themeMode || 'auto') === mode;
    b.classList.toggle('on', on);
    b.setAttribute('aria-checked', on ? 'true' : 'false');
  });
}

function bindThemeUi(){
  document.querySelectorAll('.mtBtn').forEach((b) => {
    b.onclick = () => {
      const mode = b.dataset.themeMode || 'auto';
      try { localStorage.setItem(__THEME_KEY, mode); } catch (_e) {}
      applyTheme(mode);
    };
  });
  try {
    const mq = window.matchMedia('(prefers-color-scheme: light)');
    const onChange = () => applyTheme(_themeStoredMode());
    if (mq.addEventListener) mq.addEventListener('change', onChange);
    else if (mq.addListener) mq.addListener(onChange);
  } catch (_e) {}
}

// Apply immediately (deferred script: DOM and stylesheets are already in) so a
// stored override never flashes the system theme; re-applied on `load` in case
// a stylesheet finished late.
try { applyTheme(_themeStoredMode()); } catch (_e) {}
window.addEventListener('load', () => { try { applyTheme(_themeStoredMode()); } catch (_e) {} });

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
  if (!fromNav && !bd.classList.contains('hidden') && __uiNavDepth > 0) {
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
  const bd = document.getElementById('aboutBackdrop');
  if (!bd || !bd.classList.contains('hidden')) return;
  bd.classList.remove('hidden');
  loadAboutInfo(false);
  _uiPushLayer();
}

function closeAbout(opts){
  const bd = document.getElementById('aboutBackdrop');
  if (!bd) return;
  const fromNav = !!(opts && opts.fromNav);
  if (!fromNav && !bd.classList.contains('hidden') && __uiNavDepth > 0) {
    try { history.back(); } catch (_e) {}
    return;
  }
  bd.classList.add('hidden');
}

function bindAboutUi(){
  const btn = document.getElementById('aboutBtn');
  const closeBtn = document.getElementById('aboutCloseBtn');
  const bd = document.getElementById('aboutBackdrop');
  if (btn) btn.onclick = openAbout;
  if (closeBtn) closeBtn.onclick = closeAbout;
  if (bd) bd.addEventListener('click', (e) => {
    if (e.target === bd) closeAbout();
  });
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeAbout();
  });
}

function closeNowLanguageModal(opts){
  const bd = document.getElementById('langBackdrop');
  if (!bd) return;
  const fromNav = !!(opts && opts.fromNav);
  if (!fromNav && !bd.classList.contains('hidden') && __uiNavDepth > 0) {
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
  if (!fromNav && !bd.classList.contains('hidden') && __uiNavDepth > 0) {
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

function openSettings(){
  closeHeaderMenu();
  const bd = document.getElementById('settingsBackdrop');
  if (!bd || !bd.classList.contains('hidden')) return;
  bd.classList.remove('hidden');
  _uiPushLayer();
  loadSettingsUi().catch(console.warn);
}
function closeSettings(opts){
  const bd = document.getElementById('settingsBackdrop');
  if (!bd) return;
  const fromNav = !!(opts && opts.fromNav);
  if (!fromNav && !bd.classList.contains('hidden') && __uiNavDepth > 0) {
    try { history.back(); } catch (_e) {}
    return;
  }
  bd.classList.add('hidden');
}

function qualityToFormat(q) {
  // Keep this in sync with server-side state._normalize_ytdlp_format.
  if (q === 'worst') return 'worst';
  if (q === '360' || q === '480' || q === '720' || q === '1080') {
    return `bestvideo[vcodec!*=av01][height<=${q}][fps<=60]+bestaudio/best[height<=${q}][fps<=60]/best`;
  }
  // Auto -> server picks compatibility default.
  return '';
}

const IDLE_PANEL_CATALOG = window.RELAYTV_IDLE_PANEL_CATALOG || {};

function renderIdlePanelSettings(cfg){
  const host = document.getElementById('setIdlePanels');
  if (!host) return;
  host.innerHTML = '';
  Object.entries(IDLE_PANEL_CATALOG).forEach(([key, meta]) => {
    const panel = (cfg && cfg[key]) || {};
    const enabled = !!panel.enabled;
    const layout = panel.layout || (meta.layouts && meta.layouts[0]) || 'default';

    const row = document.createElement('div');
    row.className = 'fieldRow';
    row.innerHTML = `
      <div class="toggleRow">
        <div class="toggleCopy">
          <div class="toggleTitle">${meta.title}</div>
          <div class="toggleHint">${meta.desc || ''}</div>
        </div>
        <label class="toggleSwitch" title="${meta.title}">
          <input type="checkbox" data-idle-enable="${key}" ${enabled ? 'checked' : ''}/>
          <span class="toggleTrack" aria-hidden="true"></span>
        </label>
      </div>`;

    const sel = document.createElement('select');
    sel.className = 'input';
    sel.setAttribute('data-idle-layout', key);
    (meta.layouts || ['default']).forEach((opt) => {
      const o = document.createElement('option');
      o.value = opt;
      o.textContent = opt;
      sel.appendChild(o);
    });
    sel.value = layout;
    row.appendChild(sel);
    host.appendChild(row);
  });
}

function collectIdlePanelSettings(){
  const out = {};
  Object.keys(IDLE_PANEL_CATALOG).forEach((key) => {
    const enabled = !!document.querySelector(`[data-idle-enable="${key}"]`)?.checked;
    const layout = document.querySelector(`[data-idle-layout="${key}"]`)?.value || (IDLE_PANEL_CATALOG[key].layouts || ['default'])[0] || 'default';
    out[key] = {enabled, layout};
  });
  return out;
}

const WEATHER_LOCATION_STATE = { latitude: null, longitude: null, location_name: '' };
let SETTINGS_TV_CONTROL_BASELINE = null;

function setWeatherLocationMeta(msg){
  const el = document.getElementById('setWeatherLocationMeta');
  if (el) el.textContent = msg || '';
}

function setWeatherLocation(name, latitude, longitude){
  WEATHER_LOCATION_STATE.latitude = Number.isFinite(Number(latitude)) ? Number(latitude) : null;
  WEATHER_LOCATION_STATE.longitude = Number.isFinite(Number(longitude)) ? Number(longitude) : null;
  WEATHER_LOCATION_STATE.location_name = String(name || '').trim();
  const cityInput = document.getElementById('setWeatherCity');
  if (cityInput) cityInput.value = WEATHER_LOCATION_STATE.location_name;
}

function weatherLocationSummary(name, lat, lon){
  const label = String(name || '').trim() || 'Selected location';
  const sLat = Number.isFinite(Number(lat)) ? Number(lat).toFixed(4) : '--';
  const sLon = Number.isFinite(Number(lon)) ? Number(lon).toFixed(4) : '--';
  return `${label} (${sLat}, ${sLon})`;
}

async function geocodeWeatherCity(cityQuery){
  const q = String(cityQuery || '').trim();
  if (!q) return null;
  const url = `https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(q)}&count=1&language=en&format=json`;
  const r = await fetch(url, {cache:'no-store'});
  if (!r.ok) return null;
  const j = await r.json();
  const first = Array.isArray(j.results) ? j.results[0] : null;
  if (!first) return null;
  const parts = [first.name, first.admin1, first.country].filter(Boolean);
  return {
    latitude: Number(first.latitude),
    longitude: Number(first.longitude),
    location_name: parts.join(', ') || q,
  };
}

function defaultJellyfinServerUrl(){
  try {
    const host = (window.location.hostname || '').trim();
    if (host && host !== 'localhost' && host !== '127.0.0.1') return `http://${host}:8096`;
  } catch (_e) {}
  return 'http://127.0.0.1:8096';
}

function syncSeerrRequestModeUi(){
  const mode = String(document.getElementById('setSeerrRequestMode')?.value || 'disabled');
  const hint = document.getElementById('setSeerrRequestModeHint');
  const keyState = document.getElementById('setSeerrApiKeyState');
  const userRow = document.getElementById('setSeerrRequestUserRow');
  if (userRow) userRow.classList.toggle('hidden', mode !== 'shared_admin');
  if (keyState) {
    const configured = keyState.getAttribute('data-configured') === '1';
    keyState.textContent = mode === 'caller_session'
      ? `API key is not used in caller-specific mode${configured ? '; the stored key is retained.' : '.'}`
      : (configured ? 'API key is stored.' : 'No API key stored.');
  }
  if (!hint) return;
  if (mode === 'shared_admin') {
    hint.textContent = "Uses Seerr's administrator API identity and may auto-approve regardless of the attributed user's normal policy.";
  } else if (mode === 'caller_session') {
    hint.textContent = 'Each browser must connect through Jellyfin Quick Connect; Seerr applies that caller’s permissions, quotas, and approval policy.';
  } else {
    hint.textContent = 'Browsing remains available, but RelayTV will not create Seerr requests.';
  }
}

async function loadSettingsUi(){
  const [devRes, setRes, tvRes, jfRes, seerrRes, seerrUsersRes] = await Promise.all([
    fetch('/devices'),
    fetch('/settings'),
    fetch('/tv/status').catch(() => null),
    fetch('/integrations/jellyfin/status').catch(() => null),
    fetch('/integrations/seerr/status').catch(() => null),
    fetch('/integrations/seerr/users').catch(() => null)
  ]);
  const dev = await devRes.json();
  const cur = await setRes.json();
  const tvStatus = (tvRes && tvRes.ok) ? await tvRes.json() : null;
  const jfStatus = (jfRes && jfRes.ok) ? await jfRes.json() : null;
  const seerrStatus = (seerrRes && seerrRes.ok) ? await seerrRes.json() : null;
  const seerrUsers = (seerrUsersRes && seerrUsersRes.ok) ? await seerrUsersRes.json() : null;
  const deviceName = document.getElementById('setDeviceName');
  const audioDev = document.getElementById('setAudioDev');
  const qual = document.getElementById('setQuality');
  const ytUseInvidious = document.getElementById('setYtUseInvidious');
  const ytInvidiousBase = document.getElementById('setYtInvidiousBase');
  const ytdlpAutoUpdate = document.getElementById('setYtdlpAutoUpdate');
  const ytCookiesFile = document.getElementById('setYtCookiesFile');
  const ytCookiesState = document.getElementById('setYtCookiesState');
  const subs = document.getElementById('setSubs');
  const cecEnabled = document.getElementById('setCecEnabled');
  const tvTakeoverEnabled = document.getElementById('setTvTakeoverEnabled');
  const tvPauseOnInputChange = document.getElementById('setTvPauseOnInputChange');
  const tvAutoResumeOnReturn = document.getElementById('setTvAutoResumeOnReturn');
  const cecStatus = document.getElementById('setCecStatus');
  const cecAvailabilityHint = document.getElementById('setCecAvailabilityHint');
  const idleDashboardEnabled = document.getElementById('setIdleDashboardEnabled');
  const idleNotificationsEnabled = document.getElementById('setIdleNotificationsEnabled');
  const idleQrEnabled = document.getElementById('setIdleQrEnabled');
  const idleQrSize = document.getElementById('setIdleQrSize');
  const idleQrSizeVal = document.getElementById('setIdleQrSizeVal');
  const wDays = document.getElementById('setWeatherDays');
  const uploadMaxSize = document.getElementById('setUploadMaxSize');
  const uploadRetentionHours = document.getElementById('setUploadRetentionHours');
  const iptvEnabled = document.getElementById('setIptvEnabled');
  const jfEnabled = document.getElementById('setJfEnabled');
  const jfServerUrl = document.getElementById('setJfServerUrl');
  const jfUsername = document.getElementById('setJfUsername');
  const jfUserId = document.getElementById('setJfUserId');
  const jfPwInput = document.getElementById('setJfPassword');
  const jfClearPw = document.getElementById('setJfClearPassword');
  const jfPwState = document.getElementById('setJfPasswordState');
  const jfAudioLang = document.getElementById('setJfAudioLang');
  const jfSubLang = document.getElementById('setJfSubLang');
  const jfPlaybackMode = document.getElementById('setJfPlaybackMode');
  const jfSyncDiag = document.getElementById('setJfSyncDiag');
  const jfCacheClearMsg = document.getElementById('setJfCacheClearResult');
  const seerrEnabled = document.getElementById('setSeerrEnabled');
  const seerrServerUrl = document.getElementById('setSeerrServerUrl');
  const seerrApiKey = document.getElementById('setSeerrApiKey');
  const seerrClearApiKey = document.getElementById('setSeerrClearApiKey');
  const seerrApiKeyState = document.getElementById('setSeerrApiKeyState');
  const seerrRequestMode = document.getElementById('setSeerrRequestMode');
  const seerrRequestUser = document.getElementById('setSeerrRequestUser');
  const seerrDiag = document.getElementById('setSeerrDiag');

  if (deviceName) deviceName.value = (cur.device_name || 'RelayTV');
  if (iptvEnabled) iptvEnabled.checked = !!cur.iptv_enabled;
  const iptvBadge = document.getElementById('setIptvStatus');
  if (iptvBadge) {
    iptvBadge.textContent = cur.iptv_enabled ? 'Enabled' : 'Disabled';
    iptvBadge.classList.remove('up', 'down', 'warn', 'unknown');
    iptvBadge.classList.add(cur.iptv_enabled ? 'up' : 'unknown');
  }
  if (ytUseInvidious) ytUseInvidious.checked = !!cur.youtube_use_invidious;
  if (ytInvidiousBase) ytInvidiousBase.value = (cur.youtube_invidious_base || '');
  if (ytdlpAutoUpdate) ytdlpAutoUpdate.checked = !!cur.ytdlp_auto_update_enabled;
  if (ytCookiesFile) ytCookiesFile.value = '';
  if (ytCookiesState) {
    ytCookiesState.classList.remove('ok', 'err');
    ytCookiesState.textContent = cur.youtube_cookies_configured ? 'cookies.txt is configured.' : 'No cookies.txt uploaded.';
  }
  applyJfBranding(
    (jfStatus && jfStatus.server_type) || cur.jellyfin_server_type,
    !!String(cur.jellyfin_server_url || '').trim()
  );
  if (jfEnabled) jfEnabled.checked = !!cur.jellyfin_enabled;
  if (jfServerUrl) jfServerUrl.value = (cur.jellyfin_server_url || defaultJellyfinServerUrl());
  if (jfUsername) jfUsername.value = (cur.jellyfin_username || '');
  if (jfUserId) jfUserId.value = (cur.jellyfin_user_id || '');
  if (jfAudioLang) jfAudioLang.value = (cur.jellyfin_audio_lang || '');
  if (jfSubLang) jfSubLang.value = (cur.jellyfin_sub_lang || '');
  if (jfPlaybackMode) jfPlaybackMode.value = (cur.jellyfin_playback_mode || 'auto');
  if (jfPwInput) jfPwInput.value = '';
  if (jfClearPw) jfClearPw.checked = false;
  if (jfPwState) {
    const hasPw = !!cur.jellyfin_password_configured;
    jfPwState.textContent = hasPw ? 'Password is stored.' : 'No password stored.';
    jfPwState.setAttribute('data-configured', hasPw ? '1' : '0');
  }
  const jfBadge = document.getElementById('setJfStatus');
  if (jfBadge) {
    const enabled = jfStatus && Object.prototype.hasOwnProperty.call(jfStatus, 'enabled')
      ? !!jfStatus.enabled
      : !!cur.jellyfin_enabled;
    const up = !!(enabled && jfStatus && (jfStatus.connected || jfStatus.authenticated));
    jfBadge.textContent = enabled ? (up ? 'Connected' : 'Down') : 'Disabled';
    jfBadge.classList.remove('up', 'down', 'warn', 'unknown');
    jfBadge.classList.add(enabled ? (up ? 'up' : 'down') : 'unknown');
  }
  if (jfSyncDiag) {
    if (!jfStatus) {
      jfSyncDiag.textContent = 'Status unavailable.';
    } else {
      const pOk = Number(jfStatus.progress_success_count || 0);
      const pFail = Number(jfStatus.progress_failure_count || 0);
      const sOk = Number(jfStatus.stopped_success_count || 0);
      const sFail = Number(jfStatus.stopped_failure_count || 0);
      const sSupp = Number(jfStatus.stopped_suppressed_count || 0);
      const pLat = Number.isFinite(Number(jfStatus.last_progress_latency_ms)) ? `${Number(jfStatus.last_progress_latency_ms)}ms` : 'n/a';
      const sLat = Number.isFinite(Number(jfStatus.last_stopped_latency_ms)) ? `${Number(jfStatus.last_stopped_latency_ms)}ms` : 'n/a';
      const auth = jfStatus.authenticated ? 'yes' : 'no';
      const catalogUserId = (jfStatus.catalog_user_id || '').toString().trim();
      const catalogUserSource = (jfStatus.catalog_user_source || 'none').toString().trim();
      const catalogUser = catalogUserId ? `${catalogUserId} (${catalogUserSource || 'preferred'})` : 'auto';
      const cacheEntries = Number(jfStatus.catalog_cache_entries || 0);
      const cacheMax = Number(jfStatus.catalog_cache_max_entries || 0);
      const cacheDiag = cacheMax > 0 ? `${cacheEntries}/${cacheMax}` : String(cacheEntries);
      const cacheClears = Number(jfStatus.catalog_cache_clears || 0);
      const cacheClearReason = (jfStatus.catalog_cache_last_cleared_reason || '').toString().trim();
      const health = (jfStatus.sync_health || 'unknown').toString();
      const healthReason = (jfStatus.sync_health_reason || '').toString().trim();
      const err = (jfStatus.last_error || '').toString().trim();
      jfSyncDiag.textContent =
        `Health: ${health}${healthReason ? ` (${healthReason})` : ''} · Auth: ${auth} · Catalog user: ${catalogUser} · Cache: ${cacheDiag} (clears: ${cacheClears}${cacheClearReason ? `, ${cacheClearReason}` : ''}) · Progress ok/fail: ${pOk}/${pFail} (${pLat}) · Stopped ok/fail: ${sOk}/${sFail} (${sLat}) · Stop dedupe: ${sSupp}` +
        (err ? ` · Last error: ${err}` : '');
    }
  }
  if (jfCacheClearMsg) {
    jfCacheClearMsg.classList.remove('ok', 'err');
    jfCacheClearMsg.textContent = '';
  }
  if (seerrEnabled) seerrEnabled.checked = !!cur.seerr_enabled;
  if (seerrServerUrl) seerrServerUrl.value = String(cur.seerr_server_url || '');
  if (seerrApiKey) seerrApiKey.value = '';
  if (seerrClearApiKey) seerrClearApiKey.checked = false;
  if (seerrApiKeyState) {
    const configured = !!cur.seerr_api_key_configured;
    seerrApiKeyState.textContent = configured ? 'API key is stored.' : 'No API key stored.';
    seerrApiKeyState.setAttribute('data-configured', configured ? '1' : '0');
  }
  if (seerrRequestMode) seerrRequestMode.value = String(cur.seerr_request_mode || (cur.seerr_shared_requests_enabled ? 'shared_admin' : 'disabled'));
  syncSeerrRequestModeUi();
  if (seerrRequestUser) {
    seerrRequestUser.replaceChildren();
    const defaultOption = document.createElement('option');
    defaultOption.value = '';
    defaultOption.textContent = 'API identity (default)';
    seerrRequestUser.appendChild(defaultOption);
    const users = seerrUsers && Array.isArray(seerrUsers.users) ? seerrUsers.users : [];
    users.forEach(user => {
      const id = Number(user && user.id);
      if (!Number.isInteger(id) || id <= 0) return;
      const option = document.createElement('option');
      option.value = String(id);
      const display = String(user.display_name || user.username || `User ${id}`);
      const username = String(user.username || '');
      option.textContent = username && username !== display ? `${display} (${username})` : display;
      seerrRequestUser.appendChild(option);
    });
    seerrRequestUser.value = cur.seerr_request_user_id ? String(cur.seerr_request_user_id) : '';
  }
  const seerrBadge = document.getElementById('setSeerrStatus');
  if (seerrBadge) {
    const enabled = seerrStatus ? !!seerrStatus.enabled : !!cur.seerr_enabled;
    const reachable = !!(enabled && seerrStatus && seerrStatus.reachable);
    seerrBadge.textContent = enabled ? (reachable ? 'Connected' : 'Down') : 'Disabled';
    seerrBadge.classList.remove('up', 'down', 'warn', 'unknown');
    seerrBadge.classList.add(enabled ? (reachable ? 'up' : 'down') : 'unknown');
  }
  if (seerrDiag) {
    if (!seerrStatus) seerrDiag.textContent = 'Status unavailable.';
    else seerrDiag.textContent = [
      seerrStatus.application_title || 'Seerr',
      seerrStatus.version ? `v${seerrStatus.version}` : '',
      seerrStatus.media_server_type || '',
      seerrStatus.auth_mode === 'shared_api_key' ? 'server API key' : 'not authenticated',
    ].filter(Boolean).join(' · ');
  }
  if (window.relaytvSeerr) window.relaytvSeerr.updateStatus(seerrStatus || {enabled:!!cur.seerr_enabled, configured:!!cur.seerr_api_key_configured});

  if (audioDev){
    audioDev.innerHTML = '';
    const optAuto = document.createElement('option');
    optAuto.value = '';
    optAuto.textContent = 'Auto';
    audioDev.appendChild(optAuto);

    (dev.alsa_devices || []).forEach(d => {
      const o = document.createElement('option');
      o.value = d.id;
      o.textContent = d.desc ? `${d.id} — ${d.desc}` : d.id;
      audioDev.appendChild(o);
    });
    audioDev.value = (cur.audio_device || '');
  }

  // Quality dropdown from quality_mode/quality_cap (fallback: ytdlp_format heuristic)
  if (qual){
    const qMode = (cur.quality_mode || '').toString().toLowerCase();
    let sel = '';
    if (qMode === 'auto' || qMode === 'auto_profile' || qMode === 'profile') {
      const cap = (cur.quality_cap || '').toString().trim();
      sel = cap || '';
    } else {
      const yf = (cur.ytdlp_format || '').toString();
      const m = yf.match(/height<=([0-9]+)/);
      if (m) sel = m[1];
      if (yf.trim() === 'worst') sel = 'worst';
    }
    qual.value = sel;
  }

  if (subs){
    subs.value = (cur.sub_lang || '');
  }
  if (cecEnabled) cecEnabled.checked = ['1', 'true', 'yes', 'on'].includes(String(cur.cec_enabled || '').trim().toLowerCase());
  if (tvTakeoverEnabled) tvTakeoverEnabled.checked = String(cur.tv_takeover_enabled ?? '1').trim() !== '0';
  if (tvPauseOnInputChange) tvPauseOnInputChange.checked = String(cur.tv_pause_on_input_change ?? '1').trim() !== '0';
  if (tvAutoResumeOnReturn) tvAutoResumeOnReturn.checked = ['1', 'true', 'yes', 'on'].includes(String(cur.tv_auto_resume_on_return || '').trim().toLowerCase());
  SETTINGS_TV_CONTROL_BASELINE = {
    cec_enabled: cecEnabled ? (cecEnabled.checked ? '1' : '0') : undefined,
    tv_takeover_enabled: tvTakeoverEnabled ? (tvTakeoverEnabled.checked ? '1' : '0') : undefined,
    tv_pause_on_input_change: tvPauseOnInputChange ? (tvPauseOnInputChange.checked ? '1' : '0') : undefined,
    tv_auto_resume_on_return: tvAutoResumeOnReturn ? (tvAutoResumeOnReturn.checked ? '1' : '0') : undefined,
  };
  {
    const availability = tvStatus?.cec_controller?.availability || {};
    const cecAvailable = availability.available === true;
    const cecKnown = !!tvStatus && typeof tvStatus.cec_controller === 'object';
    [cecEnabled, tvTakeoverEnabled, tvPauseOnInputChange, tvAutoResumeOnReturn].forEach(el => {
      if (el) el.disabled = !cecAvailable;
    });
    if (cecStatus) {
      cecStatus.classList.remove('up', 'down', 'warn', 'unknown');
      cecStatus.classList.add(cecAvailable ? 'up' : (cecKnown ? 'down' : 'unknown'));
      cecStatus.textContent = cecAvailable ? 'Available' : (cecKnown ? 'Unavailable' : 'Unknown');
    }
    if (cecAvailabilityHint) {
      const devices = Array.isArray(availability.devices) ? availability.devices : [];
      const adapters = Array.isArray(availability.adapters_reported) ? availability.adapters_reported : [];
      if (cecAvailable) {
        cecAvailabilityHint.textContent = devices.length ? `Adapter visible: ${devices.join(', ')}` : 'CEC adapter is visible to RelayTV.';
      } else if (cecKnown) {
        const reason = availability.last_error ? ` Last error: ${availability.last_error}` : '';
        cecAvailabilityHint.textContent = devices.length || adapters.length
          ? `CEC adapter is detected but not usable by the running container.${reason}`
          : `No CEC adapter is visible to the running container. Enable CEC passthrough during install and recreate the container.${reason}`;
      } else {
        cecAvailabilityHint.textContent = 'CEC status is unavailable.';
      }
    }
  }
  if (idleDashboardEnabled) idleDashboardEnabled.checked = (cur.idle_dashboard_enabled !== false);
  if (idleNotificationsEnabled) idleNotificationsEnabled.checked = (cur.idle_notifications_enabled !== false);
  if (idleQrEnabled) idleQrEnabled.checked = (cur.idle_qr_enabled !== false);
  if (idleQrSize) {
    const size = Number(cur.idle_qr_size);
    const safe = Number.isFinite(size) ? Math.max(96, Math.min(280, Math.round(size))) : 168;
    idleQrSize.value = String(safe);
    if (idleQrSizeVal) idleQrSizeVal.textContent = `${safe}px`;
  }

  if (wDays) wDays.value = (cur.weather && cur.weather.forecast_days) ? String(cur.weather.forecast_days) : '7';
  if (uploadMaxSize) {
    const maxSize = Number(cur.uploads && cur.uploads.max_size_gb);
    uploadMaxSize.value = String(Number.isFinite(maxSize) ? maxSize : 5);
  }
  if (uploadRetentionHours) {
    const retention = Number(cur.uploads && cur.uploads.retention_hours);
    uploadRetentionHours.value = String(Number.isFinite(retention) ? retention : 24);
  }

  const weather = cur.weather || {};
  setWeatherLocation(
    weather.location_name || 'New York, NY',
    weather.latitude,
    weather.longitude,
  );
  setWeatherLocationMeta(weatherLocationSummary(WEATHER_LOCATION_STATE.location_name, WEATHER_LOCATION_STATE.latitude, WEATHER_LOCATION_STATE.longitude));

  renderIdlePanelSettings(cur.idle_panels || {});
}

function bindSettingsUi(){
  const btn = document.getElementById('settingsBtn');
  const closeBtn = document.getElementById('settingsCloseBtn');
  const saveBtn = document.getElementById('settingsSaveBtn');
  const bd = document.getElementById('settingsBackdrop');

  if (btn) btn.onclick = openSettings;
  if (closeBtn) closeBtn.onclick = closeSettings;
  if (bd) bd.addEventListener('click', (e) => { if (e.target === bd) closeSettings(); });
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      const open = bd && !bd.classList.contains('hidden');
      if (open) closeSettings();
    }
  });

  const weatherCityInput = document.getElementById('setWeatherCity');
  const weatherFindBtn = document.getElementById('setWeatherFindBtn');
  const idleQrSize = document.getElementById('setIdleQrSize');
  const idleQrSizeVal = document.getElementById('setIdleQrSizeVal');
  const jfApplyBtn = document.getElementById('setJfApplyBtn');
  const jfApplyMsg = document.getElementById('setJfApplyResult');
  const jfCacheClearBtn = document.getElementById('setJfCacheClearBtn');
  const jfCacheClearMsg = document.getElementById('setJfCacheClearResult');
  const seerrApplyBtn = document.getElementById('setSeerrApplyBtn');
  const seerrTestBtn = document.getElementById('setSeerrTestBtn');
  const seerrApplyMsg = document.getElementById('setSeerrApplyResult');
  const seerrRequestMode = document.getElementById('setSeerrRequestMode');
  const ytUploadBtn = document.getElementById('setYtCookiesUploadBtn');
  const ytClearBtn = document.getElementById('setYtCookiesClearBtn');
  const ytCookiesFile = document.getElementById('setYtCookiesFile');
  const ytCookiesState = document.getElementById('setYtCookiesState');

  if (seerrRequestMode) seerrRequestMode.onchange = syncSeerrRequestModeUi;

  function setYtCookiesStatus(text, cls){
    if (!ytCookiesState) return;
    ytCookiesState.classList.remove('ok', 'err');
    if (cls) ytCookiesState.classList.add(cls);
    ytCookiesState.textContent = text || '';
  }

  if (weatherFindBtn) weatherFindBtn.onclick = async () => {
    const city = weatherCityInput?.value || '';
    if (!city.trim()) {
      setWeatherLocationMeta('Enter a city to search.');
      return;
    }
    setWeatherLocationMeta('Looking up city…');
    const found = await geocodeWeatherCity(city);
    if (!found) {
      setWeatherLocationMeta('City not found. Try adding state/country.');
      return;
    }
    setWeatherLocation(found.location_name, found.latitude, found.longitude);
    setWeatherLocationMeta(weatherLocationSummary(found.location_name, found.latitude, found.longitude));
  };

  if (idleQrSize) {
    const syncQrSizeLabel = () => {
      const n = Number(idleQrSize.value || '168');
      const safe = Number.isFinite(n) ? Math.max(96, Math.min(280, Math.round(n))) : 168;
      if (idleQrSizeVal) idleQrSizeVal.textContent = `${safe}px`;
    };
    idleQrSize.addEventListener('input', syncQrSizeLabel);
    syncQrSizeLabel();
  }

  if (ytUploadBtn) ytUploadBtn.onclick = async () => {
    const file = ytCookiesFile?.files && ytCookiesFile.files[0] ? ytCookiesFile.files[0] : null;
    if (!file) {
      setYtCookiesStatus('Choose a cookies.txt file first.', 'err');
      return;
    }
    ytUploadBtn.disabled = true;
    setYtCookiesStatus('Uploading cookies.txt…');
    try {
      const text = await file.text();
      const r = await fetch('/settings/youtube/cookies', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({cookies_text: text, filename: file.name || ''})
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) {
        setYtCookiesStatus(`Upload failed: ${String((j && j.detail) || `HTTP ${r.status}`)}`, 'err');
        return;
      }
      setYtCookiesStatus('cookies.txt uploaded and applied.', 'ok');
      if (ytCookiesFile) ytCookiesFile.value = '';
      await loadSettingsUi();
    } catch (e) {
      setYtCookiesStatus(`Upload failed: ${e && e.message ? e.message : e}`, 'err');
    } finally {
      ytUploadBtn.disabled = false;
    }
  };

  if (ytClearBtn) ytClearBtn.onclick = async () => {
    ytClearBtn.disabled = true;
    setYtCookiesStatus('Clearing cookies configuration…');
    try {
      const r = await fetch('/settings/youtube/cookies/clear', {method:'POST'});
      const j = await r.json().catch(() => ({}));
      if (!r.ok) {
        setYtCookiesStatus(`Clear failed: ${String((j && j.detail) || `HTTP ${r.status}`)}`, 'err');
        return;
      }
      setYtCookiesStatus('cookies.txt configuration cleared.', 'ok');
      if (ytCookiesFile) ytCookiesFile.value = '';
      await loadSettingsUi();
    } catch (e) {
      setYtCookiesStatus(`Clear failed: ${e && e.message ? e.message : e}`, 'err');
    } finally {
      ytClearBtn.disabled = false;
    }
  };

  async function applyJellyfinOnly(){
    if (jfApplyMsg) {
      jfApplyMsg.classList.remove('ok', 'err');
      jfApplyMsg.textContent = '';
    }
    const jfEnabled = !!document.getElementById('setJfEnabled')?.checked;
    const jfServer = (document.getElementById('setJfServerUrl')?.value || '').trim();
    const jfUser = (document.getElementById('setJfUsername')?.value || '').trim();
    const jfUserId = (document.getElementById('setJfUserId')?.value || '').trim();
    const jfPass = (document.getElementById('setJfPassword')?.value || '').trim();
    const jfClearPw = !!document.getElementById('setJfClearPassword')?.checked;
    const jfPwConfigured = (document.getElementById('setJfPasswordState')?.getAttribute('data-configured') || '') === '1';
    const jfAudioLang = (document.getElementById('setJfAudioLang')?.value || '').trim().toLowerCase();
    const jfSubLang = (document.getElementById('setJfSubLang')?.value || '').trim().toLowerCase();
    const jfPlaybackMode = (document.getElementById('setJfPlaybackMode')?.value || 'auto').trim().toLowerCase();
    const deviceName = (document.getElementById('setDeviceName')?.value || '').trim();

    if (jfEnabled) {
      if (!jfServer) { if (jfApplyMsg){ jfApplyMsg.classList.add('err'); jfApplyMsg.textContent='Server URL is required.'; } return; }
      if (!jfUser) { if (jfApplyMsg){ jfApplyMsg.classList.add('err'); jfApplyMsg.textContent='Username is required.'; } return; }
      if (!jfPass && !jfPwConfigured) { if (jfApplyMsg){ jfApplyMsg.classList.add('err'); jfApplyMsg.textContent='Password is required.'; } return; }
    }

    const payload = {
      device_name: deviceName || 'RelayTV',
      jellyfin_enabled: jfEnabled,
      jellyfin_server_url: jfServer,
      jellyfin_username: jfUser,
      jellyfin_user_id: jfUserId,
      jellyfin_audio_lang: jfAudioLang,
      jellyfin_sub_lang: jfSubLang,
      jellyfin_playback_mode: (jfPlaybackMode === 'direct' || jfPlaybackMode === 'transcode') ? jfPlaybackMode : 'auto',
      apply_now: true
    };
    if (jfPass || jfClearPw) payload.jellyfin_password = jfClearPw ? '' : jfPass;

    if (jfApplyBtn) jfApplyBtn.disabled = true;
    try {
      const r = await fetch('/settings', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
      if (!r.ok) {
        if (jfApplyMsg) {
          jfApplyMsg.classList.add('err');
          jfApplyMsg.textContent = 'Apply failed.';
        }
        return;
      }
      const body = await r.json().catch(() => ({}));
      const failed = Array.isArray(body.live_apply_failed) ? body.live_apply_failed : [];
      if (failed.length) {
        if (jfApplyMsg) {
          jfApplyMsg.classList.add('err');
          jfApplyMsg.textContent = `Apply failed: ${failed.join(', ')}`;
        }
      } else {
        if (jfApplyMsg) {
          jfApplyMsg.classList.add('ok');
          jfApplyMsg.textContent = 'Jellyfin settings applied.';
        }
      }
      await loadSettingsUi();
    } catch (_e) {
      if (jfApplyMsg) {
        jfApplyMsg.classList.add('err');
        jfApplyMsg.textContent = 'Apply failed.';
      }
    } finally {
      if (jfApplyBtn) jfApplyBtn.disabled = false;
    }
  }

  if (jfApplyBtn) jfApplyBtn.onclick = applyJellyfinOnly;

  async function applySeerrOnly(testAfterApply){
    if (seerrApplyMsg) { seerrApplyMsg.classList.remove('ok', 'err'); seerrApplyMsg.textContent = ''; }
    const enabled = !!document.getElementById('setSeerrEnabled')?.checked;
    const serverUrl = String(document.getElementById('setSeerrServerUrl')?.value || '').trim();
    const apiKey = String(document.getElementById('setSeerrApiKey')?.value || '').trim();
    const clearKey = !!document.getElementById('setSeerrClearApiKey')?.checked;
    const keyConfigured = document.getElementById('setSeerrApiKeyState')?.getAttribute('data-configured') === '1';
    const requestMode = String(document.getElementById('setSeerrRequestMode')?.value || 'disabled');
    const requestUserRaw = String(document.getElementById('setSeerrRequestUser')?.value || '').trim();
    if (enabled && !serverUrl) { if (seerrApplyMsg) { seerrApplyMsg.classList.add('err'); seerrApplyMsg.textContent = 'Seerr server URL is required.'; } return false; }
    if (enabled && requestMode !== 'caller_session' && !apiKey && (!keyConfigured || clearKey)) { if (seerrApplyMsg) { seerrApplyMsg.classList.add('err'); seerrApplyMsg.textContent = 'Seerr API key is required for shared browsing.'; } return false; }
    const payload = {
      seerr_enabled: enabled,
      seerr_server_url: serverUrl,
      seerr_request_mode: requestMode,
      seerr_request_user_id: requestUserRaw ? Number(requestUserRaw) : null,
      apply_now: true,
    };
    if (apiKey) payload.seerr_api_key = apiKey;
    if (clearKey) payload.seerr_api_key_clear = true;
    if (seerrApplyBtn) seerrApplyBtn.disabled = true;
    if (seerrTestBtn) seerrTestBtn.disabled = true;
    try {
      const response = await fetch('/settings', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(_seerrErrorMessage(body, response.status));
      let message = enabled ? 'Seerr settings applied.' : 'Seerr disabled.';
      if (testAfterApply && enabled) {
        const testResponse = await fetch('/integrations/seerr/test', {method:'POST'});
        const testBody = await testResponse.json().catch(() => ({}));
        if (!testResponse.ok) throw new Error(_seerrErrorMessage(testBody, testResponse.status));
        const identity = testBody.identity || {};
        message = `Connected${identity.display_name || identity.username ? ` as ${identity.display_name || identity.username}` : ''}.`;
      }
      await loadSettingsUi();
      if (seerrApplyMsg) { seerrApplyMsg.classList.add('ok'); seerrApplyMsg.textContent = message; }
      if (window.relaytvSeerr) window.relaytvSeerr.refreshStatus();
      return true;
    } catch (error) {
      if (seerrApplyMsg) { seerrApplyMsg.classList.add('err'); seerrApplyMsg.textContent = error && error.message ? error.message : 'Seerr apply failed.'; }
      return false;
    } finally {
      if (seerrApplyBtn) seerrApplyBtn.disabled = false;
      if (seerrTestBtn) seerrTestBtn.disabled = false;
    }
  }

  if (seerrApplyBtn) seerrApplyBtn.onclick = () => applySeerrOnly(false);
  if (seerrTestBtn) seerrTestBtn.onclick = () => applySeerrOnly(true);

  async function clearJellyfinCatalogCache(){
    if (jfCacheClearMsg) {
      jfCacheClearMsg.classList.remove('ok', 'err');
      jfCacheClearMsg.textContent = '';
    }
    if (jfCacheClearBtn) jfCacheClearBtn.disabled = true;
    try {
      const r = await fetch('/integrations/jellyfin/catalog/cache_clear', {method:'POST'});
      if (!r.ok) {
        if (jfCacheClearMsg) {
          jfCacheClearMsg.classList.add('err');
          jfCacheClearMsg.textContent = 'Cache clear failed.';
        }
        return;
      }
      if (jfCacheClearMsg) {
        jfCacheClearMsg.classList.add('ok');
        jfCacheClearMsg.textContent = 'Catalog cache cleared.';
      }
      await loadSettingsUi();
    } catch (_e) {
      if (jfCacheClearMsg) {
        jfCacheClearMsg.classList.add('err');
        jfCacheClearMsg.textContent = 'Cache clear failed.';
      }
    } finally {
      if (jfCacheClearBtn) jfCacheClearBtn.disabled = false;
    }
  }

  if (jfCacheClearBtn) jfCacheClearBtn.onclick = clearJellyfinCatalogCache;

  if (saveBtn) saveBtn.onclick = async () => {
    const deviceName = (document.getElementById('setDeviceName')?.value || '').trim();
    const audioDev = document.getElementById('setAudioDev')?.value || '';
    const qual = document.getElementById('setQuality')?.value || '';
    const ytUseInvidious = !!document.getElementById('setYtUseInvidious')?.checked;
    const ytInvidiousBase = (document.getElementById('setYtInvidiousBase')?.value || '').trim();
    const ytdlpAutoUpdate = !!document.getElementById('setYtdlpAutoUpdate')?.checked;
    const subs = document.getElementById('setSubs')?.value || '';
    const cecEnabled = !!document.getElementById('setCecEnabled')?.checked;
    const tvTakeoverEnabled = document.getElementById('setTvTakeoverEnabled')?.checked !== false;
    const tvPauseOnInputChange = document.getElementById('setTvPauseOnInputChange')?.checked !== false;
    const tvAutoResumeOnReturn = !!document.getElementById('setTvAutoResumeOnReturn')?.checked;
    const idleDashboardEnabled = document.getElementById('setIdleDashboardEnabled')?.checked !== false;
    const idleNotificationsEnabled = document.getElementById('setIdleNotificationsEnabled')?.checked !== false;
    const idleQrEnabled = !!document.getElementById('setIdleQrEnabled')?.checked;
    const idleQrSize = Number(document.getElementById('setIdleQrSize')?.value || '168');
    const idleQrSizeSafe = Number.isFinite(idleQrSize) ? Math.max(96, Math.min(280, Math.round(idleQrSize))) : 168;
    const weatherDays = Number(document.getElementById('setWeatherDays')?.value || '7');
    const uploadMaxSize = Number(document.getElementById('setUploadMaxSize')?.value || '5');
    const uploadRetentionHours = Number(document.getElementById('setUploadRetentionHours')?.value || '24');
    const jfEnabled = !!document.getElementById('setJfEnabled')?.checked;
    const jfServer = (document.getElementById('setJfServerUrl')?.value || '').trim();
    const jfUser = (document.getElementById('setJfUsername')?.value || '').trim();
    const jfUserId = (document.getElementById('setJfUserId')?.value || '').trim();
    const jfPass = (document.getElementById('setJfPassword')?.value || '').trim();
    const jfClearPw = !!document.getElementById('setJfClearPassword')?.checked;
    const jfPwConfigured = (document.getElementById('setJfPasswordState')?.getAttribute('data-configured') || '') === '1';
    const jfAudioLang = (document.getElementById('setJfAudioLang')?.value || '').trim().toLowerCase();
    const jfSubLang = (document.getElementById('setJfSubLang')?.value || '').trim().toLowerCase();
    const jfPlaybackMode = (document.getElementById('setJfPlaybackMode')?.value || 'auto').trim().toLowerCase();
    const seerrEnabled = !!document.getElementById('setSeerrEnabled')?.checked;
    const seerrServerUrl = String(document.getElementById('setSeerrServerUrl')?.value || '').trim();
    const seerrApiKey = String(document.getElementById('setSeerrApiKey')?.value || '').trim();
    const seerrClearApiKey = !!document.getElementById('setSeerrClearApiKey')?.checked;
    const seerrApiKeyConfigured = document.getElementById('setSeerrApiKeyState')?.getAttribute('data-configured') === '1';
    const seerrRequestMode = String(document.getElementById('setSeerrRequestMode')?.value || 'disabled');
    const seerrRequestUserRaw = String(document.getElementById('setSeerrRequestUser')?.value || '').trim();
    const typedCity = weatherCityInput?.value || '';
    if (typedCity.trim() && typedCity.trim() !== WEATHER_LOCATION_STATE.location_name) {
      const found = await geocodeWeatherCity(typedCity);
      if (found) {
        setWeatherLocation(found.location_name, found.latitude, found.longitude);
      }
    }
    if (ytUseInvidious && !ytInvidiousBase) {
      alert('Invidious server URL is required when YouTube Invidious mode is enabled.');
      return;
    }
    if (jfEnabled) {
      if (!jfServer) { alert(`${jfBrandName()} server URL is required.`); return; }
      if (!jfUser) { alert(`${jfBrandName()} username is required.`); return; }
      if (!jfPass && !jfPwConfigured) { alert(`${jfBrandName()} password is required.`); return; }
    }
    if (seerrEnabled && !seerrServerUrl) { alert('Seerr server URL is required.'); return; }
    if (seerrEnabled && seerrRequestMode !== 'caller_session' && !seerrApiKey && (!seerrApiKeyConfigured || seerrClearApiKey)) { alert('Seerr API key is required for shared browsing.'); return; }

    const payload = {
      device_name: deviceName || 'RelayTV',
      audio_device: audioDev,
      quality_mode: (qual ? 'manual' : 'auto_profile'),
      quality_cap: (qual && qual !== 'worst') ? qual : '',
      ytdlp_format: (qual ? qualityToFormat(qual) : ''),
      youtube_use_invidious: ytUseInvidious,
      youtube_invidious_base: ytInvidiousBase,
      ytdlp_auto_update_enabled: ytdlpAutoUpdate,
      sub_lang: subs,
      idle_dashboard_enabled: idleDashboardEnabled,
      idle_notifications_enabled: idleNotificationsEnabled,
      idle_qr_enabled: idleQrEnabled,
      idle_qr_size: idleQrSizeSafe,
      idle_panels: collectIdlePanelSettings(),
      weather: {
        forecast_days: [1,3,7].includes(weatherDays) ? weatherDays : 7,
        latitude: Number.isFinite(WEATHER_LOCATION_STATE.latitude) ? WEATHER_LOCATION_STATE.latitude : 40.7128,
        longitude: Number.isFinite(WEATHER_LOCATION_STATE.longitude) ? WEATHER_LOCATION_STATE.longitude : -74.006,
        location_name: (WEATHER_LOCATION_STATE.location_name || typedCity || 'New York, NY').trim()
      },
      uploads: {
        max_size_gb: Number.isFinite(uploadMaxSize) ? Math.max(0.25, Math.min(500, Number(uploadMaxSize.toFixed(2)))) : 5,
        retention_hours: Number.isFinite(uploadRetentionHours) ? Math.max(1, Math.min(2160, Math.round(uploadRetentionHours))) : 24
      },
      iptv_enabled: !!document.getElementById('setIptvEnabled')?.checked,
      jellyfin_enabled: jfEnabled,
      jellyfin_server_url: jfServer,
      jellyfin_username: jfUser,
      jellyfin_user_id: jfUserId,
      jellyfin_audio_lang: jfAudioLang,
      jellyfin_sub_lang: jfSubLang,
      jellyfin_playback_mode: (jfPlaybackMode === 'direct' || jfPlaybackMode === 'transcode') ? jfPlaybackMode : 'auto',
      seerr_enabled: seerrEnabled,
      seerr_server_url: seerrServerUrl,
      seerr_request_mode: seerrRequestMode,
      seerr_request_user_id: seerrRequestUserRaw ? Number(seerrRequestUserRaw) : null,
      apply_now: true
    };
    const tvControl = {
      cec_enabled: cecEnabled ? '1' : '0',
      tv_takeover_enabled: tvTakeoverEnabled ? '1' : '0',
      tv_pause_on_input_change: tvPauseOnInputChange ? '1' : '0',
      tv_auto_resume_on_return: tvAutoResumeOnReturn ? '1' : '0',
    };
    const tvBaseline = SETTINGS_TV_CONTROL_BASELINE || {};
    Object.entries(tvControl).forEach(([key, value]) => {
      if (tvBaseline[key] !== undefined && value !== tvBaseline[key]) payload[key] = value;
    });
    if (jfPass || jfClearPw) payload.jellyfin_password = jfClearPw ? '' : jfPass;
    if (seerrApiKey) payload.seerr_api_key = seerrApiKey;
    if (seerrClearApiKey) payload.seerr_api_key_clear = true;
    const r = await fetch('/settings', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
    if (!r.ok) {
      alert('Failed to save settings');
      return;
    }
    // Server-type detection runs during apply; rebrand right away instead of
    // waiting for the next status poll.
    try {
      const jfRes = await fetch('/integrations/jellyfin/status');
      if (jfRes && jfRes.ok) {
        const jf = await jfRes.json();
        applyJfBranding(jf.server_type, !!jfServer);
      }
    } catch (_e) {}
    closeSettings();
  };
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

  if (btn) btn.onclick = openAddUrl;
  if (closeBtn) closeBtn.onclick = closeAddUrl;
  if (pasteBtn) pasteBtn.onclick = pasteIntoAddUrl;
  if (playBtn) playBtn.onclick = ()=>submitAddUrl('play');
  if (queueBtn) queueBtn.onclick = ()=>submitAddUrl('queue');
  if (notifyBtn) notifyBtn.onclick = submitNotificationToast;

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
    if (target.closest('#notifySection')) return;
    // A focused control already turns Enter into a click, so handling it here
    // too submits twice — and from the Queue button that meant one keypress
    // firing play_now *and* enqueue, adding the item twice and stealing
    // playback with it.
    if (target.closest('button, a[href], select, textarea')) return;
    submitAddUrl('play');
  });
}

// Bind UI handlers only after the full DOM is parsed. The Settings modal markup
// is defined after this script block in the HTML template.
window.addEventListener('DOMContentLoaded', () => {
  initScrubber();
  initRemoteVolumeSlider();
  primeRemoteVolumeSlider().catch(() => {});
  bindHeaderMenu();
  bindThemeUi();
  bindHistoryUi();
  bindAboutUi();
  bindNowLanguageUi();
  bindNowSubtitleUi();
  bindSettingsUi();
  bindAddUrlUi();
  bindSeerrUi();
  bindJellyfinUi();
  _jfSetShellVisible(false);
  _jfSetActiveTab('dashboard', {refresh:false});
  try { history.replaceState(Object.assign({}, history.state || {}, {relaytv_root: 1}), ''); } catch (_e) {}
  window.addEventListener('popstate', () => {
    if (__uiNavDepth > 0) __uiNavDepth = Math.max(0, __uiNavDepth - 1);
    _uiCloseTopLayerFromNav();
  });
  const wakeReconnect = () => {
    if (document.visibilityState !== 'visible') return;
    const wasHealthy = _uiEventHealthy();
    _ensureUiEventStream();
    if (!wasHealthy) refresh().catch(() => {});
  };
  document.addEventListener('visibilitychange', wakeReconnect);
  window.addEventListener('online', wakeReconnect);
  window.addEventListener('pageshow', wakeReconnect);
  connectUiEventStream();
  refresh();
  setInterval(() => {
    if (_uiEventHealthy()) return;
    refresh().catch(() => {});
  }, __UI_FALLBACK_REFRESH_MS);
  setInterval(() => {
    _ensureUiEventStream();
  }, __UI_EVENT_RECONNECT_MS);
  setInterval(() => {
    if (document.visibilityState !== 'visible') return;
    if (!__jfUiVisible) return;
    if (__jfActiveTab !== 'dashboard') return;
    if (__jfLastMode === 'search' && __jfLastQuery) return;
    loadJellyfinHome(false);
  }, __JF_DASHBOARD_REFRESH_MS);
});
