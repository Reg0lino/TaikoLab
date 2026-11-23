$ErrorActionPreference = "Stop"

$root = (Get-Location).Path
if (-not (Test-Path (Join-Path $root "pyproject.toml"))) {
    throw "Run this script from the TaikoLab repository root (C:\!CODE\TaikoLab)."
}

function Write-Utf8NoBom {
    param([string]$Path, [string]$Content)
    [System.IO.File]::WriteAllText($Path, $Content, [System.Text.UTF8Encoding]::new($false))
}

function Update-FileReplaceIdempotent {
    param([string]$Path, [string]$OldText, [string]$NewText, [string]$AlreadyMarker)
    $raw = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
    $usesCrlf = $raw.Contains("`r`n")
    $norm = $raw -replace "`r`n", "`n"
    if ($AlreadyMarker -and $norm.Contains($AlreadyMarker)) {
        return
    }
    $oldNorm = $OldText -replace "`r`n", "`n"
    $newNorm = $NewText -replace "`r`n", "`n"
    if (-not $norm.Contains($oldNorm)) {
        throw "Expected anchor block not found in $Path"
    }
    $updated = $norm.Replace($oldNorm, $newNorm)
    if ($usesCrlf) { $updated = $updated -replace "`n", "`r`n" }
    Write-Utf8NoBom -Path $Path -Content $updated
}

# ---------------------------------------------------------------------------
# 1. Patch schemas.py (groove_triple_max_bpm)
# ---------------------------------------------------------------------------
$schemasPath = Join-Path $root "src\taikolab\schemas.py"
Update-FileReplaceIdempotent -Path $schemasPath `
    -OldText "    groove_triple_complexity: float | None = Field(default=None, ge=0.0, le=1.0)" `
    -NewText @'
    groove_triple_complexity: float | None = Field(default=None, ge=0.0, le=1.0)
    groove_triple_max_bpm: float | None = Field(default=None, ge=120.0, le=300.0)
'@ `
    -AlreadyMarker "groove_triple_max_bpm"

# ---------------------------------------------------------------------------
# 2. Patch section_builder.py (wire groove_triple_max_bpm)
# ---------------------------------------------------------------------------
$secBuilderPath = Join-Path $root "src\taikolab\arranger\section_builder.py"
Update-FileReplaceIdempotent -Path $secBuilderPath `
    -OldText "    groove_triple_complexity: float = 0.35" `
    -NewText @'
    groove_triple_complexity: float = 0.35
    groove_triple_max_bpm: float = 235.0
'@ `
    -AlreadyMarker "groove_triple_max_bpm: float"

Update-FileReplaceIdempotent -Path $secBuilderPath `
    -OldText @'
    groove_triple_complexity = (
        overrides.groove_triple_complexity
        if overrides and overrides.groove_triple_complexity is not None
        else 0.35
    )
'@ `
    -NewText @'
    groove_triple_complexity = (
        overrides.groove_triple_complexity
        if overrides and overrides.groove_triple_complexity is not None
        else 0.35
    )
    groove_triple_max_bpm = (
        overrides.groove_triple_max_bpm
        if overrides and overrides.groove_triple_max_bpm is not None
        else 235.0
    )
'@ `
    -AlreadyMarker "groove_triple_max_bpm ="

Update-FileReplaceIdempotent -Path $secBuilderPath `
    -OldText "                groove_triple_complexity=groove_triple_complexity," `
    -NewText @'
                groove_triple_complexity=groove_triple_complexity,
                groove_triple_max_bpm=groove_triple_max_bpm,
'@ `
    -AlreadyMarker "groove_triple_max_bpm=groove_triple_max_bpm"

# ---------------------------------------------------------------------------
# 3. Write src/taikolab/arranger/groove_triples.py (relative pocket scanner + max BPM)
# ---------------------------------------------------------------------------
$grooveTriplesPath = Join-Path $root "src\taikolab\arranger\groove_triples.py"
$grooveTriplesPy = @'
"""Post-ergonomics groove triple-stroke pass for repetitive punk/rock beats."""

