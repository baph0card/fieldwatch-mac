"""Local web UI + JSON API. Binds to 127.0.0.1 only.

The page shows SSIDs and device names that anyone nearby chose, so every response is treated as hostile
text by the UI (it escapes everything). The server also rejects foreign Host/Origin headers so a web page
in another tab cannot read the data or clear it (DNS-rebinding / CSRF).
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .store import DeviceStore

STATIC = Path(__file__).parent / "static"


def make_server(store: DeviceStore, port: int) -> ThreadingHTTPServer:
    allowed_hosts: set[str] = set()  # filled in once the real port is known (port 0 = pick a free one)

    class Handler(BaseHTTPRequestHandler):
        server_version = "FieldwatchMac"

        def log_message(self, *a) -> None:  # quiet
            pass

        def _host_ok(self) -> bool:
            return (self.headers.get("Host") or "") in allowed_hosts

        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if not self._host_ok():
                return self._send(403, b"bad host", "text/plain")
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            if path == "/api/snapshot":
                return self._send(200, json.dumps(store.snapshot()).encode(), "application/json")
            stamp = time.strftime("%Y%m%d-%H%M%S")
            if path == "/api/export.csv":
                return self._send(200, store.export_csv().encode(), "text/csv; charset=utf-8",
                                  {"Content-Disposition": f'attachment; filename="fieldwatch-{stamp}.csv"'})
            if path == "/api/export.json":
                return self._send(200, json.dumps(store.snapshot(), indent=1).encode(), "application/json",
                                  {"Content-Disposition": f'attachment; filename="fieldwatch-{stamp}.json"'})
            self._send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if not self._host_ok():
                return self._send(403, b"bad host", "text/plain")
            origin = self.headers.get("Origin")
            if origin and origin.split("://", 1)[-1] not in allowed_hosts:
                return self._send(403, b"bad origin", "text/plain")
            if self.path == "/api/clear":
                with store.lock:
                    store.devices.clear()
                    store._dirty = True
                return self._send(200, b"{}", "application/json")
            self._send(404, b"not found", "text/plain")

    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.daemon_threads = True
    real = srv.server_address[1]
    allowed_hosts.update({f"127.0.0.1:{real}", f"localhost:{real}", f"[::1]:{real}"})
    return srv


def serve_in_thread(srv: ThreadingHTTPServer) -> threading.Thread:
    t = threading.Thread(target=srv.serve_forever, name="http", daemon=True)
    t.start()
    return t
