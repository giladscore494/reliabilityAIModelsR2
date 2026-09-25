'use strict';

const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const source = fs.readFileSync(path.join(__dirname, '../../static/analytics_privacy.js'), 'utf8');

function environment(initialChoice) {
  const listeners = {};
  const inserted = [];
  const storageValues = initialChoice ? { yeda_analytics_choice: initialChoice } : {};
  function element(id) {
    return {
      id,
      dataset: id === 'analytics-consent' ? { posthogKey: 'ph_test', posthogHost: 'https://us.i.posthog.com' } : {},
      classList: { values: new Set(['hidden']), add(v) { this.values.add(v); }, remove(v) { this.values.delete(v); } },
      addEventListener(type, callback) { listeners[`${id}:${type}`] = callback; },
      focus() {},
    };
  }
  const elements = Object.fromEntries(['analytics-consent', 'analytics-privacy-settings', 'analytics-accept', 'analytics-reject'].map((id) => [id, element(id)]));
  const firstScript = { parentNode: { insertBefore(script) { inserted.push(script); } } };
  const document = {
    activeElement: { focus() {} },
    getElementById(id) { return elements[id] || null; },
    addEventListener(type, callback) { listeners[`document:${type}`] = callback; },
    createElement() { return {}; },
    getElementsByTagName() { return [firstScript]; },
  };
  const localStorage = {
    getItem(key) { return storageValues[key] || null; },
    setItem(key, value) { storageValues[key] = value; },
  };
  const window = { localStorage, location: { pathname: '/privacy' } };
  vm.runInNewContext(source, { window, document, Array, Object });
  listeners['document:DOMContentLoaded']();
  return { window, elements, listeners, inserted, storageValues };
}

const rejected = environment('rejected');
assert.strictEqual(rejected.inserted.length, 0, 'rejected choice must not load PostHog');
rejected.listeners['analytics-privacy-settings:click']();
assert(!rejected.elements['analytics-consent'].classList.values.has('hidden'), 'settings button must reopen controls');
rejected.listeners['analytics-accept:click']();
assert.strictEqual(rejected.storageValues.yeda_analytics_choice, 'accepted');
assert.strictEqual(rejected.inserted.length, 1, 'accepting must load PostHog once');

const accepted = environment('accepted');
assert.strictEqual(accepted.inserted.length, 1, 'stored acceptance must load PostHog');
accepted.listeners['analytics-reject:click']();
assert.strictEqual(accepted.storageValues.yeda_analytics_choice, 'rejected', 'decision must be changeable');

const undecided = environment(null);
assert.strictEqual(undecided.inserted.length, 0, 'undecided visitor must not load PostHog');
assert(!undecided.elements['analytics-consent'].classList.values.has('hidden'), 'undecided visitor must see controls');
