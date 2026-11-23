// TaikoLab Director's Console Frontend (Space Lock, Fluid Grid/Barlines, Multi-Select, Scrollbars)

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

const ROLL_EDGE_HIT_PX = 9;
const BARLINE_HIT_PX = 10;
const MAX_EDIT_HISTORY = 100;

let state = {
  songId: null,
  importId: null,
  data: null,
  selectedSectionIdx: 0,
  loopSection: false,
  lastPlayTime: 0,
  viewTime: 0,
  scrubbingLane: false,
  tapArmed: false,
  sessionTaps: [],
  punchStartBaseline: null,
  referenceHits: [],
  hitScreenItems: [],
  slotScreenItems: [],
  rollScreenItems: [],
  measureScreenItems: [],
  barlineScreenItems: [],
  selectedKeys: new Set(),
  selectedRollKeys: new Set(),
  clipboard: null,
  sourceTjaPath: null,
  activeProjectName: "",
  markInSec: null,
  markOutSec: null,
  gridAnchorIdx: null,
  gridPreviewTimes: null,
  gridStatusText: "",
  activeTool: "select",
  drag: null,
  editUndo: [],
  editRedo: [],
  lastRegionKey: "",
  editSeq: 0,
  rebuildInFlight: false,
  pendingRebuild: null,
};

let wavesurfer = null;
let regionsPlugin = null;
let audioCtx = null;
let hitMasterGain = null;
let referenceMasterGain = null;

// --- Math Helpers ---
function gcd(a, b) {
  a = Math.abs(Math.round(a));
  b = Math.abs(Math.round(b));
  while (b !== 0) {
    const t = a % b;
    a = b;
    b = t;
  }
  return Math.max(1, a);
}

function lcm(a, b) {
  return Math.round((a * b) / gcd(a, b));
}

function isTextInputElement(el) {
  if (!(el instanceof HTMLElement)) return false;
  if (el.isContentEditable) return true;
  if (el.tagName === "TEXTAREA") return true;
  if (el.tagName === "INPUT") {
    const type = (el.getAttribute("type") || "text").toLowerCase();
    return !["range", "checkbox", "radio", "button", "submit", "reset"].includes(type);
  }
  return false;
}

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
    const hitLimiter = audioCtx.createDynamicsCompressor();
    hitLimiter.threshold.value = -0.5;
    hitLimiter.ratio.value = 20;
    hitMasterGain.connect(hitLimiter).connect(audioCtx.destination);
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
    gain.gain.setValueAtTime(noteCode === 3 ? 0.95 : 0.80, now);
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
    gain.gain.setValueAtTime(noteCode === 4 ? 0.85 : 0.70, now);
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
  const safe = Math.max(0, sec || 0);
  const m = Math.floor(safe / 60);
  const s = (safe % 60).toFixed(2).padStart(5, "0");
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

function hasWaveAudio() {
  return Boolean(wavesurfer && wavesurfer.getDuration() > 0);
}

function getChartDuration() {
  if (hasWaveAudio()) return wavesurfer.getDuration();
  const course = getActiveCourse();
  if (course && course.measures.length > 0) {
    return course.measures[course.measures.length - 1].end_sec;
  }
  return state.data?.analysis_summary?.duration_sec || 0;
}

function getCurrentTimeSec() {
  if (hasWaveAudio()) return wavesurfer.getCurrentTime();
  return state.viewTime;
}

function seekToSec(t) {
  const dur = getChartDuration();
  const clamped = Math.max(0, Math.min(dur || 0, t));
  state.viewTime = clamped;
  if (hasWaveAudio()) {
    wavesurfer.setTime(clamped);
  }
  const scroll = document.getElementById("laneScroll");
  if (scroll && !state.scrubbingLane) {
    scroll.max = String(dur || 1);
    scroll.value = String(clamped);
  }
}

function getBaseBarlinesForCourse(course) {
  if (!course || !course.measures.length) return [];
  const offsetDelta = (state.data?.overrides?.global_offset_ms || 0) / 1000.0;
  if (
    state.importId &&
    state.data?.overrides?.barline_times?.length === course.measures.length + 1
  ) {
    return [...state.data.overrides.barline_times];
  }
  if (
    !state.importId &&
    state.data?.overrides?.barline_times?.length === course.measures.length + 1
  ) {
    return [...state.data.overrides.barline_times];
  }
  const times = course.measures.map((m) =>
    state.importId ? Math.max(0, m.start_sec - offsetDelta) : m.start_sec
  );
  const last = course.measures[course.measures.length - 1];
  times.push(state.importId ? Math.max(0, last.end_sec - offsetDelta) : last.end_sec);
  return times;
}

function measureDurationBounds(measure) {
  const minDur = Math.max(0.15, (240.0 * measure.numerator) / (measure.denominator * 370.0));
  const maxDur = Math.min(8.0, (240.0 * measure.numerator) / (measure.denominator * 32.0));
  return { minDur, maxDur };
}

function computeBarlineDragResult(origTimes, course, barIdx, rawTargetTime, anchorIdx, shiftFollowing) {
  const times = [...origTimes];
  const measures = course.measures;
  if (times.length !== measures.length + 1) return { times, status: "" };

  const formatBpm = (idx) => {
    const m = measures[idx];
    const dur = Math.max(0.05, times[idx + 1] - times[idx]);
    return ((240.0 * m.numerator) / (m.denominator * dur)).toFixed(1);
  };

  if (anchorIdx !== null && anchorIdx !== barIdx) {
    const a = Math.min(anchorIdx, barIdx);
    const b = Math.max(anchorIdx, barIdx);
    const draggedIsEnd = barIdx === b;
    const oldSpan = Math.max(0.05, origTimes[b] - origTimes[a]);

    let minSpan = 0.0;
    let maxSpan = 0.0;
    for (let i = a; i < b; i++) {
      const { minDur, maxDur } = measureDurationBounds(measures[i]);
      const ratio = (origTimes[i + 1] - origTimes[i]) / oldSpan;
      minSpan = Math.max(minSpan, minDur / Math.max(1e-4, ratio));
      maxSpan = maxSpan === 0 ? maxDur / Math.max(1e-4, ratio) : Math.min(maxSpan, maxDur / Math.max(1e-4, ratio));
    }

    if (draggedIsEnd) {
      let maxB = origTimes[a] + maxSpan;
      let minB = origTimes[a] + minSpan;
      if (!shiftFollowing && b < measures.length) {
        const nextBounds = measureDurationBounds(measures[b]);
        minB = Math.max(minB, origTimes[b + 1] - nextBounds.maxDur);
        maxB = Math.min(maxB, origTimes[b + 1] - nextBounds.minDur);
      }
      const clampedB = Math.max(minB, Math.min(maxB, rawTargetTime));
      const newSpan = clampedB - origTimes[a];
      for (let j = a + 1; j <= b; j++) {
        const ratio = (origTimes[j] - origTimes[a]) / oldSpan;
        times[j] = Number((origTimes[a] + ratio * newSpan).toFixed(4));
      }
      if (shiftFollowing) {
        const delta = times[b] - origTimes[b];
        for (let j = b + 1; j < times.length; j++) {
          times[j] = Number(Math.max(times[j - 1] + 0.12, origTimes[j] + delta).toFixed(4));
        }
      }
      return {
        times,
        status: `Stretch #${a}–#${b - 1}: ~${formatBpm(a)} BPM`,
      };
    }

    // Dragging start anchor `a` while `b` is pinned
    let minA = origTimes[b] - maxSpan;
    let maxA = origTimes[b] - minSpan;
    minA = Math.max(0, minA);
    if (a > 0) {
      const prevBounds = measureDurationBounds(measures[a - 1]);
      minA = Math.max(minA, origTimes[a - 1] + prevBounds.minDur);
      maxA = Math.min(maxA, origTimes[a - 1] + prevBounds.maxDur);
    }
    const clampedA = Math.max(minA, Math.min(maxA, rawTargetTime));
    const newSpan = origTimes[b] - clampedA;
    for (let j = a; j < b; j++) {
      const ratio = (origTimes[b] - origTimes[j]) / oldSpan;
      times[j] = Number((origTimes[b] - ratio * newSpan).toFixed(4));
    }
    return {
      times,
      status: `Stretch #${a}–#${b - 1}: ~${formatBpm(a)} BPM`,
    };
  }

  // Single barline drag (or shift downstream)
  if (shiftFollowing) {
    let delta = rawTargetTime - origTimes[barIdx];
    if (barIdx === 0) {
      delta = Math.max(-origTimes[0], delta);
    } else {
      const prevBounds = measureDurationBounds(measures[barIdx - 1]);
      const minT = origTimes[barIdx - 1] + prevBounds.minDur;
      const maxT = origTimes[barIdx - 1] + prevBounds.maxDur;
      delta = Math.max(minT - origTimes[barIdx], Math.min(maxT - origTimes[barIdx], delta));
    }
    for (let j = barIdx; j < times.length; j++) {
      times[j] = Number(Math.max(0, origTimes[j] + delta).toFixed(4));
    }
    const label = barIdx > 0 ? `Bar #${barIdx - 1}: ${formatBpm(barIdx - 1)} BPM` : `Start: ${times[0].toFixed(3)}s`;
    return { times, status: `${label} (+downstream)` };
  }

  if (barIdx === 0) {
    const firstBounds = measureDurationBounds(measures[0]);
    const minT = Math.max(0, origTimes[1] - firstBounds.maxDur);
    const maxT = origTimes[1] - firstBounds.minDur;
    times[0] = Number(Math.max(minT, Math.min(maxT, rawTargetTime)).toFixed(4));
    return { times, status: `Bar #0: ${formatBpm(0)} BPM` };
  }
  if (barIdx === times.length - 1) {
    const lastBounds = measureDurationBounds(measures[measures.length - 1]);
    const minT = origTimes[barIdx - 1] + lastBounds.minDur;
    const maxT = origTimes[barIdx - 1] + lastBounds.maxDur;
    times[barIdx] = Number(Math.max(minT, Math.min(maxT, rawTargetTime)).toFixed(4));
    return { times, status: `Bar #${measures.length - 1}: ${formatBpm(measures.length - 1)} BPM` };
  }

  const prevBounds = measureDurationBounds(measures[barIdx - 1]);
  const nextBounds = measureDurationBounds(measures[barIdx]);
  const minT = Math.max(origTimes[barIdx - 1] + prevBounds.minDur, origTimes[barIdx + 1] - nextBounds.maxDur);
  const maxT = Math.min(origTimes[barIdx - 1] + prevBounds.maxDur, origTimes[barIdx + 1] - nextBounds.minDur);
  times[barIdx] = Number(Math.max(minT, Math.min(maxT, rawTargetTime)).toFixed(4));
  return {
    times,
    status: `Bar #${barIdx - 1}: ${formatBpm(barIdx - 1)} BPM | #${barIdx}: ${formatBpm(barIdx)} BPM`,
  };
}