from __future__ import annotations

from taikolab.arranger.mapper_drums import PlacedCandidate
from taikolab.arranger.section_builder import MeasurePlan
from taikolab.schemas import ChartMeasure, DifficultyLevel, TaikoNote

_DON = TaikoNote.DON.value
_KAT = TaikoNote.KAT.value
_BIG_DON = TaikoNote.BIG_DON.value
_BIG_KAT = TaikoNote.BIG_KAT.value
_HIT_CODES = frozenset({_DON, _KAT, _BIG_DON, _BIG_KAT})
_ROLL_CODES = frozenset({
    TaikoNote.DRUMROLL.value,
    TaikoNote.BIG_DRUMROLL.value,
    TaikoNote.BALLOON.value,
    TaikoNote.END.value,
})


def _max_bpm_for(difficulty: DifficultyLevel, configured_max_bpm: float) -> float:
    if difficulty == "normal":
        return max(120.0, configured_max_bpm - 15.0)
    return configured_max_bpm


def _find_pockets(
    notes: list[int],
    next_bar_notes: list[int] | None,
    step_unit: int,
) -> list[tuple[str, int]]:
    """Find relative 16-step pockets anywhere in the bar (handles pickup-shifted grooves like 2,4,6,14)."""
    def idx(slot16: int) -> int:
        return slot16 * step_unit

    def is_hit(slot16: int) -> bool:
        return 0 <= slot16 < 16 and notes[idx(slot16)] in _HIT_CODES

    def is_big(slot16: int) -> bool:
        return 0 <= slot16 < 16 and notes[idx(slot16)] in (_BIG_DON, _BIG_KAT)

    def is_empty(slot16: int) -> bool:
        if slot16 < 0:
            return True
        if slot16 >= 16:
            if next_bar_notes is None:
                return True
            nxt_idx = (slot16 - 16) * step_unit
            return nxt_idx >= len(next_bar_notes) or next_bar_notes[nxt_idx] == 0
        s0 = idx(slot16)
        return all(notes[s0 + k] == 0 for k in range(step_unit))

    pockets: list[tuple[str, int]] = []
    for s in range(0, 12):
        # A) Paired 8th pocket: hit(s), empty(s+1), hit(s+2), empty(s+3), hit(s+4) -> d-d-d _ k
        if (
            is_empty(s - 1)
            and is_hit(s)
            and is_empty(s + 1)
            and is_hit(s + 2)
            and not is_big(s + 2)
            and is_empty(s + 3)
            and is_hit(s + 4)
        ):
            pockets.append(("paired_fwd", s))

        # B) Open forward triple from anchor s: hit(s), empty(s+1..s+3), and hit(s+4) or empty(s+4..s+5)
        if (
            is_empty(s - 1)
            and is_hit(s)
            and is_empty(s + 1)
            and is_empty(s + 2)
            and is_empty(s + 3)
            and ((is_hit(s + 4) and is_empty(s + 5)) or (is_empty(s + 4) and is_empty(s + 5)))
        ):
            pockets.append(("open_fwd", s))

        # C) Open reverse triple: hit(s), empty(s+1..s+3), non-big-hit/empty(s+4), empty(s+5) -> k _ d-d-d
        if (
            is_empty(s - 1)
            and is_hit(s)
            and is_empty(s + 1)
            and is_empty(s + 2)
            and is_empty(s + 3)
            and not is_big(s + 4)
            and is_empty(s + 5)
            and (is_hit(s + 4) or is_empty(s + 6))
        ):
            pockets.append(("open_rev", s))

    return pockets


