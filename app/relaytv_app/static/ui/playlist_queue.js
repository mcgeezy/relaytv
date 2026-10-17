// Finite YouTube playlist imports. A saved request ID survives ambiguous
// network failures; reopening/reloading checks status and never resubmits.
(function(global){
  'use strict';
  const STORAGE_KEY = 'relaytv_playlist_import_v1';
  let saved = null;
  let timer = null;
  let submitting = false;
  let checking = false;

  function element(id){ return document.getElementById(id); }
  function visible(id, show){
    const node = element(id);
    if (node) node.classList.toggle('hidden', !show);
  }

  function linkInfo(raw){
    try {
      const url = new URL(String(raw || '').trim());
      if (!['http:', 'https:'].includes(url.protocol)) return null;
      const host = url.hostname.toLowerCase().replace(/\.$/, '');
      const matches = domain => host === domain || host.endsWith('.' + domain);
      if (!['youtube.com', 'youtu.be', 'youtube-nocookie.com'].some(matches)) return null;
      const ids = url.searchParams.getAll('list');
      if (ids.length !== 1 || !/^[A-Za-z0-9_-]{2,160}$/.test(ids[0])) return null;
      const video = matches('youtu.be') ? url.pathname.split('/')[1]
        : (url.searchParams.get('v') || (/^\/(shorts|embed|live)\//.test(url.pathname) ? url.pathname.split('/')[2] : ''));
      return {
        url: 'https://www.youtube.com/playlist?list=' + encodeURIComponent(ids[0]),
        hasVideo: !!video,
        dynamic: ids[0].startsWith('RD'),
      };
    } catch (_e) { return null; }
  }

  function update(){
    const info = linkInfo(element('addUrlInput')?.value);
    const playlist = element('addPlaylistBtn');
    visible('addPlaylistBtn', !!info);
    if (playlist) playlist.disabled = submitting || !!saved || !!info?.dynamic;
    for (const id of ['addQueueBtn', 'addPlayBtn']){
      const node = element(id);
      if (node) node.disabled = !!info && !info.hasVideo;
    }
    const hint = element('playlistLinkHint');
    visible('playlistLinkHint', !!info);
    if (hint && info){
      hint.textContent = info.dynamic
        ? 'YouTube mixes change continuously. Use a saved playlist to add all videos.'
        : (info.hasVideo ? 'Queue or Play uses this video. Add entire playlist includes every available video from the beginning.'
          : 'Add every available video in playlist order. Current playback continues.');
    }
  }

  function store(value){
    saved = value;
    try {
      if (value) global.localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
      else global.localStorage.removeItem(STORAGE_KEY);
    } catch (_e) {}
    update();
  }

  function render(job){
    visible('playlistImportPanel', true);
    const status = element('playlistImportStatus');
    if (status) status.textContent = job.detail || 'Reading playlist…';
    const running = ['loading', 'adding'].includes(job.status);
    visible('playlistImportCancel', job.status === 'loading');
    visible('playlistImportCheck', job.status === 'unknown');
    visible('playlistImportDismiss', !running);
    if (timer){ global.clearTimeout(timer); timer = null; }
    if (running) timer = global.setTimeout(check, 1000);
    if (job.status === 'completed' && typeof global.refresh === 'function'){
      global.refresh().catch(() => {});
    }
    update();
  }

  async function request(path, options){
    const response = await global._fetchWithTimeout(path, options || {}, 5000);
    let data = {};
    try { data = await response.json(); } catch (_e) {}
    if (!response.ok){
      const error = new Error(data.detail || 'Could not check playlist import.');
      error.status = response.status;
      throw error;
    }
    return data;
  }

  async function check(){
    if (!saved || checking || submitting) return;
    checking = true;
    const current = saved;
    try {
      const job = await request('/queue/playlists/' + current.request_id);
      if (saved === current) render(job);
    } catch (error){
      if (saved === current) render({status:'unknown', detail: error.status === 404
        ? 'Import status expired or the server restarted. Check the queue before dismissing and adding again.'
        : 'Connection interrupted. Check import status before adding again.'});
    } finally { checking = false; }
  }

  function requestId(){
    const bytes = new Uint8Array(16);
    global.crypto.getRandomValues(bytes);
    return Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('');
  }

  async function submit(){
    const info = linkInfo(element('addUrlInput')?.value);
    if (!info || info.dynamic || saved || submitting) return;
    submitting = true;
    // Save before sending: a connection can disappear after the server accepts.
    store({url: info.url, request_id: requestId()});
    render({status:'loading', detail:'Reading playlist… You can close this dialog while it loads.'});
    try {
      const job = await request('/queue/playlists', {
        method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(saved),
      });
      render(job);
    } catch (error){
      const rejected = error.status >= 400 && error.status < 500;
      render({status:rejected ? 'failed' : 'unknown', detail: rejected
        ? error.message
        : 'Connection interrupted. Check import status before adding again.'});
    } finally {
      submitting = false;
      update();
    }
  }

  async function cancel(){
    if (!saved || submitting) return;
    const current = saved;
    const button = element('playlistImportCancel');
    if (button) button.disabled = true;
    try {
      const job = await request('/queue/playlists/' + current.request_id, {method:'DELETE'});
      if (saved === current) render(job);
    } catch (_e){
      await check();
    } finally { if (button) button.disabled = false; }
  }

  function dismiss(){
    if (timer){ global.clearTimeout(timer); timer = null; }
    store(null);
    visible('playlistImportPanel', false);
  }

  function bind(){
    const input = element('addUrlInput');
    if (input) input.addEventListener('input', update);
    const buttons = {
      addPlaylistBtn: submit, playlistImportCancel: cancel,
      playlistImportCheck: check, playlistImportDismiss: dismiss,
    };
    for (const [id, action] of Object.entries(buttons)){
      const button = element(id);
      if (button) button.onclick = action;
    }
    try {
      const value = JSON.parse(global.localStorage.getItem(STORAGE_KEY) || 'null');
      if (value && /^[a-f0-9]{32}$/.test(value.request_id) && linkInfo(value.url)) saved = value;
    } catch (_e) {}
    update();
    if (saved) check();
  }

  global.RelayTVPlaylistQueue = {bind, update, linkInfo, submit, check};
})(window);
