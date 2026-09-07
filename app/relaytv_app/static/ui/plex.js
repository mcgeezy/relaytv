// SPDX-License-Identifier: GPL-3.0-only
// Plex personal-library browser and playback actions.

let __plexVisible = false;
let __plexReady = false;
let __plexView = 'home';
let __plexQuery = '';
let __plexLibrary = null;
let __plexItems = [];
let __plexNextStart = null;
let __plexRequestController = null;
let __plexRequestSerial = 0;
let __plexSearchTimer = 0;
let __plexLastFocus = null;
let __plexDetailFocus = null;
const __PLEX_TIMEOUT_MS = 12000;
const __PLEX_PAGE_SIZE = 60;

function _plexBrowseErrorMessage(body, status){
  const detail = body && body.detail;
  if (detail && typeof detail === 'object' && detail.message) return String(detail.message);
  if (typeof detail === 'string') return detail;
  return `Request failed (HTTP ${status})`;
}

async function _plexFetchJson(url, controller){
  const timer = setTimeout(() => controller.abort(), __PLEX_TIMEOUT_MS);
  try {
    const response = await fetch(url, {cache:'no-store', signal:controller.signal});
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(_plexBrowseErrorMessage(body, response.status));
    return body;
  } finally {
    clearTimeout(timer);
  }
}

function _plexSetStatus(text, error){
  const node = document.getElementById('plexBrowseStatus');
  if (!node) return;
  node.textContent = String(text || '');
  node.classList.toggle('err', !!error);
}

function updatePlexStatus(status){
  const value = status && typeof status === 'object' ? status : {};
  __plexReady = !!(value.enabled && value.linked && value.server_selected);
  const launch = document.getElementById('plexOpenBtn');
  if (launch) {
    launch.classList.toggle('show', __plexReady);
    launch.disabled = !__plexReady;
  }
  const connection = document.getElementById('plexConnection');
  if (connection) {
    connection.classList.toggle('up', __plexReady);
    connection.classList.toggle('down', !!value.enabled && !__plexReady);
    const server = value.server || {};
    connection.textContent = __plexReady
      ? `Connected · ${String(server.name || 'Plex Media Server')}`
      : (value.enabled ? 'Setup required in Settings' : 'Disabled');
  }
  if (!__plexReady && __plexVisible) closePlexShell({fromNav:true, force:true});
  return value;
}

async function refreshPlexStatus(){
  try {
    const controller = new AbortController();
    return updatePlexStatus(await _plexFetchJson('/integrations/plex/status', controller));
  } catch (_error) {
    return updatePlexStatus({enabled:true, linked:false, server_selected:false});
  }
}

function _plexAbortRequest(){
  if (__plexRequestController) __plexRequestController.abort();
  __plexRequestController = null;
}

function _plexFormatDuration(durationMs){
  const minutes = Math.round(Math.max(0, Number(durationMs || 0)) / 60000);
  if (!minutes) return '';
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return hours ? `${hours}h ${rest}m` : `${rest}m`;
}

function _plexPoster(item){
  if (item.poster_url) {
    const image = document.createElement('img');
    image.className = 'plexPoster';
    image.loading = 'lazy';
    image.alt = '';
    image.src = String(item.poster_url);
    image.onerror = () => {
      const fallback = document.createElement('span');
      fallback.className = 'plexPoster plexPosterFallback';
      fallback.textContent = '›';
      image.replaceWith(fallback);
    };
    return image;
  }
  const fallback = document.createElement('span');
  fallback.className = 'plexPoster plexPosterFallback';
  fallback.textContent = '›';
  return fallback;
}

