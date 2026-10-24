// Resonance operator console: replays heart rate, runs the evidence agent, plays music.
// Data: data/sessions.json (build_app_data.py), data/music_library.json (build_music_library.py),
// data/evidence.json (hand-written, sources verified).

const BASELINE_S = 120;       // baseline = median of the first 120 s (hr_test.py)
const SMOOTH_S = 10;          // 10 s rolling median (hr_test.py)
const SUSTAIN_S = 20;         // elevated must persist 20 s (hr_test.py)
const MIN_DWELL_S = 60;       // guardrail: no audio change more often than once a minute
const ELEVATED_MUSIC_S = 180; // still elevated after 3 min of music -> try silence (Bernardi 2006)
const SILENCE_PAUSE_S = 120;  // Bernardi's pause was 2 min
const SESSION_CAP_S = 1800;   // ~30 min sessions (Zhang 2026)
const WINDOW_S = 600;         // chart shows the last 10 minutes
const MUSIC_VOLUME = 0.6;

const $ = (id) => document.getElementById(id);
const S = {
  sessions: [], library: [], evidence: null,
  source: "recorded", pid: null,
  hr: [], smooth: [], segments: [], baseline: NaN,
  t: 0, running: false, timer: null, speed: 20, thr: 10,
  approved: new Set(),
  stop: false, nurse: false, audioOn: true,
  // agent state
  elevated: false, aboveRun: 0, belowRun: 0,
  playing: null, musicS: 0, elevatedMusicS: 0, pauseUntil: -1, pauseReason: null,
  lastChange: -Infinity, recent: [],
  sort: { k: "bpm", dir: 1 },
};

// ---------- data ----------
async function loadJSON(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

async function init() {
  const [sessions, evidence] = await Promise.all([loadJSON("data/sessions.json"), loadJSON("data/evidence.json")]);
  S.sessions = sessions.sessions;
  S.sessionMeta = sessions;
  S.evidence = evidence;
  try {
    S.library = (await loadJSON("data/music_library.json")).tracks;
  } catch (e) {
    S.library = [];
    $("calmNote").textContent = "Music library not built yet: run analysis/build_music_library.py.";
  }
  for (const s of S.sessions) $("pidSel").add(new Option(`${s.participant_id} (${Math.round(s.hr.length / 60)} min, ${s.segments.length} songs)`, s.participant_id));
  S.pid = S.sessions[0]?.participant_id;
  // Patient approval defaults to the whole calm list; the operator/patient can untick tracks.
  for (const t of calmList()) S.approved.add(t.song_id);
  bindUI();
  renderEvidence();
  loadStream();
  renderLibrary();
}

function simulatedStream() {
  // Clearly labeled synthetic stream: resting, a stress-like rise, slow recovery, a smaller rise.
  let seed = 7;
  const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647 - 0.5);
  const out = [];
  for (let t = 0; t < 1500; t++) {
    let lift = 0;
    if (t >= 300 && t < 360) lift = 18 * (t - 300) / 60;
    else if (t >= 360 && t < 600) lift = 18;
    else if (t >= 600) lift = 18 * Math.exp(-(t - 600) / 150);
    if (t >= 1050 && t < 1110) lift += 13 * (t - 1050) / 60;
    else if (t >= 1110) lift += 13 * Math.exp(-(t - 1110) / 120);
    out.push(+(72 + lift + rnd() * 2.4 + Math.sin(t / 9) * 0.8).toFixed(2));
  }
  return out;
}

