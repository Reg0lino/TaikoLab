// TaikoLab Director's Console Frontend (with Numpad-0 Tap Studio + WebAudio Volume Mixers)

const NOTE_COLORS = {
  1: "#f14a3b", // Don
  2: "#2ca8e8", // Kat
  3: "#f14a3b", // Big Don
  4: "#2ca8e8", // Big Kat
  5: "#f5b935", // Drumroll
  6: "#f5b935", // Big Drumroll
};

const REGION_PALETTE = [
  "rgba(44, 168, 232, 0.16)",
  "rgba(241, 74, 59, 0.15)",
  "rgba(61, 220, 132, 0.15)",
  "rgba(245, 185, 53, 0.15)",
];

let state = {
  songId: null,
  data: null,
  selectedSectionIdx: 0,
  loopSection: false,
  lastPlayTime: 0,
  hitScreenItems: [],
  slotScreenItems: [],
  tapArmed: false,
  sessionTaps: [], // raw timestamps recorded during current Numpad-0 take
  punchStartBaseline: null,
};

let wavesurfer = null;
let regionsPlugin = null;
let audioCtx = null;
let hitMasterGain = null;

// --- WebAudio Synth Hitsounds with Boostable Gain & Limiter ---
function ensureAudioCtx() {
  if (!audioCtx) {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    audioCtx = new Ctx();
    const comp = audioCtx.createDynamicsCompressor();
    comp.threshold.value = -6;
    comp.ratio.value = 8;
    hitMasterGain = audioCtx.createGain();
    hitMasterGain.gain.value = parseFloat(document.getElementById("hitVol").value || "1.35");
    hitMasterGain.connect(comp).connect(audioCtx.destination);
  }
  if (audioCtx.state === "suspended") {
    audioCtx.resume();
  }
}

function playTaikoSound(noteCode) {
  if (!document.getElementById("chkHitsounds").checked) return;
  ensureAudioCtx();
  const now = audioCtx.currentTime;

  if (noteCode === 1 || noteCode === 3) {
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.type = "sine";
    osc.frequency.setValueAtTime(noteCode === 3 ? 130 : 150, now);
    osc.frequency.exponentialRampToValueAtTime(52, now + 0.065);
    gain.gain.setValueAtTime(noteCode === 3 ? 0.72 : 0.55, now);
    gain.gain.exponentialRampToValueAtTime(0.001, now + 0.085);
    osc.connect(gain).connect(hitMasterGain);
    osc.start(now);
    osc.stop(now + 0.09);
  } else if (noteCode === 2 || noteCode === 4) {
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.type = "triangle";
    osc.frequency.setValueAtTime(noteCode === 4 ? 1150 : 1380, now);
    osc.frequency.exponentialRampToValueAtTime(480, now + 0.035);
    gain.gain.setValueAtTime(noteCode === 4 ? 0.58 : 0.44, now);
    gain.gain.exponentialRampToValueAtTime(0.001, now + 0.048);
    osc.connect(gain).connect(hitMasterGain);
    osc.start(now);
    osc.stop(now + 0.055);
  }
}

function playTapTickSound() {
  ensureAudioCtx();
  const now = audioCtx.currentTime;
  const osc = audioCtx.createOscillator();
  const gain = audioCtx.createGain();
  osc.type = "square";
  osc.frequency.setValueAtTime(880, now);
  osc.frequency.exponentialRampToValueAtTime(440, now + 0.025);
  gain.gain.setValueAtTime(0.28, now);
  gain.gain.exponentialRampToValueAtTime(0.001, now + 0.035);
  osc.connect(gain).connect(hitMasterGain);
  osc.start(now);
  osc.stop(now + 0.04);
}

function showToast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.remove("hidden");
  setTimeout(() => el.classList.add("hidden"), 2800);
}

function fmtTime(sec) {
  const m = Math.floor(sec / 60);
  const s = (sec % 60).toFixed(2).padStart(5, "0");
  return `${m}:${s}`;
}

function getActiveCourse() {
  if (!state.data || !state.data.chart) return null;
  const diff = document.getElementById("diffSelect").value;
  return (
    state.data.chart.courses.find((c) => c.difficulty === diff) ||
    state.data.chart.courses[0] ||
    null
  );
}

