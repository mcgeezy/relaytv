// Jellyfin and Emby browse shell. Loaded after app.js.

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
let __jfTvSeriesBackdrop = '';
let __jfTvSeriesOverview = '';
let __jfTvSeriesYear = '';
let __jfTvSeasonNumber = null;
let __jfTvSeasonChooserExpanded = false;
let __jfTvSeasonChooserToggleTimer = 0;
let __jfTvViewMode = 'series';
let __jfLastFocus = null;
let __jfAlphaIndicatorTimer = 0;
let __jfResizeBound = false;
let __jfBrowseRequestId = 0;
let __jfBrowseController = null;
let __jfCatalogPageController = null;
let __jfCatalogObserver = null;
const __JF_CATALOG_PAGE_SIZE = 48;
const __JF_REQ_TIMEOUT_MS = 12000;
const __JF_DASHBOARD_REFRESH_MS = 45000;
const __jfCatalogState = {
  movies: {items: [], itemIds: new Set(), nextStart: null, count: 0, sort: ''},
  tv: {items: [], itemIds: new Set(), nextStart: null, count: 0, sort: ''},
};

let __jfServerType = 'jellyfin';
let __jfServerConfigured = false;
let __jfBrandApplied = false;

function jfBrandName(){
  if (!__jfServerConfigured) return 'Jellyfin / Emby';
  return __jfServerType === 'emby' ? 'Emby' : 'Jellyfin';
}

function applyJfBranding(serverType, serverConfigured){
  const t = String(serverType || '').trim().toLowerCase() === 'emby' ? 'emby' : 'jellyfin';
  const configured = !!serverConfigured;
  if (__jfBrandApplied && t === __jfServerType && configured === __jfServerConfigured) return;
  __jfServerType = t;
  __jfServerConfigured = configured;
  __jfBrandApplied = true;
  const brand = jfBrandName();
  document.querySelectorAll('.jfBrand').forEach(el => { el.textContent = brand; });
  const headLabel = document.getElementById('jfCardHeadLabel');
  if (headLabel) headLabel.textContent = brand.toUpperCase();
  const openBtn = document.getElementById('jellyfinOpenBtn');
  if (openBtn) {
    openBtn.title = `Open ${brand}`;
    openBtn.setAttribute('aria-label', `Open ${brand}`);
  }
  const searchInput = document.getElementById('jfSearchInput');
  if (searchInput) {
    searchInput.placeholder = `Search ${brand} titles…`;
    searchInput.setAttribute('aria-label', `Search ${brand}`);
  }
}

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
    shell.classList.remove('jfSeasonChooserOpen');
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
  const activeTab = (next === 'movies' || next === 'tv') ? next : 'dashboard';
  if (activeTab !== __jfActiveTab) _jfAbortBrowseRequest();
  __jfActiveTab = activeTab;
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
    detail.setAttribute('aria-hidden', 'true');
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
  _jfApplySelectionUi();
  _jfDetailPlaceholder('Select a Jellyfin item to view details.');
}

function _jfOpenDetailPanel(){
  const grid = document.getElementById('jfGrid');
  const wasOpen = _jfIsDetailOpen();
  if (grid) grid.classList.add('detailOpen');
  const detail = document.getElementById('jfDetail');
  if (detail) detail.setAttribute('aria-hidden', 'false');
  if (!wasOpen) _uiPushLayer();
  _jfSetDetailScrollLock(true);
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
  item.backdrop = String(node.getAttribute('data-item-backdrop') || '').trim();
  item.overview = String(node.getAttribute('data-item-overview') || '').trim();
  item.year = String(node.getAttribute('data-item-year') || '').trim();
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
    __jfTvSeasonChooserExpanded = false;
    loadJellyfinTvSeriesDetail(rich.item_id, {
      title: rich.title,
      thumbnail: rich.thumbnail_local || rich.thumbnail || '',
      thumbnail_local: rich.thumbnail_local || '',
      backdrop: rich.backdrop || '',
      overview: rich.overview || '',
      year: rich.year || '',
    });
    return;
  }
  // Episodes (and any future non-series entries) should open item detail panel.
  loadJellyfinDetail(rich.item_id);
}

function _jfToggleTvSeasonChooser(){
  if (!__jfTvSeriesId) return;
  if (__jfBusy) {
    if (!__jfTvSeasonChooserToggleTimer) {
      __jfTvSeasonChooserToggleTimer = window.setTimeout(() => {
        __jfTvSeasonChooserToggleTimer = 0;
        _jfToggleTvSeasonChooser();
      }, 150);
    }
    return;
  }
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
  const busy = /^(loading|searching|checking|reconnecting)/i.test(String(text || '').trim());
  const card = document.getElementById('jellyfinCard');
  const shell = document.getElementById('jellyfinShell');
  if (card) card.setAttribute('aria-busy', busy ? 'true' : 'false');
  if (shell) shell.classList.toggle('jfLoading', busy);
}