function _plexMoveCardFocus(source, key){
  if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(key)) return false;
  const grid = source && source.closest ? source.closest('.plexGrid') : null;
  const cards = grid ? Array.from(grid.querySelectorAll('.plexCard')) : [];
  const index = cards.indexOf(source);
  if (index < 0 || cards.length < 2) return false;
  let target = null;
  if (key === 'ArrowLeft' || key === 'ArrowRight') {
    target = cards[index + (key === 'ArrowLeft' ? -1 : 1)] || null;
  } else if (typeof source.getBoundingClientRect === 'function') {
    const sourceRect = source.getBoundingClientRect();
    const sourceX = sourceRect.left + sourceRect.width / 2;
    const sourceY = sourceRect.top + sourceRect.height / 2;
    const direction = key === 'ArrowUp' ? -1 : 1;
    const candidates = cards.filter(card => {
      if (card === source || typeof card.getBoundingClientRect !== 'function') return false;
      const rect = card.getBoundingClientRect();
      return direction * (rect.top + rect.height / 2 - sourceY) > 1;
    });
    candidates.sort((left, right) => {
      const leftRect = left.getBoundingClientRect();
      const rightRect = right.getBoundingClientRect();
      const leftY = Math.abs(leftRect.top + leftRect.height / 2 - sourceY);
      const rightY = Math.abs(rightRect.top + rightRect.height / 2 - sourceY);
      const leftX = Math.abs(leftRect.left + leftRect.width / 2 - sourceX);
      const rightX = Math.abs(rightRect.left + rightRect.width / 2 - sourceX);
      return (leftY - rightY) || (leftX - rightX);
    });
    target = candidates[0] || null;
  }
  if (!target || typeof target.focus !== 'function') return false;
  target.focus();
  if (typeof target.scrollIntoView === 'function') {
    target.scrollIntoView({block:'nearest', inline:'nearest'});
  }
  return true;
}

function _plexCard(item){
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'plexCard';
  button.dataset.itemId = String(item.id || '');
  button.dataset.itemTitle = String(item.title || 'Untitled');
  button.setAttribute('aria-label', `Open ${String(item.title || 'Plex item')}`);
  button.appendChild(_plexPoster(item));
  const body = document.createElement('span');
  body.className = 'plexCardBody';
  const title = document.createElement('span');
  title.className = 'plexCardTitle';
  title.textContent = String(item.title || 'Untitled');
  const meta = document.createElement('span');
  meta.className = 'plexCardMeta';
  meta.textContent = [item.subtitle || '', item.year || '', _plexFormatDuration(item.duration_ms)].filter(Boolean).join(' · ');
  body.append(title, meta);
  if (item.watched) {
    const watched = document.createElement('span');
    watched.className = 'plexWatched';
    watched.textContent = 'Watched';
    body.appendChild(watched);
  }
  button.appendChild(body);
  const progressValue = Math.max(0, Math.min(100, Number(item.progress || 0)));
  if (progressValue > 0 && progressValue < 100) {
    const progress = document.createElement('span');
    progress.className = 'plexProgress';
    const fill = document.createElement('span');
    fill.style.width = `${progressValue}%`;
    progress.appendChild(fill);
    button.appendChild(progress);
  }
  button.onclick = () => openPlexDetail(String(item.id || ''), title.textContent, button);
  button.addEventListener('keydown', event => {
    if (!_plexMoveCardFocus(button, event.key)) return;
    event.preventDefault();
    event.stopPropagation();
  });
  return button;
}

function _plexGrid(items){
  const grid = document.createElement('div');
  grid.className = 'plexGrid';
  (Array.isArray(items) ? items : []).forEach(item => grid.appendChild(_plexCard(item)));
  return grid;
}

function _plexEmpty(text){
  const node = document.createElement('div');
  node.className = 'plexBrowseStatus';
  node.textContent = String(text || 'Nothing to show.');
  return node;
}

function _plexRenderRows(rows){
  const content = document.getElementById('plexContent');
  if (!content) return;
  content.replaceChildren();
  const values = Array.isArray(rows) ? rows : [];
  if (!values.length) {
    content.appendChild(_plexEmpty('Nothing from this Plex server yet.'));
    return;
  }
  values.forEach(row => {
    const section = document.createElement('section');
    section.className = 'plexRow';
    const heading = document.createElement('h2');
    heading.textContent = String(row.title || 'Plex');
    section.append(heading, _plexGrid(row.items));
    content.appendChild(section);
  });
}