function computeFlattenedEvents(course) {
  if (!course) return { notes: [], rolls: [] };
  const offsetDelta = state.importId ? 0.0 : (state.data.overrides.global_offset_ms || 0) / 1000.0;
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
          start: rollStart.time,
          end: t,
          endMIdx: m.measure_idx,
          endSIdx: s,
          endGrid: L,
          startKey: editKey(rollStart.mIdx, rollStart.sIdx, rollStart.grid),
          endKey: editKey(m.measure_idx, s, L),
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

function orderedBalloonKeys(course) {
  if (!course) return [];
  const keys = [];
  for (const m of course.measures) {
    const L = m.notes.length;
    for (let s = 0; s < L; s++) {
      if (m.notes[s] === 7) {
        keys.push(editKey(m.measure_idx, s, L));
      }
    }
  }
  return keys;
}

function getEffectiveBalloonCounts(course, difficulty) {
  const overrideCounts = state.data?.overrides?.balloon_counts_by_course?.[difficulty];
  if (Array.isArray(overrideCounts)) {
    return [...overrideCounts];
  }
  return [...(course?.balloon_counts || [])];
}

function clearSelection() {
  state.selectedKeys.clear();
  state.selectedRollKeys.clear();
  updateSelectionUI();
}

function setSingleNoteSelection(key) {
  state.selectedKeys.clear();
  state.selectedRollKeys.clear();
  if (key) state.selectedKeys.add(key);
  updateSelectionUI();
}

function setSingleRollSelection(startKey) {
  state.selectedKeys.clear();
  state.selectedRollKeys.clear();
  if (startKey) state.selectedRollKeys.add(startKey);
  updateSelectionUI();
}

function hasSelection() {
  return state.selectedKeys.size > 0 || state.selectedRollKeys.size > 0;
}

function getSingleSelectedRollKey() {
  if (state.selectedKeys.size === 0 && state.selectedRollKeys.size === 1) {
    return [...state.selectedRollKeys][0];
  }
  return null;
}

function updateBalloonControl() {
  const control = document.getElementById("balloonCountControl");
  const input = document.getElementById("balloonCount");
  const course = getActiveCourse();
  const difficulty = document.getElementById("diffSelect").value;
  const singleRollKey = getSingleSelectedRollKey();
  let selectedBalloonIdx = -1;

  if (singleRollKey && course) {
    selectedBalloonIdx = orderedBalloonKeys(course).indexOf(singleRollKey);
  }

  const show = state.activeTool === "7" || selectedBalloonIdx >= 0;
  control.classList.toggle("hidden", !show);

  if (selectedBalloonIdx >= 0 && course) {
    const counts = getEffectiveBalloonCounts(course, difficulty);
    input.value = String(counts[selectedBalloonIdx] ?? 10);
  }
}

function updateGridUI() {
  const gridControls = document.getElementById("gridControls");
  const badge = document.getElementById("gridAnchorBadge");
  const resetBtn = document.getElementById("btnResetGrid");
  const hasWarps = Boolean(state.data?.overrides?.barline_times?.length);
  gridControls.classList.toggle("hidden", state.activeTool !== "grid" && !hasWarps);

  if (state.gridAnchorIdx !== null) {
    badge.textContent = `Pin: Bar #${state.gridAnchorIdx} (drag another barline)`;
    badge.style.borderColor = "#3ddc84";
  } else {
    badge.textContent = "Pin: none (click barline to pin)";
    badge.style.borderColor = "#2a303d";
  }
  resetBtn.disabled = !hasWarps;
}

function updateSelectionUI() {
  const count = state.selectedKeys.size + state.selectedRollKeys.size;
  const badge = document.getElementById("selectionBadge");
  badge.textContent = `${count} selected`;
  badge.style.borderColor = count > 0 ? "#3ddc84" : "#2a303d";

  const disabled = count === 0;
  document.getElementById("btnInchLeft").disabled = disabled;
  document.getElementById("btnInchRight").disabled = disabled;
  document.getElementById("btnFlipSelection").disabled = state.selectedKeys.size === 0;
  document.getElementById("btnCopySelection").disabled = disabled;
  document.getElementById("btnPasteSelection").disabled = !state.clipboard;
  document.getElementById("btnDeleteSelection").disabled = disabled;
  document.getElementById("btnClearSelection").disabled = disabled;
  updateBalloonControl();
  updateGridUI();
}

const DEFAULT_ROLL_CAPS = { easy: 1, normal: 4, hard: 5, oni: 6, ura: 6 };
const DEFAULT_GROOVE_TRIPLE_DENSITY = { easy: 0, normal: 15, hard: 45, oni: 70, ura: 75 };
const DEFAULT_TRIPLE_COMPLEXITY = { easy: 0, normal: 15, hard: 25, oni: 80, ura: 85 };

function populateChartControls() {
  if (!state.data || state.importId) return;
  const ov = state.data.overrides || {};
  const diff = document.getElementById("diffSelect").value;
  document.getElementById("lblCtrlCourseName").textContent = diff.toUpperCase();

  const speed = ov.target_highway_speed ?? 101.343;
  document.getElementById("ctrlHighwaySpeed").value = String(speed);
  document.getElementById("lblCtrlSpeed").textContent = Number(speed).toFixed(0);

  const big = ov.big_note_threshold ?? 0.88;
  document.getElementById("ctrlBigNote").value = String(big);
  document.getElementById("lblCtrlBig").textContent = Number(big).toFixed(2);

  const quiet = ov.quiet_guitar_rms_threshold ?? 0.25;
  document.getElementById("ctrlQuietThreshold").value = String(quiet);
  document.getElementById("lblCtrlQuiet").textContent = Number(quiet).toFixed(2);
  document.getElementById("ctrlQuietBehavior").value = ov.quiet_guitar_behavior || "downbeat";

  const diffOv = ov.difficulty_overrides?.[diff] || {};
  document.getElementById("ctrlCourseFollow").value = diffOv.follow_mode || "";

  const densityPct = Math.round((diffOv.density_scale ?? 1.0) * 100);
  document.getElementById("ctrlCourseDensity").value = String(densityPct);
  document.getElementById("lblCtrlDensity").textContent = `${densityPct}%`;

  const varPct = Math.round((diffOv.phrase_variation ?? 0.0) * 100);
  const varInput = document.getElementById("ctrlCourseVariation");
  varInput.value = String(varPct);
  varInput.disabled = diff === "easy" || diff === "normal";
  document.getElementById("btnResetCtrlVariation").disabled = varInput.disabled;
  document.getElementById("lblCtrlVariation").textContent = `${varPct}%`;

  const rollCap = ov.fill_rolls_by_course?.[diff] ?? ov.fill_rolls_per_course ?? DEFAULT_ROLL_CAPS[diff] ?? 0;
  document.getElementById("ctrlCourseRolls").value = String(rollCap);
  document.getElementById("lblCtrlRolls").textContent = String(rollCap);

  const tripleDenRaw = ov.groove_triple_density_by_course?.[diff];
  const tripleDenPct = tripleDenRaw !== undefined && tripleDenRaw !== null
    ? Math.round(tripleDenRaw * 100)
    : (DEFAULT_GROOVE_TRIPLE_DENSITY[diff] ?? 0);
  document.getElementById("ctrlCourseTriples").value = String(tripleDenPct);
  document.getElementById("lblCtrlTriples").textContent = `${tripleDenPct}%`;

  const compRaw = ov.groove_triple_complexity_by_course?.[diff] ?? ov.groove_triple_complexity;
  const compPct = compRaw !== undefined && compRaw !== null
    ? Math.round(compRaw * 100)
    : (DEFAULT_TRIPLE_COMPLEXITY[diff] ?? 35);
  const compInp = document.getElementById("ctrlTripleComp");
  compInp.value = String(compPct);
  compInp.disabled = diff === "easy";
  document.getElementById("btnResetCtrlTripleComp").disabled = compInp.disabled;
  document.getElementById("lblCtrlTripleComp").textContent = `${compPct}%`;

  const maxBpm = Math.round(ov.groove_triple_max_bpm ?? 235);
  document.getElementById("ctrlTripleMaxBpm").value = String(maxBpm);
  document.getElementById("lblCtrlTripleBpm").textContent = String(maxBpm);

  const rampPct = Math.round((ov.groove_triple_ramp ?? 0.50) * 100);
  document.getElementById("ctrlTripleRamp").value = String(rampPct);
  document.getElementById("lblCtrlTripleRamp").textContent = `${rampPct}%`;

  const secVarPct = Math.round((ov.section_phrase_variety ?? 0.35) * 100);
  document.getElementById("ctrlSectionVariety").value = String(secVarPct);
  document.getElementById("lblCtrlSecVariety").textContent = `${secVarPct}%`;

  const vocMin = ov.vocal_min_strength ?? 0.36;
  document.getElementById("ctrlVocalMin").value = String(vocMin);
  document.getElementById("lblCtrlVocalMin").textContent = Number(vocMin).toFixed(2);

  const gtrMin = ov.guitar_min_strength ?? 0.58;
  document.getElementById("ctrlGuitarMin").value = String(gtrMin);
  document.getElementById("lblCtrlGuitarMin").textContent = Number(gtrMin).toFixed(2);

  const cymSensPct = Math.round((ov.cymbal_sensitivity ?? 1.0) * 100);
  document.getElementById("ctrlCymbalSens").value = String(cymSensPct);
  document.getElementById("lblCtrlCymbalSens").textContent = `${cymSensPct}%`;
  document.getElementById("ctrlRideMode").value = ov.ride_cymbal_mode || "accents_and_solo";
  document.getElementById("ctrlEnableBigNotes").checked = ov.enable_big_notes !== false;
  document.getElementById("ctrlFoldBlasts").checked = Boolean(ov.fold_blasts_to_rolls);
}

function applyChartControlsToOverrides() {
  if (!state.data || state.importId) return;
  const ov = state.data.overrides;
  const diff = document.getElementById("diffSelect").value;

  ov.target_highway_speed = parseFloat(document.getElementById("ctrlHighwaySpeed").value);
  ov.big_note_threshold = parseFloat(document.getElementById("ctrlBigNote").value);
  ov.quiet_guitar_rms_threshold = parseFloat(document.getElementById("ctrlQuietThreshold").value);
  ov.quiet_guitar_behavior = document.getElementById("ctrlQuietBehavior").value;

  if (!ov.fill_rolls_by_course) ov.fill_rolls_by_course = {};
  ov.fill_rolls_by_course[diff] = parseInt(document.getElementById("ctrlCourseRolls").value, 10);

  if (!ov.groove_triple_density_by_course) ov.groove_triple_density_by_course = {};
  ov.groove_triple_density_by_course[diff] = parseFloat(document.getElementById("ctrlCourseTriples").value) / 100.0;

  if (!ov.groove_triple_complexity_by_course) ov.groove_triple_complexity_by_course = {};
  if (diff !== "easy") {
    ov.groove_triple_complexity_by_course[diff] = parseFloat(document.getElementById("ctrlTripleComp").value) / 100.0;
  }
  ov.groove_triple_max_bpm = parseFloat(document.getElementById("ctrlTripleMaxBpm").value);
  ov.groove_triple_ramp = parseFloat(document.getElementById("ctrlTripleRamp").value) / 100.0;
  ov.section_phrase_variety = parseFloat(document.getElementById("ctrlSectionVariety").value) / 100.0;
  ov.vocal_min_strength = parseFloat(document.getElementById("ctrlVocalMin").value);
  ov.guitar_min_strength = parseFloat(document.getElementById("ctrlGuitarMin").value);
  ov.cymbal_sensitivity = parseFloat(document.getElementById("ctrlCymbalSens").value) / 100.0;
  ov.ride_cymbal_mode = document.getElementById("ctrlRideMode").value;
  ov.enable_big_notes = document.getElementById("ctrlEnableBigNotes").checked;
  ov.fold_blasts_to_rolls = document.getElementById("ctrlFoldBlasts").checked;

  if (!ov.difficulty_overrides) ov.difficulty_overrides = {};
  const existing = ov.difficulty_overrides[diff] || {};
  const followVal = document.getElementById("ctrlCourseFollow").value;
  ov.difficulty_overrides[diff] = {
    ...existing,
    follow_mode: followVal || null,
    density_scale: parseFloat(document.getElementById("ctrlCourseDensity").value) / 100.0,
    phrase_variation: parseFloat(document.getElementById("ctrlCourseVariation").value) / 100.0,
  };
}

function setEditorTool(value) {
  state.activeTool = value;
  document.querySelectorAll(".tool-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tool === value);
  });
  const canvas = document.getElementById("taikoCanvas");
  if (value === "select") {
    canvas.style.cursor = "default";
  } else if (value === "erase") {
    canvas.style.cursor = "not-allowed";
  } else if (value === "grid") {
    canvas.style.cursor = "ew-resize";
  } else {
    canvas.style.cursor = "crosshair";
  }
  updateBalloonControl();
  updateGridUI();
}