function _jfSetConn(up, text){
  __jfConnected = !!up;
  const card = document.getElementById('jellyfinCard');
  const shell = document.getElementById('jellyfinShell');
  const label = document.getElementById('jfConnectionLabel');
  if (card) card.classList.toggle('jfOffline', !__jfConnected);
  if (shell) shell.classList.toggle('jfOffline', !__jfConnected);
  if (label) label.textContent = String(text || (__jfConnected ? 'Connected' : 'Unavailable'));
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

function _jfBindImageFallback(img){
  if (!img) return;
  img.addEventListener('error', () => {
    if (img.dataset.jfFallback === '1') return;
    img.dataset.jfFallback = '1';
    // A broken poster with no label is a dead end; swap in a titled tile.
    const fallbackTitle = String(img.dataset.fallbackTitle || '').trim();
    if (fallbackTitle && img.parentElement) {
      const tile = document.createElement('span');
      tile.className = 'jfThumbFallback';
      tile.textContent = fallbackTitle;
      img.parentElement.appendChild(tile);
      img.remove();
      return;
    }
    img.classList.add('jfImageFallback');
    img.src = '/pwa/weather/not-available.svg';
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
  const itemType = String(item && item.type || '').trim().toLowerCase();
  host.className = `jfDetail jfDetailType-${itemType || 'item'}`;
  host.innerHTML = '';

  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'jfDetailClose';
  closeBtn.textContent = '← Back';
  closeBtn.onclick = () => {
    _jfCloseDetailPanel();
    _jfFocusSelectedItem();
  };
  host.appendChild(closeBtn);

  const isEpisode = itemType === 'episode';
  const thumbWrap = document.createElement('div');
  thumbWrap.className = 'jfDetailThumbWrap';

  const thumb = document.createElement('img');
  thumb.className = 'jfDetailThumb';
  thumb.alt = '';
  thumb.loading = 'eager';
  thumb.src = item.backdrop || item.poster_local || item.poster || item.thumbnail_local || item.thumbnail || '/pwa/weather/not-available.svg';
  _jfBindImageFallback(thumb);
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
  title.id = 'jfDetailTitle';
  title.className = 'jfDetailTitle';
  title.textContent = item.title || '(untitled)';
  host.appendChild(title);

  const sub = document.createElement('div');
  sub.className = 'jfDetailSub';
  const parts = [];
  const detailSubtitle = String(item.subtitle || '').trim();
  const detailYear = String(item.year || '').trim();
  if (detailSubtitle) parts.push(detailSubtitle);
  if (detailYear && !detailSubtitle.includes(detailYear)) parts.push(detailYear);
  const rt = _jfFmtSec(item.runtime_sec);
  if (rt) parts.push(rt);
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

  const mkBtn = (label, action, cls) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'btn' + (cls ? ' ' + cls : '');
    b.textContent = label;
    b.onclick = () => jellyfinDetailAction(action);
    return b;
  };

  const resumeSec = Math.max(0, Number(item.resume_pos || 0));
  if (resumeSec > 0) {
    // With a resume point, "Play Now" vs "Resume" was ambiguous; spell both out.
    actions.appendChild(mkBtn(`Resume ${_jfFmtSec(resumeSec)}`, 'resume', 'primary'));
    actions.appendChild(mkBtn('Play from start', 'play_now'));
  } else {
    actions.appendChild(mkBtn('Play Now', 'play_now', 'primary'));
  }
  actions.appendChild(mkBtn('Play Next', 'play_next'));
  actions.appendChild(mkBtn('Queue Last', 'play_last'));
  host.appendChild(actions);

  const msg = document.createElement('div');
  msg.id = 'jfActionMsg';
  msg.className = 'jfActionMsg';
  host.appendChild(msg);
  _jfOpenDetailPanel();
  requestAnimationFrame(() => closeBtn.focus());
}

function _jfBuildRowItemCard(item, rowId){
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
  const normalizedRowId = String(rowId || '').trim().toLowerCase();
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
  if (itemType) btn.classList.add(`jfType-${itemType.replace(/[^a-z0-9_-]/g, '')}`);
  if (normalizedRowId) btn.classList.add(`jfRowItem-${normalizedRowId.replace(/[^a-z0-9_-]/g, '')}`);
  btn.tabIndex = 0;
  btn.setAttribute('role', 'group');
  btn.setAttribute('aria-roledescription', 'media card');
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
  btn.dataset.itemBackdrop = String(item.backdrop || '').trim();
  btn.dataset.itemOverview = String(item.overview || '').trim();
  btn.setAttribute('aria-label', `${titleText} ${subtitleText}`.trim());

  const tWrap = document.createElement('div');
  tWrap.className = 'jfThumb';
  const img = document.createElement('img');
  img.alt = '';
  img.loading = 'lazy';
  img.decoding = 'async';
  img.dataset.fallbackTitle = itemType === 'episode' ? (subtitleText || titleText) : titleText;
  img.src = item.poster_local || item.poster || item.thumbnail_local || item.thumbnail || '/pwa/weather/not-available.svg';
  _jfBindImageFallback(img);
  tWrap.appendChild(img);

  const badgeText = itemType === 'episode'
    ? ''
    : (yearText || (itemType ? itemType.charAt(0).toUpperCase() + itemType.slice(1) : ''));
  if (badgeText) {
    const badge = document.createElement('span');
    badge.className = 'jfMediaBadge';
    badge.textContent = badgeText;
    tWrap.appendChild(badge);
  }
  const resumePos = Math.max(0, Number(item.resume_pos || 0));
  const runtimeSec = Math.max(0, Number(item.runtime_sec || 0));
  const reportedProgress = Number(item.progress_percent);
  const progress = Number.isFinite(reportedProgress) && reportedProgress > 0
    ? Math.min(100, reportedProgress)
    : (resumePos > 0 && runtimeSec > 0 ? Math.min(100, (resumePos / runtimeSec) * 100) : 0);
  if (progress > 0 && progress < 100) {
    const progressTrack = document.createElement('div');
    progressTrack.className = 'jfMediaProgress';
    progressTrack.setAttribute('role', 'progressbar');
    progressTrack.setAttribute('aria-label', `${Math.round(progress)}% watched`);
    progressTrack.setAttribute('aria-valuemin', '0');
    progressTrack.setAttribute('aria-valuemax', '100');
    progressTrack.setAttribute('aria-valuenow', String(Math.round(progress)));
    const progressFill = document.createElement('span');
    progressFill.style.width = `${progress}%`;
    progressTrack.appendChild(progressFill);
    tWrap.appendChild(progressTrack);
  }

  const meta = document.createElement('div');
  meta.className = 'jfMeta';
  const itTitle = document.createElement('div');
  itTitle.className = 'jfItemTitle';
  itTitle.textContent = itemType === 'episode' ? (subtitleText || 'Episode') : titleText;
  const itSub = document.createElement('div');
  itSub.className = 'jfItemSub';
  itSub.textContent = itemType === 'episode' ? titleText : subtitleText;
  meta.appendChild(itTitle);
  meta.appendChild(itSub);
  // Library hint disambiguates same-title duplicates across libraries.
  const libraryName = String(item.library_name || '').trim();
  if (libraryName) {
    const libChip = document.createElement('div');
    libChip.className = 'jfLibChip';
    libChip.textContent = libraryName;
    meta.appendChild(libChip);
  }

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
    const canResume = resumePos > 0 && normalizedRowId === 'continue_watching';
    bPlay.setAttribute('data-jf-action', canResume ? 'resume' : 'play_now');
    bPlay.textContent = canResume ? 'Resume' : 'Play';
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

function _jfCancelCatalogPagination(){
  if (__jfCatalogObserver) {
    __jfCatalogObserver.disconnect();
    __jfCatalogObserver = null;
  }
  if (__jfCatalogPageController) {
    try { __jfCatalogPageController.abort(); } catch (_e) {}
    __jfCatalogPageController = null;
  }
}

function _jfAbortBrowseRequest(){
  if (__jfBrowseController) {
    try { __jfBrowseController.abort(); } catch (_e) {}
    __jfBrowseController = null;
  }
  _jfCancelCatalogPagination();
  __jfBrowseRequestId += 1;
}

function _jfBeginBrowseRequest(){
  _jfAbortBrowseRequest();
  __jfBrowseController = (typeof AbortController !== 'undefined') ? new AbortController() : null;
  return {
    id: __jfBrowseRequestId,
    controller: __jfBrowseController,
    signal: __jfBrowseController ? __jfBrowseController.signal : undefined,
  };
}

function _jfBrowseRequestIsCurrent(request){
  return !!request && request.id === __jfBrowseRequestId;
}

function _jfFinishBrowseRequest(request){
  if (_jfBrowseRequestIsCurrent(request) && __jfBrowseController === request.controller) {
    __jfBrowseController = null;
  }
}

function _jfIsAbortError(error){
  return String(error && error.name || '') === 'AbortError';
}

function _jfResetCatalogState(kind, sort){
  const state = __jfCatalogState[kind];
  state.items = [];
  state.itemIds = new Set();
  state.nextStart = null;
  state.count = 0;
  state.sort = String(sort || '');
  return state;
}

function _jfAddCatalogItems(state, items){
  const added = [];
  (Array.isArray(items) ? items : []).forEach((item) => {
    const itemId = String(item && item.item_id || '').trim();
    if (itemId && state.itemIds.has(itemId)) return;
    if (itemId) state.itemIds.add(itemId);
    state.items.push(item);
    added.push(item);
  });
  return added;
}

function _jfNextStart(value){
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? Math.floor(parsed) : null;
}

function _jfHasFiniteNumber(value){
  return value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value));
}

function _jfCatalogKindIsActive(kind){
  return (__jfActiveTab === 'movies' && kind === 'movies') ||
    (__jfActiveTab === 'tv' && __jfTvViewMode === 'series' && kind === 'tv');
}

function _jfCatalogEndpoint(kind){
  return kind === 'movies' ? '/jellyfin/movies' : '/jellyfin/tv/series';
}

function _jfCatalogRowId(kind){
  return kind === 'movies' ? 'movies' : 'tv_series';
}

function _jfCatalogStatus(kind, loaded, total){
  const noun = kind === 'movies' ? 'Movies' : 'TV';
  const suffix = kind === 'movies' ? 'item(s)' : 'series';
  const target = Math.max(Number(total || 0), Number(loaded || 0));
  return `${noun} · ${Number(loaded || 0)} of ${target} ${suffix}`;
}

function _jfUpdateCatalogSentinel(kind){
  const sentinel = document.querySelector(`.jfCatalogSentinel[data-jf-catalog="${kind}"]`);
  if (!sentinel) return;
  const state = __jfCatalogState[kind];
  const button = sentinel.querySelector('button');
  const hasMore = state.nextStart !== null;
  sentinel.classList.toggle('complete', !hasMore);
  if (button) {
    button.disabled = !hasMore;
    button.textContent = hasMore
      ? `Load more (${state.items.length} of ${Math.max(state.count, state.items.length)})`
      : `All ${state.items.length} loaded`;
  }
}

function _jfArmCatalogPagination(kind){
  _jfCancelCatalogPagination();
  const rowId = _jfCatalogRowId(kind);
  const scroller = document.querySelector(`.jfRow[data-row-id="${rowId}"] .jfCatalogScroller`);
  if (!scroller) return;
  const sentinel = document.createElement('div');
  sentinel.className = 'jfCatalogSentinel';
  sentinel.dataset.jfCatalog = kind;
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'btn jfCatalogMoreBtn';
  button.onclick = () => _jfLoadNextCatalogPage(kind);
  sentinel.appendChild(button);
  scroller.appendChild(sentinel);
  _jfUpdateCatalogSentinel(kind);
  if (__jfCatalogState[kind].nextStart === null || typeof IntersectionObserver === 'undefined') return;
  __jfCatalogObserver = new IntersectionObserver((entries) => {
    if (entries.some((entry) => entry.isIntersecting)) _jfLoadNextCatalogPage(kind);
  }, {root: null, rootMargin: '120px 0px'});
  __jfCatalogObserver.observe(sentinel);
}

async function _jfLoadNextCatalogPage(kind){
  const state = __jfCatalogState[kind];
  const start = state.nextStart;
  if (start === null || __jfCatalogPageController || !_jfCatalogKindIsActive(kind)) return;
  const expectedSort = kind === 'movies' ? __jfMoviesSort : __jfTvSort;
  if (state.sort !== expectedSort) return;
  const controller = (typeof AbortController !== 'undefined') ? new AbortController() : null;
  __jfCatalogPageController = controller || {abort: () => {}};
  const sentinel = document.querySelector(`.jfCatalogSentinel[data-jf-catalog="${kind}"]`);
  if (sentinel) sentinel.classList.add('loading');
  try {
    const qs = new URLSearchParams();
    qs.set('sort', expectedSort);
    qs.set('limit', String(__JF_CATALOG_PAGE_SIZE));
    qs.set('start', String(start));
    const j = await _jfFetchJson(`${_jfCatalogEndpoint(kind)}?${qs.toString()}`, {
      signal: controller ? controller.signal : undefined,
    });
    if (!_jfCatalogKindIsActive(kind) || state.sort !== expectedSort || state.nextStart !== start) return;
    const added = _jfAddCatalogItems(state, j.items);
    state.count = Math.max(0, Number(j.count || state.items.length));
    const nextStart = _jfNextStart(j.next_start_index);
    state.nextStart = nextStart !== null && nextStart > start ? nextStart : null;
    const liveSentinel = document.querySelector(`.jfCatalogSentinel[data-jf-catalog="${kind}"]`);
    const frag = document.createDocumentFragment();
    added.forEach((item) => frag.appendChild(_jfBuildRowItemCard(item, _jfCatalogRowId(kind))));
    if (liveSentinel && liveSentinel.parentNode) liveSentinel.parentNode.insertBefore(frag, liveSentinel);
    _jfApplySelectionUi();
    _jfUpdateCatalogSentinel(kind);
    _jfSetStatus(_jfCatalogStatus(kind, state.items.length, state.count), 'ok');
  } catch (e) {
    if (!_jfIsAbortError(e)) _jfSetStatus(`More items failed: ${String(e?.message || e)}`, 'err');
  } finally {
    if (__jfCatalogPageController === controller || !controller) __jfCatalogPageController = null;
    const liveSentinel = document.querySelector(`.jfCatalogSentinel[data-jf-catalog="${kind}"]`);
    if (liveSentinel) liveSentinel.classList.remove('loading');
  }
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
    const hideRowTitle = false;
    const wrap = document.createElement('div');
    wrap.className = 'jfRow';
    if (isCatalogRow) wrap.classList.add('catalog');
    if (hideRowTitle) wrap.classList.add('catalogNoTitle');
    wrap.dataset.rowId = rowId;

    if (rowId === 'tv_series_header') {
      wrap.classList.add('jfSeriesHeroRow');
      const hero = document.createElement('section');
      hero.className = 'jfSeriesHero';
      hero.setAttribute('aria-labelledby', 'jfSeriesHeroTitle');
      const art = document.createElement('div');
      art.className = 'jfSeriesHeroArt';
      const artImg = document.createElement('img');
      artImg.alt = '';
      artImg.loading = 'eager';
      artImg.src = row.backdrop || row.thumbnail || '/pwa/weather/not-available.svg';
      _jfBindImageFallback(artImg);
      art.appendChild(artImg);
      const shade = document.createElement('div');
      shade.className = 'jfSeriesHeroShade';
      art.appendChild(shade);
      hero.appendChild(art);

      const content = document.createElement('div');
      content.className = 'jfSeriesHeroContent';
      const back = document.createElement('button');
      back.type = 'button';
      back.className = 'jfSeriesBackBtn';
      back.setAttribute('data-jf-action', 'back_to_series');
      back.textContent = '← Back to Series';
      content.appendChild(back);
      const eyebrow = document.createElement('div');
      eyebrow.className = 'jfSeriesHeroEyebrow';
      eyebrow.textContent = 'TV Series';
      content.appendChild(eyebrow);
      const heroTitle = document.createElement('h2');
      heroTitle.id = 'jfSeriesHeroTitle';
      heroTitle.className = 'jfSeriesHeroTitle';
      heroTitle.textContent = row.title || 'Series';
      content.appendChild(heroTitle);
      const meta = document.createElement('div');
      meta.className = 'jfSeriesHeroMeta';
      meta.textContent = [row.year, row.seasonCount ? `${row.seasonCount} season${row.seasonCount === 1 ? '' : 's'}` : '']
        .filter(Boolean).join(' · ');
      content.appendChild(meta);
      const overview = document.createElement('p');
      overview.className = 'jfSeriesHeroOverview';
      overview.textContent = row.overview || 'Choose a season to browse episodes.';
      content.appendChild(overview);
      const actions = document.createElement('div');
      actions.className = 'jfSeriesHeroActions';
      const chooseSeason = document.createElement('button');
      chooseSeason.type = 'button';
      chooseSeason.className = 'btn jfSeasonChooseBtn';
      chooseSeason.setAttribute('data-jf-action', 'toggle_tv_season_chooser');
      chooseSeason.setAttribute('aria-haspopup', 'dialog');
      chooseSeason.setAttribute('aria-expanded', row.expanded ? 'true' : 'false');
      chooseSeason.textContent = `${row.seasonLabel || 'Choose season'} ▾`;
      const playAll = document.createElement('button');
      playAll.type = 'button';
      playAll.className = 'btn jfSeriesPlayAllBtn';
      playAll.setAttribute('data-jf-action', 'play_all_series_header');
      playAll.dataset.seriesId = String(row.seriesId || '');
      playAll.dataset.seriesTitle = String(row.title || 'Series');
      playAll.textContent = 'Play All';
      actions.appendChild(chooseSeason);
      actions.appendChild(playAll);
      content.appendChild(actions);
      hero.appendChild(content);
      wrap.appendChild(hero);
      hostFrag.appendChild(wrap);
      return;
    }

    if (rowId === 'tv_season_chooser') {
      wrap.classList.add('jfSeasonModalRow');
      const backdrop = document.createElement('button');
      backdrop.type = 'button';
      backdrop.className = 'jfSeasonModalBackdrop';
      backdrop.setAttribute('data-jf-action', 'toggle_tv_season_chooser');
      backdrop.setAttribute('aria-label', 'Close season chooser');
      const dialog = document.createElement('section');
      dialog.className = 'jfSeasonModal';
      dialog.setAttribute('role', 'dialog');
      dialog.setAttribute('aria-modal', 'true');
      dialog.setAttribute('aria-labelledby', 'jfSeasonModalTitle');
      const modalHead = document.createElement('div');
      modalHead.className = 'jfSeasonModalHead';
      const modalTitle = document.createElement('h3');
      modalTitle.id = 'jfSeasonModalTitle';
      modalTitle.textContent = `Choose a season · ${row.title || 'Series'}`;
      const close = document.createElement('button');
      close.type = 'button';
      close.className = 'jfSeasonModalClose';
      close.setAttribute('data-jf-action', 'toggle_tv_season_chooser');
      close.setAttribute('aria-label', 'Close season chooser');
      close.textContent = '✕';
      modalHead.appendChild(modalTitle);
      modalHead.appendChild(close);
      dialog.appendChild(modalHead);
      const options = document.createElement('div');
      options.className = 'jfSeasonOptions';
      (Array.isArray(row.items) ? row.items : []).forEach((season) => {
        const option = document.createElement('button');
        option.type = 'button';
        option.className = 'jfSeasonOption';
        const seasonNumber = Number(season && season.season_number);
        const active = _jfHasFiniteNumber(seasonNumber) && seasonNumber === Number(row.selectedSeason);
        option.classList.toggle('active', active);
        option.setAttribute('aria-pressed', active ? 'true' : 'false');
        option.dataset.jfAction = 'select_tv_season';
        option.dataset.seriesId = String(row.seriesId || '');
        option.dataset.seasonNumber = String(seasonNumber);
        const optionImg = document.createElement('img');
        optionImg.alt = '';
        optionImg.loading = 'lazy';
        optionImg.src = season.thumbnail_local || season.thumbnail || '/pwa/weather/not-available.svg';
        _jfBindImageFallback(optionImg);
        const optionText = document.createElement('span');
        optionText.textContent = String(season.title || `Season ${seasonNumber}`);
        const optionMark = document.createElement('span');
        optionMark.className = 'jfSeasonOptionMark';
        optionMark.textContent = active ? 'Current' : 'View';
        option.appendChild(optionImg);
        option.appendChild(optionText);
        option.appendChild(optionMark);
        options.appendChild(option);
      });
      dialog.appendChild(options);
      wrap.appendChild(backdrop);
      wrap.appendChild(dialog);
      hostFrag.appendChild(wrap);
      return;
    }

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
    // Search reads better as a full list than a cut-off horizontal rail.
    if (rowId === 'search') scroller.classList.add('jfSearchList');

    const items = Array.isArray(row.items) ? row.items : [];
    if (!items.length) {
      const empty = document.createElement('div');
      empty.className = 'muted';
      empty.textContent = 'No items';
      scroller.appendChild(empty);
    } else {
      const itemFrag = document.createDocumentFragment();
      items.forEach((item) => itemFrag.appendChild(_jfBuildRowItemCard(item, rowId)));
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
  let timedOut = false;
  const externalSignal = opts.signal;
  let abortFromExternal = null;
  if (useTimeout && typeof AbortController !== 'undefined') {
    controller = new AbortController();
    opts.signal = controller.signal;
    if (externalSignal) {
      abortFromExternal = () => {
        try { controller.abort(); } catch (_e) {}
      };
      if (externalSignal.aborted) abortFromExternal();
      else externalSignal.addEventListener('abort', abortFromExternal, {once: true});
    }
    timer = setTimeout(() => {
      timedOut = true;
      try { controller.abort(); } catch (_e) {}
    }, ms);
  }
  try {
    return await fetch(url, opts);
  } catch (e) {
    const name = String(e && e.name || '');
    if (name === 'AbortError' && timedOut) {
      const sec = Math.max(1, Math.round((useTimeout ? ms : __JF_REQ_TIMEOUT_MS) / 1000));
      throw new Error(`Request timed out (${sec}s)`);
    }
    throw e;
  } finally {
    if (timer) clearTimeout(timer);
    if (externalSignal && abortFromExternal) {
      externalSignal.removeEventListener('abort', abortFromExternal);
    }
  }
}

async function _jfFetchJson(url, options){
  const opts = Object.assign({cache:'no-store'}, options || {});
  const r = await _jfFetchWithTimeout(url, opts, __JF_REQ_TIMEOUT_MS);
  let body = {};
  try { body = await r.json(); } catch (_e) {}
  if (!r.ok) {
    const msg = body.detail || body.reason || `HTTP ${r.status}`;
    throw new Error(String(msg));
  }
  return body;
}

async function loadJellyfinHome(force){
  const request = _jfBeginBrowseRequest();
  try {
    _jfSetStatus('Loading…');
    _jfSetConn(false, 'Checking…');
    const j = await _jfFetchJson(`/jellyfin/home?limit=24${force ? '&refresh=1' : ''}`, {signal: request.signal});
    if (!_jfBrowseRequestIsCurrent(request) || __jfActiveTab !== 'dashboard') return;
    __jfDashboardRows = Array.isArray(j.rows) ? j.rows : [];
    if (__jfActiveTab === 'dashboard') _jfRenderRows(__jfDashboardRows);
    __jfLastMode = 'home';
    __jfLastQuery = '';
    _jfApplySelectionUi();
    const up = !!(j.connected || j.authenticated);
    const reason = String(j.last_error || '').trim();
    _jfSetConn(up, up ? 'Connected' : (reason ? `Unavailable · ${reason}` : 'Unavailable'));
    if (!up) _jfSetBrowseUnavailable(reason);
    // A bare "Ready" under the search box read as a UI bug; only say something
    // when the state actually needs attention.
    _jfSetStatus(j.connected ? '' : 'Degraded — showing cached catalog', j.connected ? 'ok' : '');
  } catch (e) {
    if (_jfIsAbortError(e)) return;
    const msg = String(e?.message || e);
    _jfSetBrowseUnavailable(msg);
    _jfDetailPlaceholder(`${jfBrandName()} unavailable.`);
    _jfSetConn(false, 'Unavailable');
    _jfSetStatus(`Error: ${msg}`, 'err');
  } finally {
    _jfFinishBrowseRequest(request);
  }
}

async function runJellyfinSearch(force){
  const q = (document.getElementById('jfSearchInput')?.value || '').trim();
  if (!q) {
    await _jfLoadActiveTabDefault(true);
    return;
  }
  const request = _jfBeginBrowseRequest();
  const activeTab = __jfActiveTab;
  try {
    _jfSetStatus(`Searching "${q}"…`);
    _jfSetConn(false, 'Checking…');
    const j = await _jfFetchJson(`/jellyfin/search?q=${encodeURIComponent(q)}&limit=30${force ? '&refresh=1' : ''}`, {signal: request.signal});
    const currentQuery = (document.getElementById('jfSearchInput')?.value || '').trim();
    if (!_jfBrowseRequestIsCurrent(request) || __jfActiveTab !== activeTab || currentQuery !== q) return;
    const scopedItems = _jfFilterSearchItems(j.items || []);
    _jfRenderRows([{id:'search', title:_jfSearchTitle(q), items: scopedItems}]);
    __jfLastMode = 'search';
    __jfLastQuery = q;
    _jfApplySelectionUi();
    const up = !!(j.connected || j.authenticated);
    const reason = String(j.last_error || '').trim();
    _jfSetConn(up, up ? 'Connected' : (reason ? `Unavailable · ${reason}` : 'Unavailable'));
    if (!up) _jfSetBrowseUnavailable(reason);
    _jfSetStatus(`${scopedItems.length} ${scopedItems.length === 1 ? 'result' : 'results'}`, 'ok');
  } catch (e) {
    if (_jfIsAbortError(e)) return;
    _jfSetBrowseUnavailable(String(e?.message || e));
    _jfSetConn(false, 'Unavailable');
    _jfSetStatus(`Search failed: ${String(e?.message || e)}`, 'err');
  } finally {
    _jfFinishBrowseRequest(request);
  }
}

async function loadJellyfinMovies(force){
  const request = _jfBeginBrowseRequest();
  const sort = __jfMoviesSort || 'added';
  const state = _jfResetCatalogState('movies', sort);
  try {
    _jfSetStatus('Loading movies…');
    _jfSetConn(false, 'Checking…');
    const qs = new URLSearchParams();
    qs.set('sort', sort);
    qs.set('limit', String(__JF_CATALOG_PAGE_SIZE));
    qs.set('start', '0');
    if (force) qs.set('refresh', '1');
    const j = await _jfFetchJson(`/jellyfin/movies?${qs.toString()}`, {signal: request.signal});
    if (!_jfBrowseRequestIsCurrent(request) || __jfActiveTab !== 'movies' || __jfMoviesSort !== sort) return;
    _jfAddCatalogItems(state, j.items);
    state.nextStart = _jfNextStart(j.next_start_index);
    state.count = Math.max(0, Number(j.count || state.items.length));
    __jfMoviesLimit = Math.max(1, Number(j.limit || __JF_CATALOG_PAGE_SIZE));
    __jfMoviesCount = state.count;
    _jfRenderRows([{id:'movies', title:'Movies', items: state.items}]);
    _jfArmCatalogPagination('movies');
    __jfLastMode = 'movies';
    __jfLastQuery = '';
    _jfApplySelectionUi();
    const up = !!(j.connected);
    const reason = String(j.last_error || '').trim();
    _jfSetConn(up, up ? 'Connected' : (reason ? `Unavailable · ${reason}` : 'Unavailable'));
    if (!up) _jfSetBrowseUnavailable(reason);
    _jfSetStatus(_jfCatalogStatus('movies', state.items.length, state.count), up ? 'ok' : '');
    _jfSyncTabControls();
  } catch (e) {
    if (_jfIsAbortError(e)) return;
    const msg = String(e?.message || e);
    _jfSetBrowseUnavailable(msg);
    _jfSetConn(false, 'Unavailable');
    _jfSetStatus(`Movies failed: ${msg}`, 'err');
  } finally {
    _jfFinishBrowseRequest(request);
  }
}

async function loadJellyfinTvSeries(force){
  const request = _jfBeginBrowseRequest();
  const sort = __jfTvSort || 'title_asc';
  const state = _jfResetCatalogState('tv', sort);
  try {
    _jfSetStatus('Loading series…');
    _jfSetConn(false, 'Checking…');
    const qs = new URLSearchParams();
    qs.set('sort', sort);
    qs.set('limit', String(__JF_CATALOG_PAGE_SIZE));
    qs.set('start', '0');
    if (force) qs.set('refresh', '1');
    const j = await _jfFetchJson(`/jellyfin/tv/series?${qs.toString()}`, {signal: request.signal});
    if (!_jfBrowseRequestIsCurrent(request) || __jfActiveTab !== 'tv' || __jfTvSort !== sort) return;
    _jfAddCatalogItems(state, j.items);
    state.nextStart = _jfNextStart(j.next_start_index);
    state.count = Math.max(0, Number(j.count || state.items.length));
    __jfTvLimit = Math.max(1, Number(j.limit || __JF_CATALOG_PAGE_SIZE));
    __jfTvCount = state.count;
    _jfRenderRows([{id:'tv_series', title:'TV Series', items: state.items}]);
    document.getElementById('jellyfinShell')?.classList.remove('jfSeasonChooserOpen');
    _jfArmCatalogPagination('tv');
    __jfLastMode = 'tv';
    __jfLastQuery = '';
    _jfApplySelectionUi();
    __jfTvSeriesId = '';
    __jfTvSeriesTitle = '';
    __jfTvSeriesThumb = '';
    __jfTvSeriesBackdrop = '';
    __jfTvSeriesOverview = '';
    __jfTvSeriesYear = '';
    __jfTvSeasonNumber = null;
    __jfTvSeasonChooserExpanded = false;
    __jfTvViewMode = 'series';
    const up = !!(j.connected);
    const reason = String(j.last_error || '').trim();
    _jfSetConn(up, up ? 'Connected' : (reason ? `Unavailable · ${reason}` : 'Unavailable'));
    if (!up) _jfSetBrowseUnavailable(reason);
    _jfSetStatus(_jfCatalogStatus('tv', state.items.length, state.count), up ? 'ok' : '');
    _jfSyncTabControls();
  } catch (e) {
    if (_jfIsAbortError(e)) return;
    const msg = String(e?.message || e);
    _jfSetBrowseUnavailable(msg);
    _jfSetConn(false, 'Unavailable');
    _jfSetStatus(`TV failed: ${msg}`, 'err');
  } finally {
    _jfFinishBrowseRequest(request);
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
  __jfTvSeriesBackdrop = '';
  __jfTvSeriesOverview = '';
  __jfTvSeriesYear = '';
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
  const backdrop = String((opts && opts.backdrop) || __jfTvSeriesBackdrop || '').trim();
  const overview = String((opts && opts.overview) || __jfTvSeriesOverview || '').trim();
  const year = String((opts && opts.year) || __jfTvSeriesYear || '').trim();
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
    if (!_jfHasFiniteNumber(seasonNum)) {
      const first = seasons.find((s) => _jfHasFiniteNumber(s && s.season_number));
      seasonNum = first ? Number(first.season_number) : null;
    }

    let epUrl = `/jellyfin/tv/series/${encodeURIComponent(sid)}/episodes`;
    const epQs = new URLSearchParams();
    if (_jfHasFiniteNumber(seasonNum)) epQs.set('season_number', String(Number(seasonNum)));
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
    const seasonLabel = _jfHasFiniteNumber(seasonNum) ? `Season ${Number(seasonNum)}` : 'No season selected';
    const rows = [{
      id: 'tv_series_header',
      title,
      seriesId: sid,
      thumbnail: thumb,
      backdrop,
      overview,
      year,
      seasonCount: seasons.length,
      seasonLabel,
      expanded: chooserExpanded,
    }];
    if (chooserExpanded) {
      rows.push({
        id: 'tv_season_chooser',
        title,
        seriesId: sid,
        selectedSeason: seasonNum,
        items: seasonItems,
      });
    }
    rows.push({id:'tv_episodes', title:`${seasonLabel} Episodes`, items: episodes});
    __jfTvSeriesId = sid;
    __jfTvSeriesTitle = title;
    __jfTvSeriesThumb = thumb;
    __jfTvSeriesBackdrop = backdrop;
    __jfTvSeriesOverview = overview;
    __jfTvSeriesYear = year;
    __jfTvSeasonNumber = _jfHasFiniteNumber(seasonNum) ? Number(seasonNum) : null;
    __jfTvSeasonChooserExpanded = chooserExpanded;
    __jfTvViewMode = 'detail';
    __jfSelectedItemId = _jfHasFiniteNumber(seasonNum) ? `season:${sid}:${Number(seasonNum)}` : '';
    _jfRenderRows(rows);
    document.getElementById('jellyfinShell')?.classList.toggle('jfSeasonChooserOpen', chooserExpanded);
    _jfApplySelectionUi();
    if (focusChooser) {
      requestAnimationFrame(() => {
        const chooser = document.querySelector('#jfRows .jfSeasonOption.active, #jfRows .jfSeasonModalClose');
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
    _jfNotifyAction(target, `${jfBrandName()} unavailable. Reconnect first.`, 'err');
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
    };
    window.addEventListener('resize', onResize, {passive:true});
    window.addEventListener('orientationchange', onResize, {passive:true});
    __jfResizeBound = true;
  }
  if (launchBtn) launchBtn.onclick = () => openJellyfinShell();
  if (shellBack) shellBack.onclick = () => closeJellyfinShell();
  if (detailBackdrop) detailBackdrop.onclick = () => {
    _jfCloseDetailPanel();
    _jfFocusSelectedItem();
  };
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
    searchInput.addEventListener('input', () => {
      _jfAbortBrowseRequest();
      _jfScheduleSearch(false);
    });
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
        e.stopPropagation();
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
      const backToSeries = e.target && e.target.closest ? e.target.closest('[data-jf-action="back_to_series"]') : null;
      if (backToSeries) {
        __jfTvSeasonChooserExpanded = false;
        loadJellyfinTvSeries(false);
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      const playAllHeader = e.target && e.target.closest ? e.target.closest('[data-jf-action="play_all_series_header"]') : null;
      if (playAllHeader) {
        _jfPlayAllSeries(
          String(playAllHeader.getAttribute('data-series-id') || __jfTvSeriesId),
          String(playAllHeader.getAttribute('data-series-title') || __jfTvSeriesTitle),
        );
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      const seasonOption = e.target && e.target.closest ? e.target.closest('[data-jf-action="select_tv_season"]') : null;
      if (seasonOption) {
        const seasonNumber = Number(seasonOption.getAttribute('data-season-number'));
        if (_jfHasFiniteNumber(seasonNumber)) {
          __jfTvSeasonNumber = seasonNumber;
          __jfTvSeasonChooserExpanded = false;
          loadJellyfinTvSeriesDetail(
            String(seasonOption.getAttribute('data-series-id') || __jfTvSeriesId),
            {title: __jfTvSeriesTitle},
          );
        }
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
        _jfFocusSelectedItem();
      } else if (__jfTvSeasonChooserExpanded) {
        _jfToggleTvSeasonChooser();
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