function _plexRenderItems(items, emptyText){
  const content = document.getElementById('plexContent');
  if (!content) return;
  content.replaceChildren();
  if (!items.length) content.appendChild(_plexEmpty(emptyText));
  else content.appendChild(_plexGrid(items));
}

function _plexRenderLibraries(libraries){
  const content = document.getElementById('plexContent');
  if (!content) return;
  content.replaceChildren();
  const values = Array.isArray(libraries) ? libraries : [];
  if (!values.length) {
    content.appendChild(_plexEmpty('No movie or TV libraries are available.'));
    return;
  }
  const grid = document.createElement('div');
  grid.className = 'plexLibraryGrid';
  values.forEach(library => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'plexLibrary';
    const icon = document.createElement('span');
    icon.className = 'plexLibraryIcon';
    icon.textContent = library.type === 'show' ? '▣' : '▰';
    const copy = document.createElement('span');
    const title = document.createElement('strong');
    title.textContent = String(library.title || 'Plex Library');
    const type = document.createElement('small');
    type.textContent = library.type === 'show' ? 'TV shows' : 'Movies';
    copy.append(title, type);
    button.append(icon, copy);
    button.onclick = () => _plexOpenLibrary(library);
    grid.appendChild(button);
  });
  content.appendChild(grid);
}

function _plexSetActiveTab(view){
  document.querySelectorAll('.plexTab').forEach(button => {
    const active = button.dataset.plexView === view;
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', active ? 'true' : 'false');
  });
}

function _plexSetMoreVisible(visible){
  document.getElementById('plexMoreBtn')?.classList.toggle('hidden', !visible);
}

function _plexBeginRequest(){
  _plexAbortRequest();
  const controller = new AbortController();
  __plexRequestController = controller;
  const serial = ++__plexRequestSerial;
  return {controller, serial};
}

function _plexRequestCurrent(request){
  return __plexVisible && __plexRequestController === request.controller && __plexRequestSerial === request.serial;
}

function _plexFinishRequest(request){
  if (__plexRequestController === request.controller) __plexRequestController = null;
}

async function loadPlexHome(){
  __plexView = 'home';
  __plexQuery = '';
  __plexLibrary = null;
  __plexItems = [];
  __plexNextStart = null;
  _plexSetMoreVisible(false);
  _plexSetActiveTab('home');
  const request = _plexBeginRequest();
  _plexSetStatus('Loading Plex home…', false);
  try {
    const payload = await _plexFetchJson('/plex/home?limit=20', request.controller);
    if (!_plexRequestCurrent(request)) return;
    _plexRenderRows(payload.rows);
    _plexSetStatus('', false);
  } catch (error) {
    if (error && error.name === 'AbortError') return;
    _plexSetStatus(error && error.message ? error.message : 'Plex is unavailable.', true);
    _plexRenderRows([]);
  } finally {
    _plexFinishRequest(request);
  }
}

async function loadPlexLibraries(){
  __plexView = 'libraries';
  __plexQuery = '';
  __plexLibrary = null;
  __plexItems = [];
  __plexNextStart = null;
  _plexSetMoreVisible(false);
  _plexSetActiveTab('libraries');
  const request = _plexBeginRequest();
  _plexSetStatus('Loading libraries…', false);
  try {
    const payload = await _plexFetchJson('/plex/libraries', request.controller);
    if (!_plexRequestCurrent(request)) return;
    _plexRenderLibraries(payload.libraries);
    _plexSetStatus('', false);
  } catch (error) {
    if (error && error.name === 'AbortError') return;
    _plexSetStatus(error && error.message ? error.message : 'Libraries are unavailable.', true);
    _plexRenderLibraries([]);
  } finally {
    _plexFinishRequest(request);
  }
}

async function _plexOpenLibrary(library){
  __plexView = 'library';
  __plexQuery = '';
  __plexLibrary = library;
  __plexItems = [];
  __plexNextStart = 0;
  _plexSetActiveTab('libraries');
  await loadPlexLibraryPage(false);
}

