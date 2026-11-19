"""Local audio tags and optional Audiometa track lookup."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

AUDIOMETA_API_KEYRING_SERVICE = "TaikoLab"
AUDIOMETA_API_KEYRING_USER = "audiometa-api-key"
AUDIOMETA_SEARCH_URL = "https://api.audiometa.io/v1/search"


class MetadataLookupError(RuntimeError):
    """A local metadata or Audiometa lookup failed."""


@dataclass(frozen=True)
class TrackSuggestion:
    track_id: str
    title: str
    artist: str
    duration_ms: int | None
    url: str

    @property
    def display_text(self) -> str:
        if self.duration_ms is None:
            return f"{self.artist} - {self.title}"
        minutes, seconds = divmod(self.duration_ms // 1000, 60)
        return f"{self.artist} - {self.title} ({minutes}:{seconds:02d})"


def read_audio_tags(audio_path: Path) -> tuple[str, str]:
    """Read title and artist tags locally; return empty fields when tags are absent."""
    try:
        from mutagen import File as MutagenFile
        from mutagen import MutagenError
    except ImportError as exc:
        raise MetadataLookupError(
            "Audio tag reading requires the desktop extra. Install TaikoLab with its desktop dependencies."
        ) from exc

    try:
        audio = MutagenFile(audio_path, easy=True)
    except (OSError, MutagenError) as exc:
        raise MetadataLookupError(f"Could not read audio metadata from {audio_path.name}: {exc}") from exc

    if audio is None or audio.tags is None:
        return "", ""

    def _first_tag(*names: str) -> str:
        for name in names:
            values = audio.tags.get(name)
            if values:
                first = values[0] if isinstance(values, (list, tuple)) else values
                value = str(first).strip()
                if value:
                    return value
        return ""

    return _first_tag("title"), _first_tag("artist", "albumartist")


def suggest_title_artist_from_filename(audio_path: Path) -> tuple[str, str]:
    """Suggest metadata from a common 'artist - title' filename, without asserting it."""
    stem = audio_path.stem.strip()
    for separator in (" - ", " – ", " — "):
        if separator in stem:
            artist, title = stem.split(separator, maxsplit=1)
            if artist.strip() and title.strip():
                return title.strip(), artist.strip()
    return stem, ""


def get_saved_audiometa_api_key() -> str | None:
    """Read the key from the process environment or the OS credential store."""
    environment_key = os.environ.get("AUDIOMETA_API_KEY", "").strip()
    if environment_key:
        return environment_key

    try:
        import keyring
        from keyring.errors import KeyringError
    except ImportError:
        return None

    try:
        return keyring.get_password(
            AUDIOMETA_API_KEYRING_SERVICE,
            AUDIOMETA_API_KEYRING_USER,
        )
    except (KeyringError, RuntimeError):
        return None


def save_audiometa_api_key(api_key: str) -> None:
    """Save an API key to the OS credential store, never to project files."""
    try:
        import keyring
        from keyring.errors import KeyringError
    except ImportError as exc:
        raise MetadataLookupError(
            "Secure API-key storage requires the desktop extra. Install TaikoLab with its desktop dependencies."
        ) from exc

    try:
        priority = keyring.get_keyring().priority
    except (KeyringError, RuntimeError) as exc:
        raise MetadataLookupError(f"Could not access a secure credential store: {exc}") from exc
    if priority <= 0:
        raise MetadataLookupError(
            "No secure credential-store backend is available. Set AUDIOMETA_API_KEY for this session instead."
        )

    try:
        keyring.set_password(
            AUDIOMETA_API_KEYRING_SERVICE,
            AUDIOMETA_API_KEYRING_USER,
            api_key,
        )
    except (KeyringError, RuntimeError) as exc:
        raise MetadataLookupError(f"Could not save the API key securely: {exc}") from exc


def search_audiometa_tracks(
    artist: str,
    title: str,
    api_key: str,
    timeout_sec: float = 8.0,
) -> list[TrackSuggestion]:
    """Search Audiometa by metadata text; never uploads or opens the audio file."""
    query = " ".join(part.strip() for part in (artist, title) if part.strip())
    if not query:
        raise MetadataLookupError("Enter an artist or title before searching Audiometa.")
    if not api_key.strip():
        raise MetadataLookupError("Add an Audiometa API key in the wizard settings before searching.")

    url = f"{AUDIOMETA_SEARCH_URL}?{urlencode({'q': query, 'type': 'track', 'limit': 10})}"
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key.strip()}",
            "User-Agent": "TaikoLab/0.1.0 (https://github.com/Reg0lino/TaikoLab)",
        },
    )
    try:
        with urlopen(request, timeout=timeout_sec) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 401:
            raise MetadataLookupError("Audiometa rejected the API key. Check it in the wizard settings.") from exc
        if exc.code == 429:
            raise MetadataLookupError("Audiometa rate limit reached. Try the lookup again later.") from exc
        raise MetadataLookupError(f"Audiometa lookup failed with HTTP {exc.code}.") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise MetadataLookupError(f"Could not reach Audiometa: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MetadataLookupError("Audiometa returned an unreadable response.") from exc

    if not isinstance(payload, dict):
        raise MetadataLookupError("Audiometa returned an unexpected response.")
    track_page = payload.get("tracks")
    if not isinstance(track_page, dict):
        return []
    tracks = track_page.get("items", [])
    if not isinstance(tracks, list):
        raise MetadataLookupError("Audiometa returned an invalid track list.")
    suggestions: list[TrackSuggestion] = []
    for track in tracks:
        if not isinstance(track, dict):
            continue
        artists = track.get("artists") or []
        if not isinstance(artists, list):
            continue
        artist_names = ", ".join(
            str(item.get("name", "")).strip()
            for item in artists
            if isinstance(item, dict) and item.get("name")
        )
        track_id = str(track.get("id", "")).strip()
        track_title = str(track.get("name", "")).strip()
        if not track_id or not track_title or not artist_names:
            continue
        duration_ms = track.get("duration_ms")
        suggestions.append(
            TrackSuggestion(
                track_id=track_id,
                title=track_title,
                artist=artist_names,
                duration_ms=duration_ms if isinstance(duration_ms, int) else None,
                url=str(track.get("href", "")),
            )
        )
    return suggestions
