const {test} = require('node:test');
const assert = require('node:assert/strict');
const {view, render} = require('./observer.js');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('formats a simulated observation without calling reward an HR prediction', () => {
  const state = {source_kind:'simulated', patient_id:'p1', session_id:'s1', learning_enabled:true,
    adaptive:{updates:2, last_observation:{session_id:'s1', song_id:'jamendo:1', updated:true,
      context:{mean:90}, later_hr:84, state_only_trend_forecast:89, reward:0.5}}};
  const result = view(state);
  assert.equal(result.pre, '90.00 bpm');
  assert.equal(result.post, '84.00 bpm');
  assert.equal(result.delta, '-6.00 bpm');
  assert.equal(result.forecast, '89.00 bpm');
  assert.equal(result.reward, '0.500');
  assert.equal(result.updates, '2');
  assert.equal(result.provenance, 'Simulated response');
  assert.equal(result.scope, 'Current session');
});

test('handles missing outcomes, genuine zero values and interruptions', () => {
  assert.equal(view({}).post, '—');
  const result = view({stopped:true, nurse_present:true, adaptive:{updates:0,
    last_observation:{updated:false, reason:'control_changed', context:null}}});
  assert.equal(result.status, 'Stopped');
  assert.equal(result.updates, '0');
  assert.equal(result.delta, '—');
  assert.equal(result.reason, 'control_changed');
  assert.equal(view({adaptive:{last_observation:{updated:true,context:{mean:72},later_hr:72,reward:0}}}).delta, '0.00 bpm');
});

test('replayed HR never relabels a retained simulated outcome as recorded', () => {
  const result = view({source_kind:'recorded', session_id:'new', adaptive:{updates:3,
    last_observation:{session_id:'old', updated:true, context:{mean:90}, later_hr:84}}});
  assert.equal(result.status, 'Replay only');
  assert.equal(result.provenance, 'Simulated response');
  assert.equal(result.scope, 'Previous session');
});

test('live outcomes show actual provenance and the reward source', () => {
  const result = view({source_kind:'live', simulated:false, learning_enabled:true,
    adaptive:{last_observation:{source_kind:'live', simulated:false, updated:true,
      reward_source:'comfort', comfort_rating:4, reward:.5}}});
  assert.equal(result.mode, 'Live Garmin');
  assert.equal(result.provenance, 'Live response');
  assert.equal(result.rewardSource, 'Comfort rating');
  assert.equal(result.comfort, '4 / 5');
  assert.equal(view({adaptive:{last_observation:{updated:true,reward_source:'hr_proxy'}}}).rewardSource,
    'HR proxy (comfort unknown)');
});

test('writes untrusted IDs as text, never HTML', () => {
  const elements = new Map();
  const doc = {getElementById(id) {
    if (!elements.has(id)) elements.set(id, {textContent:'', set innerHTML(_) {throw Error('unsafe HTML');}});
    return elements.get(id);
  }};
  render({patient_id:'<img src=x onerror=alert(1)>'}, doc);
  assert.equal(elements.get('obs-patient').textContent, '<img src=x onerror=alert(1)>');
});

test('shows controller gates, external summaries and update status accurately', () => {
  const cases = [
    [{nurse_present:true}, 'Nurse present'],
    [{adaptive:{error:'checkpoint failed'}}, 'Learning paused'],
    [{learning_enabled:false}, 'Learning disabled'],
    [{learning_enabled:true,adaptive:{external:true}}, 'External adaptive layer'],
    [{learning_enabled:true,adaptive:{updating:true}}, 'Updating response model'],
    [{learning_enabled:true,active_exposure_id:'e1'}, 'Observing response'],
  ];
  for (const [state, expected] of cases) assert.equal(view(state).status, expected);
  assert.equal(view({adaptive:{external:true}}).updates, '—');
  assert.equal(view({adaptive:{last_observation:{updated:true,context:{mean:NaN},later_hr:80}}}).delta, '—');
});

test('complete console polls state and renders learning outcomes alongside Stop', async () => {
  const html = fs.readFileSync(path.join(__dirname, 'console.html'), 'utf8');
  const ids = new Set([...html.matchAll(/id="([^"]+)"/g)].map(match => match[1]));
  for (const key of Object.keys(view({}))) assert.ok(ids.has(`obs-${key}`), `Missing observer element: ${key}`);
  const elements = new Map();
  const context = new Proxy({}, {get: (_, key) => () => {}, set: () => true});
  const doc = {getElementById(id) {
    if (!ids.has(id)) return null;
    if (!elements.has(id)) elements.set(id, {textContent:'', innerHTML:'', style:{}, classList:{toggle() {}},
      clientWidth:600, clientHeight:170, getContext:() => context});
    return elements.get(id);
  }, querySelectorAll:() => []};
  const state = {input:'SIMULATED test', simulated:true, interactive:true, source_kind:'simulated',
    patient_id:'p1', session_id:'s1', room:'Elevated', hr:90, baseline:72, threshold:10,
    history:[null,89,90], baseline_progress:120, baseline_needed:120, events:[],
    learning_enabled:true, adaptive:{updates:2,last_observation:{session_id:'s1',song_id:'jamendo:1',
      updated:true,context:{mean:90},later_hr:84,reward:.5,state_only_trend_forecast:89}}};
  const requests = [];
  let poll;
  const sandbox = {document:doc, devicePixelRatio:1, getComputedStyle:() => ({getPropertyValue:() => '#888'}),
    addEventListener() {}, setInterval(fn) {poll=fn;},
    ResonanceObserver:{render:s => render(s,doc)},
    fetch:async (url, options) => {requests.push([url,options]);return {ok:true,json:async () => JSON.parse(JSON.stringify(state))};}};
  vm.createContext(sandbox);
  const scripts = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]).filter(s => s.trim());
  for (const script of scripts) vm.runInContext(script,sandbox);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(elements.get('obs-delta').textContent, '-6.00 bpm');
  await poll();
  assert.ok(requests.every(([url]) => url === '/state'));
  assert.equal(elements.get('obs-updates').textContent, '2');
  state.stopped=true;state.room='Stopped';
  await poll();
  assert.equal(elements.get('obs-status').textContent, 'Stopped');
  assert.equal(elements.get('stopBtn').textContent, 'Patient resumes');
  Object.assign(state, {stopped:false, room:'Elevated', simulated:false, source_kind:'live',
    feedback_open:true, active_exposure_id:'live-e1'});
  await poll();
  assert.equal(elements.get('obs-mode').textContent, 'Live Garmin');
  elements.get('comfortRating').value = '4';
  await elements.get('comfortSubmit').onclick();
  const feedback = requests.find(([url]) => url === '/feedback');
  assert.deepEqual(JSON.parse(feedback[1].body), {session_id:'s1', exposure_id:'live-e1', rating:4});
  assert.match(elements.get('comfortMessage').textContent, /Comfort recorded/);
  elements.get('simBtn').onclick();
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(requests.some(([url, options]) => url === '/input' &&
    JSON.parse(options.body).action === 'source:live'));
});
