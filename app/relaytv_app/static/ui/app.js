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

async function post(path, body) {
  try {
    await _fetchWithTimeout(path, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: body ? JSON.stringify(body) : '{}'
    }, 1800);
  } catch(_e) {
    // Keep controls responsive even if a transient request stalls.
  }
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

async function submitAddUrl(mode){
  const inp = document.getElementById('addUrlInput');
  if (!inp) return;
  const url = normalizeUrl(inp.value);
  if (!looksLikeUrl(url)) {
    alert('Please enter a valid URL (starting with http(s):// or www.)');
    inp.focus();
    return;
  }

  if (mode === 'queue') {
    await post('/enqueue', {url});
  } else {
    await post('/play_now', {url, preserve_current:true, preserve_to:'queue_front', resume_current:true, reason:'add_menu'});
  }
  closeAddUrl();
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
  const r = await _fetchWithTimeout('/playback/state', {cache:'no-store'}, 900);
  if (!r.ok) throw new Error(`playback_state ${r.status}`);
  return await r.json();
}

async function _fetchFullStatus(){
  const r = await _fetchWithTimeout('/status', {cache:'no-store'}, 1600);
  if (!r.ok) throw new Error(`status ${r.status}`);
  const st = await r.json();
  __lastStatusFullFetchTs = Date.now();
  return st;
}

function _uiEventMarkAlive(){
  __uiEventSourceLastTs = Date.now();
}

function _uiEventHealthy(){
  return !!(__uiEventSource && ((Date.now() - __uiEventSourceLastTs) < 10000));
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
  const modalIds = ['addBackdrop', 'histBackdrop', 'aboutBackdrop', 'settingsBackdrop', 'langBackdrop'];
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
    return '/pwa/jellyfin.svg';
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
  return !!(np && (np.title || np.url || np.stream));
}

function _isNowPlayingJellyfin(np){
  if (!np || typeof np !== 'object') return false;
  const provider = String(np.provider || '').trim().toLowerCase();
  if (provider === 'jellyfin') return true;
  if (String(np.jellyfin_item_id || '').trim()) return true;
  return _looksLikeJellyfinMediaUrl(String(np.url || ''));
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
    if (label) label.textContent = `${Math.round(base)}% Volume`;
  } else if (label) {
    label.textContent = effective == null ? '--% Volume' : `${effective}% Volume`;
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
    await post('/volume', {set: val});
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
  const dur = __lastStatus.duration;
  if (dur == null || isNaN(dur) || dur <= 0) return;
  const sec = pct * dur;
  await post('/seek_abs', {sec: sec});
}

