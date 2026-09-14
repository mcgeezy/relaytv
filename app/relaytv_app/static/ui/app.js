// Core adapters are loaded before app.js and keep legacy function names while
// feature controllers migrate onto explicit RelayTV boundaries.
const __relaytvApi = window.RelayTV.api;
const __statusStore = window.RelayTV.store.createStore();
const __layerNavigation = window.RelayTV.navigation.createLayerNavigation(window);
window.RelayTV.runtime = Object.assign(window.RelayTV.runtime || {}, {
  layerNavigation:__layerNavigation,
  statusStore:__statusStore,
});

function _fetchWithTimeout(url, opts, timeoutMs){
  return __relaytvApi.fetchWithTimeout(url, opts, timeoutMs);
}

function _commandErrorDetail(response){
  return __relaytvApi.commandErrorDetail(response);
}

function post(path, body, postOpts){
  return __relaytvApi.post(path, body, postOpts, {
    connection:_connSignal,
    rejected:_commandRejected,
    refresh,
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
  bindSeerrUi();
  bindJellyfinUi();
  bindPlexUi();
  _jfSetShellVisible(false);
  _jfSetActiveTab('dashboard', {refresh:false});
  try { history.replaceState(Object.assign({}, history.state || {}, {relaytv_root: 1}), ''); } catch (_e) {}
  __layerNavigation.bind(_uiCloseTopLayerFromNav);
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
  consumeShareParam().catch(() => {});
  setInterval(() => {
    if (_uiEventHealthy()) return;
    refresh().catch(() => {});
  }, __UI_FALLBACK_REFRESH_MS);
  setInterval(() => {
    _ensureUiEventStream();
  }, __UI_EVENT_RECONNECT_MS);
  setInterval(async() => {
    const active = __uiEventSource;
    if (!active || active.kind !== 'sse') return;
    if (document.visibilityState !== 'visible') return;
    const generation = active.generation;
    const capabilities = await _loadUiRealtimeCapabilities({force:true});
    if (__uiEventSource !== active || generation !== __uiEventGeneration) return;
    const selected = __uiRealtimePolicy.select(capabilities, {
      websocketAvailable:typeof window.WebSocket === 'function',
    });
    if (selected !== 'websocket') return;
    _closeUiEventStream('upgrade');
    connectUiEventStream();
  }, __UI_TRANSPORT_UPGRADE_MS);
  setInterval(() => {
    if (document.visibilityState !== 'visible') return;
    if (!__jfUiVisible) return;
    if (__jfActiveTab !== 'dashboard') return;
    if (__jfLastMode === 'search' && __jfLastQuery) return;
    loadJellyfinHome(false);
  }, __JF_DASHBOARD_REFRESH_MS);
});
