'use strict';

(function () {
  const STORAGE_KEY = 'yeda_analytics_choice';

  function controller(win, doc, storage) {
    const panel = doc.getElementById('analytics-consent');
    if (!panel) return null;

    const settingsButton = doc.getElementById('analytics-privacy-settings');
    const acceptButton = doc.getElementById('analytics-accept');
    const rejectButton = doc.getElementById('analytics-reject');
    let lastFocused = null;

    function show() {
      lastFocused = doc.activeElement;
      panel.classList.remove('hidden');
      acceptButton?.focus();
    }

    function hide() {
      panel.classList.add('hidden');
      lastFocused?.focus?.();
    }

    function loadPostHog() {
      if (win.posthog || panel.dataset.analyticsLoading === 'true') return;
      panel.dataset.analyticsLoading = 'true';
      const apiKey = panel.dataset.posthogKey;
      const apiHost = panel.dataset.posthogHost;
      if (!apiKey || !apiHost) return;

      !function(t,e){var o,n,p,r;e.__SV||(win.posthog=e,e._i=[],e.init=function(i,s,a){function g(t,e){var o=e.split('.');2==o.length&&(t=t[o[0]],e=o[1]),t[e]=function(){t.push([e].concat(Array.prototype.slice.call(arguments,0)))}}(p=t.createElement('script')).type='text/javascript',p.async=!0,p.src=s.api_host.replace('.i.posthog.com','-assets.i.posthog.com')+'/static/array.js',(r=t.getElementsByTagName('script')[0]).parentNode.insertBefore(p,r);var u=e;for(void 0!==a?u=e[a]=[]:a='posthog',u.people=u.people||[],u.toString=function(t){var e='posthog';return'posthog'!==a&&(e+='.'+a),t||(e+=' (stub)'),e},o='capture opt_out_capturing opt_in_capturing'.split(' '),n=0;n<o.length;n++)g(u,o[n]);e._i.push([i,s,a])},e.__SV=1)}(doc,win.posthog||[]);
      win.posthog.init(apiKey, {
        api_host: apiHost,
        autocapture: false,
        disable_session_recording: true,
        capture_pageview: false,
        capture_pageleave: false,
        persistence: 'memory',
        person_profiles: 'never',
        respect_dnt: true,
        loaded: (posthog) => posthog.capture('page_view', { path: win.location.pathname }),
      });
    }

    function accept() {
      storage.setItem(STORAGE_KEY, 'accepted');
      hide();
      loadPostHog();
    }

    function reject() {
      storage.setItem(STORAGE_KEY, 'rejected');
      win.posthog?.opt_out_capturing?.();
      hide();
    }

    settingsButton?.addEventListener('click', show);
    acceptButton?.addEventListener('click', accept);
    rejectButton?.addEventListener('click', reject);
    panel.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        hide();
      }
    });
    doc.addEventListener('click', (event) => {
      const target = event.target.closest?.('[data-analytics-event]');
      if (!target || !win.posthog?.capture) return;
      win.posthog.capture(target.dataset.analyticsEvent, {
        source: target.dataset.analyticsSource || 'unknown',
      });
    });

    const choice = storage.getItem(STORAGE_KEY);
    if (choice === 'accepted') loadPostHog();
    else if (choice !== 'rejected') show();

    return { show, accept, reject, loadPostHog };
  }

  window.YedaAnalyticsPrivacy = { controller, storageKey: STORAGE_KEY };
  document.addEventListener('DOMContentLoaded', () => controller(window, document, window.localStorage));
})();