function initScrubber(){
  const bar = document.getElementById('progress');
  if (!bar) return;

  // Avoid double-binding if UI hot reloads
  if (bar.__scrubberBound) return;
  bar.__scrubberBound = true;

  bar.addEventListener('pointerdown', (e) => {
    if (!__lastStatus || !__lastStatus.playing) return;
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

let __jfBusy = false;
let __jfLastMode = 'home';
let __jfLastQuery = '';
let __jfSearchDebounceTimer = 0;
let __jfPendingSearch = null;
let __jfSelectedItemId = '';
let __jfSelectedItem = null;
let __jfActionBusy = false;
let __jfConnected = false;
let __jfActionStatusTimer = 0;
let __jfDetailNavToken = 0;
let __jfUiVisible = false;
let __jfLaunchVisible = false;
let __jfActiveTab = 'dashboard';
let __jfDashboardRows = [];
let __jfMoviesSort = 'added';
let __jfMoviesLimit = 120;
let __jfMoviesCount = 0;
let __jfTvSort = 'title_asc';
let __jfTvLimit = 120;
let __jfTvCount = 0;
let __jfTvSeriesId = '';
let __jfTvSeriesTitle = '';
let __jfTvSeriesThumb = '';
let __jfTvSeasonNumber = null;
let __jfTvSeasonChooserExpanded = false;
let __jfTvViewMode = 'series';
let __jfLastFocus = null;
let __jfAlphaIndicatorTimer = 0;
let __jfResizeBound = false;
let __jfViewportBound = false;
const __JF_CATALOG_LIMIT = 5000;
const __JF_REQ_TIMEOUT_MS = 12000;
const __UI_FALLBACK_REFRESH_MS = 8000;
const __UI_EVENT_RECONNECT_MS = 5000;
const __JF_DASHBOARD_REFRESH_MS = 45000;

function _jfCanLaunchFromStatus(st){
  if (!st || typeof st !== 'object') return false;
  const enabled = !!st.jellyfin_enabled;
  const running = !!st.jellyfin_running;
  const connected = !!(st.jellyfin_connected || st.jellyfin_authenticated);
  return enabled && running && connected;
}

function _jfSetLaunchVisible(visible){
  __jfLaunchVisible = !!visible;
  const btn = document.getElementById('jellyfinOpenBtn');
  if (btn) {
    btn.classList.toggle('show', __jfLaunchVisible);
    btn.disabled = !__jfLaunchVisible;
  }
  if (!__jfLaunchVisible) closeJellyfinShell({fromNav:true, force:true});
}

function _jfSetShellVisible(visible){
  __jfUiVisible = !!visible;
  const shell = document.getElementById('jellyfinShell');
  if (!shell) return;
  if (__jfUiVisible) {
    shell.classList.remove('hidden');
    shell.setAttribute('aria-hidden', 'false');
  } else {
    shell.classList.add('hidden');
    shell.setAttribute('aria-hidden', 'true');
    shell.classList.remove('jfDetailLock');
    document.body.classList.remove('jfNoScroll');
  }
}

function openJellyfinShell(){
  if (!__jfLaunchVisible) return;
  if (__jfUiVisible) return;
  __jfLastFocus = document.activeElement || null;
  _jfSetShellVisible(true);
  _uiPushLayer();
  _jfSetActiveTab(__jfActiveTab || 'dashboard', {refresh:false});
  const backBtn = document.getElementById('jfShellBackBtn');
  if (backBtn) {
    requestAnimationFrame(() => backBtn.focus());
  }
}

function closeJellyfinShell(opts){
  const fromNav = !!(opts && opts.fromNav);
  const force = !!(opts && opts.force);
  if (!fromNav && !force && __jfUiVisible && __uiNavDepth > 0) {
    try { history.back(); } catch (_e) {}
    return;
  }
  _jfSetShellVisible(false);
  _jfCloseDetailPanel({fromNav:true});
  const target =
    (__jfLastFocus && typeof __jfLastFocus.focus === 'function') ? __jfLastFocus :
    document.getElementById('jellyfinOpenBtn');
  if (target && typeof target.focus === 'function') {
    requestAnimationFrame(() => target.focus());
  }
  __jfLastFocus = null;
}

function _jfSetActiveTab(tab, opts){
  const next = String(tab || 'dashboard').toLowerCase();
  __jfActiveTab = (next === 'movies' || next === 'tv') ? next : 'dashboard';
  document.querySelectorAll('.jfTabBtn').forEach((b) => {
    const isActive = String(b.getAttribute('data-jf-tab') || '') === __jfActiveTab;
    b.classList.toggle('active', isActive);
    b.setAttribute('aria-selected', isActive ? 'true' : 'false');
    b.setAttribute('tabindex', isActive ? '0' : '-1');
  });
  const searchInput = document.getElementById('jfSearchInput');
  if (searchInput) searchInput.disabled = __jfActionBusy;
  _jfSyncTabControls();
  const force = !!(opts && opts.refresh);
  if (__jfLastMode === 'search' && __jfLastQuery) {
    _jfScheduleSearch(force, 0);
    return;
  }
  _jfLoadActiveTabDefault(force);
}

function _jfSyncTabControls(){
  const searchActive = (__jfLastMode === 'search' && !!__jfLastQuery);
  const isTvSeriesView = (__jfActiveTab === 'tv' && __jfTvViewMode === 'series');
  const showCatalogControls = !searchActive && ((__jfActiveTab === 'movies') || isTvSeriesView);
  const searchInput = document.getElementById('jfSearchInput');
  const sortSel = document.getElementById('jfSortSelect');
  const alphaIndicator = document.getElementById('jfAlphaIndicator');
  if (searchInput) {
    searchInput.placeholder = (__jfActiveTab === 'movies')
      ? 'Search movies…'
      : (__jfActiveTab === 'tv' ? 'Search TV series…' : 'Search Jellyfin titles…');
  }
  if (sortSel) sortSel.classList.toggle('hiddenCtl', !showCatalogControls);
  if (alphaIndicator && !showCatalogControls) alphaIndicator.classList.remove('show');
  if (!sortSel || !showCatalogControls) return;

  const opts = (__jfActiveTab === 'movies')
    ? [
        ['added', 'Recently Added'],
        ['title_asc', 'A-Z'],
        ['title_desc', 'Z-A'],
        ['year_desc', 'Year (new-old)'],
        ['year_asc', 'Year (old-new)'],
      ]
    : [
        ['title_asc', 'A-Z'],
        ['title_desc', 'Z-A'],
        ['added', 'Recently Added'],
        ['year_desc', 'Year (new-old)'],
        ['year_asc', 'Year (old-new)'],
      ];
  const selected = (__jfActiveTab === 'movies') ? __jfMoviesSort : __jfTvSort;
  sortSel.innerHTML = '';
  opts.forEach(([v, label]) => {
    const o = document.createElement('option');
    o.value = v;
    o.textContent = label;
    sortSel.appendChild(o);
  });
  sortSel.value = selected || opts[0][0];
  _jfSetAlphaIndicator('A', {show:false});
}

function _jfSetAlphaIndicator(letter, opts){
  const el = document.getElementById('jfAlphaIndicator');
  if (!el) return;
  const t = String(letter || '').trim().toUpperCase();
  el.textContent = t || 'A';
  const topPx = Number(opts && opts.topPx);
  if (Number.isFinite(topPx)) {
    el.style.top = `${Math.max(10, Math.round(topPx))}px`;
  }
  const show = !!(opts && opts.show);
  if (!show) {
    el.classList.remove('show');
    return;
  }
  el.classList.add('show');
  if (__jfAlphaIndicatorTimer) clearTimeout(__jfAlphaIndicatorTimer);
  __jfAlphaIndicatorTimer = setTimeout(() => {
    el.classList.remove('show');
    __jfAlphaIndicatorTimer = 0;
  }, 850);
}

function _jfTitleInitial(item){
  const txt = String((item && item.title) || '').trim().toUpperCase();
  if (!txt) return '#';
  const c = txt.charAt(0);
  return /[A-Z]/.test(c) ? c : '#';
}

function _jfIndicatorSortMode(rowId){
  const rid = String(rowId || '').trim().toLowerCase();
  if (__jfActiveTab === 'movies' && rid === 'movies') {
    return String(__jfMoviesSort || 'added').trim().toLowerCase();
  }
  if (__jfActiveTab === 'tv' && rid === 'tv_series') {
    return String(__jfTvSort || 'title_asc').trim().toLowerCase();
  }
  return 'title_asc';
}

function _jfExtractYearLabel(raw){
  const txt = String(raw || '').trim();
  if (!txt) return '';
  const m = txt.match(/\b(19|20)\d{2}\b/);
  return m && m[0] ? m[0] : '';
}

function _jfIndicatorLabelForNode(node, rowId){
  if (!node) return 'A';
  const mode = _jfIndicatorSortMode(rowId);
  const useYear = mode === 'added' || mode === 'year_desc' || mode === 'year_asc';
  if (useYear) {
    const year = _jfExtractYearLabel(
      node.getAttribute('data-item-year')
      || node.getAttribute('data-item-subtitle')
      || node.getAttribute('data-item-title')
      || ''
    );
    if (year) return year;
  }
  const title = String(node.getAttribute('data-item-title') || '').trim();
  return _jfTitleInitial({title});
}

function _jfIsNarrowViewport(){
  try {
    return window.matchMedia('(max-width: 980px)').matches;
  } catch (_e) {
    return window.innerWidth <= 980;
  }
}

function _jfSetDetailScrollLock(locked){
  const lock = !!locked && _jfIsNarrowViewport();
  const shell = document.getElementById('jellyfinShell');
  if (shell) shell.classList.toggle('jfDetailLock', lock);
  document.body.classList.toggle('jfNoScroll', lock);
}

function _jfPositionDetailPanel(){
  const detail = document.getElementById('jfDetail');
  if (!detail) return;
  if (_jfIsNarrowViewport()) {
    const shell = document.getElementById('jellyfinShell');
    const grid = document.getElementById('jfGrid');
    if (shell && grid) {
      const shellRect = shell.getBoundingClientRect();
      const gridRect = grid.getBoundingClientRect();
      const gutter = 12;
      const gridWidth = Math.max(0, Math.floor(grid.clientWidth || gridRect.width || shellRect.width || 0));
      const maxW = Math.max(220, Math.min(660, Math.floor(gridWidth - (gutter * 2))));
      const maxH = Math.max(220, Math.floor(shell.clientHeight - (gutter * 2)));
      detail.style.position = 'absolute';
      detail.style.left = `${Math.max(0, Math.round((grid.clientWidth - maxW) / 2))}px`;
      detail.style.right = 'auto';
      detail.style.width = `${maxW}px`;
      detail.style.maxWidth = `${maxW}px`;
      detail.style.maxHeight = `${maxH}px`;
      detail.style.transform = 'none';
      const panelH = Math.min(detail.offsetHeight || maxH, maxH);
      const rawTop = gutter - (gridRect.top - shellRect.top);
      const maxTop = Math.max(0, grid.scrollHeight - panelH - gutter);
      const top = Math.min(Math.round(rawTop), maxTop);
      detail.style.top = `${top}px`;
      return;
    }
  }
  detail.style.position = '';
  detail.style.width = '';
  detail.style.maxWidth = '';
  detail.style.maxHeight = '';
  detail.style.left = '';
  detail.style.right = '';
  detail.style.transform = '';
  const shell = document.getElementById('jellyfinShell');
  const grid = document.getElementById('jfGrid');
  if (!shell || !grid || !detail || !_jfIsDetailOpen()) return;
  const shellRect = shell.getBoundingClientRect();
  const gridRect = grid.getBoundingClientRect();
  const rawTop = 14 - (gridRect.top - shellRect.top);
  const maxTop = Math.max(0, grid.scrollHeight - detail.offsetHeight - 8);
  const top = Math.max(0, Math.min(Math.round(rawTop), maxTop));
  detail.style.top = `${top}px`;
}

function _jfCloseDetailPanel(opts){
  const grid = document.getElementById('jfGrid');
  const fromNav = !!(opts && opts.fromNav);
  if (!fromNav && _jfIsDetailOpen() && __uiNavDepth > 0) {
    try { history.back(); } catch (_e) {}
    return;
  }
  if (grid) grid.classList.remove('detailOpen');
  const detail = document.getElementById('jfDetail');
  if (detail) {
    detail.style.top = '';
    detail.style.width = '';
    detail.style.maxWidth = '';
    detail.style.maxHeight = '';
    detail.style.left = '';
    detail.style.right = '';
    detail.style.transform = '';
    detail.style.position = '';
  }
  _jfSetDetailScrollLock(false);
  __jfSelectedItemId = '';
  __jfSelectedItem = null;
  _jfApplySelectionUi();
  _jfDetailPlaceholder('Select a Jellyfin item to view details.');
}

function _jfOpenDetailPanel(){
  const grid = document.getElementById('jfGrid');
  const wasOpen = _jfIsDetailOpen();
  if (grid) grid.classList.add('detailOpen');
  if (!wasOpen) _uiPushLayer();
  _jfSetDetailScrollLock(true);
  requestAnimationFrame(() => _jfPositionDetailPanel());
}

function _jfIsDetailOpen(){
  const grid = document.getElementById('jfGrid');
  return !!(grid && grid.classList.contains('detailOpen'));
}

function _jfSeriesItemFromNode(node){
  const item = _jfLightItemFromNode(node);
  if (!item) return null;
  item.type = String(node.getAttribute('data-item-type') || '').trim().toLowerCase();
  item.series_id = String(node.getAttribute('data-item-series-id') || '').trim();
  item.season_id = String(node.getAttribute('data-item-season-id') || '').trim();
  item.thumbnail = String(node.getAttribute('data-item-thumb') || '').trim();
  item.thumbnail_local = String(node.getAttribute('data-item-thumb-local') || '').trim();
  const sn = Number(node.getAttribute('data-item-season') || '');
  if (Number.isFinite(sn)) item.season_number = sn;
  return item;
}

function _jfOpenSeriesDetailFromRich(rich){
  if (!rich) return;
  const rType = String(rich.type || '').trim().toLowerCase();
  if (rType === 'nav_back') {
    __jfTvSeasonChooserExpanded = false;
    loadJellyfinTvSeries(false);
    return;
  }
  if (rType === 'season') {
    __jfTvSeasonNumber = Number.isFinite(Number(rich.season_number)) ? Number(rich.season_number) : null;
    __jfTvSeasonChooserExpanded = false;
    loadJellyfinTvSeriesDetail(rich.series_id || __jfTvSeriesId, {
      title: __jfTvSeriesTitle,
      thumbnail: __jfTvSeriesThumb || rich.thumbnail_local || rich.thumbnail || '',
      thumbnail_local: __jfTvSeriesThumb || rich.thumbnail_local || '',
    });
    return;
  }
  if (rType === 'series') {
    __jfTvSeasonChooserExpanded = true;
    loadJellyfinTvSeriesDetail(rich.item_id, {
      title: rich.title,
      thumbnail: rich.thumbnail_local || rich.thumbnail || '',
      thumbnail_local: rich.thumbnail_local || '',
    });
    return;
  }
  // Episodes (and any future non-series entries) should open item detail panel.
  loadJellyfinDetail(rich.item_id);
}

function _jfToggleTvSeasonChooser(){
  if (__jfBusy || !__jfTvSeriesId) return;
  __jfTvSeasonChooserExpanded = !__jfTvSeasonChooserExpanded;
  loadJellyfinTvSeriesDetail(__jfTvSeriesId, {
    title: __jfTvSeriesTitle,
    thumbnail: __jfTvSeriesThumb,
    thumbnail_local: __jfTvSeriesThumb,
    chooserExpanded: __jfTvSeasonChooserExpanded,
    focusChooser: true,
  });
}

function _jfIsSeriesNavType(rich){
  if (!rich || typeof rich !== 'object') return false;
  const rType = String(rich.type || '').trim().toLowerCase();
  return rType === 'series' || rType === 'season' || rType === 'nav_back';
}

function _jfSetStatus(text, kind){
  const el = document.getElementById('jfStatus');
  if (!el) return;
  el.textContent = text || '';
  el.classList.remove('ok', 'err');
  if (kind === 'ok' || kind === 'err') el.classList.add(kind);
}

function _jfSetConn(up, text){
  __jfConnected = !!up;
  const card = document.getElementById('jellyfinCard');
  if (card) card.classList.toggle('jfOffline', !__jfConnected);
  // Connection indicator is represented by jfStatus only.
  void text;
}

function _jfSetActionStatus(text, kind, holdMs){
  const el = document.getElementById('jfActionStatus');
  if (!el) return;
  el.classList.remove('ok', 'err');
  if (kind === 'ok' || kind === 'err') el.classList.add(kind);
  const msg = String(text || '').trim();
  if (/^(connected|ready)(\s*\(.*\))?$/i.test(msg)) {
    el.textContent = '';
    el.classList.remove('ok', 'err');
    return;
  }
  el.textContent = msg;
  if (__jfActionStatusTimer) {
    clearTimeout(__jfActionStatusTimer);
    __jfActionStatusTimer = 0;
  }
  const ttl = Number(holdMs);
  if (Number.isFinite(ttl) && ttl > 0) {
    __jfActionStatusTimer = setTimeout(() => {
      if (!el) return;
      el.textContent = '';
      el.classList.remove('ok', 'err');
      __jfActionStatusTimer = 0;
    }, ttl);
  }
}

function _jfFmtSec(sec){
  const n = Number(sec);
  if (!Number.isFinite(n) || n <= 0) return '';
  const m = Math.floor(n / 60);
  const s = Math.floor(n % 60);
  return `${m}:${String(s).padStart(2,'0')}`;
}

function _jfInt(val){
  const n = Number(val);
  if (!Number.isFinite(n)) return null;
  const i = Math.floor(n);
  return i >= 0 ? i : null;
}

function _jfEpisodeTuple(item){
  if (!item || typeof item !== 'object') return {season:null, episode:null};
  let season = _jfInt(item.season_number);
  let episode = _jfInt(item.episode_number);
  if (season != null && episode != null) return {season, episode};
  const sub = String(item.subtitle || '').trim();
  const m = sub.match(/S(\d{1,3})E(\d{1,4})/i);
  if (m) {
    season = _jfInt(m[1]);
    episode = _jfInt(m[2]);
  }
  return {season, episode};
}

function _jfSeriesKey(item){
  if (!item || typeof item !== 'object') return '';
  const s = String(item.series_name || item.title || '').trim().toLowerCase();
  return s;
}

function _jfEpisodeNav(item){
  if (!item || typeof item !== 'object') return {prev:null, next:null};
  const type = String(item.type || '').trim().toLowerCase();
  if (type !== 'episode') return {prev:null, next:null};
  const cur = _jfEpisodeTuple(item);
  const key = _jfSeriesKey(item);
  if (!key || cur.season == null || cur.episode == null) return {prev:null, next:null};

  const byId = new Map();
  document.querySelectorAll('#jfRows .jfItem').forEach((node) => {
    const iid = String(node.getAttribute('data-item-id') || '').trim();
    if (!iid) return;
    const nType = String(node.getAttribute('data-item-type') || '').trim().toLowerCase();
    if (nType !== 'episode') return;
    const nSeries = String(node.getAttribute('data-item-series') || '').trim().toLowerCase();
    if (!nSeries || nSeries !== key) return;
    let nSeason = _jfInt(node.getAttribute('data-item-season'));
    let nEpisode = _jfInt(node.getAttribute('data-item-episode'));
    if (nSeason == null || nEpisode == null) {
      const parsed = _jfEpisodeTuple({
        subtitle: String(node.getAttribute('data-item-subtitle') || '').trim(),
      });
      if (nSeason == null) nSeason = parsed.season;
      if (nEpisode == null) nEpisode = parsed.episode;
    }
    if (nSeason == null || nEpisode == null) return;
    if (!byId.has(iid)) {
      byId.set(iid, {
        item_id: iid,
        title: String(node.getAttribute('data-item-title') || '').trim(),
        subtitle: String(node.getAttribute('data-item-subtitle') || '').trim(),
        season_number: nSeason,
        episode_number: nEpisode,
      });
    }
  });

  if (!byId.size) return {prev:null, next:null};
  const items = Array.from(byId.values()).sort((a, b) => {
    const sa = _jfInt(a.season_number) ?? 0;
    const sb = _jfInt(b.season_number) ?? 0;
    if (sa !== sb) return sa - sb;
    const ea = _jfInt(a.episode_number) ?? 0;
    const eb = _jfInt(b.episode_number) ?? 0;
    return ea - eb;
  });
  const curId = String(item.item_id || '').trim();
  const curRank = (cur.season * 100000) + cur.episode;
  let prev = null;
  let next = null;
  for (const ep of items) {
    const sNum = _jfInt(ep.season_number);
    const num = _jfInt(ep.episode_number);
    if (sNum == null || num == null) continue;
    const rank = (sNum * 100000) + num;
    if (curId && String(ep.item_id || '') === curId) continue;
    if (rank < curRank) prev = ep;
    if (!next && rank > curRank) next = ep;
  }
  return {prev, next};
}

async function _jfOpenAdjacentEpisode(target, opts){
  const focusItem = !!(opts && opts.focusItem);
  const iid = String((target && target.item_id) || '').trim();
  if (!iid) return;
  await loadJellyfinDetail(iid, {keepDetail: true, preloadThumb: true});
  if (focusItem) {
    const nodes = Array.from(document.querySelectorAll('#jfRows .jfItem'));
    const found = nodes.find((n) => String(n.getAttribute('data-item-id') || '').trim() === iid);
    if (found) found.focus();
  }
}

async function _jfFetchAdjacentEpisodeNav(itemId){
  const iid = String(itemId || '').trim();
  if (!iid) return {prev:null, next:null};
  try {
    const j = await _jfFetchJson(`/jellyfin/item/${encodeURIComponent(iid)}/adjacent`);
    const prev = (j && typeof j.prev === 'object') ? j.prev : null;
    const next = (j && typeof j.next === 'object') ? j.next : null;
    return {prev, next};
  } catch (_e) {
    return {prev:null, next:null};
  }
}

function _jfSetThumbNavButton(btn, target){
  if (!btn) return;
  const iid = String((target && target.item_id) || '').trim();
  if (!iid) {
    btn.disabled = true;
    btn.style.display = 'none';
    btn.onclick = null;
    return;
  }
  btn.disabled = false;
  btn.style.display = '';
  btn.onclick = (e) => {
    e.preventDefault();
    e.stopPropagation();
    _jfOpenAdjacentEpisode(target, {focusItem:false});
  };
}

function _jfPreloadImage(url){
  const src = String(url || '').trim();
  if (!src) return Promise.resolve();
  return new Promise((resolve) => {
    try {
      const img = new Image();
      let done = false;
      const finish = () => {
        if (done) return;
        done = true;
        resolve();
      };
      img.onload = finish;
      img.onerror = finish;
      img.src = src;
      setTimeout(finish, 1200);
    } catch (_e) {
      resolve();
    }
  });
}

function _jfDetailPlaceholder(text){
  const host = document.getElementById('jfDetail');
  if (!host) return;
  host.className = 'jfDetail muted';
  host.textContent = text || 'Select a Jellyfin item to view details.';
}

function _jfApplySelectionUi(){
  document.querySelectorAll('.jfItem.selected').forEach((el) => el.classList.remove('selected'));
  if (!__jfSelectedItemId) return;
  const items = document.querySelectorAll('.jfItem');
  items.forEach((el) => {
    if ((el.getAttribute('data-item-id') || '') === __jfSelectedItemId) el.classList.add('selected');
  });
}

function _jfRenderDetail(item){
  const host = document.getElementById('jfDetail');
  if (!host) return;
  host.className = 'jfDetail';
  host.innerHTML = '';

  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'jfDetailClose';
  closeBtn.textContent = '← Back';
  closeBtn.onclick = () => _jfCloseDetailPanel();
  host.appendChild(closeBtn);

  const isEpisode = String(item && item.type || '').trim().toLowerCase() === 'episode';
  const thumbWrap = document.createElement('div');
  thumbWrap.className = 'jfDetailThumbWrap';

  const thumb = document.createElement('img');
  thumb.className = 'jfDetailThumb';
  thumb.alt = '';
  thumb.loading = 'eager';
  thumb.src = item.thumbnail_local || item.thumbnail || '/pwa/weather/not-available.svg';
  thumb.addEventListener('load', () => _jfPositionDetailPanel(), {once:true});
  thumb.addEventListener('error', () => _jfPositionDetailPanel(), {once:true});
  thumbWrap.appendChild(thumb);

  const prevBtn = document.createElement('button');
  prevBtn.type = 'button';
  prevBtn.className = 'jfThumbNav prev';
  prevBtn.textContent = '<';
  prevBtn.title = 'Previous episode';
  prevBtn.disabled = true;
  prevBtn.style.display = 'none';
  thumbWrap.appendChild(prevBtn);

  const nextBtn = document.createElement('button');
  nextBtn.type = 'button';
  nextBtn.className = 'jfThumbNav next';
  nextBtn.textContent = '>';
  nextBtn.title = 'Next episode';
  nextBtn.disabled = true;
  nextBtn.style.display = 'none';
  thumbWrap.appendChild(nextBtn);

  const navToken = ++__jfDetailNavToken;
  if (isEpisode) {
    _jfFetchAdjacentEpisodeNav(item && item.item_id).then((nav) => {
      if (navToken !== __jfDetailNavToken) return;
      _jfSetThumbNavButton(prevBtn, nav && nav.prev ? nav.prev : null);
      _jfSetThumbNavButton(nextBtn, nav && nav.next ? nav.next : null);
    });
  }

  host.appendChild(thumbWrap);

  const title = document.createElement('div');
  title.className = 'jfDetailTitle';
  title.textContent = item.title || '(untitled)';
  host.appendChild(title);

  const sub = document.createElement('div');
  sub.className = 'jfDetailSub';
  const parts = [];
  if (item.subtitle) parts.push(item.subtitle);
  if (item.year) parts.push(String(item.year));
  const rt = _jfFmtSec(item.runtime_sec);
  if (rt) parts.push(rt);
  if (item.resume_pos && Number(item.resume_pos) > 0) parts.push(`Resume ${_jfFmtSec(item.resume_pos)}`);
  sub.textContent = parts.join(' · ');
  host.appendChild(sub);

  const chips = [];
  if (item.type) chips.push(String(item.type));
  if (item.season_number != null && item.episode_number != null) chips.push(`S${String(item.season_number).padStart(2,'0')}E${String(item.episode_number).padStart(2,'0')}`);
  if (item.resume_pos && Number(item.resume_pos) > 0) chips.push(`Resume ${_jfFmtSec(item.resume_pos)}`);
  if (item.audio_language) chips.push(`Audio ${String(item.audio_language)}`);
  if (item.subtitle_language) chips.push(`Subs ${String(item.subtitle_language)}`);
  if (chips.length) {
    const chipsWrap = document.createElement('div');
    chipsWrap.className = 'jfChips';
    chips.forEach((txt) => {
      const c = document.createElement('span');
      c.className = 'jfChip';
      c.textContent = txt;
      chipsWrap.appendChild(c);
    });
    host.appendChild(chipsWrap);
  }

  const audioAvail = Array.isArray(item.audio_streams)
    ? [...new Set(item.audio_streams.map((s) => String((s && s.language) || '').trim()).filter(Boolean))]
    : [];
  const subAvail = Array.isArray(item.subtitle_streams)
    ? [...new Set(item.subtitle_streams.map((s) => String((s && s.language) || '').trim()).filter(Boolean))]
    : [];
  const streamBits = [];
  if (audioAvail.length) streamBits.push(`Audio: ${audioAvail.slice(0, 6).join(', ')}`);
  if (subAvail.length) streamBits.push(`Subs: ${subAvail.slice(0, 6).join(', ')}`);
  if (streamBits.length) {
    const streamInfo = document.createElement('div');
    streamInfo.className = 'jfDetailSub';
    streamInfo.textContent = streamBits.join(' • ');
    host.appendChild(streamInfo);
  }

  if (item.overview) {
    const body = document.createElement('div');
    body.className = 'jfDetailBody';
    body.textContent = item.overview;
    host.appendChild(body);
  } else {
    const body = document.createElement('div');
    body.className = 'jfDetailBody muted';
    body.textContent = 'No overview available.';
    host.appendChild(body);
  }

  const actions = document.createElement('div');
  actions.className = 'jfActionRow';

  const mkBtn = (label, action) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'btn';
    b.textContent = label;
    b.onclick = () => jellyfinDetailAction(action);
    return b;
  };

  actions.appendChild(mkBtn('Play Now', 'play_now'));
  actions.appendChild(mkBtn('Play Next', 'play_next'));
  actions.appendChild(mkBtn('Play Last', 'play_last'));
  actions.appendChild(mkBtn('Resume', 'resume'));
  host.appendChild(actions);

  const msg = document.createElement('div');
  msg.id = 'jfActionMsg';
  msg.className = 'jfActionMsg';
  host.appendChild(msg);
  _jfOpenDetailPanel();
  requestAnimationFrame(() => _jfPositionDetailPanel());
}

function _jfBuildRowItemCard(item){
  const premiereText = String(item.premiere_date || item.PremiereDate || '').trim();
  const yearFromPremiere = (/^\d{4}/.test(premiereText) ? premiereText.slice(0, 4) : '');
  const titleText = String(
    item.title || item.name || item.Name || item.series_name || item.SeriesName || ''
  ).trim() || '(untitled)';
  const subtitleTextRaw = String(
    item.subtitle || item.Subtitle || item.sub_title || ''
  ).trim();
  const yearText = String(
    item.year || item.production_year || item.ProductionYear || yearFromPremiere || ''
  ).trim();
  const itemType = String(item.type || item.Type || '').trim().toLowerCase();
  let subtitleText = subtitleTextRaw;
  if (itemType === 'movie' && subtitleTextRaw) {
    const m = subtitleTextRaw.match(/\b(19|20)\d{2}\b/);
    if (m && m[0]) subtitleText = m[0];
  }
  if (!subtitleText) {
    subtitleText = yearText || (itemType === 'movie' ? 'Movie' : '');
  }
  const btn = document.createElement('div');
  btn.className = 'jfItem';
  btn.tabIndex = 0;
  btn.setAttribute('role', 'button');
  btn.dataset.itemId = String(item.item_id || '').trim();
  btn.dataset.itemTitle = titleText;
  btn.dataset.itemSubtitle = subtitleText;
  btn.dataset.itemYear = yearText;
  btn.dataset.itemResumePos = String(item.resume_pos != null ? item.resume_pos : '');
  btn.dataset.itemType = itemType;
  btn.dataset.itemSeason = String(item.season_number != null ? item.season_number : '');
  btn.dataset.itemEpisode = String(item.episode_number != null ? item.episode_number : '');
  btn.dataset.itemSeries = String(item.series_name || item.SeriesName || titleText).trim();
  btn.dataset.itemSeriesId = String(item.series_id || '').trim();
  btn.dataset.itemSeasonId = String(item.season_id || '').trim();
  btn.dataset.itemThumb = String(item.thumbnail || '').trim();
  btn.dataset.itemThumbLocal = String(item.thumbnail_local || '').trim();
  btn.setAttribute('aria-label', `${titleText} ${subtitleText}`.trim());

  const tWrap = document.createElement('div');
  tWrap.className = 'jfThumb';
  const img = document.createElement('img');
  img.alt = '';
  img.loading = (__jfActiveTab === 'dashboard') ? 'lazy' : 'eager';
  img.decoding = 'async';
  img.src = item.thumbnail_local || item.thumbnail || '/pwa/weather/not-available.svg';
  tWrap.appendChild(img);

  const meta = document.createElement('div');
  meta.className = 'jfMeta';
  const itTitle = document.createElement('div');
  itTitle.className = 'jfItemTitle';
  itTitle.textContent = titleText;
  const itSub = document.createElement('div');
  itSub.className = 'jfItemSub';
  itSub.textContent = subtitleText;
  meta.appendChild(itTitle);
  meta.appendChild(itSub);

  const iType = itemType;
  if (__jfActiveTab === 'tv' && iType === 'series') {
    const quick = document.createElement('div');
    quick.className = 'jfQuickRow';
    const bView = document.createElement('button');
    bView.type = 'button';
    bView.className = 'jfQuickBtn';
    bView.setAttribute('data-jf-action', 'view_series');
    bView.textContent = 'View';
    const bPlayAll = document.createElement('button');
    bPlayAll.type = 'button';
    bPlayAll.className = 'jfQuickBtn';
    bPlayAll.setAttribute('data-jf-action', 'play_all_series');
    bPlayAll.textContent = 'Play All';
    quick.appendChild(bView);
    quick.appendChild(bPlayAll);
    meta.appendChild(quick);
  } else if (__jfActiveTab === 'tv' && (iType === 'season' || iType === 'nav_back')) {
    const quick = document.createElement('div');
    quick.className = 'jfQuickRow';
    const bView = document.createElement('button');
    bView.type = 'button';
    bView.className = 'jfQuickBtn';
    bView.setAttribute('data-jf-action', 'view_series');
    bView.textContent = iType === 'nav_back' ? 'Back' : 'View';
    quick.appendChild(bView);
    meta.appendChild(quick);
  } else {
    const quick = document.createElement('div');
    quick.className = 'jfQuickRow';
    const bPlay = document.createElement('button');
    bPlay.type = 'button';
    bPlay.className = 'jfQuickBtn';
    bPlay.setAttribute('data-jf-action', 'play_now');
    bPlay.textContent = 'Play Now';
    const bNext = document.createElement('button');
    bNext.type = 'button';
    bNext.className = 'jfQuickBtn';
    bNext.setAttribute('data-jf-action', 'play_last');
    bNext.textContent = 'Queue';
    quick.appendChild(bPlay);
    quick.appendChild(bNext);
    meta.appendChild(quick);
  }

  btn.appendChild(tWrap);
  btn.appendChild(meta);
  return btn;
}

function _jfRenderRows(rows){
  const host = document.getElementById('jfRows');
  if (!host) return;
  host.innerHTML = '';
  if (!Array.isArray(rows) || !rows.length) {
    host.innerHTML = '<div class="muted">No Jellyfin items available.</div>';
    return;
  }
  const hostFrag = document.createDocumentFragment();
  rows.forEach((row) => {
    const rowId = String((row && row.id) || '').trim();
    const isCatalogRow = (__jfActiveTab !== 'dashboard') && (rowId === 'movies' || rowId === 'tv_series' || rowId === 'tv_episodes');
    const hideRowTitle = rowId === 'movies' || rowId === 'tv_series';
    const wrap = document.createElement('div');
    wrap.className = 'jfRow';
    if (isCatalogRow) wrap.classList.add('catalog');
    if (hideRowTitle) wrap.classList.add('catalogNoTitle');
    wrap.dataset.rowId = rowId;

    if (rowId === 'tv_selection') {
      wrap.classList.add('jfTvSelectionRow');
      const selection = document.createElement('button');
      selection.type = 'button';
      selection.className = 'jfTvSelectionBar';
      selection.setAttribute('data-jf-action', 'toggle_tv_season_chooser');
      selection.setAttribute('aria-expanded', row.expanded ? 'true' : 'false');
      selection.setAttribute('aria-label', `${row.title || 'Selected series and season'}. ${row.expanded ? 'Hide' : 'Show'} series and season options.`);
      const label = document.createElement('span');
      label.className = 'jfTvSelectionLabel';
      label.textContent = row.title || 'Selected series and season';
      const hint = document.createElement('span');
      hint.className = 'jfTvSelectionHint';
      hint.textContent = row.expanded ? 'Hide options ▴' : 'Change ▾';
      selection.appendChild(label);
      selection.appendChild(hint);
      wrap.appendChild(selection);
      hostFrag.appendChild(wrap);
      return;
    }

    const title = document.createElement('div');
    title.className = 'jfRowTitle';
    title.textContent = row.title || 'Results';
    wrap.appendChild(title);

    const scroller = document.createElement('div');
    scroller.className = 'jfScroller';
    if (isCatalogRow) scroller.classList.add('jfCatalogScroller');
    if (rowId === 'movies') scroller.classList.add('jfCatalogMovies');
    if (rowId === 'tv_series' || rowId === 'tv_episodes') scroller.classList.add('jfCatalogTv');
    if (rowId === 'tv_episodes') scroller.classList.add('jfCatalogEpisodes');
    if (rowId === 'tv_seasons') scroller.classList.add('jfSeasonWrap');

    const items = Array.isArray(row.items) ? row.items : [];
    if (!items.length) {
      const empty = document.createElement('div');
      empty.className = 'muted';
      empty.textContent = 'No items';
      scroller.appendChild(empty);
    } else {
      const itemFrag = document.createDocumentFragment();
      items.forEach((item) => itemFrag.appendChild(_jfBuildRowItemCard(item)));
      scroller.appendChild(itemFrag);
    }

    wrap.appendChild(scroller);
    hostFrag.appendChild(wrap);
    if (isCatalogRow) {
      const nodes = Array.from(scroller.querySelectorAll('.jfItem'));
      if (!nodes.length) return;
      const update = (showIndicator) => {
        const boxTop = Math.max(0, scroller.scrollTop || 0);
        let pick = nodes.find((node) => {
          const nt = Math.max(0, node.offsetTop || 0);
          return nt >= boxTop;
        }) || nodes[0];
        for (const node of nodes) {
          const nt = Math.max(0, node.offsetTop || 0);
          if (nt <= (boxTop + 6)) pick = node;
          else break;
        }
        const canScroll = (scroller.scrollHeight - scroller.clientHeight) > 8;
        let topPx = null;
        if (canScroll) {
          const grid = document.getElementById('jfGrid');
          if (grid) {
            const gridRect = grid.getBoundingClientRect();
            const scrollRect = scroller.getBoundingClientRect();
            const ratio = Math.max(0, Math.min(1, boxTop / Math.max(1, scroller.scrollHeight - scroller.clientHeight)));
            const trackTop = Math.max(0, scrollRect.top - gridRect.top);
            const thumbRange = Math.max(0, scrollRect.height - 28);
            topPx = trackTop + (ratio * thumbRange);
          }
        }
        _jfSetAlphaIndicator(_jfIndicatorLabelForNode(pick, rowId), {show: !!showIndicator && canScroll, topPx});
      };
      let rafId = 0;
      scroller.addEventListener('scroll', () => {
        if (rafId) return;
        rafId = requestAnimationFrame(() => {
          rafId = 0;
          update(true);
        });
      }, {passive: true});
      update(false);
    }
  });
  host.appendChild(hostFrag);
  _jfApplySelectionUi();
}

function _jfSetBrowseUnavailable(reason){
  const host = document.getElementById('jfRows');
  if (!host) return;
  const wrap = document.createElement('div');
  wrap.className = 'jfUnavailable';
  const title = document.createElement('div');
  title.className = 'jfUnavailableTitle';
  title.textContent = 'Jellyfin is unavailable.';
  const body = document.createElement('div');
  const msg = String(reason || '').trim();
  body.textContent = msg || 'Check credentials/server URL, then reconnect.';
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'btn jfReconnectInline';
  btn.textContent = 'Reconnect';
  wrap.appendChild(title);
  wrap.appendChild(body);
  wrap.appendChild(btn);
  host.innerHTML = '';
  host.appendChild(wrap);
}

async function _jfFetchWithTimeout(url, options, timeoutMs){
  const opts = Object.assign({}, options || {});
  const ms = Number(timeoutMs);
  const useTimeout = Number.isFinite(ms) && ms > 0;
  let timer = 0;
  let controller = null;
  if (useTimeout && typeof AbortController !== 'undefined') {
    controller = new AbortController();
    opts.signal = controller.signal;
    timer = setTimeout(() => {
      try { controller.abort(); } catch (_e) {}
    }, ms);
  }
  try {
    return await fetch(url, opts);
  } catch (e) {
    const name = String(e && e.name || '');
    if (name === 'AbortError') {
      const sec = Math.max(1, Math.round((useTimeout ? ms : __JF_REQ_TIMEOUT_MS) / 1000));
      throw new Error(`Request timed out (${sec}s)`);
    }
    throw e;
  } finally {
    if (timer) clearTimeout(timer);
  }
}

async function _jfFetchJson(url){
  const r = await _jfFetchWithTimeout(url, {cache:'no-store'}, __JF_REQ_TIMEOUT_MS);
  let body = {};
  try { body = await r.json(); } catch (_e) {}
  if (!r.ok) {
    const msg = body.detail || body.reason || `HTTP ${r.status}`;
    throw new Error(String(msg));
  }
  return body;
}

async function loadJellyfinHome(force){
  if (__jfBusy) return;
  __jfBusy = true;
  try {
    _jfSetStatus('Loading…');
    _jfSetConn(false, 'Checking…');
    const j = await _jfFetchJson(`/jellyfin/home?limit=24${force ? '&refresh=1' : ''}`);
    __jfDashboardRows = Array.isArray(j.rows) ? j.rows : [];
    if (__jfActiveTab === 'dashboard') _jfRenderRows(__jfDashboardRows);
    __jfLastMode = 'home';
    __jfLastQuery = '';
    _jfApplySelectionUi();
    const up = !!(j.connected || j.authenticated);
    const reason = String(j.last_error || '').trim();
    _jfSetConn(up, up ? 'Connected' : (reason ? `Unavailable · ${reason}` : 'Unavailable'));
    if (!up) _jfSetBrowseUnavailable(reason);
    _jfSetStatus(j.connected ? 'Ready' : 'Ready (degraded)', j.connected ? 'ok' : '');
  } catch (e) {
    const msg = String(e?.message || e);
    _jfSetBrowseUnavailable(msg);
    _jfDetailPlaceholder('Jellyfin unavailable.');
    _jfSetConn(false, 'Unavailable');
    _jfSetStatus(`Error: ${msg}`, 'err');
  } finally {
    __jfBusy = false;
    _jfFlushPendingSearch();
  }
}

async function runJellyfinSearch(force){
  if (__jfBusy) {
    _jfQueuePendingSearch(force);
    return;
  }
  const q = (document.getElementById('jfSearchInput')?.value || '').trim();
  if (!q) {
    await _jfLoadActiveTabDefault(true);
    return;
  }
  __jfBusy = true;
  try {
    _jfSetStatus(`Searching "${q}"…`);
    _jfSetConn(false, 'Checking…');
    const j = await _jfFetchJson(`/jellyfin/search?q=${encodeURIComponent(q)}&limit=30${force ? '&refresh=1' : ''}`);
    const scopedItems = _jfFilterSearchItems(j.items || []);
    _jfRenderRows([{id:'search', title:_jfSearchTitle(q), items: scopedItems}]);
    __jfLastMode = 'search';
    __jfLastQuery = q;
    _jfApplySelectionUi();
    const up = !!(j.connected || j.authenticated);
    const reason = String(j.last_error || '').trim();
    _jfSetConn(up, up ? 'Connected' : (reason ? `Unavailable · ${reason}` : 'Unavailable'));
    if (!up) _jfSetBrowseUnavailable(reason);
    _jfSetStatus(`${scopedItems.length} result(s)`, 'ok');
  } catch (e) {
    _jfSetBrowseUnavailable(String(e?.message || e));
    _jfSetConn(false, 'Unavailable');
    _jfSetStatus(`Search failed: ${String(e?.message || e)}`, 'err');
  } finally {
    __jfBusy = false;
    _jfFlushPendingSearch();
  }
}

async function loadJellyfinMovies(force){
  if (__jfBusy) return;
  __jfBusy = true;
  try {
    _jfSetStatus('Loading movies…');
    _jfSetConn(false, 'Checking…');
    const qs = new URLSearchParams();
    qs.set('sort', __jfMoviesSort || 'added');
    qs.set('limit', String(__JF_CATALOG_LIMIT));
    qs.set('start', '0');
    if (force) qs.set('refresh', '1');
    const j = await _jfFetchJson(`/jellyfin/movies?${qs.toString()}`);
    const items = Array.isArray(j.items) ? j.items : [];
    __jfMoviesLimit = Math.max(1, Number(j.limit || __JF_CATALOG_LIMIT));
    __jfMoviesCount = Math.max(0, Number(j.count || items.length));
    _jfRenderRows([{id:'movies', title:'Movies', items}]);
    __jfLastMode = 'movies';
    __jfLastQuery = '';
    _jfApplySelectionUi();
    const up = !!(j.connected);
    const reason = String(j.last_error || '').trim();
    _jfSetConn(up, up ? 'Connected' : (reason ? `Unavailable · ${reason}` : 'Unavailable'));
    if (!up) _jfSetBrowseUnavailable(reason);
    _jfSetStatus(`Movies · ${Number(j.count || items.length)} item(s)`, up ? 'ok' : '');
    _jfSyncTabControls();
  } catch (e) {
    const msg = String(e?.message || e);
    _jfSetBrowseUnavailable(msg);
    _jfSetConn(false, 'Unavailable');
    _jfSetStatus(`Movies failed: ${msg}`, 'err');
  } finally {
    __jfBusy = false;
    _jfFlushPendingSearch();
  }
}

async function loadJellyfinTvSeries(force){
  if (__jfBusy) return;
  __jfBusy = true;
  try {
    _jfSetStatus('Loading series…');
    _jfSetConn(false, 'Checking…');
    const qs = new URLSearchParams();
    qs.set('sort', __jfTvSort || 'title_asc');
    qs.set('limit', String(__JF_CATALOG_LIMIT));
    qs.set('start', '0');
    if (force) qs.set('refresh', '1');
    const j = await _jfFetchJson(`/jellyfin/tv/series?${qs.toString()}`);
    const items = Array.isArray(j.items) ? j.items : [];
    __jfTvLimit = Math.max(1, Number(j.limit || __JF_CATALOG_LIMIT));
    __jfTvCount = Math.max(0, Number(j.count || items.length));
    _jfRenderRows([{id:'tv_series', title:'TV Series', items}]);
    __jfLastMode = 'tv';
    __jfLastQuery = '';
    _jfApplySelectionUi();
    __jfTvSeriesId = '';
    __jfTvSeriesTitle = '';
    __jfTvSeriesThumb = '';
    __jfTvSeasonNumber = null;
    __jfTvSeasonChooserExpanded = false;
    __jfTvViewMode = 'series';
    const up = !!(j.connected);
    const reason = String(j.last_error || '').trim();
    _jfSetConn(up, up ? 'Connected' : (reason ? `Unavailable · ${reason}` : 'Unavailable'));
    if (!up) _jfSetBrowseUnavailable(reason);
    _jfSetStatus(`TV · ${Number(j.count || items.length)} series`, up ? 'ok' : '');
    _jfSyncTabControls();
  } catch (e) {
    const msg = String(e?.message || e);
    _jfSetBrowseUnavailable(msg);
    _jfSetConn(false, 'Unavailable');
    _jfSetStatus(`TV failed: ${msg}`, 'err');
  } finally {
    __jfBusy = false;
    _jfFlushPendingSearch();
  }
}

function _jfQueuePendingSearch(force){
  const nextForce = !!force;
  if (__jfPendingSearch && __jfPendingSearch.force) return;
  __jfPendingSearch = {force: nextForce};
}

function _jfFlushPendingSearch(){
  if (!__jfPendingSearch) return;
  const pending = __jfPendingSearch;
  __jfPendingSearch = null;
  _jfScheduleSearch(!!pending.force, 0);
}

function _jfScheduleSearch(force, delayMs){
  if (__jfSearchDebounceTimer) {
    clearTimeout(__jfSearchDebounceTimer);
    __jfSearchDebounceTimer = 0;
  }
  const waitMs = Number.isFinite(Number(delayMs)) ? Math.max(0, Number(delayMs)) : (force ? 0 : 280);
  __jfSearchDebounceTimer = window.setTimeout(() => {
    __jfSearchDebounceTimer = 0;
    runJellyfinSearch(!!force);
  }, waitMs);
}

function _jfFilterSearchItems(items){
  const list = Array.isArray(items) ? items : [];
  if (__jfActiveTab === 'movies') {
    return list.filter((item) => String(item && item.type ? item.type : '').toLowerCase() === 'movie');
  }
  if (__jfActiveTab === 'tv') {
    return list.filter((item) => String(item && item.type ? item.type : '').toLowerCase() === 'series');
  }
  return list;
}

function _jfSearchTitle(q){
  if (__jfActiveTab === 'movies') return `Movies · ${q}`;
  if (__jfActiveTab === 'tv') return `TV · ${q}`;
  return `Search · ${q}`;
}

function _jfLoadActiveTabDefault(force){
  _jfCloseDetailPanel();
  if (__jfActiveTab === 'dashboard') {
    loadJellyfinHome(!!force);
    return;
  }
  if (__jfActiveTab === 'movies') {
    loadJellyfinMovies(!!force);
    return;
  }
  __jfTvSeriesId = '';
  __jfTvSeriesTitle = '';
  __jfTvSeriesThumb = '';
  __jfTvSeasonNumber = null;
  __jfTvSeasonChooserExpanded = false;
  loadJellyfinTvSeries(!!force);
}

async function loadJellyfinTvSeriesDetail(seriesId, opts){
  const sid = String(seriesId || '').trim();
  if (!sid) return;
  const title = String((opts && opts.title) || __jfTvSeriesTitle || 'Series').trim();
  const thumb = String(
    (opts && (opts.thumbnail_local || opts.thumbnail)) ||
    __jfTvSeriesThumb ||
    ''
  ).trim();
  const refresh = !!(opts && opts.refresh);
  const chooserExpanded = (opts && typeof opts.chooserExpanded === 'boolean')
    ? opts.chooserExpanded
    : __jfTvSeasonChooserExpanded;
  const focusChooser = !!(opts && opts.focusChooser);
  if (__jfBusy) return;
  __jfBusy = true;
  try {
    _jfSetStatus('Loading seasons…');
    const seasonRes = await _jfFetchJson(`/jellyfin/tv/series/${encodeURIComponent(sid)}/seasons${refresh ? '?refresh=1' : ''}`);
    const seasons = Array.isArray(seasonRes.seasons) ? seasonRes.seasons : [];
    let seasonNum = __jfTvSeasonNumber;
    if (!Number.isFinite(Number(seasonNum))) {
      const first = seasons.find((s) => Number.isFinite(Number(s && s.season_number)));
      seasonNum = first ? Number(first.season_number) : null;
    }

    let epUrl = `/jellyfin/tv/series/${encodeURIComponent(sid)}/episodes`;
    const epQs = new URLSearchParams();
    if (Number.isFinite(Number(seasonNum))) epQs.set('season_number', String(Number(seasonNum)));
    if (refresh) epQs.set('refresh', '1');
    const epQuery = epQs.toString();
    if (epQuery) epUrl += `?${epQuery}`;
    const epRes = await _jfFetchJson(epUrl);
    const episodes = Array.isArray(epRes.episodes) ? epRes.episodes : [];

    const seasonItems = seasons.map((s) => ({
      item_id: `season:${sid}:${String(s && s.season_number || '')}`,
      title: String(s && s.title || 'Season').trim(),
      subtitle: String(s && s.subtitle || '').trim(),
      type: 'season',
      series_id: sid,
      season_id: String(s && s.season_id || '').trim(),
      season_number: Number(s && s.season_number),
      thumbnail: String((s && (s.thumbnail_local || s.thumbnail)) || '').trim(),
      thumbnail_local: String((s && s.thumbnail_local) || '').trim(),
    }));
    const seasonLabel = Number.isFinite(Number(seasonNum)) ? `Season ${Number(seasonNum)}` : 'No season selected';
    const rows = [
      {id:'tv_selection', title:`${title} · ${seasonLabel}`, expanded: chooserExpanded},
    ];
    if (chooserExpanded) {
      rows.push(
        {id:'tv_back', title:`${title}`, items:[{item_id:'tv_back', title:'← Back to Series', subtitle:'Return to all series', type:'nav_back', thumbnail: thumb, thumbnail_local: thumb}]},
        {id:'tv_seasons', title:'Seasons', items: seasonItems},
      );
    }
    rows.push({id:'tv_episodes', title:`Episodes${Number.isFinite(Number(seasonNum)) ? ` · Season ${Number(seasonNum)}` : ''}`, items: episodes});
    __jfTvSeriesId = sid;
    __jfTvSeriesTitle = title;
    __jfTvSeriesThumb = thumb;
    __jfTvSeasonNumber = Number.isFinite(Number(seasonNum)) ? Number(seasonNum) : null;
    __jfTvSeasonChooserExpanded = chooserExpanded;
    __jfTvViewMode = 'detail';
    __jfSelectedItemId = Number.isFinite(Number(seasonNum)) ? `season:${sid}:${Number(seasonNum)}` : '';
    _jfRenderRows(rows);
    _jfApplySelectionUi();
    if (focusChooser) {
      requestAnimationFrame(() => {
        const chooser = document.querySelector('#jfRows .jfTvSelectionBar');
        if (chooser && typeof chooser.focus === 'function') chooser.focus();
      });
    }
    _jfSetStatus(`TV · ${title}`, 'ok');
    _jfSyncTabControls();
  } catch (e) {
    _jfSetStatus(`Series load failed: ${String(e?.message || e)}`, 'err');
  } finally {
    __jfBusy = false;
  }
}

async function _jfPlayAllSeries(seriesId, title){
  const sid = String(seriesId || '').trim();
  if (!sid) return;
  if (__jfActionBusy) {
    _jfSetActionStatus('Action already in progress…', '');
    return;
  }
  __jfActionBusy = true;
  _jfSetActionButtonsDisabled(true);
  _jfSetActionStatus('Queueing series…', '');
  try {
    const r = await _jfFetchWithTimeout(`/jellyfin/tv/series/${encodeURIComponent(sid)}/play_all`, {method: 'POST'}, __JF_REQ_TIMEOUT_MS);
    let j = {};
    try { j = await r.json(); } catch (_e) {}
    if (!r.ok || !j || j.ok === false) {
      const msg = String((j && (j.detail || j.reason || j.error)) || `HTTP ${r.status}`);
      _jfSetActionStatus(`Play All failed: ${msg}`, 'err', 12000);
      return;
    }
    const qn = Number(j.queued_count || 0);
    const label = String(j.series_title || title || '').trim() || 'Series';
    _jfSetActionStatus(`Play All queued: ${label} (${qn} up next)`, 'ok', 8000);
    await refresh();
  } catch (e) {
    _jfSetActionStatus(`Play All failed: ${String(e?.message || e)}`, 'err', 12000);
  } finally {
    __jfActionBusy = false;
    _jfSetActionButtonsDisabled(false);
  }
}

async function reconnectJellyfin(){
  if (__jfBusy) return;
  _jfSetStatus('Reconnecting…');
  try {
    const r = await _jfFetchWithTimeout('/integrations/jellyfin/register', {method:'POST'}, __JF_REQ_TIMEOUT_MS);
    const body = await r.json().catch(() => ({}));
    if (!r.ok || (body && body.ok === false)) {
      const msg = String((body && (body.reason || body.error || body.detail)) || `HTTP ${r.status}`);
      _jfSetConn(false, 'Unavailable');
      _jfSetStatus(`Reconnect failed: ${msg}`, 'err');
      _jfSetBrowseUnavailable(msg);
      return;
    }
    if (__jfLastMode === 'search' && __jfLastQuery) {
      await runJellyfinSearch(true);
      return;
    }
    await _jfLoadActiveTabDefault(true);
  } catch (e) {
    const msg = String(e?.message || e);
    _jfSetConn(false, 'Unavailable');
    _jfSetStatus(`Reconnect failed: ${msg}`, 'err');
    _jfSetBrowseUnavailable(msg);
  }
}

async function loadJellyfinDetail(itemId, opts){
  const iid = String(itemId || '').trim();
  if (!iid) return;
  const keepDetail = !!(opts && opts.keepDetail);
  const preloadThumb = !!(opts && opts.preloadThumb);
  __jfSelectedItemId = iid;
  _jfApplySelectionUi();
  _jfKeepSelectedItemInView(iid);
  if (!keepDetail) {
    _jfOpenDetailPanel();
    _jfDetailPlaceholder('Loading details…');
  }
  try {
    const j = await _jfFetchJson(`/jellyfin/item/${encodeURIComponent(iid)}`);
    __jfSelectedItem = (j && j.item) ? j.item : null;
    if (preloadThumb && __jfSelectedItem) {
      await _jfPreloadImage(__jfSelectedItem.thumbnail_local || __jfSelectedItem.thumbnail || '');
    }
    _jfRenderDetail(__jfSelectedItem || {});
    requestAnimationFrame(() => _jfKeepSelectedItemInView(iid));
  } catch (e) {
    __jfSelectedItem = null;
    _jfOpenDetailPanel();
    _jfDetailPlaceholder(`Failed to load detail: ${String(e?.message || e)}`);
  }
}

function _jfActionMsg(text, kind){
  const el = document.getElementById('jfActionMsg');
  if (!el) return;
  el.classList.remove('ok', 'err');
  if (kind === 'ok' || kind === 'err') el.classList.add(kind);
  el.textContent = text || '';
}

function _jfLightItemFromNode(node){
  if (!node) return null;
  const iid = String(node.getAttribute('data-item-id') || node.dataset.itemId || '').trim();
  if (!iid) return null;
  const out = {
    item_id: iid,
    title: String(node.getAttribute('data-item-title') || node.dataset.itemTitle || '').trim(),
    subtitle: String(node.getAttribute('data-item-subtitle') || node.dataset.itemSubtitle || '').trim(),
  };
  const rpRaw = String(node.getAttribute('data-item-resume-pos') || node.dataset.itemResumePos || '').trim();
  const rp = Number(rpRaw);
  if (Number.isFinite(rp) && rp > 0) out.resume_pos = rp;
  return out;
}

function _jfRowItems(row){
  if (!row) return [];
  return Array.from(row.querySelectorAll('.jfScroller .jfItem'));
}

function _jfKeepSelectedItemInView(itemId){
  const iid = String(itemId || '').trim();
  if (!iid) return;
  const all = Array.from(document.querySelectorAll('#jfRows .jfItem'));
  const node = all.find((n) => String(n.getAttribute('data-item-id') || '').trim() === iid);
  if (!node) return;
  try {
    node.scrollIntoView({block:'nearest', inline:'nearest', behavior:'smooth'});
  } catch (_e) {}
  const scroller = node.closest('.jfScroller');
  if (!scroller) return;
  const nl = node.offsetLeft;
  const nr = nl + node.offsetWidth;
  const sl = scroller.scrollLeft;
  const sr = sl + scroller.clientWidth;
  if (nl < sl || nr > sr) {
    const targetLeft = Math.max(0, Math.round(nl - ((scroller.clientWidth - node.offsetWidth) / 2)));
    try { scroller.scrollTo({left: targetLeft, behavior: 'smooth'}); } catch (_e) { scroller.scrollLeft = targetLeft; }
  }
  const nt = node.offsetTop;
  const nb = nt + node.offsetHeight;
  const st = scroller.scrollTop;
  const sb = st + scroller.clientHeight;
  if (nt < st || nb > sb) {
    const targetTop = Math.max(0, Math.round(nt - ((scroller.clientHeight - node.offsetHeight) / 2)));
    try { scroller.scrollTo({top: targetTop, behavior: 'smooth'}); } catch (_e) { scroller.scrollTop = targetTop; }
  }
}

function _jfMoveHorizontal(item, delta){
  const row = item && item.closest ? item.closest('.jfRow') : null;
  if (!row) return false;
  const items = _jfRowItems(row);
  if (!items.length) return false;
  const idx = items.indexOf(item);
  if (idx < 0) return false;
  const next = items[idx + delta];
  if (!next) return false;
  next.focus();
  return true;
}

function _jfMoveVertical(item, delta){
  const row = item && item.closest ? item.closest('.jfRow') : null;
  if (!row) return false;
  const rows = Array.from(document.querySelectorAll('#jfRows .jfRow'));
  if (!rows.length) return false;
  const rowIdx = rows.indexOf(row);
  if (rowIdx < 0) return false;
  const nextRow = rows[rowIdx + delta];
  if (!nextRow) return false;
  const curItems = _jfRowItems(row);
  const curIdx = Math.max(0, curItems.indexOf(item));
  const nextItems = _jfRowItems(nextRow);
  if (!nextItems.length) return false;
  const target = nextItems[Math.min(curIdx, nextItems.length - 1)];
  target.focus();
  return true;
}

function _jfFocusSelectedItem(){
  const selected = document.querySelector('.jfItem.selected');
  if (selected) {
    selected.focus();
    return true;
  }
  const first = document.querySelector('.jfItem');
  if (first) {
    first.focus();
    return true;
  }
  return false;
}

function _jfFocusDetailPrimary(){
  const btn =
    document.querySelector('#jfDetail .jfThumbNav:not(:disabled)') ||
    document.querySelector('#jfDetail .jfActionRow button');
  if (!btn) return false;
  btn.focus();
  return true;
}

function _jfNotifyAction(target, text, kind){
  const msg = String(text || '');
  const pending = msg.endsWith('…') || msg.endsWith('...');
  const holdMs = pending ? 0 : (kind === 'err' ? 12000 : 8000);
  _jfSetActionStatus(msg, kind, holdMs);
  if (target === 'detail') {
    _jfActionMsg(text, kind);
    return;
  }
}

function _jfSetActionButtonsDisabled(disabled){
  document.querySelectorAll('#jfDetail .jfActionRow button, #jfRows .jfQuickBtn').forEach((b) => {
    b.disabled = !!disabled;
  });
  const searchInput = document.getElementById('jfSearchInput');
  if (searchInput) searchInput.disabled = !!disabled;
  const sortSel = document.getElementById('jfSortSelect');
  if (sortSel) sortSel.disabled = !!disabled || (__jfLastMode === 'search');
  _jfSyncTabControls();
}

async function _jfPerformItemAction(item, kind, target){
  if (__jfActionBusy) {
    _jfNotifyAction(target, 'Action already in progress…', '');
    return {ok: false};
  }
  if (!__jfConnected) {
    _jfNotifyAction(target, 'Jellyfin unavailable. Reconnect first.', 'err');
    return {ok: false};
  }
  __jfActionBusy = true;
  _jfSetActionButtonsDisabled(true);
  const itemId = String(item && item.item_id ? item.item_id : '').trim();
  if (!itemId) {
    _jfNotifyAction(target, 'Select a Jellyfin item first.', 'err');
    __jfActionBusy = false;
    _jfSetActionButtonsDisabled(false);
    return {ok: false};
  }

  const body = {item_id: itemId, command: kind};
  let human = 'Play';
  if (kind === 'play_next') {
    human = 'Play Next';
  } else if (kind === 'play_last') {
    human = 'Queue';
  } else {
    human = (kind === 'resume') ? 'Resume' : 'Play Now';
    if (kind === 'resume') {
      const rp = Number(item.resume_pos);
      if (Number.isFinite(rp) && rp > 0) body.resume_pos = rp;
    }
  }

  _jfNotifyAction(target, `${human}…`, '');
  try {
    const r = await _jfFetchWithTimeout('/jellyfin/action', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify(body),
    }, __JF_REQ_TIMEOUT_MS);
    let j = {};
    try { j = await r.json(); } catch (_e) {}
    if (!r.ok || !j || j.ok === false) {
      const msg = (j && (j.detail || j.reason || j.error)) ? (j.detail || j.reason || j.error) : `HTTP ${r.status}`;
      _jfNotifyAction(target, `Action failed: ${msg}`, 'err');
      return {ok: false};
    }

    if (j && j.suppressed_duplicate_ui_action) {
      _jfNotifyAction(target, 'Ignored duplicate action.', 'ok');
      return {ok: true};
    }
    if (j && j.suppressed_duplicate_command) {
      _jfNotifyAction(target, 'Ignored duplicate command.', 'ok');
      return {ok: true};
    }
    if (j && j.suppressed_duplicate) {
      _jfNotifyAction(target, 'Ignored duplicate play request.', 'ok');
      return {ok: true};
    }

    let msg = `${human} sent.`;
    if (j.action === 'queue_only') {
      const n = Number(j.queued || 0);
      const qlen = Number(j.queue_length || 0);
      msg = n > 0 ? `Queued ${n} item${n === 1 ? '' : 's'} · Queue ${qlen}` : `Already queued · Queue ${qlen}`;
    } else if (j.action === 'play') {
      const np = (j.now_playing && typeof j.now_playing === 'object') ? j.now_playing : {};
      const label = String(np.title || item.title || '').trim();
      if (kind === 'resume') {
        const rp = Number(j.resolved_resume_pos || item.resume_pos || 0);
        const rpTxt = (Number.isFinite(rp) && rp > 0) ? ` from ${_jfFmtSec(rp)}` : '';
        msg = label ? `Now playing: ${label}${rpTxt}` : `Resume started${rpTxt}`;
      } else {
        msg = label ? `Now playing: ${label}` : `${human} started`;
      }
    }

    _jfNotifyAction(target, msg, 'ok');
    await refresh();
    return {ok: true};
  } catch (e) {
    _jfNotifyAction(target, `Action failed: ${String(e?.message || e)}`, 'err');
    return {ok: false};
  } finally {
    __jfActionBusy = false;
    _jfSetActionButtonsDisabled(false);
  }
}

async function jellyfinDetailAction(kind){
  const item = __jfSelectedItem;
  if (!item) {
    _jfActionMsg('Select a Jellyfin item first.', 'err');
    return;
  }
  await _jfPerformItemAction(item, kind, 'detail');
}

function bindJellyfinUi(){
  const launchBtn = document.getElementById('jellyfinOpenBtn');
  const shellBack = document.getElementById('jfShellBackBtn');
  const detailBackdrop = document.getElementById('jfDetailBackdrop');
  const searchInput = document.getElementById('jfSearchInput');
  const sortSelect = document.getElementById('jfSortSelect');
  const rows = document.getElementById('jfRows');
  const detail = document.getElementById('jfDetail');
  const tabBtns = Array.from(document.querySelectorAll('.jfTabBtn'));

  if (!__jfResizeBound) {
    const onResize = () => {
      if (!_jfIsDetailOpen()) return;
      _jfSetDetailScrollLock(true);
      _jfPositionDetailPanel();
    };
    window.addEventListener('resize', onResize, {passive:true});
    window.addEventListener('orientationchange', onResize, {passive:true});
    __jfResizeBound = true;
  }
  if (!__jfViewportBound && window.visualViewport && typeof window.visualViewport.addEventListener === 'function') {
    const onViewportChange = () => {
      if (!_jfIsDetailOpen()) return;
      _jfSetDetailScrollLock(true);
      _jfPositionDetailPanel();
    };
    window.visualViewport.addEventListener('resize', onViewportChange, {passive:true});
    window.visualViewport.addEventListener('scroll', onViewportChange, {passive:true});
    __jfViewportBound = true;
  }

  if (launchBtn) launchBtn.onclick = () => openJellyfinShell();
  if (shellBack) shellBack.onclick = () => closeJellyfinShell();
  if (detailBackdrop) detailBackdrop.onclick = () => _jfCloseDetailPanel();
  tabBtns.forEach((btn) => {
    btn.onclick = () => {
      const tab = String(btn.getAttribute('data-jf-tab') || '').trim();
      _jfSetActiveTab(tab, {refresh:false});
    };
    btn.addEventListener('keydown', (e) => {
      const key = String(e.key || '');
      const idx = tabBtns.indexOf(btn);
      if (idx < 0) return;
      if (key === 'ArrowRight') {
        const next = tabBtns[(idx + 1) % tabBtns.length];
        if (next) {
          next.focus();
          next.click();
          e.preventDefault();
        }
        return;
      }
      if (key === 'ArrowLeft') {
        const prev = tabBtns[(idx - 1 + tabBtns.length) % tabBtns.length];
        if (prev) {
          prev.focus();
          prev.click();
          e.preventDefault();
        }
        return;
      }
      if (key === 'Home') {
        const first = tabBtns[0];
        if (first) {
          first.focus();
          first.click();
          e.preventDefault();
        }
        return;
      }
      if (key === 'End') {
        const last = tabBtns[tabBtns.length - 1];
        if (last) {
          last.focus();
          last.click();
          e.preventDefault();
        }
      }
    });
  });
  if (sortSelect) {
    sortSelect.onchange = () => {
      const v = String(sortSelect.value || '').trim().toLowerCase();
      if (__jfActiveTab === 'movies') {
        __jfMoviesSort = v || 'added';
      }
      if (__jfActiveTab === 'tv') {
        __jfTvSort = v || 'title_asc';
        __jfTvViewMode = 'series';
      }
      _jfSetActiveTab(__jfActiveTab, {refresh:false});
    };
  }

  if (searchInput) {
    searchInput.addEventListener('input', () => _jfScheduleSearch(false));
    searchInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') _jfScheduleSearch(true, 0);
      if (e.key === 'ArrowDown') {
        const first = document.querySelector('.jfItem');
        if (first) {
          first.focus();
          e.preventDefault();
        }
      }
      if (e.key === 'Escape') {
        searchInput.value = '';
        _jfLoadActiveTabDefault(true);
        e.preventDefault();
      }
    });
  }
  if (rows) {
    rows.addEventListener('click', (e) => {
      const reconnect = e.target && e.target.closest ? e.target.closest('.jfReconnectInline') : null;
      if (reconnect) {
        reconnectJellyfin();
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      const chooserToggle = e.target && e.target.closest ? e.target.closest('[data-jf-action="toggle_tv_season_chooser"]') : null;
      if (chooserToggle) {
        _jfToggleTvSeasonChooser();
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      const quick = e.target && e.target.closest ? e.target.closest('.jfQuickBtn') : null;
      const target = e.target && e.target.closest ? e.target.closest('.jfItem') : null;
      if (!target) return;
      const rich = _jfSeriesItemFromNode(target);
      if (__jfActiveTab === 'tv' && _jfIsSeriesNavType(rich)) {
        _jfOpenSeriesDetailFromRich(rich);
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      if (quick) {
        const action = String(quick.getAttribute('data-jf-action') || '').trim();
        if (__jfActiveTab === 'tv' && rich) {
          if (action === 'view_series') {
            _jfOpenSeriesDetailFromRich(rich);
            e.preventDefault();
            e.stopPropagation();
            return;
          }
          if (action === 'play_all_series' && rich.type === 'series') {
            _jfPlayAllSeries(rich.item_id, rich.title);
            e.preventDefault();
            e.stopPropagation();
            return;
          }
        }
        const item = _jfLightItemFromNode(target);
        if (item && action) _jfPerformItemAction(item, action, 'status');
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      const iid = target.dataset.itemId || '';
      loadJellyfinDetail(iid);
    });

    rows.addEventListener('keydown', (e) => {
      const target = e.target && e.target.closest ? e.target.closest('.jfItem') : null;
      if (!target) return;
      const quick = e.target && e.target.closest ? e.target.closest('.jfQuickBtn') : null;
      const iid = String(target.getAttribute('data-item-id') || '').trim();
      const item = _jfLightItemFromNode(target);
      const rich = _jfSeriesItemFromNode(target);
      const key = String(e.key || '');
      if (key === 'ArrowRight') {
        if (quick) {
          const btns = Array.from(target.querySelectorAll('.jfQuickBtn'));
          const idx = btns.indexOf(quick);
          if (idx >= 0 && idx + 1 < btns.length) {
            btns[idx + 1].focus();
            e.preventDefault();
            return;
          }
        }
        if (_jfMoveHorizontal(target, +1)) {
          e.preventDefault();
          return;
        }
        if (_jfFocusDetailPrimary()) {
          e.preventDefault();
          return;
        }
      }
      if (key === 'ArrowLeft') {
        if (quick) {
          const btns = Array.from(target.querySelectorAll('.jfQuickBtn'));
          const idx = btns.indexOf(quick);
          if (idx > 0) {
            btns[idx - 1].focus();
            e.preventDefault();
            return;
          }
        }
        if (_jfMoveHorizontal(target, -1)) {
          e.preventDefault();
          return;
        }
      }
      if (key === 'ArrowDown') {
        if (quick) {
          target.focus();
          e.preventDefault();
          return;
        }
        if (_jfMoveVertical(target, +1)) {
          e.preventDefault();
          return;
        }
      }
      if (key === 'ArrowUp') {
        if (quick) {
          target.focus();
          e.preventDefault();
          return;
        }
        if (_jfMoveVertical(target, -1)) {
          e.preventDefault();
          return;
        }
      }
      if (key === 'Enter') {
        if (__jfActiveTab === 'tv' && _jfIsSeriesNavType(rich)) {
          _jfOpenSeriesDetailFromRich(rich);
          e.preventDefault();
          return;
        }
        if (quick) {
          const action = String(quick.getAttribute('data-jf-action') || '').trim();
          if (__jfActiveTab === 'tv' && rich) {
            if (action === 'view_series') {
              _jfOpenSeriesDetailFromRich(rich);
              e.preventDefault();
              return;
            }
            if (action === 'play_all_series' && rich.type === 'series') {
              _jfPlayAllSeries(rich.item_id, rich.title);
              e.preventDefault();
              return;
            }
          }
          if (item && action) _jfPerformItemAction(item, action, 'status');
          e.preventDefault();
          return;
        }
        if (iid) loadJellyfinDetail(iid);
        e.preventDefault();
        return;
      }
      if ((key === 'p' || key === 'P') && item) {
        _jfPerformItemAction(item, 'play_now', 'status');
        e.preventDefault();
        return;
      }
      if ((key === 'n' || key === 'N') && item) {
        _jfPerformItemAction(item, 'play_next', 'status');
        e.preventDefault();
        return;
      }
      if ((key === 'l' || key === 'L') && item) {
        _jfPerformItemAction(item, 'play_last', 'status');
        e.preventDefault();
        return;
      }
      if ((key === 'r' || key === 'R') && item) {
        _jfPerformItemAction(item, 'resume', 'status');
        e.preventDefault();
      }
    });
  }
  if (detail) {
    detail.addEventListener('keydown', (e) => {
      const navBtn = e.target && e.target.closest ? e.target.closest('.jfThumbNav') : null;
      const actionBtn = e.target && e.target.closest ? e.target.closest('.jfActionRow button') : null;
      if (!navBtn && !actionBtn) return;
      const all = Array.from(detail.querySelectorAll('.jfActionRow button'));
      const idx = actionBtn ? all.indexOf(actionBtn) : -1;
      const key = String(e.key || '');
      if (navBtn) {
        const left = detail.querySelector('.jfThumbNav.prev');
        const right = detail.querySelector('.jfThumbNav.next');
        if (key === 'ArrowRight') {
          if (navBtn === left && right && !right.disabled) {
            right.focus();
            e.preventDefault();
            return;
          }
          if (navBtn === right && all.length) {
            all[0].focus();
            e.preventDefault();
          }
          return;
        }
        if (key === 'ArrowLeft') {
          if (navBtn === right && left && !left.disabled) {
            left.focus();
            e.preventDefault();
            return;
          }
          if (_jfFocusSelectedItem()) e.preventDefault();
          return;
        }
        if (key === 'ArrowDown') {
          if (all.length) {
            all[0].focus();
            e.preventDefault();
          }
          return;
        }
        if (key === 'ArrowUp') {
          if (_jfFocusSelectedItem()) e.preventDefault();
          return;
        }
        if (key === 'Escape') {
          _jfCloseDetailPanel();
          if (_jfFocusSelectedItem()) e.preventDefault();
        }
        return;
      }
      if (idx < 0) return;
      if (key === 'ArrowRight') {
        if (idx + 1 < all.length) {
          all[idx + 1].focus();
          e.preventDefault();
        }
        return;
      }
      if (key === 'ArrowLeft') {
        if (idx > 0) {
          all[idx - 1].focus();
          e.preventDefault();
          return;
        }
        if (_jfFocusSelectedItem()) {
          e.preventDefault();
        }
        return;
      }
      if (key === 'ArrowDown') {
        if (idx + 2 < all.length) {
          all[idx + 2].focus();
          e.preventDefault();
          return;
        }
        if (idx + 1 < all.length) {
          all[idx + 1].focus();
          e.preventDefault();
        }
        return;
      }
      if (key === 'ArrowUp') {
        if (idx - 2 >= 0) {
          all[idx - 2].focus();
          e.preventDefault();
          return;
        }
        if (_jfFocusSelectedItem()) {
          e.preventDefault();
        }
        return;
      }
      if (key === 'Escape') {
        _jfCloseDetailPanel();
        if (_jfFocusSelectedItem()) e.preventDefault();
      }
    });
  }
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && __jfUiVisible) {
      if (_jfIsDetailOpen()) {
        _jfCloseDetailPanel();
      } else {
        closeJellyfinShell();
      }
      e.preventDefault();
      return;
    }
    if (!__jfUiVisible) return;
    const activeTag = (document.activeElement && document.activeElement.tagName) ? document.activeElement.tagName.toLowerCase() : '';
    const typing = activeTag === 'input' || activeTag === 'textarea';
    if (!typing && e.key === '/') {
      if (searchInput) {
        searchInput.focus();
        searchInput.select();
      }
      e.preventDefault();
    }
    if (!typing && (e.key === '1' || e.key === '2' || e.key === '3')) {
      const keyMap = { '1': 'dashboard', '2': 'movies', '3': 'tv' };
      const nextTab = keyMap[e.key];
      if (nextTab) {
        _jfSetActiveTab(nextTab, {refresh:false});
        const tabBtn = document.querySelector(`.jfTabBtn[data-jf-tab="${nextTab}"]`);
        if (tabBtn && typeof tabBtn.focus === 'function') tabBtn.focus();
        e.preventDefault();
      }
    }
    if (!typing && (e.key === 'j' || e.key === 'J')) {
      const first = document.querySelector('.jfItem');
      if (first) {
        first.focus();
        e.preventDefault();
      }
    }
  });
}

