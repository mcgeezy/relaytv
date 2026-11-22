(function (root, factory) {
  'use strict';
  const api = factory();
  if (root) {
    root.RelayTV = root.RelayTV || {};
    root.RelayTV.shell = api;
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';

  const DESTINATIONS = new Set(['remote', 'browse', 'settings']);

  function destinationFromHash(hash) {
    const value = String(hash || '').replace(/^#/, '').split('/')[0].toLowerCase();
    return DESTINATIONS.has(value) ? value : 'remote';
  }

  function destinationUrl(win, destination) {
    return `${win.location.pathname}${win.location.search}#${destination}`;
  }

  function createDestinationState(win, onChange) {
    let current = destinationFromHash(win.location.hash);
    let unbind = null;

    function emit(source) {
      current = destinationFromHash(win.location.hash);
      if (typeof onChange === 'function') onChange(current, source);
      return current;
    }

    function navigate(destination, options) {
      const next = DESTINATIONS.has(destination) ? destination : 'remote';
      const replace = !!(options && options.replace);
      if (next === current && String(win.location.hash || '').toLowerCase() === `#${next}`) {
        if (typeof onChange === 'function') onChange(current, 'repeat');
        return current;
      }
      const state = Object.assign({}, win.history.state || {}, {
        relaytv_destination:next,
        relaytv_previous_destination:current,
      });
      win.history[replace ? 'replaceState' : 'pushState'](state, '', destinationUrl(win, next));
      current = next;
      if (typeof onChange === 'function') onChange(current, replace ? 'replace' : 'navigate');
      return current;
    }

    function bind() {
      if (unbind) return unbind;
      const listener = () => emit('popstate');
      win.addEventListener('popstate', listener);
      unbind = () => {
        win.removeEventListener('popstate', listener);
        unbind = null;
      };
      return unbind;
    }

    function getCurrent() { return current; }
    return {bind, emit, getCurrent, navigate};
  }

  function hasNowPlaying(snapshot) {
    if (!snapshot || typeof snapshot !== 'object') return false;
    if (snapshot.playing || snapshot.paused) return true;
    const item = snapshot.now_playing;
    const hasItem = !!(item && typeof item === 'object' && (item.title || item.url || item.stream));
    if (typeof snapshot.has_now_playing === 'boolean') return snapshot.has_now_playing && hasItem;
    return hasItem;
  }

  function playbackCopy(snapshot) {
    const item = snapshot && snapshot.now_playing && typeof snapshot.now_playing === 'object'
      ? snapshot.now_playing : {};
    const playing = !!(snapshot && snapshot.playing && !snapshot.paused);
    const meta = item.channel || item.subtitle || item.provider || 'RelayTV';
    return {
      hasItem:hasNowPlaying(snapshot),
      meta:String(meta || 'RelayTV'),
      playing,
      title:String(item.title || 'Nothing playing'),
    };
  }

  function initShell(win, doc) {
    const remote = doc.getElementById('remoteDestination');
    const browse = doc.getElementById('browseDestination');
    const settings = doc.getElementById('settingsBackdrop');
    const bar = doc.getElementById('compactPlaybackBar');
    const store = win.RelayTV?.runtime?.statusStore;
    let returnAfterProviderPop = false;
    let currentSnapshot = store?.getSnapshot() || null;

    const providerShells = [
      doc.getElementById('iptvShell'),
      doc.getElementById('jellyfinShell'),
      doc.getElementById('plexShell'),
      doc.getElementById('seerrShell'),
    ].filter(Boolean);

    function providerIsOpen() {
      return providerShells.some((shell) => !shell.classList.contains('hidden'));
    }

    function layerDepth() {
      return Number(win.RelayTV?.runtime?.layerNavigation?.getDepth?.() || 0);
    }

    function closeOpenProviders() {
      if (win.relaytvPlex?.isDetailOpen?.()) win.relaytvPlex.closeDetail({fromNav:true});
      if (win.relaytvPlex?.isOpen?.()) win.relaytvPlex.close({fromNav:true, force:true});
      if (win.relaytvSeerr?.isDetailOpen?.()) win.relaytvSeerr.closeDetail({fromNav:true});
      if (win.relaytvSeerr?.isOpen?.()) win.relaytvSeerr.close({fromNav:true, force:true});
      if (win.relaytvIptv?.isOpen?.()) win.relaytvIptv.close({fromNav:true, force:true});
      if (typeof win._jfCloseDetailPanel === 'function' && typeof win._jfIsDetailOpen === 'function' && win._jfIsDetailOpen()) {
        win._jfCloseDetailPanel({fromNav:true});
      }
      if (typeof win.closeJellyfinShell === 'function' && !doc.getElementById('jellyfinShell')?.classList.contains('hidden')) {
        win.closeJellyfinShell({fromNav:true, force:true});
      }
    }

    function returnToRemoteDestination() {
      if (navigation.getCurrent() === 'remote') return;
      if (win.history.state?.relaytv_previous_destination) win.history.back();
      else navigation.navigate('remote', {replace:true});
    }

    function syncBar() {
      if (!bar) return;
      const copy = playbackCopy(currentSnapshot);
      const away = navigation.getCurrent() !== 'remote' || providerIsOpen();
      bar.classList.toggle('hidden', !away || !copy.hasItem);
      doc.getElementById('compactTitle').textContent = copy.title;
      doc.getElementById('compactMeta').textContent = copy.meta;
      const play = doc.getElementById('compactPlayPauseBtn');
      if (play) {
        play.textContent = copy.playing ? 'Ⅱ' : '▶';
        play.setAttribute('aria-label', copy.playing ? 'Pause' : 'Play');
        play.classList.toggle('isPlaying', copy.playing);
      }
      const art = doc.getElementById('compactArtwork');
      if (art) {
        let url = '';
        try {
          if (typeof win.thumbUrl === 'function') url = win.thumbUrl(currentSnapshot?.now_playing || {});
        } catch (_error) {}
        const safeUrl = String(url || '').replace(/[\r\n\f"'()\\]/g, '').trim();
        art.style.backgroundImage = safeUrl ? `url("${safeUrl}")` : '';
        art.classList.toggle('hasArtwork', !!safeUrl);
      }
    }

    function render(destination) {
      const isRemote = destination === 'remote';
      remote?.classList.toggle('hidden', !isRemote);
      browse?.classList.toggle('hidden', destination !== 'browse');
      doc.querySelectorAll('[data-destination]').forEach((button) => {
        const active = button.dataset.destination === destination;
        button.classList.toggle('isActive', active);
        if (active) button.setAttribute('aria-current', 'page');
        else button.removeAttribute('aria-current');
      });
      if (destination === 'settings') {
        if (typeof win.openSettings === 'function') win.openSettings({fromDestination:true});
      } else if (settings && !settings.classList.contains('hidden') && typeof win.closeSettings === 'function') {
        win.closeSettings({fromNav:true});
      }
      syncBar();
    }

    const navigation = createDestinationState(win, (destination, source) => {
      render(destination);
      if (returnAfterProviderPop && source === 'popstate') {
        if (providerIsOpen() || layerDepth() > 0) {
          win.setTimeout(() => {
            if (!returnAfterProviderPop) return;
            if (providerIsOpen() || layerDepth() > 0) win.history.back();
            else {
              returnAfterProviderPop = false;
              returnToRemoteDestination();
            }
          }, 0);
          return;
        }
        returnAfterProviderPop = false;
        win.setTimeout(() => {
          returnToRemoteDestination();
          const active = doc.activeElement;
          if (!active || active === doc.body || active.closest?.('.hidden, [hidden]')) {
            doc.querySelector('[data-destination="remote"]')?.focus?.();
          }
        }, 0);
      }
    });

    function useDestination(destination) {
      if (providerIsOpen()) closeOpenProviders();
      navigation.navigate(destination);
    }

    doc.querySelectorAll('[data-destination]').forEach((button) => {
      button.addEventListener('click', () => useDestination(button.dataset.destination));
    });
    doc.getElementById('browseConfigureBtn')?.addEventListener('click', () => useDestination('settings'));

    function syncProviderChoice(choice) {
      const target = doc.getElementById(choice.dataset.providerTarget || '');
      if (!target) return;
      const ready = target.classList.contains('show') && !target.disabled;
      choice.classList.toggle('isUnavailable', !ready);
      const labelEl = choice.querySelector('strong');
      if (choice.dataset.providerTarget === 'jellyfinOpenBtn' && labelEl) {
        const snap = currentSnapshot || {};
        const browseCount = Number(snap.browse_connected_count || 0);
        const jfConfigured = !!snap.jellyfin_server_url_configured;
        if (browseCount > 1 || (snap.browse_connected && !jfConfigured)) {
          labelEl.textContent = 'Libraries';
        } else if (jfConfigured) {
          labelEl.textContent = String(snap.jellyfin_server_type || '').toLowerCase() === 'emby' ? 'Emby' : 'Jellyfin';
        } else {
          labelEl.textContent = 'Libraries';
        }
      }
      if (ready) choice.removeAttribute('aria-label');
      else choice.setAttribute('aria-label', `Configure ${labelEl?.textContent || 'service'} in Settings`);
      const status = choice.querySelector('[data-provider-status]');
      const remembered = win.localStorage.getItem('relaytv_browse_provider') === choice.dataset.providerTarget;
      choice.classList.toggle('isRecent', remembered);
      if (status) status.textContent = ready
        ? (remembered ? 'Ready · last used' : 'Ready to browse')
        : 'Set up in Settings';
    }

    doc.querySelectorAll('[data-provider-target]').forEach((choice) => {
      const target = doc.getElementById(choice.dataset.providerTarget || '');
      if (target) new MutationObserver(() => syncProviderChoice(choice))
        .observe(target, {attributes:true, attributeFilter:['class', 'disabled', 'title']});
      choice.addEventListener('click', () => {
        syncProviderChoice(choice);
        if (!target || target.disabled || !target.classList.contains('show')) {
          useDestination('settings');
          return;
        }
        win.localStorage.setItem('relaytv_browse_provider', choice.dataset.providerTarget);
        doc.querySelectorAll('[data-provider-target]').forEach(syncProviderChoice);
        target.click();
        syncBar();
      });
      syncProviderChoice(choice);
    });

    doc.getElementById('compactPlayPauseBtn')?.addEventListener('click', () => {
      if (typeof win.post === 'function') win.post('/playback/toggle');
    });
    doc.getElementById('compactReturnBtn')?.addEventListener('click', () => {
      if (providerIsOpen()) {
        if (layerDepth() > 0) {
          returnAfterProviderPop = true;
          win.history.back();
        } else {
          closeOpenProviders();
          returnToRemoteDestination();
        }
        return;
      }
      returnToRemoteDestination();
    });

    if (store?.subscribe) store.subscribe((snapshot) => {
      currentSnapshot = snapshot;
      doc.querySelectorAll('[data-provider-target]').forEach(syncProviderChoice);
      syncBar();
    });

    const shellObserver = new MutationObserver(() => {
      syncBar();
      if (settings?.classList.contains('hidden') && navigation.getCurrent() === 'settings') {
        win.setTimeout(() => {
          if (navigation.getCurrent() !== 'settings' || !settings.classList.contains('hidden')) return;
          if (win.history.state?.relaytv_previous_destination) win.history.back();
          else navigation.navigate('remote', {replace:true});
        }, 0);
      }
    });
    providerShells.forEach((shell) => shellObserver.observe(shell, {attributes:true, attributeFilter:['class']}));
    if (settings) shellObserver.observe(settings, {attributes:true, attributeFilter:['class']});

    const initial = destinationFromHash(win.location.hash);
    if (!DESTINATIONS.has(String(win.location.hash || '').replace(/^#/, ''))) {
      navigation.navigate(initial, {replace:true});
    } else {
      win.history.replaceState(Object.assign({}, win.history.state || {}, {
        relaytv_destination:initial,
      }), '', destinationUrl(win, initial));
      render(initial);
    }
    navigation.bind();
    doc.documentElement.classList.add('shellReady');
    return {navigation, render, syncBar};
  }

  if (typeof window !== 'undefined' && typeof document !== 'undefined') {
    window.addEventListener('DOMContentLoaded', () => {
      window.RelayTV.runtime.shell = initShell(window, document);
    });
  }

  return {createDestinationState, destinationFromHash, hasNowPlaying, initShell, playbackCopy};
});
