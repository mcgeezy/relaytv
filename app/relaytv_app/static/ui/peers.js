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
    device: null,
    loading: false,
    sending: '',
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
    const n = queueLength();
    el.textContent = n === 0 ? 'Nothing queued' : (n === 1 ? '1 item in the queue' : `${n} items in the queue`);
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
    pick.disabled = state.sending === peer.id || queueLength() === 0 || peer.online === false;
    pick.title = peer.online === false ? 'Device is offline' : `Send the queue to ${peer.name}`;

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
    add.textContent = 'Add';
    add.addEventListener('click', () => adoptCandidate(candidate));

    row.appendChild(text);
    row.appendChild(add);
    return row;
  }

  function render(){
    const list = $('peersList');
    if (list){
      list.innerHTML = '';
      if (!state.peers.length){
        const empty = document.createElement('div');
        empty.className = 'pmEmpty';
        empty.textContent = state.loading ? 'Loading devices…' : 'No other devices yet.';
        list.appendChild(empty);
      } else {
        state.peers.forEach((peer) => list.appendChild(deviceRow(peer)));
      }
    }

    const wrap = $('peersNearbyWrap');
    const nearby = $('peersNearby');
    if (wrap && nearby){
      nearby.innerHTML = '';
      const candidates = state.discovered.filter((c) => !state.peers.some((p) => p.device_id && p.device_id === c.device_id));
      wrap.classList.toggle('hidden', candidates.length === 0);
      candidates.forEach((candidate) => nearby.appendChild(nearbyRow(candidate)));
    }

    syncSubtitle();
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
    if (!rejected.length) return {msg: `Sent ${accepted} ${noun} to ${name}`, kind: 'ok'};
    const reason = rejected[0] && rejected[0].reason ? ` (${rejected[0].reason})` : '';
    return {msg: `Sent ${accepted} of ${sent} to ${name} — ${rejected.length} skipped${reason}`, kind: ''};
  }

  async function sendToPeer(peer){
    if (state.sending) return;
    if (queueLength() === 0){
      setStatus('The queue is empty.', 'err');
      return;
    }
    state.sending = peer.id;
    setStatus(`Sending to ${peer.name}…`);
    render();
    try {
      const result = await postJson(`/peers/${encodeURIComponent(peer.id)}/send`, {mode: 'append'});
      const summary = sendSummary(result);
      peer.online = true;
      peer.last_error = '';
      setStatus(summary.msg, summary.kind);
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
    setStatus(`Adding ${candidate.device_name || 'device'}…`);
    try {
      await postJson('/peers', {base_url: candidate.base_url, name: candidate.device_name || ''});
      await load();
    } catch (e) {
      setStatus(e.message || 'Could not add that device.', 'err');
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

  function open(){
    const bd = $('peersBackdrop');
    if (!bd || isOpen()) return;
    state.lastFocus = document.activeElement;
    bd.classList.remove('hidden');
    setStatus('');
    setAddHelper('');
    toggleAddForm(false);
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

  function bind(){
    $('queueSendBtn')?.addEventListener('click', open);
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

  window.relaytvPeers = {open, close, isOpen, refresh: load};

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind);
  else bind();
})();