function median(a) {
  const s = [...a].sort((x, y) => x - y);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

function loadStream() {
  stopReplay();
  if (S.source === "sim") {
    S.hr = simulatedStream();
    S.segments = [];
  } else {
    const s = S.sessions.find((x) => x.participant_id === S.pid);
    S.hr = s ? s.hr : [];
    S.segments = s ? s.segments : [];
  }
  S.smooth = S.hr.map((_, i) => median(S.hr.slice(Math.max(0, i - SMOOTH_S + 1), i + 1)));
  S.baseline = median(S.hr.slice(0, BASELINE_S));
  Object.assign(S, { t: 0, elevated: false, aboveRun: 0, belowRun: 0, musicS: 0, elevatedMusicS: 0,
    pauseUntil: -1, pauseReason: null, lastChange: -Infinity, recent: [] });
  setAudio(null, "Silence");
  const sim = S.source === "sim";
  $("pidWrap").hidden = sim;
  $("sourceBadge").textContent = sim ? "SIMULATED input" : "Recorded replay · HKU956";
  $("sourceBadge").className = "badge" + (sim ? " sim" : "");
  $("sourceNote").textContent = sim
    ? "Synthetic heart rate for demonstrating triggers. It does not respond to the audio."
    : `${S.sessionMeta.source}. ${S.sessionMeta.note} Dashed ticks on the chart mark song boundaries.`;
  $("playBtn").textContent = "▶ Start replay";
  render();
}

// ---------- replay loop ----------
function startReplay() {
  if (S.running) return pauseReplay();
  S.running = true;
  $("playBtn").textContent = "❚❚ Pause replay";
  S.timer = setInterval(step, 1000 / S.speed);
}
function pauseReplay() {
  S.running = false;
  clearInterval(S.timer);
  $("playBtn").textContent = "▶ Resume replay";
}
function stopReplay() {
  S.running = false;
  clearInterval(S.timer);
}

function step() {
  if (S.t >= S.hr.length - 1) {
    pauseReplay();
    $("playBtn").textContent = "▶ Replay ended";
    return;
  }
  S.t++;
  updateElevated();
  if (S.playing) {
    S.musicS++;
    S.elevatedMusicS = S.elevated ? S.elevatedMusicS + 1 : 0;
  } else {
    S.elevatedMusicS = 0;
  }
  act(decide());
  render();
}

function updateElevated() {
  const sm = S.smooth[S.t];
  if (sm >= S.baseline + S.thr) { S.aboveRun++; S.belowRun = 0; }
  else if (sm < S.baseline + S.thr / 2) { S.belowRun++; S.aboveRun = 0; }
  else { S.aboveRun = 0; S.belowRun = 0; }
  if (!S.elevated && S.aboveRun >= SUSTAIN_S) S.elevated = true;
  // Hysteresis: clear only after 20 s back below half the threshold.
  if (S.elevated && S.belowRun >= SUSTAIN_S) S.elevated = false;
}

// ---------- agent ----------
function isCalm(t) {
  const lo = +$("bpmMin").value, hi = +$("bpmMax").value;
  if (!t.bpm_reliable) return false;
  if (t.bpm >= lo && t.bpm <= hi) return true;
  return $("halfTime").checked && t.bpm / 2 >= lo && t.bpm / 2 <= hi;
}
function calmList() { return S.library.filter(isCalm); }

let zStats = null;
function z(t, k) {
  if (!zStats) {
    zStats = {};
    for (const key of ["onsets_per_s", "loudness_dbfs", "dynamics_db", "treble_share", "brightness_hz"]) {
      const v = S.library.map((x) => x[key]).filter(Number.isFinite);
      const m = v.reduce((a, b) => a + b, 0) / v.length;
      const sd = Math.sqrt(v.reduce((a, b) => a + (b - m) ** 2, 0) / v.length) || 1;
      zStats[key] = { m, sd };
    }
  }
  return (t[k] - zStats[k].m) / zStats[k].sd;
}

function effectiveBpm(t) {
  const lo = +$("bpmMin").value, hi = +$("bpmMax").value;
  return t.bpm >= lo && t.bpm <= hi ? t.bpm : t.bpm / 2;
}

function scoreTrack(t) {
  const lo = +$("bpmMin").value, hi = +$("bpmMax").value;
  const tempo = 1 - (effectiveBpm(t) - lo) / Math.max(hi - lo, 1); // slower within band = better
  const texture = -(z(t, "onsets_per_s") + z(t, "loudness_dbfs") + z(t, "dynamics_db") +
    z(t, "treble_share") + z(t, "brightness_hz")) / 5;
  const observed = t.hku_listeners >= 2 && t.hku_hr_delta_mean != null ? -t.hku_hr_delta_mean / 4 : 0;
  const parts = { tempo: tempo * 1.0, texture: texture * 0.6, observed: observed * 0.4 };
  return { total: parts.tempo + parts.texture + parts.observed, parts };
}

function candidates() {
  return S.library
    .filter((t) => S.approved.has(t.song_id) && isCalm(t) && !S.recent.includes(t.song_id))
    .map((t) => ({ t, s: scoreTrack(t) }))
    .sort((a, b) => b.s.total - a.s.total);
}

// Returns the decision plus the rule trace shown to the operator.
function decide() {
  const trace = [];
  const add = (id, status, detail) => trace.push({ id, status, detail });
  const canChange = S.t - S.lastChange >= MIN_DWELL_S;
  const delta = S.smooth[S.t] - S.baseline;

  add("stop", S.stop ? "fired" : "pass", S.stop ? "Patient said stop: silent until re-enabled." : "No stop request.");
  if (S.stop) return { action: "silence", why: "Patient said “stop”.", trace, force: true };

  add("care_present", S.nurse ? "fired" : "pass", S.nurse ? "Nurse present: audio faded and paused." : "No clinician present.");
  if (S.nurse) return { action: "pause", why: "Nurse present: audio paused.", trace, force: true };

  add("context_not_diagnosis", "na", `HR ${delta >= 0 ? "+" : ""}${delta.toFixed(1)} bpm vs baseline is treated as physiological change only. Movement filter unavailable for this source.`);

  if (S.t < S.pauseUntil) {
    add(S.pauseReason, "fired", `Silent pause, ${S.pauseUntil - S.t} s left.`);
    return { action: "silence", why: S.pauseReason === "session_cap" ? "30-minute session cap: silent pause." : "Still elevated during music: trying a silent pause.", trace };
  }

  if (S.musicS >= SESSION_CAP_S) {
    add("session_cap", "fired", "About 30 min of music: pausing to re-evaluate.");
    return { action: "start_pause", reason: "session_cap", why: "30-minute session cap reached.", trace, force: true };
  }
  add("session_cap", "pass", `${Math.round(S.musicS / 60)} of ~30 min music used.`);

  if (S.playing && S.elevated && S.elevatedMusicS >= ELEVATED_MUSIC_S && canChange) {
    add("silence_option", "fired", "Elevated for 3+ min despite calm music: silence lowered HR most in Bernardi 2006.");
    return { action: "start_pause", reason: "silence_option", why: "Still elevated after 3 min of calm music: trying silence.", trace };
  }
  add("silence_option", "pass", "Silence stays available as an option.");

  const cands = candidates();
  add("approved_only", cands.length ? "pass" : "fired", `${S.approved.size} approved tracks; ${cands.length} eligible now.`);
  add("tempo_calm", "pass", `Calm list: reliable tempo ${$("bpmMin").value}-${$("bpmMax").value} bpm${$("halfTime").checked ? " (half-time readings counted)" : ""}.`);
  add("no_fast", "pass", "Fast/high-energy tracks are excluded from the calm list.");
  add("small_effects", "na", "Success = trend back toward baseline, not a target number.");
  add("tiebreak_calm_texture", "na", "Used only to rank within the calm list.");
  add("tiebreak_observed", "na", "Used only to rank within the calm list.");

  if (!S.elevated) {
    if (S.playing) return { action: "hold", why: "Near baseline: keep the current calm track.", trace, cands };
    return { action: "silence", why: "Near baseline: room stays quiet.", trace, cands };
  }
  if (S.playing) return { action: "hold", why: "Elevated: calm track playing; waiting for a trend back toward baseline.", trace, cands };
  if (!cands.length) return { action: "silence", why: "Elevated, but no approved calm track is available: silence.", trace, cands };
  if (!canChange) return { action: "hold", why: "Elevated: waiting for the 60 s minimum between changes.", trace, cands };
  return { action: "play", track: cands[0].t, why: `Elevated ${S.elevated ? "for 20+ s" : ""}: starting the top-ranked approved calm track.`, trace, cands };
}

function act(d) {
  S.decision = d;
  if (d.action === "silence" && S.playing && (d.force || S.t - S.lastChange >= 0)) {
    setAudio(null, d.why);
  } else if (d.action === "pause" && S.playing) {
    setAudio(null, d.why);
  } else if (d.action === "start_pause") {
    S.pauseUntil = S.t + SILENCE_PAUSE_S;
    S.pauseReason = d.reason;
    if (d.reason === "session_cap") S.musicS = 0;
    setAudio(null, d.why);
  } else if (d.action === "play") {
    setAudio(d.track, d.why);
  }
}

// ---------- audio ----------
const player = () => $("player");
let fadeTimer = null;
function fadeTo(target, ms) {
  return new Promise((resolve) => {
    clearInterval(fadeTimer);
    const p = player();
    const start = p.volume, steps = Math.max(1, Math.round(ms / 50));
    let i = 0;
    fadeTimer = setInterval(() => {
      i++;
      p.volume = Math.min(1, Math.max(0, start + (target - start) * (i / steps)));
      if (i >= steps) { clearInterval(fadeTimer); resolve(); }
    }, 50);
  });
}

async function setAudio(track, why) {
  const changed = (S.playing?.song_id || null) !== (track?.song_id || null);
  if (!changed) return;
  S.lastChange = S.t;
  S.playing = track;
  if (track) {
    S.recent = [track.song_id, ...S.recent].slice(0, 3);
    S.musicS = S.musicS || 0;
  }
  const p = player();
  if (!p.paused) await fadeTo(0, 2500);
  p.pause();
  if (track && S.audioOn) {
    p.src = track.stream_url;
    p.volume = 0;
    try { await p.play(); fadeTo(MUSIC_VOLUME, 5000); } catch (e) { console.warn("audio blocked", e); }
  }
  render();
}

// ---------- rendering ----------
function render() {
  const sm = S.smooth[S.t], base = S.baseline;
  $("hrNow").textContent = Number.isFinite(sm) ? `${sm.toFixed(0)} bpm` : "–";
  $("hrBase").textContent = Number.isFinite(base) ? `${base.toFixed(0)} bpm` : "–";
  $("hrDelta").textContent = Number.isFinite(sm) ? `${sm - base >= 0 ? "+" : ""}${(sm - base).toFixed(1)}` : "–";
  $("hrState").textContent = S.elevated ? "⚠ Elevated" : "Near baseline";
  $("thrVal").textContent = S.thr;

  const d = S.decision;
  const box = $("decision");
  if (d) {
    const label = { silence: "Silence", pause: "Paused (nurse present)", hold: S.playing ? "Keep playing" : "Stay quiet",
      play: "Play calm track", start_pause: "Silent pause" }[d.action];
    $("decAction").textContent = d.action === "play" ? `${label}: ${trackName(d.track)}` : label;
    $("decDetail").textContent = d.why;
    box.className = "decision" + (d.action === "silence" || d.action === "start_pause" || d.action === "pause" ? " silence" : "") + (S.stop ? " alert" : "");
    renderRules(d.trace);
    renderCandidates(d.cands || candidates());
  }
  $("nowTrack").textContent = S.playing ? `${trackName(S.playing)} · ${S.playing.bpm} bpm · ${S.playing.key} ${S.playing.mode}` : "silence";
  $("sessionClock").textContent = `· replay ${fmt(S.t)} / ${fmt(S.hr.length)} · music ${fmt(S.musicS)}`;
  drawChart();
  highlightPlaying();
}

const fmt = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
const trackName = (t) => (t.title ? `${t.title} — ${t.artist}` : `Jamendo #${t.song_id}`);

function renderRules(trace) {
  const ev = S.evidence;
  $("ruleList").innerHTML = trace.map(({ id, status, detail }) => {
    const rule = ev.rules.find((r) => r.id === id) || { text: id, kind: "", sources: [] };
    const chips = rule.sources.map((s) => `<a class="chip" href="#src-${s}" title="${esc(ev.sources[s].cite)}">${esc(shortCite(s))}</a>`).join("");
    const st = { pass: "✓ ok", fired: "● active", na: "– info" }[status];
    return `<li><span class="st ${status}">${st}</span><span><span class="kind">${esc(rule.kind)}</span> ${esc(rule.text)}${chips}<br><span class="muted">${esc(detail)}</span></span></li>`;
  }).join("");
}
function shortCite(id) { return S.evidence.sources[id].cite.split("(")[0].split(",")[0].trim() + " " + (S.evidence.sources[id].cite.match(/\((\d{4})\)/)?.[1] || ""); }

function renderCandidates(cands) {
  $("candBody").innerHTML = cands.slice(0, 3).map(({ t, s }) => `<tr>
    <td>${esc(trackName(t))}</td><td class="num">${t.bpm}</td><td>${t.key} ${t.mode}</td>
    <td class="num">${s.total.toFixed(2)}</td>
    <td class="muted">tempo ${s.parts.tempo.toFixed(2)} · texture ${s.parts.texture.toFixed(2)} · observed ${s.parts.observed.toFixed(2)}</td></tr>`).join("")
    || `<tr><td colspan="5" class="muted">No approved calm tracks. Widen the BPM band or approve tracks below.</td></tr>`;
}

function renderLibrary() {
  zStats = null;
  const q = $("search").value.toLowerCase(), mode = $("modeSel").value, calmOnly = $("calmOnly").checked;
  const rows = S.library.filter((t) =>
    (!calmOnly || isCalm(t)) && (!mode || t.mode === mode) &&
    (!q || `${t.title} ${t.artist}`.toLowerCase().includes(q)));
  const { k, dir } = S.sort;
  rows.sort((a, b) => (a[k] > b[k] ? 1 : a[k] < b[k] ? -1 : 0) * dir);
  const calm = calmList().length;
  $("libCount").textContent = `${S.library.length} tracks · ${calm} on the calm list · ${S.approved.size} approved`;
  $("calmNote").textContent = `Calm list (evidence: Bernardi 2006): reliable tempo between ${$("bpmMin").value} and ${$("bpmMax").value} bpm. Tempo is estimated from the first 90 s and may read at double or half time; “count half-time readings” lets 110-140 bpm readings qualify as 55-70.`;
  S.shown = rows;
  $("libBody").innerHTML = rows.slice(0, 400).map((t) => `<tr data-id="${t.song_id}">
    <td><input type="checkbox" class="appr" ${S.approved.has(t.song_id) ? "checked" : ""} aria-label="approve"></td>
    <td><a href="${esc(t.page_url)}" target="_blank" rel="noopener">${esc(trackName(t))}</a></td>
    <td class="num">${t.bpm}${t.bpm_reliable ? "" : ' <span class="flag" title="tempo unsteady">?</span>'}</td>
    <td>${t.key} ${t.mode}</td>
    <td class="num">${t.loudness_dbfs}</td>
    <td class="num">${t.dynamics_db}</td>
    <td class="num">${Math.round(t.brightness_hz)}</td>
    <td class="num">${pct(t.bass_share)} / ${pct(t.mid_share)} / ${pct(t.treble_share)}</td>
    <td class="num">${t.onsets_per_s}</td>
    <td class="num">${t.hku_hr_delta_mean == null ? "–" : `${t.hku_hr_delta_mean > 0 ? "+" : ""}${t.hku_hr_delta_mean} (${t.hku_listeners})`}</td>
    <td>${t.license_url ? `<a href="${esc(t.license_url)}" target="_blank" rel="noopener">${esc(t.license)}</a>` : '<span class="muted">unknown</span>'}</td>
    <td><button class="prev">▶</button></td></tr>`).join("");
  highlightPlaying();
  if (S.decision) renderCandidates(candidates());
}
const pct = (x) => `${Math.round(x * 100)}%`;

function highlightPlaying() {
  for (const tr of $("libBody").querySelectorAll("tr")) tr.classList.toggle("playing", tr.dataset.id === S.playing?.song_id);
}

function renderEvidence() {
  const ev = S.evidence;
  $("evSources").innerHTML = Object.entries(ev.sources).map(([id, s]) => `<div class="source" id="src-${id}">
    <h4><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.cite)}</a></h4>
    <div class="muted">${esc(s.design)} · ${s.verified ? "checked against original" : "not checked against original"}</div>
    <ul>${s.findings.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>
    <div class="cav">Caveat: ${esc(s.caveat)}</div></div>`).join("") + `<p class="note">${esc(ev.note)}</p>`;
}

function esc(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]); }

