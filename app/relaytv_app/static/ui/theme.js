(function (root, factory) {
  'use strict';
  let storage = null;
  try { storage = root && root.localStorage; } catch (_error) {}
  const api = factory({
    window: root,
    document: root && root.document,
    storage,
  });
  if (root) root.relaytvTheme = api;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = {createThemeController: factory};
  }
  if (root && root.document) api.initialize();
})(typeof window !== 'undefined' ? window : globalThis, function createThemeController(env) {
  'use strict';

  const win = env && env.window;
  const doc = env && env.document;
  const storage = env && env.storage;
  const themeKey = 'relaytv_theme';
  const themeMedia = /\(prefers-color-scheme:\s*(light|dark)\)/;
  let bound = false;
  let initialized = false;

  function storedMode() {
    try {
      const value = storage && storage.getItem(themeKey);
      return value === 'dark' || value === 'light' ? value : 'auto';
    } catch (_error) {
      return 'auto';
    }
  }

  function applyToSheets(mode) {
    if (!doc) return;
    for (const sheet of Array.from(doc.styleSheets || [])) {
      let rules = null;
      try { rules = sheet.cssRules; } catch (_error) { continue; }
      if (!rules) continue;
      for (const rule of Array.from(rules)) {
        if (!rule.media || !rule.media.mediaText) continue;
        const original = rule.__relaytvOrigMedia || rule.media.mediaText;
        const match = original.match(themeMedia);
        if (!match) continue;
        rule.__relaytvOrigMedia = original;
        if (mode === 'auto') {
          rule.media.mediaText = original;
        } else {
          const enabled = match[1] === mode;
          rule.media.mediaText = original.replace(
            themeMedia,
            enabled ? '(min-width: 0px)' : '(min-width: 99999px)',
          );
        }
      }
    }
  }

  function effectiveMode(mode) {
    if (mode !== 'auto') return mode;
    try {
      return win && win.matchMedia && win.matchMedia('(prefers-color-scheme: light)').matches
        ? 'light'
        : 'dark';
    } catch (_error) {
      return 'dark';
    }
  }

  function apply(mode) {
    const selected = mode === 'dark' || mode === 'light' ? mode : 'auto';
    applyToSheets(selected);
    if (!doc) return selected;
    try {
      doc.documentElement.dataset.theme = selected;
      doc.documentElement.style.colorScheme = selected === 'auto' ? '' : selected;
    } catch (_error) {}
    const meta = doc.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', effectiveMode(selected) === 'light' ? '#f4f6f9' : '#0b1017');
    doc.querySelectorAll('.mtBtn').forEach((button) => {
      const active = (button.dataset.themeMode || 'auto') === selected;
      button.classList.toggle('on', active);
      button.setAttribute('aria-checked', active ? 'true' : 'false');
    });
    return selected;
  }

  function select(mode) {
    const selected = mode === 'dark' || mode === 'light' ? mode : 'auto';
    try { if (storage) storage.setItem(themeKey, selected); } catch (_error) {}
    return apply(selected);
  }

  function bind() {
    if (bound || !doc) return;
    bound = true;
    doc.querySelectorAll('.mtBtn').forEach((button) => {
      button.onclick = () => select(button.dataset.themeMode || 'auto');
    });
    try {
      const media = win && win.matchMedia && win.matchMedia('(prefers-color-scheme: light)');
      const onChange = () => apply(storedMode());
      if (media && media.addEventListener) media.addEventListener('change', onChange);
      else if (media && media.addListener) media.addListener(onChange);
    } catch (_error) {}
  }

  function initialize() {
    if (initialized || !doc) return;
    initialized = true;
    apply(storedMode());
    if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', bind, {once:true});
    else bind();
    if (win && win.addEventListener) {
      win.addEventListener('load', () => apply(storedMode()), {once:true});
    }
  }

  return {apply, applyToSheets, bind, effectiveMode, initialize, select, storedMode};
});
