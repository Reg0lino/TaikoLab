$ErrorActionPreference = "Stop"

$root = (Get-Location).Path

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

$appJsPath = Join-Path $root "src\taikolab\ui\static\app.js"

Update-FileReplaceIdempotent -Path $appJsPath `
    -OldText @'
["secFollow", "secBpmMult", "secMeter", "secBigNotes", "secFoldRolls"].forEach((id) => {
  document.getElementById(id).addEventListener("change", () => {
    readInspectorIntoSelected();
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
'@ `
    -NewText @'
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
'@ `
    -AlreadyMarker "function markSectionBoundary("

Update-FileReplaceIdempotent -Path $appJsPath `
    -OldText @'
document.getElementById("btnApplySection").addEventListener("click", () => {
  readInspectorIntoSelected();
  state.editSeq += 1;
  rebuildChart(false);
  showToast("Section rebuilt!");
});
'@ `
    -NewText @'
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
'@ `
    -AlreadyMarker "btnConfirmApplySection"

$testExporterPath = Join-Path $root "tests\test_exporter_and_arranger.py"
$pass2ExporterTest = @'


def test_per_difficulty_section_rule_applies_drums_vocals_without_clobbering_other_courses():
    from taikolab.schemas import SectionDifficultyRule

    cfg = load_config("punk_gallop")
    bar_dur = 1.6  # 150 BPM
    beats = [BeatEvent(time_sec=i * 0.4, is_downbeat=(i % 4 == 0)) for i in range(16)]
    drums = []
    vocals = []
    for m_idx in range(4):
        start = m_idx * bar_dur
        for b in range(4):
            drums.append(DrumHit(time_sec=start + b * 0.4, drum_class="kick" if b % 2 == 0 else "snare", velocity=0.85))
        vocals.append(OnsetEvent(time_sec=start + 0.2, strength=0.85))
        vocals.append(OnsetEvent(time_sec=start + 1.0, strength=0.85))

    analysis = SongAnalysis(
        song_id="sec_diff_rules",
        title="Section Diff Rules",
        audio_path="audio.wav",
        duration_sec=4 * bar_dur,
        beats=beats,
        tempo_regions=[TempoRegion(start_sec=0.0, end_sec=4 * bar_dur, bpm=150.0, numerator=4, denominator=4)],
        drum_hits=drums,
        vocal_events=vocals,
    )
    baseline = arrange_song(analysis, cfg)
    custom = arrange_song(
        analysis,
        cfg,
        overrides=SongOverrides(
            song_id="sec_diff_rules",
            sections=[
                SectionOverride(
                    start_sec=0.0,
                    end_sec=2 * bar_dur,
                    label="Verse",
                    difficulty_rules={
                        "hard": SectionDifficultyRule(follow_mode="drums_vocals"),
                        "easy": SectionDifficultyRule(follow_mode="drums_vocals"),
                    },
                ),
                SectionOverride(start_sec=2 * bar_dur, end_sec=4 * bar_dur, label="Chorus"),
            ],
        ),
    )

    base_hard = next(c for c in baseline.courses if c.difficulty == "hard")
    cust_hard = next(c for c in custom.courses if c.difficulty == "hard")
    base_oni = next(c for c in baseline.courses if c.difficulty == "oni")
    cust_oni = next(c for c in custom.courses if c.difficulty == "oni")
    cust_easy = next(c for c in custom.courses if c.difficulty == "easy")

    assert sum(n != 0 for n in cust_hard.measures[0].notes) > sum(n != 0 for n in base_hard.measures[0].notes)
    assert cust_hard.measures[2].notes == base_hard.measures[2].notes
    assert [m.notes for m in cust_oni.measures] == [m.notes for m in base_oni.measures]
    assert analyze_course(cust_easy).avg_nps <= cfg.hori.max_nps_easy + 0.6
'@
$existingExporter = [System.IO.File]::ReadAllText($testExporterPath, [System.Text.UTF8Encoding]::new($false))
if (-not $existingExporter.Contains("test_per_difficulty_section_rule_applies_drums_vocals_without_clobbering_other_courses")) {
    Write-Utf8NoBom -Path $testExporterPath -Content ($existingExporter.TrimEnd() + "`n" + $pass2ExporterTest.TrimStart())
}

$testUiPath = Join-Path $root "tests\test_ui_and_overrides.py"
$pass2UiTest = @'


def test_auto_split_sections_endpoint_detects_intro_and_chorus(tmp_path: Path, monkeypatch):
    from taikolab.schemas import EnergyPoint

    song_dir = tmp_path / "split_song"
    song_dir.mkdir()
    sf.write(str(song_dir / "audio.wav"), np.zeros(22050 * 16, dtype=np.float32), 22050)

    beats = [BeatEvent(time_sec=i * 0.5, is_downbeat=(i % 4 == 0)) for i in range(32)]
    drums = [
        DrumHit(time_sec=8.0 + i * 0.5, drum_class="kick" if i % 2 == 0 else "snare", velocity=0.9)
        for i in range(16)
    ]
    vocals = [OnsetEvent(time_sec=8.25 + i * 1.0, strength=0.8) for i in range(8)]
    guitars = [OnsetEvent(time_sec=0.5 + i * 1.0, strength=0.7) for i in range(8)]
    energy = [
        EnergyPoint(time_sec=i * 0.2, drums_rms=0.05 if i * 0.2 < 8.0 else 0.85, guitar_rms=0.2 if i * 0.2 < 8.0 else 0.8)
        for i in range(80)
    ]
    analysis = SongAnalysis(
        song_id="split_song",
        title="Split Song",
        artist="Tester",
        audio_path="audio.wav",
        duration_sec=16.0,
        beats=beats,
        tempo_regions=[TempoRegion(start_sec=0.0, end_sec=16.0, bpm=120.0, numerator=4, denominator=4)],
        drum_hits=drums,
        vocal_events=vocals,
        guitar_events=guitars,
        energy_curve=energy,
    )
    (song_dir / "analysis.json").write_text(analysis.model_dump_json(), encoding="utf-8")

    from taikolab import config as cfg_mod
    real_load = cfg_mod.load_config

    def fake_load(preset_name=None, root_dir=None):
        c = real_load(preset_name, root_dir)
        c.settings.workspace_dir = str(tmp_path)
        return c

    import taikolab.ui.server as srv_mod
    monkeypatch.setattr(srv_mod, "load_config", fake_load)

    client = TestClient(create_app())
    res = client.post("/api/songs/split_song/sections/auto-split")
    assert res.status_code == 200
    secs = res.json()["sections"]
    assert len(secs) >= 2
    assert secs[0]["start_sec"] == 0.0
    assert secs[-1]["end_sec"] == 16.0
    assert abs(secs[0]["end_sec"] - 8.0) < 0.1
'@
$existingTestUi = [System.IO.File]::ReadAllText($testUiPath, [System.Text.UTF8Encoding]::new($false))
if (-not $existingTestUi.Contains("test_auto_split_sections_endpoint_detects_intro_and_chorus")) {
    Write-Utf8NoBom -Path $testUiPath -Content ($existingTestUi.TrimEnd() + "`n" + $pass2UiTest.TrimStart())
}

if (Get-Command node -ErrorAction SilentlyContinue) {
    Write-Host "Checking app.js syntax..."
    node --check $appJsPath
    if ($LASTEXITCODE -ne 0) { throw "node --check failed on app.js" }
}

.\scripts\run_tests.ps1
if ($LASTEXITCODE -ne 0) { throw "Test suite failed with exit code $LASTEXITCODE" }
Write-Host "Pass 2 verified cleanly." -ForegroundColor Green