// ---------- chart ----------
function cssVar(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }

function chartGeom() {
  const c = $("chart");
  const w = c.clientWidth, h = 220;
  const t1 = S.t, t0 = Math.max(0, t1 - WINDOW_S);
  const span = Math.max(WINDOW_S, 1);
  const vals = S.hr.slice(t0, t1 + 1);
  const lo = Math.min(S.baseline - 12, ...vals), hi = Math.max(S.baseline + S.thr + 12, ...vals);
  const pad = { l: 36, r: 8, t: 8, b: 20 };
  const x = (t) => pad.l + ((t - t0) / span) * (w - pad.l - pad.r);
  const y = (v) => pad.t + (1 - (v - lo) / (hi - lo)) * (h - pad.t - pad.b);
  return { c, w, h, t0, t1, lo, hi, pad, x, y, span };
}

function drawChart() {
  const g = chartGeom();
  const { c, w, h, t0, t1, x, y, pad } = g;
  const dpr = window.devicePixelRatio || 1;
  c.width = w * dpr; c.height = h * dpr;
  const ctx = c.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);
  ctx.font = "11px system-ui, sans-serif";

  // grid + y labels
  ctx.strokeStyle = cssVar("--border"); ctx.fillStyle = cssVar("--text-muted"); ctx.lineWidth = 1;
  const step = g.hi - g.lo > 40 ? 10 : 5;
  for (let v = Math.ceil(g.lo / step) * step; v <= g.hi; v += step) {
    ctx.beginPath(); ctx.moveTo(pad.l, y(v)); ctx.lineTo(w - pad.r, y(v)); ctx.stroke();
    ctx.fillText(String(v), 4, y(v) + 4);
  }
  ctx.fillText(`${fmt(t0)}`, pad.l, h - 4);
  ctx.textAlign = "right"; ctx.fillText(`${fmt(t1)}`, x(t1), h - 4); ctx.textAlign = "left";

  // elevated bands (recomputed over the window)
  ctx.fillStyle = cssVar("--warn-bg");
  let above = 0, below = 0, el = false, bandStart = null;
  for (let t = 0; t <= t1; t++) {
    const sm = S.smooth[t];
    if (sm >= S.baseline + S.thr) { above++; below = 0; } else if (sm < S.baseline + S.thr / 2) { below++; above = 0; } else { above = below = 0; }
    if (!el && above >= SUSTAIN_S) { el = true; bandStart = t; }
    if (el && below >= SUSTAIN_S) { el = false; if (t >= t0) ctx.fillRect(x(Math.max(bandStart, t0)), pad.t, x(t) - x(Math.max(bandStart, t0)), h - pad.t - pad.b); }
  }
  if (el) ctx.fillRect(x(Math.max(bandStart, t0)), pad.t, x(t1) - x(Math.max(bandStart, t0)), h - pad.t - pad.b);

  // song boundaries
  ctx.strokeStyle = cssVar("--border"); ctx.setLineDash([2, 3]);
  for (const s of S.segments) if (s.start > t0 && s.start <= t1) { ctx.beginPath(); ctx.moveTo(x(s.start), pad.t); ctx.lineTo(x(s.start), h - pad.b); ctx.stroke(); }

  // baseline and threshold
  ctx.lineWidth = 1.5;
  ctx.strokeStyle = cssVar("--ref"); ctx.setLineDash([6, 4]);
  ctx.beginPath(); ctx.moveTo(pad.l, y(S.baseline)); ctx.lineTo(w - pad.r, y(S.baseline)); ctx.stroke();
  ctx.strokeStyle = cssVar("--text-secondary"); ctx.setLineDash([2, 3]);
  ctx.beginPath(); ctx.moveTo(pad.l, y(S.baseline + S.thr)); ctx.lineTo(w - pad.r, y(S.baseline + S.thr)); ctx.stroke();
  ctx.setLineDash([]);

  // HR line (smoothed)
  ctx.strokeStyle = cssVar("--series-1"); ctx.lineWidth = 2; ctx.lineJoin = "round";
  ctx.beginPath();
  for (let t = t0; t <= t1; t++) { const px = x(t), py = y(S.smooth[t]); t === t0 ? ctx.moveTo(px, py) : ctx.lineTo(px, py); }
  ctx.stroke();

  if (hoverT != null && hoverT >= t0 && hoverT <= t1) {
    ctx.strokeStyle = cssVar("--text-muted"); ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x(hoverT), pad.t); ctx.lineTo(x(hoverT), h - pad.b); ctx.stroke();
    ctx.fillStyle = cssVar("--series-1");
    ctx.beginPath(); ctx.arc(x(hoverT), y(S.smooth[hoverT]), 4, 0, Math.PI * 2); ctx.fill();
  }
}

