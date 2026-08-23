// SPDX-License-Identifier: GPL-3.0-only
// Native, read-only Seerr browser. Request mutations arrive in milestone M4.

let __seerrVisible = false;
let __seerrEnabled = false;
let __seerrConfigured = false;
let __seerrSection = 'trending';
let __seerrQuery = '';
let __seerrPage = 1;
let __seerrTotalPages = 1;
let __seerrItems = [];
let __seerrBrowseController = null;
let __seerrDetailController = null;
let __seerrRequestSerial = 0;
let __seerrSearchTimer = 0;
let __seerrLastFocus = null;
let __seerrRequestPoll = 0;
const __SEERR_REQUEST_POLL_MS = 30000;
const __SEERR_TIMEOUT_MS = 12000;

function _seerrErrorMessage(body, status){
  const detail = body && body.detail;
  if (detail && typeof detail === 'object' && detail.message) return String(detail.message);
  if (typeof detail === 'string') return detail;
  return `Request failed (HTTP ${status})`;
}

async function _seerrFetchJson(url, controller){
  const timer = setTimeout(() => controller.abort(), __SEERR_TIMEOUT_MS);
  try {
    const response = await fetch(url, {cache:'no-store', signal:controller.signal});
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(_seerrErrorMessage(body, response.status));
    return body;
  } finally {
    clearTimeout(timer);
  }
}

function _seerrSetStatus(text, error){
  const el = document.getElementById('seerrBrowseStatus');
  if (!el) return;
  el.textContent = String(text || '');
  el.classList.toggle('err', !!error);
}

function _seerrSetLaunchVisible(visible){
  const button = document.getElementById('seerrOpenBtn');
  if (!button) return;
  button.classList.toggle('show', !!visible);
  button.disabled = !visible;
  if (!visible && __seerrVisible) closeSeerrShell({fromNav:true, force:true});
}

function updateSeerrStatus(status){
  const value = status && typeof status === 'object' ? status : {};
  __seerrEnabled = !!value.enabled;
  __seerrConfigured = !!value.configured;
  _seerrSetLaunchVisible(__seerrEnabled);
  const connection = document.getElementById('seerrConnection');
  const title = document.getElementById('seerrTitle');
  if (title) title.textContent = String(value.application_title || 'Seerr');
  if (connection) {
    const ready = __seerrEnabled && __seerrConfigured && !!value.reachable;
    connection.classList.toggle('up', ready);
    connection.classList.toggle('down', __seerrEnabled && !ready);
    connection.textContent = !__seerrEnabled ? 'Disabled' : (ready ? `Connected${value.version ? ` · ${value.version}` : ''}` : (__seerrConfigured ? 'Unavailable' : 'Setup required'));
  }
  return value;
}

async function refreshSeerrStatus(){
  try {
    const controller = new AbortController();
    return updateSeerrStatus(await _seerrFetchJson('/integrations/seerr/status', controller));
  } catch (_e) {
    return updateSeerrStatus({enabled:__seerrEnabled, configured:__seerrConfigured, reachable:false});
  }
}

function _seerrAbortBrowse(){
  if (__seerrBrowseController) __seerrBrowseController.abort();
  __seerrBrowseController = null;
}

function _seerrCard(item){
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'seerrCard';
  const mediaType = String(item.media_type || 'media');
  const mediaId = Number(item.media_id || 0);
  button.setAttribute('aria-label', `Open ${String(item.title || `${mediaType} ${mediaId}`)}`);
  if (item.poster_url) {
    const image = document.createElement('img');
    image.className = 'seerrPoster';
    image.loading = 'lazy';
    image.alt = '';
    image.src = item.poster_url;
    image.onerror = () => {
      const fallback = document.createElement('div');
      fallback.className = 'seerrPoster seerrPosterFallback';
      fallback.textContent = '✦';
      image.replaceWith(fallback);
    };
    button.appendChild(image);
  } else {
    const fallback = document.createElement('div');
    fallback.className = 'seerrPoster seerrPosterFallback';
    fallback.textContent = '✦';
    button.appendChild(fallback);
  }
  const body = document.createElement('span');
  body.className = 'seerrCardBody';
  const title = document.createElement('span');
  title.className = 'seerrCardTitle';
  title.textContent = String(item.title || `${mediaType === 'tv' ? 'Series' : 'Movie'} #${mediaId}`);
  const meta = document.createElement('span');
  meta.className = 'seerrCardMeta';
  meta.textContent = [item.year || '', mediaType === 'tv' ? 'Series' : 'Movie', item.rating ? `★ ${item.rating}` : ''].filter(Boolean).join(' · ');
  const state = document.createElement('span');
  state.className = 'seerrState';
  state.textContent = String(item.status || item.media_status || 'unknown').replaceAll('_', ' ');
  body.append(title, meta, state);
  button.appendChild(body);
  if (mediaId > 0 && (mediaType === 'movie' || mediaType === 'tv')) {
    button.onclick = () => openSeerrDetail(mediaType, mediaId, title.textContent);
  } else {
    button.disabled = true;
  }
  return button;
}

