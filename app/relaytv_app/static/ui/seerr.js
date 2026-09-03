// SPDX-License-Identifier: GPL-3.0-only
// Native, read-only Seerr browser. Request mutations arrive in milestone M4.

let __seerrVisible = false;
let __seerrEnabled = false;
let __seerrConfigured = false;
let __seerrRequestMode = 'disabled';
let __seerrCallerConnected = false;
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
let __seerrQuickFlowId = '';
let __seerrQuickPollTimer = 0;
let __seerrQuickBusy = false;
let __seerrQuickSerial = 0;
const __SEERR_REQUEST_POLL_MS = 30000;
const __SEERR_TIMEOUT_MS = 12000;

function _seerrPopulateRequestUsers(select, usersPayload, configuredUserId){
  if (!select) return;
  select.replaceChildren();

  const defaultOption = document.createElement('option');
  defaultOption.value = '';
  defaultOption.textContent = 'API identity (default)';
  select.appendChild(defaultOption);

  const configuredId = Number(configuredUserId);
  const selectedId = Number.isInteger(configuredId) && configuredId > 0 ? configuredId : 0;
  let selectedIdAvailable = false;
  const users = usersPayload && Array.isArray(usersPayload.users) ? usersPayload.users : [];
  users.forEach(user => {
    const id = Number(user && user.id);
    if (!Number.isInteger(id) || id <= 0) return;
    const option = document.createElement('option');
    option.value = String(id);
    const display = String(user.display_name || user.username || `User ${id}`);
    const username = String(user.username || '');
    option.textContent = username && username !== display ? `${display} (${username})` : display;
    select.appendChild(option);
    if (id === selectedId) selectedIdAvailable = true;
  });

  // A failed lookup, or a user no longer returned by Seerr, must not turn an
  // unrelated settings save into an explicit request-attribution clear.
  if (selectedId && !selectedIdAvailable) {
    const retainedOption = document.createElement('option');
    retainedOption.value = String(selectedId);
    retainedOption.textContent = `Configured user #${selectedId} (unavailable)`;
    select.appendChild(retainedOption);
  }
  select.value = selectedId ? String(selectedId) : '';
}

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
  __seerrRequestMode = ['disabled','shared_admin','caller_session'].includes(value.request_mode) ? value.request_mode : 'disabled';
  __seerrCallerConnected = !!value.caller_connected;
  _seerrSetLaunchVisible(__seerrEnabled);
  const connection = document.getElementById('seerrConnection');
  const title = document.getElementById('seerrTitle');
  if (title) title.textContent = String(value.application_title || 'Seerr');
  if (connection) {
    const ready = __seerrEnabled && __seerrConfigured && !!value.reachable;
    connection.classList.toggle('account', __seerrRequestMode === 'caller_session');
    connection.classList.toggle('up', ready);
    connection.classList.toggle('down', __seerrEnabled && !ready);
    if (__seerrRequestMode === 'caller_session') {
      const identity = value.caller_identity || {};
      const name = String(identity.display_name || identity.username || 'Caller');
      connection.textContent = __seerrCallerConnected ? `${name} · Sign out` : 'Connect account';
      connection.title = __seerrCallerConnected ? 'Sign out of this browser’s Seerr session' : 'Connect this browser to Seerr';
    } else {
      connection.textContent = !__seerrEnabled ? 'Disabled' : (ready ? `Connected${value.version ? ` · ${value.version}` : ''}` : (__seerrConfigured ? 'Unavailable' : 'Setup required'));
      connection.title = '';
    }
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

function _seerrCancelPendingSearch(){
  if (__seerrSearchTimer) clearTimeout(__seerrSearchTimer);
  __seerrSearchTimer = 0;
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
  // Upstream's "unknown" means nobody asked for it; say so in human terms.
  const rawStatus = String(item.status || item.media_status || 'unknown').replaceAll('_', ' ');
  state.textContent = rawStatus === 'unknown' ? 'Not requested' : rawStatus;
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
    // The raw upstream total ("10000 results") is noise for browsed sections;
    // counts only mean something for searches and the requests list.
    const total = Number(payload.total_results || __seerrItems.length);
    const statusText = __seerrQuery
      ? `${total} result${total === 1 ? '' : 's'}`
      : (__seerrSection === 'requests' ? `${total} request${total === 1 ? '' : 's'}` : '');
    _seerrSetStatus(statusText, false);
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
  _seerrCancelPendingSearch();
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
  body.appendChild(_seerrRequestControls(item));
  detail.append(hero, body);
}

function _seerrExistingMovieRequestState(item){
  if (!item || item.media_type !== 'movie') return '';
  const requestStatus = String(item.request && item.request.status || '').trim().toLowerCase();
  if (requestStatus === 'declined' || requestStatus === 'failed') return '';
  const requestLabels = {
    pending: 'Request pending in Seerr',
    approved: 'Request approved in Seerr',
    completed: 'Request completed in Seerr',
  };
  if (requestStatus) return requestLabels[requestStatus] || 'Request already exists in Seerr';
  const mediaStatus = String(item.media_status || '').trim().toLowerCase();
  const mediaLabels = {
    pending: 'Request pending in Seerr',
    processing: 'Request processing in Seerr',
    partially_available: 'Partially available in Seerr',
    available: 'Available in Seerr',
  };
  return mediaLabels[mediaStatus] || '';
}

function _seerrRequestControls(item){
  const controls = document.createElement('div');
  controls.className = 'seerrRequestControls';
  const status = document.createElement('div'); status.className = 'seerrRequestResult'; status.setAttribute('role','status');
  if (item.playback_available) {
    const playback = document.createElement('div'); playback.className = 'seerrRequestButtons';
    const play = document.createElement('button'); play.type = 'button'; play.className = 'seerrRequestBtn seerrPlaybackBtn'; play.textContent = 'Play'; play.onclick = () => _seerrSubmitPlayback(item, 'play_now', play, status);
    const queue = document.createElement('button'); queue.type = 'button'; queue.className = 'seerrRequestBtn secondary'; queue.textContent = 'Add to queue'; queue.onclick = () => _seerrSubmitPlayback(item, 'play_last', queue, status);
    playback.append(play, queue); controls.appendChild(playback);
  }
  if (__seerrRequestMode === 'disabled') {
    const disabled = document.createElement('span'); disabled.className = 'seerrDisabledAction'; disabled.textContent = 'Requests are disabled by the operator'; controls.append(disabled, status); return controls;
  }
  if (__seerrRequestMode === 'caller_session' && !__seerrCallerConnected) {
    const connect = document.createElement('button'); connect.type = 'button'; connect.className = 'seerrRequestBtn'; connect.textContent = 'Connect Seerr account'; connect.onclick = () => window.relaytvSeerr.startQuickConnect(); controls.append(connect, status); return controls;
  }
  if (item.media_type === 'movie') {
    const existingRequest = _seerrExistingMovieRequestState(item);
    if (existingRequest) {
      const existing = document.createElement('span'); existing.className = 'seerrExistingRequest'; existing.textContent = existingRequest; controls.append(existing, status); return controls;
    }
    const button = document.createElement('button'); button.type = 'button'; button.className = 'seerrRequestBtn'; button.textContent = 'Request movie'; button.onclick = () => _seerrSubmitRequest(item, null, button, status); controls.append(button, status); return controls;
  }
  const seasons = (Array.isArray(item.seasons) ? item.seasons : []).filter(season => Number(season.season_number) >= 0);
  if (seasons.length) {
    const choices = document.createElement('div'); choices.className = 'seerrSeasonChoices';
    seasons.forEach(season => {
      const label = document.createElement('label'); label.className = 'seerrSeasonChoice';
      const input = document.createElement('input'); input.type = 'checkbox'; input.value = String(Number(season.season_number));
      const text = document.createElement('span'); text.textContent = String(season.name || `Season ${season.season_number}`);
      label.append(input, text); choices.appendChild(label);
    });
    controls.appendChild(choices);
  }
  const actions = document.createElement('div'); actions.className = 'seerrRequestButtons';
  const selected = document.createElement('button'); selected.type = 'button'; selected.className = 'seerrRequestBtn'; selected.textContent = 'Request selected seasons'; selected.onclick = () => {
    const values = Array.from(controls.querySelectorAll('.seerrSeasonChoice input:checked')).map(input => Number(input.value));
    if (!values.length) { status.textContent = 'Select at least one season.'; status.classList.add('err'); return; }
    _seerrSubmitRequest(item, values, selected, status);
  };
  const all = document.createElement('button'); all.type = 'button'; all.className = 'seerrRequestBtn secondary'; all.textContent = 'Request all seasons'; all.onclick = () => _seerrSubmitRequest(item, 'all', all, status);
  actions.append(selected, all); controls.append(actions, status); return controls;
}

async function _seerrSubmitPlayback(item, command, button, status){
  button.disabled = true; status.classList.remove('err','ok'); status.textContent = command === 'play_now' ? 'Starting playback…' : 'Adding to queue…';
  try {
    const response = await fetch('/seerr/playback', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({media_type:item.media_type, media_id:item.media_id, command})});
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(_seerrErrorMessage(body, response.status));
    status.classList.add('ok');
    status.textContent = command === 'play_now' ? 'Playback started on RelayTV.' : (body.queued ? 'Added to the RelayTV queue.' : 'Playback started because the queue was idle.');
  } catch (error) {
    status.classList.add('err'); status.textContent = error && error.message ? error.message : 'Playback failed.';
  } finally {
    button.disabled = false;
  }
}