function computeFlattenedEvents(course) {
  if (!course) return { notes: [], rolls: [] };
  const offsetDelta = (state.data.overrides.global_offset_ms || 0) / 1000.0;
  const notes = [];
  const rolls = [];
  let rollStart = null;

  for (const m of course.measures) {
    const start = m.start_sec + offsetDelta;
    const end = m.end_sec + offsetDelta;
    const dur = end - start;
    const L = m.notes.length;

    for (let s = 0; s < L; s++) {
      const code = m.notes[s];
      const t = start + (s / L) * dur;
      if (code === 5 || code === 6) {
        rollStart = { time: t, big: code === 6 };
      } else if (code === 8 && rollStart) {
        rolls.push({ start: rollStart.time, end: t, big: rollStart.big });
        rollStart = null;
      } else if (code >= 1 && code <= 4) {
        notes.push({ time: t, code, mIdx: m.measure_idx, sIdx: s, grid: L });
      }
    }
  }
  return { notes, rolls };
}

function updateTapStatusBadge() {
  const taps = state.data?.overrides?.custom_downbeats || [];
  const badge = document.getElementById("tapStatus");
  if (taps.length >= 2) {
    const durs = [];
    for (let i = 0; i < taps.length - 1; i++) {
      durs.push(taps[i + 1].time_sec - taps[i].time_sec);
    }
    const lastBpm = durs.length ? (240.0 / durs[durs.length - 1]).toFixed(1) : "--";
    badge.textContent = `Taps: ${taps.length} (Locked | Last ~${lastBpm} BPM)`;
    badge.style.borderColor = "#ffd06b";
  } else {
    badge.textContent = `Taps: ${taps.length} (Auto Grid)`;
    badge.style.borderColor = "#2a303d";
  }
}

function initWaveSurfer(audioUrl) {
  if (wavesurfer) {
    wavesurfer.destroy();
  }
  const RegionsCls = window.WaveSurfer.Regions || window.WaveSurfer.RegionsPlugin;
  const TimelineCls = window.WaveSurfer.Timeline || window.WaveSurfer.TimelinePlugin;

  regionsPlugin = RegionsCls.create();
  wavesurfer = WaveSurfer.create({
    container: "#waveform",
    waveColor: "#485468",
    progressColor: "#2ca8e8",
    cursorColor: "#3ddc84",
    height: 92,
    normalize: true,
    url: audioUrl,
    plugins: [
      regionsPlugin,
      TimelineCls.create({ container: "#waveTimeline" }),
    ],
  });

  wavesurfer.on("ready", () => {
    wavesurfer.setVolume(parseFloat(document.getElementById("musicVol").value || "0.55"));
    renderRegions();
  });

  regionsPlugin.on("region-clicked", (region, e) => {
    e.stopPropagation();
    if (region.id.startsWith("sec-")) {
      const idx = parseInt(region.id.replace("sec-", ""), 10);
      if (!Number.isNaN(idx)) {
        selectSection(idx);
        wavesurfer.setTime(region.start);
      }
    } else if (region.id.startsWith("tap-")) {
      wavesurfer.setTime(region.start);
    }
  });

  regionsPlugin.on("region-double-clicked", (region, e) => {
    if (region.id.startsWith("tap-")) {
      e.stopPropagation();
      const idx = parseInt(region.id.replace("tap-", ""), 10);
      if (!Number.isNaN(idx) && state.data?.overrides?.custom_downbeats) {
        state.data.overrides.custom_downbeats.splice(idx, 1);
        updateTapStatusBadge();
        rebuildChart(false);
        showToast("Deleted tapped downbeat marker");
      }
    }
  });

  regionsPlugin.on("region-updated", (region) => {
    if (region.id.startsWith("sec-")) {
      const idx = parseInt(region.id.replace("sec-", ""), 10);
      const secs = state.data?.overrides?.sections || [];
      if (!Number.isNaN(idx) && secs[idx]) {
        secs[idx].start_sec = Number(region.start.toFixed(3));
        secs[idx].end_sec = Number(region.end.toFixed(3));
        renderSectionList();
        if (idx === state.selectedSectionIdx) populateInspector();
      }
    } else if (region.id.startsWith("tap-")) {
      const idx = parseInt(region.id.replace("tap-", ""), 10);
      const taps = state.data?.overrides?.custom_downbeats || [];
      if (!Number.isNaN(idx) && taps[idx]) {
        taps[idx].time_sec = Number(region.start.toFixed(4));
        taps.sort((a, b) => a.time_sec - b.time_sec);
        rebuildChart(false);
      }
    }
  });
}

