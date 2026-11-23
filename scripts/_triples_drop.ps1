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
    if ($usesCrlf) {
        $updated = $updated -replace "`n", "`r`n"
    }
    Write-Utf8NoBom -Path $Path -Content $updated
}

# ---------------------------------------------------------------------------
# 1. Patch src/taikolab/schemas.py
# ---------------------------------------------------------------------------
$schemasPath = Join-Path $root "src\taikolab\schemas.py"
Update-FileReplaceIdempotent -Path $schemasPath `
    -OldText @'
    fill_rolls_by_course: dict[DifficultyLevel, int] = Field(default_factory=dict)
    # Read older saved settings that used one cap for every course.
    fill_rolls_per_course: int | None = Field(default=None, ge=0, le=12)
    difficulty_overrides: dict[DifficultyLevel, DifficultyRuleOverride] = Field(
        default_factory=dict
    )

    @model_validator(mode="after")
    def _validate_course_roll_caps(self) -> ChartControlSettings:
        for difficulty, cap in self.fill_rolls_by_course.items():
            if not 0 <= cap <= 12:
                raise ValueError(
                    f"Fill-roll cap for {difficulty} must be between 0 and 12"
                )
        return self
'@ `
    -NewText @'
    fill_rolls_by_course: dict[DifficultyLevel, int] = Field(default_factory=dict)
    # Read older saved settings that used one cap for every course.
    fill_rolls_per_course: int | None = Field(default=None, ge=0, le=12)
    groove_triples_by_course: dict[DifficultyLevel, int] = Field(default_factory=dict)
    groove_triple_complexity: float | None = Field(default=None, ge=0.0, le=1.0)
    difficulty_overrides: dict[DifficultyLevel, DifficultyRuleOverride] = Field(
        default_factory=dict
    )

    @model_validator(mode="after")
    def _validate_course_roll_caps(self) -> ChartControlSettings:
        for difficulty, cap in self.fill_rolls_by_course.items():
            if not 0 <= cap <= 12:
                raise ValueError(
                    f"Fill-roll cap for {difficulty} must be between 0 and 12"
                )
        for difficulty, count in self.groove_triples_by_course.items():
            if not 0 <= count <= 60:
                raise ValueError(
                    f"Groove-triple amount for {difficulty} must be between 0 and 60"
                )
        return self
'@ `
    -AlreadyMarker "groove_triples_by_course"

# ---------------------------------------------------------------------------
# 2. Write src/taikolab/arranger/groove_triples.py
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


def _min_step_ms_for(difficulty: DifficultyLevel) -> float:
    if difficulty == "normal":
        return 75.0
    if difficulty == "hard":
        return 62.0
    return 58.0


