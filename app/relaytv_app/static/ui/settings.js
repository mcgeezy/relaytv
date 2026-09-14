'use strict';

// Settings controller extracted from app.js. It retains the existing global
// function bridge until the responsive settings destination lands in Phase 5.

function openSettings(opts){
  closeHeaderMenu();
  const bd = document.getElementById('settingsBackdrop');
  if (!bd || !bd.classList.contains('hidden')) return;
  bd.classList.remove('hidden');
  if (!(opts && opts.fromDestination)) _uiPushLayer();
  loadSettingsUi().catch(console.warn);
}
function closeSettings(opts){
  const bd = document.getElementById('settingsBackdrop');
  if (!bd) return;
  const fromNav = !!(opts && opts.fromNav);
  if (!fromNav && !bd.classList.contains('hidden') && __layerNavigation.getDepth() > 0) {
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

function syncJellyfinAuthModeUi(){
  const shared = !!document.getElementById('setJfSharedCastEnabled')?.checked;
  const hint = document.getElementById('setJfAuthModeHint');
  if (hint) {
    hint.textContent = shared
      ? 'Requires a server API key. Client login remains active for browsing. Leave login empty for a cast-only setup.'
      : 'Casting uses the client login’s session and server permissions. Any stored API key is retained but inactive.';
  }
}

function jellyfinCredentialsError({enabled, server, shared, apiKey, clearApiKey, apiKeyConfigured, user, password, clearPassword, passwordConfigured}){
  if (!enabled) return '';
  if (!server) return 'Server URL is required.';
  const hasApiKey = !clearApiKey && (!!apiKey || apiKeyConfigured);
  const hasPassword = !clearPassword && (!!password || passwordConfigured);
  if (shared && !hasApiKey) return 'A server API key is required for the shared cast target.';
  if ((!shared || user || hasPassword) && !(user && hasPassword)) {
    return 'A username and password are required for client login. For casting only, clear the username and stored password, and enable the shared cast target.';
  }
  return '';
}

let __plexLinkFlowId = '';

function _plexErrorMessage(body, status){
  const detail = body && body.detail;
  if (detail && typeof detail === 'object' && detail.message) return String(detail.message);
  if (typeof detail === 'string' && detail) return detail;
  return `Plex request failed (HTTP ${status || 'error'}).`;
}

function _renderPlexSettings(cur, plexStatus, plexServers){
  const status = plexStatus && typeof plexStatus === 'object' ? plexStatus : {};
  const linked = !!status.linked;
  const selected = status.server && typeof status.server === 'object' ? status.server : null;
  const servers = plexServers && Array.isArray(plexServers.servers) ? plexServers.servers : [];
  const enabled = Object.prototype.hasOwnProperty.call(status, 'enabled')
    ? !!status.enabled
    : !!cur.plex_enabled;
  const enabledInput = document.getElementById('setPlexEnabled');
  if (enabledInput) enabledInput.checked = enabled;
  const playbackMode = document.getElementById('setPlexPlaybackMode');
  if (playbackMode) playbackMode.value = ['direct', 'transcode'].includes(cur.plex_playback_mode) ? cur.plex_playback_mode : 'auto';
  const maxBitrate = document.getElementById('setPlexMaxBitrate');
  if (maxBitrate) maxBitrate.value = ['4000', '8000', '12000', '20000'].includes(String(cur.plex_max_bitrate)) ? String(cur.plex_max_bitrate) : '0';

  const badge = document.getElementById('setPlexStatus');
  if (badge) {
    const reachable = !!(status.last_server_test && status.last_server_test.reachable);
    badge.textContent = !enabled ? 'Disabled' : linked ? (selected ? (reachable ? 'Connected' : 'Configured') : 'Choose Server') : 'Link Account';
    badge.classList.remove('up', 'down', 'warn', 'unknown');
    badge.classList.add(!enabled ? 'unknown' : (linked && selected ? (reachable ? 'up' : 'warn') : 'warn'));
  }

  const accountStatus = document.getElementById('setPlexAccountStatus');
  if (accountStatus) {
    const account = status.account || {};
    const label = account.friendly_name || account.username || 'linked account';
    const unresolved = !!status.server_token_unresolved;
    accountStatus.textContent = linked
      ? (unresolved
        ? `Linked as ${label}, but this server's credential could not be resolved. Browsing works; playback will fail. Reselect the server, or unlink and link again.`
        : `Linked as ${label}.`)
      : (__plexLinkFlowId ? 'Waiting for Plex authorization.' : 'No account linked.');
    accountStatus.classList.toggle('err', linked && unresolved);
  }
  const linkBtn = document.getElementById('setPlexLinkBtn');
  const linkUrl = document.getElementById('setPlexLinkUrl');
  const pollBtn = document.getElementById('setPlexPollBtn');
  const cancelBtn = document.getElementById('setPlexCancelBtn');
  const unlinkBtn = document.getElementById('setPlexUnlinkBtn');
  if (linkBtn) linkBtn.classList.toggle('hidden', linked || !!__plexLinkFlowId);
  if (linkUrl) linkUrl.classList.toggle('hidden', linked || !__plexLinkFlowId || !linkUrl.getAttribute('href') || linkUrl.getAttribute('href') === '#');
  if (pollBtn) pollBtn.classList.toggle('hidden', linked || !__plexLinkFlowId);
  if (cancelBtn) cancelBtn.classList.toggle('hidden', linked || !__plexLinkFlowId);
  if (unlinkBtn) unlinkBtn.classList.toggle('hidden', !linked);

  const serverSelect = document.getElementById('setPlexServer');
  if (serverSelect) {
    serverSelect.replaceChildren();
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = linked ? (servers.length ? 'Choose a Plex server' : 'No direct server connection found') : 'Link an account to load servers';
    serverSelect.appendChild(placeholder);
    for (const server of servers) {
      const option = document.createElement('option');
      option.value = String(server.machine_id || '');
      const local = Array.isArray(server.connections) && server.connections.some(connection => connection && connection.local);
      option.textContent = `${server.name || 'Plex Media Server'}${local ? ' · local' : ''}${server.owned ? ' · owned' : ''}`;
      serverSelect.appendChild(option);
    }
    const selectedMachineId = String((selected && selected.machine_id) || cur.plex_server_machine_id || '');
    serverSelect.value = selectedMachineId;
    serverSelect.setAttribute('data-selected-machine-id', selectedMachineId);
  }
  const testBtn = document.getElementById('setPlexTestBtn');
  if (testBtn) testBtn.disabled = !selected;
  const diag = document.getElementById('setPlexDiag');
  if (diag) {
    const transport = selected ? [selected.local ? 'local' : 'remote', selected.secure ? 'secure' : 'HTTP'].join(', ') : '';
    const test = status.last_server_test || {};
    diag.textContent = selected
      ? `${selected.name || 'Plex Media Server'} · ${transport}${test.version ? ` · PMS ${test.version}` : ''}`
      : (linked ? 'Choose a library server to finish setup.' : 'Link a Plex account to discover its servers.');
  }
  if (window.relaytvPlex) window.relaytvPlex.updateStatus({...status, enabled});
}

async function loadSettingsUi(){
  const [devRes, setRes, tvRes, jfRes, plexRes, seerrRes, seerrUsersRes] = await Promise.all([
    fetch('/devices'),
    fetch('/settings'),
    fetch('/tv/status').catch(() => null),
    fetch('/integrations/jellyfin/status').catch(() => null),
    fetch('/integrations/plex/status').catch(() => null),
    fetch('/integrations/seerr/status').catch(() => null),
    fetch('/integrations/seerr/users').catch(() => null)
  ]);
  const dev = await devRes.json();
  const cur = await setRes.json();
  const tvStatus = (tvRes && tvRes.ok) ? await tvRes.json() : null;
  const jfStatus = (jfRes && jfRes.ok) ? await jfRes.json() : null;
  const plexStatus = (plexRes && plexRes.ok) ? await plexRes.json() : null;
  let plexServers = null;
  if (plexStatus && plexStatus.linked) {
    const plexServersRes = await fetch('/plex/servers').catch(() => null);
    plexServers = (plexServersRes && plexServersRes.ok) ? await plexServersRes.json() : null;
  }
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
  const jfSharedCast = document.getElementById('setJfSharedCastEnabled');
  const jfApiKeyInput = document.getElementById('setJfApiKey');
  const jfClearApiKey = document.getElementById('setJfClearApiKey');
  const jfApiKeyState = document.getElementById('setJfApiKeyState');
  const jfUsername = document.getElementById('setJfUsername');
  const jfUserId = document.getElementById('setJfUserId');
  const jfUserIdState = document.getElementById('setJfUserIdState');
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
  if (jfSharedCast) jfSharedCast.checked = (cur.jellyfin_auth_mode || (cur.jellyfin_api_key_configured ? 'shared_api_key' : 'user_login')) === 'shared_api_key';
  syncJellyfinAuthModeUi();
  if (jfApiKeyInput) jfApiKeyInput.value = '';
  if (jfClearApiKey) jfClearApiKey.checked = false;
  if (jfApiKeyState) {
    const hasApiKey = !!cur.jellyfin_api_key_configured;
    jfApiKeyState.textContent = hasApiKey ? 'Server API key is stored.' : 'No server API key stored.';
    jfApiKeyState.setAttribute('data-configured', hasApiKey ? '1' : '0');
  }
  if (jfUsername) jfUsername.value = (cur.jellyfin_username || '');
  if (jfUserId) jfUserId.value = (cur.jellyfin_user_id || '');
  if (jfUserIdState) {
    const rejected = (jfStatus && jfStatus.catalog_user_id_rejected ? jfStatus.catalog_user_id_rejected : '').toString().trim();
    jfUserIdState.classList.toggle('err', !!rejected);
    jfUserIdState.textContent = rejected
      ? `“${rejected}” is not a server user ID, so it is being ignored. Use the ID from the server’s user page, or clear this field to use the signed-in account.`
      : '';
  }
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
  const jfClientStatus = document.getElementById('setJfClientStatus');
  const jfCastStatus = document.getElementById('setJfCastStatus');
  if (jfClientStatus) {
    jfClientStatus.textContent = !cur.jellyfin_enabled ? 'Client login: integration disabled.'
      : !jfStatus ? 'Client login: status unavailable.'
      : jfStatus.authenticated ? `Client login: connected as ${jfStatus.auth_user || cur.jellyfin_username || 'configured user'}.`
      : jfStatus.last_auth_ok === false ? 'Client login: failed. Check the username and password.'
      : jfStatus.auth_user_partial ? 'Client login: incomplete. Fill in both the username and the password — browsing stays off until then.'
      : cur.jellyfin_username ? 'Client login: waiting for sign-in.'
      : 'Client login: not configured. Sign in above to use your personal library.';
  }
  if (jfCastStatus) {
    jfCastStatus.textContent = !cur.jellyfin_enabled ? 'Cast target: integration disabled.'
      : !jfStatus ? 'Cast target: status unavailable.'
      : jfStatus.cast_target_ready ? `Cast target: ready (${jfStatus.cast_target_scope === 'shared' ? 'shared API key' : 'client login session'}).`
      : 'Cast target: not ready.';
  }
  const jfBadge = document.getElementById('setJfStatus');
  if (jfBadge) {
    const enabled = jfStatus && Object.prototype.hasOwnProperty.call(jfStatus, 'enabled')
      ? !!jfStatus.enabled
      : !!cur.jellyfin_enabled;
    const castReady = !!(enabled && jfStatus && jfStatus.cast_target_ready);
    const up = !!(enabled && jfStatus && (castReady || jfStatus.connected || jfStatus.authenticated));
    const castScope = String((jfStatus && jfStatus.cast_target_scope) || 'unavailable');
    jfBadge.textContent = enabled ? (castReady ? (castScope === 'shared' ? 'Shared Cast' : 'Cast Ready') : (up ? 'Connected' : 'Down')) : 'Disabled';
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
      const castReady = jfStatus.cast_target_ready ? 'ready' : 'not ready';
      const castScope = (jfStatus.cast_target_scope || 'unavailable').toString();
      const controlAuth = (jfStatus.control_auth_source || 'none').toString();
      const catalogReady = jfStatus.catalog_ready ? 'ready' : 'not ready';
      const catalogAuth = (jfStatus.catalog_auth_source || 'none').toString();
      const catalogUserId = (jfStatus.catalog_user_id || '').toString().trim();
      const catalogUserSource = (jfStatus.catalog_user_source || 'none').toString().trim();
      const catalogUserRejected = (jfStatus.catalog_user_id_rejected || '').toString().trim();
      const catalogUser = (catalogUserId ? `${catalogUserId} (${catalogUserSource || 'preferred'})` : 'auto')
        + (catalogUserRejected ? `, ignoring ${catalogUserRejected}` : '');
      const cacheEntries = Number(jfStatus.catalog_cache_entries || 0);
      const cacheMax = Number(jfStatus.catalog_cache_max_entries || 0);
      const cacheDiag = cacheMax > 0 ? `${cacheEntries}/${cacheMax}` : String(cacheEntries);
      const cacheClears = Number(jfStatus.catalog_cache_clears || 0);
      const cacheClearReason = (jfStatus.catalog_cache_last_cleared_reason || '').toString().trim();
      const health = (jfStatus.sync_health || 'unknown').toString();
      const healthReason = (jfStatus.sync_health_reason || '').toString().trim();
      const err = (jfStatus.last_error || '').toString().trim();
      jfSyncDiag.textContent =
        `Health: ${health}${healthReason ? ` (${healthReason})` : ''} · Cast: ${castReady}, ${castScope} (${controlAuth}) · Catalog: ${catalogReady} (${catalogAuth}), login: ${auth}, user: ${catalogUser} · Cache: ${cacheDiag} (clears: ${cacheClears}${cacheClearReason ? `, ${cacheClearReason}` : ''}) · Progress ok/fail: ${pOk}/${pFail} (${pLat}) · Stopped ok/fail: ${sOk}/${sFail} (${sLat}) · Stop dedupe: ${sSupp}` +
        (err ? ` · Last error: ${err}` : '');
    }
  }
  if (jfCacheClearMsg) {
    jfCacheClearMsg.classList.remove('ok', 'err');
    jfCacheClearMsg.textContent = '';
  }
  _renderPlexSettings(cur, plexStatus, plexServers);
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
  _seerrPopulateRequestUsers(seerrRequestUser, seerrUsers, cur.seerr_request_user_id);
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
  const jfSharedCast = document.getElementById('setJfSharedCastEnabled');
  if (jfSharedCast) jfSharedCast.onchange = syncJellyfinAuthModeUi;
  const plexApplyBtn = document.getElementById('setPlexApplyBtn');
  const plexTestBtn = document.getElementById('setPlexTestBtn');
  const plexLinkBtn = document.getElementById('setPlexLinkBtn');
  const plexLinkUrl = document.getElementById('setPlexLinkUrl');
  const plexPollBtn = document.getElementById('setPlexPollBtn');
  const plexCancelBtn = document.getElementById('setPlexCancelBtn');
  const plexUnlinkBtn = document.getElementById('setPlexUnlinkBtn');
  const plexApplyMsg = document.getElementById('setPlexApplyResult');
  const plexAccountStatus = document.getElementById('setPlexAccountStatus');
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
    const jfAuthMode = document.getElementById('setJfSharedCastEnabled')?.checked ? 'shared_api_key' : 'user_login';
    const jfApiKey = (document.getElementById('setJfApiKey')?.value || '').trim();
    const jfClearApiKey = !!document.getElementById('setJfClearApiKey')?.checked;
    const jfApiKeyConfigured = (document.getElementById('setJfApiKeyState')?.getAttribute('data-configured') || '') === '1';
    const jfUser = (document.getElementById('setJfUsername')?.value || '').trim();
    const jfUserId = (document.getElementById('setJfUserId')?.value || '').trim();
    const jfPass = (document.getElementById('setJfPassword')?.value || '').trim();
    const jfClearPw = !!document.getElementById('setJfClearPassword')?.checked;
    const jfPwConfigured = (document.getElementById('setJfPasswordState')?.getAttribute('data-configured') || '') === '1';
    const jfAudioLang = (document.getElementById('setJfAudioLang')?.value || '').trim().toLowerCase();
    const jfSubLang = (document.getElementById('setJfSubLang')?.value || '').trim().toLowerCase();
    const jfPlaybackMode = (document.getElementById('setJfPlaybackMode')?.value || 'auto').trim().toLowerCase();
    const deviceName = (document.getElementById('setDeviceName')?.value || '').trim();

    const jfError = jellyfinCredentialsError({
      enabled: jfEnabled, server: jfServer, shared: jfAuthMode === 'shared_api_key',
      apiKey: jfApiKey, clearApiKey: jfClearApiKey, apiKeyConfigured: jfApiKeyConfigured,
      user: jfUser, password: jfPass, clearPassword: jfClearPw, passwordConfigured: jfPwConfigured,
    });
    if (jfError) {
      if (jfApplyMsg) { jfApplyMsg.classList.add('err'); jfApplyMsg.textContent = jfError; }
      return;
    }

    const payload = {
      device_name: deviceName || 'RelayTV',
      jellyfin_enabled: jfEnabled,
      jellyfin_server_url: jfServer,
      jellyfin_auth_mode: jfAuthMode,
      jellyfin_username: jfUser,
      jellyfin_user_id: jfUserId,
      jellyfin_audio_lang: jfAudioLang,
      jellyfin_sub_lang: jfSubLang,
      jellyfin_playback_mode: (jfPlaybackMode === 'direct' || jfPlaybackMode === 'transcode') ? jfPlaybackMode : 'auto',
      apply_now: true
    };
    if (jfApiKey || jfClearApiKey) payload.jellyfin_api_key = jfClearApiKey ? '' : jfApiKey;
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

  function setPlexMessage(text, cls){
    if (!plexApplyMsg) return;
    plexApplyMsg.classList.remove('ok', 'err');
    if (cls) plexApplyMsg.classList.add(cls);
    plexApplyMsg.textContent = text || '';
  }

  async function selectPlexServerIfChanged(machineId){
    const serverSelect = document.getElementById('setPlexServer');
    const selectedMachineId = String(serverSelect?.getAttribute('data-selected-machine-id') || '').trim();
    if (!machineId || machineId === selectedMachineId) return false;
    const response = await fetch('/integrations/plex/server', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({machine_id:machineId}),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(_plexErrorMessage(body, response.status));
    if (serverSelect) serverSelect.setAttribute('data-selected-machine-id', machineId);
    return true;
  }

  async function applyPlexOnly(){
    const enabled = !!document.getElementById('setPlexEnabled')?.checked;
    const machineId = String(document.getElementById('setPlexServer')?.value || '').trim();
    const playbackMode = String(document.getElementById('setPlexPlaybackMode')?.value || 'auto').trim().toLowerCase();
    const maxBitrate = Number(document.getElementById('setPlexMaxBitrate')?.value || '0');
    if (plexApplyBtn) plexApplyBtn.disabled = true;
    if (plexTestBtn) plexTestBtn.disabled = true;
    setPlexMessage('Applying Plex settings…');
    try {
      await selectPlexServerIfChanged(machineId);
      const settingsResponse = await fetch('/settings', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({
          plex_enabled:enabled,
          plex_playback_mode:['direct', 'transcode'].includes(playbackMode) ? playbackMode : 'auto',
          plex_max_bitrate:[4000, 8000, 12000, 20000].includes(maxBitrate) ? maxBitrate : 0,
          apply_now:true,
        }),
      });
      const settingsBody = await settingsResponse.json().catch(() => ({}));
      if (!settingsResponse.ok) throw new Error(_plexErrorMessage(settingsBody, settingsResponse.status));
      await loadSettingsUi();
      setPlexMessage(enabled ? 'Plex settings applied.' : 'Plex disabled.', 'ok');
      return true;
    } catch (error) {
      setPlexMessage(error && error.message ? error.message : 'Plex apply failed.', 'err');
      return false;
    } finally {
      if (plexApplyBtn) plexApplyBtn.disabled = false;
      if (plexTestBtn) plexTestBtn.disabled = false;
    }
  }

  if (plexApplyBtn) plexApplyBtn.onclick = applyPlexOnly;

  if (plexLinkBtn) plexLinkBtn.onclick = async () => {
    plexLinkBtn.disabled = true;
    setPlexMessage('Starting secure Plex link…');
    try {
      const response = await fetch('/integrations/plex/auth/start', {method:'POST'});
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(_plexErrorMessage(body, response.status));
      __plexLinkFlowId = String(body.flow_id || '');
      if (!__plexLinkFlowId || !body.link_url) throw new Error('Plex returned an incomplete link response.');
      if (plexLinkUrl) {
        plexLinkUrl.href = String(body.link_url);
        plexLinkUrl.classList.remove('hidden');
      }
      if (plexPollBtn) plexPollBtn.classList.remove('hidden');
      if (plexCancelBtn) plexCancelBtn.classList.remove('hidden');
      plexLinkBtn.classList.add('hidden');
      if (plexAccountStatus) plexAccountStatus.textContent = 'Open Plex, approve RelayTV, then choose Check link.';
      setPlexMessage('Plex link is ready.', 'ok');
    } catch (error) {
      setPlexMessage(error && error.message ? error.message : 'Could not start Plex linking.', 'err');
    } finally {
      plexLinkBtn.disabled = false;
    }
  };

  if (plexPollBtn) plexPollBtn.onclick = async () => {
    if (!__plexLinkFlowId) return;
    plexPollBtn.disabled = true;
    setPlexMessage('Checking Plex authorization…');
    try {
      const response = await fetch('/integrations/plex/auth/poll', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({flow_id:__plexLinkFlowId}),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(_plexErrorMessage(body, response.status));
      if (!body.linked) {
        setPlexMessage('Still waiting for approval on Plex.', '');
        return;
      }
      __plexLinkFlowId = '';
      if (plexLinkUrl) plexLinkUrl.href = '#';
      await loadSettingsUi();
      setPlexMessage('Plex account linked. Choose a library server.', 'ok');
    } catch (error) {
      setPlexMessage(error && error.message ? error.message : 'Could not finish Plex linking.', 'err');
    } finally {
      plexPollBtn.disabled = false;
    }
  };

  if (plexCancelBtn) plexCancelBtn.onclick = async () => {
    if (!__plexLinkFlowId) return;
    plexCancelBtn.disabled = true;
    try {
      const response = await fetch('/integrations/plex/auth/cancel', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({flow_id:__plexLinkFlowId}),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(_plexErrorMessage(body, response.status));
      __plexLinkFlowId = '';
      if (plexLinkUrl) plexLinkUrl.href = '#';
      await loadSettingsUi();
      setPlexMessage('Plex link cancelled.');
    } catch (error) {
      setPlexMessage(error && error.message ? error.message : 'Could not cancel Plex linking.', 'err');
    } finally {
      plexCancelBtn.disabled = false;
    }
  };

  if (plexUnlinkBtn) plexUnlinkBtn.onclick = async () => {
    plexUnlinkBtn.disabled = true;
    setPlexMessage('Removing the linked Plex account…');
    try {
      const response = await fetch('/integrations/plex/disconnect', {method:'POST'});
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(_plexErrorMessage(body, response.status));
      __plexLinkFlowId = '';
      if (plexLinkUrl) plexLinkUrl.href = '#';
      await loadSettingsUi();
      setPlexMessage('Plex account unlinked.', 'ok');
    } catch (error) {
      setPlexMessage(error && error.message ? error.message : 'Could not unlink Plex.', 'err');
    } finally {
      plexUnlinkBtn.disabled = false;
    }
  };

  if (plexTestBtn) plexTestBtn.onclick = async () => {
    plexTestBtn.disabled = true;
    setPlexMessage('Testing the selected Plex server…');
    try {
      const response = await fetch('/integrations/plex/test', {method:'POST'});
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(_plexErrorMessage(body, response.status));
      await loadSettingsUi();
      setPlexMessage(`Connected${body.version ? ` to PMS ${body.version}` : ''}.`, 'ok');
    } catch (error) {
      setPlexMessage(error && error.message ? error.message : 'Plex server test failed.', 'err');
    } finally {
      plexTestBtn.disabled = false;
    }
  };

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
    const jfAuthMode = document.getElementById('setJfSharedCastEnabled')?.checked ? 'shared_api_key' : 'user_login';
    const jfApiKey = (document.getElementById('setJfApiKey')?.value || '').trim();
    const jfClearApiKey = !!document.getElementById('setJfClearApiKey')?.checked;
    const jfApiKeyConfigured = (document.getElementById('setJfApiKeyState')?.getAttribute('data-configured') || '') === '1';
    const jfUser = (document.getElementById('setJfUsername')?.value || '').trim();
    const jfUserId = (document.getElementById('setJfUserId')?.value || '').trim();
    const jfPass = (document.getElementById('setJfPassword')?.value || '').trim();
    const jfClearPw = !!document.getElementById('setJfClearPassword')?.checked;
    const jfPwConfigured = (document.getElementById('setJfPasswordState')?.getAttribute('data-configured') || '') === '1';
    const jfAudioLang = (document.getElementById('setJfAudioLang')?.value || '').trim().toLowerCase();
    const jfSubLang = (document.getElementById('setJfSubLang')?.value || '').trim().toLowerCase();
    const jfPlaybackMode = (document.getElementById('setJfPlaybackMode')?.value || 'auto').trim().toLowerCase();
    const plexEnabled = !!document.getElementById('setPlexEnabled')?.checked;
    const plexMachineId = String(document.getElementById('setPlexServer')?.value || '').trim();
    const plexPlaybackMode = String(document.getElementById('setPlexPlaybackMode')?.value || 'auto').trim().toLowerCase();
    const plexMaxBitrate = Number(document.getElementById('setPlexMaxBitrate')?.value || '0');
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
    const jfError = jellyfinCredentialsError({
      enabled: jfEnabled, server: jfServer, shared: jfAuthMode === 'shared_api_key',
      apiKey: jfApiKey, clearApiKey: jfClearApiKey, apiKeyConfigured: jfApiKeyConfigured,
      user: jfUser, password: jfPass, clearPassword: jfClearPw, passwordConfigured: jfPwConfigured,
    });
    if (jfError) { alert(jfError); return; }
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
      jellyfin_auth_mode: jfAuthMode,
      jellyfin_username: jfUser,
      jellyfin_user_id: jfUserId,
      jellyfin_audio_lang: jfAudioLang,
      jellyfin_sub_lang: jfSubLang,
      jellyfin_playback_mode: (jfPlaybackMode === 'direct' || jfPlaybackMode === 'transcode') ? jfPlaybackMode : 'auto',
      plex_enabled: plexEnabled,
      plex_playback_mode: (plexPlaybackMode === 'direct' || plexPlaybackMode === 'transcode') ? plexPlaybackMode : 'auto',
      plex_max_bitrate: [4000, 8000, 12000, 20000].includes(plexMaxBitrate) ? plexMaxBitrate : 0,
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
    if (jfApiKey || jfClearApiKey) payload.jellyfin_api_key = jfClearApiKey ? '' : jfApiKey;
    if (jfPass || jfClearPw) payload.jellyfin_password = jfClearPw ? '' : jfPass;
    if (seerrApiKey) payload.seerr_api_key = seerrApiKey;
    if (seerrClearApiKey) payload.seerr_api_key_clear = true;
    try {
      await selectPlexServerIfChanged(plexMachineId);
    } catch (error) {
      alert(error && error.message ? error.message : 'Failed to select Plex server');
      return;
    }
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