function renderRegions() {
  if (!regionsPlugin || !state.data) return;
  regionsPlugin.clearRegions();
  const secs = state.data.overrides.sections || [];
  secs.forEach((sec, idx) => {
    regionsPlugin.addRegion({
      id: `sec-${idx}`,
      start: sec.start_sec,
      end: sec.end_sec,
      color: REGION_PALETTE[idx % REGION_PALETTE.length],
      content: sec.label || `Part ${idx + 1}`,
      drag: false,
      resize: true,
    });
  });

  const taps = state.data.overrides.custom_downbeats || [];
  taps.forEach((tp, idx) => {
    regionsPlugin.addRegion({
      id: `tap-${idx}`,
      start: tp.time_sec,
      color: "rgba(255, 208, 107, 0.95)",
      drag: true,
      resize: false,
    });
  });
}

function renderSectionList() {
  const container = document.getElementById("sectionList");
  container.innerHTML = "";
  const secs = state.data?.overrides?.sections || [];
  secs.forEach((sec, idx) => {
    const div = document.createElement("div");
    div.className = "section-item" + (idx === state.selectedSectionIdx ? " active" : "");
    div.innerHTML = `
      <div>
        <div><strong>${sec.label || `Part ${idx + 1}`}</strong></div>
        <div class="sec-meta">${fmtTime(sec.start_sec)} &ndash; ${fmtTime(sec.end_sec)}</div>
      </div>
      <span class="badge">${sec.follow_mode}</span>
    `;
    div.onclick = () => {
      selectSection(idx);
      if (wavesurfer) wavesurfer.setTime(sec.start_sec);
    };
    container.appendChild(div);
  });
}

function selectSection(idx) {
  state.selectedSectionIdx = idx;
  renderSectionList();
  populateInspector();
}

function populateInspector() {
  const secs = state.data?.overrides?.sections || [];
  const sec = secs[state.selectedSectionIdx];
  if (!sec) return;
  document.getElementById("secRangeBadge").textContent = `${fmtTime(sec.start_sec)} - ${fmtTime(sec.end_sec)}`;
  document.getElementById("secLabel").value = sec.label || "";
  document.getElementById("secFollow").value = sec.follow_mode || "auto";
  document.getElementById("secShiftMs").value = sec.offset_shift_ms || 0;
  document.getElementById("secBpmMult").value = String(sec.bpm_multiplier || 1);
  document.getElementById("secMeter").value = sec.numerator && sec.denominator ? `${sec.numerator}/${sec.denominator}` : "auto";
  document.getElementById("secDrumSens").value = sec.drum_sensitivity || 1.0;
  document.getElementById("lblDrumSens").textContent = Number(sec.drum_sensitivity || 1.0).toFixed(2);
  document.getElementById("secVocalSens").value = sec.vocal_sensitivity || 1.0;
  document.getElementById("lblVocalSens").textContent = Number(sec.vocal_sensitivity || 1.0).toFixed(2);
  document.getElementById("secMaxNps").value = sec.max_nps || "";
  document.getElementById("secBigNotes").checked = sec.enable_big_notes !== false;
  document.getElementById("secFoldRolls").checked = sec.fold_blasts_to_rolls !== false;
}

function readInspectorIntoSelected() {
  const secs = state.data?.overrides?.sections || [];
  const sec = secs[state.selectedSectionIdx];
  if (!sec) return;
  sec.label = document.getElementById("secLabel").value;
  sec.follow_mode = document.getElementById("secFollow").value;
  sec.offset_shift_ms = parseFloat(document.getElementById("secShiftMs").value || "0");
  sec.bpm_multiplier = parseFloat(document.getElementById("secBpmMult").value || "1");
  const meterVal = document.getElementById("secMeter").value;
  if (meterVal === "auto") {
    sec.numerator = null;
    sec.denominator = null;
  } else {
    const [n, d] = meterVal.split("/").map(Number);
    sec.numerator = n;
    sec.denominator = d;
  }
  sec.drum_sensitivity = parseFloat(document.getElementById("secDrumSens").value || "1");
  sec.vocal_sensitivity = parseFloat(document.getElementById("secVocalSens").value || "1");
  const maxNpsStr = document.getElementById("secMaxNps").value.trim();
  sec.max_nps = maxNpsStr ? parseFloat(maxNpsStr) : null;
  sec.enable_big_notes = document.getElementById("secBigNotes").checked;
  sec.fold_blasts_to_rolls = document.getElementById("secFoldRolls").checked;
}