function renderStatus(st) {
  if (!st) return;
  if (_uiRefreshInteractionLockActive()) return;
  _jfSetLaunchVisible(_jfCanLaunchFromStatus(st));

  // state pill
  const dot = document.getElementById('dot');
  const state = document.getElementById('state');
  const brand = document.getElementById('appBrandName');
  const sess = st.state || (st.playing ? (st.paused ? 'paused' : 'playing') : 'idle');
  if (brand) brand.textContent = st.device_name || 'RelayTV';
  if (dot) {
    dot.className = 'dot' + (sess === 'playing' ? ' playing' : (sess === 'paused' ? ' paused' : (sess === 'closed' ? ' closed' : '')));
  }
  if (state) state.textContent = sess;

  // now playing
  const np = st.now_playing || {};
  const picon = document.getElementById('picon');
  const hasNow = _hasNowPlayingItem(st, np);
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

  // background thumbnail (YouTube supported; others fall back to none)
  setBg(document.getElementById('nowTopCard'), thumbUrl(np));

  const posTxt = fmtTime(st.position);
  const durTxt = fmtTime(st.duration);

  // Only overwrite the pos readout if not scrubbing
  if (!__scrubbing) document.getElementById('pos').textContent = posTxt;
  document.getElementById('dur').textContent = durTxt;

  _renderRemoteVolume(st.volume);
  const mute = !!st.mute;
  const mb = document.getElementById('muteBtn');
  if (mb){
    // update label/icon subtly
    mb.querySelector('.bIcon').textContent = mute ? '🔇' : '🔈';
    mb.querySelector('span:last-child').textContent = mute ? 'Unmute' : 'Mute';
  }
  document.getElementById('qlen').textContent = st.queue_length || 0;

  // progress bar fill
  if (!__scrubbing && st.position != null && st.duration != null && st.duration > 0) {
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

    setBg(li, thumbUrl(item));

    // Big, faint provider logo behind handle
    const bg = document.createElement('div');
    bg.className = 'qProvBg';
    const bgFav = faviconUrl(item);
    if (bgFav){
      bg.innerHTML = `<img src="${bgFav}" alt="" />`;
      li.appendChild(bg);
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

    const favImg = document.createElement('img');
    favImg.className = 'fav';
    favImg.alt = '';
    favImg.loading = 'lazy';
    favImg.src = faviconUrl(item) || '';

    const tspan = document.createElement('span');
    tspan.className = 'qTitleText';
    tspan.textContent = item.title || item.url || '';

    if (favImg.src) title.appendChild(favImg);
    title.appendChild(tspan);
    const titleBadge = _uploadBadge(item);
    if (titleBadge) title.insertAdjacentHTML('beforeend', titleBadge);

    const chan = document.createElement('div');
    chan.className = 'qChan';
    chan.textContent = displaySub(item) || '';

    body.appendChild(title);
    body.appendChild(chan);

    const del = document.createElement('button');
    del.className = 'qDelBtn';
    del.textContent = '✕';
    del.title = 'Remove from queue';
    del.onclick = () => qRemove(idx);

    li.appendChild(handle);
    li.appendChild(body);
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
  try {
    fast = await _fetchFastPlaybackState();
    st = _mergePlaybackStateIntoStatus(st, fast);
  } catch(_e) {}

  try {
    if (_shouldRefreshFullStatus(st, fast)) {
      const full = await _fetchFullStatus();
      st = fast ? _mergePlaybackStateIntoStatus(full, fast) : full;
    }
  } catch(_e) {
    if (!st) return;
  }

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
  _uiEventMarkAlive();

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
    setBg(row, thumbUrl(it));

    const bgFav = faviconUrl(it);
    if (bgFav){
      const bg = document.createElement('div');
      bg.className = 'histProvBg';
      bg.innerHTML = `<img src="${bgFav}" alt="" />`;
      row.appendChild(bg);
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
      row.appendChild(bar);
    }

    const meta = document.createElement('div');
    meta.className = 'histMeta';

    const title = document.createElement('div');
    title.className = 'histTitle';

    const fav = faviconUrl(it);
    if (fav){
      const favImg = document.createElement('img');
      favImg.className = 'fav';
      favImg.alt = '';
      favImg.loading = 'lazy';
      favImg.src = fav;
      title.appendChild(favImg);
    }

    const tspan = document.createElement('span');
    tspan.className = 'histTitleText';
    tspan.textContent = it.title || it.url || '(unknown)';
    title.appendChild(tspan);
    const titleBadge = _uploadBadge(it);
    if (titleBadge) title.insertAdjacentHTML('beforeend', titleBadge);

    const channel = document.createElement('div');
    channel.className = 'histSub';
    channel.textContent = displaySub(it) || '';

    const sub = document.createElement('div');
    sub.className = 'histSub';
    sub.textContent = `${_fmtTs(it.ts)}  •  ${it.mode || ''}`.trim();

    const progress = document.createElement('div');
    progress.className = 'histSub';
    if (it.completed === true) {
      progress.textContent = 'Completed · 00:00';
    } else {
      const resumePos = Number(it.resume_pos);
      progress.textContent = Number.isFinite(resumePos) && resumePos > 0
        ? `Resume · ${fmtTime(resumePos)}`
        : 'Resume · 00:00';
    }

    const url = document.createElement('div');
    url.className = 'histSub';
    url.textContent = available ? (it.url || '') : 'Playback unavailable: stored upload was removed';

    const btns = document.createElement('div');
    btns.className = 'histBtns';

    const play = document.createElement('button');
    play.textContent = 'Play';
    play.disabled = !available;
    play.onclick = async () => {
      if (!available) return;
      await fetch('/history/play', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({index: idx})});
      closeHistory();
      await refresh();
    };

    const queue = document.createElement('button');
    queue.textContent = 'Queue';
    queue.disabled = !available;
    queue.onclick = async () => {
      if (!available) return;
      if (!it.url) return;
      await fetch('/enqueue', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({url: it.url})});
      await refresh();
    };

    btns.appendChild(play);
    btns.appendChild(queue);

    meta.appendChild(title);
    meta.appendChild(channel);
    meta.appendChild(sub);
    meta.appendChild(progress);
    meta.appendChild(url);
    meta.appendChild(btns);

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

async function loadSettingsUi(){
  const [devRes, setRes, tvRes, jfRes] = await Promise.all([
    fetch('/devices'),
    fetch('/settings'),
    fetch('/tv/status').catch(() => null),
    fetch('/integrations/jellyfin/status').catch(() => null)
  ]);
  const dev = await devRes.json();
  const cur = await setRes.json();
  const tvStatus = (tvRes && tvRes.ok) ? await tvRes.json() : null;
  const jfStatus = (jfRes && jfRes.ok) ? await jfRes.json() : null;
  const deviceName = document.getElementById('setDeviceName');
  const audioDev = document.getElementById('setAudioDev');
  const qual = document.getElementById('setQuality');
  const ytUseInvidious = document.getElementById('setYtUseInvidious');
  const ytInvidiousBase = document.getElementById('setYtInvidiousBase');
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

  if (deviceName) deviceName.value = (cur.device_name || 'RelayTV');
  if (ytUseInvidious) ytUseInvidious.checked = !!cur.youtube_use_invidious;
  if (ytInvidiousBase) ytInvidiousBase.value = (cur.youtube_invidious_base || '');
  if (ytCookiesFile) ytCookiesFile.value = '';
  if (ytCookiesState) {
    ytCookiesState.classList.remove('ok', 'err');
    ytCookiesState.textContent = cur.youtube_cookies_configured ? 'cookies.txt is configured.' : 'No cookies.txt uploaded.';
  }
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
  const ytUploadBtn = document.getElementById('setYtCookiesUploadBtn');
  const ytClearBtn = document.getElementById('setYtCookiesClearBtn');
  const ytCookiesFile = document.getElementById('setYtCookiesFile');
  const ytCookiesState = document.getElementById('setYtCookiesState');

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
      if (!jfServer) { alert('Jellyfin server URL is required.'); return; }
      if (!jfUser) { alert('Jellyfin username is required.'); return; }
      if (!jfPass && !jfPwConfigured) { alert('Jellyfin password is required.'); return; }
    }

    const payload = {
      device_name: deviceName || 'RelayTV',
      audio_device: audioDev,
      quality_mode: (qual ? 'manual' : 'auto_profile'),
      quality_cap: (qual && qual !== 'worst') ? qual : '',
      ytdlp_format: (qual ? qualityToFormat(qual) : ''),
      youtube_use_invidious: ytUseInvidious,
      youtube_invidious_base: ytInvidiousBase,
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
      jellyfin_enabled: jfEnabled,
      jellyfin_server_url: jfServer,
      jellyfin_username: jfUser,
      jellyfin_user_id: jfUserId,
      jellyfin_audio_lang: jfAudioLang,
      jellyfin_sub_lang: jfSubLang,
      jellyfin_playback_mode: (jfPlaybackMode === 'direct' || jfPlaybackMode === 'transcode') ? jfPlaybackMode : 'auto',
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
    const r = await fetch('/settings', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
    if (!r.ok) {
      alert('Failed to save settings');
      return;
    }
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
    // When the modal is open, Enter defaults to Play
    const open = bd && !bd.classList.contains('hidden');
    const target = e.target;
    if (open && e.key === 'Enter' && !(target && target.closest && target.closest('#notifySection'))) submitAddUrl('play');
  });
}

