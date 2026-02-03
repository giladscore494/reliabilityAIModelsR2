'use strict';

(function () {
  const drawer = document.getElementById('navbar-drawer');
  const toggle = document.getElementById('navbar-toggle');
  const closeBtn = document.getElementById('navbar-close');

  if (!drawer || !toggle) return;

  const overlay = drawer.querySelector('.drawer-overlay');
  const panel = drawer.querySelector('.drawer-panel');
  const links = drawer.querySelectorAll('.nav-drawer-link');

  function open() {
    drawer.classList.remove('hidden');
    requestAnimationFrame(() => {
      panel.classList.remove('translate-x-full');
      panel.classList.add('translate-x-0');
    });
    document.body.classList.add('overflow-hidden');
  }

  function close() {
    panel.classList.add('translate-x-full');
    panel.classList.remove('translate-x-0');
    setTimeout(() => drawer.classList.add('hidden'), 180);
    document.body.classList.remove('overflow-hidden');
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
    }
  });
})();
