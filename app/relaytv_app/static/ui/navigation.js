(function (root, factory) {
  'use strict';
  const api = factory();
  if (root) {
    root.RelayTV = root.RelayTV || {};
    root.RelayTV.navigation = api;
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';

  function createLayerNavigation(win) {
    let depth = 0;
    let popPending = false;
    let popTimer = 0;
    let unbind = null;

    function clearPending() {
      popPending = false;
      if (popTimer && win && typeof win.clearTimeout === 'function') {
        win.clearTimeout(popTimer);
      }
      popTimer = 0;
    }

    function push() {
      clearPending();
      try {
        win.history.pushState({relaytv_ui:1, t:Date.now()}, '');
        depth += 1;
      } catch (_error) {}
      return depth;
    }

    function back() {
      if (depth <= 0) return false;
      if (popPending) return true;
      popPending = true;
      if (win && typeof win.setTimeout === 'function') {
        popTimer = win.setTimeout(clearPending, 300);
      }
      try { win.history.back(); } catch (_error) {
        clearPending();
        return false;
      }
      return true;
    }

    function bind(onPop) {
      if (unbind || !win || !win.addEventListener) return unbind || (() => {});
      const listener = () => {
        clearPending();
        if (depth > 0) depth -= 1;
        if (onPop) onPop();
      };
      win.addEventListener('popstate', listener);
      unbind = () => {
        clearPending();
        win.removeEventListener('popstate', listener);
        unbind = null;
      };
      return unbind;
    }

    function getDepth() { return depth; }

    return {back, bind, getDepth, push};
  }

  return {createLayerNavigation};
});