function renderDiagnostics() {
  const diff = document.getElementById("diffSelect").value;
  const d = (state.data?.diagnostics || []).find((x) => x.difficulty === diff);
  const bar = document.getElementById("diagBar");
  if (!d) { bar.innerHTML = ""; return; }
  bar.innerHTML = `
    <span><strong>${d.difficulty.toUpperCase()} ${d.stars}★</strong></span>
    <span>Notes: ${d.total_notes} (Don=${d.don_count}, Kat=${d.kat_count}, Big=${d.big_count}, Rolls=${d.roll_count})</span>
    <span>NPS: avg ${d.avg_nps} / peak ${d.peak_nps}</span>
    <span>BPM: ${d.min_bpm}–${d.max_bpm}</span>
    <span>Meters: ${d.meters.join(", ")}</span>
  `;
}

async function rebuildChart(exportToSim = false) {
  if (!state.data) return;
  state.data.overrides.preset = document.getElementById("presetSelect").value;
  state.data.overrides.global_offset_ms = parseFloat(document.getElementById("globalOffsetInput").value || "0");
  state.data.overrides.tap_latency_ms = parseFloat(document.getElementById("tapLatencyInput").value || "0");
  const endpoint = exportToSim ? "export" : "arrange";
  const res = await fetch(`/api/songs/${state.songId}/${endpoint}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(state.data.overrides),
  });
  if (!res.ok) {
    showToast("Error rebuilding chart");
    return;
  }
  state.data = await res.json();
  updateTapStatusBadge();
  renderSectionList();
  renderRegions();
  renderDiagnostics();
  if (exportToSim) {
    showToast(state.data.exported_path ? `Exported -> ${state.data.exported_path}` : "Saved .tja");
  }
}

// --- Numpad-0 Tap Recording Logic ---
function toggleTapArm() {
  state.tapArmed = !state.tapArmed;
  const btn = document.getElementById("btnArmTap");
  btn.classList.toggle("armed", state.tapArmed);
  btn.blur();

  if (state.tapArmed) {
    ensureAudioCtx();
    state.sessionTaps = [];
    state.punchStartBaseline = [...(state.data?.overrides?.custom_downbeats || [])];
    btn.textContent = "RECORDING [Tap Numpad 0] — Click to Finish";
    showToast("Tap Numpad 0 on Beat 1 of each bar!");
  } else {
    btn.textContent = "Record Downbeats [Numpad 0]";
    if (state.sessionTaps.length >= 2) {
      rebuildChart(false);
      showToast(`Applied ${state.sessionTaps.length} tapped downbeats!`);
    }
  }
}

function recordNumpadTap() {
  if (!wavesurfer || !state.data) return;
  const rawT = wavesurfer.getCurrentTime();
  const flash = document.getElementById("tapFlash");
  flash.classList.add("active");
  setTimeout(() => flash.classList.remove("active"), 130);
  playTapTickSound();

  // Apply tap_latency_ms immediately if desired, or keep raw and let Magnet-Snap shift it cleanly.
  // Applying tap_latency_ms live ensures visual markers already line up closely while tapping!
  const latSec = parseFloat(document.getElementById("tapLatencyInput").value || "0") / 1000.0;
  const t = Math.max(0.0, Number((rawT + latSec).toFixed(4)));

  if (state.sessionTaps.length > 0 && (t - state.sessionTaps[state.sessionTaps.length - 1]) < 0.30) {
    return; // debounce accidental bounce
  }
  state.sessionTaps.push(t);

  const punchIn = document.getElementById("chkPunchIn").checked;
  let merged = [];
  if (punchIn && state.punchStartBaseline && state.punchStartBaseline.length > 0) {
    const tMin = state.sessionTaps[0] - 0.15;
    const tMax = state.sessionTaps[state.sessionTaps.length - 1] + 0.15;
    merged = state.punchStartBaseline.filter((m) => m.time_sec < tMin || m.time_sec > tMax);
  }
  for (const st of state.sessionTaps) {
    merged.push({ time_sec: st, numerator: null, denominator: null, locked: true });
  }
  merged.sort((a, b) => a.time_sec - b.time_sec);
  state.data.overrides.custom_downbeats = merged;

  updateTapStatusBadge();
  if (regionsPlugin) {
    regionsPlugin.addRegion({
      id: `tap-live-${state.sessionTaps.length}`,
      start: t,
      color: "rgba(255, 208, 107, 0.95)",
      drag: false,
      resize: false,
    });
  }
}

async function loadSong(songId) {
  state.songId = songId;
  const res = await fetch(`/api/songs/${songId}`);
  state.data = await res.json();
  state.selectedSectionIdx = 0;

  document.getElementById("presetSelect").value = state.data.overrides.preset || "punk_gallop";
  document.getElementById("globalOffsetInput").value = state.data.overrides.global_offset_ms || 0;
  document.getElementById("tapLatencyInput").value = state.data.overrides.tap_latency_ms ?? -20;

  const stemSel = document.getElementById("stemSelect");
  stemSel.innerHTML = "";
  (state.data.analysis_summary.stems_available || ["mix"]).forEach((st) => {
    const opt = document.createElement("option");
    opt.value = st;
    opt.textContent = st === "mix" ? "Full Mix" : `Stem: ${st}`;
    stemSel.appendChild(opt);
  });

  updateTapStatusBadge();
  renderSectionList();
  populateInspector();
  renderDiagnostics();
  initWaveSurfer(`/api/songs/${songId}/audio?stem=mix`);
}

async function init() {
  const res = await fetch("/api/songs");
  const json = await res.json();
  const sel = document.getElementById("songSelect");
  sel.innerHTML = "";
  json.songs.forEach((s) => {
    const opt = document.createElement("option");
    opt.value = s.song_id;
    opt.textContent = s.song_id;
    sel.appendChild(opt);
  });
  if (json.songs.length > 0) {
    const preferred = json.songs.find((s) => s.song_id === "dumpweed")?.song_id || json.songs[0].song_id;
    sel.value = preferred;
    await loadSong(preferred);
  }
  requestAnimationFrame(drawLoop);
}

// --- 60fps Scrolling Taiko Playfield & WebAudio Hitsound Loop ---
function drawLoop() {
  const canvas = document.getElementById("taikoCanvas");
  const ctx = canvas.getContext("2d");
  const W = canvas.width;
  const H = canvas.height;
  const hitX = 110;
  const centerY = H / 2;
  const pxPerSec = parseFloat(document.getElementById("laneZoom").value || "340");

  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = "#161a22";
  ctx.fillRect(0, 26, W, H - 52);

  ctx.strokeStyle = state.tapArmed ? "#e12d39" : "#5a657d";
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.arc(hitX, centerY, 26, 0, Math.PI * 2);
  ctx.stroke();

  const course = getActiveCourse();
  const curT = wavesurfer ? wavesurfer.getCurrentTime() : 0;
  const dur = wavesurfer ? wavesurfer.getDuration() : 0;
  document.getElementById("timeReadout").textContent = `${fmtTime(curT)} / ${fmtTime(dur)}`;

  if (state.loopSection && wavesurfer && wavesurfer.isPlaying()) {
    const sec = state.data?.overrides?.sections?.[state.selectedSectionIdx];
    if (sec && curT >= sec.end_sec) {
      wavesurfer.setTime(sec.start_sec);
    }
  }

  state.hitScreenItems = [];
  state.slotScreenItems = [];

  if (course) {
    const { notes, rolls } = computeFlattenedEvents(course);
    const minVisibleT = curT - 0.35;
    const maxVisibleT = curT + (W - hitX) / pxPerSec + 0.3;

    if (wavesurfer && wavesurfer.isPlaying()) {
      const prevT = state.lastPlayTime;
      if (curT > prevT && (curT - prevT) < 0.15) {
        for (const n of notes) {
          if (n.time > prevT && n.time <= curT) {
            playTaikoSound(n.code);
          }
        }
      }
    }
    state.lastPlayTime = curT;

    const offsetDelta = (state.data.overrides.global_offset_ms || 0) / 1000.0;
    for (const m of course.measures) {
      const mStart = m.start_sec + offsetDelta;
      const mEnd = m.end_sec + offsetDelta;
      if (mEnd < minVisibleT || mStart > maxVisibleT) continue;

      const xBar = hitX + (mStart - curT) * pxPerSec;
      ctx.strokeStyle = (state.data.overrides.custom_downbeats?.length >= 2) ? "#8a6d29" : "#3a4254";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(xBar, 26);
      ctx.lineTo(xBar, H - 26);
      ctx.stroke();

      ctx.fillStyle = "#8b95a8";
      ctx.font = "11px monospace";
      ctx.fillText(`#${m.measure_idx} ${Math.round(m.bpm)}bpm ${m.numerator}/${m.denominator}`, xBar + 4, 18);

      const L = m.notes.length;
      const mDur = mEnd - mStart;
      for (let s = 0; s < L; s++) {
        const st = mStart + (s / L) * mDur;
        const sx = hitX + (st - curT) * pxPerSec;
        if (s > 0 && s % Math.max(1, Math.floor(L / 4)) === 0) {
          ctx.strokeStyle = "#242a36";
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(sx, 36);
          ctx.lineTo(sx, H - 36);
          ctx.stroke();
        }
        state.slotScreenItems.push({ x: sx, y: centerY, mIdx: m.measure_idx, sIdx: s, code: m.notes[s] });
      }
    }

    for (const r of rolls) {
      if (r.end < minVisibleT || r.start > maxVisibleT) continue;
      const x1 = hitX + (r.start - curT) * pxPerSec;
      const x2 = hitX + (r.end - curT) * pxPerSec;
      const rad = r.big ? 24 : 18;
      ctx.fillStyle = "rgba(245, 185, 53, 0.85)";
      ctx.fillRect(x1, centerY - rad, Math.max(8, x2 - x1), rad * 2);
    }

    for (let i = notes.length - 1; i >= 0; i--) {
      const n = notes[i];
      if (n.time < minVisibleT || n.time > maxVisibleT) continue;
      const x = hitX + (n.time - curT) * pxPerSec;
      const isBig = n.code === 3 || n.code === 4;
      const rad = isBig ? 24 : 17;

      ctx.fillStyle = NOTE_COLORS[n.code];
      ctx.beginPath();
      ctx.arc(x, centerY, rad, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 2.5;
      ctx.stroke();

      state.hitScreenItems.push({ x, y: centerY, r: rad + 6, mIdx: n.mIdx, sIdx: n.sIdx, code: n.code });
    }
  }

  requestAnimationFrame(drawLoop);
}

