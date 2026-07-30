// SPDX-License-Identifier: GPL-3.0-only
// Send-to-device sheet: pick a peer RelayTV device and send the queue to it.
//
// Tapping a device sends (copy semantics: this device keeps its queue). Peers
// stay listed while offline with the reason shown, because a device that
// disappears from the list reads as data loss rather than an outage.
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
    mode: 'copy',
    index: null,
    itemTitle: '',
    canHandoff: false,
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

  function queueLength(){
    // app.js keeps the last status payload in a script-scoped binding shared
    // across the UI's classic scripts; fall back to the rendered badge.
    try {
      if (typeof __lastStatus !== 'undefined' && __lastStatus){
        const n = Number(__lastStatus.queue_length || 0);
        if (Number.isFinite(n)) return n;
      }
    } catch (_e) {}
    const el = $('queueCount');
    const n = Number((el && el.textContent) || 0);
    return Number.isFinite(n) ? n : 0;
  }

  function syncSubtitle(){
    const el = $('peersSubtitle');
    if (!el) return;
    if (state.index !== null){
      el.textContent = state.itemTitle || 'One queue item';
      return;
    }
    const n = queueLength();
    el.textContent = n === 0 ? 'Nothing queued' : (n === 1 ? '1 item in the queue' : `${n} items in the queue`);
  }

  function syncTitle(){
    const el = $('peersTitle');
    if (!el) return;
    if (state.mode === 'handoff') el.textContent = 'Continue on';
    else if (state.index !== null) el.textContent = state.mode === 'move' ? 'Move item to' : 'Send item to';
    else el.textContent = state.mode === 'move' ? 'Move queue to' : 'Send queue to';
  }

  function syncModes(){
    // Handoff only makes sense for the whole session, and only when something
    // is actually playing here. The sheet can stay open across playback
    // stopping, so this is re-evaluated on every render and on status pushes
    // rather than being frozen at open time.
    state.canHandoff = playbackActive();
    const handoffAllowed = state.index === null && state.canHandoff;
    if (state.mode === 'handoff' && !handoffAllowed){
      state.mode = 'copy';
      setStatus('Playback stopped here, so Handoff is no longer available.');
    }
    document.querySelectorAll('.pmMode').forEach((button) => {
      const mode = button.dataset.peerMode || 'copy';
      if (mode === 'handoff') button.classList.toggle('hidden', !handoffAllowed);
      const on = mode === state.mode;
      button.classList.toggle('on', on);
      button.setAttribute('aria-checked', on ? 'true' : 'false');
    });
    syncTitle();
  }

  function setMode(mode){
    const next = String(mode || 'copy');
    if (next === 'handoff' && !(state.index === null && playbackActive())) return;
    state.mode = next;
    setStatus('');
    render();
  }

  function nothingToSend(){
    if (state.mode === 'handoff') return !state.canHandoff;
    return state.index === null && queueLength() === 0;
  }

  function actionVerb(){
    if (state.mode === 'handoff') return 'Continue on';
    return state.mode === 'move' ? 'Move to' : 'Send to';
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

  function render(){
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
      return {msg: `Now playing on ${name}${tail}`, kind: 'ok'};
    }
    const verb = result.moved ? 'Moved' : 'Sent';
    if (!rejected.length) return {msg: `${verb} ${accepted} ${noun} to ${name}`, kind: 'ok'};
    const reason = rejected[0] && rejected[0].reason ? ` (${rejected[0].reason})` : '';
    return {msg: `${verb} ${accepted} of ${sent} to ${name} — ${rejected.length} skipped${reason}`, kind: ''};
  }

  async function sendToPeer(peer){
    if (state.sending) return;
    if (nothingToSend()){
      setStatus(state.mode === 'handoff' ? 'Nothing is playing to hand off.' : 'The queue is empty.', 'err');
      return;
    }
    state.sending = peer.id;
    setStatus(state.mode === 'handoff' ? `Handing off to ${peer.name}…` : `Sending to ${peer.name}…`);
    render();
    try {
      const path = state.mode === 'handoff'
        ? `/peers/${encodeURIComponent(peer.id)}/handoff`
        : `/peers/${encodeURIComponent(peer.id)}/send`;
      const body = state.mode === 'handoff'
        ? {}
        : {mode: state.mode === 'move' ? 'move' : 'append', index: state.index};
      const result = await postJson(path, body);
      const summary = sendSummary(result);
      peer.online = true;
      peer.last_error = '';
      setStatus(summary.msg, summary.kind);
      // Move and handoff change this device's own queue; let the shared remote
      // catch up instead of leaving a stale list behind the sheet.
      if (result.moved || result.status === 'handed_off'){
        state.index = null;
        state.itemTitle = '';
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
    state.index = Number.isInteger(scope.index) ? scope.index : null;
    state.itemTitle = String(scope.title || '');
    state.mode = 'copy';
    state.canHandoff = playbackActive();
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
      button.addEventListener('click', () => setMode(button.dataset.peerMode || 'copy'));
    });
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
    if (!isOpen()) return;
    syncModes();
  }

  window.relaytvPeers = {open, close, isOpen, refresh: load, syncPlayback};

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind);
  else bind();
})();
