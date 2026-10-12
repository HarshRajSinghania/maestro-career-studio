"""Serve the Companion side panel in a normal browser, one URL per panel state.

Run from backend/: python -m scripts.panel_preview [--port 8791]
Open a URL at 380x760 (the side panel's size). The REAL extension/panel/panel.html,
panel.css and panel scripts load; the server only rewrites panel.html so a stub of
`chrome.*` (scripts/panel_preview_stub.js, fed by tests/fixtures/panel_preview/<state>.json)
runs before the panel's own scripts. Nothing under extension/ is changed, and no
backend or extension is needed. Made-up employers only.

Dark: panel.css switches on `prefers-color-scheme: dark`, which a plain page cannot
set. Humans: emulate it in the browser (devtools Rendering, or the browser pane's
colour-scheme setting) on any state URL. Tests: Playwright `color_scheme="dark"`.
The one state named `dark-job-saved` also forces it, by serving panel.css with that
media query always on, so it reads dark in any browser.
"""

import argparse
from contextlib import contextmanager, suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import re
from threading import Thread
from urllib.parse import parse_qs, urlsplit


BACKEND = Path(__file__).resolve().parents[1]
EXTENSION = BACKEND.parent / "extension"
FIXTURES = BACKEND / "tests" / "fixtures" / "panel_preview"
STUB = Path(__file__).with_name("panel_preview_stub.js")

STATES = (
    "job-unsaved", "job-saved", "resume", "resume-ready", "fill",
    "fill-report", "track-draft", "track-applied", "dark-job-saved",
)
DARK_QUERY = re.compile(r"@media\s*\(\s*prefers-color-scheme\s*:\s*dark\s*\)")
FIRST_SCRIPT = '<script src="../shared/decisions.js"></script>'


def fixture(state):
    """The state's spec, read fresh each time so a fixture edit shows on reload."""
    if state not in STATES:
        return None
    return json.loads((FIXTURES / f"{state}.json").read_text(encoding="utf-8"))


def panel_html(state, spec):
    html = (EXTENSION / "panel" / "panel.html").read_text(encoding="utf-8")
    assert FIRST_SCRIPT in html, "panel.html's first script moved; update FIRST_SCRIPT"
    stub = f'<script src="/__preview/stub.js?state={state}"></script>\n'
    html = html.replace(FIRST_SCRIPT, stub + FIRST_SCRIPT, 1)
    if spec.get("scheme") == "dark":
        html = html.replace('href="panel.css"', 'href="/__preview/panel.css?scheme=dark"', 1)
    return html


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def send(self, status, body, kind="text/plain; charset=utf-8"):
        data = body.encode() if isinstance(body, str) else body
        headers = {"Content-Type": kind, "Cache-Control": "no-store",
                   "Content-Length": str(len(data))}
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        state = (query.get("state") or [""])[0]
        route = {
            "/": self.page_index,
            "/panel/panel.html": self.page_panel,
            "/__preview/stub.js": self.page_stub,
            "/__preview/panel.css": self.page_css,
        }.get(url.path)
        if route is None:
            return self.page_static(url.path)
        return route(state, query)

    def page_index(self, _state, _query):
        self.send(200, self.server.index(), "text/html; charset=utf-8")

    def page_panel(self, state, _query):
        spec = fixture(state)
        if spec is None:
            return self.send(404, f"unknown state {state!r}; see /")
        self.send(200, panel_html(state, spec), "text/html; charset=utf-8")

    def page_stub(self, state, _query):
        spec = fixture(state)
        if spec is None:
            return self.send(404, f"unknown state {state!r}")
        head = f"window.__PANEL_PREVIEW_SPEC = {json.dumps(spec)};\n"
        self.send(200, head + STUB.read_text(encoding="utf-8"), "text/javascript; charset=utf-8")

    def page_css(self, _state, query):
        css = (EXTENSION / "panel" / "panel.css").read_text(encoding="utf-8")
        if query.get("scheme") == ["dark"]:
            css = DARK_QUERY.sub("@media all", css)
        self.send(200, css, "text/css; charset=utf-8")

    def page_static(self, path):
        target = (EXTENSION / path.lstrip("/")).resolve()
        if EXTENSION.resolve() not in target.parents or not target.is_file():
            return self.send(404, "not found")
        kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send(200, target.read_bytes(), kind)


class PreviewServer(ThreadingHTTPServer):
    daemon_threads = True

    @property
    def base(self):
        return f"http://127.0.0.1:{self.server_address[1]}"

    def url(self, state):
        return f"{self.base}/panel/panel.html?state={state}"

    def index(self):
        links = "".join(f'<li><a href="{self.url(s)}">{s}</a></li>' for s in STATES)
        return f"<!doctype html><title>Panel preview</title><ul>{links}</ul>"


@contextmanager
def serve(port=0):
    server = PreviewServer(("127.0.0.1", port), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=0, help="default: any free port")
    args = parser.parse_args()
    server = PreviewServer(("127.0.0.1", args.port), Handler)
    for state in STATES:
        print(server.url(state), flush=True)
    with suppress(KeyboardInterrupt), server:
        server.serve_forever()


if __name__ == "__main__":
    main()