let hoverT = null;
function onHover(ev) {
  const g = chartGeom();
  const rect = g.c.getBoundingClientRect();
  const px = ev.clientX - rect.left;
  const t = Math.round(g.t0 + ((px - g.pad.l) / (g.w - g.pad.l - g.pad.r)) * g.span);
  const tip = $("tip");
  if (t < g.t0 || t > g.t1 || !S.hr.length) { hoverT = null; tip.hidden = true; drawChart(); return; }
  hoverT = t;
  const seg = S.segments.find((s) => t >= s.start && t < s.start + s.n);
  tip.innerHTML = `<b>${fmt(t)}</b><br>HR ${S.hr[t].toFixed(0)} bpm (median ${S.smooth[t].toFixed(1)})<br>${(S.smooth[t] - S.baseline >= 0 ? "+" : "") + (S.smooth[t] - S.baseline).toFixed(1)} vs baseline${seg ? `<br>listening to song #${seg.song_no} (Jamendo ${seg.song_id})` : ""}`;
  tip.hidden = false;
  tip.style.left = `${Math.min(px + 12, g.w - tip.offsetWidth - 4)}px`;
  tip.style.top = "8px";
  drawChart();
}

// ---------- UI wiring ----------
function bindUI() {
  $("sourceSel").onchange = (e) => { S.source = e.target.value; loadStream(); };
  $("pidSel").onchange = (e) => { S.pid = e.target.value; loadStream(); };
  $("playBtn").onclick = () => { if (S.t >= S.hr.length - 1) loadStream(); startReplay(); };
  $("resetBtn").onclick = () => loadStream();
  $("speedSel").onchange = (e) => { S.speed = +e.target.value; if (S.running) { clearInterval(S.timer); S.timer = setInterval(step, 1000 / S.speed); } };
  $("thrIn").oninput = (e) => { S.thr = +e.target.value; render(); };
  $("stopBtn").onclick = () => {
    S.stop = !S.stop;
    $("stopBtn").classList.toggle("on", S.stop);
    $("stopBtn").textContent = S.stop ? "Stopped: tap to re-enable audio" : "Patient: “Stop”";
    act(decide()); render();
  };
  $("nurseBtn").onclick = () => {
    S.nurse = !S.nurse;
    $("nurseBtn").classList.toggle("on", S.nurse);
    $("nurseBtn").textContent = S.nurse ? "Nurse present: tap when they leave" : "Nurse present (simulated)";
    act(decide()); render();
  };
  $("audioBtn").onclick = () => {
    S.audioOn = !S.audioOn;
    $("audioBtn").textContent = S.audioOn ? "🔊 Audio on" : "🔇 Audio off";
    if (!S.audioOn) player().pause();
    else if (S.playing) { player().src = S.playing.stream_url; player().play().then(() => fadeTo(MUSIC_VOLUME, 2000)).catch(() => {}); }
  };
  for (const id of ["bpmMin", "bpmMax", "halfTime", "modeSel", "calmOnly", "search"]) $(id).addEventListener("input", renderLibrary);
  $("approveAll").onclick = () => { for (const t of S.shown) S.approved.add(t.song_id); renderLibrary(); };
  $("approveNone").onclick = () => { S.approved.clear(); renderLibrary(); };
  $("libBody").addEventListener("change", (e) => {
    if (!e.target.classList.contains("appr")) return;
    const id = e.target.closest("tr").dataset.id;
    e.target.checked ? S.approved.add(id) : S.approved.delete(id);
    renderLibrary();
  });
  $("libBody").addEventListener("click", (e) => {
    if (!e.target.classList.contains("prev")) return;
    const t = S.library.find((x) => x.song_id === e.target.closest("tr").dataset.id);
    const p = player();
    p.src = t.stream_url; p.volume = MUSIC_VOLUME; p.play().catch(() => {});
    $("nowTrack").textContent = `${trackName(t)} (operator preview)`;
  });
  document.querySelector("#libTable thead").addEventListener("click", (e) => {
    const k = e.target.dataset?.k;
    if (!k) return;
    S.sort = { k, dir: S.sort.k === k ? -S.sort.dir : 1 };
    renderLibrary();
  });
  $("evSources").addEventListener("click", () => {});
  document.addEventListener("click", (e) => {
    const a = e.target.closest("a.chip");
    if (!a) return;
    document.querySelectorAll(".source.target").forEach((n) => n.classList.remove("target"));
    document.querySelector(a.getAttribute("href"))?.classList.add("target");
  });
  $("chart").addEventListener("mousemove", onHover);
  $("chart").addEventListener("mouseleave", () => { hoverT = null; $("tip").hidden = true; drawChart(); });
  window.addEventListener("resize", drawChart);
}

init().catch((e) => { document.body.insertAdjacentHTML("afterbegin", `<p style="padding:16px;color:#d03b3b">Failed to load data: ${esc(e.message)}. Serve the app folder over HTTP (see README).</p>`); });