def _is_eligible_bar(
    measure: ChartMeasure,
    next_measure: ChartMeasure | None,
    plan: MeasurePlan,
    difficulty: DifficultyLevel,
) -> bool:
    if measure.numerator != 4 or measure.denominator != 4:
        return False
    grid = len(measure.notes)
    if grid not in (16, 32):
        return False
    if plan.low_energy_guitar or plan.is_blast_zone:
        return False
    if any(n in _ROLL_CODES for n in measure.notes):
        return False
    if measure.bpm > _max_bpm_for(difficulty, plan.groove_triple_max_bpm):
        return False

    step_unit = grid // 16
    active_slots = [idx for idx, n in enumerate(measure.notes) if n in _HIT_CODES]
    if not (3 <= len(active_slots) <= 6):
        return False
    dur = max(0.1, measure.end_sec - measure.start_sec)
    if (len(active_slots) / dur) >= 6.5:
        return False

    # Skip dense/gallop bars that already contain adjacent 16th-note runs (e.g. NOFX d-d-k)
    for i in range(len(active_slots) - 1):
        if (active_slots[i + 1] - active_slots[i]) <= step_unit:
            return False

    next_notes = next_measure.notes if next_measure else None
    return bool(_find_pockets(measure.notes, next_notes, step_unit))