// --- Click-on-Lane Individual Note Editing ---
function handleCanvasClick(e, isRightClick) {
  if (!state.data) return;
  const canvas = document.getElementById("taikoCanvas");
  const rect = canvas.getBoundingClientRect();
  const cx = ((e.clientX - rect.left) / rect.width) * canvas.width;
  const cy = ((e.clientY - rect.top) / rect.height) * canvas.height;
  const diff = document.getElementById("diffSelect").value;

  if (!state.data.overrides.note_edits) state.data.overrides.note_edits = {};

  const hitNote = state.hitScreenItems.find((item) => Math.hypot(item.x - cx, item.y - cy) <= item.r);
  if (hitNote) {
    const key = `${diff}:${hitNote.mIdx}:${hitNote.sIdx}`;
    if (isRightClick) {
      state.data.overrides.note_edits[key] = 0;
    } else {
      const cycle = { 1: 2, 2: 3, 3: 4, 4: 0 };
      state.data.overrides.note_edits[key] = cycle[hitNote.code] ?? 0;
    }
    rebuildChart(false);
    return;
  }

  if (!isRightClick && Math.abs(cy - canvas.height / 2) < 42) {
    let best = null;
    let bestDist = 14;
    for (const s of state.slotScreenItems) {
      const d = Math.abs(s.x - cx);
      if (d < bestDist) {
        bestDist = d;
        best = s;
      }
    }
    if (best) {
      const key = `${diff}:${best.mIdx}:${best.sIdx}`;
      state.data.overrides.note_edits[key] = 1;
      rebuildChart(false);
    }
  }
}

