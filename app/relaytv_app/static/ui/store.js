(function (root, factory) {
  'use strict';
  const api = factory();
  if (root) {
    root.RelayTV = root.RelayTV || {};
    root.RelayTV.store = api;
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';

  function createStore(initialSnapshot = null) {
    let snapshot = initialSnapshot;
    const transient = new Map();
    const listeners = new Set();

    function getSnapshot() { return snapshot; }

    function setSnapshot(next) {
      if (Object.is(snapshot, next)) return snapshot;
      snapshot = next;
      for (const listener of Array.from(listeners)) listener(snapshot);
      return snapshot;
    }

    function subscribe(listener) {
      if (typeof listener !== 'function') return () => {};
      listeners.add(listener);
      let active = true;
      return () => {
        if (!active) return;
        active = false;
        listeners.delete(listener);
      };
    }

    function getTransient(key, fallback = null) {
      return transient.has(key) ? transient.get(key) : fallback;
    }

    function setTransient(key, value) {
      if (typeof value === 'undefined') transient.delete(key);
      else transient.set(key, value);
      return value;
    }

    return {getSnapshot, getTransient, setSnapshot, setTransient, subscribe};
  }

  return {createStore};
});