async function loadPlexLibraryPage(append){
  if (!__plexLibrary) return;
  const start = append ? Number(__plexNextStart) : 0;
  if (append && !Number.isInteger(start)) return;
  const request = _plexBeginRequest();
  const more = document.getElementById('plexMoreBtn');
  if (!append) _plexSetMoreVisible(false);
  if (more) more.disabled = true;
  _plexSetStatus(append ? 'Loading more…' : `Loading ${String(__plexLibrary.title || 'library')}…`, false);
  try {
    const params = new URLSearchParams({start:String(start), limit:String(__PLEX_PAGE_SIZE), sort:'title'});
    const path = `/plex/libraries/${encodeURIComponent(String(__plexLibrary.id || ''))}/items?${params.toString()}`;
    const payload = await _plexFetchJson(path, request.controller);
    if (!_plexRequestCurrent(request) || __plexView !== 'library') return;
    const pageItems = Array.isArray(payload.items) ? payload.items : [];
    __plexItems = append ? __plexItems.concat(pageItems) : pageItems;
    __plexNextStart = Number.isInteger(payload.next_start) ? payload.next_start : null;
    _plexRenderItems(__plexItems, 'This library is empty.');
    _plexSetMoreVisible(__plexNextStart !== null);
    const total = Math.max(__plexItems.length, Number(payload.count || 0));
    _plexSetStatus(`${String(__plexLibrary.title || 'Library')} · ${__plexItems.length} of ${total}`, false);
  } catch (error) {
    if (error && error.name === 'AbortError') return;
    _plexSetStatus(error && error.message ? error.message : 'Library items are unavailable.', true);
    if (!append) _plexRenderItems([], 'This library could not be loaded.');
  } finally {
    _plexFinishRequest(request);
    if (more) more.disabled = false;
  }
}

async function searchPlex(query){
  __plexQuery = String(query || '').trim();
  if (!__plexQuery) {
    if (__plexView === 'libraries' || __plexView === 'library') await loadPlexLibraries();
    else await loadPlexHome();
    return;
  }
  __plexView = 'search';
  __plexLibrary = null;
  __plexItems = [];
  __plexNextStart = null;
  _plexSetMoreVisible(false);
  _plexSetActiveTab('');
  const request = _plexBeginRequest();
  _plexSetStatus(`Searching for “${__plexQuery}”…`, false);
  try {
    const payload = await _plexFetchJson(`/plex/search?q=${encodeURIComponent(__plexQuery)}&limit=60`, request.controller);
    if (!_plexRequestCurrent(request) || __plexView !== 'search') return;
    __plexItems = Array.isArray(payload.items) ? payload.items : [];
    _plexRenderItems(__plexItems, 'No matching movies or episodes.');
    _plexSetStatus(`${Number(payload.count || __plexItems.length)} result${Number(payload.count || __plexItems.length) === 1 ? '' : 's'}`, false);
  } catch (error) {
    if (error && error.name === 'AbortError') return;
    _plexSetStatus(error && error.message ? error.message : 'Search failed.', true);
    _plexRenderItems([], 'Search could not be completed.');
  } finally {
    _plexFinishRequest(request);
  }
}

function _plexCloseDetailNow(options){
  const restoreFocus = !(options && options.restoreFocus === false);
  const detail = document.getElementById('plexDetail');
  const backdrop = document.getElementById('plexDetailBackdrop');
  if (detail) {
    detail.classList.add('hidden');
    detail.setAttribute('aria-hidden', 'true');
    detail.replaceChildren();
  }
  if (backdrop) {
    backdrop.classList.add('hidden');
    backdrop.setAttribute('aria-hidden', 'true');
  }
  const focus = __plexDetailFocus;
  __plexDetailFocus = null;
  if (restoreFocus && __plexVisible && focus && focus.isConnected !== false && typeof focus.focus === 'function') {
    requestAnimationFrame(() => focus.focus());
  }
}

function closePlexDetail(options){
  const fromNav = !!(options && options.fromNav);
  if (!fromNav && window.relaytvPlex.isDetailOpen() && __uiNavDepth > 0) {
    try { history.back(); } catch (_error) {}
    return;
  }
  _plexCloseDetailNow();
}

