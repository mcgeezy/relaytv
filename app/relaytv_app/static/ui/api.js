(function (root, factory) {
  'use strict';
  let storage = null;
  try { storage = root && root.localStorage; } catch (_error) {}
  const api = factory({
    window:root,
    fetch:root && root.fetch ? root.fetch.bind(root) : null,
    Headers:root && root.Headers,
    AbortController:root && root.AbortController,
    storage,
    setTimeout:root && root.setTimeout ? root.setTimeout.bind(root) : setTimeout,
    clearTimeout:root && root.clearTimeout ? root.clearTimeout.bind(root) : clearTimeout,
  });
  if (root) {
    root.RelayTV = root.RelayTV || {};
    root.RelayTV.api = api;
  }
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = {createApi:factory};
  }
  if (root && root.document) api.installAuthFetch();
})(typeof window !== 'undefined' ? window : globalThis, function createApi(env) {
  'use strict';

  const win = env && env.window;
  const storage = env && env.storage;
  const HeadersClass = env && env.Headers;
  const AbortControllerClass = env && env.AbortController;
  const schedule = env && env.setTimeout;
  const unschedule = env && env.clearTimeout;
  const storageKey = 'relaytv_api_token';
  let request = env && env.fetch;
  let promptedThisLoad = false;
  let installed = false;

  function storedToken() {
    try { return String((storage && storage.getItem(storageKey)) || '').trim(); }
    catch (_error) { return ''; }
  }

  function storeToken(value) {
    try {
      const token = String(value || '').trim();
      if (!storage) return;
      if (token) storage.setItem(storageKey, token);
      else storage.removeItem(storageKey);
    } catch (_error) {}
  }

  function isSameOrigin(input) {
    try {
      const raw = typeof input === 'string' ? input : ((input && input.url) || '');
      return new URL(raw, win.location.href).origin === win.location.origin;
    } catch (_error) { return false; }
  }

  function withAuth(options, token) {
    const output = Object.assign({}, options || {});
    const headers = new HeadersClass((options && options.headers) || {});
    headers.set('Authorization', `Bearer ${token}`);
    output.headers = headers;
    return output;
  }

  function isBearerChallenge(response) {
    try {
      return response.status === 401
        && String(response.headers.get('www-authenticate') || '').toLowerCase().startsWith('bearer');
    } catch (_error) { return false; }
  }

  function installAuthFetch() {
    if (installed || !win || !request || !HeadersClass) return false;
    installed = true;
    const nativeFetch = request;
    const authenticatedFetch = async(input, options) => {
      const sameOrigin = isSameOrigin(input);
      const token = sameOrigin ? storedToken() : '';
      let response = await nativeFetch(input, token ? withAuth(options, token) : options);
      if (!sameOrigin || promptedThisLoad || !isBearerChallenge(response)) return response;
      promptedThisLoad = true;
      const entered = win.prompt(
        'This RelayTV server requires an API token for control actions.\n'
          + 'Enter the API token (RELAYTV_API_TOKEN):',
        '',
      );
      const replacement = String(entered || '').trim();
      if (!replacement) return response;
      storeToken(replacement);
      response = await nativeFetch(input, withAuth(options, replacement));
      return response;
    };
    request = authenticatedFetch;
    win.fetch = authenticatedFetch;
    return true;
  }

  function fetchWithTimeout(url, options, timeoutMs) {
    const milliseconds = Number(timeoutMs || 0);
    if (!request) return Promise.reject(new Error('fetch unavailable'));
    if (!(Number.isFinite(milliseconds) && milliseconds > 0) || !AbortControllerClass) {
      return request(url, options || {});
    }
    const controller = new AbortControllerClass();
    const finalOptions = Object.assign({}, options || {}, {signal:controller.signal});
    const timer = schedule(() => {
      try { controller.abort(); } catch (_error) {}
    }, milliseconds);
    return request(url, finalOptions).finally(() => unschedule(timer));
  }

  async function commandErrorDetail(response) {
    const status = Number(response && response.status) || 0;
    if (status === 401 || status === 403) return 'Not authorized — check API token';
    let detail = '';
    try {
      const payload = await response.json();
      if (payload && typeof payload === 'object') {
        detail = String(payload.detail || payload.message || '').trim();
      }
    } catch (_error) {}
    detail = detail.replace(/\s+/g, ' ').trim().slice(0, 120);
    return detail || `Command failed (HTTP ${status || '?'})`;
  }

  async function post(path, body, postOptions, hooks = {}) {
    const options = {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:body ? JSON.stringify(body) : '{}',
    };
    let response = null;
    let unreachable = false;
    try {
      response = await fetchWithTimeout(path, options, 5000);
    } catch (_error) {
      unreachable = true;
      if (postOptions && postOptions.idempotent) {
        try {
          response = await fetchWithTimeout(path, options, 5000);
          unreachable = false;
        } catch (_retryError) {}
      }
    }
    if (unreachable || !response) {
      if (hooks.connection) hooks.connection(false, {
        sticky:true,
        message:'Command failed — check connection',
      });
      if (hooks.refresh) Promise.resolve(hooks.refresh()).catch(() => null);
      return {ok:false, status:0, detail:'unreachable', reached:false};
    }
    if (!response.ok) {
      const detail = await commandErrorDetail(response);
      if (hooks.rejected) hooks.rejected(detail);
      if (hooks.refresh) Promise.resolve(hooks.refresh()).catch(() => null);
      return {ok:false, status:response.status, detail, reached:true};
    }
    if (hooks.connection) hooks.connection(true);
    if (hooks.refresh) Promise.resolve(hooks.refresh()).catch(() => null);
    return {ok:true, status:response.status, detail:'', reached:true};
  }

  return {
    commandErrorDetail,
    fetchWithTimeout,
    installAuthFetch,
    isSameOrigin,
    post,
    storedToken,
    storeToken,
  };
});
