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
  importId: null,
  data: null,
  selectedSectionIdx: 0,
  loopSection: false,
  lastPlayTime: 0,
  hitScreenItems: [],
  slotScreenItems: [],
  tapArmed: false,
  sessionTaps: [], // raw timestamps recorded during current Numpad-0 take
  punchStartBaseline: null,
  referenceHits: [],
  hitScreenItems: [],
  rollScreenItems: [],
  measureScreenItems: [],
  selectedEdit: null,
  drag: null,
  editUndo: [],
  editRedo: [],
  suppressClick: false,
};

let wavesurfer = null;
let regionsPlugin = null;
let audioCtx = null;
let hitMasterGain = null;
let referenceMasterGain = null;
const MAX_EDIT_HISTORY = 100;

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
    referenceMasterGain = audioCtx.createGain();
    referenceMasterGain.gain.value = parseFloat(document.getElementById("referenceVol").value || "0.8");
    referenceMasterGain.connect(comp).connect(audioCtx.destination);
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

function playReferenceSound(drumClass) {
  if (!document.getElementById("chkReferenceClicks").checked) return;
  ensureAudioCtx();
  const now = audioCtx.currentTime;
  const isKick = drumClass === "kick";
  const osc = audioCtx.createOscillator();
  const gain = audioCtx.createGain();
  osc.type = isKick ? "sine" : "square";
  osc.frequency.setValueAtTime(isKick ? 220 : 1040, now);
  osc.frequency.exponentialRampToValueAtTime(isKick ? 75 : 520, now + 0.045);
  gain.gain.setValueAtTime(isKick ? 0.7 : 0.35, now);
  gain.gain.exponentialRampToValueAtTime(0.001, now + 0.055);
  osc.connect(gain).connect(referenceMasterGain);
  osc.start(now);
  osc.stop(now + 0.06);
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
      if (code === 5 || code === 6 || code === 7) {
        rollStart = {
          time: t,
          big: code === 6,
          balloon: code === 7,
          mIdx: m.measure_idx,
          sIdx: s,
          grid: L,
        };
      } else if (code === 8 && rollStart) {
        rolls.push({
          ...rollStart,
          end: t,
          endMIdx: m.measure_idx,
          endSIdx: s,
          endGrid: L,
        });
        rollStart = null;
      } else if (code >= 1 && code <= 4) {
        notes.push({
          time: t,
          code,
          mIdx: m.measure_idx,
          sIdx: s,
          grid: L,
          key: editKey(m.measure_idx, s, L),
        });
      }
    }
  }
  return { notes, rolls };
}

function cloneEditState() {
  return JSON.parse(JSON.stringify({
    note_edits: state.data?.overrides?.note_edits || {},
    balloon_counts_by_course: state.data?.overrides?.balloon_counts_by_course || {},
  }));
}

function saveUndoPoint() {
  state.editUndo.push(cloneEditState());
  if (state.editUndo.length > MAX_EDIT_HISTORY) state.editUndo.shift();
  state.editRedo = [];
  updateUndoButtons();
}

function updateUndoButtons() {
  document.getElementById("btnUndo").disabled = state.editUndo.length === 0;
  document.getElementById("btnRedo").disabled = state.editRedo.length === 0;
}

function restoreEditState(snapshot) {
  if (!state.data) return;
  state.data.overrides.note_edits = snapshot.note_edits || {};
  state.data.overrides.balloon_counts_by_course = snapshot.balloon_counts_by_course || {};
  state.selectedEdit = null;
  rebuildChart(false);
  updateUndoButtons();
}

function undoEdit() {
  if (!state.data || state.editUndo.length === 0) return;
  state.editRedo.push(cloneEditState());
  restoreEditState(state.editUndo.pop());
}

function redoEdit() {
  if (!state.data || state.editRedo.length === 0) return;
  state.editUndo.push(cloneEditState());
  restoreEditState(state.editRedo.pop());
}