// --- Event Listeners ---
document.getElementById("taikoCanvas").addEventListener("click", (e) => handleCanvasClick(e, false));
document.getElementById("taikoCanvas").addEventListener("contextmenu", (e) => {
  e.preventDefault();
  handleCanvasClick(e, true);
});

document.getElementById("songSelect").addEventListener("change", (e) => loadSong(e.target.value));
document.getElementById("stemSelect").addEventListener("change", (e) => {
  if (!wavesurfer || !state.songId) return;
  const t = wavesurfer.getCurrentTime();
  initWaveSurfer(`/api/songs/${state.songId}/audio?stem=${e.target.value}`);
  wavesurfer.once("ready", () => wavesurfer.setTime(t));
});
document.getElementById("presetSelect").addEventListener("change", () => rebuildChart(false));
document.getElementById("diffSelect").addEventListener("change", () => renderDiagnostics());
document.getElementById("speedSelect").addEventListener("change", (e) => {
  if (wavesurfer) wavesurfer.setPlaybackRate(parseFloat(e.target.value));
});

// Volume sliders
document.getElementById("musicVol").addEventListener("input", (e) => {
  const v = parseFloat(e.target.value);
  document.getElementById("lblMusicVol").textContent = `${Math.round(v * 100)}%`;
  if (wavesurfer) wavesurfer.setVolume(v);
});
document.getElementById("hitVol").addEventListener("input", (e) => {
  const v = parseFloat(e.target.value);
  document.getElementById("lblHitVol").textContent = `${Math.round(v * 100)}%`;
  if (hitMasterGain && audioCtx) {
    hitMasterGain.gain.setValueAtTime(v, audioCtx.currentTime);
  }
});

