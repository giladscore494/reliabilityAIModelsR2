'use strict';

(function () {
  const drawer = document.getElementById('navbar-drawer');
  const toggle = document.getElementById('navbar-toggle');
  const closeBtn = document.getElementById('navbar-close');

  if (!drawer || !toggle) return;

  const overlay = drawer.querySelector('.drawer-overlay');
  const panel = drawer.querySelector('.drawer-panel');
  const links = drawer.querySelectorAll('.nav-drawer-link');
  let lastFocused = null;
  const background = () => Array.from(document.body.children).filter((el) => el !== drawer && el.tagName !== 'SCRIPT');

  function focusables() {
    return Array.from(panel.querySelectorAll('a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])'));
  }

  function open() {
    lastFocused = document.activeElement;
    drawer.classList.remove('hidden');
    drawer.setAttribute('aria-hidden', 'false');
    toggle.setAttribute('aria-expanded', 'true');
    background().forEach((el) => { el.inert = true; });
    requestAnimationFrame(() => {
      panel.classList.remove('translate-x-full');
      panel.classList.add('translate-x-0');
    });
    document.body.classList.add('overflow-hidden');
    (closeBtn || panel).focus();
  }

  function close({ restoreFocus = true } = {}) {
    panel.classList.add('translate-x-full');
    panel.classList.remove('translate-x-0');
    drawer.setAttribute('aria-hidden', 'true');
    toggle.setAttribute('aria-expanded', 'false');
    background().forEach((el) => { el.inert = false; });
    setTimeout(() => drawer.classList.add('hidden'), 180);
    document.body.classList.remove('overflow-hidden');
    if (restoreFocus && lastFocused && typeof lastFocused.focus === 'function') lastFocused.focus();
  }

  toggle.addEventListener('click', () => {
    if (drawer.classList.contains('hidden')) open();
    else close();
  });

  closeBtn?.addEventListener('click', close);
  overlay?.addEventListener('click', close);
  links.forEach((link) => link.addEventListener('click', close));

  // Escape key
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !drawer.classList.contains('hidden')) {
      close();
      return;
    }
    if (e.key === 'Tab' && !drawer.classList.contains('hidden')) {
      const items = focusables();
      if (!items.length) { e.preventDefault(); panel.focus(); return; }
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  });
})();