function editKey(measureIndex, numerator, denominator) {
  let n = Math.max(0, Math.min(denominator - 1, Math.round(numerator)));
  let d = denominator;
  let a = n;
  let b = d;
  while (b !== 0) {
    const remainder = a % b;
    a = b;
    b = remainder;
  }
  const divisor = Math.max(1, a);
  n /= divisor;
  d /= divisor;
  return `${document.getElementById("diffSelect").value}:${measureIndex}:p${n}/${d}`;
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
  const url = state.importId
    ? `/api/imported/${state.importId}/${endpoint}`
    : `/api/songs/${state.songId}/${endpoint}`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(state.data.overrides),
  });
  if (!res.ok) {
    const body = await res.json();
    showToast(body.detail || "Could not rebuild the chart");
    return;
  }
  state.data = await res.json();
  updateTapStatusBadge();
  renderSectionList();
  renderRegions();
  renderDiagnostics();
  if (exportToSim && state.importId) {
    state.editUndo = [];
    state.editRedo = [];
  }
  updateUndoButtons();
  if (exportToSim) {
    const verb = state.importId ? "Saved" : "Exported";
    showToast(state.data.exported_path ? `${verb} -> ${state.data.exported_path}` : "Saved .tja");
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
  state.importId = null;
  document.body.classList.remove("imported-tja");
  document.getElementById("songSelect").disabled = false;
  document.getElementById("presetSelect").disabled = false;
  document.getElementById("btnExport").textContent = "Export to YataiDON";
  state.songId = songId;
  const res = await fetch(`/api/songs/${songId}`);
  if (!res.ok) {
    showToast(`Could not load song: ${songId}`);
    return;
  }
  state.data = await res.json();
  state.referenceHits = state.data.reference_hits || [];
  state.editUndo = [];
  state.editRedo = [];
  state.selectedEdit = null;
  updateUndoButtons();
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

async function loadImportedTja(importId) {
  state.importId = importId;
  state.songId = importId;
  document.body.classList.add("imported-tja");
  const response = await fetch(`/api/imported/${importId}`);
  if (!response.ok) {
    showToast(`Could not load imported TJA: ${importId}`);
    return;
  }
  state.data = await response.json();
  state.referenceHits = [];
  state.editUndo = [];
  state.editRedo = [];
  state.selectedEdit = null;
  state.selectedSectionIdx = 0;
  updateUndoButtons();

  const songSelect = document.getElementById("songSelect");
  songSelect.innerHTML = "";
  const option = document.createElement("option");
  option.value = importId;
  option.textContent = `${state.data.analysis_summary.title} (TJA)`;
  songSelect.appendChild(option);
  songSelect.value = importId;
  songSelect.disabled = true;
  document.getElementById("btnExport").textContent = "Save TJA";
  document.getElementById("presetSelect").disabled = true;
  document.getElementById("presetSelect").value = state.data.overrides.preset;
  document.getElementById("globalOffsetInput").value = state.data.overrides.global_offset_ms || 0;
  document.getElementById("tapLatencyInput").value = state.data.overrides.tap_latency_ms ?? 0;

  const difficultySelect = document.getElementById("diffSelect");
  const hasUra = state.data.chart.courses.some((course) => course.difficulty === "ura");
  let uraOption = [...difficultySelect.options].find((item) => item.value === "ura");
  if (hasUra && !uraOption) {
    uraOption = document.createElement("option");
    uraOption.value = "ura";
    uraOption.textContent = "Ura / Edit";
    difficultySelect.appendChild(uraOption);
  } else if (!hasUra && uraOption) {
    uraOption.remove();
  }
  difficultySelect.value = state.data.chart.courses[0]?.difficulty || "hard";

  const stemSelect = document.getElementById("stemSelect");
  stemSelect.innerHTML = "";
  const hasAudio = state.data.analysis_summary.stems_available.includes("mix");
  const mixOption = document.createElement("option");
  mixOption.value = "mix";
  mixOption.textContent = "Full Mix";
  stemSelect.appendChild(mixOption);
  stemSelect.disabled = !hasAudio;
  document.getElementById("btnPlay").disabled = !hasAudio;
  document.getElementById("btnLoopSec").disabled = !hasAudio;
  renderSectionList();
  populateInspector();
  renderDiagnostics();
  if (hasAudio) {
    initWaveSurfer(`/api/imported/${importId}/audio`);
  } else {
    showToast("Chart opened. Its WAVE audio file was not found beside the TJA.");
  }
}

async function init() {
  const importId = new URLSearchParams(window.location.search).get("import_id");
  if (importId) {
    await loadImportedTja(importId);
    requestAnimationFrame(drawLoop);
    return;
  }

  const res = await fetch("/api/songs");
  if (!res.ok) {
    showToast("Could not load workspace songs");
    return;
  }
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
    const requested = new URLSearchParams(window.location.search).get("song");
    const preferred = json.songs.find((s) => s.song_id === requested)?.song_id ||
      json.songs.find((s) => s.song_id === "dumpweed")?.song_id ||
      json.songs[0].song_id;
    sel.value = preferred;
    await loadSong(preferred);
  }
  requestAnimationFrame(drawLoop);
}