async function _seerrSubmitRequest(item, seasons, button, status){
  button.disabled = true; status.classList.remove('err','ok'); status.textContent = 'Sending request…';
  try {
    const response = await fetch('/seerr/requests', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({media_type:item.media_type, media_id:item.media_id, seasons, is_4k:false})});
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(_seerrErrorMessage(body, response.status));
    status.classList.add('ok');
    status.textContent = body.created === false ? 'Nothing new to request.' : 'Request sent to Seerr.';
    loadSeerrBrowse({append:false});
  } catch (error) {
    status.classList.add('err'); status.textContent = error && error.message ? error.message : 'Request failed.'; button.disabled = false;
  }
}

function _seerrShowConnect(show){
  const backdrop = document.getElementById('seerrConnectBackdrop');
  if (!backdrop) return;
  backdrop.classList.toggle('hidden', !show);
  backdrop.setAttribute('aria-hidden', show ? 'false' : 'true');
  if (show) document.getElementById('seerrConnectClose')?.focus();
}

function _seerrStopQuickPoll(){
  if (__seerrQuickPollTimer) clearTimeout(__seerrQuickPollTimer);
  __seerrQuickPollTimer = 0; __seerrQuickBusy = false; __seerrQuickSerial += 1;
}

function closeSeerrQuickConnect(){
  _seerrStopQuickPoll(); __seerrQuickFlowId = ''; _seerrShowConnect(false);
}

