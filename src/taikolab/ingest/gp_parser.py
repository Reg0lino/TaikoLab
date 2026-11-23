"""Native Guitar Pro (.gp GP7+ ZIP/XML) section & articulation sidecar parser."""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from taikolab.schemas import GpHints, GpSectionMarker


def classify_gp_section_role(label: str) -> str:
    low = label.strip().lower()
    if any(k in low for k in ("intro", "clean", "acoustic", "quiet", "ambien")):
        return "intro"
    if any(k in low for k in ("pre-chorus", "pre chorus", "prechorus", "buildup", "build")):
        return "pre_chorus"
    if any(k in low for k in ("chorus", "refrain", "hook")):
        return "chorus"
    if any(k in low for k in ("verse",)):
        return "verse"
    if any(k in low for k in ("solo", "lead")):
        return "solo"
    if any(k in low for k in ("bridge", "breakdown", "break")):
        return "bridge"
    if any(k in low for k in ("interlude", "riff", "inst")):
        return "interlude"
    if any(k in low for k in ("outro", "ending", "coda")):
        return "outro"
    return "unknown"


def _parse_gpif_xml(xml_bytes: bytes) -> GpHints:
    root = ET.fromstring(xml_bytes)

    track_roles: list[str] = []
    for el in root.findall("./Tracks/Track/Name"):
        if el.text and el.text.strip():
            track_roles.append(el.text.strip())

    # Index Note -> has_accent
    accent_notes: set[str] = set()
    for note_el in root.findall("./Notes/Note"):
        nid = note_el.attrib.get("id")
        if not nid:
            continue
        acc = note_el.find("Accent")
        if acc is not None and (acc.text or "").strip() not in ("", "0"):
            accent_notes.add(nid)

    # Index Beat -> (has_palm, has_tremolo, has_accent)
    beat_info: dict[str, tuple[bool, bool, bool]] = {}
    for beat_el in root.findall("./Beats/Beat"):
        bid = beat_el.attrib.get("id")
        if not bid:
            continue
        has_palm = beat_el.find("./Properties/Property[@name='PalmMuted']") is not None
        has_trem = (
            beat_el.find("./Properties/Property[@name='Tremolo']") is not None
            or beat_el.find("Tremolo") is not None
            or beat_el.find("Trill") is not None
        )
        notes_el = beat_el.find("Notes")
        has_acc = False
        if notes_el is not None and notes_el.text:
            has_acc = any(nid in accent_notes for nid in notes_el.text.split())
        beat_info[bid] = (has_palm, has_trem, has_acc)

    # Index Voice -> list of beat ids
    voice_beats: dict[str, list[str]] = {}
    for v_el in root.findall("./Voices/Voice"):
        vid = v_el.attrib.get("id")
        beats_el = v_el.find("Beats")
        if vid and beats_el is not None and beats_el.text:
            voice_beats[vid] = beats_el.text.split()

    # Index Bar -> (has_palm, has_trem, has_acc)
    bar_flags: dict[str, tuple[bool, bool, bool]] = {}
    for bar_el in root.findall("./Bars/Bar"):
        bar_id = bar_el.attrib.get("id")
        voices_el = bar_el.find("Voices")
        if not bar_id or voices_el is None or not voices_el.text:
            continue
        p = t = a = False
        for vid in voices_el.text.split():
            if vid == "-1":
                continue
            for bid in voice_beats.get(vid, ()):
                bp, bt, ba = beat_info.get(bid, (False, False, False))
                p = p or bp
                t = t or bt
                a = a or ba
        bar_flags[bar_id] = (p, t, a)

    sections: list[GpSectionMarker] = []
    accent_bars: list[int] = []
    tremolo_bars: list[int] = []
    palm_mute_bars: list[int] = []
    bar_meters: dict[int, tuple[int, int]] = {}
    cur_meter = (4, 4)

    for mb_idx, mb_el in enumerate(root.findall("./MasterBars/MasterBar")):
        time_el = mb_el.find("./Time")
        if time_el is not None and time_el.text and "/" in time_el.text:
            try:
                num_s, den_s = time_el.text.strip().split("/", 1)
                cur_meter = (int(num_s), int(den_s))
            except Exception:
                pass
        bar_meters[mb_idx] = cur_meter
        sec_text_el = mb_el.find("./Section/Text")
        if sec_text_el is not None and sec_text_el.text and sec_text_el.text.strip():
            lbl = " ".join(sec_text_el.text.strip().split())
            sections.append(
                GpSectionMarker(
                    bar_idx=mb_idx,
                    label=lbl,
                    role=classify_gp_section_role(lbl),
                )
            )
        bars_el = mb_el.find("Bars")
        if bars_el is not None and bars_el.text:
            p = t = a = False
            for bar_id in bars_el.text.split():
                bp, bt, ba = bar_flags.get(bar_id, (False, False, False))
                p = p or bp
                t = t or bt
                a = a or ba
            if a:
                accent_bars.append(mb_idx)
            if t:
                tremolo_bars.append(mb_idx)
            if p:
                palm_mute_bars.append(mb_idx)

    return GpHints(
        sections=sections,
        accent_bars=accent_bars,
        tremolo_bars=tremolo_bars,
        palm_mute_bars=palm_mute_bars,
        track_roles=track_roles,
        bar_meters=bar_meters,
    )


def parse_gp_hints(gp_path: Path) -> GpHints:
    """Extract section markers & articulation bars from a .gp file cleanly."""
    if not gp_path.is_file():
        return GpHints()

    if zipfile.is_zipfile(gp_path):
        try:
            with zipfile.ZipFile(gp_path) as zf:
                if "Content/score.gpif" in zf.namelist():
                    return _parse_gpif_xml(zf.read("Content/score.gpif"))
        except Exception as exc:
            print(f"[TaikoLab GP] Warning: could not parse GP7+ XML in {gp_path.name}: {exc}")
            return GpHints()

    print(f"[TaikoLab GP] Note: {gp_path.name} is not a GP7+ ZIP/XML file; continuing without GP section hints.")
    return GpHints()