// --- 60fps Scrolling Taiko Playfield & WebAudio Hitsound Loop ---
function drawLoop() {
  const canvas = document.getElementById("taikoCanvas");
  const ctx = canvas.getContext("2d");
  const W = canvas.clientWidth;
  const H = canvas.clientHeight;
  const pixelRatio = window.devicePixelRatio || 1;
  const pixelWidth = Math.round(W * pixelRatio);
  const pixelHeight = Math.round(H * pixelRatio);
  if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
    canvas.width = pixelWidth;
    canvas.height = pixelHeight;
  }
  ctx.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
  const hitX = 110;
  const chartY = Math.round(H * 0.36);
  const ghostY = Math.round(H * 0.76);
  const pxPerSec = parseFloat(document.getElementById("laneZoom").value || "340");

  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = "#161a22";
  ctx.fillRect(0, 32, W, H - 48);
  ctx.fillStyle = "#121720";
  ctx.fillRect(0, ghostY - 28, W, H - ghostY + 4);
  ctx.strokeStyle = "#2a303d";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(0, ghostY - 40);
  ctx.lineTo(W, ghostY - 40);
  ctx.stroke();

  ctx.strokeStyle = state.tapArmed ? "#e12d39" : "#5a657d";
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.arc(hitX, chartY, 26, 0, Math.PI * 2);
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
  state.rollScreenItems = [];
  state.measureScreenItems = [];

  if (course) {
    const { notes, rolls } = computeFlattenedEvents(course);
    const minVisibleT = curT - 0.35;
    const maxVisibleT = curT + (W - hitX) / pxPerSec + 0.3;

    if (wavesurfer && wavesurfer.isPlaying()) {
      const prevT = state.lastPlayTime;
      if (curT > prevT && (curT - prevT) < 0.15) {
        for (const n of notes) {
          if (n.time > prevT && n.time <= curT && document.getElementById("chkHitsounds").checked) {
            playTaikoSound(n.code);
          }
        }
        if (document.getElementById("chkReferenceClicks").checked) {
          for (const hit of state.referenceHits) {
            if (hit.time_sec > prevT && hit.time_sec <= curT) {
              playReferenceSound(hit.drum_class);
            }
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
      const xEnd = hitX + (mEnd - curT) * pxPerSec;
      state.measureScreenItems.push({
        mIdx: m.measure_idx,
        x1: xBar,
        x2: xEnd,
        start: mStart,
        end: mEnd,
        grid: m.notes.length,
        numerator: m.numerator,
        denominator: m.denominator,
      });
      ctx.strokeStyle = (state.data.overrides.custom_downbeats?.length >= 2) ? "#8a6d29" : "#3a4254";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(xBar, 32);
      ctx.lineTo(xBar, H - 12);
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
          ctx.moveTo(sx, 42);
          ctx.lineTo(sx, ghostY - 38);
          ctx.stroke();
        }
        state.slotScreenItems.push({
          x: sx,
          y: chartY,
          mIdx: m.measure_idx,
          sIdx: s,
          grid: L,
          code: m.notes[s],
          key: editKey(m.measure_idx, s, L),
        });
      }
    }

    for (const r of rolls) {
      if (r.end < minVisibleT || r.start > maxVisibleT) continue;
      const x1 = hitX + (r.start - curT) * pxPerSec;
      const x2 = hitX + (r.end - curT) * pxPerSec;
      const color = r.balloon ? "#be68ec" : "#f5b935";
      ctx.fillStyle = color;
      ctx.globalAlpha = state.selectedEdit?.startKey === editKey(r.mIdx, r.sIdx, r.grid) ? 1 : 0.82;
      ctx.fillRect(x1, chartY - 14, Math.max(10, x2 - x1), 28);
      ctx.globalAlpha = 1;
      ctx.fillStyle = "#101319";
      ctx.font = "bold 11px sans-serif";
      ctx.fillText(r.balloon ? "BALLOON" : r.big ? "BIG ROLL" : "ROLL", x1 + 6, chartY + 4);
      const startKey = editKey(r.mIdx, r.sIdx, r.grid);
      const endKey = editKey(r.endMIdx, r.endSIdx, r.endGrid);
      state.rollScreenItems.push({
        x1,
        x2,
        y: chartY,
        startKey,
        endKey,
        roll: r,
        r: { startKey, endKey, balloon: r.balloon, big: r.big, start: r.start, end: r.end },
      });
    }

    for (let i = notes.length - 1; i >= 0; i--) {
      const n = notes[i];
      if (n.time < minVisibleT || n.time > maxVisibleT) continue;
      const x = hitX + (n.time - curT) * pxPerSec;
      const isBig = n.code === 3 || n.code === 4;
      const rad = isBig ? 20 : 15;

      ctx.fillStyle = NOTE_COLORS[n.code];
      ctx.beginPath();
      ctx.arc(x, chartY, rad, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 2.5;
      if (state.selectedEdit?.key === n.key) ctx.lineWidth = 4;
      ctx.stroke();

      state.hitScreenItems.push({
        x,
        y: chartY,
        r: rad + 7,
        mIdx: n.mIdx,
        sIdx: n.sIdx,
        grid: n.grid,
        code: n.code,
        key: n.key,
      });
    }

    ctx.fillStyle = "#b5c1d3";
    ctx.font = "11px sans-serif";
    ctx.fillText("PLAYABLE CHART", 12, 45);
    ctx.fillStyle = "#8895a8";
    ctx.fillText("MIDI GHOST REFERENCE", 12, ghostY - 30);
    for (const hit of state.referenceHits) {
      if (hit.time_sec < minVisibleT || hit.time_sec > maxVisibleT) continue;
      const x = hitX + (hit.time_sec - curT) * pxPerSec;
      const kick = hit.drum_class === "kick";
      const snare = hit.drum_class === "snare";
      ctx.fillStyle = kick ? "#72c8a4" : snare ? "#d29cff" : "#8792a3";
      ctx.globalAlpha = 0.72;
      ctx.beginPath();
      if (kick) {
        ctx.arc(x, ghostY, 6, 0, Math.PI * 2);
      } else {
        ctx.moveTo(x, ghostY - 7);
        ctx.lineTo(x + 6, ghostY);
        ctx.lineTo(x, ghostY + 7);
        ctx.lineTo(x - 6, ghostY);
        ctx.closePath();
      }
      ctx.fill();
      ctx.globalAlpha = 1;
    }
    if (state.referenceHits.length === 0) {
      ctx.fillStyle = "#707b8d";
      ctx.fillText("No aligned MIDI drum events are available for this song.", 190, ghostY + 4);
    }

    if (state.drag?.currentPoint) {
      const point = state.drag.currentPoint;
      const x = hitX + (point.time - curT) * pxPerSec;
      ctx.strokeStyle = "#ffffff";
      ctx.setLineDash([5, 4]);
      ctx.beginPath();
      ctx.moveTo(x, 42);
      ctx.lineTo(x, ghostY - 38);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }

  requestAnimationFrame(drawLoop);
}

function pointFromPointer(x) {
  const measures = state.measureScreenItems;
  if (measures.length === 0) return null;
  let measure = measures.find((item) => x >= item.x1 && x < item.x2);
  if (!measure) {
    measure = measures.reduce((best, item) => {
      const distance = x < item.x1 ? item.x1 - x : x - item.x2;
      const bestDistance = x < best.x1 ? best.x1 - x : x - best.x2;
      return distance < bestDistance ? item : best;
    });
  }
  const span = Math.max(1, measure.x2 - measure.x1);
  const position = Math.max(0, Math.min(0.999999, (x - measure.x1) / span));
  const snap = document.getElementById("snapToggle").checked;
  const gridValue = document.getElementById("editGrid").value;
  let subdivisions = 64;
  if (snap) {
    subdivisions = gridValue === "chart"
      ? measure.grid
      : Math.max(1, Math.round(measure.numerator * Number(gridValue) / measure.denominator));
  }
  const numerator = Math.max(0, Math.min(subdivisions - 1, Math.round(position * subdivisions)));
  const snappedPosition = numerator / subdivisions;
  const time = measure.start + snappedPosition * (measure.end - measure.start);
  return {
    mIdx: measure.mIdx,
    numerator,
    denominator: subdivisions,
    position: snappedPosition,
    time,
    key: editKey(measure.mIdx, numerator, subdivisions),
  };
}

function pointForTime(time) {
  const measure = state.measureScreenItems.find((item) => time >= item.start && time < item.end);
  if (!measure) return null;
  const position = (time - measure.start) / (measure.end - measure.start);
  const snap = document.getElementById("snapToggle").checked;
  const gridValue = document.getElementById("editGrid").value;
  let subdivisions = 64;
  if (snap) {
    subdivisions = gridValue === "chart"
      ? measure.grid
      : Math.max(1, Math.round(measure.numerator * Number(gridValue) / measure.denominator));
  }
  const numerator = Math.max(0, Math.min(subdivisions - 1, Math.round(position * subdivisions)));
  const snappedPosition = numerator / subdivisions;
  return {
    mIdx: measure.mIdx,
    numerator,
    denominator: subdivisions,
    position: snappedPosition,
    time: measure.start + snappedPosition * (measure.end - measure.start),
    key: editKey(measure.mIdx, numerator, subdivisions),
  };
}

function findNoteAt(x, y) {
  return state.hitScreenItems.find((item) => Math.hypot(item.x - x, item.y - y) <= item.r);
}

function findRollAt(x, y) {
  return state.rollScreenItems.find((item) => (
    y >= item.y - 20 && y <= item.y + 20 && x >= item.x1 && x <= item.x2
  ));
}

function setSingleEdit(point, code, sourceKey = null) {
  if (!state.data || !point) return;
  const existing = state.hitScreenItems.find((item) => (
    item.mIdx === point.mIdx && Math.abs(item.sIdx / item.grid - point.position) < 0.0001
  ));
  if (existing && existing.key !== sourceKey) {
    showToast("That chart position already has a note. Choose a note tool to replace it.");
    return;
  }
  saveUndoPoint();
  if (sourceKey) state.data.overrides.note_edits[sourceKey] = 0;
  state.data.overrides.note_edits[point.key] = code;
  state.selectedEdit = { key: point.key };
  rebuildChart(false);
}

function setRollSpan(start, end, code, existingRoll = null) {
  if (!state.data || !start || !end) return;
  if (end.time < start.time) [start, end] = [end, start];
  if (end.time - start.time < 0.03) {
    showToast("A roll needs a visible start and end. Drag across at least one grid step.");
    return;
  }
  const course = getActiveCourse();
  const { rolls } = computeFlattenedEvents(course);
  const offset = (state.data.overrides.global_offset_ms || 0) / 1000;
  const overlaps = rolls.some((roll) => {
    if (
      existingRoll &&
      roll.mIdx === existingRoll.roll.mIdx &&
      roll.sIdx === existingRoll.roll.sIdx
    ) return false;
    const rollStart = roll.start;
    return start.time < roll.end && end.time > rollStart;
  });
  if (overlaps) {
    showToast("Rolls cannot overlap. Move the new span outside the existing roll.");
    return;
  }

  saveUndoPoint();
  const edits = state.data.overrides.note_edits;
  const chartCourse = course;
  for (const measure of chartCourse.measures) {
    const measureStart = measure.start_sec + offset;
    const duration = measure.end_sec - measure.start_sec;
    for (let slot = 0; slot < measure.notes.length; slot++) {
      const time = measureStart + (slot / measure.notes.length) * duration;
      if (time > start.time && time < end.time) {
        edits[editKey(measure.measure_idx, slot, measure.notes.length)] = 0;
      }
    }
  }
  if (existingRoll) {
    edits[existingRoll.startKey] = 0;
    edits[existingRoll.endKey] = 0;
  }
  edits[start.key] = code;
  edits[end.key] = 8;

  const difficulty = document.getElementById("diffSelect").value;
  if (code === 7 && !existingRoll) {
    if (!state.data.overrides.balloon_counts_by_course) {
      state.data.overrides.balloon_counts_by_course = {};
    }
    const counts = state.data.overrides.balloon_counts_by_course[difficulty] ||
      [...(course.balloon_counts || [])];
    counts.push(Number(document.getElementById("balloonCount").value || "10"));
    state.data.overrides.balloon_counts_by_course[difficulty] = counts;
  }
  state.selectedEdit = existingRoll ? { startKey: start.key } : null;
  rebuildChart(false);
}

function removeRoll(item) {
  if (!state.data || !item) return;
  saveUndoPoint();
  state.data.overrides.note_edits[item.startKey] = 0;
  state.data.overrides.note_edits[item.endKey] = 0;
  if (item.roll.balloon) {
    const difficulty = document.getElementById("diffSelect").value;
    const counts = state.data.overrides.balloon_counts_by_course?.[difficulty] ||
      [...(getActiveCourse()?.balloon_counts || [])];
    const balloons = state.rollScreenItems.filter((roll) => roll.roll.balloon);
    const index = balloons.findIndex((roll) => roll.startKey === item.startKey);
    if (index >= 0) counts.splice(index, 1);
    state.data.overrides.balloon_counts_by_course[difficulty] = counts;
  }
  state.selectedEdit = null;
  rebuildChart(false);
}

function deleteSelectedEdit() {
  if (!state.data || !state.selectedEdit) return;
  if (state.selectedEdit.startKey) {
    const roll = state.rollScreenItems.find(
      (item) => item.startKey === state.selectedEdit.startKey
    );
    if (roll) removeRoll(roll);
    return;
  }
  const note = state.hitScreenItems.find((item) => item.key === state.selectedEdit.key);
  if (note) {
    setSingleEdit(
      { mIdx: note.mIdx, numerator: note.sIdx, denominator: note.grid, key: note.key },
      0,
      note.key
    );
  }
}

function finishCanvasEdit(event) {
  const drag = state.drag;
  if (!drag) return;
  const rect = event.currentTarget.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const point = pointFromPointer(x);
  state.drag = null;
  if (!point) return;

  if (drag.kind === "note") {
    if (drag.moved) {
      setSingleEdit(point, drag.note.code, drag.note.key);
    } else {
      const tool = document.getElementById("editTool").value;
      if (tool === "5" || tool === "7") {
        state.selectedEdit = { key: drag.note.key };
        showToast("Use click-drag on an empty chart position to draw a roll or balloon.");
        return;
      }
      const code = ["1", "2", "3", "4"].includes(tool)
        ? Number(tool)
        : ({ 1: 2, 2: 3, 3: 4, 4: 0 }[drag.note.code] ?? 0);
      setSingleEdit(point, code, drag.note.key);
    }
    return;
  }

  if (drag.kind === "roll") {
    if (!drag.moved) {
      state.selectedEdit = { startKey: drag.roll.startKey };
      return;
    }
    const delta = point.time - drag.startPoint.time;
    const start = pointForTime(drag.roll.roll.start + delta);
    const end = pointForTime(drag.roll.roll.end + delta);
    setRollSpan(start, end, drag.roll.roll.balloon ? 7 : drag.roll.roll.big ? 6 : 5, drag.roll);
    return;
  }

  const tool = document.getElementById("editTool").value;
  if (["1", "2", "3", "4"].includes(tool)) {
    setSingleEdit(point, Number(tool));
  } else if (tool === "5" || tool === "7") {
    if (!drag.moved) {
      showToast("Click and drag on the chart lane to draw a roll or balloon span.");
      return;
    }
    setRollSpan(drag.startPoint, point, Number(tool));
  }
}

function handleCanvasPointerDown(event) {
  if (!state.data || event.button === 2) return;
  const canvas = document.getElementById("taikoCanvas");
  const rect = canvas.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  const note = findNoteAt(x, y);
  const roll = findRollAt(x, y);
  const point = pointFromPointer(x);
  if (!point) return;
  const tool = document.getElementById("editTool").value;
  if (wavesurfer?.isPlaying()) wavesurfer.pause();
  state.selectedEdit = note ? { key: note.key } : roll ? { startKey: roll.startKey } : null;
  if (note) {
    state.drag = { kind: "note", note, startX: x, startY: y, currentPoint: point, moved: false };
  } else if (roll) {
    state.drag = { kind: "roll", roll, startPoint: point, startX: x, currentPoint: point, moved: false };
  } else if (y < (canvas.clientHeight * 0.57)) {
    state.drag = { kind: "add", startPoint: point, startX: x, currentPoint: point, moved: false };
  } else {
    return;
  }
  canvas.setPointerCapture(event.pointerId);
  event.preventDefault();
}

function handleCanvasPointerMove(event) {
  if (!state.drag) return;
  const rect = event.currentTarget.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const point = pointFromPointer(x);
  if (!point) return;
  state.drag.currentPoint = point;
  state.drag.moved = state.drag.moved || Math.abs(x - state.drag.startX) > 4;
}

function handleCanvasContextMenu(event) {
  event.preventDefault();
  const rect = event.currentTarget.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  const note = findNoteAt(x, y);
  const roll = findRollAt(x, y);
  if (note) setSingleEdit({ mIdx: note.mIdx, numerator: note.sIdx, denominator: note.grid, key: note.key }, 0, note.key);
  else if (roll) removeRoll(roll);
}

// --- Event Listeners ---
const taikoCanvas = document.getElementById("taikoCanvas");
taikoCanvas.addEventListener("pointerdown", handleCanvasPointerDown);
taikoCanvas.addEventListener("pointermove", handleCanvasPointerMove);
taikoCanvas.addEventListener("pointerup", finishCanvasEdit);
taikoCanvas.addEventListener("pointercancel", () => { state.drag = null; });
taikoCanvas.addEventListener("contextmenu", handleCanvasContextMenu);

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
document.getElementById("referenceVol").addEventListener("input", (e) => {
  const value = parseFloat(e.target.value);
  document.getElementById("lblReferenceVol").textContent = `${Math.round(value * 100)}%`;
  if (referenceMasterGain && audioCtx) {
    referenceMasterGain.gain.setValueAtTime(value, audioCtx.currentTime);
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
  const target = e.target;
  const isTyping = target instanceof HTMLElement &&
    (["INPUT", "SELECT", "TEXTAREA"].includes(target.tagName) || target.isContentEditable);
  const commandKey = e.ctrlKey || e.metaKey;

  if (commandKey && e.code === "KeyZ" && !isTyping) {
    e.preventDefault();
    if (e.shiftKey) redoEdit();
    else undoEdit();
    return;
  }
  if (commandKey && e.code === "KeyY" && !isTyping) {
    e.preventDefault();
    redoEdit();
    return;
  }
  if (commandKey && e.code === "KeyS" && !isTyping) {
    e.preventDefault();
    rebuildChart(true);
    return;
  }
  if (e.code === "Space" && !isTyping) {
    e.preventDefault();
    ensureAudioCtx();
    if (wavesurfer) wavesurfer.playPause();
    return;
  }
  if (isTyping) return;

  if (e.code === "Delete" || e.code === "Backspace") {
    e.preventDefault();
    deleteSelectedEdit();
  } else if (e.code === "ArrowLeft" || e.code === "ArrowRight") {
    if (!wavesurfer) return;
    e.preventDefault();
    const delta = e.code === "ArrowLeft" ? -1 : 1;
    wavesurfer.setTime(Math.max(0, wavesurfer.getCurrentTime() + delta * (e.shiftKey ? 1 : 0.1)));
  } else if (e.code === "Home" || e.code === "End") {
    if (!wavesurfer) return;
    e.preventDefault();
    wavesurfer.setTime(e.code === "Home" ? 0 : wavesurfer.getDuration());
  } else if (e.code === "Equal" || e.code === "NumpadAdd") {
    const zoom = document.getElementById("laneZoom");
    zoom.value = String(Math.min(Number(zoom.max), Number(zoom.value) + 20));
  } else if (e.code === "Minus" || e.code === "NumpadSubtract") {
    const zoom = document.getElementById("laneZoom");
    zoom.value = String(Math.max(Number(zoom.min), Number(zoom.value) - 20));
  } else if (e.code === "Escape") {
    state.drag = null;
    state.selectedEdit = null;
  } else if (e.code === "KeyS") {
    const snap = document.getElementById("snapToggle");
    snap.checked = !snap.checked;
    snap.dispatchEvent(new Event("change"));
  } else if (e.code === "KeyA") {
    setEditorTool("select");
  } else if (e.code === "KeyD") {
    setEditorTool("1");
  } else if (e.code === "KeyK") {
    setEditorTool("2");
  } else if (e.code === "KeyR") {
    setEditorTool("5");
  } else if (e.code === "KeyB") {
    setEditorTool("7");
  }
});

document.getElementById("btnUndo").addEventListener("click", undoEdit);
document.getElementById("btnRedo").addEventListener("click", redoEdit);
document.getElementById("btnHelp").addEventListener("click", () => {
  document.getElementById("shortcutPanel").classList.toggle("hidden");
});
function setEditorTool(value) {
  const tool = document.getElementById("editTool");
  tool.value = value;
  tool.dispatchEvent(new Event("change"));
}
document.getElementById("snapToggle").checked =
  localStorage.getItem("taikolab-editor-snap") !== "false";
document.getElementById("snapToggle").addEventListener("change", (e) => {
  localStorage.setItem("taikolab-editor-snap", String(e.target.checked));
  document.getElementById("editGrid").disabled = !e.target.checked;
});
document.getElementById("editGrid").disabled = !document.getElementById("snapToggle").checked;
document.getElementById("editTool").addEventListener("change", (e) => {
  document.getElementById("balloonCountControl").classList.toggle("hidden", e.target.value !== "7");
});
document.getElementById("balloonCountControl").classList.add("hidden");

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
  const difficulty = document.getElementById("diffSelect").value;
  const prefix = `${difficulty}:`;
  saveUndoPoint();
  state.data.overrides.note_edits = Object.fromEntries(
    Object.entries(state.data.overrides.note_edits || {}).filter(([key]) => !key.startsWith(prefix))
  );
  if (state.data.overrides.balloon_counts_by_course) {
    delete state.data.overrides.balloon_counts_by_course[difficulty];
  }
  state.selectedEdit = null;
  rebuildChart(false);
  showToast(`${difficulty} manual edits cleared`);
});

document.getElementById("btnExport").addEventListener("click", () => rebuildChart(true));

init();