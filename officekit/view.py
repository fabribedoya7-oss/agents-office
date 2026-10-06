"""`office.py city` (alias `office.py view`): a live isometric floor plan of the office.

Standard library only. Serves one page (officekit/web/office.html) and workspace/dashboard.json on
127.0.0.1, and refreshes the snapshot in the background so the page always reads current state.
"""
from __future__ import annotations

import http.server
import sys
import threading
import webbrowser

from . import dashboard as dash
from . import store

PAGE = store.ROOT / "officekit" / "web" / "office.html"


def _refresh_loop(interval: float, stop: threading.Event) -> None:
    last_error = None
    while not stop.wait(interval):
        try:
            dash.write_files(dash.snapshot())
            last_error = None
        except Exception as e:  # keep serving the last good snapshot; report each new error once
            if str(e) != last_error:
                print(f"[office view] snapshot refresh failed: {e}", file=sys.stderr)
                last_error = str(e)


class _Handler(http.server.BaseHTTPRequestHandler):
    # only two routes, so nothing else in the repo or workspace is ever served
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(PAGE.read_bytes(), "text/html; charset=utf-8")
        elif path == "/dashboard.json":
            f = store.WS / "dashboard.json"
            self._send(f.read_bytes() if f.exists() else b"{}", "application/json")
        else:
            self.send_error(404)

    def _send(self, body: bytes, ctype: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # silence per-request logging (the page polls every 2s)
        pass


def _bind(port: int) -> http.server.ThreadingHTTPServer:
    for p in range(port, port + 20):
        try:
            return http.server.ThreadingHTTPServer(("127.0.0.1", p), _Handler)
        except OSError:
            continue
    raise OSError(f"no free port in {port}-{port + 19}")


def serve(port: int = 8765, open_browser: bool = True, interval: float = 2.0) -> None:
    dash.write_files(dash.snapshot())
    server = _bind(port)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    stop = threading.Event()
    threading.Thread(target=_refresh_loop, args=(interval, stop), daemon=True).start()
    print(f"Agents Office floor: {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nOffice view closed.")
    finally:
        stop.set()
        server.server_close()
