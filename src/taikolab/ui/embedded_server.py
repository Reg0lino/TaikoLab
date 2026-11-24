"""Lifecycle helpers for the editor's loopback-only ASGI server."""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import uvicorn

from taikolab.ui.server import create_app


class EmbeddedEditorServer:
    def __init__(self) -> None:
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None
        self.url: str | None = None

    def start(self) -> str:
        if self.url and self._thread and self._thread.is_alive():
            return self.url

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        listener.setblocking(False)

        config = uvicorn.Config(create_app(), host="127.0.0.1", port=listener.getsockname()[1], log_level="error")
        server = uvicorn.Server(config)
        thread = threading.Thread(
            target=server.run,
            kwargs={"sockets": [listener]},
            name="taikolab-editor-server",
            daemon=True,
        )
        thread.start()

        deadline = time.monotonic() + 10.0
        while not server.started:
            if not thread.is_alive():
                listener.close()
                raise RuntimeError("The chart editor server stopped before it was ready")
            if time.monotonic() >= deadline:
                server.should_exit = True
                thread.join(timeout=5.0)
                listener.close()
                raise TimeoutError("The chart editor server did not start within 10 seconds")
            time.sleep(0.02)

        self._server = server
        self._thread = thread
        self._socket = listener
        self.url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        return self.url

    def open_tja(self, path: Path) -> str:
        base_url = self.start()
        request = Request(
            f"{base_url}/api/import-tja",
            data=json.dumps({"path": str(path)}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=10.0) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("detail", str(exc))
            except (UnicodeDecodeError, json.JSONDecodeError):
                detail = str(exc)
            raise ValueError(f"Could not open TJA: {detail}") from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Could not open TJA through the local editor server: {exc}") from exc

        import_id = result.get("import_id")
        if not isinstance(import_id, str) or not import_id:
            raise RuntimeError("The editor server did not return a TJA import identifier")
        self.last_import_has_audio = bool(result.get("has_audio", False))
        return import_id

    def attach_tja_audio(self, import_id: str, audio_path: Path) -> bool:
        if not self.url:
            raise RuntimeError("The local editor server is not running.")
        request = Request(
            f"{self.url}/api/imported/{import_id}/attach-audio",
            data=json.dumps({"path": str(audio_path)}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=10.0) as response:
            result = json.loads(response.read().decode("utf-8"))
        return bool(result.get("has_audio", False))

    def stop(self) -> None:
        server = self._server
        thread = self._thread
        listener = self._socket
        self.url = None
        self._server = None
        self._thread = None
        self._socket = None
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(timeout=5.0)
            if thread.is_alive():
                raise RuntimeError("The chart editor server did not shut down cleanly")
        if listener is not None:
            listener.close()