async function startSeerrQuickConnect(){
  if (__seerrRequestMode !== 'caller_session') return;
  _seerrStopQuickPoll(); __seerrQuickFlowId = '';
  const serial = __seerrQuickSerial;
  const code = document.getElementById('seerrConnectCode');
  const status = document.getElementById('seerrConnectStatus');
  const retry = document.getElementById('seerrConnectRetry');
  if (code) code.textContent = '······';
  if (status) { status.classList.remove('err','ok'); status.textContent = 'Starting Quick Connect…'; }
  if (retry) retry.classList.add('hidden');
  _seerrShowConnect(true);
  try {
    const response = await fetch('/integrations/seerr/session/quick-connect', {method:'POST'});
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(_seerrErrorMessage(body, response.status));
    if (serial !== __seerrQuickSerial || !__seerrVisible) return;
    __seerrQuickFlowId = String(body.flow_id || '');
    if (!__seerrQuickFlowId) throw new Error('Seerr did not start Quick Connect.');
    if (code) code.textContent = String(body.code || '');
    if (status) status.textContent = 'Waiting for approval in Jellyfin…';
    __seerrQuickPollTimer = setTimeout(_seerrPollQuickConnect, 1200);
  } catch (error) {
    if (status) { status.classList.add('err'); status.textContent = error && error.message ? error.message : 'Quick Connect failed.'; }
    if (retry) retry.classList.remove('hidden');
  }
}