def _drum_rhythm_signature(
    drum_candidates: dict[tuple[int, int], PlacedCandidate],
    measure_idx: int,
    grid_size: int,
) -> tuple[tuple[int, int], ...]:
    step_unit = max(1, grid_size // 16)
    return tuple(
        sorted(
            (s_idx // step_unit, cand.note_type)
            for (m_i, s_idx), cand in drum_candidates.items()
            if m_i == measure_idx and cand.source.startswith("drum:") and cand.note_type in _HIT_CODES
        )
    )


def _select_target_bars(
    eligible: list[bool],
    sigs: list[tuple[tuple[int, int], ...]],
    cap: int,
) -> list[int]:
    """Identify repeating groove runs and pick alternating 1st/3rd or 2nd/4th bars."""
    n = len(eligible)
    in_repeat = [False] * n
    for i in range(n):
        if not eligible[i] or not sigs[i]:
            continue
        for j in range(max(0, i - 4), min(n, i + 5)):
            if j != i and sigs[j] == sigs[i]:
                in_repeat[i] = True
                break

    runs: list[list[int]] = []
    cur: list[int] = []
    for i in range(n):
        if in_repeat[i]:
            cur.append(i)
        else:
            if len(cur) >= 3:
                runs.append(cur)
            cur = []
    if len(cur) >= 3:
        runs.append(cur)

    primary: list[int] = []
    secondary: list[int] = []
    for run_idx, run in enumerate(runs):
        prefer_odd = (run_idx % 2 == 0)
        for pos, m_idx in enumerate(run):
            if m_idx == 0:
                continue
            is_odd = (pos % 2 == 1)
            if is_odd == prefer_odd:
                primary.append(m_idx)
            else:
                secondary.append(m_idx)

    if cap <= len(primary):
        chosen = primary
    else:
        chosen = sorted(set(primary + secondary[: cap - len(primary)]))

    if len(chosen) > cap:
        if cap == 1:
            return [chosen[len(chosen) // 2]]
        last = len(chosen) - 1
        return [chosen[round(k * last / (cap - 1))] for k in range(cap)]
    return chosen


def _pattern_pools(complexity: float) -> tuple[list[tuple[int, int, int, int | None]], list[tuple[int, int, int]]]:
    low_fwd = [(_DON, _DON, _DON, None), (_KAT, _KAT, _KAT, None)]
    med_fwd = low_fwd + [
        (_DON, _DON, _KAT, _DON),
        (_KAT, _KAT, _DON, _KAT),
        (_DON, _DON, _DON, _KAT),
        (_KAT, _KAT, _KAT, _DON),
    ]
    high_fwd = med_fwd + [(_DON, _KAT, _DON, _KAT), (_KAT, _DON, _KAT, _DON)]

    low_rev = [(_DON, _DON, _DON), (_KAT, _KAT, _KAT)]
    med_rev = low_rev + [(_DON, _DON, _KAT), (_KAT, _KAT, _DON)]
    high_rev = med_rev + [(_DON, _KAT, _DON), (_KAT, _DON, _KAT)]

    if complexity <= 0.30:
        return low_fwd, low_rev
    if complexity <= 0.68:
        return med_fwd, med_rev
    return high_fwd, high_rev


def _try_apply_pocket(
    notes: list[int],
    next_bar_notes: list[int] | None,
    step_unit: int,
    complexity: float,
    variant_seed: int,
    difficulty: DifficultyLevel,
) -> bool:
    pockets = _find_pockets(notes, next_bar_notes, step_unit)
    if not pockets:
        return False

    fwd_pool, rev_pool = _pattern_pools(complexity)

    def idx(slot16: int) -> int:
        return slot16 * step_unit

    def is_big(slot16: int) -> bool:
        return notes[idx(slot16)] in (_BIG_DON, _BIG_KAT)

    kind, s = pockets[variant_seed % len(pockets)]

    def apply_pocket(p_kind: str, p_start: int, seed: int) -> bool:
        anchor_note = notes[idx(p_start)]
        base_color = _KAT if anchor_note in (_KAT, _BIG_KAT) else _DON

        if p_kind in ("paired_fwd", "open_fwd"):
            if complexity <= 0.30:
                pool = [p for p in fwd_pool if p[0] == base_color] or fwd_pool
            else:
                pool = fwd_pool
            t0, t1, t2, end_c = pool[seed % len(pool)]
            if not is_big(p_start):
                notes[idx(p_start)] = t0
            notes[idx(p_start + 1)] = t1
            notes[idx(p_start + 2)] = t2
            if notes[idx(p_start + 4)] == 0:
                notes[idx(p_start + 4)] = end_c if end_c is not None else (_KAT if t0 == _DON else _DON)
            elif end_c is not None and not is_big(p_start + 4):
                notes[idx(p_start + 4)] = end_c
            return True

        if p_kind == "open_rev":
            if complexity <= 0.30:
                other = _DON if base_color == _KAT else _KAT
                pool = [p for p in rev_pool if p[0] == other] or rev_pool
            else:
                pool = rev_pool
            t0, t1, t2 = pool[seed % len(pool)]
            if not is_big(p_start):
                notes[idx(p_start)] = _KAT if t0 == _DON else _DON
            notes[idx(p_start + 2)] = t0
            notes[idx(p_start + 3)] = t1
            if not is_big(p_start + 4):
                notes[idx(p_start + 4)] = t2
            return True

        return False

    placed = apply_pocket(kind, s, variant_seed)

    # On Oni at high complexity, optionally embellish a second non-overlapping pocket >= 6 steps away
    if placed and difficulty in ("oni", "ura") and complexity >= 0.80:
        second = next(((k, s2) for k, s2 in pockets if abs(s2 - s) >= 6), None)
        if second is not None:
            apply_pocket(second[0], second[1], variant_seed + 1)

    return placed


def apply_groove_triples(
    measures: list[ChartMeasure],
    drum_candidates: dict[tuple[int, int], PlacedCandidate],
    plans: list[MeasurePlan],
    difficulty: DifficultyLevel,
) -> list[ChartMeasure]:
    """Inject Taiko-style 16th-note triple-stroke accents into sparse repeating grooves."""
    if difficulty == "easy" or not measures or not plans:
        return measures

    cap = max((p.max_groove_triples for p in plans), default=0)
    if cap <= 0:
        return measures
    complexity = max(0.0, min(1.0, plans[0].groove_triple_complexity))

    eligible = [
        _is_eligible_bar(m, measures[i + 1] if i + 1 < len(measures) else None, plans[i], difficulty)
        for i, m in enumerate(measures)
    ]
    sigs = [
        _drum_rhythm_signature(drum_candidates, i, len(m.notes))
        for i, m in enumerate(measures)
    ]
    target_indices = _select_target_bars(eligible, sigs, cap)
    if not target_indices:
        return measures

    out = [m.model_copy(deep=True) for m in measures]
    for seq_idx, m_idx in enumerate(target_indices):
        m = out[m_idx]
        step_unit = max(1, len(m.notes) // 16)
        next_notes = out[m_idx + 1].notes if m_idx + 1 < len(out) else None
        _try_apply_pocket(
            m.notes,
            next_notes,
            step_unit=step_unit,
            complexity=complexity,
            variant_seed=seq_idx + m_idx,
            difficulty=difficulty,
        )

    return out
'@
Write-Utf8NoBom -Path $grooveTriplesPath -Content $grooveTriplesPy

# ---------------------------------------------------------------------------
# 4. Patch wizard.py, index.html, app.js (Groove Triple Max BPM slider)
# ---------------------------------------------------------------------------
$wizardPath = Join-Path $root "src\taikolab\ui\wizard.py"
Update-FileReplaceIdempotent -Path $wizardPath `
    -OldText "_DEFAULT_GROOVE_TRIPLE_COMPLEXITY = 35.0" `
    -NewText @'
_DEFAULT_GROOVE_TRIPLE_COMPLEXITY = 35.0
_DEFAULT_GROOVE_TRIPLE_MAX_BPM = 235.0
'@ `
    -AlreadyMarker "_DEFAULT_GROOVE_TRIPLE_MAX_BPM"

Update-FileReplaceIdempotent -Path $wizardPath `
    -OldText @'
        self.groove_triple_complexity = tk.DoubleVar(
            value=(
                chart_controls.groove_triple_complexity * 100.0
                if chart_controls.groove_triple_complexity is not None
                else _DEFAULT_GROOVE_TRIPLE_COMPLEXITY
            )
        )
'@ `
    -NewText @'
        self.groove_triple_complexity = tk.DoubleVar(
            value=(
                chart_controls.groove_triple_complexity * 100.0
                if chart_controls.groove_triple_complexity is not None
                else _DEFAULT_GROOVE_TRIPLE_COMPLEXITY
            )
        )
        self.groove_triple_max_bpm = tk.DoubleVar(
            value=(
                chart_controls.groove_triple_max_bpm
                if chart_controls.groove_triple_max_bpm is not None
                else _DEFAULT_GROOVE_TRIPLE_MAX_BPM
            )
        )
'@ `
    -AlreadyMarker "self.groove_triple_max_bpm"

Update-FileReplaceIdempotent -Path $wizardPath `
    -OldText @'
        self._add_slider(
            controls,
            23,
            "Groove triple complexity",
            self.groove_triple_complexity,
            0,
            100,
            5,
            "{:.0f}%",
            default_value=_DEFAULT_GROOVE_TRIPLE_COMPLEXITY,
        )
        ttk.Label(
            controls,
            text="Groove triple-stroke amount (sparse repeating punk/rock beats)",
            style="Muted.TLabel",
        ).grid(row=24, column=0, columnspan=4, sticky="w", pady=(8, 0))
        for row, difficulty in enumerate(("easy", "normal", "hard", "oni"), start=25):
'@ `
    -NewText @'
        self._add_slider(
            controls,
            23,
            "Groove triple max BPM",
            self.groove_triple_max_bpm,
            120,
            300,
            5,
            "{:.0f}",
            default_value=_DEFAULT_GROOVE_TRIPLE_MAX_BPM,
        )
        self._add_slider(
            controls,
            24,
            "Groove triple complexity",
            self.groove_triple_complexity,
            0,
            100,
            5,
            "{:.0f}%",
            default_value=_DEFAULT_GROOVE_TRIPLE_COMPLEXITY,
        )
        ttk.Label(
            controls,
            text="Groove triple-stroke amount (sparse repeating punk/rock beats)",
            style="Muted.TLabel",
        ).grid(row=25, column=0, columnspan=4, sticky="w", pady=(8, 0))
        for row, difficulty in enumerate(("easy", "normal", "hard", "oni"), start=26):
'@ `
    -AlreadyMarker '"Groove triple max BPM"'

Update-FileReplaceIdempotent -Path $wizardPath `
    -OldText @'
            groove_triple_complexity=(
                float(self.groove_triple_complexity.get()) / 100.0
                if hasattr(self, "groove_triple_complexity")
                else None
            ),
'@ `
    -NewText @'
            groove_triple_complexity=(
                float(self.groove_triple_complexity.get()) / 100.0
                if hasattr(self, "groove_triple_complexity")
                else None
            ),
            groove_triple_max_bpm=(
                float(self.groove_triple_max_bpm.get())
                if hasattr(self, "groove_triple_max_bpm")
                else None
            ),
'@ `
    -AlreadyMarker "groove_triple_max_bpm=("

$indexPath = Join-Path $root "src\taikolab\ui\static\index.html"
Update-FileReplaceIdempotent -Path $indexPath `
    -OldText @'
        <div class="ctrl-item">
          <label>Groove Triple Complexity (<span id="lblCtrlTripleComp">35%</span>)
            <div class="ctrl-row">
              <input type="range" id="ctrlTripleComp" min="0" max="100" step="5" value="35" />
              <button type="button" class="btn-xs" id="btnResetCtrlTripleComp">Reset</button>
            </div>
          </label>
        </div>
'@ `
    -NewText @'
        <div class="ctrl-item">
          <label>Groove Triple Complexity (<span id="lblCtrlTripleComp">35%</span>)
            <div class="ctrl-row">
              <input type="range" id="ctrlTripleComp" min="0" max="100" step="5" value="35" />
              <button type="button" class="btn-xs" id="btnResetCtrlTripleComp">Reset</button>
            </div>
          </label>
        </div>
        <div class="ctrl-item">
          <label>Groove Triple Max BPM (<span id="lblCtrlTripleBpm">235</span>)
            <div class="ctrl-row">
              <input type="range" id="ctrlTripleMaxBpm" min="120" max="300" step="5" value="235" />
              <button type="button" class="btn-xs" id="btnResetCtrlTripleBpm">Reset</button>
            </div>
          </label>
        </div>
'@ `
    -AlreadyMarker "ctrlTripleMaxBpm"

$appJsPath = Join-Path $root "src\taikolab\ui\static\app.js"
Update-FileReplaceIdempotent -Path $appJsPath `
    -OldText @'
  const compPct = Math.round((ov.groove_triple_complexity ?? 0.35) * 100);
  document.getElementById("ctrlTripleComp").value = String(compPct);
  document.getElementById("lblCtrlTripleComp").textContent = `${compPct}%`;
}
'@ `
    -NewText @'
  const compPct = Math.round((ov.groove_triple_complexity ?? 0.35) * 100);
  document.getElementById("ctrlTripleComp").value = String(compPct);
  document.getElementById("lblCtrlTripleComp").textContent = `${compPct}%`;

  const maxBpm = Math.round(ov.groove_triple_max_bpm ?? 235);
  document.getElementById("ctrlTripleMaxBpm").value = String(maxBpm);
  document.getElementById("lblCtrlTripleBpm").textContent = String(maxBpm);
}
'@ `
    -AlreadyMarker "ctrlTripleMaxBpm"

Update-FileReplaceIdempotent -Path $appJsPath `
    -OldText "  ov.groove_triple_complexity = parseFloat(document.getElementById(`"ctrlTripleComp`").value) / 100.0;" `
    -NewText @'
  ov.groove_triple_complexity = parseFloat(document.getElementById("ctrlTripleComp").value) / 100.0;
  ov.groove_triple_max_bpm = parseFloat(document.getElementById("ctrlTripleMaxBpm").value);
'@ `
    -AlreadyMarker "ov.groove_triple_max_bpm"

Update-FileReplaceIdempotent -Path $appJsPath `
    -OldText 'bindSliderPair("ctrlTripleComp", "lblCtrlTripleComp", (v) => `${Math.round(v)}%`, 35, "btnResetCtrlTripleComp");' `
    -NewText @'
bindSliderPair("ctrlTripleComp", "lblCtrlTripleComp", (v) => `${Math.round(v)}%`, 35, "btnResetCtrlTripleComp");
bindSliderPair("ctrlTripleMaxBpm", "lblCtrlTripleBpm", (v) => String(Math.round(v)), 235, "btnResetCtrlTripleBpm");
'@ `
    -AlreadyMarker "btnResetCtrlTripleBpm"

# ---------------------------------------------------------------------------
# 5. Add shifted-pickup Aliens Exist style test + max BPM slider test
# ---------------------------------------------------------------------------
$testExporterPath = Join-Path $root "tests\test_exporter_and_arranger.py"
$pickupTest = @'


def test_groove_triples_handle_pickup_shifted_2_4_6_14_beat_and_max_bpm_slider():
    cfg = load_config("punk_gallop")
    bpm = 215.0
    bar_dur = 240.0 / bpm
    beats = []
    drums = []
    for m_idx in range(8):
        start = m_idx * bar_dur
        for b in range(4):
            beats.append(BeatEvent(time_sec=start + b * (bar_dur / 4.0), is_downbeat=(b == 0)))
        for s_idx, cls in ((2, "kick"), (4, "kick"), (6, "snare"), (14, "snare")):
            drums.append(DrumHit(time_sec=start + bar_dur * (s_idx / 16.0), drum_class=cls, velocity=0.85))
    analysis = SongAnalysis(
        song_id="aliens_style",
        title="Aliens Style",
        audio_path="audio.wav",
        duration_sec=8 * bar_dur,
        beats=beats,
        tempo_regions=[TempoRegion(start_sec=0.0, end_sec=8 * bar_dur, bpm=bpm, numerator=4, denominator=4)],
        drum_hits=drums,
    )
    default_chart = arrange_song(analysis, cfg)
    hard = next(c for c in default_chart.courses if c.difficulty == "hard")
    normal = next(c for c in default_chart.courses if c.difficulty == "normal")
    assert any(sum(n != 0 for n in m.notes) >= 5 for m in hard.measures)
    assert any(sum(n != 0 for n in m.notes) >= 5 for m in normal.measures)

    capped = arrange_song(
        analysis,
        cfg,
        overrides=SongOverrides(song_id="aliens_style", groove_triple_max_bpm=190.0),
    )
    capped_hard = next(c for c in capped.courses if c.difficulty == "hard")
    assert all(sum(n != 0 for n in m.notes) == 4 for m in capped_hard.measures)
'@
$existingExporter = [System.IO.File]::ReadAllText($testExporterPath, [System.Text.UTF8Encoding]::new($false))
if (-not $existingExporter.Contains("test_groove_triples_handle_pickup_shifted_2_4_6_14_beat_and_max_bpm_slider")) {
    Write-Utf8NoBom -Path $testExporterPath -Content ($existingExporter.TrimEnd() + "`n" + $pickupTest.TrimStart())
}

if (Get-Command node -ErrorAction SilentlyContinue) {
    node --check $appJsPath
    if ($LASTEXITCODE -ne 0) { throw "node --check failed on app.js" }
}

.\scripts\run_tests.ps1
if ($LASTEXITCODE -ne 0) { throw "Test suite failed with exit code $LASTEXITCODE" }
Write-Host "Groove triples v2 (relative pockets + Max BPM slider) verified cleanly." -ForegroundColor Green