function _seerrRender(){
  const grid = document.getElementById('seerrGrid');
  const more = document.getElementById('seerrMoreBtn');
  if (!grid || !more) return;
  grid.replaceChildren();
  if (!__seerrItems.length) {
    const empty = document.createElement('div');
    empty.className = 'seerrEmpty';
    empty.textContent = __seerrQuery ? 'No matching movies or series.' : 'Nothing to show yet.';
    grid.appendChild(empty);
  } else {
    __seerrItems.forEach(item => grid.appendChild(_seerrCard(item)));
  }
  more.classList.toggle('hidden', __seerrPage >= __seerrTotalPages || !__seerrItems.length);
}

function _seerrBrowseUrl(page){
  if (__seerrQuery) return `/seerr/search?query=${encodeURIComponent(__seerrQuery)}&page=${page}`;
  if (__seerrSection === 'requests') {
    const filter = document.getElementById('seerrRequestFilter')?.value || 'all';
    return `/seerr/requests?take=40&skip=${(page - 1) * 40}&filter=${encodeURIComponent(filter)}`;
  }
  return `/seerr/discover?section=${encodeURIComponent(__seerrSection)}&page=${page}`;
}

async function loadSeerrBrowse(options){
  const append = !!(options && options.append);
  const page = append ? __seerrPage + 1 : 1;
  _seerrAbortBrowse();
  const controller = new AbortController();
  __seerrBrowseController = controller;
  const serial = ++__seerrRequestSerial;
  _seerrSetStatus(append ? 'Loading more…' : 'Loading…', false);
  const more = document.getElementById('seerrMoreBtn');
  if (more) more.disabled = true;
  try {
    const payload = await _seerrFetchJson(_seerrBrowseUrl(page), controller);
    if (serial !== __seerrRequestSerial || !__seerrVisible) return;
    const rows = Array.isArray(payload.results) ? payload.results : [];
    __seerrItems = append ? __seerrItems.concat(rows) : rows;
    __seerrPage = Number(payload.page || page) || page;
    __seerrTotalPages = Math.max(__seerrPage, Number(payload.total_pages || 1) || 1);
    _seerrRender();
    _seerrSetStatus(`${Number(payload.total_results || __seerrItems.length)} result${Number(payload.total_results || __seerrItems.length) === 1 ? '' : 's'}`, false);
  } catch (error) {
    if (error && error.name === 'AbortError') return;
    if (serial !== __seerrRequestSerial) return;
    if (!append) { __seerrItems = []; _seerrRender(); }
    _seerrSetStatus(error && error.message ? error.message : 'Seerr is unavailable.', true);
  } finally {
    if (__seerrBrowseController === controller) __seerrBrowseController = null;
    if (more) more.disabled = false;
  }
}

function _seerrSelectSection(section){
  __seerrSection = ['trending','movies','tv','requests'].includes(section) ? section : 'trending';
  __seerrQuery = '';
  const search = document.getElementById('seerrSearchInput');
  const filter = document.getElementById('seerrRequestFilter');
  if (search) search.value = '';
  if (filter) filter.classList.toggle('hidden', __seerrSection !== 'requests');
  document.querySelectorAll('.seerrTab').forEach(button => {
    const selected = button.dataset.seerrSection === __seerrSection;
    button.classList.toggle('active', selected);
    button.setAttribute('aria-selected', selected ? 'true' : 'false');
  });
  loadSeerrBrowse({append:false});
}