// Bind UI handlers only after the full DOM is parsed. The Settings modal markup
// is defined after this script block in the HTML template.
window.addEventListener('DOMContentLoaded', () => {
  initScrubber();
  initRemoteVolumeSlider();
  primeRemoteVolumeSlider().catch(() => {});
  bindHeaderMenu();
  bindHistoryUi();
  bindAboutUi();
  bindNowLanguageUi();
  bindNowSubtitleUi();
  bindSettingsUi();
  bindAddUrlUi();
  bindJellyfinUi();
  _jfSetShellVisible(false);
  _jfSetActiveTab('dashboard', {refresh:false});
  try { history.replaceState(Object.assign({}, history.state || {}, {relaytv_root: 1}), ''); } catch (_e) {}
  window.addEventListener('popstate', () => {
    if (__uiNavDepth > 0) __uiNavDepth = Math.max(0, __uiNavDepth - 1);
    _uiCloseTopLayerFromNav();
  });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState !== 'visible') return;
    if (!__uiEventSource) connectUiEventStream();
    if (!_uiEventHealthy()) refresh().catch(() => {});
  });
  connectUiEventStream();
  refresh();
  setInterval(() => {
    if (_uiEventHealthy()) return;
    refresh().catch(() => {});
  }, __UI_FALLBACK_REFRESH_MS);
  setInterval(() => {
    if (__uiEventSource) return;
    connectUiEventStream();
  }, __UI_EVENT_RECONNECT_MS);
  setInterval(() => {
    if (document.visibilityState !== 'visible') return;
    if (!__jfUiVisible) return;
    if (__jfActiveTab !== 'dashboard') return;
    if (__jfLastMode === 'search' && __jfLastQuery) return;
    loadJellyfinHome(false);
  }, __JF_DASHBOARD_REFRESH_MS);
});