document.getElementById("btnPlay").addEventListener("click", () => {
  ensureAudioCtx();
  if (wavesurfer) wavesurfer.playPause();
});
document.getElementById("btnLoopSec").addEventListener("click", (e) => {
  state.loopSection = !state.loopSection;
  e.target.classList.toggle("btn-accent", state.loopSection);
});

window.addEventListener("keydown", (e) => {
  if (state.tapArmed && e.code === "Numpad0") {
    e.preventDefault();
    if (!e.repeat) recordNumpadTap();
    return;
  }
  if (e.code === "Space" && !["INPUT", "SELECT"].includes(document.activeElement.tagName)) {
    e.preventDefault();
    ensureAudioCtx();
    if (wavesurfer) wavesurfer.playPause();
  }
});

// Tap Studio Buttons
document.getElementById("btnArmTap").addEventListener("click", toggleTapArm);

document.getElementById("btnSeedTaps").addEventListener("click", () => {
  const course = getActiveCourse();
  if (!course || !course.measures.length) return;
  state.data.overrides.custom_downbeats = course.measures.map((m) => ({
    time_sec: Number(m.start_sec.toFixed(4)),
    numerator: null,
    denominator: null,
    locked: true,
  }));
  rebuildChart(false);
  showToast(`Seeded ${course.measures.length} downbeat markers from current grid!`);
});

// Auto-rebuild immediately when Section Inspector dropdowns/checkboxes change
["secFollow", "secBpmMult", "secMeter", "secBigNotes", "secFoldRolls"].forEach((id) => {
  document.getElementById(id).addEventListener("change", () => {
    readInspectorIntoSelected();
    // If changing section meter while custom_downbeats exist, clear per-tap overrides inside this section so section meter takes effect
    if (id === "secMeter" && state.data?.overrides?.custom_downbeats?.length) {
      const sec = state.data.overrides.sections[state.selectedSectionIdx];
      if (sec) {
        state.data.overrides.custom_downbeats.forEach((tp) => {
          if (tp.time_sec >= sec.start_sec - 0.05 && tp.time_sec < sec.end_sec) {
            tp.numerator = null;
            tp.denominator = null;
          }
        });
      }
    }
    rebuildChart(false);
  });
});

// Per-Bar Time Signature Switcher right at the Playhead
document.getElementById("barMeterSelect").addEventListener("change", (e) => {
  const val = e.target.value;
  if (!val || !wavesurfer || !state.data) return;
  const [n, d] = val.split("/").map(Number);
  const curT = wavesurfer.getCurrentTime();
  const course = getActiveCourse();
  if (!course || !course.measures.length) return;

  // Ensure custom_downbeats are seeded so individual bars can carry unique meters
  if (!state.data.overrides.custom_downbeats || state.data.overrides.custom_downbeats.length < 2) {
    state.data.overrides.custom_downbeats = course.measures.map((m) => ({
      time_sec: Number(m.start_sec.toFixed(4)),
      numerator: m.numerator,
      denominator: m.denominator,
      locked: true,
    }));
  }

  const taps = state.data.overrides.custom_downbeats;
  let targetIdx = taps.findIndex((tp, idx) => {
    const nextT = idx + 1 < taps.length ? taps[idx + 1].time_sec : 999999;
    return curT >= tp.time_sec - 0.05 && curT < nextT;
  });
  if (targetIdx < 0) targetIdx = 0;
  taps[targetIdx].numerator = n;
  taps[targetIdx].denominator = d;
  rebuildChart(false);
  showToast(`Set bar #${targetIdx} at ${fmtTime(curT)} to ${n}/${d}!`);
  e.target.value = "";
});

document.getElementById("btnSnapTaps").addEventListener("click", async () => {
  const taps = state.data?.overrides?.custom_downbeats || [];
  if (taps.length < 2) { showToast("Record or seed at least 2 taps first"); return; }
  const res = await fetch(`/api/songs/${state.songId}/taps/snap`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ taps, window_ms: 45.0, tap_latency_ms: 0.0 }),
  });
  const json = await res.json();
  state.data.overrides.custom_downbeats = json.taps;
  await rebuildChart(false);
  showToast(`Magnet-snapped ${json.snapped_count}/${taps.length} taps to exact audio hits!`);
});