function _plexRenderDetail(item){
  const detail = document.getElementById('plexDetail');
  if (!detail) return;
  detail.replaceChildren();
  const hero = document.createElement('div');
  hero.className = 'plexDetailHero';
  if (item.backdrop_url || item.poster_url) hero.style.backgroundImage = `url("${String(item.backdrop_url || item.poster_url)}")`;
  const copy = document.createElement('div');
  copy.className = 'plexDetailCopy';
  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'plexDetailClose';
  close.setAttribute('aria-label', 'Close details');
  close.textContent = '✕';
  close.onclick = () => closePlexDetail();
  const title = document.createElement('h2');
  title.id = 'plexDetailTitle';
  title.textContent = String(item.title || 'Plex item');
  const meta = document.createElement('div');
  meta.className = 'plexDetailMeta';
  meta.textContent = [item.subtitle || '', item.year || '', item.content_rating || '', _plexFormatDuration(item.duration_ms)].filter(Boolean).join(' · ');
  copy.append(close, title, meta);
  hero.appendChild(copy);
  const body = document.createElement('div');
  body.className = 'plexDetailBody';
  if (item.tagline) {
    const tagline = document.createElement('p');
    tagline.className = 'plexDetailMeta';
    tagline.textContent = String(item.tagline);
    body.appendChild(tagline);
  }
  const summary = document.createElement('p');
  summary.className = 'plexDetailSummary';
  summary.textContent = String(item.summary || 'No summary is available.');
  body.appendChild(summary);
  const genres = Array.isArray(item.genres) ? item.genres : [];
  if (genres.length) {
    const pills = document.createElement('div');
    pills.className = 'plexPills';
    genres.forEach(value => {
      const pill = document.createElement('span');
      pill.className = 'plexPill';
      pill.textContent = String(value);
      pills.appendChild(pill);
    });
    body.appendChild(pills);
  }
  if (item.children_available) {
    const children = document.createElement('section');
    children.className = 'plexChildren';
    const heading = document.createElement('h3');
    heading.textContent = item.type === 'show' ? 'Seasons' : 'Episodes';
    const content = document.createElement('div');
    content.className = 'plexGrid';
    content.textContent = 'Loading…';
    children.append(heading, content);
    body.appendChild(children);
    _plexLoadChildren(String(item.id || ''), content);
  } else if (item.type === 'movie' || item.type === 'episode') {
    const actions = document.createElement('div');
    actions.className = 'plexActions';
    const versions = Array.isArray(item.versions) ? item.versions.filter(version => version && version.id) : [];
    let versionSelect = null;
    if (versions.length > 1) {
      const versionLabel = document.createElement('label');
      versionLabel.className = 'plexVersion';
      const versionTitle = document.createElement('span');
      versionTitle.textContent = 'Version';
      versionSelect = document.createElement('select');
      versionSelect.setAttribute('aria-label', 'Plex media version');
      versions.forEach(version => {
        const option = document.createElement('option');
        option.value = String(version.id);
        option.textContent = String(version.label || 'Media version');
        versionSelect.appendChild(option);
      });
      versionLabel.append(versionTitle, versionSelect);
      actions.appendChild(versionLabel);
    }
    const versionTracks = version => ({
      audio: Array.isArray(version && version.audio_tracks) ? version.audio_tracks.filter(track => track && track.id) : [],
      subtitle: Array.isArray(version && version.subtitle_tracks) ? version.subtitle_tracks.filter(track => track && track.id) : [],
    });
    const selectedVersion = () => versions.find(version => String(version.id) === String(versionSelect ? versionSelect.value : '')) || versions[0] || null;
    const trackSelector = (title, ariaLabel, emptyLabel) => {
      const label = document.createElement('label');
      label.className = 'plexTrack';
      const heading = document.createElement('span');
      heading.textContent = title;
      const select = document.createElement('select');
      select.setAttribute('aria-label', ariaLabel);
      const empty = document.createElement('option');
      empty.value = '';
      empty.textContent = emptyLabel;
      select.appendChild(empty);
      label.append(heading, select);
      actions.appendChild(label);
      return select;
    };
    const hasAudioChoices = versions.some(version => versionTracks(version).audio.length > 1);
    const hasSubtitleChoices = versions.some(version => versionTracks(version).subtitle.length > 0);
    const audioSelect = hasAudioChoices ? trackSelector('Audio', 'Plex audio track', 'Plex default') : null;
    const subtitleSelect = hasSubtitleChoices ? trackSelector('Subtitles', 'Plex subtitle track', 'Off') : null;
    const refreshTrackSelectors = () => {
      const tracks = versionTracks(selectedVersion());
      [[audioSelect, tracks.audio], [subtitleSelect, tracks.subtitle]].forEach(([select, values]) => {
        if (!select) return;
        const empty = select.children[0];
        select.replaceChildren(empty);
        values.forEach(track => {
          const option = document.createElement('option');
          option.value = String(track.id);
          option.textContent = String(track.label || 'Media track');
          select.appendChild(option);
        });
        select.value = '';
      });
    };
    refreshTrackSelectors();
    if (versionSelect) versionSelect.onchange = refreshTrackSelectors;
    const choices = [['Play now', 'play_now']];
    if (Number(item.view_offset_ms || 0) > 0) choices.push(['Resume', 'resume']);
    choices.push(['Play next', 'play_next'], ['Add to queue', 'play_last']);
    choices.forEach(([label, command], index) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = `plexAction${index === 0 ? ' primary' : ''}`;
      button.textContent = label;
      button.onclick = () => _plexRunAction(
        item,
        command,
        button,
        versionSelect ? versionSelect.value : '',
        audioSelect ? audioSelect.value : '',
        subtitleSelect ? subtitleSelect.value : '',
      );
      actions.appendChild(button);
    });
    const result = document.createElement('span');
    result.className = 'plexActionResult';
    result.setAttribute('aria-live', 'polite');
    actions.appendChild(result);
    body.appendChild(actions);
  }
  detail.append(hero, body);
}