def _is_eligible_bar(
    measure: ChartMeasure,
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

    step_unit = grid // 16
    sixteenth_ms = 1000.0 * (measure.end_sec - measure.start_sec) / 16.0
    if sixteenth_ms < _min_step_ms_for(difficulty):
        return False

    active_slots = [idx for idx, n in enumerate(measure.notes) if n in _HIT_CODES]
    if not (2 <= len(active_slots) <= 6):
        return False
    dur = max(0.1, measure.end_sec - measure.start_sec)
    if (len(active_slots) / dur) >= 6.5:
        return False

    # Skip dense/gallop bars that already contain adjacent 16th-note runs (e.g. NOFX d-d-k)
    for i in range(len(active_slots) - 1):
        if (active_slots[i + 1] - active_slots[i]) <= step_unit:
            return False

    return True


def _drum_rhythm_signature(
    drum_candidates: dict[tuple[int, int], PlacedCandidate],
    measure_idx: int,
    grid_size: int,
) -> tuple[int, ...]:
    step_unit = max(1, grid_size // 16)
    return tuple(
        sorted(
            s_idx // step_unit
            for (m_i, s_idx), cand in drum_candidates.items()
            if m_i == measure_idx and cand.source.startswith("drum:") and cand.note_type in _HIT_CODES
        )
    )


def _select_target_bars(
    eligible: list[bool],
    sigs: list[tuple[int, ...]],
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
            if len(cur) >= 2:
                runs.append(cur)
            cur = []
    if len(cur) >= 2:
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
    """Return (forward_triples_with_optional_end_color, reverse_triples) scaled by complexity."""
    # Forward tuple: (t0, t1, t2, optional_anchor_color_or_None)
    low_fwd = [
        (_DON, _DON, _DON, None),
        (_KAT, _KAT, _KAT, None),
    ]
    med_fwd = low_fwd + [
        (_DON, _DON, _KAT, _DON),
        (_KAT, _KAT, _DON, _KAT),
        (_DON, _DON, _DON, _KAT),
        (_KAT, _KAT, _KAT, _DON),
    ]
    high_fwd = med_fwd + [
        (_DON, _KAT, _DON, _KAT),
        (_KAT, _DON, _KAT, _DON),
    ]

    low_rev = [
        (_DON, _DON, _DON),
        (_KAT, _KAT, _KAT),
    ]
    med_rev = low_rev + [
        (_DON, _DON, _KAT),
        (_KAT, _KAT, _DON),
    ]
    high_rev = med_rev + [
        (_DON, _KAT, _DON),
        (_KAT, _DON, _KAT),
    ]

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
    fwd_pool, rev_pool = _pattern_pools(complexity)

    def idx(slot16: int) -> int:
        return slot16 * step_unit

    def is_hit(slot16: int) -> bool:
        return notes[idx(slot16)] in _HIT_CODES

    def is_big(slot16: int) -> bool:
        return notes[idx(slot16)] in (_BIG_DON, _BIG_KAT)

    def is_empty(slot16: int) -> bool:
        s0 = idx(slot16)
        return all(notes[s0 + k] == 0 for k in range(step_unit))

    # Candidate pockets inside a 16-step bar:
    # A) Front 0..4: [0: hit, 1: 0, 2: hit/0, 3: 0, 4: hit] -> d-d-d _ k
    can_front = (
        is_hit(0)
        and is_hit(4)
        and is_empty(1)
        and is_empty(3)
        and not is_big(2)
    )
    # B) Mid forward 4..8: [4: hit, 5: 0, 6: 0, 7: 0, 8: hit or empty-space] -> k-k-k _ k
    can_mid_fwd = (
        is_hit(4)
        and is_empty(5)
        and is_empty(6)
        and is_empty(7)
        and (is_hit(8) or (is_empty(8) and is_empty(9)))
    )
    # C) Mid reverse 4..8: [4: hit, 5: 0, 6: 0, 7: 0, 8: hit/0, 9: 0, 10: 0] -> k _ d-d-d
    can_mid_rev = (
        is_hit(4)
        and is_empty(5)
        and is_empty(6)
        and is_empty(7)
        and not is_big(8)
        and is_empty(9)
        and is_empty(10)
    )
    # D) Back reverse 8..12: [8: hit, 9: 0, 10: 0, 11: 0, 12: hit/0, 13: 0] -> k _ d-d-d
    next_has_0 = bool(next_bar_notes and next_bar_notes[0] in _HIT_CODES)
    can_back_rev = (
        is_hit(8)
        and is_empty(9)
        and is_empty(10)
        and is_empty(11)
        and not is_big(12)
        and is_empty(13)
        and (is_empty(14) or not next_has_0)
    )

    options: list[str] = []
    if can_front:
        options.append("front")
    if can_mid_fwd:
        options.append("mid_fwd")
    if can_mid_rev:
        options.append("mid_rev")
    if can_back_rev:
        options.append("back_rev")

    if not options:
        return False

    # Pick pocket deterministically so consecutive modified bars vary nicely
    pocket = options[variant_seed % len(options)]
    placed = False

    def apply_one(kind: str, pat_idx: int) -> bool:
        if kind == "front":
            # Prefer Don-led front triple at low complexity, then full pool
            pool = [p for p in fwd_pool if p[0] == _DON] if complexity <= 0.30 else fwd_pool
            t0, t1, t2, end_c = pool[pat_idx % len(pool)]
            if not is_big(0):
                notes[idx(0)] = t0
            notes[idx(1)] = t1
            notes[idx(2)] = t2
            if end_c is not None and not is_big(4):
                notes[idx(4)] = end_c
            return True

        if kind == "mid_fwd":
            pool = [p for p in fwd_pool if p[0] == _KAT] if complexity <= 0.30 else fwd_pool
            t0, t1, t2, end_c = pool[pat_idx % len(pool)]
            if not is_big(4):
                notes[idx(4)] = t0
            notes[idx(5)] = t1
            notes[idx(6)] = t2
            if notes[idx(8)] == 0:
                notes[idx(8)] = end_c if end_c is not None else t0
            elif end_c is not None and not is_big(8):
                notes[idx(8)] = end_c
            return True

        if kind == "mid_rev":
            t0, t1, t2 = rev_pool[pat_idx % len(rev_pool)]
            if not is_big(4):
                notes[idx(4)] = _KAT if t0 == _DON else _DON
            notes[idx(6)] = t0
            notes[idx(7)] = t1
            notes[idx(8)] = t2
            return True

        if kind == "back_rev":
            t0, t1, t2 = rev_pool[pat_idx % len(rev_pool)]
            if not is_big(8):
                notes[idx(8)] = _KAT if t0 == _DON else _DON
            notes[idx(10)] = t0
            notes[idx(11)] = t1
            notes[idx(12)] = t2
            return True
        return False

    placed = apply_one(pocket, variant_seed)

    # On Oni at very high complexity, allow a second non-overlapping pocket in the same sparse bar
    if (
        placed
        and difficulty in ("oni", "ura")
        and complexity >= 0.80
        and pocket == "front"
        and can_back_rev
    ):
        apply_one("back_rev", variant_seed + 1)

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
        _is_eligible_bar(m, plans[i], difficulty)
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
# 3. Patch section_builder.py & difficulty.py
# ---------------------------------------------------------------------------
$secBuilderPath = Join-Path $root "src\taikolab\arranger\section_builder.py"
Update-FileReplaceIdempotent -Path $secBuilderPath `
    -OldText @'
    quiet_guitar_behavior: str = "downbeat"
    max_fill_rolls: int = 0
'@ `
    -NewText @'
    quiet_guitar_behavior: str = "downbeat"
    max_fill_rolls: int = 0
    max_groove_triples: int = 0
    groove_triple_complexity: float = 0.35
'@ `
    -AlreadyMarker "max_groove_triples"

Update-FileReplaceIdempotent -Path $secBuilderPath `
    -OldText @'
    max_fill_rolls = 3 if difficulty in ("easy", "normal") else 0
    if overrides and difficulty in overrides.fill_rolls_by_course:
        max_fill_rolls = overrides.fill_rolls_by_course[difficulty]
    elif overrides and overrides.fill_rolls_per_course is not None:
        max_fill_rolls = overrides.fill_rolls_per_course
'@ `
    -NewText @'
    max_fill_rolls = 3 if difficulty in ("easy", "normal") else 0
    if overrides and difficulty in overrides.fill_rolls_by_course:
        max_fill_rolls = overrides.fill_rolls_by_course[difficulty]
    elif overrides and overrides.fill_rolls_per_course is not None:
        max_fill_rolls = overrides.fill_rolls_per_course

    default_groove_triples = {"easy": 0, "normal": 6, "hard": 24, "oni": 36, "ura": 40}.get(difficulty, 0)
    max_groove_triples = (
        overrides.groove_triples_by_course[difficulty]
        if overrides and difficulty in overrides.groove_triples_by_course
        else default_groove_triples
    )
    if overrides and difficulty in overrides.difficulty_overrides:
        d_scale = overrides.difficulty_overrides[difficulty].density_scale
        if d_scale is not None and d_scale < 1.0:
            max_groove_triples = int(round(max_groove_triples * d_scale))
    groove_triple_complexity = (
        overrides.groove_triple_complexity
        if overrides and overrides.groove_triple_complexity is not None
        else 0.35
    )
'@ `
    -AlreadyMarker "default_groove_triples"

Update-FileReplaceIdempotent -Path $secBuilderPath `
    -OldText @'
                quiet_guitar_behavior=quiet_guitar_behavior,
                max_fill_rolls=max_fill_rolls,
'@ `
    -NewText @'
                quiet_guitar_behavior=quiet_guitar_behavior,
                max_fill_rolls=max_fill_rolls,
                max_groove_triples=max_groove_triples,
                groove_triple_complexity=groove_triple_complexity,
'@ `
    -AlreadyMarker "max_groove_triples=max_groove_triples"

$difficultyPath = Join-Path $root "src\taikolab\arranger\difficulty.py"
Update-FileReplaceIdempotent -Path $difficultyPath `
    -OldText "from taikolab.arranger.hori_filter import build_ergonomic_measures_with_balloons" `
    -NewText @'
from taikolab.arranger.groove_triples import apply_groove_triples
from taikolab.arranger.hori_filter import build_ergonomic_measures_with_balloons
'@ `
    -AlreadyMarker "apply_groove_triples"

Update-FileReplaceIdempotent -Path $difficultyPath `
    -OldText @'
    measures, auto_balloons = build_ergonomic_measures_with_balloons(
        fused, slots, plans, cfg, effective_rule_diff, analysis=effective_analysis
    )

    if overrides and overrides.note_edits:
'@ `
    -NewText @'
    measures, auto_balloons = build_ergonomic_measures_with_balloons(
        fused, slots, plans, cfg, effective_rule_diff, analysis=effective_analysis
    )
    measures = apply_groove_triples(measures, drum_cands, plans, effective_rule_diff)

    if overrides and overrides.note_edits:
'@ `
    -AlreadyMarker "measures = apply_groove_triples"

# ---------------------------------------------------------------------------
# 4. Patch wizard.py, index.html, app.js (Groove Triples Amount + Complexity sliders)
# ---------------------------------------------------------------------------
$wizardPath = Join-Path $root "src\taikolab\ui\wizard.py"
Update-FileReplaceIdempotent -Path $wizardPath `
    -OldText '_DEFAULT_FILL_ROLLS_BY_COURSE = {"easy": 4, "normal": 4, "hard": 2, "oni": 2}' `
    -NewText @'
_DEFAULT_FILL_ROLLS_BY_COURSE = {"easy": 4, "normal": 4, "hard": 2, "oni": 2}
_DEFAULT_GROOVE_TRIPLES_BY_COURSE = {"easy": 0, "normal": 6, "hard": 24, "oni": 36}
_DEFAULT_GROOVE_TRIPLE_COMPLEXITY = 35.0
'@ `
    -AlreadyMarker "_DEFAULT_GROOVE_TRIPLES_BY_COURSE"

Update-FileReplaceIdempotent -Path $wizardPath `
    -OldText @'
        self.fill_roll_caps = {
            difficulty: tk.DoubleVar(
                value=chart_controls.fill_rolls_by_course.get(
                    difficulty,
                    (
                        chart_controls.fill_rolls_per_course
                        if chart_controls.fill_rolls_per_course is not None
                        else _DEFAULT_FILL_ROLLS_BY_COURSE[difficulty]
                    ),
                )
            )
            for difficulty in ("easy", "normal", "hard", "oni")
        }
'@ `
    -NewText @'
        self.fill_roll_caps = {
            difficulty: tk.DoubleVar(
                value=chart_controls.fill_rolls_by_course.get(
                    difficulty,
                    (
                        chart_controls.fill_rolls_per_course
                        if chart_controls.fill_rolls_per_course is not None
                        else _DEFAULT_FILL_ROLLS_BY_COURSE[difficulty]
                    ),
                )
            )
            for difficulty in ("easy", "normal", "hard", "oni")
        }
        self.groove_triple_amounts = {
            difficulty: tk.DoubleVar(
                value=chart_controls.groove_triples_by_course.get(
                    difficulty,
                    _DEFAULT_GROOVE_TRIPLES_BY_COURSE[difficulty],
                )
            )
            for difficulty in ("easy", "normal", "hard", "oni")
        }
        self.groove_triple_complexity = tk.DoubleVar(
            value=(
                chart_controls.groove_triple_complexity * 100.0
                if chart_controls.groove_triple_complexity is not None
                else _DEFAULT_GROOVE_TRIPLE_COMPLEXITY
            )
        )
'@ `
    -AlreadyMarker "self.groove_triple_amounts"

Update-FileReplaceIdempotent -Path $wizardPath `
    -OldText @'
        ttk.Label(controls, text="Quiet intro treatment").grid(
            row=22, column=0, sticky="w", padx=(0, 10), pady=(4, 0)
        )
        ttk.Combobox(
            controls,
            textvariable=self.quiet_guitar_behavior,
            values=("One downbeat", "Leave empty"),
            state="readonly",
        ).grid(row=22, column=1, sticky="w", pady=(4, 0))
'@ `
    -NewText @'
        ttk.Label(controls, text="Quiet intro treatment").grid(
            row=22, column=0, sticky="w", padx=(0, 10), pady=(4, 0)
        )
        ttk.Combobox(
            controls,
            textvariable=self.quiet_guitar_behavior,
            values=("One downbeat", "Leave empty"),
            state="readonly",
        ).grid(row=22, column=1, sticky="w", pady=(4, 0))
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
            self._add_slider(
                controls,
                row,
                difficulty.title(),
                self.groove_triple_amounts[difficulty],
                0,
                60,
                1,
                "{:.0f}",
                default_value=_DEFAULT_GROOVE_TRIPLES_BY_COURSE[difficulty],
            )
'@ `
    -AlreadyMarker "Groove triple complexity"

Update-FileReplaceIdempotent -Path $wizardPath `
    -OldText @'
            fill_rolls_by_course={
                difficulty: round(self.fill_roll_caps[difficulty].get())
                for difficulty in ("easy", "normal", "hard", "oni")
            },
            difficulty_overrides=difficulty_overrides,
'@ `
    -NewText @'
            fill_rolls_by_course={
                difficulty: round(self.fill_roll_caps[difficulty].get())
                for difficulty in ("easy", "normal", "hard", "oni")
            },
            groove_triples_by_course={
                difficulty: round(self.groove_triple_amounts[difficulty].get())
                for difficulty in ("easy", "normal", "hard", "oni")
            },
            groove_triple_complexity=float(self.groove_triple_complexity.get()) / 100.0,
            difficulty_overrides=difficulty_overrides,
'@ `
    -AlreadyMarker "groove_triples_by_course={"

$indexPath = Join-Path $root "src\taikolab\ui\static\index.html"
Update-FileReplaceIdempotent -Path $indexPath `
    -OldText @'
        <div class="ctrl-item">
          <label>Course Fill-Roll Cap (<span id="lblCtrlRolls">0</span>)
            <div class="ctrl-row">
              <input type="range" id="ctrlCourseRolls" min="0" max="12" step="1" value="0" />
              <button type="button" class="btn-xs" id="btnResetCtrlRolls">Reset</button>
            </div>
          </label>
        </div>
      </div>
'@ `
    -NewText @'
        <div class="ctrl-item">
          <label>Course Fill-Roll Cap (<span id="lblCtrlRolls">0</span>)
            <div class="ctrl-row">
              <input type="range" id="ctrlCourseRolls" min="0" max="12" step="1" value="0" />
              <button type="button" class="btn-xs" id="btnResetCtrlRolls">Reset</button>
            </div>
          </label>
        </div>
        <div class="ctrl-item">
          <label>Groove Triples Amount (<span id="lblCtrlTriples">24</span>)
            <div class="ctrl-row">
              <input type="range" id="ctrlCourseTriples" min="0" max="60" step="1" value="24" />
              <button type="button" class="btn-xs" id="btnResetCtrlTriples">Reset</button>
            </div>
          </label>
        </div>
        <div class="ctrl-item">
          <label>Groove Triple Complexity (<span id="lblCtrlTripleComp">35%</span>)
            <div class="ctrl-row">
              <input type="range" id="ctrlTripleComp" min="0" max="100" step="5" value="35" />
              <button type="button" class="btn-xs" id="btnResetCtrlTripleComp">Reset</button>
            </div>
          </label>
        </div>
      </div>
'@ `
    -AlreadyMarker "ctrlCourseTriples"

$appJsPath = Join-Path $root "src\taikolab\ui\static\app.js"
Update-FileReplaceIdempotent -Path $appJsPath `
    -OldText "const DEFAULT_ROLL_CAPS = { easy: 4, normal: 4, hard: 2, oni: 2, ura: 2 };" `
    -NewText @'
const DEFAULT_ROLL_CAPS = { easy: 4, normal: 4, hard: 2, oni: 2, ura: 2 };
const DEFAULT_GROOVE_TRIPLES = { easy: 0, normal: 6, hard: 24, oni: 36, ura: 40 };
'@ `
    -AlreadyMarker "DEFAULT_GROOVE_TRIPLES"

Update-FileReplaceIdempotent -Path $appJsPath `
    -OldText @'
  const rollCap = ov.fill_rolls_by_course?.[diff] ?? ov.fill_rolls_per_course ?? DEFAULT_ROLL_CAPS[diff] ?? 0;
  document.getElementById("ctrlCourseRolls").value = String(rollCap);
  document.getElementById("lblCtrlRolls").textContent = String(rollCap);
}
'@ `
    -NewText @'
  const rollCap = ov.fill_rolls_by_course?.[diff] ?? ov.fill_rolls_per_course ?? DEFAULT_ROLL_CAPS[diff] ?? 0;
  document.getElementById("ctrlCourseRolls").value = String(rollCap);
  document.getElementById("lblCtrlRolls").textContent = String(rollCap);

  const tripleCap = ov.groove_triples_by_course?.[diff] ?? DEFAULT_GROOVE_TRIPLES[diff] ?? 0;
  document.getElementById("ctrlCourseTriples").value = String(tripleCap);
  document.getElementById("lblCtrlTriples").textContent = String(tripleCap);

  const compPct = Math.round((ov.groove_triple_complexity ?? 0.35) * 100);
  document.getElementById("ctrlTripleComp").value = String(compPct);
  document.getElementById("lblCtrlTripleComp").textContent = `${compPct}%`;
}
'@ `
    -AlreadyMarker "ctrlCourseTriples"

Update-FileReplaceIdempotent -Path $appJsPath `
    -OldText @'
  if (!ov.fill_rolls_by_course) ov.fill_rolls_by_course = {};
  ov.fill_rolls_by_course[diff] = parseInt(document.getElementById("ctrlCourseRolls").value, 10);
'@ `
    -NewText @'
  if (!ov.fill_rolls_by_course) ov.fill_rolls_by_course = {};
  ov.fill_rolls_by_course[diff] = parseInt(document.getElementById("ctrlCourseRolls").value, 10);

  if (!ov.groove_triples_by_course) ov.groove_triples_by_course = {};
  ov.groove_triples_by_course[diff] = parseInt(document.getElementById("ctrlCourseTriples").value, 10);
  ov.groove_triple_complexity = parseFloat(document.getElementById("ctrlTripleComp").value) / 100.0;
'@ `
    -AlreadyMarker "ov.groove_triples_by_course"

Update-FileReplaceIdempotent -Path $appJsPath `
    -OldText @'
bindSliderPair(
  "ctrlCourseRolls",
  "lblCtrlRolls",
  (v) => String(Math.round(v)),
  () => DEFAULT_ROLL_CAPS[document.getElementById("diffSelect").value] ?? 0,
  "btnResetCtrlRolls"
);
'@ `
    -NewText @'
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
  (v) => String(Math.round(v)),
  () => DEFAULT_GROOVE_TRIPLES[document.getElementById("diffSelect").value] ?? 0,
  "btnResetCtrlTriples"
);
bindSliderPair("ctrlTripleComp", "lblCtrlTripleComp", (v) => `${Math.round(v)}%`, 35, "btnResetCtrlTripleComp");
'@ `
    -AlreadyMarker "btnResetCtrlTriples"

# ---------------------------------------------------------------------------
# 5. Add regression tests in tests/test_exporter_and_arranger.py
# ---------------------------------------------------------------------------
$testExporterPath = Join-Path $root "tests\test_exporter_and_arranger.py"
$tripleTests = @'


def _make_sparse_punk_beat(bars: int = 8, bpm: float = 215.0) -> SongAnalysis:
    bar_dur = 240.0 / bpm
    beats = []
    drums = []
    for m_idx in range(bars):
        start = m_idx * bar_dur
        for b in range(4):
            beats.append(BeatEvent(time_sec=start + b * (bar_dur / 4.0), is_downbeat=(b == 0)))
        # Sparse rock/punk pocket: Don(0), Don(2), Ka(4), ..., Ka(12)
        drums.append(DrumHit(time_sec=start + 0.0, drum_class="kick", velocity=0.85))
        drums.append(DrumHit(time_sec=start + bar_dur * (2.0 / 16.0), drum_class="kick", velocity=0.85))
        drums.append(DrumHit(time_sec=start + bar_dur * (4.0 / 16.0), drum_class="snare", velocity=0.85))
        drums.append(DrumHit(time_sec=start + bar_dur * (12.0 / 16.0), drum_class="snare", velocity=0.85))
    return SongAnalysis(
        song_id="sparse_punk",
        title="Sparse Punk",
        audio_path="audio.wav",
        duration_sec=bars * bar_dur,
        beats=beats,
        tempo_regions=[TempoRegion(start_sec=0.0, end_sec=bars * bar_dur, bpm=bpm, numerator=4, denominator=4)],
        drum_hits=drums,
    )


def test_groove_triples_embellish_sparse_repeating_punk_beat_and_skip_easy():
    cfg = load_config("punk_gallop")
    analysis = _make_sparse_punk_beat(bars=12, bpm=215.0)
    chart = arrange_song(analysis, cfg)
    by_diff = {c.difficulty: c for c in chart.courses}

    # Easy never gets groove triples (stays at <= 4 notes/bar)
    assert all(sum(n != 0 for n in m.notes) <= 4 for m in by_diff["easy"].measures)

    # Hard & Oni inject 16th-note triples with a breathing space into alternating bars
    hard_triple_bars = [m for m in by_diff["hard"].measures if sum(n != 0 for n in m.notes) >= 5]
    oni_triple_bars = [m for m in by_diff["oni"].measures if sum(n != 0 for n in m.notes) >= 5]
    assert len(hard_triple_bars) >= 3
    assert len(oni_triple_bars) >= 3


def test_groove_triples_skip_dense_fast_skate_punk_gallop():
    cfg = load_config("punk_gallop")
    bpm = 205.0
    bar_dur = 240.0 / bpm
    beats = []
    drums = []
    gallop_slots = ((0, "kick"), (2, "snare"), (4, "kick"), (5, "kick"), (6, "snare"), (8, "kick"), (10, "snare"), (12, "kick"), (13, "kick"), (14, "snare"))
    for m_idx in range(8):
        start = m_idx * bar_dur
        for b in range(4):
            beats.append(BeatEvent(time_sec=start + b * (bar_dur / 4.0), is_downbeat=(b == 0)))
        for s_idx, cls in gallop_slots:
            drums.append(DrumHit(time_sec=start + bar_dur * (s_idx / 16.0), drum_class=cls, velocity=0.85))
    analysis = SongAnalysis(
        song_id="nofx_gallop",
        title="NOFX Gallop",
        audio_path="audio.wav",
        duration_sec=8 * bar_dur,
        beats=beats,
        tempo_regions=[TempoRegion(start_sec=0.0, end_sec=8 * bar_dur, bpm=bpm, numerator=4, denominator=4)],
        drum_hits=drums,
    )
    chart_on = arrange_song(
        analysis,
        cfg,
        overrides=SongOverrides(song_id="nofx_gallop", groove_triples_by_course={"oni": 30}),
    )
    chart_off = arrange_song(
        analysis,
        cfg,
        overrides=SongOverrides(song_id="nofx_gallop", groove_triples_by_course={"oni": 0}),
    )
    oni_on = next(c for c in chart_on.courses if c.difficulty == "oni")
    oni_off = next(c for c in chart_off.courses if c.difficulty == "oni")
    assert [m.notes for m in oni_on.measures] == [m.notes for m in oni_off.measures]


def test_groove_triple_complexity_slider_controls_same_color_vs_alternating():
    cfg = load_config("punk_gallop")
    analysis = _make_sparse_punk_beat(bars=10, bpm=160.0)
    low = arrange_song(
        analysis,
        cfg,
        overrides=SongOverrides(
            song_id="sparse_punk",
            groove_triples_by_course={"oni": 10},
            groove_triple_complexity=0.0,
        ),
    )
    high = arrange_song(
        analysis,
        cfg,
        overrides=SongOverrides(
            song_id="sparse_punk",
            groove_triples_by_course={"oni": 10},
            groove_triple_complexity=1.0,
        ),
    )
    low_oni = next(c for c in low.courses if c.difficulty == "oni")
    high_oni = next(c for c in high.courses if c.difficulty == "oni")
    assert [m.notes for m in low_oni.measures] != [m.notes for m in high_oni.measures]
'@
$existingExporter = [System.IO.File]::ReadAllText($testExporterPath, [System.Text.UTF8Encoding]::new($false))
if (-not $existingExporter.Contains("test_groove_triples_embellish_sparse_repeating_punk_beat_and_skip_easy")) {
    Write-Utf8NoBom -Path $testExporterPath -Content ($existingExporter.TrimEnd() + "`n" + $tripleTests.TrimStart())
}

# ---------------------------------------------------------------------------
# 6. Verify JS syntax & run test suite
# ---------------------------------------------------------------------------
if (Get-Command node -ErrorAction SilentlyContinue) {
    Write-Host "Checking app.js syntax..."
    node --check $appJsPath
    if ($LASTEXITCODE -ne 0) { throw "node --check failed on app.js" }
}

Write-Host "Running TaikoLab test suite..."
.\scripts\run_tests.ps1
if ($LASTEXITCODE -ne 0) { throw "Test suite failed with exit code $LASTEXITCODE" }
Write-Host "Groove triples pass and sliders verified cleanly." -ForegroundColor Green