document.getElementById("btnStraightenTaps").addEventListener("click", async () => {
  const taps = state.data?.overrides?.custom_downbeats || [];
  if (taps.length < 4) { showToast("Need at least 4 taps to straighten BPM"); return; }
  const res = await fetch(`/api/songs/${state.songId}/taps/straighten`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ taps, tolerance_pct: 5.5 }),
  });
  const json = await res.json();
  state.data.overrides.custom_downbeats = json.taps;
  await rebuildChart(false);
  showToast("Straightened steady sections to flat BPM!");
});

document.getElementById("btnFillGaps").addEventListener("click", async () => {
  const taps = state.data?.overrides?.custom_downbeats || [];
  if (taps.length < 3) return;
  const res = await fetch(`/api/songs/${state.songId}/taps/fill-gaps`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ taps }),
  });
  const json = await res.json();
  state.data.overrides.custom_downbeats = json.taps;
  await rebuildChart(false);
  showToast(`Filled ${json.added_count} skipped 2x bar gaps!`);
});

document.getElementById("btnClearTaps").addEventListener("click", () => {
  if (!state.data) return;
  state.data.overrides.custom_downbeats = [];
  state.sessionTaps = [];
  rebuildChart(false);
  showToast("Cleared custom taps — reverted to auto grid");
});

document.querySelectorAll(".nudge-global").forEach((btn) => {
  btn.addEventListener("click", () => {
    const inp = document.getElementById("globalOffsetInput");
    inp.value = parseFloat(inp.value || "0") + parseFloat(btn.dataset.ms);
    rebuildChart(false);
  });
});
document.getElementById("globalOffsetInput").addEventListener("change", () => rebuildChart(false));

document.querySelectorAll(".nudge-sec").forEach((btn) => {
  btn.addEventListener("click", () => {
    const inp = document.getElementById("secShiftMs");
    inp.value = parseFloat(inp.value || "0") + parseFloat(btn.dataset.ms);
    readInspectorIntoSelected();
    rebuildChart(false);
  });
});

document.getElementById("secDrumSens").addEventListener("input", (e) => {
  document.getElementById("lblDrumSens").textContent = Number(e.target.value).toFixed(2);
});
document.getElementById("secVocalSens").addEventListener("input", (e) => {
  document.getElementById("lblVocalSens").textContent = Number(e.target.value).toFixed(2);
});

document.getElementById("btnApplySection").addEventListener("click", () => {
  readInspectorIntoSelected();
  rebuildChart(false);
  showToast("Section rebuilt!");
});

document.getElementById("btnSplitHere").addEventListener("click", () => {
  if (!wavesurfer || !state.data) return;
  const t = Number(wavesurfer.getCurrentTime().toFixed(3));
  const secs = state.data.overrides.sections;
  const idx = secs.findIndex((s) => t > s.start_sec + 0.5 && t < s.end_sec - 0.5);
  if (idx < 0) {
    showToast("Move playhead inside a section (>0.5s from edges) to split");
    return;
  }
  const orig = secs[idx];
  const right = { ...orig, start_sec: t, label: `${orig.label || "Part"} (B)` };
  orig.end_sec = t;
  secs.splice(idx + 1, 0, right);
  selectSection(idx + 1);
  renderRegions();
  rebuildChart(false);
  showToast(`Split section at ${fmtTime(t)}`);
});

document.getElementById("btnDeleteSection").addEventListener("click", () => {
  const secs = state.data?.overrides?.sections || [];
  if (secs.length <= 1 || state.selectedSectionIdx <= 0) {
    showToast("Cannot merge the first section");
    return;
  }
  const cur = secs[state.selectedSectionIdx];
  secs[state.selectedSectionIdx - 1].end_sec = cur.end_sec;
  secs.splice(state.selectedSectionIdx, 1);
  selectSection(state.selectedSectionIdx - 1);
  rebuildChart(false);
});

document.getElementById("btnResetEdits").addEventListener("click", () => {
  if (!state.data) return;
  state.data.overrides.note_edits = {};
  rebuildChart(false);
  showToast("Manual note edits cleared");
});

document.getElementById("btnExport").addEventListener("click", () => rebuildChart(true));

init();