async function _plexRunAction(item, command, source, versionId, audioId, subtitleId){
  const actions = source && source.parentElement;
  const result = actions && actions.querySelector('.plexActionResult');
  const buttons = actions ? Array.from(actions.querySelectorAll('button')) : [];
  buttons.forEach(button => { button.disabled = true; });
  if (result) result.textContent = command === 'play_last' || command === 'play_next' ? 'Adding…' : 'Starting…';
  try {
    const response = await fetch('/plex/items/action', {
      method:'POST',
      cache:'no-store',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        item_id:String(item.id || ''),
        command,
        version_id:String(versionId || ''),
        audio_id:String(audioId || ''),
        subtitle_id:String(subtitleId || ''),
      }),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(_plexBrowseErrorMessage(body, response.status));
    if (command === 'play_next' || command === 'play_last') {
      if (result) result.textContent = command === 'play_next' ? 'Added next.' : 'Added to queue.';
    } else {
      closePlexShell();
    }
  } catch (error) {
    if (result) result.textContent = error && error.message ? error.message : 'Plex action failed.';
  } finally {
    buttons.forEach(button => { button.disabled = false; });
  }
}

async function _plexLoadChildren(itemId, host){
  try {
    const controller = new AbortController();
    const payload = await _plexFetchJson(`/plex/items/${encodeURIComponent(itemId)}/children?limit=100`, controller);
    if (!window.relaytvPlex.isDetailOpen()) return;
    host.replaceChildren();
    const items = Array.isArray(payload.items) ? payload.items : [];
    if (!items.length) host.appendChild(_plexEmpty('Nothing is listed here.'));
    else items.forEach(item => host.appendChild(_plexCard(item)));
  } catch (error) {
    if (error && error.name === 'AbortError') return;
    host.replaceChildren(_plexEmpty(error && error.message ? error.message : 'Could not load this list.'));
  }
}

