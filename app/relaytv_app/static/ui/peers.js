// SPDX-License-Identifier: GPL-3.0-only
// Send-to-device sheet: pick a peer RelayTV device and send it what is playing
// here plus the queue.
//
// The two modes differ only in what happens *here*: Send gives the session away
// and this device stops, Copy leaves this device exactly as it was. The payload
// is identical, so the choice is about this room, not the other one.
//
// Tapping a device sends immediately, so the header always reports the current
// selection — the item list itself sits below the fold. Peers stay listed while
// offline with the reason shown, because a device that disappears from the list
// reads as data loss rather than an outage.
(function(){
  'use strict';

  const $ = (id) => document.getElementById(id);

  const state = {
    peers: [],
    discovered: [],
    discovery: null,
    device: null,
    loading: false,
    sending: '',
    adopting: '',
    mode: 'send',
    items: [],
    // Only exclusions are tracked: everything is selected by default, so an
    // item that arrives while the sheet is open joins the send rather than
    // being silently left out.
    deselected: new Set(),
    pickSignature: '',
    lastFocus: null,
  };

  async function api(path, opts){
    const res = await fetch(path, opts || {});
    let payload = null;
    try { payload = await res.json(); } catch (_e) { payload = null; }
    if (!res.ok) {
      const detail = (payload && (payload.detail || payload.error)) || `request failed (${res.status})`;
      throw new Error(String(detail));
    }
    return payload || {};
  }

  function postJson(path, body){
    return api(path, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body || {}),
    });
  }

  function setStatus(msg, kind){
    const el = $('peersStatus');
    if (!el) return;
    el.classList.remove('err', 'ok');
    if (kind) el.classList.add(kind);
    el.textContent = String(msg || '');
  }

  function setAddHelper(msg, kind){
    const el = $('peerAddHelper');
    if (!el) return;
    el.classList.remove('err', 'ok');
    if (kind) el.classList.add(kind);
    el.textContent = String(msg || '');
  }

  function lastStatus(){
    // app.js keeps the last status payload in a script-scoped binding shared
    // across the UI's classic scripts.
    try {
      if (typeof __lastStatus !== 'undefined' && __lastStatus) return __lastStatus;
    } catch (_e) {}
    return {};
  }

  function itemRow(item, kind, index){
    const source = (item && typeof item === 'object') ? item : {};
    const url = String(source.url || '');
    const provider = String(source.provider || '').trim().toLowerCase();
    // Mirror the server's rule (see peers.wire_items): a live channel's stream
    // URL can carry credentials anywhere in its path, so it never travels.
    // Saying so up front beats reporting it as a skip after the send.
    const sendable = !!url && provider !== 'iptv';
    return {
      kind,
      index,
      url,
      provider,
      title: String(source.title || source.name || url || 'Untitled'),
      channel: String(source.channel || ''),
      thumbnail: String(source.thumbnail || source.thumbnail_local || ''),
      sendable,
      reason: sendable ? '' : (provider === 'iptv' ? 'Live TV stays here' : 'No shareable link'),
    };
  }

  function buildItems(){
    const st = lastStatus();
    const rows = [];
    const np = st.now_playing;
    // Only offer the session when there really is one: the server refuses a
    // handoff otherwise, and a resumable-but-stopped item is not playing.
    if (playbackActive() && np && np.url) rows.push(itemRow(np, 'now', null));
    (Array.isArray(st.queue) ? st.queue : []).forEach((item, idx) => rows.push(itemRow(item, 'queue', idx)));

    // Key by URL rather than position: auto-next advancing while the sheet is
    // open shifts every index, and a selection must not slide onto a different
    // item. The ordinal disambiguates the same URL queued twice.
    const seen = Object.create(null);
    rows.forEach((row) => {
      const base = `${row.kind}:${row.url}`;
      const n = (seen[base] = (seen[base] || 0) + 1);
      row.key = n === 1 ? base : `${base}#${n}`;
    });

    // Drop exclusions whose item is gone, so a returning URL starts selected.
    const keys = new Set(rows.map((row) => row.key));
    Array.from(state.deselected).forEach((key) => {
      if (!keys.has(key)) state.deselected.delete(key);
    });
    state.items = rows;
  }

  function isSelected(row){
    return !!row && row.sendable && !state.deselected.has(row.key);
  }

  function selectedNow(){
    return state.items.find((row) => row.kind === 'now' && isSelected(row)) || null;
  }

  function selectedQueueIndexes(){
    return state.items.filter((row) => row.kind === 'queue' && isSelected(row)).map((row) => row.index);
  }

  function queueRowCount(){
    return state.items.filter((row) => row.kind === 'queue').length;
  }

  function selectionCoversQueue(){
    return selectedQueueIndexes().length === queueRowCount();
  }

  function syncSubtitle(){
    const el = $('peersSubtitle');
    if (!el) return;
    if (!state.items.length){
      el.textContent = 'Nothing playing or queued';
      return;
    }
    const parts = [];
    if (selectedNow()) parts.push('Now playing');
    const n = selectedQueueIndexes().length;
    if (n) parts.push(`${n} ${n === 1 ? 'item' : 'items'}`);
    el.textContent = parts.length ? parts.join(' + ') : 'Nothing selected';
  }

  function syncTitle(){
    const label = state.mode === 'copy' ? 'Copy to' : 'Send to';
    const el = $('peersTitle');
    if (el) el.textContent = label;
    // The device list and the item list are both lists in this sheet, so each
    // one says what it is. This head also restates the mode next to the rows
    // that act on it, which is where the tap happens.
    const head = $('peersListHead');
    if (head) head.textContent = `${label}:`;
  }

  function modeNote(){
    const now = !!selectedNow();
    if (state.mode === 'copy'){
      return now
        ? 'Both devices play. Nothing here changes.'
        : 'The other device gets a copy. Nothing here changes.';
    }
    return now
      ? 'Playback continues there and stops here.'
      : 'The selected items move off this device.';
  }

  function syncModes(){
    document.querySelectorAll('.pmMode').forEach((button) => {
      const on = (button.dataset.peerMode || 'send') === state.mode;
      button.classList.toggle('on', on);
      button.setAttribute('aria-checked', on ? 'true' : 'false');
    });
    syncTitle();
  }

  function setMode(mode){
    state.mode = String(mode || 'send') === 'copy' ? 'copy' : 'send';
    setStatus('');
    render();
  }

  function nothingToSend(){
    return !selectedNow() && selectedQueueIndexes().length === 0;
  }

  function actionVerb(){
    return state.mode === 'copy' ? 'Copy to' : 'Send to';
  }

  function hostLabel(baseUrl){
    try {
      const u = new URL(String(baseUrl || ''));
      return u.port ? `${u.hostname}:${u.port}` : u.hostname;
    } catch (_e) {
      return String(baseUrl || '');
    }
  }

  function peerMeta(peer){
    if (peer.online === false) return {text: peer.last_error || 'offline', kind: 'err'};
    return {text: hostLabel(peer.base_url), kind: ''};
  }

  function deviceRow(peer){
    const row = document.createElement('div');
    row.className = 'pmRow';

    const pick = document.createElement('button');
    pick.type = 'button';
    pick.className = 'pmDevice';
    pick.disabled = state.sending === peer.id || nothingToSend() || peer.online === false;
    pick.title = peer.online === false ? 'Device is offline' : `${actionVerb()} ${peer.name}`;

    const dot = document.createElement('span');
    dot.className = 'pmDot';
    if (peer.online === true) dot.classList.add('isOnline');
    if (peer.online === false) dot.classList.add('isOffline');

    const text = document.createElement('span');
    text.className = 'pmDeviceText';
    const name = document.createElement('span');
    name.className = 'pmName';
    name.textContent = peer.name || 'RelayTV';
    const meta = document.createElement('span');
    const info = peerMeta(peer);
    meta.className = info.kind ? `pmMeta ${info.kind}` : 'pmMeta';
    meta.textContent = state.sending === peer.id ? 'Sending…' : info.text;
    text.appendChild(name);
    text.appendChild(meta);

    pick.appendChild(dot);
    pick.appendChild(text);
    pick.addEventListener('click', () => sendToPeer(peer));

    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'pmRowBtn danger';
    remove.textContent = 'Remove';
    remove.title = `Forget ${peer.name}`;
    remove.addEventListener('click', () => removePeer(peer));

    row.appendChild(pick);
    row.appendChild(remove);
    return row;
  }

  function nearbyRow(candidate){
    const row = document.createElement('div');
    row.className = 'pmRow';

    const text = document.createElement('span');
    text.className = 'pmDeviceText';
    const name = document.createElement('span');
    name.className = 'pmName';
    name.textContent = candidate.device_name || candidate.name || 'RelayTV';
    const meta = document.createElement('span');
    meta.className = 'pmMeta';
    meta.textContent = hostLabel(candidate.base_url);
    text.appendChild(name);
    text.appendChild(meta);

    const add = document.createElement('button');
    add.type = 'button';
    add.className = 'pmRowBtn';
    const adopting = state.adopting === candidate.base_url;
    add.textContent = adopting ? 'Adding…' : 'Add';
    add.disabled = adopting;
    add.title = `Save ${candidate.device_name || 'this device'}`;
    add.addEventListener('click', () => adoptCandidate(candidate));

    row.appendChild(text);
    row.appendChild(add);
    return row;
  }

  function discoveryNote(found){
    const discovery = state.discovery || {};
    if (discovery.enabled === false) return 'Discovery is turned off on this device.';
    if (discovery.active === false){
      // A bridged container never receives multicast, which is the common
      // cause; say what to change instead of showing an empty list.
      const reason = discovery.last_error ? ` (${discovery.last_error})` : '';
      return `Discovery is unavailable on this network${reason}. Host networking is required for mDNS.`;
    }
    if (found > 0) return '';
    return 'No devices found yet. Add one by address below.';
  }

  function pickRow(row){
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'pmPickRow';
    button.setAttribute('role', 'checkbox');
    const on = isSelected(row);
    button.setAttribute('aria-checked', on ? 'true' : 'false');
    button.classList.toggle('on', on);
    if (!row.sendable){
      button.classList.add('blocked');
      button.disabled = true;
      button.title = row.reason;
    } else {
      button.title = on ? `Leave out ${row.title}` : `Include ${row.title}`;
      button.addEventListener('click', () => toggleItem(row));
    }

    const check = document.createElement('span');
    check.className = 'pmCheck';
    check.setAttribute('aria-hidden', 'true');
    check.textContent = row.sendable ? (on ? '✓' : '') : '✕';

    const thumb = document.createElement('span');
    thumb.className = 'pmPickThumb';
    if (row.thumbnail){
      const img = document.createElement('img');
      img.src = row.thumbnail;
      img.alt = '';
      img.loading = 'lazy';
      img.addEventListener('error', () => img.remove());
      thumb.appendChild(img);
    }

    const text = document.createElement('span');
    text.className = 'pmDeviceText';
    const title = document.createElement('span');
    title.className = 'pmName';
    title.textContent = row.title;
    const meta = document.createElement('span');
    meta.className = 'pmMeta';
    if (row.kind === 'now'){
      const at = nowPlayingPosition();
      meta.textContent = at ? `Now playing · at ${at}` : 'Now playing';
    } else {
      meta.textContent = row.reason || row.channel || '';
    }
    text.appendChild(title);
    text.appendChild(meta);

    button.appendChild(check);
    button.appendChild(thumb);
    button.appendChild(text);
    return button;
  }

  function nowPlayingPosition(){
    const seconds = Number(lastStatus().position || 0);
    if (!Number.isFinite(seconds) || seconds < 1) return '';
    const total = Math.floor(seconds);
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const secs = total % 60;
    const pad = (n) => String(n).padStart(2, '0');
    return hours ? `${hours}:${pad(minutes)}:${pad(secs)}` : `${minutes}:${pad(secs)}`;
  }

  function toggleItem(row){
    if (!row.sendable) return;
    if (state.deselected.has(row.key)) state.deselected.delete(row.key);
    else state.deselected.add(row.key);
    setStatus('');
    render();
  }

  function selectAll(on){
    state.deselected.clear();
    if (!on) state.items.forEach((row) => { if (row.sendable) state.deselected.add(row.key); });
    setStatus('');
    render();
  }

  function renderPicker(){
    const wrap = $('peersPickWrap');
    const list = $('peersPick');
    if (!wrap || !list) return;
    wrap.classList.toggle('hidden', state.items.length === 0);

    const note = $('peersPickNote');
    if (note) note.textContent = state.items.length ? modeNote() : '';

    const bulk = $('peersPickBulk');
    if (bulk) bulk.classList.toggle('hidden', state.items.length < 2);

    // Status pushes re-render about once a second; rebuilding the list every
    // time would fight the user's scroll and drop keyboard focus mid-toggle.
    const signature = `${state.items.map((row) => `${row.key}:${isSelected(row) ? 1 : 0}`).join('|')}|${nowPlayingPosition()}`;
    if (signature === state.pickSignature) return;
    state.pickSignature = signature;

    list.innerHTML = '';
    state.items.forEach((row) => list.appendChild(pickRow(row)));
  }

  function render(){
    buildItems();
    const candidates = state.discovered.filter((c) => !state.peers.some((p) => p.device_id && p.device_id === c.device_id));

    const list = $('peersList');
    if (list){
      list.innerHTML = '';
      if (!state.peers.length){
        // Stay quiet when a nearby device is already offering something to do:
        // "No other devices yet" directly above a found device contradicts it.
        if (state.loading || candidates.length === 0){
          const empty = document.createElement('div');
          empty.className = 'pmEmpty';
          empty.textContent = state.loading ? 'Loading devices…' : 'No other devices yet.';
          list.appendChild(empty);
        }
      } else {
        state.peers.forEach((peer) => list.appendChild(deviceRow(peer)));
      }
    }

    // Nothing to head when there are no saved devices: the empty row or the
    // nearby group already says what the space is for.
    const listHead = $('peersListHead');
    if (listHead) listHead.classList.toggle('hidden', !state.peers.length);

    const wrap = $('peersNearbyWrap');
    const nearby = $('peersNearby');
    const note = $('peersNearbyNote');
    if (wrap && nearby){
      nearby.innerHTML = '';
      // Show the group when there is something new to adopt, or — for someone
      // with no devices yet — to make clear that discovery exists. Once devices
      // are saved and nothing new is around, an empty group is just noise.
      const discovery = state.discovery || {};
      const show = candidates.length > 0 || (state.peers.length === 0 && discovery.enabled === true);
      wrap.classList.toggle('hidden', !show);
      candidates.forEach((candidate) => nearby.appendChild(nearbyRow(candidate)));
      if (note) note.textContent = discoveryNote(candidates.length);
    }

    syncSubtitle();
    syncModes();
    renderPicker();
  }

  async function probeAll(){
    // Probe in parallel on open so status is current without serializing on
    // the slowest device.
    await Promise.all(state.peers.map(async (peer) => {
      try {
        const result = await postJson(`/peers/${encodeURIComponent(peer.id)}/probe`, {});
        peer.online = !!result.online;
        peer.last_error = String(result.error || '');
        if (result.device_name) peer.name = peer.name || result.device_name;
      } catch (_e) {
        peer.online = false;
        peer.last_error = 'offline';
      }
    }));
    render();
  }

  async function load(){
    state.loading = true;
    render();
    try {
      const payload = await api('/peers');
      state.device = payload.device || null;
      state.peers = Array.isArray(payload.peers) ? payload.peers : [];
      state.discovered = Array.isArray(payload.discovered) ? payload.discovered : [];
      state.discovery = payload.discovery || null;
      setStatus('');
    } catch (e) {
      setStatus(e.message || 'Could not load devices', 'err');
    } finally {
      state.loading = false;
      render();
    }
    if (state.peers.length) probeAll().catch(() => null);
  }

  function sendSummary(result){
    const name = (result.peer && result.peer.name) || 'device';
    const accepted = Number(result.accepted || 0);
    const sent = Number(result.sent || 0);
    const rejected = Array.isArray(result.rejected) ? result.rejected : [];
    const noun = accepted === 1 ? 'item' : 'items';
    if (result.status === 'handed_off'){
      const tail = accepted > 0 ? ` with ${accepted} more ${accepted === 1 ? 'item' : 'items'}` : '';
      const lead = result.kept_local ? 'Also playing on' : 'Now playing on';
      return {msg: `${lead} ${name}${tail}`, kind: 'ok'};
    }
    const verb = result.moved ? 'Moved' : 'Copied';
    if (!rejected.length) return {msg: `${verb} ${accepted} ${noun} to ${name}`, kind: 'ok'};
    const reason = rejected[0] && rejected[0].reason ? ` (${rejected[0].reason})` : '';
    return {msg: `${verb} ${accepted} of ${sent} to ${name} — ${rejected.length} skipped${reason}`, kind: ''};
  }

  function sendRequest(peer){
    const id = encodeURIComponent(peer.id);
    const keepLocal = state.mode === 'copy';
    const indexes = selectedQueueIndexes();
    // Omitting the selection when it covers the whole queue means the server
    // reads the queue itself, which stays correct even if an item was added
    // between this render and the request.
    const scoped = selectionCoversQueue() ? {} : {indexes};
    if (selectedNow()){
      // A session is in play, so this is the handoff payload either way; the
      // mode only decides whether this device tears down afterwards.
      return {path: `/peers/${id}/handoff`, body: Object.assign({keep_local: keepLocal}, scoped)};
    }
    // Nothing playing, or the user left it out: this is a plain queue transfer.
    return {path: `/peers/${id}/send`, body: Object.assign({mode: keepLocal ? 'append' : 'move'}, scoped)};
  }

  async function sendToPeer(peer){
    if (state.sending) return;
    if (nothingToSend()){
      setStatus('Nothing selected to send.', 'err');
      return;
    }
    state.sending = peer.id;
    setStatus(`${state.mode === 'copy' ? 'Copying' : 'Sending'} to ${peer.name}…`);
    render();
    try {
      const request = sendRequest(peer);
      const result = await postJson(request.path, request.body);
      const summary = sendSummary(result);
      peer.online = true;
      peer.last_error = '';
      setStatus(summary.msg, summary.kind);
      // Send changes this device's own queue and session; let the shared remote
      // catch up instead of leaving a stale list behind the sheet. Copy leaves
      // this device alone, so there is nothing to re-read.
      if (result.moved || (result.status === 'handed_off' && !result.kept_local)){
        state.deselected.clear();
        if (typeof window.refresh === 'function') window.refresh().catch(() => null);
      }
    } catch (e) {
      peer.online = false;
      peer.last_error = e.message || 'send failed';
      setStatus(`${peer.name}: ${peer.last_error}`, 'err');
    } finally {
      state.sending = '';
      render();
    }
  }

  async function removePeer(peer){
    if (!window.confirm(`Remove ${peer.name} from this device?`)) return;
    try {
      await api(`/peers/${encodeURIComponent(peer.id)}`, {method: 'DELETE'});
      state.peers = state.peers.filter((p) => p.id !== peer.id);
      setStatus(`Removed ${peer.name}`, 'ok');
      render();
    } catch (e) {
      setStatus(e.message || 'Could not remove device', 'err');
    }
  }

  function addFormValues(){
    return {
      base_url: String(($('peerUrlInput') || {}).value || '').trim(),
      name: String(($('peerNameInput') || {}).value || '').trim(),
      token: String(($('peerTokenInput') || {}).value || '').trim(),
    };
  }

  function clearAddForm(){
    ['peerUrlInput', 'peerNameInput', 'peerTokenInput'].forEach((id) => {
      const el = $(id);
      if (el) el.value = '';
    });
  }

  async function testAddress(){
    const values = addFormValues();
    if (!values.base_url){
      setAddHelper('Enter the address of the other device.', 'err');
      return;
    }
    setAddHelper('Testing…');
    try {
      const result = await postJson('/peers/probe', values);
      if (!result.online){
        setAddHelper(result.error || 'Could not reach that address.', 'err');
        return;
      }
      if (result.is_self){
        setAddHelper('That address is this device.', 'err');
        return;
      }
      // Show the name the device reports so the operator can confirm the box
      // before saving it.
      setAddHelper(`Found "${result.device_name}"${result.version ? ` · ${result.version}` : ''}`, 'ok');
      const nameInput = $('peerNameInput');
      if (nameInput && !nameInput.value.trim()) nameInput.value = result.device_name;
    } catch (e) {
      setAddHelper(e.message || 'Could not reach that address.', 'err');
    }
  }

  async function saveAddress(){
    const values = addFormValues();
    if (!values.base_url){
      setAddHelper('Enter the address of the other device.', 'err');
      return;
    }
    setAddHelper('Adding…');
    try {
      const result = await postJson('/peers', values);
      clearAddForm();
      setAddHelper('');
      toggleAddForm(false);
      setStatus(`Added ${(result.peer && result.peer.name) || 'device'}`, 'ok');
      await load();
    } catch (e) {
      setAddHelper(e.message || 'Could not add that device.', 'err');
    }
  }

  async function adoptCandidate(candidate){
    if (state.adopting) return;
    state.adopting = candidate.base_url;
    setStatus('');
    render();
    try {
      await postJson('/peers', {base_url: candidate.base_url, name: candidate.device_name || ''});
      state.adopting = '';
      await load();
      setStatus(`Added ${candidate.device_name || 'device'}`, 'ok');
    } catch (e) {
      state.adopting = '';
      // A device that requires a token cannot be adopted with one tap; send the
      // operator to the form with the address already filled in.
      const message = e.message || 'Could not add that device.';
      if (message.includes('token')){
        toggleAddForm(true);
        const url = $('peerUrlInput');
        const name = $('peerNameInput');
        if (url) url.value = candidate.base_url;
        if (name && !name.value.trim()) name.value = candidate.device_name || '';
        setAddHelper('That device requires an API token. Enter it and add again.', 'err');
      } else {
        setStatus(message, 'err');
      }
      render();
    }
  }

  function toggleAddForm(show){
    const form = $('peersAddForm');
    const toggle = $('peersAddToggle');
    if (!form || !toggle) return;
    const next = (show === undefined) ? form.classList.contains('hidden') : !!show;
    form.classList.toggle('hidden', !next);
    toggle.setAttribute('aria-expanded', next ? 'true' : 'false');
    if (next){
      const input = $('peerUrlInput');
      if (input) input.focus();
    }
  }

  function isOpen(){
    const bd = $('peersBackdrop');
    return !!bd && !bd.classList.contains('hidden');
  }

  function open(opts){
    const bd = $('peersBackdrop');
    if (!bd || isOpen()) return;
    const scope = opts && typeof opts === 'object' ? opts : {};
    state.mode = 'send';
    state.deselected.clear();
    state.pickSignature = '';
    buildItems();
    if (Number.isInteger(scope.index)){
      // Opened from one queue tile: that item is the whole point, so everything
      // else — including the session — starts excluded.
      state.items.forEach((row) => {
        if (row.kind !== 'queue' || row.index !== scope.index) state.deselected.add(row.key);
      });
    }
    state.lastFocus = document.activeElement;
    bd.classList.remove('hidden');
    setStatus('');
    setAddHelper('');
    toggleAddForm(false);
    syncModes();
    // Reuse the shared history-layer helper so Android back closes the sheet.
    if (typeof _uiPushLayer === 'function') _uiPushLayer();
    load().catch(() => null);
    const close = $('peersCloseBtn');
    if (close) close.focus();
  }

  function close(opts){
    const bd = $('peersBackdrop');
    if (!bd || bd.classList.contains('hidden')) return;
    const fromNav = !!(opts && opts.fromNav);
    if (!fromNav && typeof __uiNavDepth === 'number' && __uiNavDepth > 0){
      try { history.back(); } catch (_e) {}
      return;
    }
    bd.classList.add('hidden');
    const target = state.lastFocus && typeof state.lastFocus.focus === 'function' ? state.lastFocus : $('queueSendBtn');
    if (target) {
      try { target.focus(); } catch (_e) {}
    }
  }

  function playbackActive(){
    try {
      if (typeof __lastStatus !== 'undefined' && __lastStatus){
        return !!(__lastStatus.playing || __lastStatus.paused);
      }
    } catch (_e) {}
    return false;
  }

  function bind(){
    $('queueSendBtn')?.addEventListener('click', () => open());
    document.querySelectorAll('.pmMode').forEach((button) => {
      button.addEventListener('click', () => setMode(button.dataset.peerMode || 'send'));
    });
    $('peersPickAll')?.addEventListener('click', () => selectAll(true));
    $('peersPickNone')?.addEventListener('click', () => selectAll(false));
    $('peersCloseBtn')?.addEventListener('click', () => close());
    $('peersAddToggle')?.addEventListener('click', () => toggleAddForm());
    $('peerTestBtn')?.addEventListener('click', testAddress);
    $('peerSaveBtn')?.addEventListener('click', saveAddress);
    $('peersBackdrop')?.addEventListener('click', (event) => {
      if (event.target === $('peersBackdrop')) close();
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && isOpen()){
        event.preventDefault();
        close();
      }
    });
    $('peerUrlInput')?.addEventListener('keydown', (event) => {
      if (event.key === 'Enter'){
        event.preventDefault();
        testAddress();
      }
    });
  }

  function syncPlayback(){
    // Playback and the queue can both move while the sheet is open, so the
    // item list is rebuilt from the pushed status rather than frozen at open.
    if (!isOpen()) return;
    render();
  }

  window.relaytvPeers = {open, close, isOpen, refresh: load, syncPlayback};

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind);
  else bind();
})();
