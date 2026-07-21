// IPTV browse, favorites, ordering, source, and discovery UI.
(function(){
  'use strict';

  const state = {
    visible: false,
    enabled: false,
    tab: 'channels',
    sources: [],
    lastFocus: null,
    lastPlayed: null,
    openMenu: null,
    directoryTimer: 0,
    // Link-driven navigation: My channels is home; Discover/Sources are pushed
    // onto this stack and popped by Back.
    nav: [],
    // Two channel browsers over the same catalog: the curated "My channels"
    // home (chan, favorites floated into their own section) and the
    // full-catalog explorer (disc).
    chan: { channels: [], favorites: [], offset: 0, hasMore: false, busy: false, searchTimer: 0 },
    disc: { channels: [], offset: 0, hasMore: false, busy: false, searchTimer: 0 },
  };
  const GRID = { chan: 'iptvChannelGrid', disc: 'iptvDiscoverGrid' };
  const MORE = { chan: 'iptvMoreBtn', disc: 'iptvDiscoverMoreBtn' };
  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value == null ? '' : value).replace(/[&<>'"]/g, (ch) => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));

  async function api(path, opts){
    const final = Object.assign({cache:'no-store'}, opts || {});
    if (final.body && typeof final.body !== 'string') {
      final.headers = Object.assign({'Content-Type':'application/json'}, final.headers || {});
      final.body = JSON.stringify(final.body);
    }
    const res = await fetch(path, final);
    let body = {};
    try { body = await res.json(); } catch (_e) {}
    if (!res.ok) throw new Error(body.detail || `Request failed (${res.status})`);
    return body;
  }

  function setStatus(message, bad){
    const el = $('iptvStatus');
    if (!el) return;
    el.textContent = String(message || '');
    el.classList.toggle('bad', !!bad);
  }

  window.iptvUpdateLaunch = function(status){
    state.enabled = !!(status && status.iptv_enabled);
    const button = $('iptvOpenBtn');
    if (button) {
      button.classList.toggle('show', state.enabled);
      button.disabled = !state.enabled;
      const count = Number(status && status.iptv_channel_count || 0);
      button.title = count ? `Open IPTV · ${count} channels` : 'Open IPTV';
    }
    if (!state.enabled && state.visible) closeShell();
  };

  function showShell(show){
    state.visible = !!show;
    const shell = $('iptvShell');
    if (!shell) return;
    shell.classList.toggle('hidden', !show);
    shell.setAttribute('aria-hidden', show ? 'false' : 'true');
    document.body.classList.toggle('iptvNoScroll', !!show);
  }

  async function openShell(){
    if (!state.enabled || state.visible) return;
    state.lastFocus = document.activeElement;
    showShell(true);
    setStatus('Loading…');
    $('iptvBackBtn')?.focus();
    try {
      await loadSources();
      // First run (no sources) opens Sources — add a playlist or pick a free
      // provider — with Back returning to My channels.
      if (state.sources.length) { state.nav = []; await selectTab('channels'); }
      else { state.nav = ['channels']; await selectTab('sources'); }
    } catch (error) {
      setStatus(error.message, true);
    }
  }

  function closeShell(){
    closeMenu();
    showShell(false);
    const target = state.lastFocus && typeof state.lastFocus.focus === 'function' ? state.lastFocus : $('iptvOpenBtn');
    target?.focus();
  }

  async function loadSources(){
    const result = await api('/iptv/sources');
    state.sources = Array.isArray(result.items) ? result.items : [];
    renderSources();
    setStatus(`${state.sources.length} source${state.sources.length === 1 ? '' : 's'}`);
  }

  // ---- Channel browsers (chan + disc) ----------------------------------

  function discQuery(reset){
    const ctx = state.disc;
    const params = new URLSearchParams();
    const q = $('iptvDiscoverSearch')?.value.trim();
    if (q) params.set('q', q);
    if ($('iptvDiscoverGroup')?.value) params.set('group', $('iptvDiscoverGroup').value);
    params.set('sort', 'name');
    params.set('visibility', 'visible');
    params.set('offset', String(reset ? 0 : ctx.offset));
    params.set('limit', '60');
    return params;
  }

  async function loadGrid(which, reset){
    if (which === 'chan') return loadChan(reset);
    const ctx = state.disc;
    if (ctx.busy) return;
    ctx.busy = true;
    if (reset) { ctx.offset = 0; ctx.channels = []; }
    setStatus('Loading channels…');
    try {
      const result = await api(`/iptv/channels?${discQuery(!!reset).toString()}`);
      const incoming = Array.isArray(result.items) ? result.items : [];
      ctx.channels = reset ? incoming : ctx.channels.concat(incoming);
      ctx.offset = ctx.channels.length;
      ctx.hasMore = !!result.has_more;
      updateGroups(result.groups || []);
      renderDisc();
      setStatus(`${result.total || 0} channel${Number(result.total || 0) === 1 ? '' : 's'}`);
    } catch (error) {
      renderEmpty('disc', error.message);
      setStatus(error.message, true);
    } finally {
      ctx.busy = false;
    }
  }

  // My channels: favorites are fetched separately so they always float to a
  // section at the top, and the paginated main list excludes them.
  async function loadChan(reset){
    const ctx = state.chan;
    if (ctx.busy) return;
    ctx.busy = true;
    if (reset) { ctx.offset = 0; ctx.channels = []; }
    setStatus('Loading channels…');
    try {
      const q = $('iptvSearch')?.value.trim() || '';
      const main = new URLSearchParams({visibility:'visible', sort:'manual', offset:String(reset ? 0 : ctx.offset), limit:'60'});
      if (q) main.set('q', q);
      const requests = [api(`/iptv/channels?${main.toString()}`)];
      if (reset) {
        const fav = new URLSearchParams({favorites:'true', visibility:'all', sort:'manual', offset:'0', limit:'60'});
        if (q) fav.set('q', q);
        requests.push(api(`/iptv/channels?${fav.toString()}`));
      }
      const [mainRes, favRes] = await Promise.all(requests);
      if (reset) ctx.favorites = Array.isArray(favRes && favRes.items) ? favRes.items : [];
      const rawItems = Array.isArray(mainRes.items) ? mainRes.items : [];
      ctx.offset = (reset ? 0 : ctx.offset) + rawItems.length;
      const incoming = rawItems.filter((row) => !row.favorite);
      ctx.channels = reset ? incoming : ctx.channels.concat(incoming);
      ctx.hasMore = !!mainRes.has_more;
      renderChan();
      setStatus(`${mainRes.total || 0} channel${Number(mainRes.total || 0) === 1 ? '' : 's'}`);
    } catch (error) {
      renderEmpty('chan', error.message);
      setStatus(error.message, true);
    } finally {
      ctx.busy = false;
    }
  }

  function updateGroups(groups){
    const select = $('iptvDiscoverGroup');
    if (!select) return;
    const selected = select.value;
    select.innerHTML = '<option value="">All groups</option>' + groups.map((group) => `<option value="${esc(group)}">${esc(group)}</option>`).join('');
    if (groups.includes(selected)) select.value = selected;
  }

  function renderEmpty(which, message){
    const grid = $(GRID[which]);
    if (grid) {
      let title = 'No channels';
      let hint = message || 'No channels match your search.';
      if (!message && !state.sources.length) { title = 'No channels yet'; hint = 'Add a playlist or pick a free provider from Sources.'; }
      grid.innerHTML = `<div class="iptvEmpty"><strong>${esc(title)}</strong><span>${esc(hint)}</span></div>`;
    }
    $(MORE[which])?.classList.add('hidden');
  }

  function availabilityLabel(channel){
    if (!channel.active || channel.availability === 'unavailable') return {cls:'bad', text:'Unavailable'};
    switch (channel.availability) {
      case 'available': return {cls:'ok', text:'Available'};
      case 'suspect': return {cls:'warn', text:'Suspect'};
      case 'geo_blocked': return {cls:'warn', text:'Geo-blocked'};
      case 'checking': return {cls:'warn', text:'Checking'};
      default: return null;
    }
  }

  function isPlaying(channel){
    return !!(state.lastPlayed && state.lastPlayed.source_id === channel.source_id && state.lastPlayed.channel_id === channel.channel_id);
  }

  function channelTile(channel, index, total, manual){
    const unavailable = !channel.active || channel.availability === 'unavailable';
    const playing = isPlaying(channel);
    const logo = channel.logo_url ? `<img src="${esc(channel.logo_url)}" alt="" loading="lazy" referrerpolicy="no-referrer" />` : '▦';
    const meta = `${esc(channel.group_title || 'Ungrouped')}${channel.source_name ? ' · ' + esc(channel.source_name) : ''}`;
    const avail = availabilityLabel(channel);
    const status = playing
      ? '<span class="iptvNowTag">On now</span>'
      : (avail ? `<span class="iptvAvail"><span class="iptvDot ${avail.cls}"></span>${esc(avail.text)}</span>` : '');
    const moveRows = manual
      ? `<div class="iptvMenuSep"></div><button type="button" data-action="up" role="menuitem"${index === 0 ? ' disabled' : ''}><span aria-hidden="true">↑</span>Move up</button><button type="button" data-action="down" role="menuitem"${index === total - 1 ? ' disabled' : ''}><span aria-hidden="true">↓</span>Move down</button>`
      : '';
    return `<article class="iptvChannel${unavailable ? ' isUnavailable' : ''}${playing ? ' isPlaying' : ''}" data-source="${esc(channel.source_id)}" data-channel="${esc(channel.channel_id)}">
      <button type="button" class="iptvChannelPlay" data-action="play_now" title="Play ${esc(channel.name)}">
        <span class="iptvChannelLogo">${logo}<span class="iptvPlayGlyph" aria-hidden="true">▶</span></span>
        <span class="iptvChannelBody"><span class="iptvChannelTitle">${esc(channel.name)}</span><span class="iptvChannelMeta">${meta}</span>${status}</span>
      </button>
      <div class="iptvCornerCol">
        <button type="button" class="iptvFav${channel.favorite ? ' isFavorite' : ''}" data-action="favorite" aria-pressed="${channel.favorite ? 'true' : 'false'}" aria-label="${channel.favorite ? 'Remove favorite' : 'Add favorite'}">${channel.favorite ? '★' : '☆'}</button>
        <button type="button" class="iptvKebab" data-action="menu" aria-haspopup="menu" aria-expanded="false" aria-label="More actions for ${esc(channel.name)}">⋯</button>
      </div>
      <div class="iptvMenu hidden" role="menu">
        <button type="button" data-action="play_next" role="menuitem"><span aria-hidden="true">⏭</span>Play next</button>
        <button type="button" data-action="play_last" role="menuitem"><span aria-hidden="true">＋</span>Add to queue</button>
        <div class="iptvMenuSep"></div>
        <button type="button" data-action="check" role="menuitem"><span aria-hidden="true">↻</span>Check availability</button>
        ${moveRows}
      </div>
    </article>`;
  }

  function renderChan(){
    closeMenu();
    const grid = $('iptvChannelGrid');
    if (!grid) return;
    const favs = state.chan.favorites;
    const main = state.chan.channels;
    if (!favs.length && !main.length) { renderEmpty('chan'); return; }
    let html = '';
    if (favs.length) {
      html += '<div class="iptvGroupLabel" data-iptv-section="favorites"><span aria-hidden="true">★</span> Favorites</div>';
      html += `<div class="iptvChannelGrid">${favs.map((channel, index) => channelTile(channel, index, favs.length, false)).join('')}</div>`;
    }
    if (main.length) {
      if (favs.length) html += '<div class="iptvGroupLabel">All channels</div>';
      html += `<div class="iptvChannelGrid">${main.map((channel, index) => channelTile(channel, index, main.length, true)).join('')}</div>`;
    }
    grid.innerHTML = html;
    $('iptvMoreBtn')?.classList.toggle('hidden', !state.chan.hasMore);
  }

  function renderDisc(){
    closeMenu();
    const ctx = state.disc;
    const grid = $('iptvDiscoverGrid');
    if (!grid) return;
    if (!ctx.channels.length) { renderEmpty('disc'); return; }
    grid.innerHTML = ctx.channels.map((channel, index) => channelTile(channel, index, ctx.channels.length, false)).join('');
    $('iptvDiscoverMoreBtn')?.classList.toggle('hidden', !ctx.hasMore);
  }

  function closeMenu(){
    if (!state.openMenu) return;
    const { card, btn } = state.openMenu;
    card?.querySelector('.iptvMenu')?.classList.add('hidden');
    card?.classList.remove('menuOpen');
    if (btn) { btn.setAttribute('aria-expanded', 'false'); btn.classList.remove('open'); }
    state.openMenu = null;
  }

  function toggleMenu(card, btn){
    if (!card) return;
    const wasOpen = state.openMenu && state.openMenu.card === card;
    closeMenu();
    if (wasOpen) return;
    card.querySelector('.iptvMenu')?.classList.remove('hidden');
    card.classList.add('menuOpen');
    btn.setAttribute('aria-expanded', 'true');
    btn.classList.add('open');
    state.openMenu = { card, btn };
  }

  async function channelAction(which, card, action){
    const ctx = state[which];
    const sourceId = card?.dataset.source || '';
    const channelId = card?.dataset.channel || '';
    const pool = which === 'chan' ? ctx.channels.concat(ctx.favorites) : ctx.channels;
    const channel = pool.find((row) => row.source_id === sourceId && row.channel_id === channelId);
    if (!channel) return;
    setStatus('Working…');
    try {
      if (['play_now','play_next','play_last'].includes(action)) {
        await api(`/iptv/channels/${encodeURIComponent(channelId)}/action`, {method:'POST', body:{source_id:sourceId, command:action}});
        if (action === 'play_now') { state.lastPlayed = {source_id:sourceId, channel_id:channelId}; markPlaying(); }
        setStatus(action === 'play_now' ? `Playing ${channel.name}` : `Queued ${channel.name}`);
        return;
      }
      if (action === 'favorite') {
        const next = !channel.favorite;
        await api(`/iptv/channels/${encodeURIComponent(channelId)}`, {method:'PATCH', body:{source_id:sourceId, favorite:next}});
        channel.favorite = next;
        // My channels restructures into its Favorites section on change; on
        // Discover, update in place so the reader keeps their scroll spot
        // ("returned to the added item") with a brief confirming flash.
        if (which === 'chan') { await loadGrid('chan', true); }
        else { updateFavoriteTile(card, channel, next); }
        return;
      }
      if (action === 'check') {
        await api(`/iptv/channels/${encodeURIComponent(channelId)}/check`, {method:'POST', body:{source_id:sourceId, command:'check'}});
        await loadGrid(which, true);
        return;
      }
      if (action === 'up' || action === 'down') {
        const index = ctx.channels.indexOf(channel);
        const anchor = ctx.channels[index + (action === 'up' ? -1 : 1)];
        if (!anchor || anchor.source_id !== sourceId) return;
        const body = {source_id:sourceId, channel_id:channelId};
        if (action === 'up') body.before_channel_id = anchor.channel_id;
        else body.after_channel_id = anchor.channel_id;
        await api('/iptv/channels/reorder', {method:'POST', body});
        await loadGrid(which, true);
      }
    } catch (error) { setStatus(error.message, true); }
  }

  function updateFavoriteTile(card, channel, next){
    closeMenu();
    const fav = card?.querySelector('.iptvFav');
    if (fav) {
      fav.classList.toggle('isFavorite', next);
      fav.setAttribute('aria-pressed', next ? 'true' : 'false');
      fav.setAttribute('aria-label', next ? 'Remove favorite' : 'Add favorite');
      fav.textContent = next ? '★' : '☆';
    }
    if (next && card) {
      card.classList.add('justAdded');
      card.scrollIntoView({block:'nearest', behavior:'smooth'});
      setTimeout(() => card.classList.remove('justAdded'), 900);
    }
    setStatus(next ? `Added ${channel.name} to favorites` : `Removed ${channel.name} from favorites`);
  }

  // Reflect the freshly-played channel across whichever grids are mounted
  // without a full reload, so the "On now" tag lights up instantly.
  function markPlaying(){
    ['chan','disc'].forEach((which) => {
      const grid = $(GRID[which]);
      if (!grid) return;
      grid.querySelectorAll('.iptvChannel').forEach((card) => {
        const playing = state.lastPlayed && card.dataset.source === state.lastPlayed.source_id && card.dataset.channel === state.lastPlayed.channel_id;
        card.classList.toggle('isPlaying', !!playing);
        const body = card.querySelector('.iptvChannelBody');
        if (!body) return;
        const existing = body.querySelector('.iptvNowTag');
        if (playing && !existing) {
          body.querySelector('.iptvAvail')?.remove();
          body.insertAdjacentHTML('beforeend', '<span class="iptvNowTag">On now</span>');
        } else if (!playing && existing) {
          existing.remove();
        }
      });
    });
  }

  // ---- Sources + provider directory ------------------------------------

  function renderSources(){
    const host = $('iptvSourceList');
    const add = $('iptvAddCard');
    if (add && !add.dataset.touched) add.open = !state.sources.length;
    if (!host) return;
    if (!state.sources.length) { host.innerHTML = '<div class="iptvEmpty"><strong>No playlists yet</strong><span>Add a custom M3U above, or pick a free provider from the directory below.</span></div>'; return; }
    host.innerHTML = state.sources.map((source) => `<article class="iptvSourceCard" data-source="${esc(source.id)}"><h3>${esc(source.name)}</h3><div class="iptvSourceMeta">${esc(source.kind === 'upload' ? 'Pasted playlist' : (source.location_host || 'Configured URL'))} · ${Number(source.channel_count || 0)} channels<br>${source.last_error ? `Last error: ${esc(source.last_error)}` : (source.last_success_at ? 'Last refresh succeeded' : 'Not refreshed yet')}</div><div class="iptvSourceActions"><button data-source-action="refresh">Refresh</button><button data-source-action="toggle">${source.enabled ? 'Disable' : 'Enable'}</button><button class="danger" data-source-action="delete">Remove</button></div></article>`).join('');
  }

  async function sourceAction(card, action){
    const sourceId = card?.dataset.source || '';
    const source = state.sources.find((row) => row.id === sourceId);
    if (!source) return;
    try {
      if (action === 'delete') {
        if (!window.confirm(`Remove ${source.name} and its catalog?`)) return;
        await api(`/iptv/sources/${encodeURIComponent(sourceId)}`, {method:'DELETE'});
      } else if (action === 'toggle') {
        await api(`/iptv/sources/${encodeURIComponent(sourceId)}`, {method:'PATCH', body:{enabled:!source.enabled}});
      } else {
        setStatus(`Refreshing ${source.name}…`);
        await api(`/iptv/sources/${encodeURIComponent(sourceId)}/refresh`, {method:'POST'});
      }
      await loadSources();
      loadDirectory();
    } catch (error) { setStatus(error.message, true); }
  }

  async function addSource(){
    const name = $('iptvSourceName')?.value.trim() || '';
    const location = $('iptvSourceUrl')?.value.trim() || '';
    const content = $('iptvSourceContent')?.value.trim() || '';
    const msg = $('iptvSourceMsg');
    if (!name || (!location && !content)) { if (msg) msg.textContent = 'Name and URL or pasted M3U are required.'; return; }
    try {
      if (msg) msg.textContent = 'Adding…';
      await api('/iptv/sources', {method:'POST', body:{name, location, content, refresh_now:true}});
      if ($('iptvSourceName')) $('iptvSourceName').value = '';
      if ($('iptvSourceUrl')) $('iptvSourceUrl').value = '';
      if ($('iptvSourceContent')) $('iptvSourceContent').value = '';
      if (msg) msg.textContent = 'Source added.';
      await loadSources();
      loadDirectory();
    } catch (error) { if (msg) msg.textContent = error.message; }
  }

  async function loadDirectory(){
    const q = $('iptvDirectorySearch')?.value.trim() || '';
    const host = $('iptvDirectoryGrid');
    if (host) host.innerHTML = '<div class="iptvEmpty"><span>Loading providers…</span></div>';
    try {
      const result = await api(`/iptv/directory?q=${encodeURIComponent(q)}`);
      const items = Array.isArray(result.items) ? result.items : [];
      const have = new Set(state.sources.map((source) => String(source.name || '').toLowerCase()));
      if (host) host.innerHTML = items.length ? items.map((item) => {
        const added = have.has(String(item.name || '').toLowerCase());
        return `<article class="iptvDirectoryCard${added ? ' isAdded' : ''}" data-preset="${esc(item.id)}"><h3>${esc(item.name)}</h3><p>${esc(item.description)}<br>${esc(item.country || '')}${item.language ? ` · ${esc(item.language)}` : ''} · ${esc(item.category || '')}</p><button class="${added ? 'iptvGhostBtn' : 'good'}" data-directory-add${added ? ' disabled' : ''}>${added ? '✓ Added' : 'Add source'}</button></article>`;
      }).join('') : '<div class="iptvEmpty"><strong>No providers found</strong><span>No providers match that search.</span></div>';
    } catch (error) { if (host) host.innerHTML = `<div class="iptvEmpty"><span>${esc(error.message)}</span></div>`; }
  }

  async function addDirectory(card){
    const preset = card?.dataset.preset || '';
    const button = card?.querySelector('[data-directory-add]');
    if (!preset || !button) return;
    button.disabled = true;
    button.textContent = 'Adding…';
    try {
      await api(`/iptv/directory/${encodeURIComponent(preset)}/add`, {method:'POST'});
      button.textContent = '✓ Added';
      await loadSources();
    } catch (error) { button.disabled = false; button.textContent = 'Retry'; setStatus(error.message, true); }
  }

  // ---- Navigation ------------------------------------------------------

  async function selectTab(tab){
    state.tab = tab;
    closeMenu();
    $('iptvBrowsePanel')?.classList.toggle('hidden', tab !== 'channels');
    $('iptvDiscoverPanel')?.classList.toggle('hidden', tab !== 'discover');
    $('iptvSourcesPanel')?.classList.toggle('hidden', tab !== 'sources');
    $('iptvShell')?.scrollTo({top:0});
    if (tab === 'channels') await loadGrid('chan', true);
    else if (tab === 'discover') await loadGrid('disc', true);
    else { await loadSources(); loadDirectory(); }
  }

  function goTo(tab){ if (tab === state.tab) return; state.nav.push(state.tab); selectTab(tab); }
  function goBack(){ selectTab(state.nav.pop() || 'channels'); }

  async function applySetting(){
    const enabled = !!$('setIptvEnabled')?.checked;
    const msg = $('setIptvApplyResult');
    try {
      if (msg) msg.textContent = 'Applying…';
      await api('/settings', {method:'POST', body:{iptv_enabled:enabled, apply_now:true}});
      state.enabled = enabled;
      window.iptvUpdateLaunch({iptv_enabled:enabled});
      const badge = $('setIptvStatus');
      if (badge) { badge.textContent = enabled ? 'Enabled' : 'Disabled'; badge.className = `sectionStatus ${enabled ? 'up' : 'unknown'}`; }
      if (msg) msg.textContent = enabled ? 'IPTV enabled.' : 'IPTV disabled.';
    } catch (error) { if (msg) msg.textContent = error.message; }
  }

  async function removeUnavailable(){
    if (!window.confirm('Permanently remove inactive and unavailable channels from all sources?')) return;
    try {
      const result = await api('/iptv/channels/remove-unavailable', {method:'POST', body:{source_id:''}});
      setStatus(`Removed ${Number(result.removed || 0)} unavailable channel${Number(result.removed || 0) === 1 ? '' : 's'}.`);
      await loadSources();
    } catch (error) { setStatus(error.message, true); }
  }

  function bindGrid(which){
    $(GRID[which])?.addEventListener('click', (event) => {
      const button = event.target.closest('[data-action]');
      if (!button) return;
      const card = button.closest('.iptvChannel');
      if (button.dataset.action === 'menu') { toggleMenu(card, button); return; }
      closeMenu();
      channelAction(which, card, button.dataset.action);
    });
  }

  function bind(){
    $('iptvOpenBtn')?.addEventListener('click', openShell);
    $('iptvBackBtn')?.addEventListener('click', closeShell);
    $('setIptvApplyBtn')?.addEventListener('click', applySetting);
    document.querySelectorAll('[data-iptv-goto]').forEach((button) => button.addEventListener('click', () => goTo(button.dataset.iptvGoto)));
    document.querySelectorAll('[data-iptv-back]').forEach((button) => button.addEventListener('click', goBack));
    $('iptvDiscoverRefresh')?.addEventListener('click', () => loadGrid('disc', true));
    $('iptvDiscoverGroup')?.addEventListener('change', () => loadGrid('disc', true));
    $('iptvRemoveUnavailableBtn')?.addEventListener('click', removeUnavailable);
    $('iptvMoreBtn')?.addEventListener('click', () => loadGrid('chan', false));
    $('iptvDiscoverMoreBtn')?.addEventListener('click', () => loadGrid('disc', false));
    $('iptvSearch')?.addEventListener('input', () => { clearTimeout(state.chan.searchTimer); state.chan.searchTimer = setTimeout(() => loadGrid('chan', true), 240); });
    $('iptvDiscoverSearch')?.addEventListener('input', () => { clearTimeout(state.disc.searchTimer); state.disc.searchTimer = setTimeout(() => loadGrid('disc', true), 240); });
    $('iptvDirectorySearch')?.addEventListener('input', () => { clearTimeout(state.directoryTimer); state.directoryTimer = setTimeout(loadDirectory, 240); });
    $('iptvAddCard')?.addEventListener('toggle', (event) => { event.target.dataset.touched = '1'; });
    $('iptvAddSourceBtn')?.addEventListener('click', addSource);
    bindGrid('chan');
    bindGrid('disc');
    $('iptvSourceList')?.addEventListener('click', (event) => { const button = event.target.closest('[data-source-action]'); if (button) sourceAction(button.closest('.iptvSourceCard'), button.dataset.sourceAction); });
    $('iptvDirectoryGrid')?.addEventListener('click', (event) => { const button = event.target.closest('[data-directory-add]'); if (button) addDirectory(button.closest('.iptvDirectoryCard')); });
    document.addEventListener('click', (event) => { if (state.openMenu && !event.target.closest('.iptvMenu') && !event.target.closest('[data-action="menu"]')) closeMenu(); });
    document.addEventListener('keydown', (event) => { if (state.visible && event.key === 'Escape') { event.preventDefault(); if (state.openMenu) closeMenu(); else if (state.nav.length) goBack(); else closeShell(); } });
    api('/settings').then((settings) => { const enabled = !!settings.iptv_enabled; if ($('setIptvEnabled')) $('setIptvEnabled').checked = enabled; window.iptvUpdateLaunch({iptv_enabled:enabled}); }).catch(() => {});
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind);
  else bind();
})();
