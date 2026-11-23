"""Safe staging and naming helpers for new automatic song builds."""

from __future__ import annotations

import json
import re
import shutil
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

_AUDIO_EXTENSIONS = {".mp3", ".ogg", ".wav", ".flac"}


def _slug_part(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")


def make_song_id(artist: str, title: str, variant: int = 1) -> str:
    """Create a stable filesystem-safe ID ending in '-auto'."""
    if variant < 1:
        raise ValueError("Song name variant must be a positive integer.")
    artist_slug = _slug_part(artist)
    title_slug = _slug_part(title)
    if not artist_slug or not title_slug:
        raise ValueError("Artist and title must contain letters or numbers.")
    suffix = "-auto" if variant == 1 else f"-{variant}-auto"
    available = 120 - len(suffix) - 1
    identity = f"{artist_slug}-{title_slug}"[:available].strip("-")
    if not identity:
        raise ValueError("Artist and title are too long to create a safe song folder name.")
    return f"{identity}{suffix}"


def normalize_songs_directory(selected_path: Path) -> Path:
    """Accept a Songs folder, YataiDON/OpenTaiko executable, or install folder."""
    path = selected_path.expanduser()
    if path.is_file():
        if path.suffix.lower() != ".exe":
            raise ValueError("Choose the Songs folder or the YataiDON/OpenTaiko executable.")
        songs_dir = path.parent / "Songs"
        if not songs_dir.is_dir():
            raise FileNotFoundError(f"Could not find a Songs folder beside {path.name}: {songs_dir}")
        return songs_dir.resolve()
    if not path.is_dir():
        raise FileNotFoundError(f"Selected location is not an existing folder: {path}")
    if path.name.casefold() == "songs":
        return path.resolve()
    child_songs = path / "Songs"
    if child_songs.is_dir():
        return child_songs.resolve()
    raise FileNotFoundError(
        f"Could not find a Songs folder in {path}. Choose the Songs folder or the game executable."
    )


_GP_EXTENSIONS = {".gp", ".gp5", ".gpx", ".gp4", ".gp3"}


def find_staged_gp_input(song_dir: Path) -> Path | None:
    for ext in (".gp", ".gpx", ".gp5", ".gp4", ".gp3"):
        cand = song_dir / f"input{ext}"
        if cand.is_file():
            return cand
    return None


def stage_song_inputs(
    song_dir: Path,
    audio_source: Path,
    midi_source: Path | None,
    gp_source: Path | None = None,
) -> tuple[Path, Path | None]:
    """Copy chosen source files into one new song folder using standard filenames."""
    if song_dir.exists():
        raise FileExistsError(f"Song folder already exists: {song_dir}")
    audio_source = audio_source.expanduser().resolve(strict=True)
    audio_ext = audio_source.suffix.lower()
    if audio_ext not in _AUDIO_EXTENSIONS:
        raise ValueError(f"Unsupported audio extension '{audio_ext}'.")
    if not audio_source.is_file():
        raise FileNotFoundError(f"Audio file not found: {audio_source}")

    midi_path: Path | None = None
    if midi_source is not None:
        midi_source = midi_source.expanduser().resolve(strict=True)
        if midi_source.suffix.lower() not in (".mid", ".midi"):
            raise ValueError("The wizard accepts MIDI files (.mid/.midi); GP files are not used.")
        if not midi_source.is_file():
            raise FileNotFoundError(f"MIDI file not found: {midi_source}")

    if gp_source is not None:
        gp_source = gp_source.expanduser().resolve(strict=True)
        if gp_source.suffix.lower() not in _GP_EXTENSIONS:
            raise ValueError("Choose a Guitar Pro file (.gp, .gpx, .gp5).")
        if not gp_source.is_file():
            raise FileNotFoundError(f"Guitar Pro file not found: {gp_source}")

    song_dir.mkdir(parents=True)
    audio_path = song_dir / f"audio{audio_ext}"
    shutil.copy2(audio_source, audio_path)
    if midi_source is not None:
        midi_path = song_dir / "input.mid"
        shutil.copy2(midi_source, midi_path)
    if gp_source is not None:
        shutil.copy2(gp_source, song_dir / f"input{gp_source.suffix.lower()}")
    return audio_path, midi_path


def write_build_manifest(
    song_dir: Path,
    *,
    song_id: str,
    artist: str,
    title: str,
    preset: str,
    audio_source: Path,
    midi_source: Path | None,
    metadata_source: str,
    status: str,
    error: str | None = None,
    gp_source: Path | None = None,
) -> Path:
    """Record non-secret inputs and build state beside the generated chart."""
    manifest = {
        "schema_version": 1,
        "song_id": song_id,
        "artist": artist,
        "title": title,
        "preset": preset,
        "metadata_source": metadata_source,
        "audio_source_name": audio_source.name,
        "midi_source_name": midi_source.name if midi_source else None,
        "gp_source_name": gp_source.name if gp_source else None,
        "gp_supported": bool(gp_source),
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if error:
        manifest["error"] = error

    manifest_path = song_dir / "build_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path
