(function (root, factory) {
  'use strict';
  const api = factory();
  if (root) {
    root.RelayTV = root.RelayTV || {};
    root.RelayTV.overlays = api;
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';

  const focusSelector = [
    'button:not([disabled])',
    'a[href]',
    'input:not([disabled])',
    'select:not([disabled])',
    'textarea:not([disabled])',
    '[tabindex]:not([tabindex="-1"])',
  ].join(',');

  function createDialogController(options) {
    const doc = options.document;
    const win = options.window;
    const backdrop = options.backdrop;
    const opener = options.opener || null;
    const initialFocus = options.initialFocus || null;
    const onRequestClose = options.onRequestClose || (() => {});
    let returnFocus = null;
    let mounted = false;

    function isOpen() {
      return !!backdrop && !backdrop.classList.contains('hidden');
    }

    function focusable() {
      if (!backdrop || !backdrop.querySelectorAll) return [];
      return Array.from(backdrop.querySelectorAll(focusSelector)).filter((element) => {
        return !element.hasAttribute('disabled') && element.getClientRects().length > 0;
      });
    }

    function open(openOpts = {}) {
      if (!backdrop || isOpen()) return false;
      const opts = openOpts && typeof openOpts === 'object' ? openOpts : {};
      returnFocus = opts.opener || opener || doc.activeElement;
      backdrop.classList.remove('hidden');
      const target = opts.initialFocus || initialFocus || focusable()[0];
      if (target && target.focus) target.focus({preventScroll:true});
      return true;
    }

    function close({restoreFocus = true} = {}) {
      if (!backdrop || !isOpen()) return false;
      backdrop.classList.add('hidden');
      const hiddenTarget = returnFocus && (
        returnFocus.classList?.contains?.('hidden')
        || returnFocus.closest?.('.hidden, [hidden]')
      );
      if (restoreFocus && returnFocus && returnFocus.focus && returnFocus.isConnected !== false && !hiddenTarget) {
        returnFocus.focus({preventScroll:true});
      }
      returnFocus = null;
      return true;
    }

    function onKeydown(event) {
      if (!isOpen()) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        onRequestClose();
        return;
      }
      if (event.key !== 'Tab') return;
      const items = focusable();
      if (!items.length) {
        event.preventDefault();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && doc.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && doc.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    function onBackdropClick(event) {
      if (event.target === backdrop) onRequestClose();
    }

    function mount() {
      if (mounted || !backdrop) return;
      mounted = true;
      backdrop.addEventListener('click', onBackdropClick);
      win.addEventListener('keydown', onKeydown);
    }

    function unmount() {
      if (!mounted || !backdrop) return;
      mounted = false;
      backdrop.removeEventListener('click', onBackdropClick);
      win.removeEventListener('keydown', onKeydown);
    }

    return {close, isOpen, mount, open, unmount};
  }

  return {createDialogController};
});