function _seerrCloseDetailNow(){
  if (__seerrDetailController) __seerrDetailController.abort();
  __seerrDetailController = null;
  const detail = document.getElementById('seerrDetail');
  const backdrop = document.getElementById('seerrDetailBackdrop');
  if (detail) { detail.classList.add('hidden'); detail.setAttribute('aria-hidden', 'true'); detail.replaceChildren(); }
  if (backdrop) { backdrop.classList.add('hidden'); backdrop.setAttribute('aria-hidden', 'true'); }
}

function closeSeerrDetail(options){
  const fromNav = !!(options && options.fromNav);
  if (!fromNav && window.relaytvSeerr.isDetailOpen() && typeof _uiPushLayer === 'function' && __uiNavDepth > 0) {
    try { history.back(); } catch (_e) {}
    return;
  }
  _seerrCloseDetailNow();
}

function _seerrRenderDetail(item){
  const detail = document.getElementById('seerrDetail');
  if (!detail) return;
  detail.replaceChildren();
  const hero = document.createElement('div');
  hero.className = 'seerrDetailHero';
  if (item.backdrop_url) hero.style.backgroundImage = `url("${String(item.backdrop_url).replaceAll('"', '%22')}")`;
  const copy = document.createElement('div');
  copy.className = 'seerrDetailCopy';
  const close = document.createElement('button');
  close.type = 'button'; close.className = 'seerrDetailClose'; close.setAttribute('aria-label', 'Close details'); close.textContent = '✕'; close.onclick = () => closeSeerrDetail();
  const title = document.createElement('h2');
  title.id = 'seerrDetailTitle'; title.textContent = String(item.title || 'Untitled');
  const meta = document.createElement('div');
  meta.className = 'seerrDetailMeta';
  meta.textContent = [item.year || '', item.runtime_minutes ? `${item.runtime_minutes} min` : '', item.rating ? `★ ${item.rating}` : '', String(item.media_status || '').replaceAll('_',' ')].filter(Boolean).join(' · ');
  copy.append(close, title, meta); hero.appendChild(copy);
  const body = document.createElement('div'); body.className = 'seerrDetailBody';
  if (item.tagline) { const tagline = document.createElement('p'); tagline.textContent = String(item.tagline); body.appendChild(tagline); }
  const overview = document.createElement('p'); overview.className = 'seerrDetailOverview'; overview.textContent = String(item.overview || 'No overview available.'); body.appendChild(overview);
  const genres = document.createElement('div'); genres.className = 'seerrGenres';
  (Array.isArray(item.genres) ? item.genres : []).forEach(genre => { const pill = document.createElement('span'); pill.className = 'seerrPill'; pill.textContent = String(genre.name || ''); genres.appendChild(pill); });
  body.appendChild(genres);
  if (Array.isArray(item.seasons) && item.seasons.length) {
    const heading = document.createElement('h3'); heading.textContent = 'Seasons'; body.appendChild(heading);
    const seasons = document.createElement('div'); seasons.className = 'seerrSeasons';
    item.seasons.filter(season => Number(season.season_number) > 0).forEach(season => { const pill = document.createElement('span'); pill.className = 'seerrPill'; pill.textContent = `${String(season.name || `Season ${season.season_number}`)} · ${Number(season.episode_count || 0)} episodes`; seasons.appendChild(pill); });
    body.appendChild(seasons);
  }
  const action = document.createElement('span'); action.className = 'seerrDisabledAction';
  action.textContent = item.playback_available ? 'Playback arrives with the validated library bridge' : (__seerrConfigured ? 'Request actions are not enabled yet' : 'Complete Seerr setup in Settings');
  body.appendChild(action); detail.append(hero, body);
}