async function _seerrPollQuickConnect(){
  if (!__seerrQuickFlowId || __seerrQuickBusy || !__seerrVisible) return;
  const flowId = __seerrQuickFlowId;
  __seerrQuickBusy = true;
  const status = document.getElementById('seerrConnectStatus');
  const retry = document.getElementById('seerrConnectRetry');
  try {
    const response = await fetch('/integrations/seerr/session/quick-connect/complete', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({flow_id:flowId})});
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(_seerrErrorMessage(body, response.status));
    if (flowId !== __seerrQuickFlowId || !__seerrVisible) return;
    if (!body.connected) {
      __seerrQuickBusy = false;
      __seerrQuickPollTimer = setTimeout(_seerrPollQuickConnect, 2000);
      return;
    }
    if (status) { status.classList.add('ok'); status.textContent = 'Connected. Loading Seerr…'; }
    __seerrQuickFlowId = '';
    await refreshSeerrStatus();
    setTimeout(() => { _seerrShowConnect(false); _seerrCloseDetailNow(); loadSeerrBrowse({append:false}); }, 450);
  } catch (error) {
    __seerrQuickFlowId = '';
    if (status) { status.classList.add('err'); status.textContent = error && error.message ? error.message : 'Quick Connect failed.'; }
    if (retry) retry.classList.remove('hidden');
  } finally {
    __seerrQuickBusy = false;
  }
}

async function _seerrAccountAction(){
  if (__seerrRequestMode !== 'caller_session') return;
  if (!__seerrCallerConnected) { startSeerrQuickConnect(); return; }
  if (!window.confirm('Sign out of Seerr on this browser?')) return;
  try { await fetch('/integrations/seerr/session/logout', {method:'POST'}); } catch (_e) {}
  await refreshSeerrStatus();
  __seerrItems = []; _seerrRender(); _seerrSetStatus('Connect your Seerr account to browse.', false);
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

async function openSeerrShell(){
  if (!__seerrEnabled || __seerrVisible) return;
  __seerrLastFocus = document.activeElement;
  __seerrVisible = true;
  const shell = document.getElementById('seerrShell');
  if (shell) { shell.classList.remove('hidden'); shell.setAttribute('aria-hidden', 'false'); }
  document.body.classList.add('seerrNoScroll');
  _uiPushLayer();
  await refreshSeerrStatus();
  if (__seerrRequestMode === 'caller_session' && !__seerrCallerConnected) {
    startSeerrQuickConnect();
  } else {
    _seerrSelectSection(__seerrSection);
    document.getElementById('seerrBackBtn')?.focus();
  }
}

function closeSeerrShell(options){
  const fromNav = !!(options && options.fromNav);
  const force = !!(options && options.force);
  if (!fromNav && !force && __seerrVisible && __uiNavDepth > 0) {
    try { history.back(); } catch (_e) {}
    return;
  }
  __seerrVisible = false;
  _seerrCancelPendingSearch();
  closeSeerrQuickConnect();
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
  document.getElementById('seerrConnection')?.addEventListener('click', _seerrAccountAction);
  document.getElementById('seerrConnectClose')?.addEventListener('click', closeSeerrQuickConnect);
  document.getElementById('seerrConnectRetry')?.addEventListener('click', startSeerrQuickConnect);
  document.querySelectorAll('.seerrTab').forEach(button => button.addEventListener('click', () => _seerrSelectSection(button.dataset.seerrSection)));
  document.getElementById('seerrRequestFilter')?.addEventListener('change', () => loadSeerrBrowse({append:false}));
  document.getElementById('seerrSearchInput')?.addEventListener('input', event => {
    _seerrCancelPendingSearch();
    const query = String(event.target.value || '').trim();
    __seerrSearchTimer = setTimeout(() => { __seerrSearchTimer = 0; __seerrQuery = query; loadSeerrBrowse({append:false}); }, 350);
  });
  window.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || !__seerrVisible) return;
    if (!document.getElementById('seerrConnectBackdrop')?.classList.contains('hidden')) closeSeerrQuickConnect();
    else if (window.relaytvSeerr.isDetailOpen()) closeSeerrDetail();
    else closeSeerrShell();
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
  startQuickConnect: startSeerrQuickConnect,
};
