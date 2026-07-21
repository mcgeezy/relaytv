// IPTV browse, favorites, visibility, ordering, source, and discovery UI.
(function(){
  'use strict';

  const state = {
    visible: false,
    enabled: false,
    tab: 'channels',
    view: 'all',
    sources: [],
    channels: [],
    offset: 0,
    hasMore: false,
    busy: false,
    searchTimer: 0,
    directoryTimer: 0,
    lastFocus: null,
    lastPlayed: null,
    openMenu: null,
  };
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
      // First run (no sources yet) opens Discover so onboarding is obvious.
      const initial = state.sources.length ? (state.tab || 'channels') : 'discover';
      await selectTab(initial);
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

  function updateSourceSelect(){
    const select = $('iptvSourceFilter');
    if (!select) return;
    const selected = select.value;
    select.innerHTML = '<option value="">All sources</option>' + state.sources.map((source) => `<option value="${esc(source.id)}">${esc(source.name)}</option>`).join('');
    if (state.sources.some((source) => source.id === selected)) select.value = selected;
  }

  async function loadSources(){
    const result = await api('/iptv/sources');
    state.sources = Array.isArray(result.items) ? result.items : [];
    updateSourceSelect();
    renderSources();
    setStatus(`${state.sources.length} source${state.sources.length === 1 ? '' : 's'}`);
  }

  function channelQuery(reset){
    const params = new URLSearchParams();
    if ($('iptvSearch')?.value.trim()) params.set('q', $('iptvSearch').value.trim());
    if ($('iptvSourceFilter')?.value) params.set('source_id', $('iptvSourceFilter').value);
    if ($('iptvGroupFilter')?.value) params.set('group', $('iptvGroupFilter').value);
    params.set('sort', $('iptvSort')?.value || 'manual');
    params.set('offset', String(reset ? 0 : state.offset));
    params.set('limit', '60');
    if (state.view === 'favorites') {
      params.set('favorites', 'true');
      params.set('visibility', 'all');
    } else if (state.view === 'hidden') {
      params.set('visibility', 'hidden');
    } else {
      params.set('visibility', 'visible');
      if ($('iptvUnavailable')?.checked) params.set('include_unavailable', 'true');
    }
    return params;
  }

  async function loadChannels(reset){
    if (state.busy) return;
    state.busy = true;
    if (reset) { state.offset = 0; state.channels = []; }
    setStatus('Loading channels…');
    try {
      const result = await api(`/iptv/channels?${channelQuery(!!reset).toString()}`);
      const incoming = Array.isArray(result.items) ? result.items : [];
      state.channels = reset ? incoming : state.channels.concat(incoming);
      state.offset = state.channels.length;
      state.hasMore = !!result.has_more;
      updateGroups(result.groups || []);
      renderChannels();
      setStatus(`${result.total || 0} channel${Number(result.total || 0) === 1 ? '' : 's'}`);
    } catch (error) {
      renderEmpty(error.message);
      setStatus(error.message, true);
    } finally {
      state.busy = false;
    }
  }

  function updateGroups(groups){
    const select = $('iptvGroupFilter');
    if (!select) return;
    const selected = select.value;
    select.innerHTML = '<option value="">All groups</option>' + groups.map((group) => `<option value="${esc(group)}">${esc(group)}</option>`).join('');
    if (groups.includes(selected)) select.value = selected;
  }

  function renderEmpty(message){
    const grid = $('iptvChannelGrid');
    if (grid) {
      const title = state.view === 'favorites' ? 'No favorites yet' : (state.view === 'hidden' ? 'Nothing hidden' : 'No channels');
      const hint = message || (state.view === 'favorites' ? 'Tap the star on a channel to keep it here.' : 'No channels match these filters.');
      grid.innerHTML = `<div class="iptvEmpty"><strong>${esc(title)}</strong><span>${esc(hint)}</span></div>`;
    }
    $('iptvMoreBtn')?.classList.add('hidden');
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

  function renderChannels(){
    closeMenu();
    const grid = $('iptvChannelGrid');
    if (!grid) return;
    if (!state.channels.length) { renderEmpty(); return; }
    const manual = ($('iptvSort')?.value || 'manual') === 'manual' && state.view === 'all';
    grid.innerHTML = state.channels.map((channel, index) => {
      const unavailable = !channel.active || channel.availability === 'unavailable';
      const playing = isPlaying(channel);
      const logo = channel.logo_url ? `<img src="${esc(channel.logo_url)}" alt="" loading="lazy" referrerpolicy="no-referrer" />` : '▦';
      const meta = `${esc(channel.group_title || 'Ungrouped')}${channel.source_name ? ' · ' + esc(channel.source_name) : ''}`;
      const avail = availabilityLabel(channel);
      const status = playing
        ? '<span class="iptvNowTag">On now</span>'
        : (avail ? `<span class="iptvAvail"><span class="iptvDot ${avail.cls}"></span>${esc(avail.text)}</span>` : '');
      const moveRows = manual
        ? `<div class="iptvMenuSep"></div><button type="button" data-action="up" role="menuitem"${index === 0 ? ' disabled' : ''}><span aria-hidden="true">↑</span>Move up</button><button type="button" data-action="down" role="menuitem"${index === state.channels.length - 1 ? ' disabled' : ''}><span aria-hidden="true">↓</span>Move down</button>`
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
          <button type="button" data-action="hidden" role="menuitem"><span aria-hidden="true">${channel.hidden ? '👁' : '⊘'}</span>${channel.hidden ? 'Show channel' : 'Hide channel'}</button>
          <button type="button" data-action="check" role="menuitem"><span aria-hidden="true">↻</span>Check availability</button>
          ${moveRows}
        </div>
      </article>`;
    }).join('');
    $('iptvMoreBtn')?.classList.toggle('hidden', !state.hasMore);
  }

  function closeMenu(){
    if (!state.openMenu) return;
    const { card, btn } = state.openMenu;
    card?.querySelector('.iptvMenu')?.classList.add('hidden');
    if (btn) { btn.setAttribute('aria-expanded', 'false'); btn.classList.remove('open'); }
    state.openMenu = null;
  }

  function toggleMenu(card, btn){
    if (!card) return;
    const wasOpen = state.openMenu && state.openMenu.card === card;
    closeMenu();
    if (wasOpen) return;
    card.querySelector('.iptvMenu')?.classList.remove('hidden');
    btn.setAttribute('aria-expanded', 'true');
    btn.classList.add('open');
    state.openMenu = { card, btn };
  }

  async function channelAction(card, action){
    const sourceId = card?.dataset.source || '';
    const channelId = card?.dataset.channel || '';
    const channel = state.channels.find((row) => row.source_id === sourceId && row.channel_id === channelId);
    if (!channel) return;
    setStatus('Working…');
    try {
      if (['play_now','play_next','play_last'].includes(action)) {
        await api(`/iptv/channels/${encodeURIComponent(channelId)}/action`, {method:'POST', body:{source_id:sourceId, command:action}});
        if (action === 'play_now') { state.lastPlayed = {source_id:sourceId, channel_id:channelId}; renderChannels(); }
        setStatus(action === 'play_now' ? `Playing ${channel.name}` : `Queued ${channel.name}`);
        return;
      }
      if (action === 'favorite' || action === 'hidden') {
        const patch = {source_id:sourceId};
        patch[action] = !channel[action];
        await api(`/iptv/channels/${encodeURIComponent(channelId)}`, {method:'PATCH', body:patch});
        await loadChannels(true);
        return;
      }
      if (action === 'check') {
        await api(`/iptv/channels/${encodeURIComponent(channelId)}/check`, {method:'POST', body:{source_id:sourceId, command:'check'}});
        await loadChannels(true);
        return;
      }
      if (action === 'up' || action === 'down') {
        const index = state.channels.indexOf(channel);
        const anchor = state.channels[index + (action === 'up' ? -1 : 1)];
        if (!anchor || anchor.source_id !== sourceId) return;
        const body = {source_id:sourceId, channel_id:channelId};
        if (action === 'up') body.before_channel_id = anchor.channel_id;
        else body.after_channel_id = anchor.channel_id;
        await api('/iptv/channels/reorder', {method:'POST', body});
        await loadChannels(true);
      }
    } catch (error) { setStatus(error.message, true); }
  }

  function renderSources(){
    const host = $('iptvSourceList');
    if (!host) return;
    if (!state.sources.length) { host.innerHTML = '<div class="iptvEmpty"><strong>No playlists yet</strong><span>Add a custom M3U above, or browse the Discover tab for free providers.</span></div>'; return; }
    host.innerHTML = state.sources.map((source) => `<article class="iptvSourceCard" data-source="${esc(source.id)}"><h3>${esc(source.name)}</h3><div class="iptvSourceMeta">${esc(source.kind === 'upload' ? 'Pasted playlist' : (source.location_host || 'Configured URL'))} · ${Number(source.channel_count || 0)} channels<br>${source.last_error ? `Last error: ${esc(source.last_error)}` : (source.last_success_at ? 'Last refresh succeeded' : 'Not refreshed yet')}</div><div class="iptvSourceActions"><button data-source-action="refresh">Refresh</button><button data-source-action="toggle">${source.enabled ? 'Disable' : 'Enable'}</button><button class="danger" data-source-action="delete">Delete</button></div></article>`).join('');
  }

  async function sourceAction(card, action){
    const sourceId = card?.dataset.source || '';
    const source = state.sources.find((row) => row.id === sourceId);
    if (!source) return;
    try {
      if (action === 'delete') {
        if (!window.confirm(`Delete ${source.name} and its catalog?`)) return;
        await api(`/iptv/sources/${encodeURIComponent(sourceId)}`, {method:'DELETE'});
      } else if (action === 'toggle') {
        await api(`/iptv/sources/${encodeURIComponent(sourceId)}`, {method:'PATCH', body:{enabled:!source.enabled}});
      } else {
        setStatus(`Refreshing ${source.name}…`);
        await api(`/iptv/sources/${encodeURIComponent(sourceId)}/refresh`, {method:'POST'});
      }
      await loadSources();
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
    } catch (error) { if (msg) msg.textContent = error.message; }
  }

  async function loadDirectory(){
    const q = $('iptvDirectorySearch')?.value.trim() || '';
    const host = $('iptvDirectoryGrid');
    if (host) host.innerHTML = '<div class="iptvEmpty"><span>Loading providers…</span></div>';
    try {
      const result = await api(`/iptv/directory?q=${encodeURIComponent(q)}`);
      const items = Array.isArray(result.items) ? result.items : [];
      if (host) host.innerHTML = items.length ? items.map((item) => `<article class="iptvDirectoryCard" data-preset="${esc(item.id)}"><h3>${esc(item.name)}</h3><p>${esc(item.description)}<br>${esc(item.country || '')}${item.language ? ` · ${esc(item.language)}` : ''} · ${esc(item.category || '')}</p><button class="good" data-directory-add>Add source</button></article>`).join('') : '<div class="iptvEmpty"><strong>No providers found</strong><span>No providers match that search.</span></div>';
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
      button.textContent = 'Added';
      await loadSources();
    } catch (error) { button.disabled = false; button.textContent = 'Retry'; setStatus(error.message, true); }
  }

  async function selectTab(tab){
    state.tab = tab;
    closeMenu();
    document.querySelectorAll('[data-iptv-tab]').forEach((button) => { const active = button.dataset.iptvTab === tab; button.classList.toggle('active', active); button.setAttribute('aria-current', active ? 'page' : 'false'); });
    const browse = tab === 'channels';
    $('iptvBrowsePanel')?.classList.toggle('hidden', !browse);
    $('iptvSourcesPanel')?.classList.toggle('hidden', tab !== 'sources');
    $('iptvDiscoverPanel')?.classList.toggle('hidden', tab !== 'discover');
    if (browse) await loadChannels(true);
    else if (tab === 'sources') await loadSources();
    else await loadDirectory();
  }

  function selectView(view){
    if (state.view === view) return;
    state.view = view;
    document.querySelectorAll('[data-iptv-view]').forEach((button) => { const active = button.dataset.iptvView === view; button.classList.toggle('active', active); button.setAttribute('aria-pressed', active ? 'true' : 'false'); });
    $('iptvUnavailableWrap')?.classList.toggle('hidden', view !== 'all');
    loadChannels(true);
  }

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
    const sourceId = $('iptvSourceFilter')?.value || '';
    const scope = sourceId ? 'from this source' : 'from all sources';
    if (!window.confirm(`Permanently remove inactive and unavailable channels ${scope}?`)) return;
    try {
      const result = await api('/iptv/channels/remove-unavailable', {method:'POST', body:{source_id:sourceId}});
      setStatus(`Removed ${Number(result.removed || 0)} unavailable channel${Number(result.removed || 0) === 1 ? '' : 's'}.`);
      await loadSources();
    } catch (error) { setStatus(error.message, true); }
  }

  function bind(){
    $('iptvOpenBtn')?.addEventListener('click', openShell);
    $('iptvBackBtn')?.addEventListener('click', closeShell);
    $('setIptvApplyBtn')?.addEventListener('click', applySetting);
    document.querySelectorAll('[data-iptv-tab]').forEach((button) => button.addEventListener('click', () => selectTab(button.dataset.iptvTab)));
    document.querySelectorAll('[data-iptv-view]').forEach((button) => button.addEventListener('click', () => selectView(button.dataset.iptvView)));
    $('iptvReloadBtn')?.addEventListener('click', () => loadChannels(true));
    $('iptvRemoveUnavailableBtn')?.addEventListener('click', removeUnavailable);
    $('iptvMoreBtn')?.addEventListener('click', () => loadChannels(false));
    ['iptvSourceFilter','iptvGroupFilter','iptvSort','iptvUnavailable'].forEach((id) => $(id)?.addEventListener('change', () => loadChannels(true)));
    $('iptvSearch')?.addEventListener('input', () => { clearTimeout(state.searchTimer); state.searchTimer = setTimeout(() => loadChannels(true), 240); });
    $('iptvDirectorySearch')?.addEventListener('input', () => { clearTimeout(state.directoryTimer); state.directoryTimer = setTimeout(loadDirectory, 240); });
    $('iptvAddSourceBtn')?.addEventListener('click', addSource);
    $('iptvChannelGrid')?.addEventListener('click', (event) => {
      const button = event.target.closest('[data-action]');
      if (!button) return;
      const card = button.closest('.iptvChannel');
      if (button.dataset.action === 'menu') { toggleMenu(card, button); return; }
      closeMenu();
      channelAction(card, button.dataset.action);
    });
    $('iptvSourceList')?.addEventListener('click', (event) => { const button = event.target.closest('[data-source-action]'); if (button) sourceAction(button.closest('.iptvSourceCard'), button.dataset.sourceAction); });
    $('iptvDirectoryGrid')?.addEventListener('click', (event) => { const button = event.target.closest('[data-directory-add]'); if (button) addDirectory(button.closest('.iptvDirectoryCard')); });
    document.addEventListener('click', (event) => { if (state.openMenu && !event.target.closest('.iptvMenu') && !event.target.closest('[data-action="menu"]')) closeMenu(); });
    document.addEventListener('keydown', (event) => { if (state.visible && event.key === 'Escape') { event.preventDefault(); if (state.openMenu) closeMenu(); else closeShell(); } });
    api('/settings').then((settings) => { const enabled = !!settings.iptv_enabled; if ($('setIptvEnabled')) $('setIptvEnabled').checked = enabled; window.iptvUpdateLaunch({iptv_enabled:enabled}); }).catch(() => {});
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind);
  else bind();
})();