async function openSeerrDetail(mediaType, mediaId, fallbackTitle){
  if (__seerrDetailController) __seerrDetailController.abort();
  const controller = new AbortController(); __seerrDetailController = controller;
  const detail = document.getElementById('seerrDetail');
  const backdrop = document.getElementById('seerrDetailBackdrop');
  if (!detail || !backdrop) return;
  detail.classList.remove('hidden'); detail.setAttribute('aria-hidden', 'false');
  backdrop.classList.remove('hidden'); backdrop.setAttribute('aria-hidden', 'false');
  detail.textContent = `Loading ${String(fallbackTitle || 'details')}…`;
  _uiPushLayer();
  try {
    const item = await _seerrFetchJson(`/seerr/item/${encodeURIComponent(mediaType)}/${Number(mediaId)}`, controller);
    if (__seerrDetailController !== controller || !__seerrVisible) return;
    _seerrRenderDetail(item);
    detail.querySelector('.seerrDetailClose')?.focus();
  } catch (error) {
    if (error && error.name === 'AbortError') return;
    detail.textContent = error && error.message ? error.message : 'Details unavailable.';
  }
}

function openSeerrShell(){
  if (!__seerrEnabled || __seerrVisible) return;
  __seerrLastFocus = document.activeElement;
  __seerrVisible = true;
  const shell = document.getElementById('seerrShell');
  if (shell) { shell.classList.remove('hidden'); shell.setAttribute('aria-hidden', 'false'); }
  document.body.classList.add('seerrNoScroll');
  _uiPushLayer();
  refreshSeerrStatus();
  _seerrSelectSection(__seerrSection);
  document.getElementById('seerrBackBtn')?.focus();
}

function closeSeerrShell(options){
  const fromNav = !!(options && options.fromNav);
  const force = !!(options && options.force);
  if (!fromNav && !force && __seerrVisible && __uiNavDepth > 0) {
    try { history.back(); } catch (_e) {}
    return;
  }
  __seerrVisible = false;
  _seerrAbortBrowse(); _seerrCloseDetailNow();
  const shell = document.getElementById('seerrShell');
  if (shell) { shell.classList.add('hidden'); shell.setAttribute('aria-hidden', 'true'); }
  document.body.classList.remove('seerrNoScroll');
  const focus = __seerrLastFocus && typeof __seerrLastFocus.focus === 'function' ? __seerrLastFocus : document.getElementById('seerrOpenBtn');
  if (focus) requestAnimationFrame(() => focus.focus());
  __seerrLastFocus = null;
}

function bindSeerrUi(){
  document.getElementById('seerrOpenBtn')?.addEventListener('click', openSeerrShell);
  document.getElementById('seerrBackBtn')?.addEventListener('click', () => closeSeerrShell());
  document.getElementById('seerrMoreBtn')?.addEventListener('click', () => loadSeerrBrowse({append:true}));
  document.getElementById('seerrDetailBackdrop')?.addEventListener('click', () => closeSeerrDetail());
  document.querySelectorAll('.seerrTab').forEach(button => button.addEventListener('click', () => _seerrSelectSection(button.dataset.seerrSection)));
  document.getElementById('seerrRequestFilter')?.addEventListener('change', () => loadSeerrBrowse({append:false}));
  document.getElementById('seerrSearchInput')?.addEventListener('input', event => {
    if (__seerrSearchTimer) clearTimeout(__seerrSearchTimer);
    const query = String(event.target.value || '').trim();
    __seerrSearchTimer = setTimeout(() => { __seerrQuery = query; loadSeerrBrowse({append:false}); }, 350);
  });
  window.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || !__seerrVisible) return;
    if (window.relaytvSeerr.isDetailOpen()) closeSeerrDetail(); else closeSeerrShell();
    event.preventDefault();
  });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && __seerrVisible && __seerrSection === 'requests') loadSeerrBrowse({append:false});
  });
  if (__seerrRequestPoll) clearInterval(__seerrRequestPoll);
  __seerrRequestPoll = setInterval(() => {
    if (!__seerrVisible || document.visibilityState !== 'visible' || __seerrSection !== 'requests' || __seerrQuery) return;
    loadSeerrBrowse({append:false});
  }, __SEERR_REQUEST_POLL_MS);
  refreshSeerrStatus();
}

window.relaytvSeerr = {
  isOpen: () => __seerrVisible,
  isDetailOpen: () => !document.getElementById('seerrDetail')?.classList.contains('hidden'),
  close: closeSeerrShell,
  closeDetail: closeSeerrDetail,
  refreshStatus: refreshSeerrStatus,
  updateStatus: updateSeerrStatus,
};