function cloneEditState() {
  return JSON.parse(JSON.stringify({
    note_edits: state.data?.overrides?.note_edits || {},
    balloon_counts_by_course: state.data?.overrides?.balloon_counts_by_course || {},
    barline_times: state.data?.overrides?.barline_times || [],
    global_offset_ms: state.data?.overrides?.global_offset_ms ?? 0,
    course_sources: state.data?.overrides?.course_sources || {},
    disabled_courses: state.data?.overrides?.disabled_courses || [],
    difficulty_overrides: state.data?.overrides?.difficulty_overrides || {},
    fill_rolls_by_course: state.data?.overrides?.fill_rolls_by_course || {},
    sections: state.data?.overrides?.sections || [],
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
  state.data.overrides.barline_times = snapshot.barline_times || [];
  state.data.overrides.global_offset_ms = snapshot.global_offset_ms ?? 0;
  state.data.overrides.course_sources = snapshot.course_sources || {};
  state.data.overrides.disabled_courses = snapshot.disabled_courses || [];
  state.data.overrides.difficulty_overrides = snapshot.difficulty_overrides || {};
  state.data.overrides.fill_rolls_by_course = snapshot.fill_rolls_by_course || {};
  if (snapshot.sections) state.data.overrides.sections = snapshot.sections;
  document.getElementById("globalOffsetInput").value = state.data.overrides.global_offset_ms;
  clearSelection();
  state.editSeq += 1;
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
  const nRaw = Math.max(0, Math.min(denominator - 1, Math.round(numerator)));
  const divisor = gcd(nRaw, denominator);
  const n = nRaw / divisor;
  const d = denominator / divisor;
  return `${document.getElementById("diffSelect").value}:${measureIndex}:p${n}/${d}`;
}

function applyOptimisticNotePatch(patch) {
  const course = getActiveCourse();
  if (!course) return;
  const activeDiff = document.getElementById("diffSelect").value;
  const prefix = `${activeDiff}:`;

  for (const [key, code] of Object.entries(patch)) {
    if (!key.startsWith(prefix)) continue;
    const parts = key.split(":");
    if (parts.length !== 3 || !parts[2].startsWith("p")) continue;
    const mIdx = Number(parts[1]);
    const [n, d] = parts[2].slice(1).split("/").map(Number);
    const measure = course.measures[mIdx];
    if (!measure || !Number.isInteger(n) || !Number.isInteger(d) || d <= 0) continue;

    if (measure.notes.length % d !== 0) {
      const targetLen = lcm(measure.notes.length, d);
      if (targetLen <= 4096) {
        const expanded = new Array(targetLen).fill(0);
        const factor = targetLen / measure.notes.length;
        for (let i = 0; i < measure.notes.length; i++) {
          expanded[i * factor] = measure.notes[i];
        }
        measure.notes = expanded;
      } else {
        continue;
      }
    }
    const slotIdx = n * (measure.notes.length / d);
    if (slotIdx >= 0 && slotIdx < measure.notes.length) {
      measure.notes[slotIdx] = code;
    }
  }
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
  const waveZoomVal = Number(document.getElementById("waveZoom")?.value || "0");

  regionsPlugin = RegionsCls.create();
  wavesurfer = WaveSurfer.create({
    container: "#waveform",
    waveColor: "#485468",
    progressColor: "#2ca8e8",
    cursorColor: "#3ddc84",
    height: 92,
    normalize: true,
    minPxPerSec: waveZoomVal,
    dragToSeek: true,
    autoScroll: true,
    autoCenter: true,
    hideScrollbar: false,
    url: audioUrl,
    plugins: [
      regionsPlugin,
      TimelineCls.create({
        height: 20,
        style: { fontSize: "11px", color: "#8b95a8" },
      }),
    ],
  });

  wavesurfer.on("ready", () => {
    wavesurfer.setVolume(parseFloat(document.getElementById("musicVol").value || "0.55"));
    const scroll = document.getElementById("laneScroll");
    if (scroll) {
      scroll.max = String(wavesurfer.getDuration() || 1);
      scroll.value = String(wavesurfer.getCurrentTime() || 0);
    }
    renderRegions(true);
  });

  wavesurfer.on("timeupdate", (t) => {
    state.viewTime = t;
  });

  wavesurfer.on("interaction", (t) => {
    state.viewTime = t;
  });

  regionsPlugin.on("region-clicked", (region, e) => {
    if (region.id.startsWith("sec-")) {
      const idx = parseInt(region.id.replace("sec-", ""), 10);
      if (!Number.isNaN(idx)) {
        selectSection(idx);
        if (e && e.shiftKey) {
          e.stopPropagation();
          seekToSec(region.start);
        }
      }
    } else if (region.id.startsWith("tap-")) {
      if (e && e.shiftKey) {
        e.stopPropagation();
        seekToSec(region.start);
      }
    }
  });

  regionsPlugin.on("region-double-clicked", (region, e) => {
    if (region.id.startsWith("tap-")) {
      e.stopPropagation();
      const idx = parseInt(region.id.replace("tap-", ""), 10);
      if (!Number.isNaN(idx) && state.data?.overrides?.custom_downbeats) {
        state.data.overrides.custom_downbeats.splice(idx, 1);
        state.data.overrides.barline_times = [];
        updateTapStatusBadge();
        state.editSeq += 1;
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
        const minStart = idx > 0 ? secs[idx - 1].start_sec + 0.25 : 0.0;
        const maxEnd = idx + 1 < secs.length ? secs[idx + 1].end_sec - 0.25 : getChartDuration();
        let newStart = snapTimeToBarline(region.start, minStart, region.end - 0.25);
        let newEnd = snapTimeToBarline(region.end, newStart + 0.25, maxEnd || region.end + 10);
        if (idx === 0) newStart = 0.0;
        secs[idx].start_sec = Number(newStart.toFixed(3));
        secs[idx].end_sec = Number(newEnd.toFixed(3));
        if (idx > 0) secs[idx - 1].end_sec = secs[idx].start_sec;
        if (idx + 1 < secs.length) secs[idx + 1].start_sec = secs[idx].end_sec;
        state.lastRegionKey = "";
        renderSectionList();
        renderRegions(true);
        if (idx === state.selectedSectionIdx) populateInspector();
      }
    } else if (region.id.startsWith("tap-")) {
      const idx = parseInt(region.id.replace("tap-", ""), 10);
      const taps = state.data?.overrides?.custom_downbeats || [];
      if (!Number.isNaN(idx) && taps[idx]) {
        taps[idx].time_sec = Number(region.start.toFixed(4));
        taps.sort((a, b) => a.time_sec - b.time_sec);
        state.data.overrides.barline_times = [];
        state.lastRegionKey = "";
        state.editSeq += 1;
        rebuildChart(false);
      }
    }
  });
}

function renderRegions(force = false) {
  if (!regionsPlugin || !state.data) return;
  const secs = state.data.overrides.sections || [];
  const taps = state.data.overrides.custom_downbeats || [];
  const sig = JSON.stringify({ secs, taps });
  if (!force && sig === state.lastRegionKey) return;
  state.lastRegionKey = sig;

  regionsPlugin.clearRegions();
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

function formatSectionBadge(sec) {
  const custom = sec.difficulty_rules ? Object.entries(sec.difficulty_rules) : [];
  if (custom.length > 0) {
    return custom.map(([d, r]) => `${d[0].toUpperCase()}:${r.follow_mode}`).join(" ");
  }
  return sec.follow_mode || "auto";
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
      <span class="badge">${formatSectionBadge(sec)}</span>
    `;
    div.onclick = () => {
      selectSection(idx);
      seekToSec(sec.start_sec);
    };
    container.appendChild(div);
  });
}

function selectSection(idx) {
  state.selectedSectionIdx = idx;
  renderSectionList();
  populateInspector();
}

function snapTimeToBarline(timeSec, minSec, maxSec) {
  const course = getActiveCourse();
  const barlines = getBaseBarlinesForCourse(course);
  const candidates = barlines.filter((t) => t > minSec + 0.05 && t < maxSec - 0.05);
  if (candidates.length === 0) {
    return Math.max(minSec, Math.min(maxSec, timeSec));
  }
  let best = candidates[0];
  let bestDist = Math.abs(best - timeSec);
  for (const t of candidates) {
    const d = Math.abs(t - timeSec);
    if (d < bestDist) {
      best = t;
      bestDist = d;
    }
  }
  return bestDist <= 2.0 ? best : Math.max(minSec, Math.min(maxSec, timeSec));
}

function carveSectionRange(startSec, endSec, label) {
  if (!state.data) return false;
  const secs = state.data.overrides.sections || [];
  const s = Number(startSec.toFixed(3));
  const e = Number(endSec.toFixed(3));
  if (e - s < 0.25) {
    showToast("Selected range is too short for a section.");
    return false;
  }

  const nextSecs = [];
  let insertedIdx = -1;
  for (const sec of secs) {
    if (sec.end_sec <= s + 0.05 || sec.start_sec >= e - 0.05) {
      nextSecs.push({ ...sec });
      continue;
    }
    if (sec.start_sec < s - 0.05) {
      nextSecs.push({ ...sec, end_sec: s });
    }
    if (insertedIdx < 0) {
      insertedIdx = nextSecs.length;
      nextSecs.push({
        ...sec,
        start_sec: s,
        end_sec: e,
        label: label || `Section ${secs.length + 1}`,
      });
    }
    if (sec.end_sec > e + 0.05) {
      nextSecs.push({
        ...sec,
        start_sec: e,
        label: `${sec.label || "Part"} (cont.)`,
      });
    }
  }

  if (insertedIdx < 0) {
    insertedIdx = nextSecs.length;
    nextSecs.push({
      start_sec: s,
      end_sec: e,
      label: label || `Section ${nextSecs.length + 1}`,
      follow_mode: "auto",
    });
  }

  nextSecs.sort((a, b) => a.start_sec - b.start_sec);
  for (let i = 0; i < nextSecs.length - 1; i++) {
    nextSecs[i].end_sec = nextSecs[i + 1].start_sec;
  }
  state.data.overrides.sections = nextSecs.filter((sec) => sec.end_sec - sec.start_sec >= 0.05);
  const finalIdx = state.data.overrides.sections.findIndex(
    (sec) => Math.abs(sec.start_sec - s) < 0.02
  );
  selectSection(finalIdx >= 0 ? finalIdx : 0);
  renderRegions(true);
  state.editSeq += 1;
  rebuildChart(false);
  return true;
}

function splitSectionAtPlayhead() {
  if (!state.data) return;
  const offsetDelta = state.importId ? 0.0 : (state.data.overrides.global_offset_ms || 0) / 1000.0;
  const rawT = getCurrentTimeSec() - offsetDelta;
  const secs = state.data.overrides.sections || [];
  const idx = secs.findIndex((s) => rawT >= s.start_sec && rawT <= s.end_sec);
  if (idx < 0) {
    showToast("Move playhead inside a section to split it.");
    return;
  }
  const orig = secs[idx];
  const snapped = snapTimeToBarline(rawT, orig.start_sec + 0.15, orig.end_sec - 0.15);
  if (snapped <= orig.start_sec + 0.15 || snapped >= orig.end_sec - 0.15) {
    showToast("Section is too short at the playhead to split.");
    return;
  }
  const t = Number(snapped.toFixed(3));
  const right = { ...orig, start_sec: t, label: `${orig.label || "Part"} (B)` };
  orig.end_sec = t;
  secs.splice(idx + 1, 0, right);
  selectSection(idx + 1);
  renderRegions(true);
  state.editSeq += 1;
  rebuildChart(false);
  showToast(`Split section cleanly at barline ${fmtTime(t)}`);
}

function createSectionFromSelection() {
  if (!state.data || !hasSelection()) {
    showToast("Select notes or rolls on the lane first.");
    return;
  }
  const course = getActiveCourse();
  if (!course || !course.measures.length) return;
  const { notes, rolls } = computeFlattenedEvents(course);
  const indices = [];
  for (const n of notes) {
    if (state.selectedKeys.has(n.key)) indices.push(n.mIdx);
  }
  for (const r of rolls) {
    if (state.selectedRollKeys.has(r.startKey)) {
      indices.push(r.mIdx, r.endMIdx);
    }
  }
  if (!indices.length) return;
  const minIdx = Math.min(...indices);
  const maxIdx = Math.max(...indices);
  const barlines = getBaseBarlinesForCourse(course);
  const startSec = barlines[minIdx];
  const endSec = barlines[maxIdx + 1];
  if (carveSectionRange(startSec, endSec, `Bars #${minIdx}-#${maxIdx}`)) {
    showToast(`Created section for bars #${minIdx}–#${maxIdx}`);
  }
}

function straightenSelectedSectionBpm() {
  if (!state.data) return;
  const secs = state.data.overrides.sections || [];
  const sec = secs[state.selectedSectionIdx];
  const course = getActiveCourse();
  if (!sec || !course || !course.measures.length) return;

  const barlines = getBaseBarlinesForCourse(course);
  const matching = course.measures.filter((m, idx) => {
    const mid = 0.5 * (barlines[idx] + barlines[idx + 1]);
    return mid >= sec.start_sec && mid < sec.end_sec;
  });
  if (matching.length < 2) {
    showToast("Need at least 2 bars inside the section to straighten BPM.");
    return;
  }

  const firstIdx = matching[0].measure_idx;
  const lastIdx = matching[matching.length - 1].measure_idx;
  const spanStart = barlines[firstIdx];
  const spanEnd = barlines[lastIdx + 1];
  const totalSpan = spanEnd - spanStart;

  const weights = [];
  let totalWeight = 0.0;
  for (let i = firstIdx; i <= lastIdx; i++) {
    const m = course.measures[i];
    const w = m.numerator / m.denominator;
    weights.push(w);
    totalWeight += w;
  }

  saveUndoPoint();
  let acc = 0.0;
  for (let offset = 0; offset < weights.length - 1; offset++) {
    acc += weights[offset];
    barlines[firstIdx + 1 + offset] = Number(
      (spanStart + (acc / totalWeight) * totalSpan).toFixed(4)
    );
  }
  state.data.overrides.barline_times = barlines;
  state.editSeq += 1;
  rebuildChart(false);
  const avgBpm = ((240.0 * totalWeight) / totalSpan).toFixed(1);
  showToast(`Straightened ${matching.length} bars to ${avgBpm} BPM (notes locked)`);
}

function syncSourceCheckboxesFromFollow(mode) {
  const d = document.getElementById("secSrcDrums");
  const v = document.getElementById("secSrcVocals");
  const g = document.getElementById("secSrcGuitar");
  const map = {
    drums: [true, false, false],
    vocals: [false, true, false],
    guitar: [false, false, true],
    drums_vocals: [true, true, false],
    hybrid: [true, false, true],
    vocals_guitar: [false, true, true],
    all_balanced: [true, true, true],
  };
  const [bd, bv, bg] = map[mode] || [false, false, false];
  d.checked = bd;
  v.checked = bv;
  g.checked = bg;
}

function followModeFromSourceCheckboxes() {
  const bd = document.getElementById("secSrcDrums").checked;
  const bv = document.getElementById("secSrcVocals").checked;
  const bg = document.getElementById("secSrcGuitar").checked;
  if (bd && bv && bg) return "all_balanced";
  if (bd && bv) return "drums_vocals";
  if (bd && bg) return "hybrid";
  if (bv && bg) return "vocals_guitar";
  if (bd) return "drums";
  if (bv) return "vocals";
  if (bg) return "guitar";
  return "auto";
}

function populateInspector() {
  const secs = state.data?.overrides?.sections || [];
  const sec = secs[state.selectedSectionIdx];
  if (!sec) return;
  const diff = document.getElementById("diffSelect").value;
  const diffRule = sec.difficulty_rules?.[diff] || null;
  const scopeLbl = document.getElementById("lblSecCourseScope");
  if (scopeLbl) {
    scopeLbl.textContent = diffRule ? `${diff.toUpperCase()} CUSTOM` : "ALL / DEFAULT";
  }
  const activeRule = diffRule || sec;

  document.getElementById("secRangeBadge").textContent = `${fmtTime(sec.start_sec)} - ${fmtTime(sec.end_sec)}`;
  document.getElementById("secLabel").value = sec.label || "";
  document.getElementById("secFollow").value = activeRule.follow_mode || "auto";
  syncSourceCheckboxesFromFollow(activeRule.follow_mode || "auto");
  document.getElementById("secShiftMs").value = sec.offset_shift_ms || 0;
  document.getElementById("secBpm").value = sec.bpm || "";
  document.getElementById("secBpmMult").value = String(sec.bpm_multiplier || 1);
  document.getElementById("secMeter").value = sec.numerator && sec.denominator ? `${sec.numerator}/${sec.denominator}` : "auto";
  document.getElementById("secDrumSens").value = activeRule.drum_sensitivity || 1.0;
  document.getElementById("lblDrumSens").textContent = Number(activeRule.drum_sensitivity || 1.0).toFixed(2);
  document.getElementById("secVocalSens").value = activeRule.vocal_sensitivity || 1.0;
  document.getElementById("lblVocalSens").textContent = Number(activeRule.vocal_sensitivity || 1.0).toFixed(2);
  document.getElementById("secMaxNps").value = activeRule.max_nps || "";
  document.getElementById("secBigNotes").checked = activeRule.enable_big_notes !== false;
  document.getElementById("secFoldRolls").checked = activeRule.fold_blasts_to_rolls !== false;
}

function buildSectionRuleFromInspector() {
  const maxNpsStr = document.getElementById("secMaxNps").value.trim();
  return {
    follow_mode: document.getElementById("secFollow").value,
    drum_sensitivity: parseFloat(document.getElementById("secDrumSens").value || "1"),
    vocal_sensitivity: parseFloat(document.getElementById("secVocalSens").value || "1"),
    max_nps: maxNpsStr ? parseFloat(maxNpsStr) : null,
    enable_big_notes: document.getElementById("secBigNotes").checked,
    fold_blasts_to_rolls: document.getElementById("secFoldRolls").checked,
  };
}

function readInspectorIntoSelected(targetDifficulties = null) {
  const secs = state.data?.overrides?.sections || [];
  const sec = secs[state.selectedSectionIdx];
  if (!sec) return;
  sec.label = document.getElementById("secLabel").value;
  sec.offset_shift_ms = parseFloat(document.getElementById("secShiftMs").value || "0");
  const bpmStr = document.getElementById("secBpm").value.trim();
  sec.bpm = bpmStr ? Math.max(30, Math.min(380, parseFloat(bpmStr))) : null;
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

  const rule = buildSectionRuleFromInspector();
  if (Array.isArray(targetDifficulties) && targetDifficulties.length > 0) {
    if (!sec.difficulty_rules) sec.difficulty_rules = {};
    for (const d of targetDifficulties) {
      if (rule.follow_mode === "auto" && rule.drum_sensitivity === 1 && rule.vocal_sensitivity === 1 && !rule.max_nps && rule.enable_big_notes) {
        delete sec.difficulty_rules[d];
      } else {
        sec.difficulty_rules[d] = { ...rule };
      }
    }
    return;
  }

  sec.follow_mode = rule.follow_mode;
  sec.drum_sensitivity = rule.drum_sensitivity;
  sec.vocal_sensitivity = rule.vocal_sensitivity;
  sec.max_nps = rule.max_nps;
  sec.enable_big_notes = rule.enable_big_notes;
  sec.fold_blasts_to_rolls = rule.fold_blasts_to_rolls;
}

const COURSE_LABELS = {
  easy: "Easy",
  normal: "Normal",
  hard: "Hard",
  oni: "Oni",
  ura: "Ura / Edit",
};

function syncCourseDropdown() {
  const courses = state.data?.chart?.courses || [];
  const sel = document.getElementById("diffSelect");
  const prev = sel.value;
  sel.innerHTML = "";
  for (const c of courses) {
    const opt = document.createElement("option");
    opt.value = c.difficulty;
    opt.textContent = COURSE_LABELS[c.difficulty] || c.difficulty;
    sel.appendChild(opt);
  }
  if (courses.some((c) => c.difficulty === prev)) {
    sel.value = prev;
  } else if (courses.length > 0) {
    sel.value = courses[courses.length - 1].difficulty;
  }
  const active = getActiveCourse();
  const starsInput = document.getElementById("courseStarsInput");
  if (starsInput && active) {
    starsInput.value = String(active.level_stars || 5);
  }
}

function renderDiagnostics() {
  syncCourseDropdown();
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
  if (state.rebuildInFlight) {
    state.pendingRebuild = {
      exportToSim: Boolean(state.pendingRebuild?.exportToSim || exportToSim),
    };
    return;
  }

  state.rebuildInFlight = true;
  const sentSeq = state.editSeq;
  state.data.overrides.preset = document.getElementById("presetSelect").value;
  state.data.overrides.global_offset_ms = parseFloat(document.getElementById("globalOffsetInput").value || "0");
  state.data.overrides.tap_latency_ms = parseFloat(document.getElementById("tapLatencyInput").value || "0");
  const endpoint = exportToSim ? "export" : "arrange";
  const url = state.importId
    ? `/api/imported/${state.importId}/${endpoint}`
    : `/api/songs/${state.songId}/${endpoint}`;

  try {
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
    const payload = await res.json();
    if (sentSeq === state.editSeq) {
      state.data = payload;
      document.getElementById("globalOffsetInput").value = state.data.overrides.global_offset_ms || 0;
      updateTapStatusBadge();
      renderSectionList();
      renderRegions(false);
      renderDiagnostics();
      updateSelectionUI();
      populateChartControls();
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
  } finally {
    state.rebuildInFlight = false;
    if (state.pendingRebuild) {
      const next = state.pendingRebuild;
      state.pendingRebuild = null;
      await rebuildChart(next.exportToSim);
    }
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
      state.data.overrides.barline_times = [];
      state.editSeq += 1;
      rebuildChart(false);
      showToast(`Applied ${state.sessionTaps.length} tapped downbeats!`);
    }
  }
}

function recordNumpadTap() {
  if (!wavesurfer || !state.data) return;
  const rawT = getCurrentTimeSec();
  const flash = document.getElementById("tapFlash");
  flash.classList.add("active");
  setTimeout(() => flash.classList.remove("active"), 130);
  playTapTickSound();

  const latSec = parseFloat(document.getElementById("tapLatencyInput").value || "0") / 1000.0;
  const t = Math.max(0.0, Number((rawT + latSec).toFixed(4)));

  if (state.sessionTaps.length > 0 && (t - state.sessionTaps[state.sessionTaps.length - 1]) < 0.30) {
    return;
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
  state.sourceTjaPath = null;
  state.activeProjectName = songId;
  state.lastRegionKey = "";
  state.viewTime = 0;
  state.gridAnchorIdx = null;
  state.gridPreviewTimes = null;
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
  clearSelection();
  updateUndoButtons();
  state.selectedSectionIdx = 0;

  const presetSelect = document.getElementById("presetSelect");
  if (presetSelect) {
    presetSelect.value = state.data.overrides.preset || "taiko_auto";
  }
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
  populateChartControls();
  initWaveSurfer(`/api/songs/${songId}/audio?stem=mix`);
}

async function loadImportedTja(importId, sourcePath = null) {
  state.importId = importId;
  state.songId = importId;
  state.sourceTjaPath = sourcePath;
  state.lastRegionKey = "";
  state.viewTime = 0;
  state.gridAnchorIdx = null;
  state.gridPreviewTimes = null;
  document.body.classList.add("imported-tja");
  const response = await fetch(`/api/imported/${importId}`);
  if (!response.ok) {
    showToast(`Could not load imported TJA: ${importId}`);
    return;
  }
  state.data = await response.json();
  state.sourceTjaPath = state.sourceTjaPath || state.data?.analysis_summary?.source_tja_path || null;
  state.activeProjectName = state.data?.analysis_summary?.title || "imported-chart";
  state.referenceHits = [];
  state.editUndo = [];
  state.editRedo = [];
  clearSelection();
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

  const scroll = document.getElementById("laneScroll");
  if (scroll) {
    scroll.max = String(getChartDuration() || 1);
    scroll.value = "0";
  }

  if (hasAudio) {
    initWaveSurfer(`/api/imported/${importId}/audio`);
  } else {
    if (wavesurfer) {
      wavesurfer.destroy();
      wavesurfer = null;
    }
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
  const curT = getCurrentTimeSec();
  const dur = getChartDuration();
  document.getElementById("timeReadout").textContent = `${fmtTime(curT)} / ${fmtTime(dur)}`;

  const scroll = document.getElementById("laneScroll");
  if (scroll && !state.scrubbingLane) {
    const maxVal = Math.max(0.01, dur);
    if (Math.abs(Number(scroll.max) - maxVal) > 0.05) {
      scroll.max = String(maxVal);
    }
    scroll.value = String(Math.min(maxVal, curT));
  }

  if (state.loopSection && wavesurfer && wavesurfer.isPlaying()) {
    const sec = state.data?.overrides?.sections?.[state.selectedSectionIdx];
    if (sec && curT >= sec.end_sec) {
      seekToSec(sec.start_sec);
    }
  }

  state.hitScreenItems = [];
  state.slotScreenItems = [];
  state.rollScreenItems = [];
  state.measureScreenItems = [];
  state.barlineScreenItems = [];

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
    const snapOn = document.getElementById("snapToggle").checked;
    const gridChoice = document.getElementById("editGrid").value;
    const previewTimes = state.gridPreviewTimes;

    const mStartAt = (m, idx) => {
      if (previewTimes && previewTimes.length === course.measures.length + 1) {
        return previewTimes[idx] + offsetDelta;
      }
      return state.importId ? m.start_sec : m.start_sec + offsetDelta;
    };
    const mEndAt = (m, idx) => {
      if (previewTimes && previewTimes.length === course.measures.length + 1) {
        return previewTimes[idx + 1] + offsetDelta;
      }
      return state.importId ? m.end_sec : m.end_sec + offsetDelta;
    };

    for (let idx = 0; idx < course.measures.length; idx++) {
      const m = course.measures[idx];
      const mStart = mStartAt(m, idx);
      const mEnd = mEndAt(m, idx);
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

      const L = m.notes.length;
      const mDur = mEnd - mStart;
      const subdivisions = snapOn
        ? (gridChoice === "chart" ? L : Math.max(1, Math.round(m.numerator * Number(gridChoice) / m.denominator)))
        : 64;
      const stepPx = (xEnd - xBar) / subdivisions;
      const beatEvery = Math.max(1, Math.round(subdivisions / Math.max(1, m.numerator)));

      if (stepPx >= 6) {
        for (let s = 1; s < subdivisions; s++) {
          const sx = xBar + s * stepPx;
          const isBeat = (s % beatEvery) === 0;
          ctx.strokeStyle = isBeat ? "#2b3342" : "#1c222e";
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(sx, 42);
          ctx.lineTo(sx, ghostY - 38);
          ctx.stroke();
        }
      } else {
        const beatStepPx = (xEnd - xBar) / Math.max(1, m.numerator);
        for (let b = 1; b < m.numerator; b++) {
          const bx = xBar + b * beatStepPx;
          ctx.strokeStyle = "#2b3342";
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(bx, 42);
          ctx.lineTo(bx, ghostY - 38);
          ctx.stroke();
        }
      }

      const isPinned = state.gridAnchorIdx === idx;
      const isGridTool = state.activeTool === "grid";
      ctx.strokeStyle = isPinned
        ? "#3ddc84"
        : isGridTool
          ? "#ffd06b"
          : (state.data.overrides.custom_downbeats?.length >= 2 || state.data.overrides.barline_times?.length)
            ? "#8a6d29"
            : "#3a4254";
      ctx.lineWidth = isPinned ? 3 : 2;
      ctx.beginPath();
      ctx.moveTo(xBar, 32);
      ctx.lineTo(xBar, H - 12);
      ctx.stroke();

      if (isGridTool) {
        ctx.fillStyle = isPinned ? "#3ddc84" : "#ffd06b";
        ctx.fillRect(xBar - 4, 24, 8, 12);
      }
      state.barlineScreenItems.push({ barIdx: idx, x: xBar, time: mStart });

      const displayBpm = previewTimes
        ? (240.0 * m.numerator) / (m.denominator * Math.max(0.05, mDur))
        : m.bpm;
      ctx.fillStyle = isPinned ? "#3ddc84" : "#8b95a8";
      ctx.font = "11px monospace";
      ctx.fillText(
        `#${m.measure_idx} ${Math.round(displayBpm)}bpm ${m.numerator}/${m.denominator}${isPinned ? " [PIN]" : ""}`,
        xBar + 6,
        18
      );

      for (let s = 0; s < L; s++) {
        const st = mStart + (s / L) * mDur;
        const sx = hitX + (st - curT) * pxPerSec;
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

    if (course.measures.length > 0) {
      const lastIdx = course.measures.length;
      const endT = mEndAt(course.measures[lastIdx - 1], lastIdx - 1);
      if (endT >= minVisibleT && endT <= maxVisibleT) {
        const xLast = hitX + (endT - curT) * pxPerSec;
        const isPinnedEnd = state.gridAnchorIdx === lastIdx;
        if (state.activeTool === "grid") {
          ctx.strokeStyle = isPinnedEnd ? "#3ddc84" : "#ffd06b";
          ctx.lineWidth = isPinnedEnd ? 3 : 2;
          ctx.beginPath();
          ctx.moveTo(xLast, 32);
          ctx.lineTo(xLast, H - 12);
          ctx.stroke();
          ctx.fillStyle = isPinnedEnd ? "#3ddc84" : "#ffd06b";
          ctx.fillRect(xLast - 4, 24, 8, 12);
        }
        state.barlineScreenItems.push({ barIdx: lastIdx, x: xLast, time: endT });
      }
    }

    const previewPos = (mIdx, sIdx, grid, origTime) => {
      if (!previewTimes || previewTimes.length !== course.measures.length + 1) return origTime;
      const start = previewTimes[mIdx] + offsetDelta;
      const end = previewTimes[mIdx + 1] + offsetDelta;
      return start + (sIdx / grid) * (end - start);
    };

    for (const r of rolls) {
      const rStart = previewPos(r.mIdx, r.sIdx, r.grid, r.start);
      const rEnd = previewPos(r.endMIdx, r.endSIdx, r.endGrid, r.end);
      if (rEnd < minVisibleT || rStart > maxVisibleT) continue;
      const x1 = hitX + (rStart - curT) * pxPerSec;
      const x2 = hitX + (rEnd - curT) * pxPerSec;
      const isSelected = state.selectedRollKeys.has(r.startKey);
      const color = r.balloon ? "#be68ec" : "#f5b935";
      const barH = r.big ? 34 : 28;
      const barY = chartY - barH / 2;
      const barW = Math.max(10, x2 - x1);

      ctx.fillStyle = color;
      ctx.globalAlpha = isSelected ? 1 : 0.84;
      ctx.fillRect(x1, barY, barW, barH);
      ctx.globalAlpha = 1;
      if (isSelected) {
        ctx.strokeStyle = "#3ddc84";
        ctx.lineWidth = 2.5;
        ctx.strokeRect(x1, barY, barW, barH);
        ctx.fillStyle = "#ffffff";
        ctx.fillRect(x1 - 2, barY - 2, 4, barH + 4);
        ctx.fillRect(x1 + barW - 2, barY - 2, 4, barH + 4);
      }
      ctx.fillStyle = "#101319";
      ctx.font = "bold 11px sans-serif";
      ctx.fillText(r.balloon ? "BALLOON" : r.big ? "BIG ROLL" : "ROLL", x1 + 6, chartY + 4);

      state.rollScreenItems.push({
        x1,
        x2: Math.max(x1 + 10, x2),
        y: chartY,
        startKey: r.startKey,
        endKey: r.endKey,
        roll: r,
      });
    }

    for (let i = notes.length - 1; i >= 0; i--) {
      const n = notes[i];
      const nTime = previewPos(n.mIdx, n.sIdx, n.grid, n.time);
      if (nTime < minVisibleT || nTime > maxVisibleT) continue;
      const x = hitX + (nTime - curT) * pxPerSec;
      const isBig = n.code === 3 || n.code === 4;
      const rad = isBig ? 20 : 15;
      const isSelected = state.selectedKeys.has(n.key);

      ctx.fillStyle = NOTE_COLORS[n.code];
      ctx.beginPath();
      ctx.arc(x, chartY, rad, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = isSelected ? "#3ddc84" : "#ffffff";
      ctx.lineWidth = isSelected ? 4 : 2.5;
      ctx.stroke();

      if (isSelected) {
        ctx.strokeStyle = "rgba(61, 220, 132, 0.55)";
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(x, chartY, rad + 5, 0, Math.PI * 2);
        ctx.stroke();
      }

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

    if (state.drag) {
      const drag = state.drag;
      if (drag.kind === "barline" && state.gridStatusText) {
        ctx.fillStyle = "rgba(17, 22, 28, 0.92)";
        ctx.strokeStyle = "#ffd06b";
        ctx.lineWidth = 1;
        const bx = Math.min(W - 250, Math.max(12, drag.currentX + 12));
        ctx.fillRect(bx, 42, 236, 24);
        ctx.strokeRect(bx, 42, 236, 24);
        ctx.fillStyle = "#ffd06b";
        ctx.font = "11px monospace";
        ctx.fillText(state.gridStatusText, bx + 8, 58);
      } else if (drag.kind === "marquee" && drag.moved) {
        const mx1 = Math.min(drag.startX, drag.currentX);
        const mx2 = Math.max(drag.startX, drag.currentX);
        const my1 = Math.max(34, Math.min(drag.startY, drag.currentY));
        const my2 = Math.min(ghostY - 40, Math.max(drag.startY, drag.currentY, my1 + 24));
        ctx.fillStyle = "rgba(44, 168, 232, 0.16)";
        ctx.fillRect(mx1, my1, mx2 - mx1, my2 - my1);
        ctx.strokeStyle = "#2ca8e8";
        ctx.lineWidth = 1.5;
        ctx.setLineDash([4, 3]);
        ctx.strokeRect(mx1, my1, mx2 - mx1, my2 - my1);
        ctx.setLineDash([]);
      } else if (drag.currentPoint) {
        const point = drag.currentPoint;
        const x = hitX + (point.time - curT) * pxPerSec;
        if (
          (drag.kind === "add" && ["5", "6", "7"].includes(state.activeTool)) ||
          drag.kind === "roll-resize"
        ) {
          const startT = drag.kind === "roll-resize"
            ? (drag.edge === "start" ? point.time : drag.roll.roll.start)
            : drag.startPoint.time;
          const endT = drag.kind === "roll-resize"
            ? (drag.edge === "end" ? point.time : drag.roll.roll.end)
            : point.time;
          const px1 = hitX + (Math.min(startT, endT) - curT) * pxPerSec;
          const px2 = hitX + (Math.max(startT, endT) - curT) * pxPerSec;
          const isBalloon = drag.kind === "roll-resize" ? drag.roll.roll.balloon : state.activeTool === "7";
          ctx.fillStyle = isBalloon ? "rgba(190, 104, 236, 0.35)" : "rgba(245, 185, 53, 0.35)";
          ctx.strokeStyle = "#ffffff";
          ctx.lineWidth = 1.5;
          ctx.fillRect(px1, chartY - 14, Math.max(8, px2 - px1), 28);
          ctx.strokeRect(px1, chartY - 14, Math.max(8, px2 - px1), 28);
        } else {
          ctx.strokeStyle = "#ffffff";
          ctx.setLineDash([5, 4]);
          ctx.beginPath();
          ctx.moveTo(x, 42);
          ctx.lineTo(x, ghostY - 38);
          ctx.stroke();
          ctx.setLineDash([]);
        }
      }
    }
  }

  requestAnimationFrame(drawLoop);
}

function subdivisionsForMeasure(measure) {
  const snap = document.getElementById("snapToggle").checked;
  const gridValue = document.getElementById("editGrid").value;
  if (!snap) return 64;
  const nativeGrid = measure.grid ?? measure.notes?.length ?? 16;
  return gridValue === "chart"
    ? nativeGrid
    : Math.max(1, Math.round(measure.numerator * Number(gridValue) / measure.denominator));
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
  const position = Math.max(0, Math.min(1, (x - measure.x1) / span));
  let subdivisions = subdivisionsForMeasure(measure);
  let numerator = Math.round(position * subdivisions);

  if (numerator >= subdivisions) {
    const nextMeasure = measures.find((item) => item.mIdx === measure.mIdx + 1);
    if (nextMeasure) {
      measure = nextMeasure;
      subdivisions = subdivisionsForMeasure(measure);
      numerator = 0;
    } else {
      numerator = subdivisions - 1;
    }
  }
  numerator = Math.max(0, numerator);
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
  const course = getActiveCourse();
  if (!course || !course.measures.length) return null;
  const offset = state.importId ? 0.0 : (state.data?.overrides?.global_offset_ms || 0) / 1000.0;
  const measure = course.measures.find(
    (m) => time >= m.start_sec + offset && time < m.end_sec + offset
  );
  if (!measure) return null;
  const start = measure.start_sec + offset;
  const end = measure.end_sec + offset;
  const position = (time - start) / (end - start);
  const subdivisions = subdivisionsForMeasure(measure);
  const numerator = Math.max(0, Math.min(subdivisions - 1, Math.round(position * subdivisions)));
  const snappedPosition = numerator / subdivisions;
  return {
    mIdx: measure.measure_idx,
    numerator,
    denominator: subdivisions,
    position: snappedPosition,
    time: start + snappedPosition * (end - start),
    key: editKey(measure.measure_idx, numerator, subdivisions),
  };
}

function endpointFromRoll(rollItem, isEnd) {
  const r = rollItem.roll;
  const mIdx = isEnd ? r.endMIdx : r.mIdx;
  const sIdx = isEnd ? r.endSIdx : r.sIdx;
  const grid = isEnd ? r.endGrid : r.grid;
  const time = isEnd ? r.end : r.start;
  return {
    mIdx,
    numerator: sIdx,
    denominator: grid,
    position: sIdx / grid,
    time,
    key: editKey(mIdx, sIdx, grid),
  };
}

function shiftCoursePosition(course, mIdx, sIdx, grid, stepDelta) {
  if (!course || !course.measures[mIdx] || stepDelta === 0) return null;
  const offset = state.importId ? 0.0 : (state.data?.overrides?.global_offset_ms || 0) / 1000.0;
  let curM = mIdx;
  let curNum = sIdx;
  let curDen = grid;
  const steps = Math.abs(stepDelta);
  const dir = stepDelta > 0 ? 1 : -1;

  for (let step = 0; step < steps; step++) {
    const measure = course.measures[curM];
    const stepDen = subdivisionsForMeasure(measure);
    let workDen = lcm(curDen, stepDen);
    if (workDen > 256) {
      curNum = Math.round((curNum / curDen) * 64);
      curDen = 64;
      workDen = lcm(curDen, stepDen);
      if (workDen > 256) workDen = stepDen;
    }
    const curSlot = Math.round((curNum / curDen) * workDen);
    const deltaSlots = dir * Math.max(1, Math.round(workDen / stepDen));
    const nextSlot = curSlot + deltaSlots;

    if (nextSlot >= 0 && nextSlot < workDen) {
      curNum = nextSlot;
      curDen = workDen;
    } else if (nextSlot >= workDen) {
      if (curM + 1 >= course.measures.length) return null;
      const remFrac = (nextSlot - workDen) / workDen;
      curM += 1;
      const nextStepDen = subdivisionsForMeasure(course.measures[curM]);
      const nextDen = lcm(workDen, nextStepDen) <= 256 ? lcm(workDen, nextStepDen) : nextStepDen;
      curDen = nextDen;
      curNum = Math.min(nextDen - 1, Math.max(0, Math.round(remFrac * nextDen)));
    } else {
      if (curM - 1 < 0) return null;
      const remFrac = 1 + nextSlot / workDen;
      curM -= 1;
      const prevStepDen = subdivisionsForMeasure(course.measures[curM]);
      const prevDen = lcm(workDen, prevStepDen) <= 256 ? lcm(workDen, prevStepDen) : prevStepDen;
      curDen = prevDen;
      curNum = Math.min(prevDen - 1, Math.max(0, Math.round(remFrac * prevDen)));
    }
  }

  const d = gcd(curNum, curDen);
  const finalNum = curNum / d;
  const finalDen = curDen / d;
  const targetM = course.measures[curM];
  const mStart = targetM.start_sec + offset;
  const mEnd = targetM.end_sec + offset;
  const position = finalNum / finalDen;
  return {
    mIdx: curM,
    numerator: finalNum,
    denominator: finalDen,
    position,
    time: mStart + position * (mEnd - mStart),
    key: editKey(curM, finalNum, finalDen),
  };
}

function findBarlineAt(x) {
  return state.barlineScreenItems.find((item) => Math.abs(item.x - x) <= BARLINE_HIT_PX);
}

function findNoteAt(x, y) {
  return state.hitScreenItems.find((item) => Math.hypot(item.x - x, item.y - y) <= item.r);
}

function findRollAt(x, y) {
  return state.rollScreenItems.find((item) => (
    y >= item.y - 20 && y <= item.y + 20 && x >= item.x1 - 6 && x <= item.x2 + 6
  ));
}

function setSingleEdit(point, code, sourceKey = null) {
  if (!state.data || !point) return;
  if (!state.data.overrides.note_edits) {
    state.data.overrides.note_edits = {};
  }
  const existing = state.hitScreenItems.find((item) => (
    item.mIdx === point.mIdx && Math.abs(item.sIdx / item.grid - point.position) < 0.0001
  ));
  if (existing && existing.key !== sourceKey && code !== 0 && sourceKey) {
    showToast("That chart position already has a note.");
    return;
  }
  saveUndoPoint();
  const patch = {};
  if (sourceKey && sourceKey !== point.key) {
    patch[sourceKey] = 0;
  }
  if (existing && existing.key !== point.key && !sourceKey) {
    patch[existing.key] = 0;
  }
  patch[point.key] = code;
  Object.assign(state.data.overrides.note_edits, patch);
  applyOptimisticNotePatch(patch);
  if (code === 0) {
    clearSelection();
  } else {
    setSingleNoteSelection(point.key);
  }
  state.editSeq += 1;
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
  const offset = state.importId ? 0.0 : (state.data.overrides.global_offset_ms || 0) / 1000;
  const overlaps = rolls.some((roll) => {
    if (
      existingRoll &&
      roll.mIdx === existingRoll.roll.mIdx &&
      roll.sIdx === existingRoll.roll.sIdx
    ) return false;
    return start.time < roll.end && end.time > roll.start;
  });
  if (overlaps) {
    showToast("Rolls cannot overlap. Move the new span outside the existing roll.");
    return;
  }

  saveUndoPoint();
  if (!state.data.overrides.note_edits) {
    state.data.overrides.note_edits = {};
  }
  const patch = {};
  // Explicitly erase any notes between start and end (including endpoints)
  for (const measure of course.measures) {
    const measureStart = measure.start_sec + offset;
    const duration = measure.end_sec - measure.start_sec;
    for (let slot = 0; slot < measure.notes.length; slot++) {
      const time = measureStart + (slot / measure.notes.length) * duration;
      if (time >= start.time - 0.005 && time <= end.time + 0.005) {
        patch[editKey(measure.measure_idx, slot, measure.notes.length)] = 0;
      }
    }
  }
  if (existingRoll) {
    patch[existingRoll.startKey] = 0;
    patch[existingRoll.endKey] = 0;
  }
  patch[start.key] = code;
  patch[end.key] = 8;
  Object.assign(state.data.overrides.note_edits, patch);
  applyOptimisticNotePatch(patch);

  const difficulty = document.getElementById("diffSelect").value;
  if (code === 7) {
    if (!state.data.overrides.balloon_counts_by_course) {
      state.data.overrides.balloon_counts_by_course = {};
    }
    const counts = getEffectiveBalloonCounts(course, difficulty);
    if (!existingRoll) {
      const insertIdx = rolls.filter((roll) => roll.balloon && roll.start < start.time - 1e-6).length;
      counts.splice(insertIdx, 0, Number(document.getElementById("balloonCount").value || "10"));
      state.data.overrides.balloon_counts_by_course[difficulty] = counts;
    } else if (existingRoll.roll.balloon) {
      const oldIdx = orderedBalloonKeys(course).indexOf(existingRoll.startKey);
      const otherBalloons = rolls.filter(
        (roll) => roll.balloon && !(roll.mIdx === existingRoll.roll.mIdx && roll.sIdx === existingRoll.roll.sIdx)
      );
      const newIdx = otherBalloons.filter((roll) => roll.start < start.time - 1e-6).length;
      if (oldIdx >= 0 && oldIdx < counts.length && oldIdx !== newIdx) {
        const [movedCount] = counts.splice(oldIdx, 1);
        counts.splice(newIdx, 0, movedCount);
        state.data.overrides.balloon_counts_by_course[difficulty] = counts;
      }
    }
  }
  setSingleRollSelection(start.key);
  state.editSeq += 1;
  rebuildChart(false);
}

function removeRoll(item) {
  if (!state.data || !item) return;
  saveUndoPoint();
  if (!state.data.overrides.note_edits) {
    state.data.overrides.note_edits = {};
  }
  const patch = {
    [item.startKey]: 0,
    [item.endKey]: 0,
  };
  Object.assign(state.data.overrides.note_edits, patch);
  applyOptimisticNotePatch(patch);
  if (item.roll.balloon) {
    const course = getActiveCourse();
    const difficulty = document.getElementById("diffSelect").value;
    if (!state.data.overrides.balloon_counts_by_course) {
      state.data.overrides.balloon_counts_by_course = {};
    }
    const counts = getEffectiveBalloonCounts(course, difficulty);
    const index = orderedBalloonKeys(course).indexOf(item.startKey);
    if (index >= 0 && index < counts.length) counts.splice(index, 1);
    state.data.overrides.balloon_counts_by_course[difficulty] = counts;
  }
  clearSelection();
  state.editSeq += 1;
  rebuildChart(false);
}

function deleteSelection() {
  if (!state.data || !hasSelection()) return;
  const course = getActiveCourse();
  if (!course) return;
  const { notes, rolls } = computeFlattenedEvents(course);
  const selectedNotes = notes.filter((n) => state.selectedKeys.has(n.key));
  const selectedRolls = rolls.filter((r) => state.selectedRollKeys.has(r.startKey));
  if (selectedNotes.length === 0 && selectedRolls.length === 0) {
    clearSelection();
    return;
  }

  saveUndoPoint();
  if (!state.data.overrides.note_edits) {
    state.data.overrides.note_edits = {};
  }
  const patch = {};
  for (const n of selectedNotes) {
    patch[n.key] = 0;
  }
  for (const r of selectedRolls) {
    patch[r.startKey] = 0;
    patch[r.endKey] = 0;
  }

  const deletedBalloons = selectedRolls.filter((r) => r.balloon);
  if (deletedBalloons.length > 0) {
    const difficulty = document.getElementById("diffSelect").value;
    if (!state.data.overrides.balloon_counts_by_course) {
      state.data.overrides.balloon_counts_by_course = {};
    }
    const counts = getEffectiveBalloonCounts(course, difficulty);
    const balloonOrder = orderedBalloonKeys(course);
    const removeIndices = deletedBalloons
      .map((r) => balloonOrder.indexOf(r.startKey))
      .filter((idx) => idx >= 0)
      .sort((a, b) => b - a);
    for (const idx of removeIndices) {
      if (idx < counts.length) counts.splice(idx, 1);
    }
    state.data.overrides.balloon_counts_by_course[difficulty] = counts;
  }

  Object.assign(state.data.overrides.note_edits, patch);
  applyOptimisticNotePatch(patch);
  clearSelection();
  state.editSeq += 1;
  rebuildChart(false);
}

function copySelection() {
  if (!state.data || !hasSelection()) return;
  const course = getActiveCourse();
  if (!course) return;
  const { notes, rolls } = computeFlattenedEvents(course);
  const selectedNotes = notes.filter((n) => state.selectedKeys.has(n.key));
  const selectedRolls = rolls.filter((r) => state.selectedRollKeys.has(r.startKey));
  if (!selectedNotes.length && !selectedRolls.length) return;

  const anchorUnits = Math.min(
    ...selectedNotes.map((n) => n.mIdx + n.sIdx / n.grid),
    ...selectedRolls.map((r) => r.mIdx + r.sIdx / r.grid)
  );
  const difficulty = document.getElementById("diffSelect").value;
  const counts = getEffectiveBalloonCounts(course, difficulty);
  const balloonOrder = orderedBalloonKeys(course);

  state.clipboard = {
    notes: selectedNotes.map((n) => ({
      relUnit: n.mIdx + n.sIdx / n.grid - anchorUnits,
      code: n.code,
      grid: n.grid,
    })),
    rolls: selectedRolls.map((r) => {
      const bIdx = r.balloon ? balloonOrder.indexOf(r.startKey) : -1;
      return {
        relStart: r.mIdx + r.sIdx / r.grid - anchorUnits,
        relEnd: r.endMIdx + r.endSIdx / r.endGrid - anchorUnits,
        code: r.balloon ? 7 : r.big ? 6 : 5,
        grid: r.grid,
        balloonCount: bIdx >= 0 ? (counts[bIdx] ?? 10) : 10,
      };
    }),
  };
  updateSelectionUI();
  showToast(`Copied ${selectedNotes.length} note(s) and ${selectedRolls.length} roll(s)`);
}

function unitToMeasureFraction(course, totalUnit, preferredGrid) {
  const mIdx = Math.floor(totalUnit + 1e-7);
  if (mIdx < 0 || mIdx >= course.measures.length) return null;
  let frac = totalUnit - mIdx;
  if (frac < 0) frac = 0;
  const m = course.measures[mIdx];
  const snapGrid = subdivisionsForMeasure(m);
  const denom = Math.min(256, Math.max(snapGrid, preferredGrid || snapGrid));
  let num = Math.round(frac * denom);
  let targetM = mIdx;
  if (num >= denom) {
    targetM += 1;
    num = 0;
  }
  if (targetM >= course.measures.length) return null;
  const d = gcd(num, denom);
  const rn = num / d;
  const rd = denom / d;
  const destM = course.measures[targetM];
  const offset = state.importId ? 0.0 : (state.data?.overrides?.global_offset_ms || 0) / 1000.0;
  const start = destM.start_sec + offset;
  const end = destM.end_sec + offset;
  return {
    mIdx: targetM,
    numerator: rn,
    denominator: rd,
    time: start + (rn / rd) * (end - start),
    key: editKey(targetM, rn, rd),
  };
}

function pasteSelection() {
  if (!state.data || !state.clipboard) return;
  const course = getActiveCourse();
  if (!course || !course.measures.length) return;
  const curT = getCurrentTimeSec();
  let anchorPt = pointForTime(curT);
  if (!anchorPt) {
    const offset = state.importId ? 0.0 : (state.data?.overrides?.global_offset_ms || 0) / 1000.0;
    const firstStart = course.measures[0].start_sec + offset;
    const mIdx = curT <= firstStart ? 0 : course.measures.length - 1;
    anchorPt = { mIdx, numerator: 0, denominator: 16, position: 0 };
  }
  const baseUnit = anchorPt.mIdx + anchorPt.position;

  const patch = {};
  const newNoteKeys = new Set();
  const newRollKeys = new Set();
  const pastedBalloons = [];
  let truncated = 0;

  for (const item of state.clipboard.notes) {
    const pos = unitToMeasureFraction(course, baseUnit + item.relUnit, item.grid);
    if (!pos) {
      truncated += 1;
      continue;
    }
    patch[pos.key] = item.code;
    newNoteKeys.add(pos.key);
  }

  for (const r of state.clipboard.rolls) {
    const pStart = unitToMeasureFraction(course, baseUnit + r.relStart, r.grid);
    const pEnd = unitToMeasureFraction(course, baseUnit + r.relEnd, r.grid);
    if (!pStart || !pEnd || pEnd.time <= pStart.time) {
      truncated += 1;
      continue;
    }
    patch[pStart.key] = r.code;
    patch[pEnd.key] = 8;
    newRollKeys.add(pStart.key);
    if (r.code === 7) {
      pastedBalloons.push({ time: pStart.time, count: r.balloonCount });
    }
  }

  if (!Object.keys(patch).length) {
    showToast("Paste target is outside the chart.");
    return;
  }

  saveUndoPoint();
  if (!state.data.overrides.note_edits) {
    state.data.overrides.note_edits = {};
  }
  Object.assign(state.data.overrides.note_edits, patch);
  applyOptimisticNotePatch(patch);

  if (pastedBalloons.length > 0) {
    const difficulty = document.getElementById("diffSelect").value;
    if (!state.data.overrides.balloon_counts_by_course) {
      state.data.overrides.balloon_counts_by_course = {};
    }
    const existingCounts = getEffectiveBalloonCounts(course, difficulty);
    const { rolls } = computeFlattenedEvents(course);
    const existingBalloons = rolls.filter((r) => r.balloon);
    const merged = existingBalloons.map((r, idx) => ({
      time: r.start,
      count: existingCounts[idx] ?? 10,
      isNew: false,
    }));
    for (const pb of pastedBalloons) {
      merged.push({ time: pb.time, count: pb.count, isNew: true });
    }
    merged.sort((a, b) => a.time - b.time);
    state.data.overrides.balloon_counts_by_course[difficulty] = merged.map((item) => item.count);
  }

  state.selectedKeys = newNoteKeys;
  state.selectedRollKeys = newRollKeys;
  updateSelectionUI();
  state.editSeq += 1;
  rebuildChart(false);
  showToast(truncated ? `Pasted selection (${truncated} item(s) past song end skipped)` : "Pasted selection");
}

function straightenGridRange() {
  if (!state.data) return;
  const course = getActiveCourse();
  if (!course || !course.measures.length) return;
  const barlines = getBaseBarlinesForCourse(course);

  let startBar = null;
  let endBar = null;
  if (state.gridAnchorIdx !== null) {
    const offset = state.importId ? 0.0 : (state.data.overrides.global_offset_ms || 0) / 1000.0;
    const playAudioT = getCurrentTimeSec() - offset;
    let nearestBar = 0;
    let bestDist = Infinity;
    for (let i = 0; i < barlines.length; i++) {
      const d = Math.abs(barlines[i] - playAudioT);
      if (d < bestDist) {
        bestDist = d;
        nearestBar = i;
      }
    }
    startBar = Math.min(state.gridAnchorIdx, nearestBar);
    endBar = Math.max(state.gridAnchorIdx, nearestBar);
  } else if (hasSelection()) {
    const { notes, rolls } = computeFlattenedEvents(course);
    const indices = [];
    for (const n of notes) if (state.selectedKeys.has(n.key)) indices.push(n.mIdx);
    for (const r of rolls) if (state.selectedRollKeys.has(r.startKey)) indices.push(r.mIdx, r.endMIdx);
    if (indices.length) {
      startBar = Math.min(...indices);
      endBar = Math.max(...indices) + 1;
    }
  }

  if (startBar === null || endBar === null || endBar - startBar < 2) {
    showToast("Pin a barline and move playhead across at least 2 bars (or select >= 2 bars).");
    return;
  }

  const spanStart = barlines[startBar];
  const spanEnd = barlines[endBar];
  const totalSpan = spanEnd - spanStart;
  const weights = [];
  let totalWeight = 0.0;
  for (let i = startBar; i < endBar; i++) {
    const m = course.measures[i];
    const w = m.numerator / m.denominator;
    weights.push(w);
    totalWeight += w;
  }

  saveUndoPoint();
  let acc = 0.0;
  for (let offset = 0; offset < weights.length - 1; offset++) {
    acc += weights[offset];
    barlines[startBar + 1 + offset] = Number(
      (spanStart + (acc / totalWeight) * totalSpan).toFixed(4)
    );
  }
  state.data.overrides.barline_times = barlines;
  state.editSeq += 1;
  rebuildChart(false);
  const bpm = ((240.0 * totalWeight) / totalSpan).toFixed(1);
  showToast(`Straightened bars #${startBar}–#${endBar - 1} to ${bpm} BPM`);
}

function flipSelectionColors() {
  if (!state.data || state.selectedKeys.size === 0) return;
  const course = getActiveCourse();
  if (!course) return;
  const { notes } = computeFlattenedEvents(course);
  const swap = { 1: 2, 2: 1, 3: 4, 4: 3 };
  const selectedNotes = notes.filter((n) => state.selectedKeys.has(n.key) && swap[n.code]);
  if (selectedNotes.length === 0) return;

  saveUndoPoint();
  if (!state.data.overrides.note_edits) {
    state.data.overrides.note_edits = {};
  }
  const patch = {};
  for (const n of selectedNotes) {
    patch[n.key] = swap[n.code];
  }
  Object.assign(state.data.overrides.note_edits, patch);
  applyOptimisticNotePatch(patch);
  state.editSeq += 1;
  rebuildChart(false);
}

function nudgeSelection(stepDelta) {
  if (!state.data || !hasSelection() || stepDelta === 0) return;
  const course = getActiveCourse();
  if (!course) return;

  const { notes, rolls } = computeFlattenedEvents(course);
  const selectedNotes = notes.filter((n) => state.selectedKeys.has(n.key));
  const selectedRolls = rolls.filter((r) => state.selectedRollKeys.has(r.startKey));
  if (selectedNotes.length === 0 && selectedRolls.length === 0) return;

  const movedNotes = [];
  for (const n of selectedNotes) {
    const dest = shiftCoursePosition(course, n.mIdx, n.sIdx, n.grid, stepDelta);
    if (!dest) {
      showToast("Cannot move selection past the start or end of the chart.");
      return;
    }
    movedNotes.push({ orig: n, dest });
  }

  const movedRolls = [];
  for (const r of selectedRolls) {
    const newStart = shiftCoursePosition(course, r.mIdx, r.sIdx, r.grid, stepDelta);
    const newEnd = shiftCoursePosition(course, r.endMIdx, r.endSIdx, r.endGrid, stepDelta);
    if (!newStart || !newEnd || newEnd.time <= newStart.time) {
      showToast("Cannot move roll past the start or end of the chart.");
      return;
    }
    movedRolls.push({
      orig: r,
      newStart,
      newEnd,
      code: r.balloon ? 7 : r.big ? 6 : 5,
    });
  }

  const targetKeys = new Set();
  for (const item of movedNotes) {
    if (targetKeys.has(item.dest.key)) {
      showToast("Selection spacing is finer than the current grid; switch Grid to 1/32 or 1/64.");
      return;
    }
    targetKeys.add(item.dest.key);
  }

  const unselectedNotes = notes.filter((n) => !state.selectedKeys.has(n.key));
  for (const item of movedNotes) {
    const hitsUnselected = unselectedNotes.some(
      (u) => u.mIdx === item.dest.mIdx && Math.abs(u.sIdx / u.grid - item.dest.position) < 0.0001
    );
    if (hitsUnselected) {
      showToast("Target slot already contains an unselected note.");
      return;
    }
  }

  const unselectedRolls = rolls.filter((r) => !state.selectedRollKeys.has(r.startKey));
  for (const mr of movedRolls) {
    const overlapsUnselected = unselectedRolls.some(
      (ur) => mr.newStart.time < ur.end && mr.newEnd.time > ur.start
    );
    if (overlapsUnselected) {
      showToast("Moved roll would overlap an existing unselected roll.");
      return;
    }
    const overlapsNote = unselectedNotes.some(
      (un) => un.time > mr.newStart.time && un.time < mr.newEnd.time
    );
    if (overlapsNote) {
      showToast("Moved roll would cover an unselected note.");
      return;
    }
  }

  saveUndoPoint();
  if (!state.data.overrides.note_edits) {
    state.data.overrides.note_edits = {};
  }

  const patch = {};
  for (const item of movedNotes) {
    patch[item.orig.key] = 0;
  }
  for (const mr of movedRolls) {
    patch[mr.orig.startKey] = 0;
    patch[mr.orig.endKey] = 0;
  }
  for (const item of movedNotes) {
    patch[item.dest.key] = item.orig.code;
  }
  for (const mr of movedRolls) {
    patch[mr.newStart.key] = mr.code;
    patch[mr.newEnd.key] = 8;
  }

  const anyBalloonMoved = movedRolls.some((mr) => mr.orig.balloon);
  if (anyBalloonMoved) {
    const difficulty = document.getElementById("diffSelect").value;
    const oldOrder = orderedBalloonKeys(course);
    const oldCounts = getEffectiveBalloonCounts(course, difficulty);
    const pairs = [];
    const movedByStart = new Map(movedRolls.map((mr) => [mr.orig.startKey, mr]));
    for (let i = 0; i < oldOrder.length; i++) {
      const k = oldOrder[i];
      const count = oldCounts[i] ?? 10;
      const moved = movedByStart.get(k);
      const time = moved ? moved.newStart.time : rolls.find((r) => r.startKey === k)?.start ?? 0;
      pairs.push({ time, count });
    }
    pairs.sort((a, b) => a.time - b.time);
    if (!state.data.overrides.balloon_counts_by_course) {
      state.data.overrides.balloon_counts_by_course = {};
    }
    state.data.overrides.balloon_counts_by_course[difficulty] = pairs.map((p) => p.count);
  }

  Object.assign(state.data.overrides.note_edits, patch);
  applyOptimisticNotePatch(patch);

  state.selectedKeys = new Set(movedNotes.map((item) => item.dest.key));
  state.selectedRollKeys = new Set(movedRolls.map((mr) => mr.newStart.key));
  updateSelectionUI();

  state.editSeq += 1;
  rebuildChart(false);
}

function selectMarqueeBox(t1, t2, additive) {
  const course = getActiveCourse();
  if (!course) return;
  const minT = Math.min(t1, t2);
  const maxT = Math.max(t1, t2);
  const { notes, rolls } = computeFlattenedEvents(course);

  if (!additive) {
    state.selectedKeys.clear();
    state.selectedRollKeys.clear();
  }
  for (const n of notes) {
    if (n.time >= minT && n.time <= maxT) {
      state.selectedKeys.add(n.key);
    }
  }
  for (const r of rolls) {
    if (r.end >= minT && r.start <= maxT) {
      state.selectedRollKeys.add(r.startKey);
    }
  }
  updateSelectionUI();
}

function finishCanvasEdit(event) {
  const drag = state.drag;
  if (!drag) return;
  const rect = event.currentTarget.getBoundingClientRect();
  const x = event.clientX - rect.left;
  state.drag = null;

  if (drag.kind === "barline") {
    const preview = state.gridPreviewTimes;
    state.gridPreviewTimes = null;
    state.gridStatusText = "";
    if (!drag.moved) {
      state.gridAnchorIdx = state.gridAnchorIdx === drag.barIdx ? null : drag.barIdx;
      updateGridUI();
      return;
    }
    if (preview && preview.length === drag.origTimes.length) {
      saveUndoPoint();
      state.data.overrides.barline_times = preview;
      if (state.importId) {
        state.data.overrides.global_offset_ms = 0;
        document.getElementById("globalOffsetInput").value = "0";
      }
      state.editSeq += 1;
      rebuildChart(false);
    }
    return;
  }

  const point = pointFromPointer(x);

  if (drag.kind === "marquee") {
    if (drag.moved) {
      const hitX = 110;
      const curT = getCurrentTimeSec();
      const pxPerSec = parseFloat(document.getElementById("laneZoom").value || "340");
      const t1 = curT + (drag.startX - hitX) / pxPerSec;
      const t2 = curT + (x - hitX) / pxPerSec;
      selectMarqueeBox(t1, t2, drag.additive);
    } else if (!drag.additive) {
      clearSelection();
    }
    return;
  }

  if (!point) return;
  const tool = state.activeTool;

  if (drag.kind === "group-move") {
    if (!drag.moved) return;
    const measure = state.measureScreenItems.find((m) => m.mIdx === drag.startPoint.mIdx);
    const subs = measure ? subdivisionsForMeasure(measure) : 16;
    const stepSec = measure ? (measure.end - measure.start) / subs : 0.1;
    const stepDelta = Math.round((point.time - drag.startPoint.time) / Math.max(0.01, stepSec));
    if (stepDelta !== 0) {
      nudgeSelection(stepDelta);
    }
    return;
  }

  if (drag.kind === "note") {
    if (drag.moved) {
      setSingleEdit(point, drag.note.code, drag.note.key);
      return;
    }
    if (["1", "2", "3", "4"].includes(tool)) {
      const targetCode = Number(tool);
      if (targetCode !== drag.note.code) {
        setSingleEdit(
          { mIdx: drag.note.mIdx, numerator: drag.note.sIdx, denominator: drag.note.grid, position: drag.note.sIdx / drag.note.grid, key: drag.note.key },
          targetCode,
          drag.note.key
        );
      } else {
        setSingleNoteSelection(drag.note.key);
      }
      return;
    }
    if (tool === "5" || tool === "6" || tool === "7") {
      setSingleNoteSelection(drag.note.key);
      showToast("Click and drag on an empty chart position to draw a roll or balloon.");
      return;
    }
    setSingleNoteSelection(drag.note.key);
    return;
  }

  if (drag.kind === "roll-resize") {
    if (!drag.moved) {
      setSingleRollSelection(drag.roll.startKey);
      return;
    }
    const startPt = drag.edge === "start" ? point : endpointFromRoll(drag.roll, false);
    const endPt = drag.edge === "end" ? point : endpointFromRoll(drag.roll, true);
    const code = drag.roll.roll.balloon ? 7 : drag.roll.roll.big ? 6 : 5;
    setRollSpan(startPt, endPt, code, drag.roll);
    return;
  }

  if (drag.kind === "roll") {
    if (!drag.moved) {
      setSingleRollSelection(drag.roll.startKey);
      return;
    }
    const delta = point.time - drag.startPoint.time;
    const start = pointForTime(drag.roll.roll.start + delta);
    const end = pointForTime(drag.roll.roll.end + delta);
    setRollSpan(start, end, drag.roll.roll.balloon ? 7 : drag.roll.roll.big ? 6 : 5, drag.roll);
    return;
  }

  if (["1", "2", "3", "4"].includes(tool)) {
    setSingleEdit(point, Number(tool));
  } else if (tool === "5" || tool === "6" || tool === "7") {
    if (!drag.moved) {
      showToast("Click and drag on the chart lane to draw a roll or balloon span.");
      return;
    }
    setRollSpan(drag.startPoint, point, Number(tool));
  } else if (tool === "select") {
    clearSelection();
  }
}

function handleCanvasPointerDown(event) {
  if (!state.data || event.button === 2) return;
  const canvas = document.getElementById("taikoCanvas");
  const rect = canvas.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;

  if (wavesurfer?.isPlaying()) wavesurfer.pause();

  if (state.activeTool === "grid") {
    const barline = findBarlineAt(x);
    if (!barline) return;
    const course = getActiveCourse();
    const origTimes = getBaseBarlinesForCourse(course);
    if (!origTimes.length) return;
    state.drag = {
      kind: "barline",
      barIdx: barline.barIdx,
      startX: x,
      currentX: x,
      origTimes,
      course,
      moved: false,
    };
    canvas.setPointerCapture(event.pointerId);
    event.preventDefault();
    return;
  }

  const note = findNoteAt(x, y);
  const roll = findRollAt(x, y);
  const point = pointFromPointer(x);
  if (!point) return;

  if (state.activeTool === "erase") {
    if (note) {
      setSingleEdit(
        { mIdx: note.mIdx, numerator: note.sIdx, denominator: note.grid, position: note.sIdx / note.grid, key: note.key },
        0,
        note.key
      );
    } else if (roll) {
      removeRoll(roll);
    }
    return;
  }

  if (event.shiftKey && state.activeTool === "select") {
    if (note) {
      if (state.selectedKeys.has(note.key)) state.selectedKeys.delete(note.key);
      else state.selectedKeys.add(note.key);
      updateSelectionUI();
      return;
    }
    if (roll) {
      if (state.selectedRollKeys.has(roll.startKey)) state.selectedRollKeys.delete(roll.startKey);
      else state.selectedRollKeys.add(roll.startKey);
      updateSelectionUI();
      return;
    }
  }

  const totalSel = state.selectedKeys.size + state.selectedRollKeys.size;
  if (note && state.selectedKeys.has(note.key) && totalSel > 1 && state.activeTool === "select") {
    state.drag = { kind: "group-move", startPoint: point, startX: x, currentPoint: point, moved: false };
    canvas.setPointerCapture(event.pointerId);
    event.preventDefault();
    return;
  }

  if (roll && state.selectedRollKeys.has(roll.startKey) && totalSel > 1 && state.activeTool === "select") {
    state.drag = { kind: "group-move", startPoint: point, startX: x, currentPoint: point, moved: false };
    canvas.setPointerCapture(event.pointerId);
    event.preventDefault();
    return;
  }

  if (note) {
    setSingleNoteSelection(note.key);
    state.drag = { kind: "note", note, startX: x, startY: y, currentPoint: point, moved: false };
  } else if (roll) {
    setSingleRollSelection(roll.startKey);
    if (Math.abs(x - roll.x1) <= ROLL_EDGE_HIT_PX) {
      state.drag = { kind: "roll-resize", edge: "start", roll, startX: x, currentPoint: point, moved: false };
    } else if (Math.abs(x - roll.x2) <= ROLL_EDGE_HIT_PX) {
      state.drag = { kind: "roll-resize", edge: "end", roll, startX: x, currentPoint: point, moved: false };
    } else {
      state.drag = { kind: "roll", roll, startPoint: point, startX: x, currentPoint: point, moved: false };
    }
  } else if (y < (canvas.clientHeight * 0.57)) {
    if (state.activeTool === "select") {
      state.drag = {
        kind: "marquee",
        startX: x,
        startY: y,
        currentX: x,
        currentY: y,
        additive: event.shiftKey,
        moved: false,
      };
    } else {
      clearSelection();
      state.drag = { kind: "add", startPoint: point, startX: x, currentPoint: point, moved: false };
    }
  } else {
    return;
  }
  canvas.setPointerCapture(event.pointerId);
  event.preventDefault();
}

function handleCanvasPointerMove(event) {
  const canvas = event.currentTarget;
  const rect = canvas.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;

  if (!state.drag) {
    if (state.activeTool === "grid") {
      canvas.style.cursor = findBarlineAt(x) ? "ew-resize" : "default";
    } else if (state.activeTool === "select") {
      const roll = findRollAt(x, y);
      if (roll && (Math.abs(x - roll.x1) <= ROLL_EDGE_HIT_PX || Math.abs(x - roll.x2) <= ROLL_EDGE_HIT_PX)) {
        canvas.style.cursor = "ew-resize";
      } else if (findNoteAt(x, y) || roll) {
        canvas.style.cursor = "grab";
      } else {
        canvas.style.cursor = "default";
      }
    }
    return;
  }

  if (state.drag.kind === "barline") {
    state.drag.currentX = x;
    state.drag.moved = state.drag.moved || Math.abs(x - state.drag.startX) > 3;
    if (state.drag.moved) {
      const hitX = 110;
      const curT = getCurrentTimeSec();
      const pxPerSec = parseFloat(document.getElementById("laneZoom").value || "340");
      const offsetDelta = (state.data?.overrides?.global_offset_ms || 0) / 1000.0;
      const rawTargetTime = curT + (x - hitX) / pxPerSec - offsetDelta;
      const shiftFollowing = document.getElementById("chkShiftFollowing").checked || event.altKey;
      const res = computeBarlineDragResult(
        state.drag.origTimes,
        state.drag.course,
        state.drag.barIdx,
        rawTargetTime,
        state.gridAnchorIdx,
        shiftFollowing
      );
      state.gridPreviewTimes = res.times;
      state.gridStatusText = res.status;
    }
    return;
  }

  if (state.drag.kind === "marquee") {
    state.drag.currentX = x;
    state.drag.currentY = y;
    state.drag.moved = state.drag.moved || Math.abs(x - state.drag.startX) > 4 || Math.abs(y - state.drag.startY) > 4;
    return;
  }

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
  if (note) {
    setSingleEdit(
      { mIdx: note.mIdx, numerator: note.sIdx, denominator: note.grid, position: note.sIdx / note.grid, key: note.key },
      0,
      note.key
    );
  } else if (roll) {
    removeRoll(roll);
  }
}

// --- Event Listeners ---
const taikoCanvas = document.getElementById("taikoCanvas");
taikoCanvas.addEventListener("pointerdown", handleCanvasPointerDown);
taikoCanvas.addEventListener("pointermove", handleCanvasPointerMove);
taikoCanvas.addEventListener("pointerup", finishCanvasEdit);
taikoCanvas.addEventListener("pointercancel", () => {
  state.drag = null;
  state.gridPreviewTimes = null;
  state.gridStatusText = "";
});
taikoCanvas.addEventListener("contextmenu", handleCanvasContextMenu);
taikoCanvas.addEventListener("wheel", (e) => {
  if (!state.data) return;
  e.preventDefault();
  if (e.ctrlKey || e.metaKey) {
    const zoom = document.getElementById("laneZoom");
    const step = e.deltaY < 0 ? 25 : -25;
    zoom.value = String(Math.max(Number(zoom.min), Math.min(Number(zoom.max), Number(zoom.value) + step)));
    return;
  }
  const pxPerSec = parseFloat(document.getElementById("laneZoom").value || "340");
  const deltaPx = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
  const deltaSec = (deltaPx / pxPerSec) * 0.75;
  seekToSec(getCurrentTimeSec() + deltaSec);
}, { passive: false });

const laneScroll = document.getElementById("laneScroll");
laneScroll.addEventListener("pointerdown", () => { state.scrubbingLane = true; });
laneScroll.addEventListener("input", (e) => {
  state.scrubbingLane = true;
  seekToSec(parseFloat(e.target.value || "0"));
});
laneScroll.addEventListener("change", () => { state.scrubbingLane = false; });
laneScroll.addEventListener("pointerup", () => { state.scrubbingLane = false; });

document.getElementById("waveZoom").addEventListener("input", (e) => {
  if (hasWaveAudio()) {
    wavesurfer.zoom(Number(e.target.value));
  }
});
document.getElementById("btnWaveFit").addEventListener("click", () => {
  const slider = document.getElementById("waveZoom");
  slider.value = "0";
  if (hasWaveAudio()) {
    wavesurfer.zoom(0);
  }
});

document.querySelectorAll(".tool-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    setEditorTool(btn.dataset.tool);
    btn.blur();
  });
});

document.querySelectorAll("select").forEach((sel) => {
  sel.addEventListener("change", () => sel.blur());
});

document.getElementById("btnResetGrid").addEventListener("click", () => {
  if (!state.data) return;
  saveUndoPoint();
  state.data.overrides.barline_times = [];
  state.gridAnchorIdx = null;
  state.gridPreviewTimes = null;
  state.editSeq += 1;
  rebuildChart(false);
  showToast("Restored original barlines");
});

document.getElementById("btnStraightenGridRange").addEventListener("click", straightenGridRange);
document.getElementById("btnInchLeft").addEventListener("click", () => nudgeSelection(-1));
document.getElementById("btnInchRight").addEventListener("click", () => nudgeSelection(1));
document.getElementById("btnFlipSelection").addEventListener("click", flipSelectionColors);
document.getElementById("btnCopySelection").addEventListener("click", copySelection);
document.getElementById("btnPasteSelection").addEventListener("click", pasteSelection);
document.getElementById("btnDeleteSelection").addEventListener("click", deleteSelection);
document.getElementById("btnClearSelection").addEventListener("click", clearSelection);

document.getElementById("btnToggleChartControls").addEventListener("click", (e) => {
  const card = document.getElementById("chartControlsCard");
  card.classList.toggle("hidden");
  e.currentTarget.classList.toggle("btn-accent", !card.classList.contains("hidden"));
  e.currentTarget.blur();
});

const bindSliderPair = (inputId, labelId, fmt, defaultVal, resetBtnId) => {
  const inp = document.getElementById(inputId);
  const lbl = document.getElementById(labelId);
  inp.addEventListener("input", () => {
    lbl.textContent = fmt(parseFloat(inp.value));
  });
  inp.addEventListener("change", () => {
    applyChartControlsToOverrides();
    state.editSeq += 1;
    rebuildChart(false);
  });
  if (resetBtnId) {
    document.getElementById(resetBtnId).addEventListener("click", () => {
      const val = typeof defaultVal === "function" ? defaultVal() : defaultVal;
      inp.value = String(val);
      lbl.textContent = fmt(parseFloat(inp.value));
      applyChartControlsToOverrides();
      state.editSeq += 1;
      rebuildChart(false);
    });
  }
};

bindSliderPair("ctrlHighwaySpeed", "lblCtrlSpeed", (v) => v.toFixed(0), 101.343, "btnResetCtrlSpeed");
bindSliderPair("ctrlBigNote", "lblCtrlBig", (v) => v.toFixed(2), 0.88, "btnResetCtrlBig");
bindSliderPair("ctrlQuietThreshold", "lblCtrlQuiet", (v) => v.toFixed(2), 0.25, "btnResetCtrlQuiet");
bindSliderPair("ctrlCourseDensity", "lblCtrlDensity", (v) => `${Math.round(v)}%`, 100, "btnResetCtrlDensity");
bindSliderPair("ctrlCourseVariation", "lblCtrlVariation", (v) => `${Math.round(v)}%`, 0, "btnResetCtrlVariation");
bindSliderPair(
  "ctrlCourseRolls",
  "lblCtrlRolls",
  (v) => String(Math.round(v)),
  () => DEFAULT_ROLL_CAPS[document.getElementById("diffSelect").value] ?? 0,
  "btnResetCtrlRolls"
);
bindSliderPair(
  "ctrlCourseTriples",
  "lblCtrlTriples",
  (v) => `${Math.round(v)}%`,
  () => DEFAULT_GROOVE_TRIPLE_DENSITY[document.getElementById("diffSelect").value] ?? 0,
  "btnResetCtrlTriples"
);
bindSliderPair(
  "ctrlTripleComp",
  "lblCtrlTripleComp",
  (v) => `${Math.round(v)}%`,
  () => DEFAULT_TRIPLE_COMPLEXITY[document.getElementById("diffSelect").value] ?? 35,
  "btnResetCtrlTripleComp"
);
bindSliderPair("ctrlTripleMaxBpm", "lblCtrlTripleBpm", (v) => String(Math.round(v)), 235, "btnResetCtrlTripleBpm");
bindSliderPair("ctrlTripleRamp", "lblCtrlTripleRamp", (v) => `${Math.round(v)}%`, 50, "btnResetCtrlTripleRamp");
bindSliderPair("ctrlSectionVariety", "lblCtrlSecVariety", (v) => `${Math.round(v)}%`, 35, "btnResetCtrlSecVariety");
bindSliderPair("ctrlVocalMin", "lblCtrlVocalMin", (v) => v.toFixed(2), 0.36, "btnResetCtrlVocalMin");
bindSliderPair("ctrlGuitarMin", "lblCtrlGuitarMin", (v) => v.toFixed(2), 0.58, "btnResetCtrlGuitarMin");
bindSliderPair("ctrlCymbalSens", "lblCtrlCymbalSens", (v) => `${Math.round(v)}%`, 100, "btnResetCtrlCymbalSens");
["ctrlRideMode", "ctrlEnableBigNotes", "ctrlFoldBlasts"].forEach((id) => {
  document.getElementById(id).addEventListener("change", () => {
    applyChartControlsToOverrides();
    state.editSeq += 1;
    rebuildChart(false);
  });
});

let cachedSliderProfiles = {};
async function refreshSliderProfiles() {
  const res = await fetch("/api/slider-profiles");
  if (!res.ok) return;
  const { profiles } = await res.json();
  cachedSliderProfiles = profiles || {};
  const sel = document.getElementById("sliderProfileSelect");
  const prev = sel.value;
  sel.innerHTML = '<option value="">-- Select Profile --</option>';
  Object.keys(cachedSliderProfiles).sort().forEach((name) => {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    sel.appendChild(opt);
  });
  if (cachedSliderProfiles[prev]) sel.value = prev;
}

document.getElementById("sliderProfileSelect").addEventListener("change", (e) => {
  const name = e.target.value;
  if (!name || !cachedSliderProfiles[name] || !state.data) return;
  Object.assign(state.data.overrides, cachedSliderProfiles[name]);
  populateChartControls();
  state.editSeq += 1;
  rebuildChart(false);
  showToast(`Applied slider profile '${name}'`);
});

document.getElementById("btnSaveSliderProfile").addEventListener("click", async () => {
  if (!state.data) return;
  const name = window.prompt("Name for this slider profile:");
  if (!name || !name.trim()) return;
  applyChartControlsToOverrides();
  const payload = { ...state.data.overrides };
  delete payload.sections;
  delete payload.note_edits;
  delete payload.custom_downbeats;
  delete payload.barline_times;
  const res = await fetch(`/api/slider-profiles/${encodeURIComponent(name.trim())}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (res.ok) {
    await refreshSliderProfiles();
    document.getElementById("sliderProfileSelect").value = name.trim();
    showToast(`Saved profile '${name.trim()}'`);
  }
});

document.getElementById("btnDeleteSliderProfile").addEventListener("click", async () => {
  const name = document.getElementById("sliderProfileSelect").value;
  if (!name) return;
  await fetch(`/api/slider-profiles/${encodeURIComponent(name)}`, { method: "DELETE" });
  await refreshSliderProfiles();
  showToast(`Deleted profile '${name}'`);
});

["ctrlQuietBehavior", "ctrlCourseFollow"].forEach((id) => {
  document.getElementById(id).addEventListener("change", () => {
    applyChartControlsToOverrides();
    state.editSeq += 1;
    rebuildChart(false);
  });
});

document.getElementById("balloonCount").addEventListener("change", (e) => {
  const singleRollKey = getSingleSelectedRollKey();
  if (!state.data || !singleRollKey) return;
  const course = getActiveCourse();
  if (!course) return;
  const idx = orderedBalloonKeys(course).indexOf(singleRollKey);
  if (idx < 0) return;
  const parsed = Math.max(1, Math.min(1000, Math.round(Number(e.target.value || "10"))));
  e.target.value = String(parsed);
  const difficulty = document.getElementById("diffSelect").value;
  saveUndoPoint();
  if (!state.data.overrides.balloon_counts_by_course) {
    state.data.overrides.balloon_counts_by_course = {};
  }
  const counts = getEffectiveBalloonCounts(course, difficulty);
  while (counts.length <= idx) counts.push(10);
  counts[idx] = parsed;
  state.data.overrides.balloon_counts_by_course[difficulty] = counts;
  state.editSeq += 1;
  rebuildChart(false);
});

document.getElementById("songSelect").addEventListener("change", (e) => loadSong(e.target.value));
document.getElementById("stemSelect").addEventListener("change", (e) => {
  if (!wavesurfer || !state.songId) return;
  const t = getCurrentTimeSec();
  initWaveSurfer(`/api/songs/${state.songId}/audio?stem=${e.target.value}`);
  wavesurfer.once("ready", () => seekToSec(t));
});
document.getElementById("presetSelect").addEventListener("change", () => {
  state.editSeq += 1;
  rebuildChart(false);
});
document.getElementById("diffSelect").addEventListener("change", () => {
  clearSelection();
  renderDiagnostics();
  populateChartControls();
  populateInspector();
});
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

document.getElementById("btnPlay").addEventListener("click", (e) => {
  ensureAudioCtx();
  if (wavesurfer) wavesurfer.playPause();
  e.currentTarget.blur();
});
document.getElementById("btnLoopSec").addEventListener("click", (e) => {
  state.loopSection = !state.loopSection;
  e.target.classList.toggle("btn-accent", state.loopSection);
});

// Capture Space strictly for Play/Pause on both keydown and keyup
window.addEventListener("keydown", (e) => {
  if (e.code === "Space" && !isTextInputElement(e.target)) {
    e.preventDefault();
    e.stopPropagation();
    if (!e.repeat) {
      ensureAudioCtx();
      if (wavesurfer) wavesurfer.playPause();
    }
    return;
  }

  if (state.tapArmed && e.code === "Numpad0") {
    e.preventDefault();
    if (!e.repeat) recordNumpadTap();
    return;
  }

  const isTyping = isTextInputElement(e.target);
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
  if (commandKey && e.code === "KeyC" && !isTyping && hasSelection()) {
    e.preventDefault();
    copySelection();
    return;
  }
  if (commandKey && e.code === "KeyV" && !isTyping && state.clipboard) {
    e.preventDefault();
    pasteSelection();
    return;
  }
  if (isTyping || commandKey) return;

  if (e.altKey && e.code === "ArrowLeft") {
    e.preventDefault();
    nudgeSelection(-1);
    return;
  }
  if (e.altKey && e.code === "ArrowRight") {
    e.preventDefault();
    nudgeSelection(1);
    return;
  }
  if (e.altKey) return;

  if (e.code === "BracketLeft") {
    e.preventDefault();
    nudgeSelection(-1);
  } else if (e.code === "BracketRight") {
    e.preventDefault();
    nudgeSelection(1);
  } else if (e.code === "KeyF") {
    e.preventDefault();
    flipSelectionColors();
  } else if (e.code === "Delete" || e.code === "Backspace") {
    e.preventDefault();
    deleteSelection();
  } else if (e.code === "ArrowLeft" || e.code === "ArrowRight") {
    e.preventDefault();
    const delta = e.code === "ArrowLeft" ? -1 : 1;
    seekToSec(getCurrentTimeSec() + delta * (e.shiftKey ? 1 : 0.1));
  } else if (e.code === "Home" || e.code === "End") {
    e.preventDefault();
    seekToSec(e.code === "Home" ? 0 : getChartDuration());
  } else if (e.code === "Equal" || e.code === "NumpadAdd") {
    const zoom = document.getElementById("laneZoom");
    zoom.value = String(Math.min(Number(zoom.max), Number(zoom.value) + 20));
  } else if (e.code === "Minus" || e.code === "NumpadSubtract") {
    const zoom = document.getElementById("laneZoom");
    zoom.value = String(Math.max(Number(zoom.min), Number(zoom.value) - 20));
  } else if (e.code === "Escape") {
    state.drag = null;
    state.gridPreviewTimes = null;
    state.gridStatusText = "";
    state.gridAnchorIdx = null;
    clearSelection();
  } else if (e.code === "KeyS") {
    const snap = document.getElementById("snapToggle");
    snap.checked = !snap.checked;
    snap.dispatchEvent(new Event("change"));
  } else if (e.code === "KeyA") {
    setEditorTool("select");
  } else if (e.code === "KeyG") {
    setEditorTool("grid");
  } else if (e.code === "KeyD") {
    setEditorTool(e.shiftKey ? "3" : "1");
  } else if (e.code === "KeyK") {
    setEditorTool(e.shiftKey ? "4" : "2");
  } else if (e.code === "KeyR") {
    setEditorTool(e.shiftKey ? "6" : "5");
  } else if (e.code === "KeyB") {
    setEditorTool("7");
  } else if (e.code === "KeyE") {
    setEditorTool("erase");
  } else if (e.code === "KeyI" && !state.importId) {
    markSectionBoundary("in");
  } else if (e.code === "KeyO" && !state.importId) {
    markSectionBoundary("out");
  }
}, { capture: true });

window.addEventListener("keyup", (e) => {
  if (e.code === "Space" && !isTextInputElement(e.target)) {
    e.preventDefault();
    e.stopPropagation();
  }
}, { capture: true });

document.getElementById("btnUndo").addEventListener("click", undoEdit);
document.getElementById("btnRedo").addEventListener("click", redoEdit);
document.getElementById("btnHelp").addEventListener("click", () => {
  document.getElementById("shortcutPanel").classList.toggle("hidden");
});

document.getElementById("snapToggle").checked =
  localStorage.getItem("taikolab-editor-snap") !== "false";
document.getElementById("snapToggle").addEventListener("change", (e) => {
  localStorage.setItem("taikolab-editor-snap", String(e.target.checked));
  document.getElementById("editGrid").disabled = !e.target.checked;
});
document.getElementById("editGrid").disabled = !document.getElementById("snapToggle").checked;
setEditorTool("select");

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
  state.data.overrides.barline_times = [];
  state.editSeq += 1;
  rebuildChart(false);
  showToast(`Seeded ${course.measures.length} downbeat markers from current grid!`);
});

// Auto-rebuild immediately when Section Inspector dropdowns/checkboxes change
function updateMarkRangeUI() {
  const lbl = document.getElementById("lblMarkRange");
  const btn = document.getElementById("btnCreateMarkedSection");
  if (!lbl || !btn) return;
  const inTxt = state.markInSec !== null ? fmtTime(state.markInSec) : "--:--";
  const outTxt = state.markOutSec !== null ? fmtTime(state.markOutSec) : "--:--";
  lbl.textContent = `${inTxt} / ${outTxt}`;
  btn.disabled = !(state.markInSec !== null && state.markOutSec !== null && Math.abs(state.markOutSec - state.markInSec) >= 0.25);
}

function markSectionBoundary(which) {
  if (!state.data) return;
  const offsetDelta = state.importId ? 0.0 : (state.data.overrides.global_offset_ms || 0) / 1000.0;
  const rawT = Math.max(0, getCurrentTimeSec() - offsetDelta);
  const dur = getChartDuration();
  const snapped = Number(snapTimeToBarline(rawT, 0.0, dur || rawT + 10).toFixed(3));
  if (which === "in") {
    state.markInSec = snapped;
    showToast(`Marked section IN at ${fmtTime(snapped)}`);
  } else {
    state.markOutSec = snapped;
    showToast(`Marked section OUT at ${fmtTime(snapped)}`);
  }
  updateMarkRangeUI();
}

document.getElementById("btnMarkIn").addEventListener("click", () => markSectionBoundary("in"));
document.getElementById("btnMarkOut").addEventListener("click", () => markSectionBoundary("out"));
document.getElementById("btnCreateMarkedSection").addEventListener("click", () => {
  if (state.markInSec === null || state.markOutSec === null) return;
  const s = Math.min(state.markInSec, state.markOutSec);
  const e = Math.max(state.markInSec, state.markOutSec);
  saveUndoPoint();
  if (carveSectionRange(s, e)) {
    state.markInSec = null;
    state.markOutSec = null;
    updateMarkRangeUI();
    showToast(`Created section ${fmtTime(s)} - ${fmtTime(e)}`);
  }
});

document.getElementById("btnAutoSplitSections").addEventListener("click", async () => {
  if (!state.data || state.importId) return;
  const res = await fetch(`/api/songs/${state.songId}/sections/auto-split`, { method: "POST" });
  if (!res.ok) {
    showToast("Could not auto-split sections.");
    return;
  }
  const { sections } = await res.json();
  if (!sections || !sections.length) return;
  saveUndoPoint();
  state.data.overrides.sections = sections;
  state.selectedSectionIdx = 0;
  renderSectionList();
  populateInspector();
  renderRegions(true);
  state.editSeq += 1;
  rebuildChart(false);
  showToast(`Detected ${sections.length} musical sections`);
});

["secSrcDrums", "secSrcVocals", "secSrcGuitar"].forEach((id) => {
  document.getElementById(id).addEventListener("change", () => {
    document.getElementById("secFollow").value = followModeFromSourceCheckboxes();
  });
});

document.getElementById("secFollow").addEventListener("change", (e) => {
  syncSourceCheckboxesFromFollow(e.target.value);
});

["secBpmMult", "secMeter"].forEach((id) => {
  document.getElementById(id).addEventListener("change", () => {
    readInspectorIntoSelected(null);
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
    state.editSeq += 1;
    rebuildChart(false);
  });
});

// Per-Bar Time Signature Switcher right at the Playhead
document.getElementById("barMeterSelect").addEventListener("change", (e) => {
  const val = e.target.value;
  if (!val || !state.data) return;
  const [n, d] = val.split("/").map(Number);
  const curT = getCurrentTimeSec();
  const course = getActiveCourse();
  if (!course || !course.measures.length) return;

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
  state.data.overrides.barline_times = [];
  state.editSeq += 1;
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
  state.data.overrides.barline_times = [];
  state.editSeq += 1;
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
  state.data.overrides.barline_times = [];
  state.editSeq += 1;
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
  state.data.overrides.barline_times = [];
  state.editSeq += 1;
  await rebuildChart(false);
  showToast(`Filled ${json.added_count} skipped 2x bar gaps!`);
});

document.getElementById("btnClearTaps").addEventListener("click", () => {
  if (!state.data) return;
  state.data.overrides.custom_downbeats = [];
  state.data.overrides.barline_times = [];
  state.sessionTaps = [];
  state.editSeq += 1;
  rebuildChart(false);
  showToast("Cleared custom taps — reverted to auto grid");
});

document.querySelectorAll(".nudge-global").forEach((btn) => {
  btn.addEventListener("click", () => {
    const inp = document.getElementById("globalOffsetInput");
    saveUndoPoint();
    inp.value = parseFloat(inp.value || "0") + parseFloat(btn.dataset.ms);
    state.editSeq += 1;
    rebuildChart(false);
  });
});
document.getElementById("globalOffsetInput").addEventListener("change", () => {
  saveUndoPoint();
  state.editSeq += 1;
  rebuildChart(false);
});

document.querySelectorAll(".nudge-sec").forEach((btn) => {
  btn.addEventListener("click", () => {
    const inp = document.getElementById("secShiftMs");
    inp.value = parseFloat(inp.value || "0") + parseFloat(btn.dataset.ms);
    readInspectorIntoSelected();
    state.editSeq += 1;
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
  const secs = state.data?.overrides?.sections || [];
  const sec = secs[state.selectedSectionIdx];
  if (!sec) return;
  const activeDiff = document.getElementById("diffSelect").value;
  const mode = document.getElementById("secFollow").value;
  document.getElementById("lblSectionApplySummary").textContent =
    `Apply "${sec.label || `Part ${state.selectedSectionIdx + 1}`}" (${mode}) within each selected difficulty's NPS & ergonomics limits:`;
  document.querySelectorAll(".sec-diff-chk").forEach((chk) => {
    chk.checked = chk.value === activeDiff;
  });
  document.getElementById("sectionApplyModal").classList.remove("hidden");
});

document.getElementById("btnCloseSectionApplyModal").addEventListener("click", () => {
  document.getElementById("sectionApplyModal").classList.add("hidden");
});
document.getElementById("btnSecDiffCurrent").addEventListener("click", () => {
  const activeDiff = document.getElementById("diffSelect").value;
  document.querySelectorAll(".sec-diff-chk").forEach((chk) => {
    chk.checked = chk.value === activeDiff;
  });
});
document.getElementById("btnSecDiffAll").addEventListener("click", () => {
  document.querySelectorAll(".sec-diff-chk").forEach((chk) => {
    chk.checked = true;
  });
});
document.getElementById("btnConfirmApplySection").addEventListener("click", () => {
  const chosen = [...document.querySelectorAll(".sec-diff-chk")]
    .filter((chk) => chk.checked)
    .map((chk) => chk.value);
  if (!chosen.length) {
    showToast("Select at least one difficulty.");
    return;
  }
  saveUndoPoint();
  readInspectorIntoSelected(chosen);
  document.getElementById("sectionApplyModal").classList.add("hidden");
  state.editSeq += 1;
  rebuildChart(false);
  showToast(`Applied section to ${chosen.join(", ")}`);
});

document.getElementById("btnSplitHere").addEventListener("click", splitSectionAtPlayhead);
document.getElementById("btnSplitAtPlayhead").addEventListener("click", splitSectionAtPlayhead);
document.getElementById("btnSectionFromSel").addEventListener("click", createSectionFromSelection);
document.getElementById("btnQuantizeSection").addEventListener("click", straightenSelectedSectionBpm);
document.getElementById("secBpm").addEventListener("change", () => {
  readInspectorIntoSelected();
  state.data.overrides.barline_times = [];
  state.editSeq += 1;
  rebuildChart(false);
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
  state.editSeq += 1;
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
  clearSelection();
  state.editSeq += 1;
  rebuildChart(false);
  showToast(`${difficulty} manual edits cleared`);
});

document.getElementById("btnExport").addEventListener("click", () => rebuildChart(true));

// --- Editable Star Rating (LEVEL) ---
document.getElementById("courseStarsInput").addEventListener("change", (e) => {
  if (!state.data) return;
  const diff = document.getElementById("diffSelect").value;
  const stars = Math.max(1, Math.min(12, Math.round(Number(e.target.value || "5"))));
  e.target.value = String(stars);
  saveUndoPoint();
  if (!state.data.overrides.difficulty_overrides) state.data.overrides.difficulty_overrides = {};
  const existing = state.data.overrides.difficulty_overrides[diff] || {};
  state.data.overrides.difficulty_overrides[diff] = { ...existing, star_rating: stars };
  state.editSeq += 1;
  rebuildChart(false);
});

// --- Course Copy / Move / Delete Modal ---
function renderCourseModal() {
  if (!state.data) return;
  const courses = state.data.chart.courses || [];
  const diags = state.data.diagnostics || [];
  const allDiffs = state.importId
    ? ["easy", "normal", "hard", "oni", ...(courses.some((c) => c.difficulty === "ura") ? ["ura"] : [])]
    : ["easy", "normal", "hard", "oni"];

  const summary = document.getElementById("courseSummaryList");
  summary.innerHTML = "";
  for (const diff of allDiffs) {
    const c = courses.find((item) => item.difficulty === diff);
    const d = diags.find((item) => item.difficulty === diff);
    const row = document.createElement("div");
    row.className = "course-summary-row";
    row.innerHTML = `<strong>${COURSE_LABELS[diff] || diff}</strong><span class="mono">${
      c ? `${c.level_stars}★ · ${d ? d.total_notes : 0} notes` : "Not present"
    }</span>`;
    summary.appendChild(row);
  }

  const srcSel = document.getElementById("courseSrcSelect");
  const dstSel = document.getElementById("courseDstSelect");
  const activeDiff = document.getElementById("diffSelect").value;
  srcSel.innerHTML = "";
  dstSel.innerHTML = "";
  for (const c of courses) {
    const opt = document.createElement("option");
    opt.value = c.difficulty;
    opt.textContent = `${COURSE_LABELS[c.difficulty] || c.difficulty} (${c.level_stars}★)`;
    srcSel.appendChild(opt);
  }
  for (const diff of allDiffs) {
    const opt = document.createElement("option");
    opt.value = diff;
    opt.textContent = COURSE_LABELS[diff] || diff;
    dstSel.appendChild(opt);
  }
  if (courses.some((c) => c.difficulty === activeDiff)) srcSel.value = activeDiff;
  const firstOther = allDiffs.find((d) => d !== srcSel.value) || allDiffs[0];
  dstSel.value = firstOther;
  const srcCourse = courses.find((c) => c.difficulty === srcSel.value);
  document.getElementById("courseTargetStars").value = String(srcCourse ? srcCourse.level_stars : 5);

  document.getElementById("btnCourseDeleteOnly").disabled = courses.length <= 1;
  const hasRemoved = Boolean(state.data.overrides?.disabled_courses?.length);
  document.getElementById("btnCourseRestoreAll").classList.toggle("hidden", Boolean(state.importId) || !hasRemoved);
}

document.getElementById("courseSrcSelect").addEventListener("change", (e) => {
  const c = (state.data?.chart?.courses || []).find((item) => item.difficulty === e.target.value);
  if (c) document.getElementById("courseTargetStars").value = String(c.level_stars);
});

async function applyCourseTransfer(removeSource) {
  if (!state.data) return;
  const src = document.getElementById("courseSrcSelect").value;
  const dst = document.getElementById("courseDstSelect").value;
  if (!src || !dst || src === dst) {
    showToast("Choose two different difficulties for Source and Target.");
    return;
  }
  const courses = state.data.chart.courses || [];
  if (removeSource && courses.length <= 1) {
    showToast("At least one course must remain.");
    return;
  }
  const stars = Math.max(1, Math.min(12, Math.round(Number(document.getElementById("courseTargetStars").value || "5"))));

  saveUndoPoint();
  const ov = state.data.overrides;
  if (!ov.note_edits) ov.note_edits = {};
  if (!ov.course_sources) ov.course_sources = {};
  if (!ov.disabled_courses) ov.disabled_courses = [];
  if (!ov.difficulty_overrides) ov.difficulty_overrides = {};
  if (!ov.balloon_counts_by_course) ov.balloon_counts_by_course = {};
  if (!ov.fill_rolls_by_course) ov.fill_rolls_by_course = {};

  const dstPrefix = `${dst}:`;
  const srcPrefix = `${src}:`;
  const cleaned = Object.fromEntries(
    Object.entries(ov.note_edits).filter(([k]) => !k.startsWith(dstPrefix))
  );
  for (const [k, v] of Object.entries(ov.note_edits)) {
    if (k.startsWith(srcPrefix)) {
      cleaned[dstPrefix + k.slice(srcPrefix.length)] = v;
    }
  }
  ov.note_edits = cleaned;

  const srcCourse = courses.find((c) => c.difficulty === src);
  const srcBalloons = ov.balloon_counts_by_course[src] ?? srcCourse?.balloon_counts ?? [];
  ov.balloon_counts_by_course[dst] = [...srcBalloons];

  ov.course_sources[dst] = ov.course_sources[src] || src;
  ov.disabled_courses = ov.disabled_courses.filter((d) => d !== dst);

  const srcDiffOv = ov.difficulty_overrides[src] || {};
  ov.difficulty_overrides[dst] = { ...srcDiffOv, star_rating: stars };
  if (ov.fill_rolls_by_course[src] !== undefined) {
    ov.fill_rolls_by_course[dst] = ov.fill_rolls_by_course[src];
  }

  if (removeSource) {
    ov.disabled_courses = [...new Set([...ov.disabled_courses, src])];
  }

  document.getElementById("courseModal").classList.add("hidden");
  clearSelection();
  state.editSeq += 1;
  await rebuildChart(false);
  document.getElementById("diffSelect").value = dst;
  renderDiagnostics();
  populateChartControls();
  showToast(removeSource ? `Moved ${src} → ${dst} and deleted ${src}` : `Copied ${src} → ${dst}`);
}

document.getElementById("btnManageCourses").addEventListener("click", () => {
  if (!state.data) return;
  renderCourseModal();
  document.getElementById("courseModal").classList.remove("hidden");
});
document.getElementById("btnCloseCourseModal").addEventListener("click", () => {
  document.getElementById("courseModal").classList.add("hidden");
});
document.getElementById("btnCourseCopyKeep").addEventListener("click", () => applyCourseTransfer(false));
document.getElementById("btnCourseMoveDelete").addEventListener("click", () => applyCourseTransfer(true));
document.getElementById("btnCourseDeleteOnly").addEventListener("click", async () => {
  if (!state.data) return;
  const courses = state.data.chart.courses || [];
  if (courses.length <= 1) {
    showToast("At least one course must remain.");
    return;
  }
  const src = document.getElementById("courseSrcSelect").value;
  saveUndoPoint();
  const ov = state.data.overrides;
  ov.disabled_courses = [...new Set([...(ov.disabled_courses || []), src])];
  document.getElementById("courseModal").classList.add("hidden");
  clearSelection();
  state.editSeq += 1;
  await rebuildChart(false);
  showToast(`Removed ${src} course`);
});
document.getElementById("btnCourseRestoreAll").addEventListener("click", async () => {
  if (!state.data || state.importId) return;
  saveUndoPoint();
  state.data.overrides.disabled_courses = [];
  state.data.overrides.course_sources = {};
  document.getElementById("courseModal").classList.add("hidden");
  state.editSeq += 1;
  await rebuildChart(false);
  showToast("Restored default courses");
});

// --- Save / Open Project Modal (workspace/saved_projects) ---
document.getElementById("btnCloseProjectModal").addEventListener("click", () => {
  document.getElementById("projectModal").classList.add("hidden");
});

document.getElementById("btnSaveProject").addEventListener("click", () => {
  if (!state.data) return;
  document.getElementById("projectModalTitle").textContent = "Save Project Draft";
  document.getElementById("projectSaveForm").classList.remove("hidden");
  document.getElementById("projectListWrap").classList.add("hidden");
  const defaultName = state.activeProjectName || state.data.analysis_summary?.title || state.songId || "taiko-project";
  document.getElementById("projectNameInput").value = defaultName;
  document.getElementById("projectModal").classList.remove("hidden");
});

document.getElementById("btnConfirmSaveProject").addEventListener("click", async () => {
  if (!state.data) return;
  const projectName = document.getElementById("projectNameInput").value.trim();
  if (!projectName) {
    showToast("Enter a project name.");
    return;
  }
  if (state.importId && !state.sourceTjaPath) {
    showToast("Cannot save an imported TJA project without its source file path.");
    return;
  }
  state.data.overrides.preset = document.getElementById("presetSelect").value;
  state.data.overrides.global_offset_ms = parseFloat(document.getElementById("globalOffsetInput").value || "0");
  const payload = {
    project_name: projectName,
    mode: state.importId ? "imported_tja" : "song",
    song_id: state.importId ? null : state.songId,
    source_tja_path: state.importId ? state.sourceTjaPath : null,
    overrides: state.data.overrides,
    active_difficulty: document.getElementById("diffSelect").value,
    playhead_sec: Math.max(0, getCurrentTimeSec()),
    lane_zoom: parseFloat(document.getElementById("laneZoom").value || "340"),
  };
  const res = await fetch("/api/projects/save", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json();
    showToast(err.detail || "Could not save project");
    return;
  }
  const saved = await res.json();
  state.activeProjectName = saved.project_name;
  document.getElementById("projectModal").classList.add("hidden");
  showToast(`Saved project → ${saved.project_id}.taikolab.json`);
});

async function refreshProjectList() {
  const res = await fetch("/api/projects");
  const listEl = document.getElementById("projectList");
  listEl.innerHTML = "";
  if (!res.ok) {
    showToast("Could not load saved projects");
    return;
  }
  const { projects } = await res.json();
  if (!projects.length) {
    listEl.innerHTML = '<div class="hint">No saved projects yet in workspace/saved_projects/.</div>';
    return;
  }
  for (const p of projects) {
    const row = document.createElement("div");
    row.className = "course-summary-row";
    row.innerHTML = `
      <div>
        <div><strong>${p.project_name}</strong></div>
        <div class="sec-meta">${p.mode === "imported_tja" ? "TJA" : p.song_id} &bull; ${p.active_difficulty} &bull; ${p.saved_at || ""}</div>
      </div>
      <div class="modal-actions">
        <button type="button" class="btn-accent btn-open-proj">Load</button>
        <button type="button" class="btn-danger btn-del-proj">Delete</button>
      </div>
    `;
    row.querySelector(".btn-open-proj").addEventListener("click", () => loadSavedProject(p.project_id));
    row.querySelector(".btn-del-proj").addEventListener("click", async () => {
      await fetch(`/api/projects/${encodeURIComponent(p.project_id)}`, { method: "DELETE" });
      await refreshProjectList();
    });
    listEl.appendChild(row);
  }
}

async function loadSavedProject(projectId) {
  const res = await fetch(`/api/projects/${encodeURIComponent(projectId)}/open`, { method: "POST" });
  if (!res.ok) {
    const err = await res.json();
    showToast(err.detail || "Could not open saved project");
    return;
  }
  const { project, import_id } = await res.json();
  document.getElementById("projectModal").classList.add("hidden");

  if (project.mode === "imported_tja" && import_id) {
    await loadImportedTja(import_id, project.source_tja_path);
  } else if (project.song_id) {
    const songSel = document.getElementById("songSelect");
    if ([...songSel.options].some((o) => o.value === project.song_id)) {
      songSel.value = project.song_id;
    }
    await loadSong(project.song_id);
  }

  state.activeProjectName = project.project_name;
  state.data.overrides = project.overrides;
  document.getElementById("globalOffsetInput").value = project.overrides.global_offset_ms || 0;
  document.getElementById("laneZoom").value = String(project.lane_zoom || 340);
  if (project.overrides.preset) {
    const presetSel = document.getElementById("presetSelect");
    if (presetSel) presetSel.value = project.overrides.preset;
  }
  state.editSeq += 1;
  await rebuildChart(false);
  if ([...document.getElementById("diffSelect").options].some((o) => o.value === project.active_difficulty)) {
    document.getElementById("diffSelect").value = project.active_difficulty;
    renderDiagnostics();
    populateChartControls();
  }
  seekToSec(project.playhead_sec || 0);
  showToast(`Loaded project '${project.project_name}'`);
}

document.getElementById("btnOpenProject").addEventListener("click", async () => {
  document.getElementById("projectModalTitle").textContent = "Open Saved Project (workspace/saved_projects)";
  document.getElementById("projectSaveForm").classList.add("hidden");
  document.getElementById("projectListWrap").classList.remove("hidden");
  document.getElementById("projectModal").classList.remove("hidden");
  await refreshProjectList();
});

refreshSliderProfiles();
init();