async function openPlexDetail(itemId, fallbackTitle, source){
  const detail = document.getElementById('plexDetail');
  const backdrop = document.getElementById('plexDetailBackdrop');
  if (!detail || !backdrop || !itemId) return;
  __plexDetailFocus = source && typeof source.focus === 'function' ? source : document.activeElement;
  detail.classList.remove('hidden');
  detail.setAttribute('aria-hidden', 'false');
  detail.textContent = `Loading ${String(fallbackTitle || 'details')}…`;
  backdrop.classList.remove('hidden');
  backdrop.setAttribute('aria-hidden', 'false');
  _uiPushLayer();
  try {
    const controller = new AbortController();
    const payload = await _plexFetchJson(`/plex/items/${encodeURIComponent(itemId)}`, controller);
    if (!__plexVisible || !window.relaytvPlex.isDetailOpen()) return;
    _plexRenderDetail(payload.item || {});
    detail.querySelector('.plexDetailClose')?.focus();
  } catch (error) {
    if (error && error.name === 'AbortError') return;
    detail.textContent = error && error.message ? error.message : 'Details are unavailable.';
  }
}

async function openPlexShell(){
  if (!__plexReady || __plexVisible) return;
  __plexLastFocus = document.activeElement;
  __plexVisible = true;
  const shell = document.getElementById('plexShell');
  if (shell) {
    shell.classList.remove('hidden');
    shell.setAttribute('aria-hidden', 'false');
  }
  document.body.classList.add('plexNoScroll');
  _uiPushLayer();
  await refreshPlexStatus();
  if (!__plexReady) return;
  await loadPlexHome();
  document.getElementById('plexBackBtn')?.focus();
}

function closePlexShell(options){
  const fromNav = !!(options && options.fromNav);
  const force = !!(options && options.force);
  if (!fromNav && !force && __plexVisible && __uiNavDepth > 0) {
    try { history.back(); } catch (_error) {}
    return;
  }
  __plexVisible = false;
  if (__plexSearchTimer) clearTimeout(__plexSearchTimer);
  __plexSearchTimer = 0;
  _plexAbortRequest();
  _plexCloseDetailNow({restoreFocus:false});
  const shell = document.getElementById('plexShell');
  if (shell) {
    shell.classList.add('hidden');
    shell.setAttribute('aria-hidden', 'true');
  }
  document.body.classList.remove('plexNoScroll');
  const focus = __plexLastFocus && typeof __plexLastFocus.focus === 'function'
    ? __plexLastFocus
    : document.getElementById('plexOpenBtn');
  if (focus) requestAnimationFrame(() => focus.focus());
  __plexLastFocus = null;
}

function bindPlexUi(){
  document.getElementById('plexOpenBtn')?.addEventListener('click', openPlexShell);
  document.getElementById('plexBackBtn')?.addEventListener('click', () => closePlexShell());
  document.getElementById('plexDetailBackdrop')?.addEventListener('click', () => closePlexDetail());
  document.getElementById('plexMoreBtn')?.addEventListener('click', () => loadPlexLibraryPage(true));
  document.querySelectorAll('.plexTab').forEach(button => button.addEventListener('click', () => {
    if (__plexSearchTimer) clearTimeout(__plexSearchTimer);
    __plexSearchTimer = 0;
    const input = document.getElementById('plexSearchInput');
    if (input) input.value = '';
    if (button.dataset.plexView === 'libraries') loadPlexLibraries();
    else loadPlexHome();
  }));
  document.getElementById('plexSearchInput')?.addEventListener('input', event => {
    if (__plexSearchTimer) clearTimeout(__plexSearchTimer);
    const query = String(event.target.value || '').trim();
    __plexSearchTimer = setTimeout(() => {
      __plexSearchTimer = 0;
      searchPlex(query);
    }, 350);
  });
  window.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || !__plexVisible) return;
    if (window.relaytvPlex.isDetailOpen()) closePlexDetail();
    else closePlexShell();
    event.preventDefault();
  });
  refreshPlexStatus();
}

window.relaytvPlex = {
  isOpen: () => __plexVisible,
  isDetailOpen: () => !document.getElementById('plexDetail')?.classList.contains('hidden'),
  close: closePlexShell,
  closeDetail: closePlexDetail,
  refreshStatus: refreshPlexStatus,
  updateStatus: updatePlexStatus,
};
