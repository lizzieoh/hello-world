#!/usr/bin/env python3
"""The booth control dashboard, on this Mac only (never on a Pi, never on the TVs):

    python3 dashboard.py          then open http://localhost:8099

The page talks straight to each booth's control API (control.py on the Pi, port 8080) over the venue Wi-Fi, with the
PIN (staff.pin next to this file, copied from a Pi, or STAFF_PIN in native.conf; else it asks). Which booths: BOOTHS in native.conf (hostnames or IPs), e.g. BOOTHS=fal-booth-1.local,fal-booth-2.local.
It listens on 127.0.0.1 only, so nobody else on the network can open it.
"""
import json
import os
import sys
import mimetypes
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
PORT = 8099


def conf():
    out = {}
    path = HERE / "native.conf"
    if path.exists():
        for raw in path.read_text().splitlines():
            line = raw.split("#", 1)[0].strip()
            if "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"')
    out.update({k[6:]: v for k, v in os.environ.items() if k.startswith("BOOTH_")})  # BOOTH_BOOTHS=... to test
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, body, ctype, cache="no-store"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", cache)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self.send((HERE / "dashboard.html").read_bytes(), "text/html; charset=utf-8")
        if path == "/config.json":
            c = conf()  # read every time, so an edit to BOOTHS shows on reload
            booths = [b.strip() for b in c.get("BOOTHS", "fal-booth-1.local,fal-booth-2.local").split(",") if b.strip()]
            pin = c.get("STAFF_PIN") or ((HERE / "staff.pin").read_text().strip() if (HERE / "staff.pin").exists() else "")
            return self.send(json.dumps({"booths": booths, "port": int(c.get("CONTROL_PORT", "8080")), "pin": pin}).encode(),
                             "application/json")
        if path == "/logo.svg":
            return self.send((HERE / "brand" / "call-a-fal-lockup.svg").read_bytes(), "image/svg+xml", "max-age=86400")
        if path.startswith("/fonts/"):
            f = HERE / "brand" / "fonts" / Path(path).name
            if f.is_file():
                return self.send(f.read_bytes(), mimetypes.guess_type(f.name)[0] or "font/otf", "max-age=86400")
        self.send_response(404)
        self.end_headers()


if __name__ == "__main__":
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:  # already running (another Terminal tab): just open it
        print(f"Already running: http://localhost:{PORT}")
        webbrowser.open(f"http://localhost:{PORT}")
        raise SystemExit(0)
    print(f"Booth control: http://localhost:{PORT}  (Ctrl-C to stop)", flush=True)
    if "--no-open" not in sys.argv:
        webbrowser.open(f"http://localhost:{PORT}")
    srv.serve_forever()
